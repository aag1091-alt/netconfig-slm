#!/usr/bin/env python3
"""HF Jobs: batched DEV evaluation of the published yang-learn
run-1 model (aag1091/netconfig-yang-qwen3-4b).

Authorized by Codex board #88 under Avinash's standing under-$15
approval (2026-11-27): explicit timeout, raw outputs, and separate
EOS/length counts. DEV SPLIT ONLY (78 records, frozen corpus v1
commit 5189d33) — the benchmark is not touched by this job.

Generation is BATCHED (batch 16) via the same core routine as
eval_batched.py — run 1's sequential generation burned ~50 min of
its job timeout on 217 rows; this job exists in part to measure
the batched replacement on the dev split before run 2 relies on
it. Greedy decoding, ChatML prompts identical to
job_train_yanglearn_v1.prompt_of, max_new_tokens 768, eos
[151645, 151643].

Outputs go to /output/yang-learn-dev-eval (mounted HF bucket
aag1091/netconfig-train-output): RUN_INFO.json,
generations_dev.jsonl (score_bench.py row shape + stopped_on_eos
+ output_tokens per record), dev_eval_stats.json (per-record EOS /
length stats + aggregates + durations), DONE.json.

Cost discipline: flavor a10g-small ($1.00/hr), timeoutSeconds=1800
set at submission -> maximum possible charge $0.50; expected
~$0.15-0.35 (pip install + 8 GB model load + 78 batched rows).
"""
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

OUT = Path("/output/yang-learn-dev-eval")
COMMIT = "5189d33ccc4211a310338133661be452f65b643f"
RAW = f"https://raw.githubusercontent.com/aag1091-alt/netconfig-slm/{COMMIT}"
DEV_PATH = "yang-learn/corpus/dev.jsonl"
DEV_SHA = "f2a27fd21f590803e40145318170094359a2316e8dd43c99ba213c9610adf37d"
DEV_ROWS = 78
MODEL = "aag1091/netconfig-yang-qwen3-4b"
IM_END_ID = 151645
ENDOFTEXT_ID = 151643
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 768
BATCH_SIZE = 16
VERSION_PKGS = ["transformers", "torch", "accelerate"]


def log(*a):
    print("[yangdev-eval]", *a, flush=True)


def versions():
    out = {}
    for pkg in VERSION_PKGS:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except Exception as e:  # noqa: BLE001
            out[pkg] = f"ERROR: {e}"
    return out


def pip_freeze():
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout.strip().splitlines()
    except Exception as e:  # noqa: BLE001
        return [f"ERROR: {e}"]


def prompt_of(text):
    return f"<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n"


def batched_generate(model, tok, rows, batch_size=BATCH_SIZE,
                     max_new_tokens=MAX_NEW_TOKENS):
    """Same core routine as eval_batched.py (kept inline so the
    job is self-contained, run-1 plumbing style)."""
    import torch
    model.eval()
    try:
        dev = model.device
    except Exception:  # noqa: BLE001
        dev = next(model.parameters()).device
    tok.padding_side = "left"
    if tok.pad_token_id is None:
        tok.pad_token_id = ENDOFTEXT_ID
    enc = [tok(prompt_of(r["prompt"]), add_special_tokens=False)
           ["input_ids"] for r in rows]
    order = sorted(range(len(rows)), key=lambda i: -len(enc[i]))
    results: list = [None] * len(rows)
    for start in range(0, len(order), batch_size):
        idxs = order[start:start + batch_size]
        width = max(len(enc[i]) for i in idxs)
        input_ids = torch.full((len(idxs), width), ENDOFTEXT_ID,
                               dtype=torch.long)
        attn = torch.zeros((len(idxs), width), dtype=torch.long)
        for j, i in enumerate(idxs):
            ids = enc[i]
            input_ids[j, width - len(ids):] = torch.tensor(ids)
            attn[j, width - len(ids):] = 1
        with torch.no_grad():
            out = model.generate(
                input_ids=input_ids.to(dev),
                attention_mask=attn.to(dev),
                max_new_tokens=max_new_tokens, do_sample=False,
                eos_token_id=EOS_IDS, pad_token_id=ENDOFTEXT_ID)
        seqs = out.tolist()
        for j, i in enumerate(idxs):
            gen_ids = seqs[j][width:]
            eos_pos = next((k for k, t in enumerate(gen_ids)
                            if t in EOS_IDS), None)
            if eos_pos is not None:
                out_tokens = len([t for t in gen_ids[:eos_pos + 1]
                                  if t != ENDOFTEXT_ID])
            else:
                out_tokens = len([t for t in gen_ids
                                  if t != ENDOFTEXT_ID])
            raw = tok.decode(gen_ids, skip_special_tokens=False)
            gen = tok.decode(gen_ids,
                             skip_special_tokens=True).strip()
            stopped_on_eos = eos_pos is not None
            results[i] = {
                "id": rows[i]["id"], "family": rows[i]["family"],
                "set": "dev", "generated": gen,
                "stopped_cleanly": stopped_on_eos and
                    "<|im_start|>" not in raw,
                "stopped_on_eos": stopped_on_eos,
                "output_tokens": out_tokens}
        log(f"batch {start // batch_size + 1}: "
            f"{min(start + batch_size, len(rows))}/{len(rows)} rows")
    return results


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    vers = versions()
    run_info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "model": MODEL, "corpus_commit": COMMIT,
                "script": "job_eval_dev_batched.py",
                "batch_size": BATCH_SIZE,
                "max_new_tokens": MAX_NEW_TOKENS,
                "eos_ids": EOS_IDS, "versions": vers,
                "pip_freeze": pip_freeze(),
                "run": "batched dev-split evaluation of the "
                       "published run-1 model; Codex board #88 "
                       "under Avinash's standing under-$15 approval; "
                       "a10g-small, timeoutSeconds=1800 ($0.50 cap)"}
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("RUN_INFO written; versions:", vers)

    dest = Path("/tmp/dev.jsonl")
    urllib.request.urlretrieve(f"{RAW}/{DEV_PATH}", dest)
    raw = dest.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    rows = [json.loads(l) for l in raw.decode().splitlines()
            if l.strip()]
    run_info["dev_file"] = {"path": DEV_PATH, "sha256": sha,
                            "sha_ok": sha == DEV_SHA,
                            "rows": len(rows),
                            "rows_ok": len(rows) == DEV_ROWS}
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("dev fetch check:", run_info["dev_file"])
    if not (sha == DEV_SHA and len(rows) == DEV_ROWS):
        (OUT / "DONE.json").write_text(json.dumps(
            {"status": "aborted_dataset_guard",
             "dev_file": run_info["dev_file"]}, indent=2))
        log("ABORTED: dataset guard tripped; no generation done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    t_load = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, device_map="auto")
    load_s = round(time.time() - t_load, 1)
    log(f"model loaded in {load_s}s")

    t_gen = time.time()
    gens = batched_generate(model, tok, rows)
    gen_s = round(time.time() - t_gen, 1)
    (OUT / "generations_dev.jsonl").write_text(
        "\n".join(json.dumps(g) for g in gens) + "\n")
    toks = sorted(g["output_tokens"] for g in gens)
    n = len(toks)
    stats = {
        "n": n,
        "stopped_on_eos": sum(g["stopped_on_eos"] for g in gens),
        "stopped_cleanly": sum(g["stopped_cleanly"] for g in gens),
        "output_tokens": {
            "mean": round(sum(toks) / n, 1),
            "p50": toks[n // 2], "p95": toks[int(n * 0.95)],
            "max": toks[-1]},
        "durations": {"load_s": load_s, "gen_s": gen_s,
                      "total_s": round(time.time() - t0, 1)},
        "batch_size": BATCH_SIZE, "max_new_tokens": MAX_NEW_TOKENS,
        "per_record": [{"id": g["id"],
                        "stopped_on_eos": g["stopped_on_eos"],
                        "stopped_cleanly": g["stopped_cleanly"],
                        "output_tokens": g["output_tokens"]}
                       for g in gens]}
    (OUT / "dev_eval_stats.json").write_text(
        json.dumps(stats, indent=1) + "\n")
    done = {"status": "completed",
            "elapsed_s": round(time.time() - t0, 1),
            "model": MODEL, "rows": n,
            "stopped_on_eos": stats["stopped_on_eos"],
            "stopped_cleanly": stats["stopped_cleanly"],
            "gen_s": gen_s}
    (OUT / "DONE.json").write_text(json.dumps(done, indent=2))
    log("ALL DONE:", done)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "DONE.json").write_text(json.dumps(
                {"status": "error",
                 "error": traceback.format_exc()}, indent=2))
        except Exception:  # noqa: BLE001
            pass
        raise
