#!/usr/bin/env python3
"""Submit the yang-learn run-2 TRAINING job and poll to completion.

Payload mirrors the run-1 training job: bash -c with a single
pinned pip install + base64-embedded script, bucket volume at
/output, environment (not env), timeoutSeconds create field (NOT
timeout — the run-1 submission bug). Flavor a100-large,
timeoutSeconds=3600 -> hard cap $2.50.
"""
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response
import urllib.request

BASE_DIR = Path("/home/hatch/workspace/projects/small-ai-model-for-"
                "networking/netconfig-slm")
SCRIPT = BASE_DIR / "yang-learn" / "job_train_yanglearn_v2.py"
OUT_META = BASE_DIR / "outputs" / "yang-learn-run2" / "train_job.json"
TERMINAL = {"COMPLETED", "ERROR", "CANCELED"}


def call(method, path, body=None):
    req = urllib.request.Request(
        "https://huggingface.co" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    add_surrogate_to_request(req, "custom.huggingface",
                             allowed_hosts=["huggingface.co"])
    with urllib.request.build_opener().open(req, timeout=120) as resp:
        return read_json_response(resp)


def main():
    b64 = base64.b64encode(SCRIPT.read_bytes()).decode()
    cmd = ("pip install torch==2.5.1 torchvision==0.20.1 "
           "torchaudio==2.5.1 'transformers>=4.51,<4.58' "
           "'trl>=0.19,<0.26' 'accelerate>=1.4' 'datasets>=3.2,<4' && "
           f"echo '{b64}' | base64 -d > /tmp/job_train_yanglearn_v2.py "
           "&& python3 /tmp/job_train_yanglearn_v2.py")
    payload = {
        "command": ["bash", "-c", cmd],
        "arguments": [],
        "environment": {"PYTORCH_CUDA_ALLOC_CONF":
                        "expandable_segments:True"},
        "flavor": "a100-large",
        "timeoutSeconds": 3600,
        "dockerImage": "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
        "volumes": [{"type": "bucket",
                     "source": "aag1091/netconfig-train-output",
                     "mountPath": "/output", "readOnly": False}],
    }
    job = call("POST", "/api/jobs/aag1091", payload)
    jid = job.get("id")
    print("SUBMITTED:", jid, json.dumps(job.get("status")), flush=True)
    OUT_META.write_text(json.dumps(job, indent=1))
    deadline = time.time() + 75 * 60
    last = None
    while time.time() < deadline:
        try:
            j = call("GET", f"/api/jobs/aag1091/{jid}")
            stage = (j.get("status") or {}).get("stage")
            if stage != last:
                print(f"stage: {stage} durations: {j.get('durations')}",
                      flush=True)
                last = stage
            if stage in TERMINAL:
                print("FINAL:", json.dumps(
                    {k: j.get(k) for k in
                     ("id", "status", "durations", "createdAt",
                      "startedAt", "finishedAt", "timeout")},
                    indent=1), flush=True)
                OUT_META.write_text(json.dumps(j, indent=1))
                return 0 if stage == "COMPLETED" else 1
        except Exception as e:  # noqa: BLE001
            print("poll error (continuing):", e, flush=True)
        time.sleep(45)
    print("MONITOR TIMEOUT")
    return 1


if __name__ == "__main__":
    sys.exit(main())
