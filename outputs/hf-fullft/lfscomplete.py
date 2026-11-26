#!/usr/bin/env python3
"""Collect slice etags from the bucket, verify coverage, complete multipart."""
import importlib.util, json, os, sys, urllib.request, urllib.error

spec = importlib.util.spec_from_file_location(
    "hf", "/home/hatch/workspace/custom-connectors/custom.huggingface/bin/hf.py")
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request

BASE = os.path.dirname(os.path.abspath(__file__))
FILES = [
    ("shard1", "lfs-shard1.json", 311,
     "db9ea65212779183828dcb304dbc9b8046e61310ec54307c64b4571dfa4758d1"),
    ("shard2", "lfs-shard2.json", 193,
     "6350d4630d88795decfe2aa3bf958198d57af370b29e9261ed07cb77ede2db8f"),
    ("gguf", "lfs-gguf.json", 157,
     "f8c96f3e551fb0ab1e2d84e25ca4a7745c694a8e11e1bc61125f69c5fbde142e"),
]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

def fetch_bucket_json(path):
    url = ("https://huggingface.co/buckets/aag1091/netconfig-train-output"
           f"/resolve/{path}")
    req = urllib.request.Request(url, method="GET")
    add_surrogate_to_request(req, "custom.huggingface",
                             allowed_hosts=["huggingface.co"])
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            with urllib.request.urlopen(e.headers["Location"],
                                        timeout=60) as resp:
                return json.loads(resp.read().decode())
        raise

for tag, batch_name, nparts, oid in FILES:
    etags = {}
    nslices = (nparts + 23) // 24
    for idx in range(nslices):
        part = fetch_bucket_json(f"run3-fullft/lfs-parts/{tag}-{idx}.json")
        for k, v in part.items():
            etags[int(k)] = v
    missing = [n for n in range(1, nparts + 1) if n not in etags]
    print(f"{tag}: collected {len(etags)}/{nparts} etags, missing={missing}")
    if missing:
        sys.exit(1)
    batch = json.load(open(os.path.join(BASE, batch_name)))
    href = batch["objects"][0]["actions"]["upload"]["href"]
    payload = json.dumps({
        "oid": oid,
        "parts": [{"partNumber": n, "etag": etags[n]}
                  for n in range(1, nparts + 1)],
    }).encode()
    req = urllib.request.Request(href, data=payload, method="POST")
    req.add_header("Content-Type", "application/vnd.git-lfs+json")
    req.add_header("Accept", "application/vnd.git-lfs+json")
    add_surrogate_to_request(req, "custom.huggingface",
                             allowed_hosts=["huggingface.co"])
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            print(f"{tag}: COMPLETE -> {resp.status} "
                  f"{resp.read().decode()[:200]}")
    except urllib.error.HTTPError as e:
        print(f"{tag}: COMPLETE ERROR {e.code} {e.read().decode()[:400]}")
        sys.exit(1)
print("ALL MULTIPART UPLOADS COMPLETED")
