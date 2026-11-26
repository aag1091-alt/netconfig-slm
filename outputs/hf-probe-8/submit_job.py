#!/usr/bin/env python3
"""Submit probe attempt 8 (job_probe_v8.py) as an HF Job.

Same pattern as attempts 1-7: direct POST /api/jobs/aag1091 using the
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

# Sanity (as in attempts 4/6/7): run 2's merged model must be in the
# bucket for the retained position scan. Read-only; the scan is
# crash-isolated in-job, so this only informs, never blocks.
status, tree = hf.call(
    "GET", "/api/buckets/aag1091/netconfig-train-output/tree")
paths = []

def walk(node):
    if isinstance(node, dict):
        if "path" in node:
            paths.append(node["path"])
        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)

walk(tree)
merged = [p for p in paths if p.startswith("run2/merged")]
print(f"bucket tree status {status}; run2/merged entries: "
      f"{len(merged)}; sample: {merged[:5]}")

script_b64 = base64.b64encode(
    (ROOT / "scripts/job_probe_v8.py").read_bytes()).decode()
pip_cmd = ("pip install torch==2.5.1 torchvision==0.20.1 "
           "torchaudio==2.5.1 'transformers>=4.51,<4.58' "
           "'trl>=0.19,<0.26' 'peft>=0.17,<0.19' 'bitsandbytes>=0.47' "
           "'accelerate>=1.4' 'datasets>=3.2,<4'")
command = (f"{pip_cmd} && echo '{script_b64}' | base64 -d "
           f"> /tmp/job_probe_v8.py && python3 /tmp/job_probe_v8.py")
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
(ROOT / "outputs/hf-probe-8/job_id.txt").write_text(
    data["id"] + "\n# attempt 8 — attempt-4 recipe verbatim "
    "(end-terminator reformulation, 102/256 shortest-quartile rows, "
    "EOS assertion, dual eval, >=90% gate, position scan retained) "
    "+ untie at load (config tie_word_embeddings=False on both the "
    "4-bit training load and the fp32 merge base load, in-job "
    "untie_check with corrective embed-value copy if the loader "
    "left lm_head uninitialized) + LoRA target_modules += lm_head "
    "(r16/alpha32/dropout 0.05, single optimizer lr 2e-4, warmup "
    "0.03, logging_steps 2); NO modules_to_save, NO gradient "
    "checkpointing — the head hypothesis tested via the LoRA route "
    "after attempts 5/7 died on modules_to_save NaNs "
    "(job_probe_v8.py); outputs under bucket prefix run8-probe/\n")
print("job id saved:", data["id"])
