#!/usr/bin/env python3
"""Assemble dataset v1.0 = v0.4 corpus (3,280) + batch 7 (1,720) = 5,000.

v1.0 is the COMPLETE locked Cisco v0 dataset: the 2026-11-22 scope
(6 families, ~5,000 validated pairs) is fulfilled at exactly 5,000 rows
(834 per family, 830 interface_mgmt). Cisco v0 dataset growth stops here;
the full QLoRA training run follows.

Sources (never modified):
  data/v0.4/dataset.jsonl          - published v0.4 snapshot (all round-1
                                     fixes already applied, flags populated)
  data/generated/*_b7.jsonl        - batch 7, 287/family (285 interface_mgmt),
                                     flags populated from gen run 007
                                     (docs/gen-run-007-batch7.txt)

No data fixes in v1.0: v0.4 rows are taken as-is, batch-7 rows carry real
validation flags from their generation run (which included a mutation-
tested intent-match self-check gate, 22/22).

Outputs under data/v1.0/: dataset.jsonl, train/val/test.jsonl (90/5/5
stratified by family, seed 42, same algorithm as build_v01..v04).
DATASET_CARD.md is maintained alongside, not by this script.
"""
import ipaddress
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V04_FILE = ROOT / "data" / "v0.4" / "dataset.jsonl"
GENERATED_DIR = ROOT / "data" / "generated"
OUT_DIR = ROOT / "data" / "v1.0"
SPLIT_SEED = 42

FAMILIES = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp", "interface_mgmt"]
SOURCES = {"handwritten", "cisco_guide", "synthetic"}
REQUIRED_KEYS = {
    "id", "family", "intent_nl", "intent_oc", "device",
    "config", "source", "source_ref", "validation", "notes",
}
# batch 7 quotas per generated file
B7_EXPECTED = {
    "vlan_l2_b7.jsonl": 287,
    "trunk_svi_b7.jsonl": 287,
    "static_route_b7.jsonl": 287,
    "ospf_b7.jsonl": 287,
    "bgp_b7.jsonl": 287,
    "interface_mgmt_b7.jsonl": 285,
}


def load_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main():
    problems, warnings = [], []

    v04_rows = load_jsonl(V04_FILE)
    print(f"Loaded v0.4 corpus: {len(v04_rows)} rows")
    assert len(v04_rows) == 3280, f"v0.4 corpus: {len(v04_rows)} rows != 3280"

    b7_files = sorted(GENERATED_DIR.glob("*_b7.jsonl"))
    assert {p.name for p in b7_files} == set(B7_EXPECTED), \
        f"batch-7 files mismatch: {[p.name for p in b7_files]}"
    b7_rows = []
    for p in b7_files:
        rows = load_jsonl(p)
        assert len(rows) == B7_EXPECTED[p.name], \
            f"{p.name}: {len(rows)} rows != {B7_EXPECTED[p.name]}"
        b7_rows.extend(rows)
    print(f"Loaded batch 7: {len(b7_rows)} rows from {len(b7_files)} files")

    corpus = v04_rows + b7_rows
    print(f"Merged corpus: {len(corpus)} rows")
    assert len(corpus) == 5000, f"merged corpus: {len(corpus)} rows != 5000"

    # ---- integrity (same checks as build_v02/v03/v04_dataset.py) ----
    ids = Counter(r["id"] for r in corpus if isinstance(r.get("id"), str))
    for rid, n in ids.items():
        if n > 1:
            problems.append(f"duplicate id '{rid}' x{n}")
    pair_counts = Counter((r.get("intent_nl"), r.get("config")) for r in corpus)
    for (intent, _c), n in pair_counts.items():
        if n > 1:
            problems.append(f"duplicate (intent_nl, config) pair x{n}: {intent!r:.80}")

    scrub_pat = re.compile(r"\bsecret\b|snmp-server community|password 7 |encryption", re.I)
    ip_pat = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
    routable_tokens = Counter()
    for r in corpus:
        rid = r.get("id", "<missing id>")
        if set(r.keys()) != REQUIRED_KEYS:
            problems.append(f"{rid}: keyset mismatch")
        if r.get("family") not in FAMILIES:
            problems.append(f"{rid}: bad family {r.get('family')!r}")
        if r.get("source") not in SOURCES:
            problems.append(f"{rid}: bad source {r.get('source')!r}")
        dev = r.get("device")
        if not isinstance(dev, dict) or dev.get("vendor") != "cisco" or dev.get("os") != "ios_xe":
            problems.append(f"{rid}: device block unexpected")
        v = r.get("validation")
        if not isinstance(v, dict) or v.get("parse_ok") is not True or v.get("batfish_ok") is not True:
            problems.append(f"{rid}: validation flags not populated true: {v!r}")
        if scrub_pat.search(r["config"]):
            warnings.append(f"{rid}: config matches scrub pattern (secret/community/type-7)")
        # IP role scan: globally-routable dotted quads are only acceptable
        # as OSPF/BGP router-id identifiers (lab placeholders), never as
        # assigned addresses / peers / networks.
        for field in ("intent_nl", "config"):
            for line in r[field].splitlines():
                for tok in ip_pat.findall(line):
                    a = ipaddress.ip_address(tok)
                    if not (a.is_private or a.is_loopback or a.is_link_local
                            or a.is_reserved or a.is_multicast or a.is_unspecified):
                        if "router-id" in line:
                            routable_tokens[tok] += 1
                        else:
                            problems.append(f"{rid}: routable IP {tok} outside router-id: {line.strip()!r}")

    print(f"Integrity: {len(problems)} problems, {len(warnings)} warnings")
    for p in problems:
        print(f"  PROBLEM: {p}")
    for w in warnings:
        print(f"  WARNING: {w}")
    print(f"Router-id-only routable tokens (documented placeholders): "
          f"{dict(routable_tokens)}")

    # ---- write dataset ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "dataset.jsonl", "w") as f:
        for row in corpus:
            f.write(json.dumps(row) + "\n")
    print(f"Wrote dataset.jsonl: {len(corpus)}")

    # ---- splits: identical algorithm to build_v01/v02/v03/v04_dataset.py ----
    by_family = defaultdict(list)
    for row in corpus:
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
        per_family_split[fam] = {"total": n, "train": n - n_test - n_val,
                                 "val": n_val, "test": n_test}
        print(f"{fam}: {n} -> train {n - n_test - n_val} / val {n_val} / test {n_test}")
    for name, data in splits.items():
        rng.shuffle(data)
        with open(OUT_DIR / f"{name}.jsonl", "w") as f:
            for row in data:
                f.write(json.dumps(row) + "\n")
        print(f"Wrote {name}.jsonl: {len(data)}")
    split_ids = {name: {r["id"] for r in d} for name, d in splits.items()}
    assert not (split_ids["train"] & split_ids["val"])
    assert not (split_ids["train"] & split_ids["test"])
    assert not (split_ids["val"] & split_ids["test"])
    assert sum(len(s) for s in split_ids.values()) == len(corpus)

    # ---- stats.json ----
    fam_totals = Counter(r["family"] for r in corpus)
    stats = {
        "dataset": "netconfig-slm",
        "version": "1.0",
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(corpus),
        "families": {fam: fam_totals[fam] for fam in sorted(fam_totals)},
        "splits": {
            "seed": SPLIT_SEED,
            "method": "stratified by family, 90/5/5 train/val/test, per-family shuffle with random.Random(42); same algorithm as v0.1-v0.4 (scripts/build_v01_dataset.py .. build_v04_dataset.py)",
            "sizes": {name: len(d) for name, d in splits.items()},
            "per_family": per_family_split,
        },
        "validation": {
            "parse_pass": sum(1 for r in corpus if r["validation"].get("parse_ok") is True),
            "batfish_pass": sum(1 for r in corpus if r["validation"].get("batfish_ok") is True),
            "emulation_pass": None,
            "evidence": [
                "docs/seed-expansion-run.txt", "docs/gen-run-001-vlan-trunk.txt",
                "docs/gen-run-002-static-ospf.txt", "docs/gen-run-003-bgp-iface.txt",
                "docs/gen-run-004-batch4.txt", "docs/gen-run-005-v02-revalidation.txt",
                "docs/gen-run-005-batch5.txt", "docs/gen-run-006-batch6.txt",
                "docs/gen-run-007-batch7.txt",
                "docs/validation-round-1.md", "data/v0.2/intent_match_results.jsonl",
                "data/v0.3/intent_match_results.jsonl",
                "data/v0.4/intent_match_results.jsonl",
                "data/v1.0/intent_match_results.jsonl",
            ],
            "note": (
                "v1.0 = v0.4 corpus (3,280, validated at v0.4) + batch 7 "
                "(1,720, flags from gen run 007, which included a "
                "mutation-tested intent-match self-check gate: 22/22 "
                "hand-corrupted rows caught). v1.0 completes the locked "
                "Cisco v0 scope (2026-11-22): 6 families, exactly 5,000 "
                "validated pairs. The full 5,000-row corpus was "
                "additionally audited with scripts/intent_match.py; "
                "residual flags are documented checker extraction limits "
                "(triaged, sample-verified), not data mismatches - see "
                "data/v1.0/intent_match_results.jsonl and the v1.0 "
                "DATASET_CARD.md intent-match section for the triage detail."
            ),
        },
        "provenance": {
            "handwritten_seeds": sum(1 for r in corpus if r["source"] == "handwritten"),
            "generated_synthetic": sum(1 for r in corpus if r["source"] == "synthetic"),
            "cisco_guide": sum(1 for r in corpus if r["source"] == "cisco_guide"),
            "origin_files": ["data/v0.4/dataset.jsonl"] + [
                f"data/generated/{p.name}" for p in b7_files
            ],
        },
        "integrity": {
            "unique_ids": len(ids),
            "duplicate_intent_config_pairs": sum(1 for n in pair_counts.values() if n > 1),
            "scrub_secret_pattern_hits": len(warnings),
            "routable_ip_tokens": "all globally-routable dotted quads are OSPF/BGP router-id identifiers (conventional lab placeholders, e.g. 1.1.1.1) in v0.1 seed-era rows; every assigned address, peer, network and next-hop is RFC1918 or an IETF documentation range",
            "problems": problems,
            "warnings": warnings,
        },
    }
    (OUT_DIR / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print("Wrote stats.json")
    print("DONE" if not problems else "DONE WITH PROBLEMS - see above")


if __name__ == "__main__":
    main()
