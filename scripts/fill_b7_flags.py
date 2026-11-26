#!/usr/bin/env python3
"""Fill validation flags in data/generated/{family}_b7.jsonl from the
validator logs in b7logs/{family}.log. Fails loudly if any row did not
pass both rungs or is missing from the log."""
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAMS = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp",
        "interface_mgmt"]
LINE = re.compile(r"^(\S+)\s+\S+\s+parse=(PASS|FAIL).*batfish=(True|False|None)")

bad = 0
for fam in FAMS:
    log = (BASE / f"b7logs/{fam}.log").read_text()
    results = {}
    for line in log.splitlines():
        m = LINE.match(line)
        if m:
            results[m.group(1)] = (m.group(2) == "PASS",
                                   m.group(3) == "True")
    path = BASE / f"data/generated/{fam}_b7.jsonl"
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
                  f"batfish={b_ok}")
            bad += 1
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"{fam}: {npass}/{len(rows)} full pass, flags written")
sys.exit(1 if bad else 0)
