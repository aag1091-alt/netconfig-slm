#!/usr/bin/env python3
"""HF Jobs: batched evaluation of the yang-learn RUN-2 model.

Loads the run-2 model from the training bucket
(/output/yang-learn-run2/model, written by
job_train_yanglearn_v2.py) and runs BATCHED greedy generation
(batch 16, eval_batched.py core) over:
  * dev combined: corpus/dev.jsonl (78) + corpus/
    dev_v2_additions.jsonl (63) = 141 records
  * bench v1: corpus/benchmark.jsonl (209) + held_out.jsonl (8)
    = 217 records (frozen; evaluated, never trained on)
Corpus files are fetched from netconfig-slm commit
f3a9b1df0e0092fa54ba6caa3fd2e1dfc463f820 and sha256-guarded per
file (abort before generation on any mismatch).

Generation settings identical to run 1: ChatML prompts
(job_train prompt_of), greedy, max_new_tokens 768, eos
[151645, 151643]. Outputs to /output/yang-learn-run2:
RUN_INFO.json, generations_benchmark.jsonl (217),
generations_dev.jsonl (141), eval_stats.json (per-record EOS /
length stats + aggregates + durations), DONE.json. Generation
rows carry score_bench.py's shape plus stopped_on_eos +
output_tokens, as in the run-1 dev eval.

Cost discipline: flavor a100-large ($2.50/hr),
timeoutSeconds=1800 set at submission -> maximum charge $1.25;
expected ~$0.55-0.90 (RUN2_PLAN.md §3). Under Avinash's standing
approval (program total strictly under $15).
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

OUT = Path("/output/yang-learn-run2")
MODEL_DIR = "/output/yang-learn-run2/model"
COMMIT = "f3a9b1df0e0092fa54ba6caa3fd2e1dfc463f820"
RAW = f"https://raw.githubusercontent.com/aag1091-alt/netconfig-slm/{COMMIT}"
FILES = {
    "dev": ("yang-learn/corpus/dev.jsonl",
            "f2a27fd21f590803e40145318170094359a2316e8dd43c99ba213c9610adf37d",
            78),
    "dev_v2_additions": (
        "yang-learn/corpus/dev_v2_additions.jsonl",
        "a13333720fea97c9c92db83f54939dcf0650fbd85215945a3757212ab38ab76c",
        63),
    "benchmark": ("yang-learn/corpus/benchmark.jsonl",
                  "e8c440553f706b53552a3c051ce3c52e0b7a1178e82d6a78a9b7ebfe2be33b3b",
                  209),
    "held_out": ("yang-learn/held_out.jsonl",
                 "32bc824d2e8d1ea829372b33c68a43bd5c5ab85ca58c79044f870380129c72f6",
                 8),
}
IM_END_ID = 151645
ENDOFTEXT_ID = 151643
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 768
BATCH_SIZE = 16
VERSION_PKGS = ["transformers", "torch", "accelerate"]


def log(*a):
    print("[yangrun2-eval]", *a, flush=True)


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
                "generated": gen,
                "stopped_cleanly": stopped_on_eos and
                    "<|im_start|>" not in raw,
                "stopped_on_eos": stopped_on_eos,
                "output_tokens": out_tokens}
        log(f"batch {start // batch_size + 1}: "
            f"{min(start + batch_size, len(rows))}/{len(rows)} rows")
    return results


def group_stats(gens):
    toks = sorted(g["output_tokens"] for g in gens)
    n = len(toks)
    return {"n": n,
            "stopped_on_eos": sum(g["stopped_on_eos"] for g in gens),
            "stopped_cleanly": sum(g["stopped_cleanly"]
                                   for g in gens),
            "output_tokens": {
                "mean": round(sum(toks) / n, 1),
                "p50": toks[n // 2], "p95": toks[int(n * 0.95)],
                "max": toks[-1]}}


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    vers = versions()
    run_info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "model": MODEL_DIR, "corpus_commit": COMMIT,
                "script": "job_eval_run2_batched.py",
                "batch_size": BATCH_SIZE,
                "max_new_tokens": MAX_NEW_TOKENS,
                "eos_ids": EOS_IDS, "versions": vers,
                "pip_freeze": pip_freeze(),
                "run": "batched evaluation of the yang-learn run-2 "
                       "model (dev 141 + bench v1 217); a100-large, "
                       "timeoutSeconds=1800 ($1.25 cap); Avinash "
                       "standing approval, program total < $15"}
    (OUT / "EVAL_RUN_INFO.json").write_text(
        json.dumps(run_info, indent=2))
    log("EVAL_RUN_INFO written; versions:", vers)

    work = Path("/tmp/work")
    work.mkdir(exist_ok=True)
    data, checks = {}, []
    for tag, (path, want_sha, want_rows) in FILES.items():
        dest = work / f"{tag}.jsonl"
        urllib.request.urlretrieve(f"{RAW}/{path}", dest)
        raw = dest.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        rows = [json.loads(l) for l in raw.decode().splitlines()
                if l.strip()]
        data[tag] = rows
        checks.append({"tag": tag, "path": path, "sha256": sha,
                       "sha_ok": sha == want_sha, "rows": len(rows),
                       "rows_ok": len(rows) == want_rows})
    run_info["files"] = checks
    (OUT / "EVAL_RUN_INFO.json").write_text(
        json.dumps(run_info, indent=2))
    log("fetch checks:", json.dumps(checks))
    if not all(c["sha_ok"] and c["rows_ok"] for c in checks):
        (OUT / "EVAL_DONE.json").write_text(json.dumps(
            {"status": "aborted_dataset_guard", "checks": checks},
            indent=2))
        log("ABORTED: dataset guard tripped; no generation done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    t_load = time.time()
    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_DIR, torch_dtype=torch.bfloat16, device_map="auto")
    load_s = round(time.time() - t_load, 1)
    log(f"model loaded in {load_s}s")

    groups = {"dev": data["dev"] + data["dev_v2_additions"],
              "benchmark": data["benchmark"] + data["held_out"]}
    all_stats = {"durations": {"load_s": load_s},
                 "batch_size": BATCH_SIZE,
                 "max_new_tokens": MAX_NEW_TOKENS, "per_record": []}
    done_groups = {}
    for tag, rows in groups.items():
        t_gen = time.time()
        gens = batched_generate(model, tok, rows)
        gen_s = round(time.time() - t_gen, 1)
        for g in gens:
            g["set"] = tag
        (OUT / f"generations_{tag}.jsonl").write_text(
            "\n".join(json.dumps(g) for g in gens) + "\n")
        st = group_stats(gens)
        st["gen_s"] = gen_s
        all_stats[tag] = st
        all_stats["per_record"] += [
            {"id": g["id"], "set": tag,
             "stopped_on_eos": g["stopped_on_eos"],
             "stopped_cleanly": g["stopped_cleanly"],
             "output_tokens": g["output_tokens"]} for g in gens]
        done_groups[tag] = {"rows": len(gens),
                            "stopped_cleanly": st["stopped_cleanly"],
                            "gen_s": gen_s}
        log(f"{tag} done:", done_groups[tag])
    all_stats["durations"]["total_s"] = round(time.time() - t0, 1)
    (OUT / "eval_stats.json").write_text(
        json.dumps(all_stats, indent=1) + "\n")
    done = {"status": "completed",
            "elapsed_s": round(time.time() - t0, 1),
            "model": MODEL_DIR, "groups": done_groups}
    (OUT / "EVAL_DONE.json").write_text(json.dumps(done, indent=2))
    log("ALL DONE:", done)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "EVAL_DONE.json").write_text(json.dumps(
                {"status": "error",
                 "error": traceback.format_exc()}, indent=2))
        except Exception:  # noqa: BLE001
            pass
        raise
