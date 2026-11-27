#!/usr/bin/env python3
"""Benchmark diagnostics beyond score_bench.py's headline: the
revised_schema verdict-only rate (leading Yes/No, no citation
requirement) that RUN1_RESULTS.md reports as a diagnostic.
Usage: bench_extra.py GENERATIONS_BENCHMARK_JSONL"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "yang-learn"))
import score_bench  # noqa: E402

HERE = Path(__file__).resolve().parents[2] / "yang-learn"
refs = {}
for name in ("corpus/benchmark.jsonl", "held_out.jsonl"):
    for line in (HERE / name).read_text().splitlines():
        r = json.loads(line)
        refs[r["id"]] = r

n = p = 0
for line in Path(sys.argv[1]).read_text().splitlines():
    g = json.loads(line)
    rec = refs[g["id"]]
    if rec["family"] != "revised_schema":
        continue
    ans = rec["fact"].get("answer", {})
    if ans.get("rule"):
        want = "Yes" if ans.get("verdict") == "allowed" else "No"
        n += 1
        p += int(score_bench.lead_verdict(g["generated"]) == want)
    else:  # hand-authored held-out form: verdict + profile named
        want = score_bench.completion_lead(rec["completion"])
        profile = (rec["fact"].get("profile") or "").split()[0]
        n += 1
        p += int(score_bench.lead_verdict(g["generated"]) == want
                 and profile in g["generated"])
print(json.dumps({"rs_verdict_only": {"n": n, "pass": p,
                                      "pct": round(100 * p / n, 1)}}))
