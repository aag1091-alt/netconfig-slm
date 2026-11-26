#!/usr/bin/env python3
"""Verify the hand-authored capability representatives.

Loads data/capability_examples/examples.jsonl and asserts each
`expected` answer against its `evidence`, programmatically, wherever
the answer format allows:

- plan answers: validate with the real schema, apply with the real
  engine, walk the diff against independently derived authorization
  (src/plan/cascades.py), and check the authored assertions in S'.
- no_op answers: assertions must already hold in S.
- clarify answers: validate against the clarify schema; candidate
  sets must equal the inventory-derived alias sets. The proposed
  reason `policy_conflict` (matrix judgment question 3) is NOT in
  CLARIFY_REASONS yet; those examples are counted separately and
  checked against the policy evidence instead.
- facts answers: every fact leaf must equal the evidence value
  (state, or a parsed form of the CLI excerpt); `unresolved` fields
  must genuinely be absent from the evidence.
- eligibility answers: the named checks are recomputed from the
  evidence by rule and the verdict re-derived (matrix §4).
- recommendation answers: settings must equal the deterministic
  profile->settings mapping over the supplied policy.
- review answers: candidate and intended plans are both applied;
  the discrepancy set (extra / missing / conflict, by state
  comparison) must equal the findings set.
- interpretation answers: aspect verdicts are recomputed from the
  readback (config equality / diff; traffic tests only for
  forwarding).

Usage: .venv/bin/python scripts/check_capability_examples.py
Exit code 0 iff all examples pass.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.plan.apply import apply_plan, PlanError
from src.plan.cascades import permitted_cascade_paths, primary_paths
from src.plan.diff import diff_states
from src.plan.schema import CLARIFY_REASONS, validate_clarify, validate_output
from src.plan.state import RESERVED_VLANS, VLAN_MAX, VLAN_MIN, find_by_alias

EXAMPLES = (Path(__file__).resolve().parent.parent
            / "data/capability_examples/examples.jsonl")
TASKS = {"read_config", "check_eligibility", "recommend", "plan_edit",
         "review_change", "interpret_readback", "generate_initial"}
AXES = {"words", "device", "policy", "evidence", "scale", "none"}
PROPOSED_CLARIFY_REASONS = {"policy_conflict"}
MISSING = object()


def get_path(state, path):
    parts = path.split(".")
    cur = state
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return MISSING
        cur = cur[p]
    return cur


def check_assertions(state, assertions, problems, where):
    for a in assertions or []:
        got = get_path(state, a["path"])
        if got is MISSING or got != a["equals"]:
            problems.append(
                f"{where}: assertion {a['path']} == {a['equals']!r} "
                f"failed (got {'<missing>' if got is MISSING else got!r})")


def check_plan(state, expected, problems):
    plan = expected["plan"]
    errors = validate_output(plan, state)
    if errors:
        problems.append(f"plan failed validation: {errors}")
        return
    try:
        after, _ = apply_plan(state, plan)
    except PlanError as e:
        problems.append(f"apply() raised PlanError: {e}")
        return
    steps = plan["steps"] if "steps" in plan else [plan]
    cur = state
    for si, step in enumerate(steps):
        nxt, _ = apply_plan(cur, step)
        changed = set(diff_states(cur, nxt))
        allowed = primary_paths(step) | permitted_cascade_paths(step, cur)
        if not changed <= allowed:
            problems.append(
                f"step {si}: unauthorized paths {sorted(changed - allowed)}")
        cur = nxt
    check_assertions(after, expected.get("assertions"), problems, "S'")


def check_clarify(state, evidence, expected, problems, counters):
    cl = expected["clarify"]
    reason = cl.get("reason")
    if reason in PROPOSED_CLARIFY_REASONS:
        counters["proposed_clarify_reasons"] += 1
        rule = expected.get("policy_rule")
        if not rule or not (evidence.get("policy") or {}).get(rule):
            problems.append(
                f"clarify reason {reason!r} is a proposed extension and "
                f"needs a truthy policy_rule in evidence (got {rule!r})")
        if cl.get("candidates") != []:
            problems.append("policy-conflict clarify must have no candidates")
        return
    errors = validate_clarify(cl, state)
    if errors:
        problems.append(f"clarify failed validation: {errors}")
        return
    if reason == "ambiguous_alias":
        hit_sets = []
        aliases = {a for iface in state["interfaces"].values()
                   for a in iface.get("aliases", [])}
        for alias in aliases:
            hits = find_by_alias(state, alias)
            if len(hits) > 1:
                hit_sets.append(set(hits))
        if set(cl.get("candidates", [])) not in hit_sets:
            problems.append(
                f"candidates {cl.get('candidates')} do not equal any "
                f"inventory-derived ambiguous alias set {hit_sets}")


def parse_cli_excerpt(text):
    """Minimal parser for the render-style config subset (read tasks)."""
    ifaces = {}
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("interface "):
            cur = line.split(None, 1)[1].strip()
            ifaces.setdefault(cur, {})
        elif cur is not None and line.startswith(" "):
            body = line.strip()
            if body.startswith("description "):
                ifaces[cur]["description"] = body[len("description "):]
            elif body.startswith("switchport mode "):
                ifaces[cur]["mode"] = body.rsplit(" ", 1)[1]
            elif body.startswith("switchport access vlan "):
                ifaces[cur]["access_vlan"] = int(body.rsplit(" ", 1)[1])
            elif body == "no shutdown":
                ifaces[cur]["admin"] = "up"
            elif body == "shutdown":
                ifaces[cur]["admin"] = "down"
        elif line and not line.startswith(" "):
            cur = None
    return {"interfaces": ifaces}


def check_facts(evidence, expected, problems):
    if "state" in evidence:
        source = evidence["state"]
    elif "cli_excerpt" in evidence:
        source = parse_cli_excerpt(evidence["cli_excerpt"])
    else:
        problems.append("facts answer but no state/cli_excerpt evidence")
        return
    facts = expected.get("facts", {})
    for section in ("interfaces", "vlans"):
        for name, fields in facts.get(section, {}).items():
            for field, want in fields.items():
                got = get_path(source, f"{section}.{name}.{field}")
                if got is MISSING or got != want:
                    problems.append(
                        f"fact {section}.{name}.{field}: expected "
                        f"{want!r}, evidence has "
                        f"{'<missing>' if got is MISSING else got!r}")
    if expected.get("scope") == "trunks" and "state" in evidence:
        want = {n for n, i in evidence["state"]["interfaces"].items()
                if i["mode"] == "trunk"}
        got = set(facts.get("interfaces", {}))
        if got != want:
            problems.append(
                f"scope=trunks: fact interfaces {sorted(got)} != trunk "
                f"interfaces in state {sorted(want)}")
    for alias, name in (expected.get("resolved_alias") or {}).items():
        hits = find_by_alias(evidence["state"], alias)
        if hits != [name]:
            problems.append(
                f"resolved_alias {alias!r}: expected [{name!r}], "
                f"inventory gives {hits}")
    for u in expected.get("unresolved") or []:
        iface = evidence["state"]["interfaces"].get(u["interface"], {})
        if u["field"] in iface:
            problems.append(
                f"unresolved field {u['field']!r} IS present in evidence "
                f"for {u['interface']}")


def check_eligibility(evidence, expected, problems):
    state = evidence["state"]
    prop = evidence["proposal"]
    policy = evidence.get("policy") or {}
    caps = evidence.get("capabilities")
    computed = {}
    vid = prop.get("vlan_id")
    if vid is not None:
        valid = (isinstance(vid, int) and not isinstance(vid, bool)
                 and VLAN_MIN <= vid <= VLAN_MAX
                 and vid not in RESERVED_VLANS)
        computed["vlan_id_valid"] = "pass" if valid else "fail"
        if valid:
            computed["vlan_exists"] = ("pass" if str(vid) in state["vlans"]
                                       else "fail")
            if "permitted_access_vlans" in policy:
                computed["policy_permits_vlan"] = (
                    "pass" if vid in policy["permitted_access_vlans"]
                    else "fail")
    if prop.get("operation") == "set_port_mode" and caps:
        computed["mode_supported"] = (
            "pass" if prop.get("mode") in caps.get("supported_modes", [])
            else "fail")
    authored = {c["name"]: c["status"] for c in expected["checks"]}
    if authored != computed:
        problems.append(
            f"checks mismatch: authored {authored} != recomputed {computed}")
        return
    fails = {n for n, s in computed.items() if s == "fail"}
    if not fails:
        verdict = "eligible"
    elif fails == {"vlan_exists"}:
        verdict = "prerequisites_missing"
        want_missing = [f"create_vlan:{vid}"]
        if expected.get("missing_prerequisites") != want_missing:
            problems.append(
                f"missing_prerequisites: expected "
                f"{expected.get('missing_prerequisites')}, derived "
                f"{want_missing}")
    else:
        verdict = "blocked"
        if not expected.get("blocking_reasons"):
            problems.append("verdict blocked but no blocking_reasons given")
    if expected["verdict"] != verdict:
        problems.append(
            f"verdict: authored {expected['verdict']!r} != derived "
            f"{verdict!r}")


def check_recommendation(evidence, expected, problems):
    state = evidence["state"]
    policy = evidence.get("policy") or {}
    settings = expected["settings"]
    iface = settings.get("interface")
    if iface not in state["interfaces"]:
        problems.append(f"settings.interface {iface!r} not in state")
        return
    derived = {"interface": iface}
    if "trunk_allowed" in policy:
        derived.update({"mode": "trunk",
                        "trunk_allowed": policy["trunk_allowed"],
                        "native_vlan": policy["native_vlan"]})
    elif "data_vlan" in policy:
        derived.update({"mode": "access",
                        "access_vlan": policy["data_vlan"]})
    if "enable_port" in policy:
        derived["admin"] = ("up" if policy["enable_port"]
                            else state["interfaces"][iface]["admin"])
    if derived != settings:
        problems.append(
            f"settings mismatch: authored {settings} != profile-derived "
            f"{derived}")
    for key in expected.get("rationale", []):
        if not key.startswith("profile:") or key.split(":", 1)[1] not in policy:
            problems.append(f"rationale key {key!r} not grounded in policy")


def check_review(evidence, expected, problems):
    state = evidence["state"]
    cand = evidence["candidate"]["plan"]
    intended = expected["intended_plan"]
    for label, plan in (("candidate", cand), ("intended", intended)):
        errors = validate_output(plan, state)
        if errors:
            problems.append(f"{label} plan failed validation: {errors}")
            return
    cand_after, _ = apply_plan(state, cand)
    intended_after, _ = apply_plan(state, intended)
    paths = (set(diff_states(state, cand_after))
             | set(diff_states(state, intended_after)))
    computed = set()
    for p in paths:
        bv, cv, iv = (get_path(state, p), get_path(cand_after, p),
                      get_path(intended_after, p))
        if cv != bv and iv == bv:
            computed.add(("extra_change", p))
        elif iv != bv and cv == bv:
            computed.add(("missing_change", p))
        elif cv != bv and iv != bv and cv != iv:
            computed.add(("conflict", p))
    authored = {(f["type"], f["path"]) for f in expected["findings"]}
    if authored != computed:
        problems.append(
            f"findings mismatch: authored {sorted(authored)} != "
            f"recomputed {sorted(computed)}")
    want_verdict = "accept" if not computed else "reject"
    if expected["verdict"] != want_verdict:
        problems.append(
            f"verdict: authored {expected['verdict']!r} != derived "
            f"{want_verdict!r}")


def check_interpretation(evidence, expected, problems):
    intended = evidence["state"]
    rb = evidence["readback"]
    if rb.get("config_state") is None and rb.get("config_excerpt") is None:
        configuration, mismatches = "unknown", []
    elif rb.get("config_state") is not None:
        if rb["config_state"] == intended:
            configuration, mismatches = "passed", []
        else:
            configuration = "failed"
            mismatches = diff_states(intended, rb["config_state"])
    else:
        problems.append("config_excerpt-only readback not checkable yet")
        return
    traffic = [t for t in rb.get("tests", []) if t.get("kind") == "traffic"]
    if any(t.get("result") == "fail" for t in traffic):
        forwarding = "failed"
    elif traffic:
        forwarding = "passed"
    else:
        forwarding = "unknown"
    missing = []
    if rb.get("config_state") is None and rb.get("config_excerpt") is None:
        missing.append("config_readback")
    if not traffic:
        missing.append("traffic_test")
    if expected["aspects"] != {"configuration": configuration,
                               "forwarding": forwarding}:
        problems.append(
            f"aspects mismatch: authored {expected['aspects']} != "
            f"recomputed configuration={configuration}, "
            f"forwarding={forwarding}")
    if sorted(expected.get("mismatches", [])) != sorted(mismatches):
        problems.append(
            f"mismatches: authored {expected.get('mismatches')} != "
            f"recomputed {mismatches}")
    if set(expected.get("missing_evidence", [])) != set(missing):
        problems.append(
            f"missing_evidence: authored {expected.get('missing_evidence')} "
            f"!= recomputed {missing}")


def main():
    records = [json.loads(l) for l in open(EXAMPLES) if l.strip()]
    failures = 0
    counters = Counter()
    per_task = Counter()
    per_task_fail = Counter()
    per_axis = Counter()
    ids = set()
    groups = {}
    for rec in records:
        problems = []
        rid = rec.get("id")
        if rid in ids:
            problems.append("duplicate id")
        ids.add(rid)
        if rec.get("task") not in TASKS:
            problems.append(f"unknown task {rec.get('task')!r}")
        if rec.get("variation_axis") not in AXES:
            problems.append(f"unknown variation_axis "
                            f"{rec.get('variation_axis')!r}")
        if not isinstance(rec.get("request"), str) or not rec["request"]:
            problems.append("request must be a non-empty string")
        evidence = rec.get("evidence") or {}
        expected = rec.get("expected") or {}
        kind = expected.get("kind")
        counters[f"kind:{kind}"] += 1
        per_task[rec.get("task")] += 1
        per_axis[rec.get("variation_axis")] += 1
        if rec.get("group"):
            groups.setdefault(rec["group"], []).append(rec)
        state = evidence.get("state")
        if kind == "plan":
            check_plan(state, expected, problems)
        elif kind == "no_op":
            check_assertions(state, expected.get("assertions"), problems, "S")
        elif kind == "clarify":
            check_clarify(state, evidence, expected, problems, counters)
        elif kind == "facts":
            check_facts(evidence, expected, problems)
        elif kind == "eligibility":
            check_eligibility(evidence, expected, problems)
        elif kind == "recommendation":
            check_recommendation(evidence, expected, problems)
        elif kind == "review":
            check_review(evidence, expected, problems)
        elif kind == "interpretation":
            check_interpretation(evidence, expected, problems)
        else:
            problems.append(f"unknown expected kind {kind!r}")
        ok = not problems
        if not ok:
            failures += 1
            per_task_fail[rec.get("task")] += 1
        print(f"{'PASS' if ok else 'FAIL'}  {rid:<36} "
              f"({rec.get('task')}/{kind}/{rec.get('variation_axis')})")
        for p in problems:
            print(f"      -> {p}")
    # Paired state/CLI records must carry identical expected facts.
    for group, members in groups.items():
        facts = [m["expected"].get("facts") for m in members
                 if m["expected"].get("kind") == "facts"]
        if len(facts) > 1 and any(f != facts[0] for f in facts):
            failures += 1
            print(f"FAIL  group {group}: paired facts answers differ")
    print("\nPer task:")
    for task in sorted(per_task):
        print(f"  {task:<22} {per_task[task] - per_task_fail[task]}/"
              f"{per_task[task]}")
    print("\nPer variation axis:")
    for axis in sorted(per_axis):
        print(f"  {axis:<10} {per_axis[axis]}")
    print("\nPer answer kind:")
    for k in sorted(c for c in counters if c.startswith("kind:")):
        print(f"  {k[5:]:<22} {counters[k]}")
    print(f"\nProposed clarify reasons used (not yet in CLARIFY_REASONS): "
          f"{counters['proposed_clarify_reasons']}")
    print(f"\n{len(records) - failures}/{len(records)} capability "
          f"examples verify")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
