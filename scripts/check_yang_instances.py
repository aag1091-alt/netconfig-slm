#!/usr/bin/env python3
"""Check data/yang_slice/instances.jsonl against the pinned YANG slice.

Honesty box — what validation this script actually performs:

1. MODULE validation (authoritative): pyang loads the vendored
   OpenConfig module set from vendor/yang/openconfig/ (pin:
   docs/YANG_PIN.md) and must report zero errors. This proves the
   pinned modules are well-formed YANG and their imports resolve.

2. INSTANCE validation (DERIVED, not authoritative): libyang/yanglint
   is not available in this environment (the pip `libyang` build
   fails without the C toolchain, and no yanglint binary is
   installed), so JSON instances cannot be checked by the reference
   implementation. The checks below are a hand-written encoding of
   the constraints in the pinned modules — typedef ranges
   (oc-vlan-types:vlan-id = uint16 1..4094), the vlan-mode-type
   enumeration, the `when` conditions on access-vlan / native-vlan /
   trunk-vlans in openconfig-vlan's vlan-switched-config grouping,
   leaf types from openconfig-interfaces, and unknown-node detection
   against the slice's node registry. They are a faithful subset,
   not a substitute for yanglint; when yanglint becomes available it
   should be run over the same file and any disagreement treated as
   a bug in this checker.

Usage: .venv/bin/python scripts/check_yang_instances.py
Exit code 0 iff pyang is clean and every instance's computed verdict
matches its labeled verdict.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTANCES = ROOT / "data/yang_slice/instances.jsonl"
OC_DIR = ROOT / "vendor/yang/openconfig"
PYANG_MODULES = ["openconfig-vlan.yang", "openconfig-if-ethernet.yang",
                 "openconfig-interfaces.yang", "openconfig-vlan-types.yang"]

VLAN_MIN, VLAN_MAX = 1, 4094  # oc-vlan-types:vlan-id range
MODES = {"ACCESS", "TRUNK"}   # oc-vlan-types:vlan-mode-type
VLAN_STATUS = {"ACTIVE", "SUSPENDED"}  # openconfig-vlan vlan-config/status

# Node registries for unknown-node detection (slice scope; derived
# from the groupings named in docs/YANG_SLICE.md).
TOP_KEYS = {"openconfig-interfaces:interfaces", "openconfig-vlan:vlans"}
IFACE_ENTRY_KEYS = {"name", "config", "openconfig-if-ethernet:ethernet"}
IFACE_CONFIG_LEAVES = {"name", "description", "type", "mtu", "enabled"}
ETHERNET_KEYS = {"config", "state", "openconfig-vlan:switched-vlan"}
SWITCHED_VLAN_KEYS = {"config", "state"}
SWITCHED_CONFIG_LEAVES = {"interface-mode", "native-vlan",
                          "access-vlan", "trunk-vlans"}
VLAN_ENTRY_KEYS = {"vlan-id", "config", "state"}
VLAN_CONFIG_LEAVES = {"vlan-id", "name", "status"}

_RANGE_RE = re.compile(r"^(\d+)\.\.(\d+)$")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check_vlan_id(v, where, problems):
    if not _is_int(v):
        problems.append(f"{where}: must be an integer vlan-id, "
                        f"got {type(v).__name__}")
    elif not (VLAN_MIN <= v <= VLAN_MAX):
        problems.append(f"{where}: {v} outside vlan-id range "
                        f"{VLAN_MIN}..{VLAN_MAX}")


def _check_trunk_member(v, where, problems):
    if _is_int(v):
        if not (VLAN_MIN <= v <= VLAN_MAX):
            problems.append(f"{where}: member {v} outside vlan-id range")
    elif isinstance(v, str):
        m = _RANGE_RE.match(v)
        if not m:
            problems.append(f"{where}: member {v!r} is neither a vlan-id "
                            "nor an 'x..y' vlan-range")
        else:
            lo, hi = int(m.group(1)), int(m.group(2))
            if not (VLAN_MIN <= lo < hi <= VLAN_MAX):
                problems.append(f"{where}: range {v!r} violates "
                                "1 <= x < y <= 4094")
    else:
        problems.append(f"{where}: member must be int or range string, "
                        f"got {type(v).__name__}")


def check_instance(inst):
    """Return a list of constraint violations (empty == valid)."""
    p = []
    if not isinstance(inst, dict):
        return ["instance: must be an object"]
    for k in inst:
        if k not in TOP_KEYS:
            p.append(f"top-level: unknown node {k!r}")

    vlans = inst.get("openconfig-vlan:vlans", {})
    if not isinstance(vlans, dict):
        p.append("openconfig-vlan:vlans: must be an object")
        vlans = {}
    for k in vlans:
        if k != "vlan":
            p.append(f"vlans: unknown node {k!r}")
    for entry in vlans.get("vlan", []) or []:
        where = f"vlans.vlan[{entry.get('vlan-id')!r}]"
        if not isinstance(entry, dict):
            p.append(f"{where}: entry must be an object")
            continue
        for k in entry:
            if k not in VLAN_ENTRY_KEYS:
                p.append(f"{where}: unknown node {k!r}")
        _check_vlan_id(entry.get("vlan-id"), f"{where}.vlan-id", p)
        cfg = entry.get("config", {})
        if not isinstance(cfg, dict):
            p.append(f"{where}.config: must be an object")
            continue
        for k in cfg:
            if k not in VLAN_CONFIG_LEAVES:
                p.append(f"{where}.config: unknown leaf {k!r}")
        if "vlan-id" in cfg:
            _check_vlan_id(cfg["vlan-id"], f"{where}.config.vlan-id", p)
            if cfg["vlan-id"] != entry.get("vlan-id"):
                p.append(f"{where}: list key != config/vlan-id "
                         "(semantic consistency)")
        if "name" in cfg and not isinstance(cfg["name"], str):
            p.append(f"{where}.config.name: must be a string")
        if "status" in cfg and cfg["status"] not in VLAN_STATUS:
            p.append(f"{where}.config.status: {cfg['status']!r} not in "
                     f"{sorted(VLAN_STATUS)}")

    ifaces = inst.get("openconfig-interfaces:interfaces", {})
    if not isinstance(ifaces, dict):
        p.append("openconfig-interfaces:interfaces: must be an object")
        ifaces = {}
    for k in ifaces:
        if k != "interface":
            p.append(f"interfaces: unknown node {k!r}")
    for entry in ifaces.get("interface", []) or []:
        name = entry.get("name")
        where = f"interfaces.interface[{name!r}]"
        if not isinstance(entry, dict):
            p.append(f"{where}: entry must be an object")
            continue
        for k in entry:
            if k not in IFACE_ENTRY_KEYS:
                p.append(f"{where}: unknown node {k!r}")
        cfg = entry.get("config", {})
        if not isinstance(cfg, dict):
            p.append(f"{where}.config: must be an object")
            cfg = {}
        for k in cfg:
            if k not in IFACE_CONFIG_LEAVES:
                p.append(f"{where}.config: unknown leaf {k!r}")
        if "name" in cfg and cfg["name"] != name:
            p.append(f"{where}: list key != config/name "
                     "(semantic consistency)")
        if "description" in cfg and not isinstance(cfg["description"], str):
            p.append(f"{where}.config.description: must be a string")
        if "enabled" in cfg and not isinstance(cfg["enabled"], bool):
            p.append(f"{where}.config.enabled: must be a boolean, "
                     f"got {type(cfg['enabled']).__name__}")
        if "type" in cfg and not (isinstance(cfg["type"], str)
                                  and ":" in cfg["type"]):
            p.append(f"{where}.config.type: must be an identityref "
                     "string 'module:identity'")
        eth = entry.get("openconfig-if-ethernet:ethernet", {})
        if not isinstance(eth, dict):
            p.append(f"{where}.ethernet: must be an object")
            continue
        for k in eth:
            if k not in ETHERNET_KEYS:
                p.append(f"{where}.ethernet: unknown node {k!r}")
        sv = eth.get("openconfig-vlan:switched-vlan", {})
        if not isinstance(sv, dict):
            p.append(f"{where}.switched-vlan: must be an object")
            continue
        for k in sv:
            if k not in SWITCHED_VLAN_KEYS:
                p.append(f"{where}.switched-vlan: unknown node {k!r}")
        scfg = sv.get("config", {})
        if not isinstance(scfg, dict):
            p.append(f"{where}.switched-vlan.config: must be an object")
            continue
        for k in scfg:
            if k not in SWITCHED_CONFIG_LEAVES:
                p.append(f"{where}.switched-vlan.config: unknown leaf {k!r}")
        mode = scfg.get("interface-mode")
        if mode is not None and mode not in MODES:
            p.append(f"{where}.switched-vlan.config.interface-mode: "
                     f"{mode!r} not in vlan-mode-type {sorted(MODES)}")
        # `when` conditions from vlan-switched-config: a leaf whose
        # when is false must not exist in valid instance data.
        if "access-vlan" in scfg:
            _check_vlan_id(scfg["access-vlan"],
                           f"{where}.switched-vlan.config.access-vlan", p)
            if mode != "ACCESS":
                p.append(f"{where}: access-vlan present but "
                         "interface-mode is not ACCESS "
                         "(when \"../interface-mode = 'ACCESS'\")")
        if "native-vlan" in scfg:
            _check_vlan_id(scfg["native-vlan"],
                           f"{where}.switched-vlan.config.native-vlan", p)
            if mode != "TRUNK":
                p.append(f"{where}: native-vlan present but "
                         "interface-mode is not TRUNK "
                         "(when \"../interface-mode = 'TRUNK'\")")
        if "trunk-vlans" in scfg:
            tv = scfg["trunk-vlans"]
            if not isinstance(tv, list):
                p.append(f"{where}.switched-vlan.config.trunk-vlans: "
                         "must be a list")
            else:
                for m in tv:
                    _check_trunk_member(
                        m, f"{where}.switched-vlan.config.trunk-vlans", p)
            if mode != "TRUNK":
                p.append(f"{where}: trunk-vlans present but "
                         "interface-mode is not TRUNK "
                         "(when \"../interface-mode = 'TRUNK'\")")
    return p


def main():
    failures = 0
    print("== Step 0: module validation (pyang, authoritative) ==")
    pyang = ROOT / ".venv/bin/pyang"
    if pyang.exists():
        r = subprocess.run([str(pyang)] + PYANG_MODULES,
                           cwd=OC_DIR, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        print(f"pyang on {', '.join(PYANG_MODULES)}: "
              f"exit {r.returncode}" + (f"\n{out}" if out else
                                        " (no errors or warnings)"))
        if r.returncode != 0 or out:
            failures += 1
    else:
        print("pyang not found in .venv — module validation SKIPPED")
        failures += 1
    print(f"yanglint binary: "
          f"{shutil.which('yanglint') or 'NOT AVAILABLE — instance checks below are the derived checker, not libyang'}")
    print("\n== Instance checks (derived checker) ==")
    records = [json.loads(l) for l in open(INSTANCES) if l.strip()]
    for rec in records:
        problems = check_instance(rec["instance"])
        computed = "invalid" if problems else "valid"
        ok = computed == rec["verdict"]
        print(f"{'PASS' if ok else 'FAIL'}  {rec['id']:<38} "
              f"labeled={rec['verdict']:<7} computed={computed}")
        for prob in problems:
            print(f"      -> {prob}")
        if rec["verdict"] == "invalid" and not problems:
            print("      -> labeled invalid but no violation found")
        failures += 0 if ok else 1
    n_valid = sum(1 for r in records if r["verdict"] == "valid")
    print(f"\n{len(records) - failures}/{len(records)} instance verdicts "
          f"reproduced ({n_valid} valid, {len(records) - n_valid} invalid)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
