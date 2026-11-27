#!/usr/bin/env python3
"""Poll an already-submitted HF job to a terminal stage and save
its final record. Used to recover monitoring after a local
runtime restart (the job itself runs server-side).
Usage: poll_job.py JOB_ID OUT_JSON"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

TERMINAL = {"COMPLETED", "ERROR", "CANCELED"}
jid, out_path = sys.argv[1], Path(sys.argv[2])
deadline = time.time() + 50 * 60
last = None
while time.time() < deadline:
    try:
        req = urllib.request.Request(
            f"https://huggingface.co/api/jobs/aag1091/{jid}",
            method="GET")
        add_surrogate_to_request(req, "custom.huggingface",
                                 allowed_hosts=["huggingface.co"])
        with urllib.request.build_opener().open(req, timeout=120) as resp:
            j = read_json_response(resp)
        stage = (j.get("status") or {}).get("stage")
        if stage != last:
            print(f"stage: {stage} durations: {j.get('durations')}",
                  flush=True)
            last = stage
        if stage in TERMINAL:
            print("FINAL:", json.dumps(
                {k: j.get(k) for k in
                 ("id", "status", "durations", "createdAt",
                  "startedAt", "finishedAt", "timeout")}, indent=1),
                flush=True)
            out_path.write_text(json.dumps(j, indent=1))
            sys.exit(0 if stage == "COMPLETED" else 1)
    except Exception as e:  # noqa: BLE001
        print("poll error (continuing):", e, flush=True)
    time.sleep(45)
print("MONITOR TIMEOUT")
sys.exit(1)
