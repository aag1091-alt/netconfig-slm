#!/usr/bin/env python3
"""Bulk corpus generator for yang-learn (SCALE_DESIGN.md v1 corpus).

Scales the cleared 41-record sample (gen_records.py) to ~1,450
train_candidate records plus a frozen benchmark, under Codex's
amendments (#74):

- P2 is reserved entirely for held-out evaluation (benchmark only);
  benchmark profiles/templates use disjoint parameter domains and a
  reserved path_meaning leaf slice — nothing benchmark-flavored
  appears in train or dev.
- Splits are assigned by scenario group across task forms: all forms
  of one scenario land in the same split.
- Selector-negative typed edits are first-class records with an
  expected-rejection label, mechanically verified; a positive edit
  failing any check is dropped and reported, never relabeled.
- Validation independence is unchanged: facts from pyang, verdicts
  from yangson, expected-node rule verification, apply-checks.

Outputs (yang-learn/corpus/): train.jsonl, dev.jsonl, test.jsonl,
benchmark.jsonl, manifest.json, instances/.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_records import (  # noqa: E402
    GE, MODEL, PROFILE, S, SW, V, build_prov_map, doc, flatten, ge,
    ps, qual, source_for, sw_access, sw_trunk)
from schema_tools import inventory  # noqa: E402

ROOT = "Cisco-IOS-XE-native:native"
P_NAME = GE + "/name"
P_ACCESS = f"{SW}/access/vlan/vlan"
P_ENTRY = "/native/vlan/configuration-entry/vlan-id"
P_ENTRY_LIST = "/native/vlan/configuration-entry"
P_NATIVE = f"{SW}/trunk/native/vlan/vlan-id"
P_MODE = f"{SW}/mode"
P_AGING = f"{SW}/port-security-config/port-security/aging"
P_MAXACC = (f"{SW}/port-security-config/port-security/"
            "maximum-address-config/maximum-vlan-access")
P_ENCAP = f"{SW}/trunk/encapsulation"
P_VOICE = f"{SW}/voice/vlan/vlan"
P_ALLOWED = f"{SW}/trunk/allowed/vlan/vlan-choice/vlans/vlans"
GE_LIST = "/native/interface/GigabitEthernet"
ENTRY_LIST = P_ENTRY_LIST

TRAIN_IFS = ["1/0/1", "1/0/2", "1/0/3", "1/0/4", "1/0/5", "1/0/6",
             "2/0/1", "2/0/2", "1/0/10", "2/0/10"]
BENCH_IFS = ["1/0/21", "1/0/22", "4/0/1"]
GE_NAME_RE = re.compile(
    r"^(0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\.[0-9]*)?$")


def split_of(group: str) -> str:
    h = int(hashlib.sha256(group.encode()).hexdigest(), 16) % 10
    return "train" if h < 8 else ("dev" if h == 8 else "test")


def expand_entry_key(key: str) -> set[int]:
    out: set[int] = set()
    for part in key.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def entries_cover(entries, vlan_ids) -> set[int]:
    covered: set[int] = set()
    for e in entries:
        covered |= expand_entry_key(e["vlan-id"])
    return set(vlan_ids) - covered


# ------------------------------------------------------------- path_meaning
def gen_pm(facts, prov):
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

    pool = {}
    for f in facts.values():
        if not constrained(f):
            continue
        norm = re.sub(r"/interface/[^/]+/", "/interface/*/", f["path"])
        key = (norm, f["type"])
        if key not in pool or prio(f) < prio(pool[key]):
            pool[key] = f
    deduped = sorted(pool.values(), key=lambda f: (prio(f), f["path"]))
    bench_pool = deduped[::7][:48]    # reserved slice, bench only
    train_pool = [f for f in deduped if f not in bench_pool][:350]
    out, bench = [], []
    for tag, selected, sink in (("C", train_pool, out),
                                ("B", bench_pool, bench)):
        for i, f in enumerate(selected, 1):
            src = {**f["source"],
                   "revision": prov[f["path"]]["revision"]}
            if f["when"] and i % 3 == 0:
                w = f["when"][0]
                sink.append({
                    "id": f"{tag}-PM-{i:03d}", "family": "path_meaning",
                    "origin": "generated_fact",
                    "scenario_group": f"pm:{f['path']}",
                    "prompt": (f"In {MODEL}, under what condition may "
                               f"the leaf at {f['path']} be present?"),
                    "completion": (
                        f"It carries a when condition on {w['on']}: "
                        f"`{w['xpath']}` — the node is only valid when "
                        f"that XPath evaluates to true."),
                    "fact": {"path": f["path"], "when": f["when"],
                             "answer": {"when_on": w["on"],
                                        "xpath": w["xpath"]}},
                    "source": src, "validation": None})
            else:
                completion = f"{f['type']}"
                if f["default"] is not None:
                    completion += f" Default: {f['default']}."
                if f["description"]:
                    completion += f" Meaning: {f['description']}"
                sink.append({
                    "id": f"{tag}-PM-{i:03d}", "family": "path_meaning",
                    "origin": "generated_fact",
                    "scenario_group": f"pm:{f['path']}",
                    "prompt": (f"In {MODEL}, what is the type of the "
                               f"leaf at {f['path']}?"),
                    "completion": completion,
                    "fact": {"path": f["path"], "type": f["type"],
                             "default": f["default"],
                             "answer": {"type": f["type"],
                                        "default": f["default"]}},
                    "source": src, "validation": None})
    return out, bench


# --------------------------------------------------------- instance_validity
# Each class yields (label, instance, intended, rule, paths, fragment).
# Exactly one rule is broken per invalid case; the fragment is the
# qualified node the yangson rejection must implicate.
def _acc(vlan, iface, extra=None):
    return doc([ge(sw_access(vlan, extra), name=iface)],
               [{"vlan-id": "10"}])


def _trk(children, iface, extra=None):
    return doc([ge(sw_trunk(children, extra), name=iface)],
               [{"vlan-id": "10"}])


def iv_cases(domain):
    ifs = TRAIN_IFS if domain == "train" else BENCH_IFS
    cases = []

    def add(cls, label, data, intended, rule, paths, frag):
        cases.append({"cls": cls, "label": label, "data": data,
                      "intended": intended, "rule": rule,
                      "paths": paths, "frag": frag})

    # range on the access-VLAN union's numeric branch
    for i, (v, ok) in enumerate([(1, 1), (4094, 1), (36, 1), (0, 0),
                                 (4095, 0), (8192, 0)]):
        add("range_access", f"access VLAN {v}", _acc(v, ifs[i % 3]),
            "valid" if ok else "invalid",
            None if ok else
            "access/vlan/vlan is a union of uint16 range 1..4094 and "
            "the enumeration {dynamic}; the value matches neither",
            [P_ACCESS], "Cisco-IOS-XE-switch:access/"
                        "Cisco-IOS-XE-switch:vlan")
    # union branch / JSON-type traps on the same leaf
    for i, (v, ok) in enumerate([("dynamic", 1), (25, 1), ("4095", 0),
                                 ("10", 0), ("auto", 0)]):
        add("union_access", f"access VLAN {v!r}", _acc(v, ifs[i % 3]),
            "valid" if ok else "invalid",
            None if ok else
            "access/vlan/vlan is a union of uint16 range 1..4094 and "
            "the enumeration {dynamic}; this JSON value matches no "
            "branch (a string is not the numeric branch)",
            [P_ACCESS], "Cisco-IOS-XE-switch:access/"
                        "Cisco-IOS-XE-switch:vlan")
    # trunk encapsulation enum
    for i, (v, ok) in enumerate([("dot1q", 1), ("isl", 1),
                                 ("negotiate", 1), ("802.1q", 0),
                                 ("dot1Q", 0), ("none", 0)]):
        add("enum_encap", f"encapsulation {v}",
            _trk({"encapsulation": v}, ifs[i % 3]),
            "valid" if ok else "invalid",
            None if ok else
            "trunk/encapsulation is an enumeration of "
            "{dot1q, isl, negotiate}",
            [P_ENCAP], "encapsulation")
    # trunk native VLAN range
    for i, (v, ok) in enumerate([(1, 1), (999, 1), (4094, 1), (0, 0),
                                 (4095, 0)]):
        add("range_native", f"native VLAN {v}",
            _trk({"native": {"vlan": {"vlan-id": v}}}, ifs[i % 3]),
            "valid" if ok else "invalid",
            None if ok else
            "trunk/native/vlan/vlan-id is uint16 range 1..4094",
            [P_NATIVE], "native")
    # port-security aging time range
    for i, (v, ok) in enumerate([(1, 1), (60, 1), (1440, 1), (0, 0),
                                 (1441, 0), (2000, 0)]):
        add("range_aging", f"aging time {v}",
            _acc(10, ifs[i % 3], ps(aging={S + "time": v})),
            "valid" if ok else "invalid",
            None if ok else
            "port-security aging/time is uint16 range 1..1440",
            [P_AGING + "/time"], "aging")
    # port-security aging type enum
    for i, (v, ok) in enumerate([("absolute", 1), ("inactivity", 1),
                                 ("shutdown", 0), ("relative", 0)]):
        add("enum_aging", f"aging type {v}",
            _acc(10, ifs[i % 3], ps(aging={S + "type": v})),
            "valid" if ok else "invalid",
            None if ok else
            "port-security aging/type is an enumeration of "
            "{absolute, inactivity}",
            [P_AGING + "/type"], "aging")
    # configuration-entry key pattern
    for i, (v, ok) in enumerate([("25", 1), ("1-10,15", 1), ("4094", 1),
                                 ("10-5", 1), ("4095", 0), ("0", 0),
                                 ("abc", 0), ("9999", 0)]):
        add("pattern_entry", f"entry key {v!r}",
            doc([ge(sw_access(10), name=ifs[i % 3])],
                [{"vlan-id": v}]),
            "valid" if ok else "invalid",
            None if ok else
            "configuration-entry/vlan-id is a string whose pattern "
            "encodes VLAN IDs 1..4094 (and range lists); the key "
            "does not match",
            [P_ENTRY], "configuration-entry")
    # interface name pattern
    for i, (v, ok) in enumerate([("1/0/5", 1), ("0/2", 1),
                                 ("1/0/1.5", 1), ("Gi1/0/1", 0),
                                 ("1/0/1a", 0), ("1//1", 0), ("a/b", 0)]):
        add("pattern_ifname", f"interface name {v!r}",
            _acc(10, v), "valid" if ok else "invalid",
            None if ok else
            "the GigabitEthernet list key 'name' must match the "
            "pattern (0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\\.[0-9]*)? "
            "— numeric slot/port form only",
            [P_NAME], "GigabitEthernet")
    # mode choice exclusivity
    for i, (cases_, ok) in enumerate([
            (["access"], 1), (["trunk"], 1), (["dynamic"], 1),
            (["dot1q-tunnel"], 1), (["access", "trunk"], 0),
            (["access", "dynamic"], 0), (["trunk", "dot1q-tunnel"], 0)]):
        mode = {}
        for c in cases_:
            mode[S + ("dynamic")] = "auto" if c == "dynamic" else None
            if c != "dynamic":
                mode[S + c] = {}
        mode = {k: v for k, v in mode.items() if v is not None}
        sw = {S + "mode": mode,
              S + "access": {S + "vlan": {S + "vlan": 10}}}
        add("choice_mode", f"mode cases {cases_}",
            doc([ge(sw, name=ifs[i % 3])], [{"vlan-id": "10"}]),
            "valid" if ok else "invalid",
            None if ok else
            "mode/mode-choice is a YANG choice: at most one case may "
            "be present; this instance sets two",
            [P_MODE], "Cisco-IOS-XE-switch:mode")
    # when: maximum-vlan-access requires the access case
    for i, (mode_case, ok) in enumerate([("access", 1), ("trunk", 0)]):
        extra = ps(max_access={S + "maximum": 100, S + "vlan": "access"})
        if mode_case == "access":
            data = _acc(10, ifs[i % 3], extra)
        else:
            data = _trk(None, ifs[i % 3], extra)
        add("when_maxacc", f"maximum-vlan-access with mode {mode_case}",
            data, "valid" if ok else "invalid",
            None if ok else
            "maximum-vlan-access carries when "
            "'../../../../mode/access' — it requires the mode "
            "choice's access case",
            [P_MAXACC], "maximum-addres")
    # allowed-vlans union leaf: uint16 branch (range 1..4094) OR a
    # plain string branch. Numeric strings therefore pass via the
    # string branch (schema-valid trap); only non-string values
    # outside 1..4094 match neither branch. Labels follow the type
    # definition (pyang); yangson confirms every one at validation.
    for i, (v, ok) in enumerate([("10,20", 1), ("10", 1), (4094, 1),
                                 ("4095", 1), ("10,4095", 1),
                                 (4095, 0), (0, 0)]):
        add("union_allowed", f"allowed vlans {v!r}",
            _trk({"allowed": {"vlan": {"vlans": v}}}, ifs[i % 3]),
            "valid" if ok else "invalid",
            None if ok else
            "trunk allowed vlans is a union of uint16 range 1..4094 "
            "and string; this non-string value is outside 1..4094, "
            "so it matches neither branch",
            [P_ALLOWED], "vlans")
    # voice VLAN union (enum branch / numeric branch / string branch)
    for i, (v, ok) in enumerate([(30, 1), ("untagged", 1), ("dot1p", 1),
                                 ("4095", 1), ("vlan30", 1),
                                 (4095, 0), (0, 0)]):
        sw = sw_access(10)
        sw[S + "voice"] = {S + "vlan": {S + "vlan": v}}
        add("union_voice", f"voice VLAN {v!r}",
            doc([ge(sw, name=ifs[i % 3])], [{"vlan-id": "10"}]),
            "valid" if ok else "invalid",
            None if ok else
            "voice/vlan/vlan is a union of the enumeration "
            "{dot1p, none, untagged}, uint16 range 1..4094, and "
            "string; this non-string value is outside 1..4094, so "
            "it matches no branch",
            [P_VOICE], "voice")
    return cases


def gen_iv(domain, prov, tag, variants=6):
    out = []
    ifs = TRAIN_IFS if domain == "train" else BENCH_IFS
    per_class = Counter()
    n = 0
    for c in iv_cases(domain):
        case_idx = per_class[c["cls"]]
        per_class[c["cls"]] += 1
        for v in range(variants):
            n += 1
            rid = f"{tag}-IV-{n:03d}"
            data = variantize(c["data"], c["cls"], v, ifs)
            out.append({
                "id": rid, "family": "instance_validity",
                "origin": "template_instance",
                "scenario_group": f"iv:{domain}:{c['cls']}:g"
                                  f"{case_idx % 3}",
                "prompt": (f"Is this configuration instance valid "
                           f"against {MODEL}? Answer valid or invalid, "
                           f"and if invalid name the single rule it "
                           f"breaks.\n\nInstance ({c['label']}):\n"
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


def variantize(data, cls, v, ifs):
    """State-level variants of one IV case: rotate the interface,
    vary the entry set, add a bystander interface. The class's
    subject field is never touched."""
    d = copy.deepcopy(data)
    root = d[ROOT]
    ges = root["interface"]["GigabitEthernet"]
    if cls != "pattern_ifname":
        ges[0]["name"] = ifs[v % len(ifs)]
    if v % 3 == 1 and cls != "pattern_entry":
        root["vlan"][V + "configuration-entry"] = [
            {"vlan-id": "10"}, {"vlan-id": "20"}]
    if v % 3 == 2:
        ges.append(ge(sw_access(30), name=ifs[(v + 3) % len(ifs)]))
        if cls != "pattern_entry":
            root["vlan"][V + "configuration-entry"] = [
                {"vlan-id": "10"}, {"vlan-id": "30"}]
    return d


# ------------------------------------------------- scenario engine (SR + TE)
# A scenario builds (before, after) states for one narrow edit. Both
# task forms are emitted from the same scenario object so they always
# share a split. executable_on_device is COMPUTED from the states.
def _sw_of(state, iface):
    for g in state[ROOT]["interface"]["GigabitEthernet"]:
        if g["name"] == iface:
            return g["switchport-config"]["switchport"]
    raise KeyError(iface)


def _entries_of(state):
    return state[ROOT].get("vlan", {}).get(V + "configuration-entry",
                                           [])


def referenced_vlans(sw):
    ids = set()
    acc = sw.get(S + "access", {}).get(S + "vlan", {}).get(S + "vlan")
    if isinstance(acc, int):
        ids.add(acc)
    voice = sw.get(S + "voice", {}).get(S + "vlan", {}).get(S + "vlan")
    if isinstance(voice, int):
        ids.add(voice)
    trunk = sw.get(S + "trunk", {})
    nat = (trunk.get(S + "native", {}).get(S + "vlan", {})
           .get(S + "vlan-id"))
    if isinstance(nat, int):
        ids.add(nat)
    allowed = (trunk.get(S + "allowed", {}).get(S + "vlan", {})
               .get(S + "vlans"))
    if isinstance(allowed, str):
        ids |= expand_entry_key(allowed)
    elif isinstance(allowed, int):
        ids.add(allowed)
    return ids


def compute_executable(kind, before, after, iface, new_encap=None):
    if kind == "remove_entry":
        return False, ("deletion case — labeled schema-only, separate "
                       "from executable edit labels")
    if kind == "set_encap" and new_encap in ("isl", "negotiate"):
        return False, (f"sets the legacy encapsulation enum "
                       f"'{new_encap}' — labeled schema-only")
    missing = entries_cover(_entries_of(after),
                            referenced_vlans(_sw_of(after, iface)))
    if missing:
        return False, (f"resulting state references VLAN(s) "
                       f"{sorted(missing)} with no configuration-entry "
                       f"(missing VLAN prerequisite)")
    return True, None


def build_scenario(kind, iface, a, b, entries, bystanders):
    """Returns (before, after, instruction, edited_path, edits)."""
    def base_ifaces(target_sw):
        ifaces = [ge(target_sw, name=iface)]
        for name, sw in bystanders:
            ifaces.append(ge(sw, name=name))
        return ifaces

    ent = [{"vlan-id": e} for e in entries]
    if kind == "set_access_vlan":
        before = doc(base_ifaces(sw_access(a)), ent)
        after = doc(base_ifaces(sw_access(b)), ent)
        instr = (f"Set the access VLAN of GigabitEthernet {iface} "
                 f"from {a} to {b}. Change nothing else.")
        edits = [{"target": {"list": GE_LIST, "key": {"name": iface}},
                  "path": P_ACCESS, "op": "set", "old": a, "new": b}]
        return before, after, instr, P_ACCESS, edits
    if kind == "swap_mode":
        if a == "access":
            bsw = sw_access(10)
            asw = {S + "mode": {S + "trunk": {}},
                   S + "access": {S + "vlan": {S + "vlan": 10}}}
            old, new = "access", "trunk"
        else:
            bsw = {S + "mode": {S + "trunk": {}},
                   S + "access": {S + "vlan": {S + "vlan": 10}}}
            asw = sw_access(10)
            old, new = "trunk", "access"
        before = doc(base_ifaces(bsw), ent)
        after = doc(base_ifaces(asw), ent)
        instr = (f"Change GigabitEthernet {iface} from {old} mode to "
                 f"{new} mode. Change nothing else.")
        edits = [{"target": {"list": GE_LIST, "key": {"name": iface}},
                  "path": P_MODE, "op": "swap_case", "old": old,
                  "new": new}]
        return before, after, instr, P_MODE, edits
    if kind == "set_native_vlan":
        before = doc(base_ifaces(sw_trunk()), ent)
        after = doc(base_ifaces(sw_trunk(
            {"native": {"vlan": {"vlan-id": b}}})), ent)
        instr = (f"Set the trunk native VLAN of GigabitEthernet "
                 f"{iface} to {b}. Change nothing else.")
        edits = [{"target": {"list": GE_LIST, "key": {"name": iface}},
                  "path": P_NATIVE, "op": "set", "old": None,
                  "new": b}]
        return before, after, instr, P_NATIVE, edits
    if kind == "add_entry":
        before = doc(base_ifaces(sw_access(10)), ent)
        after = doc(base_ifaces(sw_access(10)),
                    ent + [{"vlan-id": str(b)}])
        instr = f"Add a configuration entry for VLAN {b}."
        edits = [{"target": {"list": ENTRY_LIST}, "path": ENTRY_LIST,
                  "op": "add_entry", "new": {"vlan-id": str(b)}}]
        return before, after, instr, ENTRY_LIST, edits
    if kind == "set_voice_vlan":
        bsw = sw_access(10)
        asw = sw_access(10)
        asw[S + "voice"] = {S + "vlan": {S + "vlan": b}}
        before = doc(base_ifaces(bsw), ent)
        after = doc(base_ifaces(asw), ent)
        instr = (f"Set the voice VLAN of GigabitEthernet {iface} "
                 f"to {b}. Change nothing else.")
        edits = [{"target": {"list": GE_LIST, "key": {"name": iface}},
                  "path": P_VOICE, "op": "set", "old": None, "new": b}]
        return before, after, instr, P_VOICE, edits
    if kind == "remove_entry":
        before = doc(base_ifaces(sw_access(10)),
                     ent + [{"vlan-id": str(b)}])
        after = doc(base_ifaces(sw_access(10)), ent)
        instr = f"Remove the configuration entry for VLAN {b}."
        edits = [{"target": {"list": ENTRY_LIST,
                             "key": {"vlan-id": str(b)}},
                  "path": ENTRY_LIST, "op": "remove_entry"}]
        return before, after, instr, ENTRY_LIST, edits
    if kind == "set_allowed":
        before = doc(base_ifaces(sw_trunk(
            {"allowed": {"vlan": {"vlans": a}}})), ent)
        after = doc(base_ifaces(sw_trunk(
            {"allowed": {"vlan": {"vlans": b}}})), ent)
        instr = (f"Set the trunk allowed VLANs of GigabitEthernet "
                 f"{iface} to {b}. Change nothing else.")
        edits = [{"target": {"list": GE_LIST, "key": {"name": iface}},
                  "path": P_ALLOWED, "op": "set", "old": a, "new": b}]
        return before, after, instr, P_ALLOWED, edits
    if kind == "set_encap":
        before = doc(base_ifaces(sw_trunk()), ent)
        after = doc(base_ifaces(sw_trunk({"encapsulation": b})), ent)
        instr = (f"Set the trunk encapsulation of GigabitEthernet "
                 f"{iface} to {b}. Change nothing else.")
        edits = [{"target": {"list": GE_LIST, "key": {"name": iface}},
                  "path": P_ENCAP, "op": "set", "old": None, "new": b}]
        return before, after, instr, P_ENCAP, edits
    raise ValueError(kind)


def apply_scenario_edit(before, kind, edits):
    """Apply an edit spec to a state (corpus apply-check)."""
    st = copy.deepcopy(before)
    root = st[ROOT]
    for e in edits:
        tgt = e["target"]
        if tgt["list"] == GE_LIST:
            entry = next(g for g in root["interface"]["GigabitEthernet"]
                         if g["name"] == tgt["key"]["name"])
            sw = entry["switchport-config"]["switchport"]
            if e["op"] == "set" and e["path"] == P_ACCESS:
                sw[S + "access"][S + "vlan"][S + "vlan"] = e["new"]
            elif e["op"] == "swap_case":
                sw[S + "mode"] = {S + e["new"]: {}}
            elif e["op"] == "set" and e["path"] == P_NATIVE:
                sw.setdefault(S + "trunk", {}).setdefault(
                    S + "native", {}).setdefault(
                    S + "vlan", {})[S + "vlan-id"] = e["new"]
            elif e["op"] == "set" and e["path"] == P_VOICE:
                sw.setdefault(S + "voice", {}).setdefault(
                    S + "vlan", {})[S + "vlan"] = e["new"]
            elif e["op"] == "set" and e["path"] == P_ALLOWED:
                sw.setdefault(S + "trunk", {}).setdefault(
                    S + "allowed", {}).setdefault(
                    S + "vlan", {})[S + "vlans"] = e["new"]
            elif e["op"] == "set" and e["path"] == P_ENCAP:
                sw.setdefault(S + "trunk", {})[
                    S + "encapsulation"] = e["new"]
            else:
                raise ValueError(f"unsupported edit {e}")
        elif tgt["list"] == ENTRY_LIST:
            entries = root["vlan"][V + "configuration-entry"]
            if e["op"] == "add_entry":
                entries.append(e["new"])
            elif e["op"] == "remove_entry":
                root["vlan"][V + "configuration-entry"] = [
                    x for x in entries
                    if x["vlan-id"] != tgt["key"]["vlan-id"]]
            else:
                raise ValueError(f"unsupported edit {e}")
        else:
            raise ValueError(f"unsupported target {tgt}")
    return st


KIND_GRIDS = {
    "set_access_vlan": [(10, 20), (10, 30), (20, 40), (30, 10),
                        (40, 100), (10, 4094), (100, 200), (50, 60)],
    "swap_mode": ["access", "trunk"],
    "set_native_vlan": [99, 100, 1, 4094, 777, 555],
    "add_entry": [20, 30, 50, 200, 500],
    "set_voice_vlan": [30, 40, 50, 60],
    "remove_entry": [20, 30, 50],
    "set_allowed": [("10", "10,20"), ("10,20", "10"),
                    ("10", "10,20,30"), ("10,20", "20,30"),
                    ("10", "30,40")],
    "set_encap": ["dot1q", "isl", "negotiate"],
}
ENTRY_POOLS = [["10"], ["10", "20"], ["10", "20", "30"], ["1-10,15"],
               ["10", "100"]]


def gen_scenarios(domain, prov, tag, quota, te_stride=5):
    ifs = TRAIN_IFS if domain == "train" else BENCH_IFS
    out = []
    sr_n = te_n = 0
    for kind, grid in KIND_GRIDS.items():
        made = 0
        idx = 0
        while made < quota:
            params = grid[idx % len(grid)]
            iface = ifs[(idx * 3 + len(kind)) % len(ifs)]
            entries = ENTRY_POOLS[idx % len(ENTRY_POOLS)]
            idx += 1
            if kind in ("add_entry", "remove_entry") \
                    and str(params) in entries:
                continue
            others = [x for x in ifs if x != iface]
            bystanders = []
            if idx % 3 == 1:
                bystanders = [(others[idx % len(others)],
                               sw_access(30))]
            elif idx % 3 == 2:
                bystanders = [(others[idx % len(others)],
                               sw_access(30)),
                              (others[(idx + 1) % len(others)],
                               sw_trunk())]
            if kind in ("set_access_vlan", "set_allowed"):
                a, b = params
            elif kind == "swap_mode":
                a, b = params, None
            else:
                a, b = None, params
            before, after, instr, epath, edits = build_scenario(
                kind, iface, a, b, entries, bystanders)
            _names = [g["name"] for g in
                      before[ROOT]["interface"]["GigabitEthernet"]]
            assert len(_names) == len(set(_names)), (kind, _names)
            executable, so_reason = compute_executable(
                kind, before, after, iface,
                new_encap=b if kind == "set_encap" else None)
            group = f"scen:{domain}:{kind}:{made}"
            fb, fa = flatten(before), flatten(after)
            changed = sorted(p for p in set(fb) | set(fa)
                             if fb.get(p) != fa.get(p))
            preserved = sorted(p for p in set(fb) & set(fa)
                               if fb.get(p) == fa.get(p))
            sr_n += 1
            out.append({
                "id": f"{tag}-SR-{sr_n:03d}",
                "family": "state_reconstruction",
                "origin": "template_instance", "task_form": "full_state",
                "executable_on_device": executable,
                "scenario_group": group,
                "prompt": (f"Apply this edit to the configuration "
                           f"state and return the full resulting "
                           f"state. Preserve every value the edit "
                           f"does not change.\n\nEdit: {instr}\n\n"
                           f"State:\n{json.dumps(before, indent=1)}"),
                "completion": json.dumps(after, indent=1),
                "fact": {"edited_path": epath,
                         "schema_only_reason": so_reason,
                         "changed_paths": changed,
                         "preserved_paths": preserved},
                "source": source_for([epath], prov),
                "validation": None,
                "_before": before, "_after": after})
            if made % te_stride != te_stride - 1:
                te_n += 1
                out.append({
                    "id": f"{tag}-TE-{te_n:03d}", "family": "typed_edit",
                    "origin": "template_instance",
                    "task_form": "edit_spec",
                    "executable_on_device": executable,
                    "scenario_group": group,
                    "prompt": (f"Return the typed edit that transforms "
                               f"the state below as instructed — the "
                               f"edit operations only, not the full "
                               f"state. Each edit names its target "
                               f"list and key. Preserve every value "
                               f"the edit does not touch.\n\nEdit: "
                               f"{instr}\n\nState:\n"
                               f"{json.dumps(before, indent=1)}"),
                    "completion": json.dumps({"edits": edits},
                                             indent=1),
                    "fact": {"edits": edits, "edited_path": epath,
                             "schema_only_reason": so_reason},
                    "source": source_for([epath], prov),
                    "validation": None,
                    "_before": before, "_after": after,
                    "_edits": edits, "_kind": kind})
            made += 1
    return out


def gen_te_negatives(domain, prov, tag, per_type):
    ifs = TRAIN_IFS if domain == "train" else BENCH_IFS
    absent_if = "1/0/9" if domain == "train" else "4/0/9"
    out = []
    n = 0
    for t in range(per_type):
        base_if = ifs[t % len(ifs)]
        base = doc([ge(sw_access(10), name=base_if),
                    ge(sw_access(20), name=ifs[(t + 1) % len(ifs)])],
                   [{"vlan-id": "10"}, {"vlan-id": "20"}])
        variants = [
            ({"list": GE_LIST, "key": {"name": absent_if}},
             "target_not_found",
             f"Set the access VLAN of GigabitEthernet {absent_if} "
             f"from 10 to 20.",
             [{"target": {"list": GE_LIST,
                          "key": {"name": absent_if}},
               "path": P_ACCESS, "op": "set", "old": 10, "new": 20}]),
            ({"list": ENTRY_LIST, "key": {"vlan-id": "77"}},
             "target_not_found",
             "Remove the configuration entry for VLAN 77.",
             [{"target": {"list": ENTRY_LIST,
                          "key": {"vlan-id": "77"}},
               "path": ENTRY_LIST, "op": "remove_entry"}]),
            ({"list": GE_LIST, "key": {"name": "Gi1/0/1"}},
             "invalid_key",
             "Set the access VLAN of GigabitEthernet Gi1/0/1 "
             "from 10 to 20.",
             [{"target": {"list": GE_LIST,
                          "key": {"name": "Gi1/0/1"}},
               "path": P_ACCESS, "op": "set", "old": 10, "new": 20}]),
        ]
        for target, err, instr, attempted in variants:
            # mechanical verification of the negative itself
            names = [g["name"] for g in
                     base[ROOT]["interface"]["GigabitEthernet"]]
            keys = [e["vlan-id"] for e in
                    base[ROOT]["vlan"][V + "configuration-entry"]]
            if err == "target_not_found" and \
                    target["list"] == GE_LIST:
                assert target["key"]["name"] not in names
            if err == "target_not_found" and \
                    target["list"] == ENTRY_LIST:
                assert target["key"]["vlan-id"] not in keys
            if err == "invalid_key":
                assert not GE_NAME_RE.match(target["key"]["name"])
            n += 1
            out.append({
                "id": f"{tag}-TE-N{n:02d}", "family": "typed_edit",
                "origin": "template_instance", "task_form": "edit_spec",
                "executable_on_device": False,
                "scenario_group": f"neg:{domain}:{err}:{t}",
                "prompt": (f"Return the typed edit that transforms "
                           f"the state below as instructed — the "
                           f"edit operations only, not the full "
                           f"state. Each edit names its target list "
                           f"and key. If the target cannot be "
                           f"resolved, return an error object "
                           f"instead.\n\nEdit: {instr}\n\nState:\n"
                           f"{json.dumps(base, indent=1)}"),
                "completion": json.dumps(
                    {"error": err, "target": target}, indent=1),
                "fact": {"expected": "reject_target", "error": err,
                         "target": target, "attempted_edits": attempted,
                         "schema_only_reason":
                             "deliberate selector-negative: the "
                             "correct behavior is rejection, not an "
                             "edit"},
                "source": source_for([P_ACCESS], prov),
                "validation": None,
                "_before": base})
    return out


# ------------------------------------------------------------ revised_schema
P2_TEXT = ("Deployment profile P2 (branch standard): "
           "(1) every trunk's native VLAN must be 999; "
           "(2) access VLAN IDs must be even numbers.")
P3_TEXT = ("Deployment profile P3 (datacenter standard): "
           "(1) trunk encapsulation must be dot1q; "
           "(2) port-security aging time is capped at 30 minutes; "
           "(3) access VLAN IDs are restricted to 100..199.")
P4_TEXT = ("Deployment profile P4 (branch voice standard): "
           "(1) the voice VLAN must be 40; "
           "(2) trunk allowed VLAN lists must not include VLAN 1; "
           "(3) port-security aging type must be inactivity.")
P5_TEXT = ("Deployment profile P5 (migration standard): "
           "(1) port-security maximum addresses must not exceed 10; "
           "(2) access VLAN 1 is forbidden; "
           "(3) a trunk's native VLAN must be 1000.")
P6_TEXT = ("Deployment profile P6 (evaluation reserve): "
           "(1) access VLAN IDs must be odd numbers; "
           "(2) a trunk's native VLAN must be 777; "
           "(3) port-security aging time must be at least 10 minutes.")

# profile -> (text, [(cite, path, [(question, allowed, rationale)])])
RS_PROFILES = {
    "P1": (PROFILE, [
        ("P1 rule (1)", P_ACCESS, [
            ("An access port is configured with access VLAN 50. "
             "Is that allowed?", True,
             "50 is inside the base range 1..4094 and inside "
             "profile P1 rule (1)'s restricted range 1..100."),
            ("An access port is configured with access VLAN 250. "
             "Is that allowed?", False,
             "the base schema allows 1..4094, but profile P1 "
             "rule (1) restricts access VLAN IDs to 1..100."),
            ("An access port is configured with access VLAN 100. "
             "Is that allowed?", True,
             "100 is the top of profile P1 rule (1)'s range 1..100."),
            ("An access port is configured with access VLAN 101. "
             "Is that allowed?", False,
             "101 is outside profile P1 rule (1)'s range 1..100, "
             "though the base schema allows it.")]),
        ("P1 rule (2)", P_NATIVE, [
            ("A trunk is configured with native VLAN 1. "
             "Is that allowed?", False,
             "the base schema allows native VLAN 1, but profile "
             "P1 rule (2) forbids native VLAN 1 on trunks."),
            ("A trunk is configured with native VLAN 99. "
             "Is that allowed?", True,
             "profile P1 rule (2) forbids only native VLAN 1; "
             "99 is unrestricted by the profile.")]),
        ("P1 rule (3)", P_AGING + "/time", [
            ("Port-security aging time is set to 60 minutes. "
             "Is that allowed?", True,
             "60 is exactly the cap in profile P1 rule (3)."),
            ("Port-security aging time is set to 61 minutes. "
             "Is that allowed?", False,
             "the base schema allows 1..1440, but profile P1 "
             "rule (3) caps aging time at 60 minutes."),
            ("Port-security aging time is set to 120 minutes. "
             "Is that allowed?", False,
             "profile P1 rule (3) caps aging time at 60 minutes.")]),
    ]),
    "P3": (P3_TEXT, [
        ("P3 rule (1)", P_ENCAP, [
            ("A trunk is configured with encapsulation dot1q. "
             "Is that allowed?", True,
             "dot1q is the encapsulation profile P3 rule (1) "
             "requires."),
            ("A trunk is configured with encapsulation isl. "
             "Is that allowed?", False,
             "the base schema allows isl, but profile P3 rule (1) "
             "requires dot1q."),
            ("A trunk is configured with encapsulation negotiate. "
             "Is that allowed?", False,
             "profile P3 rule (1) requires dot1q; negotiate is "
             "not dot1q.")]),
        ("P3 rule (2)", P_AGING + "/time", [
            ("Port-security aging time is set to 30 minutes. "
             "Is that allowed?", True,
             "30 is exactly the cap in profile P3 rule (2)."),
            ("Port-security aging time is set to 31 minutes. "
             "Is that allowed?", False,
             "profile P3 rule (2) caps aging time at 30 minutes."),
            ("Port-security aging time is set to 45 minutes. "
             "Is that allowed?", False,
             "profile P3 rule (2) caps aging time at 30 minutes, "
             "though the base schema allows up to 1440.")]),
        ("P3 rule (3)", P_ACCESS, [
            ("An access port is configured with access VLAN 100. "
             "Is that allowed?", True,
             "100 is the bottom of profile P3 rule (3)'s range "
             "100..199."),
            ("An access port is configured with access VLAN 199. "
             "Is that allowed?", True,
             "199 is the top of profile P3 rule (3)'s range "
             "100..199."),
            ("An access port is configured with access VLAN 99. "
             "Is that allowed?", False,
             "profile P3 rule (3) restricts access VLANs to "
             "100..199; 99 is below it."),
            ("An access port is configured with access VLAN 200. "
             "Is that allowed?", False,
             "profile P3 rule (3) restricts access VLANs to "
             "100..199; 200 is above it.")]),
    ]),
    "P4": (P4_TEXT, [
        ("P4 rule (1)", P_VOICE, [
            ("A port is configured with voice VLAN 40. "
             "Is that allowed?", True,
             "40 is the voice VLAN profile P4 rule (1) requires."),
            ("A port is configured with voice VLAN 41. "
             "Is that allowed?", False,
             "profile P4 rule (1) requires the voice VLAN to "
             "be 40.")]),
        ("P4 rule (2)", P_ALLOWED, [
            ("A trunk's allowed VLAN list is '10,20'. "
             "Is that allowed?", True,
             "the list does not include VLAN 1, so profile P4 "
             "rule (2) is satisfied."),
            ("A trunk's allowed VLAN list is '1,10,20'. "
             "Is that allowed?", False,
             "the list includes VLAN 1, which profile P4 "
             "rule (2) forbids."),
            ("A trunk's allowed VLAN list is '10,21'. "
             "Is that allowed?", True,
             "VLAN 21 is not VLAN 1; profile P4 rule (2) is "
             "satisfied.")]),
        ("P4 rule (3)", P_AGING + "/type", [
            ("Port-security aging type is set to inactivity. "
             "Is that allowed?", True,
             "inactivity is the aging type profile P4 rule (3) "
             "requires."),
            ("Port-security aging type is set to absolute. "
             "Is that allowed?", False,
             "the base schema allows absolute, but profile P4 "
             "rule (3) requires inactivity.")]),
    ]),
    "P5": (P5_TEXT, [
        ("P5 rule (1)", P_MAXACC + "/maximum", [
            ("Port-security maximum addresses is set to 10. "
             "Is that allowed?", True,
             "10 is exactly the limit in profile P5 rule (1)."),
            ("Port-security maximum addresses is set to 11. "
             "Is that allowed?", False,
             "profile P5 rule (1) caps maximum addresses at 10."),
            ("Port-security maximum addresses is set to 100. "
             "Is that allowed?", False,
             "profile P5 rule (1) caps maximum addresses at 10, "
             "though the base schema allows more.")]),
        ("P5 rule (2)", P_ACCESS, [
            ("An access port is configured with access VLAN 1. "
             "Is that allowed?", False,
             "the base schema allows VLAN 1, but profile P5 "
             "rule (2) forbids access VLAN 1."),
            ("An access port is configured with access VLAN 2. "
             "Is that allowed?", True,
             "profile P5 rule (2) forbids only access VLAN 1.")]),
        ("P5 rule (3)", P_NATIVE, [
            ("A trunk is configured with native VLAN 1000. "
             "Is that allowed?", True,
             "1000 is the native VLAN profile P5 rule (3) "
             "requires."),
            ("A trunk is configured with native VLAN 999. "
             "Is that allowed?", False,
             "profile P5 rule (3) requires the native VLAN to "
             "be 1000.")]),
    ]),
}
RS_BENCH_PROFILES = {
    "P2": (P2_TEXT, [
        ("P2 rule (1)", P_NATIVE, [
            ("A trunk's native VLAN is 999. Is that allowed?",
             True, "999 is the native VLAN profile P2 rule (1) "
                   "requires."),
            ("A trunk's native VLAN is 99. Is that allowed?",
             False, "profile P2 rule (1) requires the native "
                    "VLAN to be 999."),
            ("A trunk's native VLAN is 1000. Is that allowed?",
             False, "profile P2 rule (1) requires the native "
                    "VLAN to be 999, not 1000.")]),
        ("P2 rule (2)", P_ACCESS, [
            ("An access port uses VLAN 52. Is that allowed?",
             True, "52 is even, satisfying profile P2 rule (2)."),
            ("An access port uses VLAN 51. Is that allowed?",
             False, "51 is odd; profile P2 rule (2) requires "
                    "even access VLAN IDs."),
            ("An access port uses VLAN 100. Is that allowed?",
             True, "100 is even, satisfying profile P2 "
                   "rule (2).")]),
    ]),
    "P6": (P6_TEXT, [
        ("P6 rule (1)", P_ACCESS, [
            ("An access port uses VLAN 51. Is that allowed?",
             True, "51 is odd, satisfying profile P6 rule (1)."),
            ("An access port uses VLAN 52. Is that allowed?",
             False, "52 is even; profile P6 rule (1) requires "
                    "odd access VLAN IDs.")]),
        ("P6 rule (2)", P_NATIVE, [
            ("A trunk's native VLAN is 777. Is that allowed?",
             True, "777 is the native VLAN profile P6 rule (2) "
                   "requires."),
            ("A trunk's native VLAN is 778. Is that allowed?",
             False, "profile P6 rule (2) requires the native "
                    "VLAN to be 777.")]),
        ("P6 rule (3)", P_AGING + "/time", [
            ("Port-security aging time is set to 5 minutes. "
             "Is that allowed?", False,
             "profile P6 rule (3) requires aging time of at "
             "least 10 minutes."),
            ("Port-security aging time is set to 10 minutes. "
             "Is that allowed?", True,
             "10 is exactly the floor in profile P6 rule (3)."),
            ("Port-security aging time is set to 60 minutes. "
             "Is that allowed?", True,
             "60 is above the floor in profile P6 rule (3).")]),
    ]),
}


def gen_rs(profiles, prov, tag):
    out = []
    n = 0
    for pname, (text, rules) in profiles.items():
        for cite, path, cases in rules:
            for vi, (q, allowed, why) in enumerate(cases):
                for phrasing in (0, 1):
                    n += 1
                    qq = q if phrasing == 0 else q.replace(
                        "Is that allowed?", "Does that comply?")
                    out.append({
                        "id": f"{tag}-RS-{n:03d}",
                        "family": "revised_schema",
                        "origin": "template_instance",
                        "scenario_group": f"rs:{pname}:{cite}",
                        "prompt": (f"Answer using the deployment "
                                   f"profile below, which overrides "
                                   f"the base schema where they "
                                   f"differ.\n\n{text}\n\n{qq}"),
                        "completion": (f"{'Yes' if allowed else 'No'}"
                                       f". {why}"),
                        "fact": {"profile_rule": cite,
                                 "answer": {
                                     "verdict": "allowed" if allowed
                                     else "not_allowed",
                                     "rule": cite}},
                        "source": source_for(
                            [path], prov,
                            {"profile": f"{pname} (authored, "
                                        f"embedded in prompt)"}),
                        "validation": {"tool": "none",
                                       "note": "profile rule, not "
                                               "base schema"}})
    return out


# ---------------------------------------------------------------- validation
REJECTION_CLASSES = {"RawTypeError", "YangTypeError", "SchemaError"}


def run_validator(bundle, library, inst_dir, files):
    verdicts = {}
    here = Path(__file__).resolve().parent
    for i in range(0, len(files), 400):
        chunk = files[i:i + 400]
        proc = subprocess.run(
            [sys.executable, str(here / "validate_instances.py"),
             "--bundle", str(bundle), "--library", str(library)] +
            [str(inst_dir / f) for f in chunk],
            capture_output=True, text=True, timeout=1800)
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                v = json.loads(line)
                verdicts[v["file"]] = v
        if not any(f in verdicts for f in chunk):
            raise SystemExit(f"validator failed on chunk {i}: "
                             f"{proc.stderr[-500:]}")
    return verdicts


def validate_all(records, bundle, library, inst_dir, prefix):
    inst_dir.mkdir(parents=True, exist_ok=True)
    for stale in inst_dir.glob("*.json"):
        stale.unlink()
    files = []
    for rec in records:
        for key, suffix in (("_instance", "inst"),
                            ("_before", "before"),
                            ("_after", "after")):
            if key in rec:
                name = f"{rec['id']}-{suffix}.json"
                (inst_dir / name).write_text(
                    json.dumps(rec[key], indent=1))
                files.append(name)
                rec.setdefault("fact", {})[
                    {"inst": "instance_file", "before": "before_file",
                     "after": "after_file"}[suffix]] = \
                    f"{prefix}/{name}"
    verdicts = run_validator(bundle, library, inst_dir, sorted(files))
    if len(verdicts) != len(files):
        raise SystemExit(f"validator returned {len(verdicts)}/"
                         f"{len(files)} verdicts")
    kept, dropped = [], []
    for rec in records:
        fam = rec["family"]
        if fam == "instance_validity":
            v = verdicts[f"{rec['id']}-inst.json"]
            detail = v["detail"] or ""
            err_class = detail.split(":", 1)[0] if detail else None
            if rec["fact"]["intended_verdict"] == "valid":
                kind = ("accepted" if v["verdict"] == "valid"
                        else "unexpected_rejection")
                rec["validation"] = {"tool": "yangson",
                                     "verdict": v["verdict"],
                                     "verdict_kind": kind,
                                     "detail": v["detail"]}
                (kept if kind == "accepted" else dropped).append(
                    rec if kind == "accepted" else
                    (rec["id"], "intended valid", v))
            else:
                expect = rec["fact"]["expected_node"]
                kind = ("expected_rejection"
                        if v["verdict"] == "invalid"
                        and expect in detail
                        and err_class in REJECTION_CLASSES
                        else "validator_exception")
                rec["validation"] = {"tool": "yangson",
                                     "verdict": v["verdict"],
                                     "verdict_kind": kind,
                                     "expected_node": expect,
                                     "detail": v["detail"]}
                (kept if kind == "expected_rejection" else
                 dropped).append(
                    rec if kind == "expected_rejection" else
                    (rec["id"], f"expected rejection at {expect}", v))
        elif fam in ("state_reconstruction", "typed_edit") \
                and "_after" in rec:
            vb = verdicts[f"{rec['id']}-before.json"]
            va = verdicts[f"{rec['id']}-after.json"]
            rec["validation"] = {
                "tool": "yangson", "before": vb["verdict"],
                "after": va["verdict"],
                "detail": va["detail"] or vb["detail"]}
            ok = vb["verdict"] == "valid" and va["verdict"] == "valid"
            if ok and fam == "typed_edit":
                try:
                    applied = apply_scenario_edit(
                        rec["_before"], rec["_kind"], rec["_edits"])
                    ok = applied == rec["_after"]
                    rec["validation"]["apply_check"] = \
                        "pass" if ok else "FAIL"
                except Exception as e:  # noqa: BLE001
                    ok = False
                    rec["validation"]["apply_check"] = f"error: {e}"
            (kept if ok else dropped).append(
                rec if ok else (rec["id"], "states valid + apply",
                                rec["validation"]))
        elif fam == "typed_edit":  # selector-negative
            vb = verdicts[f"{rec['id']}-before.json"]
            rec["validation"] = {
                "tool": "yangson+mechanical",
                "before": vb["verdict"],
                "selector_check": "verified at generation "
                                  "(absence / key pattern)"}
            (kept if vb["verdict"] == "valid" else dropped).append(
                rec if vb["verdict"] == "valid" else
                (rec["id"], "negative's base state valid", vb))
        else:
            kept.append(rec)
    for rec in kept:
        for k in ("_instance", "_before", "_after", "_edits",
                  "_kind"):
            rec.pop(k, None)
    return kept, dropped


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(
        Path(__file__).resolve().parents[2] / "bundles" / "xe-2621"))
    ap.add_argument("--out-dir", default=str(
        Path(__file__).resolve().parent / "corpus"))
    args = ap.parse_args()
    bundle = Path(args.bundle)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent

    facts = {f["path"]: f for f in inventory(bundle)}
    prov = build_prov_map(bundle)

    corpus, bench = [], []
    pm_train, pm_bench = gen_pm(facts, prov)
    corpus += pm_train
    bench += pm_bench
    corpus += gen_iv("train", prov, "C", variants=6)
    bench += gen_iv("bench", prov, "B", variants=1)
    corpus += gen_scenarios("train", prov, "C", quota=37)
    bench += gen_scenarios("bench", prov, "B", quota=5)
    corpus += gen_te_negatives("train", prov, "C", per_type=10)
    bench += gen_te_negatives("bench", prov, "B", per_type=3)
    corpus += gen_rs(RS_PROFILES, prov, "C")
    bench += gen_rs(RS_BENCH_PROFILES, prov, "B")

    kept_c, dropped_c = validate_all(
        corpus, bundle, here / "scoped-library.json",
        out_dir / "instances", "instances")
    kept_b, dropped_b = validate_all(
        bench, bundle, here / "scoped-library.json",
        out_dir / "bench_instances", "bench_instances")

    # fold in the cleared sample (train split, already validated)
    sample = [json.loads(l) for l in
              (here / "records.jsonl").read_text().splitlines()]
    for rec in sample:
        rec["split"] = "train"
        rec["scenario_group"] = f"sample:{rec['id']}"
    kept_c += sample

    splits = {"train": [], "dev": [], "test": []}
    for rec in kept_c:
        rec.setdefault("split", split_of(rec["scenario_group"]))
        splits[rec["split"]].append(rec)
    for name, recs in splits.items():
        (out_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n"
                    for r in sorted(recs, key=lambda r: r["id"])))
    for rec in kept_b:
        rec["split"] = "benchmark"
    (out_dir / "benchmark.jsonl").write_text(
        "".join(json.dumps(r) + "\n"
                for r in sorted(kept_b, key=lambda r: r["id"])))

    def fam_counts(recs):
        return dict(Counter(r["family"] for r in recs))

    coverage = Counter()
    for rec in kept_c + kept_b:
        if rec["family"] == "path_meaning":
            t = rec["fact"].get("type", "")
            for cls in ("range", "enumeration", "pattern", "union"):
                if cls in t:
                    coverage[cls] += 1
            if rec["fact"].get("when"):
                coverage["when"] += 1
            if rec["fact"].get("default") is not None:
                coverage["default"] += 1
        elif rec["family"] == "instance_validity":
            cls = rec["fact"].get("constraint_class")
            if cls is None:
                continue  # folded-in sample records predate the tag
            coverage[{"range_access": "range", "range_native": "range",
                      "range_aging": "range", "enum_encap": "enum",
                      "enum_aging": "enum", "pattern_entry": "pattern",
                      "pattern_ifname": "key", "union_access": "union",
                      "union_allowed": "union", "union_voice": "union",
                      "choice_mode": "choice",
                      "when_maxacc": "when"}.get(cls, cls)] += 1
    manifest = {
        "bundle": "YangModels/yang @ 9c23db975096b67843858128f37947a0c30a2d45 (xe/2621, IOS XE 26.2.1)",
        "splits": {k: {"total": len(v), "by_family": fam_counts(v)}
                   for k, v in splits.items()},
        "benchmark": {"total": len(kept_b),
                      "by_family": fam_counts(kept_b),
                      "note": "plus the 8 hand-authored records in "
                              "../held_out.jsonl; frozen by the "
                              "commit that adds this manifest"},
        "sample_folded_into_train": len(sample),
        "coverage": dict(coverage),
        "dropped": {"corpus": [list(d) if isinstance(d, tuple) else d
                               for d in dropped_c],
                    "benchmark": [list(d) if isinstance(d, tuple)
                                  else d for d in dropped_b]},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1) + "\n")
    print("splits:", {k: len(v) for k, v in splits.items()})
    print("benchmark:", len(kept_b))
    print("dropped:", len(dropped_c), "corpus /", len(dropped_b),
          "benchmark")
    for d in (dropped_c + dropped_b)[:20]:
        print("  DROP:", d[0] if isinstance(d, tuple) else d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
