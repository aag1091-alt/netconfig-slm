#!/usr/bin/env python3
"""End-to-end branch verification for the coverage inventory.

Compares one real branch of the generator's inventory against the
project's independent tool — yang-learn/schema_tools.py inventory()
(pyang closure load of Cisco-IOS-XE-native + the cat9k deviations,
full schema_path convention) — after normalising the independent
tool's choice/case-inclusive paths through the generator's
statement index. Also checks two branches where the conventions
coincide and the path sets must be exactly equal.

Usage (repo root, venv with pyang):
  .venv/bin/python coverage/tests/verify_branch.py [--bundle DIR]

Takes ~3 minutes (two pyang loads). Exit 0 iff all checks pass.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "yang-learn"))

spec = importlib.util.spec_from_file_location(
    "generate_coverage", REPO / "coverage" / "generate_coverage.py")
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)

import schema_tools  # noqa: E402  (yang-learn's own tool)

NORMALISED_BRANCH = ("/native/interface/GigabitEthernet"
                     "/switchport-config/switchport/trunk")
EXACT_BRANCHES = [
    "/native/interface/GigabitEthernet/switchport-config/switchport/access",
    "/native/interface/GigabitEthernet/switchport-config/switchport/voice",
    "/native/vlan/configuration-entry/vlan-id",
]


def under(paths, branch):
    return {p for p in paths if p == branch or p.startswith(branch + "/")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", default=str(REPO.parent / "bundles"
                                            / "xe-2621"))
    args = ap.parse_args()
    bundle = Path(args.bundle)

    ctx, _info, _errs = gc.load_schema(bundle)
    leaves, stmt_index, _roots, _col = gc.build_inventory(ctx)
    gen_paths = set(leaves)

    facts = schema_tools.inventory(bundle)
    st_paths = {f["path"] for f in facts}

    ok = True
    for branch in EXACT_BRANCHES:
        a, b = under(st_paths, branch), under(gen_paths, branch)
        status = "PASS" if a == b else "FAIL"
        ok &= a == b
        print(f"[{status}] exact  {branch}: schema_tools={len(a)} "
              f"generator={len(b)}")
    a = under(st_paths, NORMALISED_BRANCH)
    mapped = {stmt_index[p][1] if p in stmt_index else p for p in a}
    b = under(gen_paths, NORMALISED_BRANCH)
    status = "PASS" if mapped == b else "FAIL"
    ok &= mapped == b
    print(f"[{status}] normalised {NORMALISED_BRANCH}: "
          f"schema_tools={len(a)} generator={len(b)} "
          f"(choice/case segments elided)")
    if mapped != b:
        print("  only in schema_tools:", sorted(mapped - b)[:5])
        print("  only in generator:   ", sorted(b - mapped)[:5])
    print("OVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
