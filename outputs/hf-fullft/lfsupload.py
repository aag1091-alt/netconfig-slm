#!/usr/bin/env python3
"""Multipart LFS upload via curl (part PUTs) + urllib (completion).
Usage: lfsupload.py <batch.json> <localfile> <oid> <progress.json>
Resumable: parts recorded in progress.json are skipped."""
import json, os, subprocess, sys, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request

batch_file, local, oid, prog_file = sys.argv[1:5]
batch = json.load(open(batch_file))
obj = batch["objects"][0]
upload = obj["actions"]["upload"]
header = upload["header"]
chunk = int(header["chunk_size"])
urls = [u for k, u in sorted(
    ((int(k), v) for k, v in header.items() if k.isdigit()))]
size = os.path.getsize(local)
nparts = (size + chunk - 1) // chunk
assert len(urls) == nparts, f"{len(urls)} urls vs {nparts} parts"
print(f"{local}: {size} bytes, {nparts} parts x {chunk}", flush=True)

done = {}
if os.path.exists(prog_file):
    done = {int(k): v for k, v in json.load(open(prog_file)).items()}
print(f"already done: {len(done)}", flush=True)

def put_part(i):
    url = urls[i]
    cmd = (f"dd if={local} bs={chunk} skip={i} count=1 status=none | "
           f"curl -s -X PUT --data-binary @- -D - -o /dev/null "
           f"-w '%{{http_code}}' '{url}'")
    for attempt in range(4):
        r = subprocess.run(["bash", "-c", cmd], capture_output=True,
                           text=True, timeout=1200)
        out = r.stdout
        code = out.strip().splitlines()[-1] if out.strip() else ""
        etag = None
        for line in out.splitlines():
            if line.lower().startswith("etag:"):
                etag = line.split(":", 1)[1].strip()
        if code == "200" and etag:
            return i, etag
        print(f"part {i+1} attempt {attempt+1}: code={code} etag={etag} "
              f"err={r.stderr[:200]}", flush=True)
    raise RuntimeError(f"part {i+1} failed after retries")

todo = [i for i in range(nparts) if (i + 1) not in done]
print(f"uploading {len(todo)} parts with 4 workers...", flush=True)
with ThreadPoolExecutor(max_workers=4) as ex:
    for i, etag in ex.map(put_part, todo):
        done[i + 1] = etag
        if len(done) % 20 == 0 or len(done) == nparts:
            json.dump(done, open(prog_file, "w"))
            print(f"progress: {len(done)}/{nparts}", flush=True)
json.dump(done, open(prog_file, "w"))
print("all parts uploaded", flush=True)

parts = [{"partNumber": n, "etag": done[n]} for n in range(1, nparts + 1)]
payload = json.dumps({"oid": oid, "parts": parts}).encode()
req = urllib.request.Request(upload["href"], data=payload, method="POST")
req.add_header("Content-Type", "application/vnd.git-lfs+json")
req.add_header("Accept", "application/vnd.git-lfs+json")
add_surrogate_to_request(req, "custom.huggingface",
                         allowed_hosts=["huggingface.co"])
try:
    with urllib.request.urlopen(req, timeout=300) as resp:
        print("COMPLETE ->", resp.status, resp.read().decode()[:300],
              flush=True)
except urllib.error.HTTPError as e:
    print("COMPLETE ERROR", e.code, e.read().decode()[:500], flush=True)
    sys.exit(1)
print("LFS OBJECT DONE:", oid)
