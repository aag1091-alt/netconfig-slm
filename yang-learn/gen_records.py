#!/usr/bin/env python3
"""Generate the schema-derived review sample (records.jsonl).

Labels and schema facts are extracted from the parsed YANG modules via
pyang (schema_tools). Instance verdicts are NOT computed here: every
instance is written to instances/ and validated by yangson
(validate_instances.py, an independent implementation); the verdicts
are merged back and any record whose yangson verdict disagrees with
its intended label is dropped and reported.

Families: path_meaning (PM), instance_validity (IV), typed_edit (TE),
revised_schema (RS). All records carry split="train_candidate"; the
hand-authored held-out set lives in held_out.jsonl and is NOT produced
by this script.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_tools import inventory, leaf_facts, load_context  # noqa: E402

S = "Cisco-IOS-XE-switch:"
V = "Cisco-IOS-XE-vlan:"
GE = "/native/interface/GigabitEthernet"
SW = GE + "/switchport-config/switchport"
MODEL = ("the IOS XE 26.2.1 native YANG model (Catalyst 9300 / cat9k "
         "module set)")

PROFILE = (
    "Deployment profile P1 (site standard for access ports): "
    "(1) access VLAN IDs are restricted to 1..100; "
    "(2) a trunk's native VLAN must not be 1; "
    "(3) port-security aging time is capped at 60 minutes."
)


# ---------------------------------------------------------------- instances
def doc(interfaces=None, entries=None):
    d: dict = {}
    if interfaces is not None:
        d["interface"] = {"GigabitEthernet": interfaces}
    if entries is not None:
        d["vlan"] = {V + "configuration-entry": entries}
    return {"Cisco-IOS-XE-native:native": d}


def ge(sw, name="1/0/1"):
    return {"name": name, "switchport-config": {"switchport": sw}}


def sw_access(vlan=10, extra=None):
    sw = {S + "mode": {S + "access": {}},
          S + "access": {S + "vlan": {S + "vlan": vlan}}}
    if extra:
        sw.update(extra)
    return sw


def qual(obj):
    """Qualify every key with the switch grouping's module prefix —
    grouping-defined nodes carry Cisco-IOS-XE-switch in JSON encoding."""
    if isinstance(obj, dict):
        return {(k if ":" in k else S + k): qual(v)
                for k, v in obj.items()}
    return obj


def sw_trunk(trunk_children=None, extra=None):
    sw = {S + "mode": {S + "trunk": {}},
          S + "trunk": qual(trunk_children or {})}
    if extra:
        sw.update(extra)
    return sw


def ps(aging=None, max_access=None):
    inner: dict = {}
    if aging:
        inner[S + "aging"] = aging
    if max_access:
        inner[S + "maximum-address-config"] = {
            S + "maximum-vlan-access": max_access}
    return {S + "port-security-config": {S + "port-security": inner}}


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        if not obj:
            out[prefix] = {}  # presence container marker
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(
        Path(__file__).resolve().parents[2] / "bundles" / "xe-2621"))
    ap.add_argument("--out", default=str(
        Path(__file__).resolve().parent / "records.jsonl"))
    ap.add_argument("--instances", default=str(
        Path(__file__).resolve().parent / "instances"))
    args = ap.parse_args()
    bundle = Path(args.bundle)
    inst_dir = Path(args.instances)
    inst_dir.mkdir(parents=True, exist_ok=True)

    facts = {f["path"]: f for f in inventory(bundle)}
    records: list[dict] = []
    instance_files: dict[str, str] = {}  # filename -> record id

    def save_instance(rid, suffix, data):
        name = f"{rid}-{suffix}.json"
        (inst_dir / name).write_text(json.dumps(data, indent=1))
        instance_files[name] = rid
        return f"instances/{name}"

    # ---------------------------------------------------------- path_meaning
    pm_specs = [
        (f"{SW}/access/vlan/vlan", "type"),
        (f"{SW}/trunk/native/vlan/vlan-id", "type"),
        (f"{SW}/trunk/encapsulation", "type"),
        (f"{SW}/port-security-config/port-security/aging/time", "type"),
        ("/native/vlan/configuration-entry/vlan-id", "type"),
        (f"{SW}/voice/vlan/vlan", "type"),
        (GE + "/switchport-wrapper/switchport/mode", "type"),
        (f"{SW}/trunk/allowed/vlan/vlan-choice/vlans/vlans", "type"),
        ("/native/vlan/access-log/threshold", "type"),
        (f"{SW}/port-security-config/port-security/maximum-address-config/"
         "maximum-vlan-access/maximum", "when"),
    ]
    for i, (path, focus) in enumerate(pm_specs, 1):
        f = facts[path]
        rid = f"YL-PM-{i:02d}"
        if focus == "type":
            prompt = (f"In {MODEL}, what is the type of the leaf at "
                      f"{path}?")
            completion = f"{f['type']}"
            if f["default"] is not None:
                completion += f" Default: {f['default']}."
            if f["description"]:
                completion += f" Meaning: {f['description']}"
        else:
            prompt = (f"In {MODEL}, under what condition may the leaf at "
                      f"{path} be present?")
            w = f["when"][0]
            completion = (f"It carries a when condition on {w['on']}: "
                          f"`{w['xpath']}` — the node is only valid when "
                          f"that XPath evaluates to true.")
        records.append({
            "id": rid, "family": "path_meaning", "split": "train_candidate",
            "prompt": prompt, "completion": completion,
            "fact": {"path": path, "type": f["type"],
                     "default": f["default"], "when": f["when"],
                     "must": f["must"]},
            "source": f["source"], "validation": None})

    # mode is a choice, not a leaf — authored from the module source.
    records.append({
        "id": "YL-PM-11", "family": "path_meaning",
        "split": "train_candidate",
        "prompt": (f"In {MODEL}, how is the switchport mode represented "
                   f"at {SW}/mode — as a leaf, or as something else?"),
        "completion": (
            "It is a container holding a YANG choice (mode-choice), not "
            "a leaf. Exactly one case may be present: the presence "
            "containers access or trunk, the presence container "
            "dot1q-tunnel, the leaf dynamic (enum auto | desirable), or "
            "the private-vlan container. Setting the mode means "
            "selecting one case; two cases at once are schema-invalid."),
        "fact": {"path": f"{SW}/mode", "structure": "choice",
                 "cases": ["access", "trunk", "dot1q-tunnel", "dynamic",
                           "private-vlan"]},
        "source": {"module": "Cisco-IOS-XE-switch",
                   "file": "Cisco-IOS-XE-switch.yang", "line": 728},
        "validation": None})

    # ----------------------------------------------------- instance_validity
    iv_specs = [
        ("base access config", doc([ge(sw_access(10))],
                                   [{"vlan-id": "10"}]),
         "valid", None),
        ("access VLAN 4095", doc([ge(sw_access(4095))],
                                 [{"vlan-id": "10"}]),
         "invalid", "access/vlan/vlan is a union of uint16 range 1..4094 "
                    "and the enumeration {dynamic}; 4095 matches neither"),
        ("access VLAN 'dynamic'", doc([ge(sw_access("dynamic"))],
                                      [{"vlan-id": "10"}]),
         "valid", None),
        ("interface name 'Gi1/0/1'", doc([ge(sw_access(10),
                                             name="Gi1/0/1")],
                                         [{"vlan-id": "10"}]),
         "invalid", "the GigabitEthernet list key 'name' must match the "
                    "pattern (0|[1-9][0-9]*)(/(0|[1-9][0-9]*))*(\\.[0-9]*)? "
                    "— numeric slot/port form only"),
        ("VLAN entry key '4095'", doc([ge(sw_access(10))],
                                      [{"vlan-id": "4095"}]),
         "invalid", "configuration-entry/vlan-id is a string whose "
                    "pattern encodes VLAN IDs 1..4094 (and range lists); "
                    "'4095' does not match"),
        ("VLAN entry key '1-10,15'", doc([ge(sw_access(10))],
                                         [{"vlan-id": "1-10,15"}]),
         "valid", None),
        ("VLAN entry key '0'", doc([ge(sw_access(10))],
                                   [{"vlan-id": "0"}]),
         "invalid", "configuration-entry/vlan-id pattern starts at 1; "
                    "'0' does not match"),
        ("trunk native VLAN 0", doc([ge(sw_trunk(
            {"native": {"vlan": {"vlan-id": 0}}}))],
            [{"vlan-id": "10"}]),
         "invalid", "trunk/native/vlan/vlan-id is uint16 range 1..4094; "
                    "0 is below the range"),
        ("port-security aging time 1441",
         doc([ge(sw_access(10, ps(aging={S + "time": 1441})))],
             [{"vlan-id": "10"}]),
         "invalid", "port-security aging/time is uint16 range 1..1440"),
        ("port-security aging type 'shutdown'",
         doc([ge(sw_access(10, ps(aging={S + "type": "shutdown"})))],
             [{"vlan-id": "10"}]),
         "invalid", "port-security aging/type is an enumeration of "
                    "{absolute, inactivity}"),
        ("mode with access and trunk cases both present",
         doc([ge({S + "mode": {S + "access": {}, S + "trunk": {}},
                  S + "access": {S + "vlan": {S + "vlan": 10}}})],
             [{"vlan-id": "10"}]),
         "invalid", "mode/mode-choice is a YANG choice: exactly one "
                    "case may be present"),
        ("maximum-vlan-access while mode is trunk",
         doc([ge(sw_trunk(extra=ps(max_access={S + "maximum": 100,
                                               S + "vlan": "access"})))],
             [{"vlan-id": "10"}]),
         "invalid", "maximum-vlan-access carries when "
                    "'../../../../mode/access' — it requires the mode "
                    "choice's access case"),
        ("mode dynamic 'desirable'",
         doc([ge({S + "mode": {S + "dynamic": "desirable"},
                  S + "access": {S + "vlan": {S + "vlan": 10}}})],
             [{"vlan-id": "10"}]),
         "valid", None),
        ("trunk encapsulation '802.1q'",
         doc([ge(sw_trunk({"encapsulation": "802.1q"}))],
             [{"vlan-id": "10"}]),
         "invalid", "trunk/encapsulation is an enumeration of "
                    "{dot1q, isl, negotiate}"),
    ]
    for i, (label, data, intended, rule) in enumerate(iv_specs, 1):
        rid = f"YL-IV-{i:02d}"
        rel = save_instance(rid, "inst", data)
        prompt = (f"Is this configuration instance valid against "
                  f"{MODEL}? Answer valid or invalid, and if invalid "
                  f"name the single rule it breaks.\n\n"
                  f"Instance ({label}):\n"
                  f"{json.dumps(data, indent=1)}")
        completion = ("Valid." if intended == "valid"
                      else f"Invalid — {rule}.")
        records.append({
            "id": rid, "family": "instance_validity",
            "split": "train_candidate", "prompt": prompt,
            "completion": completion,
            "fact": {"instance_file": rel, "intended_verdict": intended,
                     "violated_rule": rule},
            "source": None, "validation": None})

    # ------------------------------------------------------------ typed_edit
    te_defs = []
    b = doc([ge(sw_access(10))], [{"vlan-id": "10"}])
    a = doc([ge(sw_access(20))], [{"vlan-id": "10"}])
    te_defs.append(("Set the access VLAN of GigabitEthernet 1/0/1 "
                    "from 10 to 20.", b, a))
    a = doc([ge({S + "mode": {S + "trunk": {}},
                 S + "access": {S + "vlan": {S + "vlan": 10}}})],
            [{"vlan-id": "10"}])
    te_defs.append(("Change GigabitEthernet 1/0/1 from access mode to "
                    "trunk mode. Change nothing else.", b, a))
    b = doc([ge(sw_trunk())], [{"vlan-id": "10"}])
    a = doc([ge(sw_trunk({"native": {"vlan": {"vlan-id": 99}}}))],
            [{"vlan-id": "10"}])
    te_defs.append(("Set the trunk native VLAN of GigabitEthernet "
                    "1/0/1 to 99.", b, a))
    b = doc([ge(sw_access(10))], [{"vlan-id": "10"}])
    a = doc([ge(sw_access(10))], [{"vlan-id": "10"}, {"vlan-id": "20"}])
    te_defs.append(("Add a configuration entry for VLAN 20.", b, a))
    b = doc([ge(sw_access(10))], [{"vlan-id": "10"}])
    a2 = sw_access(10)
    a2[S + "voice"] = {S + "vlan": {S + "vlan": 30}}
    a = doc([ge(a2)], [{"vlan-id": "10"}])
    te_defs.append(("Set the voice VLAN of GigabitEthernet 1/0/1 "
                    "to 30.", b, a))
    b = doc([ge(sw_access(10))], [{"vlan-id": "10"}, {"vlan-id": "20"}])
    a = doc([ge(sw_access(10))], [{"vlan-id": "10"}])
    te_defs.append(("Remove the configuration entry for VLAN 20.", b, a))
    b = doc([ge(sw_trunk())], [{"vlan-id": "10"}])
    a = doc([ge(sw_trunk({"allowed": {"vlan": {"vlans": "10,20"}}}))],
            [{"vlan-id": "10"}])
    te_defs.append(("Restrict the trunk allowed VLANs of "
                    "GigabitEthernet 1/0/1 to 10 and 20.", b, a))
    b = doc([ge(sw_trunk())], [{"vlan-id": "10"}])
    a = doc([ge(sw_trunk({"encapsulation": "dot1q"}))],
            [{"vlan-id": "10"}])
    te_defs.append(("Set the trunk encapsulation of GigabitEthernet "
                    "1/0/1 to dot1q.", b, a))

    for i, (instruction, before, after) in enumerate(te_defs, 1):
        rid = f"YL-TE-{i:02d}"
        rel_b = save_instance(rid, "before", before)
        rel_a = save_instance(rid, "after", after)
        fb, fa = flatten(before), flatten(after)
        changed = sorted(k for k in set(fb) | set(fa)
                         if fb.get(k) != fa.get(k))
        preserved = sorted(k for k in set(fb) & set(fa)
                           if fb.get(k) == fa.get(k))
        prompt = (f"Apply this edit to the configuration state and "
                  f"return the full resulting state. Change only what "
                  f"the edit requires; preserve every other value.\n\n"
                  f"Edit: {instruction}\n\nState:\n"
                  f"{json.dumps(before, indent=1)}")
        records.append({
            "id": rid, "family": "typed_edit", "split": "train_candidate",
            "prompt": prompt, "completion": json.dumps(after, indent=1),
            "fact": {"before_file": rel_b, "after_file": rel_a,
                     "changed_paths": changed,
                     "preserved_paths": preserved},
            "source": None, "validation": None})

    # -------------------------------------------------------- revised_schema
    rs_specs = [
        ("An access port is configured with access VLAN 250. Is that "
         "allowed?", "No. The base schema allows 1..4094, but profile "
         "P1 rule (1) restricts access VLAN IDs to 1..100, and 250 is "
         "outside that range.", "P1 rule (1)"),
        ("An access port is configured with access VLAN 50. Is that "
         "allowed?", "Yes. 50 is inside the base schema range 1..4094 "
         "and inside profile P1's restricted range 1..100.", "P1 rule (1)"),
        ("A trunk is configured with native VLAN 1. Is that allowed?",
         "No. The base schema allows native VLAN 1, but profile P1 "
         "rule (2) forbids native VLAN 1 on trunks.", "P1 rule (2)"),
        ("Port-security aging time is set to 120 minutes. Is that "
         "allowed?", "No. The base schema allows 1..1440 minutes, but "
         "profile P1 rule (3) caps aging time at 60 minutes.",
         "P1 rule (3)"),
    ]
    for i, (q, ans, rule) in enumerate(rs_specs, 1):
        records.append({
            "id": f"YL-RS-{i:02d}", "family": "revised_schema",
            "split": "train_candidate",
            "prompt": (f"Answer using the deployment profile below, "
                       f"which overrides the base schema where they "
                       f"differ.\n\n{PROFILE}\n\n{q}"),
            "completion": ans,
            "fact": {"profile_rule": rule},
            "source": None,
            "validation": {"tool": "none",
                           "note": "profile rule, not base schema"}})

    # ------------------------------------------------- independent validation
    files = sorted(instance_files)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent /
                             "validate_instances.py"),
         "--bundle", str(bundle), "--library",
         str(Path(__file__).resolve().parent / "scoped-library.json")] +
        [str(inst_dir / f) for f in files],
        capture_output=True, text=True, timeout=1200)
    verdicts = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            v = json.loads(line)
            verdicts[v["file"]] = v
    if len(verdicts) != len(files):
        print(f"FATAL: validator returned {len(verdicts)}/{len(files)} "
              f"verdicts\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}",
              file=sys.stderr)
        return 2

    kept, dropped = [], []
    for rec in records:
        if rec["family"] == "instance_validity":
            fname = Path(rec["fact"]["instance_file"]).name
            v = verdicts[fname]
            rec["validation"] = {"tool": "yangson",
                                 "verdict": v["verdict"],
                                 "detail": v["detail"]}
            if v["verdict"] == rec["fact"]["intended_verdict"]:
                kept.append(rec)
            else:
                dropped.append((rec["id"], rec["fact"]["intended_verdict"],
                                v))
        elif rec["family"] == "typed_edit":
            vb = verdicts[Path(rec["fact"]["before_file"]).name]
            va = verdicts[Path(rec["fact"]["after_file"]).name]
            rec["validation"] = {
                "tool": "yangson",
                "before": vb["verdict"], "after": va["verdict"],
                "detail": va["detail"] or vb["detail"]}
            if vb["verdict"] == "valid" and va["verdict"] == "valid":
                kept.append(rec)
            else:
                dropped.append((rec["id"], "before+after valid",
                                {"before": vb, "after": va}))
        else:
            kept.append(rec)

    with open(args.out, "w") as fh:
        for rec in kept:
            fh.write(json.dumps(rec) + "\n")
    from collections import Counter
    print("kept:", len(kept), dict(Counter(r["family"] for r in kept)))
    print("dropped:", len(dropped))
    for rid, intended, v in dropped:
        print("  DROP", rid, "intended:", intended, "yangson:", v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
