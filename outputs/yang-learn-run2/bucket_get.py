#!/usr/bin/env python3
"""Download files from the private training bucket via resolve
URLs, STREAMING to disk in 8 MiB chunks (whole-file buffering
OOM-killed on the 4.97 GB shard, 2026-11-27).
Usage: bucket_get.py REMOTE_PATH LOCAL_PATH [REMOTE2 LOCAL2 ...]"""
import sys, urllib.request
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request

pairs = list(zip(sys.argv[1::2], sys.argv[2::2]))
for remote, local in pairs:
    url = ("https://huggingface.co/buckets/aag1091/"
           f"netconfig-train-output/resolve/{remote}")
    req = urllib.request.Request(url, method="GET")
    add_surrogate_to_request(req, "custom.huggingface",
                             allowed_hosts=["huggingface.co"])
    n = 0
    with urllib.request.build_opener().open(req, timeout=300) as resp, \
            open(local, "wb") as fh:
        while True:
            chunk = resp.read(8 * 1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            n += len(chunk)
    print(f"{remote} -> {local} ({n} bytes)", flush=True)
