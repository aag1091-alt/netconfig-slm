#!/usr/bin/env python3
"""Dev scoring for yang-learn, mirroring the run-1 dev-eval path:
score_bench.py's mechanical GRADERS applied to dev references
(corpus/dev.jsonl + corpus/dev_v2_additions.jsonl) instead of the
benchmark references score_bench.py hardcodes.

Usage: score_dev.py GENERATIONS_JSONL [OUT_JSON] [--v1-only]
--v1-only restricts to the original 78 v1 dev ids (apples-to-apples
vs the run-1 dev eval's 63/78 = 80.8%).
Also reports the revised_schema verdict-only diagnostic (leading
Yes/No only, no citation requirement), as in RUN1_RESULTS.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

YANG = Path(__file__).resolve().parents[2] / "yang-learn"
sys.path.insert(0, str(YANG))
import score_bench  # noqa: E402

HERE = YANG


def main() -> int:
    gens_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    v1_only = "--v1-only" in sys.argv
    refs = {}
    for name in ("corpus/dev.jsonl", "corpus/dev_v2_additions.jsonl"):
        for line in (HERE / name).read_text().splitlines():
            r = json.loads(line)
            refs[r["id"]] = (r, "v1" if name.endswith("dev.jsonl")
                             else "v2")
    rows = []
    rs_verdict = {"n": 0, "pass": 0}
    for line in gens_path.read_text().splitlines():
        g = json.loads(line)
        if g.get("set") not in (None, "dev"):
            continue
        rec, split = refs[g["id"]]
        if v1_only and split != "v1":
            continue
        ok, node_hit = score_bench.GRADERS[rec["family"]](
            rec, g["generated"])
        rows.append({"id": g["id"], "family": rec["family"],
                     "split": split, "pass": ok,
                     "node_hit": node_hit,
                     "stopped_cleanly": g["stopped_cleanly"]})
        if rec["family"] == "revised_schema":
            ans = rec["fact"].get("answer", {})
            if ans.get("rule"):
                want = "Yes" if ans.get("verdict") == "allowed" else "No"
                rs_verdict["n"] += 1
                rs_verdict["pass"] += int(
                    score_bench.lead_verdict(g["generated"]) == want)

    def agg(rs):
        n = len(rs)
        p = sum(r["pass"] for r in rs)
        return {"n": n, "pass": p,
                "pct": round(100 * p / n, 1) if n else None}

    out = {"overall": agg(rows), "by_family": {},
           "rs_verdict_only": rs_verdict}
    for fam in sorted({r["family"] for r in rows}):
        out["by_family"][fam] = agg([r for r in rows
                                     if r["family"] == fam])
    iv_bad = [r for r in rows if r["family"] == "instance_validity"
              and r["node_hit"] is not None]
    out["iv_invalid_node_hit"] = {
        "n": len(iv_bad), "hit": sum(1 for r in iv_bad if r["node_hit"])}
    for label, rs in (("stopped", [r for r in rows
                                   if r["stopped_cleanly"]]),
                      ("not_stopped", [r for r in rows
                                       if not r["stopped_cleanly"]])):
        out[f"pass_when_{label}"] = agg(rs)
    out["rows"] = rows
    print(json.dumps({k: v for k, v in out.items() if k != "rows"},
                     indent=1))
    if out_path:
        out_path.write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
