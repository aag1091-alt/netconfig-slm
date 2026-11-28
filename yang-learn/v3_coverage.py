#!/usr/bin/env python3
"""Direct-evidence coverage accounting for yang-learn corpus v3.

Implements CORPUS_V3_PLAN.md §0 accounting rules against the
coverage dashboard's inventory (coverage/generate_coverage.py,
calibrated: it reproduces the published snapshot exactly —
154,052 eligible leaves, 106 covered by corpus v1 train,
resolution 2,134 leaf / 889 construct / 0 unresolved).

Strict rule (the plan's headline metric): a leaf counts as
covered by training iff a TRAIN record's designated subject path
IS that leaf —
  path_meaning       fact.path
  instance_validity  the judged leaf: fact.path, else source paths
  state_reconstruction  fact.edited_path (sample-fold records
                     without one: their changed_paths, i.e. the
                     leaves the edit actually changed)
  typed_edit         edits[]/attempted_edits[] paths, edited_path
  revised_schema     source paths (the rule's target leaf)
  paired_view        fact.subject_paths (written by the generator)
Bystander/preserved leaves inside instance states do NOT count.
Held-out / bench / test / dev records never enter the numerator:
only the files passed on the command line are read, and the
builder passes train files only.

Also reports the dashboard's own (looser) evidence rule for
comparison, the interface-normalized view (dashboard norm:
interface list segment collapsed), and per-priority-section
counts (coverage/priorities.yaml).

Usage:
  v3_coverage.py --files corpus/train.jsonl [...] \
      [--prev covered.json] [--out result.json]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
_spec = importlib.util.spec_from_file_location(
    "generate_coverage", REPO / "coverage" / "generate_coverage.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


def strict_paths(rec) -> set[str]:
    fam = rec.get("family")
    fact = rec.get("fact") or {}
    if fam == "path_meaning":
        return {fact["path"]} if fact.get("path") else set()
    if fam == "instance_validity":
        if fact.get("path"):
            return {fact["path"]}
        return gc._src_paths(rec)
    if fam == "state_reconstruction":
        if fact.get("edited_path"):
            return {fact["edited_path"]}
        return {gc.canon_instance_path(p)
                for p in fact.get("changed_paths") or []}
    if fam == "typed_edit":
        ps = set()
        for e in (fact.get("edits") or []) + \
                (fact.get("attempted_edits") or []):
            if e.get("path"):
                ps.add(e["path"])
        if fact.get("edited_path"):
            ps.add(fact["edited_path"])
        return ps
    if fam == "revised_schema":
        return gc._src_paths(rec)
    if fam == "paired_view":
        return set(fact.get("subject_paths") or [])
    return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--cache", default="/tmp/cov_cache.json")
    ap.add_argument("--prev", default=None,
                    help="JSON with a previous 'covered_strict' list")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cache = json.loads(Path(args.cache).read_text())
    leaves = {p: dict(r) for p, r in cache["leaves"].items()}
    stmt_index = {k: tuple(v) for k, v in cache["stmt_index"].items()}
    for leaf in leaves.values():
        leaf["eligible"] = leaf["config"] and leaf["status"] != "obsolete"

    def resolve(path):
        if path in leaves:
            return leaves[path]
        hit = stmt_index.get(path)
        if hit is not None:
            kind, tp = hit
            if kind in ("leaf", "leaf-list") and tp in leaves:
                return leaves[tp]
        return None

    records = []
    for fn in args.files:
        records += [json.loads(l) for l in
                    Path(fn).read_text().splitlines() if l.strip()]

    covered_strict: set[str] = set()
    covered_dash: set[str] = set()
    unresolved = Counter()
    for rec in records:
        for p in strict_paths(rec):
            leaf = resolve(p)
            if leaf is not None and leaf["eligible"]:
                covered_strict.add(leaf["path"])
            elif leaf is None:
                unresolved[f"strict:{rec['family']}"] += 1
        leaf_ps, construct_ps = gc.extract_record_paths(rec)
        for p in leaf_ps | construct_ps:
            leaf = resolve(p)
            if leaf is not None and leaf["eligible"]:
                covered_dash.add(leaf["path"])

    eligible = [l for l in leaves.values() if l["eligible"]]
    denom = len(eligible)

    def norm(p: str) -> str:
        m = re.match(r"^(/native/interface/)[^/]+(/.*)?$", p)
        return "iface*" + (m.group(2) or "") if m else p

    norm_denom = len({norm(l["path"]) for l in eligible})
    groups = gc.load_priorities(REPO / "coverage" / "priorities.yaml")
    prio = {}
    for g in groups:
        members = [l for l in eligible
                   if gc.assign_priority(l["path"], groups) == g["id"]]
        prio[g["id"]] = {
            "total": len(members),
            "covered_strict": sum(1 for l in members
                                  if l["path"] in covered_strict)}

    out = {
        "records": len(records),
        "denominator": denom,
        "covered_strict": sorted(covered_strict),
        "covered_strict_count": len(covered_strict),
        "covered_dashboard_rule_count": len(covered_dash),
        "normalized_denominator": norm_denom,
        "covered_strict_normalized": len({norm(p)
                                          for p in covered_strict}),
        "priorities": prio,
        "unresolved_strict": dict(unresolved),
    }
    if args.prev:
        prev = set(json.loads(Path(args.prev).read_text())
                   ["covered_strict"])
        new = sorted(covered_strict - prev)
        out["newly_covered_count"] = len(new)
        out["newly_covered"] = new
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("covered_strict", "newly_covered")},
                     indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
