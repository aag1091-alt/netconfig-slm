#!/usr/bin/env python3
"""Size-verified streaming download from the training bucket.
The plain streamer silently accepted truncated streams (the
proxy closes connections early without an error), producing a
short shard 2 and index.json on 2026-11-27. This version checks
every file's size against the expected bucket size and retries.
Usage: bucket_get2.py REMOTE LOCAL EXPECTED_SIZE [REMOTE2 ...]"""
import sys, time, urllib.request
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request

triples = list(zip(sys.argv[1::3], sys.argv[2::3], sys.argv[3::3]))
failed = []
for remote, local, expected in triples:
    expected = int(expected)
    ok = False
    for attempt in range(1, 5):
        try:
            url = ("https://huggingface.co/buckets/aag1091/"
                   f"netconfig-train-output/resolve/{remote}")
            req = urllib.request.Request(url, method="GET")
            add_surrogate_to_request(req, "custom.huggingface",
                                     allowed_hosts=["huggingface.co"])
            n = 0
            with urllib.request.build_opener().open(
                    req, timeout=300) as resp, \
                    open(local, "wb") as fh:
                while True:
                    chunk = resp.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    n += len(chunk)
            print(f"{remote}: attempt {attempt}: {n} bytes "
                  f"(expected {expected})", flush=True)
            if n == expected:
                ok = True
                break
        except Exception as e:  # noqa: BLE001 - retry transient drops
            print(f"{remote}: attempt {attempt} error: {e}",
                  flush=True)
        time.sleep(5)
    if not ok:
        failed.append(remote)
    else:
        print(f"VERIFIED {remote} -> {local} ({expected} bytes)",
              flush=True)
if failed:
    print("FAILED FILES:", failed, flush=True)
    sys.exit(1)
print("ALL VERIFIED", flush=True)
