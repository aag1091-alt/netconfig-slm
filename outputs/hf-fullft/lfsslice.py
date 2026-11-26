#!/usr/bin/env python3
"""Submit LFS part-upload slice jobs. Each job PUTs a slice of parts from
the bucket-mounted source file and writes etags back to the bucket.
Usage: lfsslice.py <batch.json> <bucket_src_path> <tag> [slice_size] [only_slice]"""
import base64, importlib.util, json, sys

spec = importlib.util.spec_from_file_location(
    "hf", "/home/hatch/workspace/custom-connectors/custom.huggingface/bin/hf.py")
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)

WORKER = r'''
import base64, json, os, sys, time, urllib.request
plan = json.loads(base64.b64decode(open("/tmp/plan.b64").read()))
local, chunk, outpath = sys.argv[1], int(sys.argv[2]), sys.argv[3]
res = {}
with open(local, "rb") as fh:
    for num, url in plan:
        fh.seek((num - 1) * chunk)
        data = fh.read(chunk)
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, data=data, method="PUT")
                with urllib.request.urlopen(req, timeout=600) as resp:
                    res[str(num)] = resp.headers.get("ETag")
                break
            except Exception as e:
                print("part", num, "attempt", attempt, "err", repr(e), flush=True)
                time.sleep(5)
        else:
            raise SystemExit(f"part {num} failed")
        print("done part", num, flush=True)
os.makedirs(os.path.dirname(outpath), exist_ok=True)
json.dump(res, open(outpath, "w"))
print("SLICE DONE", len(res), flush=True)
'''

batch_file, src, tag = sys.argv[1], sys.argv[2], sys.argv[3]
slice_size = int(sys.argv[4]) if len(sys.argv) > 4 else 24
only = int(sys.argv[5]) if len(sys.argv) > 5 else None

batch = json.load(open(batch_file))
header = batch["objects"][0]["actions"]["upload"]["header"]
chunk = int(header["chunk_size"])
parts = sorted((int(k), v) for k, v in header.items() if k.isdigit())
slices = [parts[i:i + slice_size] for i in range(0, len(parts), slice_size)]
print(f"{tag}: {len(parts)} parts, {len(slices)} slices of {slice_size}")

w64 = base64.b64encode(WORKER.encode()).decode()
job_ids = []
for idx, sl in enumerate(slices):
    if only is not None and idx != only:
        continue
    plan64 = base64.b64encode(json.dumps(sl).encode()).decode()
    out = f"/output/run3-fullft/lfs-parts/{tag}-{idx}.json"
    cmd = (f"echo {w64} | base64 -d > /tmp/w.py && "
           f"echo {plan64} > /tmp/plan.b64 && "
           f"python3 /tmp/w.py {src} {chunk} {out}")
    payload = {
        "command": ["bash", "-c", cmd],
        "arguments": [],
        "environment": {},
        "flavor": "cpu-basic",
        "timeoutSeconds": 1800,
        "dockerImage": "python:3.12-slim",
        "volumes": [{"type": "bucket",
                     "source": "aag1091/netconfig-train-output",
                     "mountPath": "/output"}],
    }
    status, data = hf.call("POST", "/api/jobs/aag1091", payload)
    jid = data.get("id")
    print(f"slice {idx}: HTTP {status} job {jid}")
    if status not in (200, 201):
        print(json.dumps(data)[:500])
        sys.exit(1)
    job_ids.append(jid)
print("submitted:", job_ids)
