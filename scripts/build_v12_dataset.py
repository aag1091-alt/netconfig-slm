#!/usr/bin/env python3
"""Assemble dataset v1.2 = v1.1 corpus (5,861) + batch 9 full passes.

v1.2 is the second Phase-1 expansion (docs/PHASE1_PLAN.md): three
new families - acl, nat, hsrp - at 287 generated rows each. Only rows
whose validation flags are both true are included; flagged-out rows
stay in data/generated/*_b9.jsonl with their flags populated and are
reported here as drops.

Sources (never modified):
  data/v1.1/dataset.jsonl          - published v1.1 snapshot (5,861)
  data/generated/{acl,nat,hsrp}_b9.jsonl
                                   - batch 9, flags populated by
                                     scripts/fill_b9_flags.py from the
                                     b9logs/ validator run

Outputs under data/v1.2/: dataset.jsonl, train/val/test.jsonl (90/5/5
stratified by family, seed 42, same algorithm as build_v01..v10).
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
V11_FILE = ROOT / "data" / "v1.1" / "dataset.jsonl"
GENERATED_DIR = ROOT / "data" / "generated"
OUT_DIR = ROOT / "data" / "v1.2"
SPLIT_SEED = 42

FAMILIES = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp",
            "interface_mgmt", "etherchannel", "stp", "port_security",
            "acl", "nat", "hsrp"]
B9_FILES = ["acl_b9.jsonl", "nat_b9.jsonl", "hsrp_b9.jsonl"]
SOURCES = {"handwritten", "cisco_guide", "synthetic"}
REQUIRED_KEYS = {
    "id", "family", "intent_nl", "intent_oc", "device",
    "config", "source", "source_ref", "validation", "notes",
}


def load_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main():
    problems, warnings = [], []

    v11_rows = load_jsonl(V11_FILE)
    print(f"Loaded v1.1 corpus: {len(v11_rows)} rows")
    assert len(v11_rows) == 5861, f"v1.1 corpus: {len(v11_rows)} rows != 5861"

    b9_rows, b9_dropped = [], Counter()
    for name in B9_FILES:
        rows = load_jsonl(GENERATED_DIR / name)
        for r in rows:
            v = r.get("validation") or {}
            if v.get("parse_ok") is True and v.get("batfish_ok") is True:
                b9_rows.append(r)
            else:
                b9_dropped[r["family"]] += 1
                print(f"  DROP {r['id']} ({r['family']}): "
                      f"parse_ok={v.get('parse_ok')} "
                      f"batfish_ok={v.get('batfish_ok')}")
    b9_counts = Counter(r["family"] for r in b9_rows)
    print(f"Loaded batch 9 full passes: {len(b9_rows)} rows "
          f"({dict(b9_counts)}); dropped {sum(b9_dropped.values())} "
          f"({dict(b9_dropped)})")

    corpus = v11_rows + b9_rows
    print(f"Merged corpus: {len(corpus)} rows")

    # ---- integrity (same checks as build_v02..v10) ----
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

    # ---- splits: identical algorithm to build_v01..v10 ----
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
        "version": "1.2",
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(corpus),
        "families": {fam: fam_totals[fam] for fam in sorted(fam_totals)},
        "splits": {
            "seed": SPLIT_SEED,
            "method": "stratified by family, 90/5/5 train/val/test, per-family shuffle with random.Random(42); same algorithm as v0.1-v1.0 (scripts/build_v01_dataset.py .. build_v10_dataset.py)",
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
                "data/v1.1/intent_match_results.jsonl",
                "data/v1.2/intent_match_results.jsonl",
                "b8logs/etherchannel.log", "b8logs/stp.log",
                "b8logs/port_security.log",
                "b9logs/acl.log", "b9logs/nat.log", "b9logs/hsrp.log",
                "b9logs/probes.log", "b9logs/probes2.log",
            ],
            "note": (
                "v1.2 = v1.1 corpus (5,861, validated at v1.1) + batch 9 "
                f"({len(b9_rows)} full passes of 861 generated; "
                f"{sum(b9_dropped.values())} rows dropped at the ladder, "
                "kept out with flags populated in data/generated/*_b9.jsonl). "
                "Batch 9 is Phase 1, Batch 2 (docs/PHASE1_PLAN.md): three "
                "new families - acl, nat, hsrp. Every new CLI construct "
                "was probe-passed against Batfish before generation "
                "(24 probes, b9logs/probes*.log); Batfish-unmodelable "
                "constructs (ACL fragments keyword, NAT static port "
                "forwarding, NAT pool /28 netmask, extended ACL as NAT "
                "source list, HSRP standby track / name) were excluded "
                "from generation and ship parse+intent-only as probe "
                "evidence, not dataset rows. The generator gate is "
                "scripts/intent_match.py itself (new check_acl / "
                "check_nat / check_hsrp checkers, mutation-tested 15/15 "
                "in scripts/intent_match_b9_controls.py). No retrain "
                "accompanies v1.2; retraining happens at milestones."
            ),
        },
        "provenance": {
            "handwritten_seeds": sum(1 for r in corpus if r["source"] == "handwritten"),
            "generated_synthetic": sum(1 for r in corpus if r["source"] == "synthetic"),
            "cisco_guide": sum(1 for r in corpus if r["source"] == "cisco_guide"),
            "origin_files": ["data/v1.1/dataset.jsonl"] + [
                f"data/generated/{p}" for p in B9_FILES
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
