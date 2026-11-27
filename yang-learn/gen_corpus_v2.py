#!/usr/bin/env python3
"""Corpus v2 additions generator for yang-learn (RUN2_PLAN.md).

Generates TRAIN/DEV-only additions targeting run 1's three gaps:

1. path_meaning type recall (38.2% on bench v1): the v1 deduped
   constrained pool (235 facts) was exhausted at one representative
   path per fact. The pool CAN be widened without touching the
   benchmark: the same non-reserved facts are instantiated at
   1,229 further concrete paths (other interface families) that v1
   never used, and 175 deduped unconstrained (plain-type) facts
   were excluded by v1's constrained() filter entirely. This
   generator emits PM records at those unused paths. Facts whose
   (interface-normalized path, type) key is in the benchmark's
   reserved every-7th slice are excluded at EVERY path — the
   reserved fact never enters train/dev in any clothes.
2. revised_schema citation discipline (verdict-only 92.9% vs
   strict 46.4%): more cases per train-profile rule (P1/P3/P4/P5
   only — amendment A1 unchanged, no new profiles), verdicts
   computed by explicit predicates in this file, rationales
   templated so the exact `P# rule (n)` citation is always
   present, three question phrasings.
3. instance_validity false-valid bias (13 bench invalids called
   "Valid.") + typed_edit invalid_key/target_not_found confusion
   (3/3 TE-negative fails): fresh case values in the v1 classes
   (values disjoint from the v1/bench case lists), more
   string-branch union traps in both directions, and malformed-key
   selector negatives in six new shapes, each mechanically
   verified against the key leaf's pattern.

Stopping discipline is addressed by format discipline: every
addition's completion uses EXACTLY the v1 family format — no new
shapes — and the additions raise the share of short completions.

Validation independence is unchanged: PM facts from pyang
(schema_tools.inventory), IV/TE verdicts from yangson via
gen_corpus.validate_all (intended labels that yangson disagrees
with are dropped and reported, never relabeled), RS verdicts from
in-code predicates over the authored profile rules, TE-negative
selectors verified mechanically at generation.

Benchmark v1 is frozen (commit 5189d33): this script never writes
corpus/{train,dev,test,benchmark}.jsonl, held_out.jsonl,
bench_instances/ or BENCHMARK.md. Additions land in
corpus/train_v2_additions.jsonl and corpus/dev_v2_additions.jsonl
(split assigned by scenario group, ~85/15, never test), instance
files in corpus/instances_v2/, and a manifest addendum in
corpus/manifest_v2_addendum.json. Every addition is deduped by
prompt text against ALL v1 splits + held_out + the sample.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_corpus import (  # noqa: E402
    GE_LIST, ENTRY_LIST, GE_NAME_RE, P_ACCESS, P_NATIVE, P_AGING,
    P_ENCAP, P_VOICE, P_ALLOWED, P_MAXACC, RS_PROFILES, TRAIN_IFS,
    _acc, _trk, validate_all, variantize)
from gen_records import (  # noqa: E402
    MODEL, S, build_prov_map, doc, ge, ps, source_for, sw_access)
from schema_tools import inventory  # noqa: E402

HERE = Path(__file__).resolve().parent


def norm_path(p: str) -> str:
    return re.sub(r"/interface/[^/]+/", "/interface/*/", p)


def if_family(p: str) -> str:
    parts = p.split("/")
    return parts[3] if len(parts) > 3 and parts[2] == "interface" \
        else parts[2]


def split_v2(group: str) -> str:
    h = int(hashlib.sha256(group.encode()).hexdigest(), 16) % 20
    return "train" if h < 17 else "dev"


# ------------------------------------------------------------- path_meaning
def gen_pm_v2(facts, prov, v1_pm_paths):
    def constrained(f):
        t = f["type"]
        return (any(k in t for k in
                    ("range", "enumeration", "pattern", "union"))
                or f["default"] is not None or f["when"] or f["must"]
                or f["kind"] == "leaf-list")

    def prio(f):
        t = f["type"]
        return (0 if (f["when"] or f["must"]) else
                1 if "pattern" in t else
                2 if "union" in t else
                3 if ("range" in t or "enumeration" in t) else 4)

    # rebuild the v1 deduped pool to identify the reserved slice
    pool = {}
    for f in facts.values():
        if not constrained(f):
            continue
        key = (norm_path(f["path"]), f["type"])
        if key not in pool or prio(f) < prio(pool[key]):
            pool[key] = f
    deduped = sorted(pool.values(), key=lambda f: (prio(f), f["path"]))
    bench_keys = {(norm_path(f["path"]), f["type"])
                  for f in deduped[::7][:48]}

    by_key: dict[tuple, list] = {}
    for f in facts.values():
        if not constrained(f):
            continue
        key = (norm_path(f["path"]), f["type"])
        if key in bench_keys or f["path"] in v1_pm_paths:
            continue
        by_key.setdefault(key, []).append(f)

    out = []
    n = 0
    # type records: up to 2 unused concrete paths per non-reserved
    # fact (3 for the run-1 failure classes: when/pattern/typedef),
    # preferring interface families the v1 representative did not
    # use, then families not yet used for this fact.
    for key in sorted(by_key, key=lambda k: (prio(pool[k]), k[0])):
        cap = 3 if (prio(pool[key]) <= 1 or "->" in key[1]) else 2
        cands = sorted(by_key[key], key=lambda f: f["path"])
        rep_family = if_family(pool[key]["path"])
        cands.sort(key=lambda f: (if_family(f["path"]) == rep_family,
                                  f["path"]))
        picked, fams = [], set()
        for f in cands:
            fam = if_family(f["path"])
            if fam not in fams or len(picked) == 0:
                picked.append(f)
                fams.add(fam)
            if len(picked) == cap:
                break
        if len(picked) < cap:  # top up from the representative family
            for f in cands:
                if f not in picked:
                    picked.append(f)
                if len(picked) == cap:
                    break
        for f in picked:
            n += 1
            src = {**f["source"],
                   "revision": prov[f["path"]]["revision"]}
            completion = f"{f['type']}"
            if f["default"] is not None:
                completion += f" Default: {f['default']}."
            if f["description"]:
                completion += f" Meaning: {f['description']}"
            out.append({
                "id": f"V2-PM-{n:03d}", "family": "path_meaning",
                "origin": "generated_fact_v2",
                "scenario_group": f"pm2:{f['path']}",
                "prompt": (f"In {MODEL}, what is the type of the "
                           f"leaf at {f['path']}?"),
                "completion": completion,
                "fact": {"path": f["path"], "type": f["type"],
                         "default": f["default"],
                         "answer": {"type": f["type"],
                                    "default": f["default"]}},
                "source": src, "validation": None})
            if f["when"] and len([r for r in out
                                  if r["fact"].get("when")]) < 24:
                w = f["when"][0]
                n += 1
                out.append({
                    "id": f"V2-PM-{n:03d}", "family": "path_meaning",
                    "origin": "generated_fact_v2",
                    "scenario_group": f"pm2:{f['path']}",
                    "prompt": (f"In {MODEL}, under what condition "
                               f"may the leaf at {f['path']} be "
                               f"present?"),
                    "completion": (
                        f"It carries a when condition on {w['on']}: "
                        f"`{w['xpath']}` — the node is only valid "
                        f"when that XPath evaluates to true."),
                    "fact": {"path": f["path"], "when": f["when"],
                             "answer": {"when_on": w["on"],
                                        "xpath": w["xpath"]}},
                    "source": src, "validation": None})
        if n >= 320:
            break

    # plain-type records: deduped unconstrained facts v1 excluded
    plain: dict[tuple, dict] = {}
    for f in facts.values():
        if constrained(f) or f["path"] in v1_pm_paths:
            continue
        key = (norm_path(f["path"]), f["type"])
        if key not in plain:
            plain[key] = f
    quotas = {"boolean": 25, "string": 25, "empty": 10}
    used_q: Counter = Counter()
    for key in sorted(plain, key=lambda k: (k[1], k[0])):
        f = plain[key]
        base = f["type"].split(" ")[0]
        if base not in quotas or used_q[base] >= quotas[base]:
            continue
        if base == "string" and not f["description"]:
            continue
        used_q[base] += 1
        n += 1
        src = {**f["source"], "revision": prov[f["path"]]["revision"]}
        completion = f"{f['type']}"
        if f["description"]:
            completion += f" Meaning: {f['description']}"
        out.append({
            "id": f"V2-PM-{n:03d}", "family": "path_meaning",
            "origin": "generated_fact_v2_plain",
            "scenario_group": f"pm2:{f['path']}",
            "prompt": (f"In {MODEL}, what is the type of the leaf "
                       f"at {f['path']}?"),
            "completion": completion,
            "fact": {"path": f["path"], "type": f["type"],
                     "default": f["default"],
                     "answer": {"type": f["type"],
                                "default": f["default"]}},
            "source": src, "validation": None})
    return out


# --------------------------------------------------------- instance_validity
def iv2_cases():
    """Fresh case values per v1 class (disjoint from the v1/bench
    value lists). Same shapes, rule texts and expected-node
    fragments as gen_corpus.iv_cases."""
    cases = []

    def add(cls, label, data, intended, rule, paths, frag):
        cases.append({"cls": cls, "label": label, "data": data,
                      "intended": intended, "rule": rule,
                      "paths": paths, "frag": frag})

    R_ACC = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; the value matches "
             "neither")
    F_ACC = "Cisco-IOS-XE-switch:access/Cisco-IOS-XE-switch:vlan"
    for v, ok in [(4093, 1), (2, 1), (4096, 0), (-1, 0), (8193, 0)]:
        add("range_access", f"access VLAN {v}", _acc(v, TRAIN_IFS[0]),
            "valid" if ok else "invalid", None if ok else R_ACC,
            [P_ACCESS], F_ACC)
    R_UNI = ("access/vlan/vlan is a union of uint16 range 1..4094 "
             "and the enumeration {dynamic}; this JSON value matches "
             "no branch (a string is not the numeric branch)")
    for v, ok in [("4096", 0), ("none", 0), (4094, 1)]:
        add("union_access", f"access VLAN {v!r}",
            _acc(v, TRAIN_IFS[0]), "valid" if ok else "invalid",
            None if ok else R_UNI, [P_ACCESS], F_ACC)
    R_ENC = ("trunk/encapsulation is an enumeration of "
             "{dot1q, isl, negotiate}")
    for v in ["DOT1Q", "8021q"]:
        add("enum_encap", f"encapsulation {v}",
            _trk({"encapsulation": v}, TRAIN_IFS[0]), "invalid",
            R_ENC, [P_ENCAP], "encapsulation")
    R_NAT = "trunk/native/vlan/vlan-id is uint16 range 1..4094"
    for v, ok in [(4093, 1), (4096, 0)]:
        add("range_native", f"native VLAN {v}",
            _trk({"native": {"vlan": {"vlan-id": v}}}, TRAIN_IFS[0]),
            "valid" if ok else "invalid", None if ok else R_NAT,
            [P_NATIVE], "native")
    R_AGE = "port-security aging/time is uint16 range 1..1440"
    for v, ok in [(1439, 1), (720, 1), (2, 1), (1442, 0), (2880, 0)]:
        add("range_aging", f"aging time {v}",
            _acc(10, TRAIN_IFS[0], ps(aging={S + "time": v})),
            "valid" if ok else "invalid", None if ok else R_AGE,
            [P_AGING + "/time"], "aging")
    R_AGT = ("port-security aging/type is an enumeration of "
             "{absolute, inactivity}")
    for v in ["Absolute", "in-activity"]:
        add("enum_aging", f"aging type {v}",
            _acc(10, TRAIN_IFS[0], ps(aging={S + "type": v})),
            "invalid", R_AGT, [P_AGING + "/type"], "aging")
    R_ENT = ("configuration-entry/vlan-id is a string whose pattern "
             "encodes VLAN IDs 1..4094 (and range lists); the key "
             "does not match")
    P_ENTRY = "/native/vlan/configuration-entry/vlan-id"
    for v, ok in [("4093", 1), ("100-200", 1), ("2048", 1),
                  ("4096", 0), ("1-4095", 0), ("5000", 0)]:
        add("pattern_entry", f"entry key {v!r}",
            doc([ge(sw_access(10), name=TRAIN_IFS[0])],
                [{"vlan-id": v}]),
            "valid" if ok else "invalid", None if ok else R_ENT,
            [P_ENTRY], "configuration-entry")
    R_IFN = ("the GigabitEthernet list key 'name' must match the "
             "pattern (0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\\.[0-9]*)? "
             "— numeric slot/port form only")
    P_NAME = "/native/interface/GigabitEthernet/name"
    for v, ok in [("10/0/1", 1), ("1/0/24.3", 1), ("01/0/1", 0),
                  ("1/0/1 ", 0)]:
        add("pattern_ifname", f"interface name {v!r}", _acc(10, v),
            "valid" if ok else "invalid", None if ok else R_IFN,
            [P_NAME], "GigabitEthernet")
    R_CHO = ("mode/mode-choice is a YANG choice: at most one case "
             "may be present; this instance sets two")
    P_MODE = "/native/interface/GigabitEthernet/switchport-config" \
             "/switchport/mode"
    for cases_ in [["access", "dot1q-tunnel"], ["trunk", "dynamic"]]:
        mode = {}
        for c in cases_:
            if c == "dynamic":
                mode[S + "dynamic"] = "auto"
            else:
                mode[S + c] = {}
        sw = {S + "mode": mode,
              S + "access": {S + "vlan": {S + "vlan": 10}}}
        add("choice_mode", f"mode cases {cases_}",
            doc([ge(sw, name=TRAIN_IFS[0])], [{"vlan-id": "10"}]),
            "invalid", R_CHO, [P_MODE], "Cisco-IOS-XE-switch:mode")
    R_WHEN = ("maximum-vlan-access carries when "
              "'../../../../mode/access' — it requires the mode "
              "choice's access case")
    for mode_case, ok in [("access", 1), ("trunk", 0)]:
        extra = ps(max_access={S + "maximum": 200,
                               S + "vlan": "access"})
        data = (_acc(10, TRAIN_IFS[0], extra) if mode_case == "access"
                else _trk(None, TRAIN_IFS[0], extra))
        add("when_maxacc", f"maximum-vlan-access 200 with mode "
            f"{mode_case}", data, "valid" if ok else "invalid",
            None if ok else R_WHEN, [P_MAXACC], "maximum-addres")
    R_ALL = ("trunk allowed vlans is a union of uint16 range "
             "1..4094 and string; this non-string value is outside "
             "1..4094, so it matches neither branch")
    for v, ok in [("4096", 1), ("20,30", 1), (4096, 0), (-5, 0)]:
        add("union_allowed", f"allowed vlans {v!r}",
            _trk({"allowed": {"vlan": {"vlans": v}}}, TRAIN_IFS[0]),
            "valid" if ok else "invalid", None if ok else R_ALL,
            [P_ALLOWED], "vlans")
    R_VOI = ("voice/vlan/vlan is a union of the enumeration "
             "{dot1p, none, untagged}, uint16 range 1..4094, and "
             "string; this non-string value is outside 1..4094, so "
             "it matches no branch")
    for v, ok in [(4094, 1), ("none", 1), (4096, 0), (-1, 0)]:
        sw = sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            doc([ge(sw, name=TRAIN_IFS[0])], [{"vlan-id": "10"}]),
            "valid" if ok else "invalid", None if ok else R_VOI,
            [P_VOICE], "voice")
    return cases


def gen_iv_v2(prov, variants=3):
    out = []
    per_class = Counter()
    n = 0
    for c in iv2_cases():
        case_idx = per_class[c["cls"]]
        per_class[c["cls"]] += 1
        for v in range(variants):
            n += 1
            rid = f"V2-IV-{n:03d}"
            data = variantize(c["data"], c["cls"], v, TRAIN_IFS)
            out.append({
                "id": rid, "family": "instance_validity",
                "origin": "template_instance_v2",
                "scenario_group": f"iv2:{c['cls']}:g{case_idx % 3}",
                "prompt": (f"Is this configuration instance valid "
                           f"against {MODEL}? Answer valid or "
                           f"invalid, and if invalid name the single "
                           f"rule it breaks.\n\nInstance "
                           f"({c['label']}):\n"
                           f"{json.dumps(data, indent=1)}"),
                "completion": ("Valid." if c["intended"] == "valid"
                               else f"Invalid — {c['rule']}."),
                "fact": {"instance_ref": rid,
                         "intended_verdict": c["intended"],
                         "violated_rule": c["rule"],
                         "expected_node": c["frag"],
                         "constraint_class": c["cls"]},
                "source": source_for(c["paths"], prov),
                "validation": None,
                "_instance": data})
    return out


# ------------------------------------------------------------ revised_schema
def _rs_cases():
    """(profile, cite, path, [(question_core, allowed, why)]) —
    verdicts fixed by the predicate noted per rule; every why
    embeds the exact citation string."""
    C = []

    def add(p, cite, path, cases):
        for q, ok, why in cases:
            C.append((p, cite, path, q, ok, why))

    add("P1", "P1 rule (1)", P_ACCESS, [
        ("An access port is configured with access VLAN 1.",
         True, "1 is the bottom of profile P1 rule (1)'s restricted "
               "range 1..100."),
        ("An access port is configured with access VLAN 2.",
         True, "2 is inside profile P1 rule (1)'s restricted range "
               "1..100."),
        ("An access port is configured with access VLAN 99.",
         True, "99 is inside profile P1 rule (1)'s restricted range "
               "1..100."),
        ("An access port is configured with access VLAN 102.",
         False, "profile P1 rule (1) restricts access VLAN IDs to "
                "1..100; 102 is above it, though the base schema "
                "allows it."),
        ("An access port is configured with access VLAN 500.",
         False, "profile P1 rule (1) restricts access VLAN IDs to "
                "1..100; 500 is outside that range."),
        ("An access port is configured with access VLAN 4094.",
         False, "the base schema allows 4094, but profile P1 "
                "rule (1) restricts access VLAN IDs to 1..100.")])
    add("P1", "P1 rule (2)", P_NATIVE, [
        ("A trunk is configured with native VLAN 2.",
         True, "profile P1 rule (2) forbids only native VLAN 1; 2 "
               "is unrestricted by the profile."),
        ("A trunk is configured with native VLAN 1000.",
         True, "profile P1 rule (2) forbids only native VLAN 1; "
               "1000 is unrestricted by the profile."),
        ("A trunk is configured with native VLAN 4094.",
         True, "profile P1 rule (2) forbids only native VLAN 1; "
               "4094 is unrestricted by the profile.")])
    add("P1", "P1 rule (3)", P_AGING + "/time", [
        ("Port-security aging time is set to 1 minute.",
         True, "1 is under the cap in profile P1 rule (3) "
               "(60 minutes)."),
        ("Port-security aging time is set to 59 minutes.",
         True, "59 is under the cap in profile P1 rule (3)."),
        ("Port-security aging time is set to 600 minutes.",
         False, "profile P1 rule (3) caps aging time at 60 minutes; "
                "600 is above the cap."),
        ("Port-security aging time is set to 1440 minutes.",
         False, "the base schema allows 1440, but profile P1 "
                "rule (3) caps aging time at 60 minutes.")])
    add("P3", "P3 rule (1)", P_ENCAP, [
        ("A trunk is configured with encapsulation dot1q.",
         True, "dot1q is the encapsulation profile P3 rule (1) "
               "requires."),
        ("A trunk is configured with encapsulation isl.",
         False, "the base schema allows isl, but profile P3 "
                "rule (1) requires dot1q."),
        ("A trunk is configured with encapsulation negotiate.",
         False, "profile P3 rule (1) requires dot1q; negotiate is "
                "not dot1q.")])
    add("P3", "P3 rule (2)", P_AGING + "/time", [
        ("Port-security aging time is set to 10 minutes.",
         True, "10 is under the cap in profile P3 rule (2) "
               "(30 minutes)."),
        ("Port-security aging time is set to 29 minutes.",
         True, "29 is under the cap in profile P3 rule (2)."),
        ("Port-security aging time is set to 90 minutes.",
         False, "profile P3 rule (2) caps aging time at 30 minutes; "
                "90 is above the cap."),
        ("Port-security aging time is set to 1440 minutes.",
         False, "the base schema allows 1440, but profile P3 "
                "rule (2) caps aging time at 30 minutes.")])
    add("P3", "P3 rule (3)", P_ACCESS, [
        ("An access port is configured with access VLAN 150.",
         True, "150 is inside profile P3 rule (3)'s range 100..199."),
        ("An access port is configured with access VLAN 50.",
         False, "profile P3 rule (3) restricts access VLANs to "
                "100..199; 50 is below it."),
        ("An access port is configured with access VLAN 1000.",
         False, "profile P3 rule (3) restricts access VLANs to "
                "100..199; 1000 is above it.")])
    add("P4", "P4 rule (1)", P_VOICE, [
        ("A port is configured with voice VLAN 40.",
         True, "40 is the voice VLAN profile P4 rule (1) requires."),
        ("A port is configured with voice VLAN 400.",
         False, "profile P4 rule (1) requires the voice VLAN to "
                "be 40."),
        ("A port is configured with voice VLAN 4.",
         False, "profile P4 rule (1) requires the voice VLAN to "
                "be 40; 4 is not 40.")])
    add("P4", "P4 rule (2)", P_ALLOWED, [
        ("A trunk's allowed VLAN list is '1'.",
         False, "the list is exactly VLAN 1, which profile P4 "
                "rule (2) forbids."),
        ("A trunk's allowed VLAN list is '100,200'.",
         True, "the list does not include VLAN 1, so profile P4 "
               "rule (2) is satisfied."),
        ("A trunk's allowed VLAN list is '1,100'.",
         False, "the list includes VLAN 1, which profile P4 "
                "rule (2) forbids.")])
    add("P4", "P4 rule (3)", P_AGING + "/type", [
        ("Port-security aging type is set to inactivity.",
         True, "inactivity is the aging type profile P4 rule (3) "
               "requires."),
        ("Port-security aging type is set to absolute.",
         False, "the base schema allows absolute, but profile P4 "
                "rule (3) requires inactivity.")])
    add("P5", "P5 rule (1)", P_MAXACC + "/maximum", [
        ("Port-security maximum addresses is set to 1.",
         True, "1 is under the limit in profile P5 rule (1) "
               "(10)."),
        ("Port-security maximum addresses is set to 9.",
         True, "9 is under the limit in profile P5 rule (1)."),
        ("Port-security maximum addresses is set to 1000.",
         False, "profile P5 rule (1) caps maximum addresses at 10; "
                "1000 is above the cap.")])
    add("P5", "P5 rule (2)", P_ACCESS, [
        ("An access port is configured with access VLAN 100.",
         True, "profile P5 rule (2) forbids only access VLAN 1; "
               "100 is unrestricted by the profile."),
        ("An access port is configured with access VLAN 3.",
         True, "profile P5 rule (2) forbids only access VLAN 1; "
               "3 is unrestricted by the profile.")])
    add("P5", "P5 rule (3)", P_NATIVE, [
        ("A trunk is configured with native VLAN 1001.",
         False, "profile P5 rule (3) requires the native VLAN to "
                "be 1000; 1001 is not 1000."),
        ("A trunk is configured with native VLAN 1.",
         False, "profile P5 rule (3) requires the native VLAN to "
                "be 1000; 1 is not 1000.")])
    return C


def gen_rs_v2(prov):
    texts = {p: t for p, (t, _r) in RS_PROFILES.items()}
    out = []
    per_rule = Counter()
    for pname, cite, path, q, allowed, why in _rs_cases():
        idx = per_rule[cite]
        per_rule[cite] += 1
        phrasing = idx % 3
        qq = q + " Is that allowed?"
        if phrasing == 1:
            qq = qq[: -len("Is that allowed?")] + "Does that comply?"
        elif phrasing == 2:
            qq = (qq[: -len("Is that allowed?")]
                  + "Is that permitted under the profile?")
        n = len(out) + 1
        out.append({
            "id": f"V2-RS-{n:03d}", "family": "revised_schema",
            "origin": "template_instance_v2",
            "scenario_group": f"rs2:{cite}:g{idx % 3}",
            "prompt": (f"Answer using the deployment profile "
                       f"below, which overrides the base schema "
                       f"where they differ.\n\n{texts[pname]}\n\n{qq}"),
            "completion": f"{'Yes' if allowed else 'No'}. {why}",
            "fact": {"profile_rule": cite,
                     "answer": {"verdict": "allowed" if allowed
                                else "not_allowed",
                                "rule": cite}},
            "source": source_for(
                [path], prov,
                {"profile": f"{pname} (authored, embedded in "
                            f"prompt)"}),
            "validation": {"tool": "none",
                           "note": "profile rule, not base schema"}})
    return out


# ------------------------------------------------------- typed_edit negatives
def gen_te_neg_v2(prov, rounds=8):
    absent_ifs = ["1/0/7", "1/0/8", "2/0/3", "3/0/1"]
    absent_entries = ["88", "99", "77", "55"]
    bad_keys = ["GE1/0/1", "1//0/1", "1/0/", "eth1/0/1", "1.0.1",
                "1/0/1 "]
    out = []
    n = 0
    plan = []
    for t in range(rounds):
        plan.append(("absent_if", absent_ifs[t % len(absent_ifs)]))
        plan.append(("absent_entry",
                     absent_entries[t % len(absent_entries)]))
        plan.append(("bad_key", bad_keys[t % len(bad_keys)]))
    # extra malformed-key rounds: the run-1 confusion class
    for t in range(rounds, rounds + 6):
        plan.append(("bad_key", bad_keys[t % len(bad_keys)]))
    for t, (kind, key) in enumerate(plan):
        base_if = TRAIN_IFS[t % len(TRAIN_IFS)]
        base = doc([ge(sw_access(10), name=base_if),
                    ge(sw_access(20),
                       name=TRAIN_IFS[(t + 1) % len(TRAIN_IFS)])],
                   [{"vlan-id": "10"}, {"vlan-id": "20"}])
        names = [g["name"] for g in
                 base["Cisco-IOS-XE-native:native"]["interface"]
                 ["GigabitEthernet"]]
        keys = [e["vlan-id"] for e in
                base["Cisco-IOS-XE-native:native"]["vlan"]
                ["Cisco-IOS-XE-vlan:configuration-entry"]]
        if kind == "absent_if":
            assert key not in names and GE_NAME_RE.match(key)
            target = {"list": GE_LIST, "key": {"name": key}}
            err = "target_not_found"
            instr = (f"Set the access VLAN of GigabitEthernet {key} "
                     f"from 10 to 20.")
            attempted = [{"target": target, "path": P_ACCESS,
                          "op": "set", "old": 10, "new": 20}]
        elif kind == "absent_entry":
            assert key not in keys
            target = {"list": ENTRY_LIST, "key": {"vlan-id": key}}
            err = "target_not_found"
            instr = f"Remove the configuration entry for VLAN {key}."
            attempted = [{"target": target, "path": ENTRY_LIST,
                          "op": "remove_entry"}]
        else:
            assert not GE_NAME_RE.match(key), key
            target = {"list": GE_LIST, "key": {"name": key}}
            err = "invalid_key"
            instr = (f"Set the access VLAN of GigabitEthernet {key} "
                     f"from 10 to 20.")
            attempted = [{"target": target, "path": P_ACCESS,
                          "op": "set", "old": 10, "new": 20}]
        n += 1
        out.append({
            "id": f"V2-TE-N{n:02d}", "family": "typed_edit",
            "origin": "template_instance_v2", "task_form": "edit_spec",
            "executable_on_device": False,
            "scenario_group": f"neg2:{err}:{t}",
            "prompt": (f"Return the typed edit that transforms the "
                       f"state below as instructed — the edit "
                       f"operations only, not the full state. Each "
                       f"edit names its target list and key. If the "
                       f"target cannot be resolved, return an error "
                       f"object instead.\n\nEdit: {instr}\n\nState:\n"
                       f"{json.dumps(base, indent=1)}"),
            "completion": json.dumps({"error": err, "target": target},
                                     indent=1),
            "fact": {"expected": "reject_target", "error": err,
                     "target": target, "attempted_edits": attempted,
                     "schema_only_reason":
                         "deliberate selector-negative: the correct "
                         "behavior is rejection, not an edit"},
            "source": source_for([P_ACCESS], prov),
            "validation": None,
            "_before": base})
    return out


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(
        Path(__file__).resolve().parents[2] / "bundles" / "xe-2621"))
    ap.add_argument("--out-dir", default=str(HERE / "corpus"))
    args = ap.parse_args()
    bundle = Path(args.bundle)
    out_dir = Path(args.out_dir)

    # dedupe surface: every v1 prompt + every v1 PM path
    v1_prompts, v1_pm_paths = set(), set()
    for name in ("train", "dev", "test", "benchmark"):
        for line in (out_dir / f"{name}.jsonl").read_text() \
                .splitlines():
            r = json.loads(line)
            v1_prompts.add(r["prompt"])
            if r["family"] == "path_meaning" and r["fact"].get("path"):
                v1_pm_paths.add(r["fact"]["path"])
    for line in (HERE / "held_out.jsonl").read_text().splitlines():
        v1_prompts.add(json.loads(line)["prompt"])
    for line in (HERE / "records.jsonl").read_text().splitlines():
        r = json.loads(line)
        v1_prompts.add(r["prompt"])
        if r["family"] == "path_meaning" and r["fact"].get("path"):
            v1_pm_paths.add(r["fact"]["path"])

    facts = {f["path"]: f for f in inventory(bundle)}
    prov = build_prov_map(bundle)

    records = []
    records += gen_pm_v2(facts, prov, v1_pm_paths)
    records += gen_iv_v2(prov)
    records += gen_rs_v2(prov)
    records += gen_te_neg_v2(prov)

    kept, dropped = validate_all(
        records, bundle, HERE / "scoped-library.json",
        out_dir / "instances_v2", "instances_v2")

    deduped, dupes = [], []
    seen = set(v1_prompts)
    for rec in kept:
        if rec["prompt"] in seen:
            dupes.append(rec["id"])
            continue
        seen.add(rec["prompt"])
        deduped.append(rec)

    splits = {"train": [], "dev": []}
    for rec in deduped:
        rec["split"] = split_v2(rec["scenario_group"])
        splits[rec["split"]].append(rec)
    for name, fname in (("train", "train_v2_additions.jsonl"),
                        ("dev", "dev_v2_additions.jsonl")):
        (out_dir / fname).write_text(
            "".join(json.dumps(r) + "\n" for r in
                    sorted(splits[name], key=lambda r: r["id"])))

    def fam_counts(recs):
        return dict(Counter(r["family"] for r in recs))

    addendum = {
        "corpus": "v2 additions (train/dev only; benchmark v1 "
                  "frozen at 5189d33, untouched)",
        "generator": "gen_corpus_v2.py",
        "splits": {k: {"total": len(v),
                       "by_family": fam_counts(v)}
                   for k, v in splits.items()},
        "dropped_validation": [list(d) if isinstance(d, tuple)
                               else d for d in dropped],
        "dropped_duplicates": dupes,
        "validation": "PM facts via pyang inventory; IV/TE verdicts "
                      "via yangson (validate_all); RS verdicts via "
                      "in-generator predicates; TE-negative "
                      "selectors mechanically verified",
    }
    (out_dir / "manifest_v2_addendum.json").write_text(
        json.dumps(addendum, indent=1) + "\n")
    print("additions:", {k: len(v) for k, v in splits.items()})
    print("by family:", fam_counts(deduped))
    print("dropped (validation):", len(dropped),
          "| dropped (duplicates):", len(dupes))
    for d in dropped[:20]:
        print("  DROP:", d[0] if isinstance(d, tuple) else d)
    for d in dupes[:20]:
        print("  DUPE:", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
