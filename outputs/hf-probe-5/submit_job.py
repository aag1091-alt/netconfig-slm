#!/usr/bin/env python3
"""Submit probe attempt 5 (job_probe_v5.py) as an HF Job.

Same pattern as attempts 1-4: direct POST /api/jobs/aag1091 using the
custom.huggingface connector's surrogate machinery (hf.py's call()),
script base64-embedded in a bash -c command behind a single pinned
pip invocation, bucket aag1091/netconfig-train-output mounted at
/output, flavor a10g-small, timeout 3600.
"""
import base64
import importlib.util
import json
from pathlib import Path

ROOT = Path("/home/hatch/workspace/projects/small-ai-model-for-networking/netconfig-slm")

spec = importlib.util.spec_from_file_location(
    "hf_helper",
    "/home/hatch/workspace/custom-connectors/custom.huggingface/bin/hf.py")
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)

script_b64 = base64.b64encode(
    (ROOT / "scripts/job_probe_v5.py").read_bytes()).decode()
pip_cmd = ("pip install torch==2.5.1 torchvision==0.20.1 "
           "torchaudio==2.5.1 'transformers>=4.51,<4.58' "
           "'trl>=0.19,<0.26' 'peft>=0.17,<0.19' 'bitsandbytes>=0.47' "
           "'accelerate>=1.4' 'datasets>=3.2,<4'")
command = (f"{pip_cmd} && echo '{script_b64}' | base64 -d "
           f"> /tmp/job_probe_v5.py && python3 /tmp/job_probe_v5.py")
payload = {
    "command": ["bash", "-c", command],
    "arguments": [],
    "environment": {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"},
    "flavor": "a10g-small",
    "timeoutSeconds": 3600,
    "dockerImage": "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
    "volumes": [{"type": "bucket",
                 "source": "aag1091/netconfig-train-output",
                 "mountPath": "/output"}],
}
status, data = hf.call("POST", "/api/jobs/aag1091", payload)
print(json.dumps({"status": status, "id": data.get("id"),
                  "job_status": data.get("status"),
                  "error": data.get("error") or data.get("message")
                  or data.get("detail")}, indent=1))
if status not in (200, 201) or not data.get("id"):
    raise SystemExit(1)
(ROOT / "outputs/hf-probe-5/job_id.txt").write_text(
    data["id"] + "\n# attempt 5 — attempt-4 reformulation + "
    "modules_to_save=['lm_head'] (job_probe_v5.py); outputs under "
    "bucket prefix run5-probe/\n")
print("job id saved:", data["id"])
