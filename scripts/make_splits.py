#!/usr/bin/env python3
"""Stratified train/val/test splits from a validated JSONL file. Seed fixed for reproducibility."""
import argparse, json, random
from collections import defaultdict
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="data/processed")
    args = ap.parse_args()
    rows = [json.loads(l) for l in Path(args.input).read_text().splitlines() if l.strip()]
    by_family = defaultdict(list)
    for r in rows:
        by_family[r["family"]].append(r)
    rng = random.Random(42)
    train, val, test = [], [], []
    for fam, items in by_family.items():
        rng.shuffle(items)
        n = len(items)
        n_test = max(1, round(n * 0.05)) if n >= 20 else 0
        n_val = max(1, round(n * 0.05)) if n >= 20 else 0
        test.extend(items[:n_test]); val.extend(items[n_test:n_test+n_val]); train.extend(items[n_test+n_val:])
        print(f"{fam}: {len(items)} -> train {len(items)-n_test-n_val} / val {n_val} / test {n_test}")
    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    for name, data in [("train", train), ("val", val), ("test", test)]:
        rng.shuffle(data)
        (outdir / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in data) + "\n")
        print(f"Wrote {name}.jsonl: {len(data)}")

if __name__ == "__main__":
    main()
