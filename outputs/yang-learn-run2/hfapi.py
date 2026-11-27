#!/usr/bin/env python3
"""Tiny HF Jobs API helper (surrogate auth; never prints token)."""
import json, sys, urllib.request
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

def call(method, path, body=None):
    req = urllib.request.Request(
        "https://huggingface.co" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    add_surrogate_to_request(req, "custom.huggingface",
                             allowed_hosts=["huggingface.co"])
    with urllib.request.build_opener().open(req, timeout=120) as resp:
        return read_json_response(resp)

if __name__ == "__main__":
    out = call(sys.argv[1], sys.argv[2])
    print(json.dumps(out, indent=1))
