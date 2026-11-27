#!/usr/bin/env python3
"""Publish the yang-learn run-2 model to its existing public HF
repo (replacing run-1 weights + GGUF) via huggingface_hub,
authenticated with the Secure Vault surrogate (never printed).
Mirrors outputs/yang-learn-run1/hfup_yang.py.
Usage: hfup_yang_run2.py folder|gguf"""
import os, sys

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry

entry = dynamic_credential_entry("custom.huggingface")
surr = str(entry["surrogate"]).strip()
assert surr.startswith("hsurr:"), "unexpected surrogate shape"

from huggingface_hub import HfApi

api = HfApi(token=surr)
BASE = os.path.expanduser(
    "~/workspace/projects/small-ai-model-for-networking/netconfig-slm/"
    "outputs/yang-learn-run2")
STAGE = os.path.join(BASE, "model-staging")
REPO = "aag1091/netconfig-yang-qwen3-4b"
mode = sys.argv[1] if len(sys.argv) > 1 else "folder"

if mode == "folder":
    url = api.create_repo(repo_id=REPO, repo_type="model",
                          exist_ok=True)
    print("repo ready:", url, flush=True)
    res = api.upload_folder(
        folder_path=STAGE,
        repo_id=REPO,
        repo_type="model",
        ignore_patterns=["*.gguf"],
        commit_message="Publish yang-learn run 2 (corpus v1+v2, "
                       "1,418 train records) - bench v1 77.4% "
                       "mechanical overall (run 1: 73.7%); PM 64.7%, "
                       "TE 97.6%, stopped cleanly 95.4%",
    )
    print("UPLOAD FOLDER DONE:", res, flush=True)
elif mode == "gguf":
    res = api.upload_file(
        path_or_fileobj=os.path.join(
            STAGE, "netconfig-yang-qwen3-4b-q4_k_m.gguf"),
        path_in_repo="netconfig-yang-qwen3-4b-q4_k_m.gguf",
        repo_id=REPO,
        repo_type="model",
        commit_message="Update Q4_K_M GGUF build (llama.cpp) to "
                       "yang-learn run 2 weights",
    )
    print("UPLOAD GGUF DONE:", res, flush=True)
