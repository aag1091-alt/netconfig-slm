#!/usr/bin/env python3
"""Fill validation flags in data/generated/{family}_b9.jsonl from the
validator logs in b9logs/{family}.log. Modeled on fill_b8_flags.py,
with the Phase-1 difference from docs/PHASE1_PLAN.md / the batch-8
tasking: rows that did not pass both rungs are flagged as-is and kept
OUT of dataset v1.2 by build_v11_dataset.py (drop counts reported),
instead of failing the whole batch. Exits 1 only on a structural
problem (log/file row-count mismatch or a row missing from the log).
"""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAMS = ["acl", "nat", "hsrp"]
LINE = re.compile(r"^(\S+)\s+\S+\s+parse=(PASS|FAIL).*batfish=(True|False|None)")

bad = 0
for fam in FAMS:
    log = (BASE / f"b9logs/{fam}.log").read_text()
    results = {}
    for line in log.splitlines():
        m = LINE.match(line)
        if m:
            results[m.group(1)] = (m.group(2) == "PASS",
                                   m.group(3) == "True")
    path = BASE / f"data/generated/{fam}_b9.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if len(results) != len(rows):
        print(f"{fam}: LOG/FILE MISMATCH log={len(results)} file={len(rows)}")
        bad += 1
        continue
    npass = 0
    for r in rows:
        p_ok, b_ok = results[r["id"]]
        r["validation"]["parse_ok"] = p_ok
        r["validation"]["batfish_ok"] = b_ok
        if p_ok and b_ok:
            npass += 1
        else:
            print(f"{fam}: NOT FULL PASS {r['id']} parse={p_ok} "
                  f"batfish={b_ok} (row kept out of v1.1)")
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"{fam}: {npass}/{len(rows)} full pass, flags written")
sys.exit(1 if bad else 0)
