#!/usr/bin/env python3
"""Submit run 3 (job_train_fullft_v3.py) as an HF Job.

Same pattern as runs 1-2 and probes 1-8: direct POST /api/jobs/aag1091
using the custom.huggingface connector's surrogate machinery (hf.py's
call()), script base64-embedded in a bash -c command behind a single
pinned pip invocation, bucket aag1091/netconfig-train-output mounted
at /output. Run 3 differs in scale only: flavor a100-large
($2.50/hr), timeoutSeconds 10000 (billing cap ~$6.94, inside the
~$5-7 envelope Avinash approved 2026-11-24).
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
    (ROOT / "scripts/job_train_fullft_v3.py").read_bytes()).decode()
pip_cmd = ("pip install torch==2.5.1 torchvision==0.20.1 "
           "torchaudio==2.5.1 'transformers>=4.51,<4.58' "
           "'trl>=0.19,<0.26' 'accelerate>=1.4' 'datasets>=3.2,<4'")
command = (f"{pip_cmd} && echo '{script_b64}' | base64 -d "
           f"> /tmp/job_train_fullft_v3.py && "
           f"python3 /tmp/job_train_fullft_v3.py")
payload = {
    "command": ["bash", "-c", command],
    "arguments": [],
    "environment": {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"},
    "flavor": "a100-large",
    "timeoutSeconds": 10000,
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
(ROOT / "outputs/hf-fullft/job_id.txt").write_text(
    data["id"] + "\n# run 3 — FULL FINE-TUNE (Avinash-approved "
    "~$5-7 envelope, 2026-11-24): job_train_fullft_v3.py; Qwen3-4B-Base "
    "in bf16, NO quantization/PEFT, all 4,022,468,096 params trainable, "
    "embeddings/lm_head TIED; dataset v1.0 from the Hub with sha guard "
    "(ecf3d5a9a67ab9a52342d3a2ed6ed91b01392831), 4,496 train rows; "
    "probe-v4 `end`-terminator transform on all train completions + "
    "test refs, endings verified in-job; EOS collated-batch assertion "
    "pre-training; lr 1e-5 cosine, warmup 0.05, wd 0.0, clip 1.0, "
    "epochs 2, bs 4 x accum 4 (eff. 16, ~562 steps), max_length 1536, "
    "gradient_checkpointing (use_reentrant=False), logging_steps 10; "
    "SLIM eval: trained model only, greedy on all 252 test prompts -> "
    "generations_test.jsonl + in-job stopped_cleanly; model saved to "
    "run3-fullft/model/ (bf16 safetensors + generation_config eos "
    "[151645, 151643]); pip freeze + versions in RUN_INFO.json; "
    "flavor a100-large ($2.50/hr), timeout 10000s (cap ~$6.94); "
    "outputs under bucket prefix run3-fullft/\n")
with open(ROOT / "outputs/hf-fullft/job_id_history.txt", "a") as f:
    f.write(f"{data['id']}  run 3 — FULL FINE-TUNE "
            "(job_train_fullft_v3.py): see job_id.txt for the full "
            "design; a100-large, timeout 10000s; outputs under "
            "run3-fullft/\n")
print("job id saved:", data["id"])
