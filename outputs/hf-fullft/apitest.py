import os, sys, time
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry
surr = str(dynamic_credential_entry("custom.huggingface")["surrogate"]).strip()
from huggingface_hub import HfApi
api = HfApi(token=surr)
t=time.time(); print("whoami:", api.whoami().get("name"), f"{time.time()-t:.1f}s", flush=True)
t=time.time(); info = api.repo_info("aag1091/netconfig-slm-qwen3-4b", repo_type="model"); print("repo_info ok:", info.sha[:8], f"{time.time()-t:.1f}s", flush=True)
