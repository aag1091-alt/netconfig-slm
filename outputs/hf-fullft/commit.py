#!/usr/bin/env python3
"""One Hub commit: pre-uploaded LFS objects + small files (base64) + README."""
import base64, importlib.util, json, os, sys

spec = importlib.util.spec_from_file_location(
    "hf", "/home/hatch/workspace/custom-connectors/custom.huggingface/bin/hf.py")
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)

BASE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(BASE, "stage-model")
REPO = "aag1091/netconfig-slm-qwen3-4b"

LFS = [
    ("model-00001-of-00002.safetensors",
     "db9ea65212779183828dcb304dbc9b8046e61310ec54307c64b4571dfa4758d1",
     4967215360),
    ("model-00002-of-00002.safetensors",
     "6350d4630d88795decfe2aa3bf958198d57af370b29e9261ed07cb77ede2db8f",
     3077766632),
    ("netconfig-slm-qwen3-4b-q4_k_m.gguf",
     "f8c96f3e551fb0ab1e2d84e25ca4a7745c694a8e11e1bc61125f69c5fbde142e",
     2497280256),
]
SMALL = [
    "added_tokens.json", "chat_template.jinja", "config.json",
    "generation_config.json", "merges.txt", "model.safetensors.index.json",
    "special_tokens_map.json", "tokenizer_config.json",
    "training_args.bin", "vocab.json",
]

MODE = sys.argv[1] if len(sys.argv) > 1 else "lfs"
lines = [json.dumps({"key": "header", "value": {
    "summary": "Publish NetConfig SLM run 3 (full fine-tune) + Q4_K_M GGUF - "
               "publish bar cleared: Batfish 98.8%, intent 92.5% triaged, "
               "parse 100%"}})]
if MODE == "small":
    LFS = []
if MODE == "lfs":
    SMALL = []
for path, oid, size in LFS:
    lines.append(json.dumps({"key": "lfsFile", "value": {
        "path": path, "algo": "sha256", "oid": oid, "size": size}}))
for name in SMALL:
    with open(os.path.join(STAGE, name), "rb") as fh:
        content = base64.b64encode(fh.read()).decode()
    lines.append(json.dumps({"key": "file", "value": {
        "path": name, "content": content, "encoding": "base64"}}))
if MODE != "lfs":
    pass
else:
    with open(os.path.join(BASE, "README-model-card.md"), "rb") as fh:
        content = base64.b64encode(fh.read()).decode()
    lines.append(json.dumps({"key": "file", "value": {
        "path": "README.md", "content": content, "encoding": "base64"}}))

body = ("\n".join(lines) + "\n").encode()
print(f"commit body: {len(body)} bytes, {len(lines)} ops")
status, data = hf.call("POST", f"/api/models/{REPO}/commit/main", body,
                       content_type="application/x-ndjson")
print("COMMIT ->", status)
print(json.dumps(data)[:600])
if status not in (200, 201):
    sys.exit(1)
print("commitOid:", data.get("commitOid"))
