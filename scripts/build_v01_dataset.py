#!/usr/bin/env python3
"""Assemble dataset v0.1 from the validated corpus.

Reads data/examples/seed.jsonl (100 hand-written) + data/generated/*.jsonl
(6 x 80 synthetic) = 580 rows. Does NOT modify any source file.

Outputs under data/v0.1/:
  dataset.jsonl  - full corpus, validation flags populated from run logs
  train.jsonl / val.jsonl / test.jsonl - 90/5/5 split, stratified by family
  stats.json     - totals, per-family/per-split counts, integrity results

Split logic adapts scripts/make_splits.py (same algorithm, seed 42) inline so
the integrity pass, log cross-check and stats are produced in one run.

Validation-flag note: source corpus rows carry validation.parse_ok /
batfish_ok = null; the passes are documented per row ID in docs/*run*.txt.
This script cross-checks every corpus ID against those logs and only then
sets parse_ok/batfish_ok = true in the v0.1 copies. Any ID without a logged
pass keeps null flags and is reported as a problem row.
"""
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = ROOT / "data" / "examples" / "seed.jsonl"
GENERATED_DIR = ROOT / "data" / "generated"
DOCS_DIR = ROOT / "docs"
OUT_DIR = ROOT / "data" / "v0.1"
SPLIT_SEED = 42

FAMILIES = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp", "interface_mgmt"]
SOURCES = {"handwritten", "cisco_guide", "synthetic"}
REQUIRED_KEYS = {
    "id", "family", "intent_nl", "intent_oc", "device",
    "config", "source", "source_ref", "validation", "notes",
}
RUN_LOGS = [
    "seed-expansion-run.txt",
    "gen-run-001-vlan-trunk.txt",
    "gen-run-002-static-ospf.txt",
    "gen-run-003-bgp-iface.txt",
]


def load_jsonl(path):
    rows = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        rows.append((lineno, json.loads(line)))
    return rows


def logged_pass_ids():
    """IDs with at least one 'parse=PASS ... batfish=True' line in the run logs."""
    passed = set()
    pat = re.compile(r"^(\S+)\s+\S+\s+parse=PASS .*batfish=True")
    for name in RUN_LOGS:
        for line in (DOCS_DIR / name).read_text().splitlines():
            m = pat.match(line)
            if m:
                passed.add(m.group(1))
    return passed


def main():
    problems = []   # hard problems: schema violations, dup IDs/pairs, unlogged IDs
    warnings = []   # informational: dup config-only / intent-only, scrub hits

    # ---- load corpus in fixed order: seeds, then generated files by name ----
    corpus = []  # (origin_file, row)
    for lineno, row in load_jsonl(SEED_FILE):
        corpus.append(("data/examples/seed.jsonl", row))
    for path in sorted(GENERATED_DIR.glob("*.jsonl")):
        for lineno, row in load_jsonl(path):
            corpus.append((f"data/generated/{path.name}", row))

    print(f"Loaded {len(corpus)} rows")

    # ---- integrity pass ----
    ids = Counter(r["id"] for _, r in corpus if isinstance(r.get("id"), str))
    for rid, n in ids.items():
        if n > 1:
            problems.append(f"duplicate id '{rid}' x{n}")

    pair_counts = Counter((r.get("intent_nl"), r.get("config")) for _, r in corpus)
    for (intent, _cfg), n in pair_counts.items():
        if n > 1:
            problems.append(f"duplicate (intent_nl, config) pair x{n}: {intent!r:.80}")

    cfg_counts = Counter(r.get("config") for _, r in corpus)
    for cfg, n in cfg_counts.items():
        if n > 1:
            warnings.append(f"config text shared by {n} rows (different intents): {str(cfg)[:60]!r}...")
    intent_counts = Counter(r.get("intent_nl") for _, r in corpus)
    for intent, n in intent_counts.items():
        if n > 1:
            warnings.append(f"intent_nl shared by {n} rows: {str(intent)[:80]!r}")

    passed_ids = logged_pass_ids()
    print(f"Run logs document passes for {len(passed_ids)} unique IDs")

    scrub_pat = re.compile(r"\bsecret\b|snmp-server community|password 7 |encryption", re.I)

    for origin, r in corpus:
        rid = r.get("id", "<missing id>")
        keys = set(r.keys())
        if keys != REQUIRED_KEYS:
            problems.append(f"{rid} ({origin}): keyset mismatch missing={REQUIRED_KEYS - keys} extra={keys - REQUIRED_KEYS}")
        if r.get("family") not in FAMILIES:
            problems.append(f"{rid}: family {r.get('family')!r} not in v0 families")
        if origin.startswith("data/generated/"):
            stem = Path(origin).stem
            if r.get("family") != stem:
                problems.append(f"{rid}: in {origin} but family={r.get('family')!r}")
        if not isinstance(r.get("intent_nl"), str) or not r["intent_nl"].strip():
            problems.append(f"{rid}: intent_nl empty/not a string")
        if not isinstance(r.get("config"), str) or not r["config"].strip():
            problems.append(f"{rid}: config empty/not a string")
        if r.get("source") not in SOURCES:
            problems.append(f"{rid}: source {r.get('source')!r} not in {sorted(SOURCES)}")
        dev = r.get("device")
        if not isinstance(dev, dict) or dev.get("vendor") != "cisco" or dev.get("os") != "ios_xe":
            problems.append(f"{rid}: device block unexpected: {dev!r}")
        if not isinstance(r.get("validation"), dict):
            problems.append(f"{rid}: validation block missing/not a dict")
        if rid not in passed_ids:
            problems.append(f"{rid}: no parse=PASS/batfish=True line found in run logs")
        if isinstance(r.get("config"), str) and scrub_pat.search(r["config"]):
            warnings.append(f"{rid}: config matches scrub pattern (secret/community/type-7): review")

    print(f"Integrity: {len(problems)} problems, {len(warnings)} warnings")
    for p in problems:
        print(f"  PROBLEM: {p}")
    for w in warnings:
        print(f"  WARNING: {w}")

    # ---- assemble dataset.jsonl (copies; sources untouched) ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for _origin, r in corpus:
        row = json.loads(json.dumps(r))  # deep copy
        if row["id"] in passed_ids:
            row["validation"]["parse_ok"] = True
            row["validation"]["batfish_ok"] = True
        out_rows.append(row)

    with open(OUT_DIR / "dataset.jsonl", "w") as f:
        for row in out_rows:
            f.write(json.dumps(row) + "\n")
    print(f"Wrote dataset.jsonl: {len(out_rows)}")

    # ---- splits: stratified by family, 90/5/5, seed 42 (make_splits.py logic) ----
    by_family = defaultdict(list)
    for row in out_rows:
        by_family[row["family"]].append(row)
    rng = random.Random(SPLIT_SEED)
    splits = {"train": [], "val": [], "test": []}
    per_family_split = {}
    for fam in sorted(by_family):
        items = by_family[fam]
        rng.shuffle(items)
        n = len(items)
        n_test = max(1, round(n * 0.05)) if n >= 20 else 0
        n_val = max(1, round(n * 0.05)) if n >= 20 else 0
        splits["test"].extend(items[:n_test])
        splits["val"].extend(items[n_test:n_test + n_val])
        splits["train"].extend(items[n_test + n_val:])
        per_family_split[fam] = {
            "total": n,
            "train": n - n_test - n_val,
            "val": n_val,
            "test": n_test,
        }
        print(f"{fam}: {n} -> train {n - n_test - n_val} / val {n_val} / test {n_test}")
    for name, data in splits.items():
        rng.shuffle(data)
        with open(OUT_DIR / f"{name}.jsonl", "w") as f:
            for row in data:
                f.write(json.dumps(row) + "\n")
        print(f"Wrote {name}.jsonl: {len(data)}")

    # split disjointness sanity check
    split_ids = {name: {r["id"] for r in data} for name, data in splits.items()}
    assert not (split_ids["train"] & split_ids["val"]), "train/val overlap"
    assert not (split_ids["train"] & split_ids["test"]), "train/test overlap"
    assert not (split_ids["val"] & split_ids["test"]), "val/test overlap"
    assert sum(len(s) for s in split_ids.values()) == len(out_rows), "split total mismatch"

    # ---- stats.json ----
    fam_totals = Counter(r["family"] for r in out_rows)
    stats = {
        "dataset": "netconfig-slm",
        "version": "0.1",
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(out_rows),
        "families": {fam: fam_totals[fam] for fam in sorted(fam_totals)},
        "splits": {
            "seed": SPLIT_SEED,
            "method": "stratified by family, 90/5/5 train/val/test, per-family shuffle with random.Random(42); adapted from scripts/make_splits.py",
            "sizes": {name: len(data) for name, data in splits.items()},
            "per_family": per_family_split,
        },
        "validation": {
            "parse_pass": sum(1 for r in out_rows if r["validation"].get("parse_ok") is True),
            "batfish_pass": sum(1 for r in out_rows if r["validation"].get("batfish_ok") is True),
            "emulation_pass": None,
            "evidence": [f"docs/{n}" for n in RUN_LOGS],
            "note": (
                "Source corpus rows carry validation.parse_ok/batfish_ok = null; "
                "every row's pass is documented per ID in the run logs above "
                "(580/580 parse, 580/580 Batfish on the final corpus; ospf-0066/67/68 "
                "failed a first Batfish run, were fixed, and passed on re-validation). "
                "Flags were populated in the v0.1 copies from those logs after a "
                "per-ID cross-check; source files were not modified."
            ),
        },
        "provenance": {
            "handwritten_seeds": sum(1 for r in out_rows if r["source"] == "handwritten"),
            "generated_synthetic": sum(1 for r in out_rows if r["source"] == "synthetic"),
            "cisco_guide": sum(1 for r in out_rows if r["source"] == "cisco_guide"),
            "origin_files": ["data/examples/seed.jsonl"] + [
                f"data/generated/{p.name}" for p in sorted(GENERATED_DIR.glob("*.jsonl"))
            ],
        },
        "integrity": {
            "unique_ids": len(ids),
            "duplicate_intent_config_pairs": sum(1 for n in pair_counts.values() if n > 1),
            "problems": problems,
            "warnings": warnings,
        },
    }
    (OUT_DIR / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print("Wrote stats.json")
    print("DONE" if not problems else "DONE WITH PROBLEMS - see above")


if __name__ == "__main__":
    main()
