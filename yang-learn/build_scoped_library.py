#!/usr/bin/env python3
"""Build a scoped RFC 7895 YANG-library JSON for yangson validation.

The module set is the pyang-resolved closure of the roots
(Cisco-IOS-XE-native + its cat9k deviation modules) taken from the
bundle's own yang-set-cat9k.xml — so the validator sees exactly the
device-advertised set for our scope, nothing hand-picked. Verdicts
produced with this library come from yangson, an implementation
independent of the pyang-based label extraction in schema_tools.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_tools import DEVIATIONS, ROOT_MODULE, load_context  # noqa: E402

NS = {"y": "urn:ietf:params:xml:ns:yang:ietf-yang-library"}


def yang_set_entries(bundle: Path) -> dict[str, dict]:
    tree = ET.parse(bundle / "yang-set-cat9k.xml")
    root = tree.getroot()
    entries: dict[str, dict] = {}
    for mod in root.iter("{urn:ietf:params:xml:ns:yang:ietf-yang-library}module"):
        def child(tag):
            el = mod.find(f"y:{tag}", NS)
            return el.text.strip() if el is not None and el.text else None
        entry = {
            "name": child("name"),
            "revision": child("revision") or "",
            "namespace": child("namespace"),
            "conformance-type": child("conformance-type") or "import",
        }
        feats = [e.text.strip() for e in mod.findall("y:feature", NS)
                 if e.text]
        if feats:
            entry["feature"] = feats
        devs = []
        for d in mod.findall("y:deviation", NS):
            dn = d.find("y:name", NS)
            dr = d.find("y:revision", NS)
            if dn is not None and dn.text:
                devs.append({"name": dn.text.strip(),
                             "revision": (dr.text.strip()
                                          if dr is not None and dr.text
                                          else "")})
        if devs:
            entry["deviation"] = devs
        subs = []
        for s in mod.findall("y:submodule", NS):
            sn = s.find("y:name", NS)
            sr = s.find("y:revision", NS)
            if sn is not None and sn.text:
                subs.append({"name": sn.text.strip(),
                             "revision": (sr.text.strip()
                                          if sr is not None and sr.text
                                          else "")})
        if subs:
            entry["submodule"] = subs
        entries[entry["name"]] = entry
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(
        Path(__file__).resolve().parents[2] / "bundles" / "xe-2621"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    bundle = Path(args.bundle)
    ctx, errors = load_context(bundle)
    if errors:
        print(f"FATAL: {len(errors)} load errors", file=sys.stderr)
        return 2
    closure = {k[0] if isinstance(k, tuple) else k
               for k in ctx.modules.keys()}
    # Deviation modules are separate library entries in RFC 7895.
    for d in DEVIATIONS:
        closure.add(f"Cisco-IOS-XE-{d}-deviation")
    closure.add("Cisco-IOS-XE-vlan")
    closure.add("Cisco-IOS-XE-ethernet")
    entries = yang_set_entries(bundle)
    modules, missing = [], []
    for name in sorted(closure):
        if name in entries:
            entry = dict(entries[name])
            # IOS XE marks many still-served nodes "status obsolete"
            # (129 in native.yang alone); yangson would otherwise drop
            # them from validation patterns. Keep them, as the device
            # does. (yangson library extension, see yangson/schemadata.py.)
            entry["yangson-yl:keep-obsolete"] = True
            modules.append(entry)
        else:
            missing.append(name)
    lib = {"ietf-yang-library:modules-state": {
        "module-set-id": "netconfig-scoped-cat9k-2621",
        "module": modules}}
    Path(args.out).write_text(json.dumps(lib, indent=1))
    print(f"library modules: {len(modules)}; not in yang-set: {missing}")
    print(f"roots: {ROOT_MODULE} + {len(DEVIATIONS)} deviations "
          f"+ Cisco-IOS-XE-vlan + Cisco-IOS-XE-ethernet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
