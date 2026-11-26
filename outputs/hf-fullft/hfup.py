#!/usr/bin/env python3
"""Upload staged model folder to the public repo via huggingface_hub,
authenticated with the Secure Vault surrogate (never printed).
Usage: hfup.py folder|gguf|readme"""
import os, sys

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry

entry = dynamic_credential_entry("custom.huggingface")
surr = str(entry["surrogate"]).strip()
assert surr.startswith("hsurr:"), "unexpected surrogate shape"

pass  # xet path enabled (default)

from huggingface_hub import HfApi

api = HfApi(token=surr)
BASE = os.path.expanduser(
    "~/workspace/projects/small-ai-model-for-networking/netconfig-slm/"
    "outputs/hf-fullft")
STAGE = os.path.join(BASE, "stage-model")
REPO = "aag1091/netconfig-slm-qwen3-4b"
mode = sys.argv[1] if len(sys.argv) > 1 else "folder"

if mode == "folder":
    res = api.upload_folder(
        folder_path=STAGE,
        repo_id=REPO,
        repo_type="model",
        ignore_patterns=["*.gguf"],
        commit_message="Publish NetConfig SLM run 3 (full fine-tune) - "
                       "publish bar cleared: Batfish 98.8%, intent 92.5% "
                       "triaged, parse 100%",
    )
    print("UPLOAD FOLDER DONE:", res)
elif mode == "gguf":
    res = api.upload_file(
        path_or_fileobj=os.path.join(
            STAGE, "netconfig-slm-qwen3-4b-q4_k_m.gguf"),
        path_in_repo="netconfig-slm-qwen3-4b-q4_k_m.gguf",
        repo_id=REPO,
        repo_type="model",
        commit_message="Add Q4_K_M GGUF build (llama.cpp) for local/Ollama use",
    )
    print("UPLOAD GGUF DONE:", res)
elif mode == "readme":
    res = api.upload_file(
        path_or_fileobj=os.path.join(BASE, "README-model-card.md"),
        path_in_repo="README.md",
        repo_id=REPO,
        repo_type="model",
        commit_message="Add model card with training + evaluation details",
    )
    print("UPLOAD README DONE:", res)
