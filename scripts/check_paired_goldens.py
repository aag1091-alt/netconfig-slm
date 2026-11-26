#!/usr/bin/env python3
"""Verify data/yang_slice/paired_goldens.jsonl.

Each record carries the same device state in three forms: our plan
state (src/plan), a YANG-modeled JSON instance (pinned OpenConfig
slice, docs/YANG_PIN.md), and an IOS XE CLI excerpt produced by our
own render_config(). The checker proves the three views agree and
that the expected answer is correct against the real plan engine:

1. Context consistency: context.aliases == the plan state's per-port
   aliases, and context device/hostname == the state's. Aliases are
   context-layer inventory metadata in BOTH views — neither the
   running-config nor the YANG instance has a node for them.
2. CLI provenance: cli_state == render_config(plan_state) exactly.
3. Fact equivalence: facts projected from the YANG instance and
   facts parsed back from the CLI excerpt must BOTH equal the facts
   projected from the plan state. The projection is the
   ACTIVE-MODE projection defined in docs/YANG_SLICE.md: stored
   parameters for the other mode are not device-visible in either
   view (the CLI does not render them; the YANG schema's `when`
   conditions forbid them), so they are not part of the comparison.
4. Independent parse: ciscoconfparse2 must parse cli_state and find
   the same interface blocks (a parser that is not ours).
5. Expected answers, via the real engine:
   - plan: validate_output == [], applied with apply_plan, every
     changed path authorized by cascades.authorized_paths for its
     step, authored assertions hold in S';
   - no_op: assertions already hold in S;
   - clarify: candidates == the interfaces carrying the ambiguous
     alias in the state, and validate_clarify == [];
   - facts: expected facts equal the projected facts leaf-by-leaf.

Usage: .venv/bin/python scripts/check_paired_goldens.py
"""
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.plan import cascades
from src.plan.apply import apply_plan
from src.plan.diff import diff_states, path_touched
from src.plan.render import render_config
from src.plan.schema import validate_clarify, validate_output
from src.plan.state import find_by_alias

ROOT = Path(__file__).resolve().parent.parent
PAIRED = ROOT / "data/yang_slice/paired_goldens.jsonl"


# ---- projections -------------------------------------------------

def projected_facts(state):
    """Active-mode projection of a plan state (docs/YANG_SLICE.md)."""
    facts = {"vlans": {int(k): (v.get("name") or None)
                       for k, v in state["vlans"].items()},
             "interfaces": {}}
    for name, p in state["interfaces"].items():
        f = {"mode": p["mode"], "admin": p["admin"],
             "description": p.get("description", "")}
        if p["mode"] == "access":
            f["access_vlan"] = p["access_vlan"]
        else:
            f["native_vlan"] = p["native_vlan"]
            ta = p["trunk_allowed"]
            f["trunk_allowed"] = ("all" if ta == "all"
                                  else sorted(ta))
        facts["interfaces"][name] = f
    return facts


def facts_from_yang(inst):
    facts = {"vlans": {}, "interfaces": {}}
    for e in inst["openconfig-vlan:vlans"].get("vlan", []):
        facts["vlans"][e["vlan-id"]] = e.get("config", {}).get("name")
    for e in inst["openconfig-interfaces:interfaces"].get("interface", []):
        cfg = e.get("config", {})
        # enabled defaults to true in openconfig-interfaces when absent
        f = {"admin": "up" if cfg.get("enabled", True) else "down",
             "description": cfg.get("description", "")}
        scfg = (e.get("openconfig-if-ethernet:ethernet", {})
                 .get("openconfig-vlan:switched-vlan", {})
                 .get("config", {}))
        mode = scfg.get("interface-mode")
        if mode == "ACCESS":
            f["mode"] = "access"
            f["access_vlan"] = scfg.get("access-vlan")
        elif mode == "TRUNK":
            f["mode"] = "trunk"
            f["native_vlan"] = scfg.get("native-vlan")
            if "trunk-vlans" not in scfg:
                f["trunk_allowed"] = "all"  # unspecified == all (OC)
            else:
                members = []
                for m in scfg["trunk-vlans"]:
                    if isinstance(m, int):
                        members.append(m)
                    else:
                        lo, hi = m.split("..")
                        members.extend(range(int(lo), int(hi) + 1))
                f["trunk_allowed"] = sorted(members)
        facts["interfaces"][e["name"]] = f
    return facts


_VLAN_BLOCK = re.compile(r"^vlan (\d+)$")
_NAME_LINE = re.compile(r"^ name (.+)$")
_IFACE_BLOCK = re.compile(r"^interface (\S+)$")
_DESC_LINE = re.compile(r"^ description (.+)$")
_ACCESS_LINE = re.compile(r"^ switchport access vlan (\d+)$")
_NATIVE_LINE = re.compile(r"^ switchport trunk native vlan (\d+)$")
_ALLOWED_LINE = re.compile(r"^ switchport trunk allowed vlan (.+)$")


def facts_from_cli(text):
    """Parse a render_config()-shaped full-config excerpt back to
    facts. (scripts/cli_lab_check.py's interpreter handles delta CLI
    only — it rejects hostname/description lines — so full-config
    parsing lives here.)"""
    facts = {"vlans": {}, "interfaces": {}}
    block, name = None, None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        m = _VLAN_BLOCK.match(line)
        if m:
            block, name = "vlan", int(m.group(1))
            facts["vlans"][name] = None
            continue
        m = _IFACE_BLOCK.match(line)
        if m:
            block, name = "iface", m.group(1)
            facts["interfaces"][name] = {"admin": "down",
                                         "description": ""}
            continue
        if line == "!":
            block, name = None, None
            continue
        if block == "vlan":
            m = _NAME_LINE.match(line)
            if m:
                facts["vlans"][name] = m.group(1)
        elif block == "iface":
            f = facts["interfaces"][name]
            m = _DESC_LINE.match(line)
            if m:
                f["description"] = m.group(1)
            elif line == " switchport mode access":
                f["mode"] = "access"
            elif line == " switchport mode trunk":
                f["mode"] = "trunk"
            elif (m := _ACCESS_LINE.match(line)):
                f["access_vlan"] = int(m.group(1))
            elif (m := _NATIVE_LINE.match(line)):
                f["native_vlan"] = int(m.group(1))
            elif (m := _ALLOWED_LINE.match(line)):
                val = m.group(1)
                f["trunk_allowed"] = ("all" if val == "all" else
                                      sorted(int(x) for x in
                                             val.split(",")))
            elif line == " shutdown":
                f["admin"] = "down"
            elif line == " no shutdown":
                f["admin"] = "up"
    return facts


def ccp2_interface_names(text):
    """Independent parse via ciscoconfparse2; None if unavailable."""
    try:
        from ciscoconfparse2 import CiscoConfParse
    except ImportError:
        return None
    parse = CiscoConfParse(text.splitlines())
    return sorted(o.text[len("interface "):]
                  for o in parse.find_objects(r"^interface\s"))


# ---- expected-answer checking ------------------------------------

def _resolve(state, path):
    """Assertion paths join interface names containing dots/slashes;
    resolve greedily against interface keys."""
    parts = path.split(".")
    cur = state
    i = 0
    while i < len(parts):
        if isinstance(cur, dict) and "interfaces" in cur and i == 0:
            cur = cur[parts[i]]
            i += 1
            continue
        # interface name: consume parts until a known field matches
        if isinstance(cur, dict) and any(
                k.startswith("GigabitEthernet") for k in cur):
            joined = None
            for j in range(len(parts), i, -1):
                cand = ".".join(parts[i:j])
                if cand in cur:
                    joined = (cand, j)
                    break
            if joined is None:
                return None, False
            cur = cur[joined[0]]
            i = joined[1]
            continue
        if isinstance(cur, dict) and parts[i] in cur:
            cur = cur[parts[i]]
            i += 1
        else:
            return None, False
    return cur, True


def check_assertions(state, assertions, problems, where):
    for a in assertions or []:
        val, ok = _resolve(state, a["path"])
        if not ok or val != a["equals"]:
            problems.append(f"{where}: assertion {a['path']} == "
                            f"{a['equals']!r} failed (got {val!r})")


def check_plan_answer(state, expected, problems, where):
    plan = expected["plan"]
    errors = validate_output(plan, state)
    if errors:
        problems.append(f"{where}: validate_output: {errors}")
        return
    steps = plan["steps"] if "steps" in plan else [plan]
    cur = copy.deepcopy(state)
    for step in steps:
        before = copy.deepcopy(cur)
        after, _effects = apply_plan(cur, step)
        changed = diff_states(before, after)
        authorized = cascades.authorized_paths(step, before)
        for path in changed:
            if not any(path_touched([path], a) for a in authorized):
                problems.append(f"{where}: unauthorized change at "
                                f"{path} (step {step.get('operation')})")
        cur = after
    check_assertions(cur, expected.get("assertions"), problems, where)


def check_expected(state, expected, record, problems):
    kind = expected["kind"]
    if kind == "plan":
        check_plan_answer(state, expected, problems, "expected")
    elif kind == "no_op":
        check_assertions(state, expected.get("assertions"), problems,
                         "expected(no_op)")
    elif kind == "clarify":
        cl = expected["clarify"]
        errors = validate_clarify(cl, state)
        if errors:
            problems.append(f"expected(clarify): {errors}")
        alias = record.get("clarify_alias")
        if alias:
            want = sorted(find_by_alias(state, alias))
            if sorted(cl.get("candidates", [])) != want:
                problems.append(f"expected(clarify): candidates "
                                f"{cl.get('candidates')} != alias "
                                f"holders {want}")
    elif kind == "facts":
        facts = projected_facts(state)
        for iname, fields in expected["facts"].get("interfaces", {}).items():
            for field, want in fields.items():
                got = facts["interfaces"].get(iname, {}).get(field)
                if got != want:
                    problems.append(f"expected(facts): {iname}.{field} "
                                    f"== {want!r}, state has {got!r}")
    else:
        problems.append(f"unknown expected kind {kind!r}")


def facts_diff(a, b, where):
    out = []
    if a.get("vlans") != b.get("vlans"):
        out.append(f"{where}: vlans differ: {a.get('vlans')} vs "
                   f"{b.get('vlans')}")
    ai, bi = a.get("interfaces", {}), b.get("interfaces", {})
    if set(ai) != set(bi):
        out.append(f"{where}: interface sets differ: "
                   f"{sorted(ai)} vs {sorted(bi)}")
    for name in set(ai) & set(bi):
        if ai[name] != bi[name]:
            out.append(f"{where}: {name}: {ai[name]} vs {bi[name]}")
    return out


def main():
    records = [json.loads(l) for l in open(PAIRED) if l.strip()]
    failures = 0
    for rec in records:
        problems = []
        state = rec["plan_state"]
        # 1. context consistency
        ctx = rec["context"]
        if ctx["device"] != state["device"] or \
                ctx["hostname"] != state["hostname"]:
            problems.append("context device/hostname != plan state")
        for name, p in state["interfaces"].items():
            if sorted(ctx["aliases"].get(name, [])) != sorted(p["aliases"]):
                problems.append(f"context aliases for {name} != state "
                                f"aliases")
        # 2. CLI provenance
        if rec["cli_state"] != render_config(state):
            problems.append("cli_state != render_config(plan_state)")
        # 3. fact equivalence across the three views
        pf = projected_facts(state)
        problems += facts_diff(pf, facts_from_yang(rec["yang_state"]),
                               "yang-vs-plan")
        problems += facts_diff(pf, facts_from_cli(rec["cli_state"]),
                               "cli-vs-plan")
        # depends_on paths specifically present and equal in all views
        yf, cf = facts_from_yang(rec["yang_state"]), facts_from_cli(rec["cli_state"])
        for path in rec.get("depends_on", []):
            # direct leaf comparison via the three projected dicts
            parts = path.split(".")
            if parts[0] == "interfaces":
                iname = ".".join(parts[1:-1])
                field = parts[-1]
                vals = [pf["interfaces"].get(iname, {}).get(field),
                        yf["interfaces"].get(iname, {}).get(field),
                        cf["interfaces"].get(iname, {}).get(field)]
                if not (vals[0] == vals[1] == vals[2]):
                    problems.append(f"depends_on {path}: views disagree "
                                    f"plan={vals[0]!r} yang={vals[1]!r} "
                                    f"cli={vals[2]!r}")
        # 4. independent parse
        names = ccp2_interface_names(rec["cli_state"])
        if names is None:
            problems.append("ciscoconfparse2 unavailable — parse skipped")
        elif names != sorted(state["interfaces"]):
            problems.append(f"ciscoconfparse2 interfaces {names} != "
                            f"{sorted(state['interfaces'])}")
        # 5. expected answers
        if "requests" in rec:
            for item in rec["requests"]:
                check_expected(state, item["expected"], rec, problems)
        else:
            check_expected(state, rec["expected"], rec, problems)
        status = "PASS" if not problems else "FAIL"
        failures += 0 if not problems else 1
        print(f"{status}  {rec['id']}")
        for prob in problems:
            print(f"      -> {prob}")
    print(f"\n{len(records) - failures}/{len(records)} paired goldens "
          f"verified")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
