#!/usr/bin/env python3
"""Submit the yang-learn run-2 GGUF conversion job and poll to
completion. EXACT run-1 GGUF recipe (job 6b09127054072e8b71694012:
a10g-small, pytorch 2.5.1 cuda DEVEL image, llama.cpp clone +
cmake llama-quantize + convert_hf_to_gguf f16 -> Q4_K_M,
timeoutSeconds=1800) with yang-learn-run1 paths swapped to
yang-learn-run2. Expected ~$0.18-0.20.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

BASE_DIR = Path("/home/hatch/workspace/projects/small-ai-model-for-"
                "networking/netconfig-slm")
OUT_META = BASE_DIR / "outputs" / "yang-learn-run2" / "gguf_job.json"
TERMINAL = {"COMPLETED", "ERROR", "CANCELED"}

BASH = """set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq git cmake build-essential > /dev/null
git clone --depth 1 https://github.com/ggerganov/llama.cpp /tmp/llama.cpp
pip install --quiet -r /tmp/llama.cpp/requirements.txt
cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build -DCMAKE_BUILD_TYPE=Release > /dev/null
cmake --build /tmp/llama.cpp/build --target llama-quantize -j 4
mkdir -p /output/yang-learn-run2/gguf
python3 /tmp/llama.cpp/convert_hf_to_gguf.py /output/yang-learn-run2/model --outfile /output/yang-learn-run2/gguf/model-f16.gguf --outtype f16
/tmp/llama.cpp/build/bin/llama-quantize /output/yang-learn-run2/gguf/model-f16.gguf /output/yang-learn-run2/gguf/netconfig-yang-qwen3-4b-q4_k_m.gguf Q4_K_M
rm /output/yang-learn-run2/gguf/model-f16.gguf
python3 -c "import json,os; p='/output/yang-learn-run2/gguf/netconfig-yang-qwen3-4b-q4_k_m.gguf'; json.dump({'status':'completed','file':'netconfig-yang-qwen3-4b-q4_k_m.gguf','size_bytes':os.path.getsize(p)}, open('/output/yang-learn-run2/gguf/DONE.json','w'), indent=2)"
echo GGUF_DONE"""


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
    payload = {
        "command": ["bash", "-c", BASH],
        "arguments": [],
        "environment": {},
        "flavor": "a10g-small",
        "timeoutSeconds": 1800,
        "dockerImage": "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-devel",
        "volumes": [{"type": "bucket",
                     "source": "aag1091/netconfig-train-output",
                     "mountPath": "/output", "readOnly": False}],
    }
    job = call("POST", "/api/jobs/aag1091", payload)
    jid = job.get("id")
    print("SUBMITTED:", jid, json.dumps(job.get("status")), flush=True)
    OUT_META.write_text(json.dumps(job, indent=1))
    deadline = time.time() + 45 * 60
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
