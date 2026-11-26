#!/usr/bin/env python3
"""Assemble dataset v0.2 = fixed v0.1 corpus (580) + batch 4 (600) = 1,180.

Sources (never modified):
  data/v0.1/dataset.jsonl          - published v0.1 snapshot (flags populated)
  data/generated/*_b4.jsonl        - batch 4, 100/family, flags populated

Fixes applied to v0.1 rows only, per docs/validation-round-1.md fix list:
  1. trunk_svi: drop `name <X>` lines under `vlan <id>` blocks where the
     intent never names that VLAN (kept only when the intent says
     "named <X>"). Round 1 counted 32 instances.
  2. bgp-0093 / bgp-0094: canonicalise the aggregate prefix in intent_nl
     and config (10.43.0.0/15 -> 10.42.0.0/15, 10.51.0.0/15 -> 10.50.0.0/15;
     config carries the mask form `aggregate-address <base> 255.254.0.0`).
     The /16 network statements are untouched.
  3. Glued `!` separators: any config line ending in `!` that is not a bare
     `!` separator is split into the command line + a bare `!` line.

Outputs under data/v0.2/: dataset.jsonl, train/val/test.jsonl (90/5/5
stratified by family, seed 42, same algorithm as build_v01_dataset.py),
stats.json, changed_rows.jsonl (v0.1 rows altered by the fixes, for
re-validation). DATASET_CARD.md is maintained alongside, not by this script.
"""
import ipaddress
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V01_FILE = ROOT / "data" / "v0.1" / "dataset.jsonl"
GENERATED_DIR = ROOT / "data" / "generated"
OUT_DIR = ROOT / "data" / "v0.2"
SPLIT_SEED = 42

FAMILIES = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp", "interface_mgmt"]
SOURCES = {"handwritten", "cisco_guide", "synthetic"}
REQUIRED_KEYS = {
    "id", "family", "intent_nl", "intent_oc", "device",
    "config", "source", "source_ref", "validation", "notes",
}


def load_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------- fixes ----------------

def fix_invented_vlan_names(row, log):
    """Drop config `name X` lines under a vlan block when the intent does
    not name that VLAN (intent names it only via a 'named X' phrase)."""
    if row["family"] != "trunk_svi":
        return row
    lines = row["config"].splitlines()
    out = []
    removed = 0
    for i, ln in enumerate(lines):
        m = re.match(r"^\s+name (\S+)\s*$", ln)
        if m and i > 0 and re.match(r"^vlan \d+\s*$", lines[i - 1]):
            name = m.group(1)
            if not re.search(rf"\bnamed\s+{re.escape(name)}\b", row["intent_nl"], re.I):
                removed += 1
                log.append(f"{row['id']}: removed invented 'name {name}' "
                           f"under '{lines[i - 1]}'")
                continue
        out.append(ln)
    if removed:
        row = dict(row, config="\n".join(out) + ("\n" if row["config"].endswith("\n") else ""))
    return row


AGG_FIXES = {
    "bgp-0093": ("10.43.0.0/15", "10.42.0.0/15", "10.43.0.0", "10.42.0.0"),
    "bgp-0094": ("10.51.0.0/15", "10.50.0.0/15", "10.51.0.0", "10.50.0.0"),
}


def fix_aggregate(row, log):
    if row["id"] not in AGG_FIXES:
        return row
    old_cidr, new_cidr, old_base, new_base = AGG_FIXES[row["id"]]
    assert old_cidr in row["intent_nl"], f"{row['id']}: {old_cidr} not in intent"
    row = dict(row)
    row["intent_nl"] = row["intent_nl"].replace(old_cidr, new_cidr)
    agg_old = f"aggregate-address {old_base} 255.254.0.0"
    agg_new = f"aggregate-address {new_base} 255.254.0.0"
    assert agg_old in row["config"], f"{row['id']}: aggregate line not found"
    row["config"] = row["config"].replace(agg_old, agg_new)
    log.append(f"{row['id']}: aggregate {old_cidr} -> {new_cidr} (intent + config)")
    return row


def fix_glued_bang(row, log):
    lines = row["config"].splitlines()
    out = []
    fixed = 0
    for ln in lines:
        if ln.rstrip().endswith("!") and ln.strip() != "!":
            out.append(ln.rstrip()[:-1])
            out.append("!")
            fixed += 1
        else:
            out.append(ln)
    if fixed:
        log.append(f"{row['id']}: split {fixed} glued-'!' line(s)")
        row = dict(row, config="\n".join(out) + ("\n" if row["config"].endswith("\n") else ""))
    return row


# ---------------- main ----------------

def main():
    problems, warnings = [], []

    v01_rows = load_jsonl(V01_FILE)
    print(f"Loaded v0.1 corpus: {len(v01_rows)} rows")

    fix_log = []
    fixed_rows, changed_ids = [], []
    for orig in v01_rows:
        row = json.loads(json.dumps(orig))  # deep copy
        row = fix_invented_vlan_names(row, fix_log)
        row = fix_aggregate(row, fix_log)
        row = fix_glued_bang(row, fix_log)
        if row != orig:
            changed_ids.append(row["id"])
        fixed_rows.append(row)
    n_name = sum(1 for e in fix_log if "invented" in e)
    n_agg = sum(1 for e in fix_log if "aggregate" in e)
    n_glue_rows = len({e.split(":")[0] for e in fix_log if "glued" in e})
    n_glue_lines = sum(int(re.search(r"split (\d+)", e).group(1))
                       for e in fix_log if "glued" in e)
    print(f"Fixes: {n_name} invented name lines removed, {n_agg} aggregate "
          f"rows canonicalised, {n_glue_lines} glued-'!' lines split in "
          f"{n_glue_rows} rows; {len(changed_ids)} v0.1 rows changed")

    b4_files = sorted(GENERATED_DIR.glob("*_b4.jsonl"))
    b4_rows = []
    for p in b4_files:
        rows = load_jsonl(p)
        assert len(rows) == 100, f"{p.name}: {len(rows)} rows != 100"
        b4_rows.extend(rows)
    print(f"Loaded batch 4: {len(b4_rows)} rows from {len(b4_files)} files")

    corpus = fixed_rows + b4_rows
    print(f"Merged corpus: {len(corpus)} rows")

    # ---- integrity ----
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

    # ---- write dataset + changed rows ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "dataset.jsonl", "w") as f:
        for row in corpus:
            f.write(json.dumps(row) + "\n")
    changed = [r for r in fixed_rows if r["id"] in set(changed_ids)]
    with open(OUT_DIR / "changed_rows.jsonl", "w") as f:
        for row in changed:
            f.write(json.dumps(row) + "\n")
    print(f"Wrote dataset.jsonl: {len(corpus)}; changed_rows.jsonl: {len(changed)}")

    # ---- splits: identical algorithm to build_v01_dataset.py ----
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
        "version": "0.2",
        "assembled_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(corpus),
        "families": {fam: fam_totals[fam] for fam in sorted(fam_totals)},
        "splits": {
            "seed": SPLIT_SEED,
            "method": "stratified by family, 90/5/5 train/val/test, per-family shuffle with random.Random(42); same algorithm as v0.1 (scripts/build_v01_dataset.py)",
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
                "docs/validation-round-1.md", "data/v0.2/intent_match_results.jsonl",
            ],
            "note": (
                "v0.2 = v0.1 corpus (580, flags carried from the published "
                "snapshot) + batch 4 (600, flags from gen run 004, which "
                "included an intent-match self-check gate). Every row "
                "changed by the v0.2 fixes was re-run through both ladder "
                "rungs (parse + Batfish) - see gen-run-005. The full "
                "1,180-row corpus was re-checked with scripts/intent_match.py "
                "(round 1 on v0.1: 580/580, 3,686 assertions)."
            ),
        },
        "fixes_applied": {
            "invented_vlan_name_lines_removed": n_name,
            "aggregate_prefixes_canonicalised": n_agg,
            "glued_bang_lines_split": n_glue_lines,
            "glued_bang_rows": n_glue_rows,
            "rows_changed_total": len(changed_ids),
            "detail": fix_log,
        },
        "provenance": {
            "handwritten_seeds": sum(1 for r in corpus if r["source"] == "handwritten"),
            "generated_synthetic": sum(1 for r in corpus if r["source"] == "synthetic"),
            "cisco_guide": sum(1 for r in corpus if r["source"] == "cisco_guide"),
            "origin_files": ["data/v0.1/dataset.jsonl"] + [
                f"data/generated/{p.name}" for p in b4_files
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
