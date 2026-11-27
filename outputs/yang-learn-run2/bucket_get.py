#!/usr/bin/env python3
"""Download files from the private training bucket via resolve URLs.
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
    with urllib.request.build_opener().open(req, timeout=300) as resp:
        data = resp.read()
    open(local, "wb").write(data)
    print(f"{remote} -> {local} ({len(data)} bytes)")
