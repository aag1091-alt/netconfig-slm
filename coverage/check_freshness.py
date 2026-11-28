#!/usr/bin/env python3
"""Freshness check for coverage/data/coverage.json.

The dashboard publishers (Actions workflow, gh-pages fallback) only
ever republish the committed JSON — they never regenerate it. This
check makes a stale JSON loud instead of silent:

  fast mode (default) — recompute the input fingerprints recorded
  in meta.fingerprint at generation time (corpus split files, the
  vendored run-1 scores snapshot, priorities.yaml, the YANG bundle
  digest, and the generator's own hash) and compare. Seconds, no
  pyang needed; the Pages workflow runs this before publishing and
  fails the deploy rather than serve old numbers as current.

  --full — additionally re-run the generator (--no-timestamp) into
  a temp file and require the committed JSON to equal it exactly,
  ignoring only meta.generated_at. Needs the bundle and a
  pyang-capable python (located via coverage/regen.sh); a few
  minutes.

Exit 0 = fresh, 1 = stale or unverifiable. See coverage/DEPLOYMENT.md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_coverage", HERE / "generate_coverage.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gc = _load_generator()

REMEDY = ("regenerate with `bash coverage/regen.sh` "
          "(or `make -C coverage regen`), review the diff, and "
          "commit coverage/data/coverage.json")


def compare_fingerprints(recorded, current) -> list[str]:
    """Human-readable problems; empty list means fresh. Pure."""
    if not recorded:
        return ["coverage.json records no meta.fingerprint — it "
                "predates fingerprinting; " + REMEDY]
    problems: list[str] = []
    rec_in = recorded.get("inputs") or {}
    cur_in = current.get("inputs") or {}
    for rel in sorted(cur_in):
        if rel not in rec_in:
            problems.append(f"input {rel}: not recorded in "
                            "coverage.json (generator inputs changed?)")
        elif rec_in[rel] != cur_in[rel]:
            problems.append(
                f"input {rel}: changed since generation "
                f"(recorded {rec_in[rel][:12]}…, "
                f"current {cur_in[rel][:12]}…)")
    for rel in sorted(rec_in):
        if rel not in cur_in:
            problems.append(f"input {rel}: recorded in coverage.json "
                            "but the file is missing now")
    if recorded.get("split_counts") != current.get("split_counts"):
        problems.append(
            f"split record counts changed: recorded "
            f"{recorded.get('split_counts')}, current "
            f"{current.get('split_counts')}")
    for key in ("bundle_digest", "generator_sha256"):
        rec_v, cur_v = recorded.get(key), current.get(key)
        if cur_v is None:
            continue  # bundle absent here — cannot verify, not a fail
        if rec_v != cur_v:
            problems.append(
                f"{key}: changed since generation "
                f"(recorded {str(rec_v)[:12]}…, current {cur_v[:12]}…)")
    return problems


def full_compare(repo: Path, data_path: Path) -> list[str]:
    """Re-run the generator and require exact equality (minus
    meta.generated_at) with the committed JSON."""
    with tempfile.TemporaryDirectory() as td:
        tmp_out = Path(td) / "coverage.json"
        proc = subprocess.run(
            ["bash", str(HERE / "regen.sh"), "--no-timestamp",
             "--out", str(tmp_out)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            return ["full check: regeneration failed:\n"
                    + (proc.stdout + proc.stderr)[-2000:]]
        fresh = json.loads(tmp_out.read_text(encoding="utf-8"))
    committed = json.loads(data_path.read_text(encoding="utf-8"))
    committed.get("meta", {}).pop("generated_at", None)
    if committed == fresh:
        return []
    problems = ["full check: committed coverage.json differs from a "
                "fresh regeneration (beyond generated_at)"]
    for key in sorted(set(committed) | set(fresh)):
        if committed.get(key) != fresh.get(key):
            if key == "meta":
                for mk in sorted(set(committed["meta"])
                                 | set(fresh["meta"])):
                    if committed["meta"].get(mk) != fresh["meta"].get(mk):
                        problems.append(f"  meta.{mk} differs")
            else:
                problems.append(f"  top-level section {key!r} differs")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    ap.add_argument("--repo", default=str(here.parent))
    ap.add_argument("--bundle", default=None)
    ap.add_argument("--data", default=None,
                    help="coverage.json to check "
                         "(default: <repo>/coverage/data/coverage.json)")
    ap.add_argument("--full", action="store_true",
                    help="also regenerate and require exact equality")
    args = ap.parse_args()
    repo = Path(args.repo)
    bundle = Path(args.bundle) if args.bundle else (
        repo.parent / "bundles" / "xe-2621")
    data_path = Path(args.data) if args.data else (
        repo / "coverage" / "data" / "coverage.json")

    if not data_path.exists():
        print(f"STALE: {data_path} does not exist; " + REMEDY)
        return 1
    data = json.loads(data_path.read_text(encoding="utf-8"))
    recorded = (data.get("meta") or {}).get("fingerprint")
    current = gc.compute_fingerprints(repo, bundle)
    if current.get("bundle_digest") is None:
        print(f"note: YANG bundle not found at {bundle} — "
              "bundle_digest not verified (corpus inputs still are)")

    problems = compare_fingerprints(recorded, current)
    if args.full:
        problems += full_compare(repo, data_path)

    if problems:
        print("STALE — coverage.json does not match its inputs:")
        for p in problems:
            print(f"  - {p}")
        print("Fix: " + REMEDY + ".")
        return 1
    print(f"FRESH — coverage.json matches its recorded inputs "
          f"(inputs_digest {current['inputs_digest'][:12]}…, "
          f"splits {current['split_counts']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
