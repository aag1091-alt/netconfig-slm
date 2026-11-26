#!/usr/bin/env python3
"""Resumable batch-7 validation driver: per family, validate only the
rows whose ids are not already in b7logs/{family}.log (full result
lines), appending to the log. Safe to relaunch after an interruption;
fill_b7_flags.py consumes the completed logs afterwards."""
import json, re, subprocess, os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FAMS = ["vlan_l2", "trunk_svi", "static_route", "ospf", "bgp",
        "interface_mgmt"]
proxy = os.environ["HTTPS_PROXY"].rsplit(":", 1)[0] + ":3130"

for fam in FAMS:
    log = BASE / f"b7logs/{fam}.log"
    done = set()
    if log.exists():
        for line in log.read_text().splitlines():
            m = re.match(r"^(\S+)\s+\S+\s+parse=(PASS|FAIL).*batfish=", line)
            if m:
                done.add(m.group(1))
    rows = [json.loads(l) for l in
            (BASE / f"data/generated/{fam}_b7.jsonl").read_text().splitlines()
            if l.strip()]
    rem = [r for r in rows if r["id"] not in done]
    print(f"{fam}: {len(done)} done, {len(rem)} remaining", flush=True)
    if not rem:
        continue
    tmp = BASE / f"b7logs/.remaining_{fam}.jsonl"
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rem))
    with open(log, "a") as lf:
        rc = subprocess.run(
            [str(BASE / ".venv/bin/python"), "-u",
             str(BASE / "src/validate/validate.py"),
             "--input", str(tmp), "--batfish-host", "macbooks-macbook-pro",
             "--batfish-proxy", proxy],
            stdout=lf, stderr=subprocess.STDOUT, cwd=BASE).returncode
    tmp.unlink(missing_ok=True)
    print(f"{fam}: validator rc={rc}", flush=True)
print("ALL_VALIDATION_DONE", flush=True)
