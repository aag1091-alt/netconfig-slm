#!/usr/bin/env python3
"""LFS batch request for one object; saves the raw response JSON to a file.
Usage: lfsbatch.py <sha256> <size> <outfile>"""
import json, sys, urllib.request, urllib.error

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request

oid, size, outfile = sys.argv[1], int(sys.argv[2]), sys.argv[3]
url = ("https://huggingface.co/aag1091/netconfig-slm-qwen3-4b.git"
       "/info/lfs/objects/batch")
body = json.dumps({
    "operation": "upload",
    "transfers": ["basic", "multipart"],
    "objects": [{"oid": oid, "size": size}],
    "hash_algo": "sha256",
}).encode()
req = urllib.request.Request(url, data=body, method="POST")
req.add_header("Content-Type", "application/vnd.git-lfs+json")
req.add_header("Accept", "application/vnd.git-lfs+json")
add_surrogate_to_request(req, "custom.huggingface",
                         allowed_hosts=["huggingface.co"])
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
        print("HTTP", resp.status)
except urllib.error.HTTPError as e:
    print("HTTP ERROR", e.code, e.read().decode()[:1000])
    sys.exit(1)
with open(outfile, "w") as fh:
    json.dump(data, fh, indent=1)
obj = data["objects"][0]
print("error:", obj.get("error"))
actions = obj.get("actions", {})
for name, act in actions.items():
    hdr = act.get("header", {})
    print(f"action {name}: href={act.get('href','')[:80]}... "
          f"header keys={sorted(hdr.keys())[:10]}"
          f"{'...' if len(hdr) > 10 else ''} n_headers={len(hdr)}")
    if "chunk_size" in hdr:
        print("  chunk_size:", hdr["chunk_size"])
print("saved:", outfile)
