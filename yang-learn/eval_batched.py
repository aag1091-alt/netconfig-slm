#!/usr/bin/env python3
"""Batched greedy generation for yang-learn evaluation.

Replaces run 1's sequential in-job generation (217 bench rows took
~50 min; non-stopping rows burned the full 768-token budget one at
a time). Batches prompts (default 16), left-pads, and generates
greedily with the same ChatML prompt construction and EOS pair as
job_train_yanglearn_v1.py, so generations are directly comparable
to run 1's.

Output rows are exactly the shape score_bench.py consumes —
{"id", "family", "set", "generated", "stopped_cleanly"} — plus
two per-record stats fields Codex asked for: "stopped_on_eos"
(bool: an EOS id appears in the generated region) and
"output_tokens" (generated tokens up to and including the first
EOS, or all non-pad generated tokens when no EOS occurs). A
companion stats JSON (--stats-out) carries the same per-record
stats plus aggregates (EOS-stop rate, clean-stop rate, output
token mean/p50/p95/max).

Usage:
  eval_batched.py --model MODEL --out generations.jsonl \
      [--stats-out stats.json] [--batch-size 16] \
      [--max-new-tokens 768] FILE.jsonl [FILE2.jsonl ...]

Each input file's stem becomes the rows' "set" tag (dev,
benchmark, held_out, ...). Records are sorted by prompt length
within the whole run so the longest prompts batch together;
output rows are written in input order per file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

IM_END_ID = 151645          # <|im_end|>
ENDOFTEXT_ID = 151643       # <|endoftext|>
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]


def prompt_of(text):
    """Identical to job_train_yanglearn_v1.prompt_of."""
    return f"<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n"


def batched_generate(model, tok, rows, batch_size=16,
                     max_new_tokens=768):
    """rows: corpus records with id/family/prompt. Returns a list
    of generation dicts in the SAME order as `rows`."""
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
    results: list[dict | None] = [None] * len(rows)
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
            # trim trailing pads; locate first EOS
            eos_pos = next((k for k, t in enumerate(gen_ids)
                            if t in EOS_IDS), None)
            nonpad = [t for t in gen_ids if t != ENDOFTEXT_ID]
            if eos_pos is not None:
                out_tokens = len([t for t in gen_ids[:eos_pos + 1]
                                  if t != ENDOFTEXT_ID])
            else:
                out_tokens = len(nonpad)
            raw = tok.decode(gen_ids, skip_special_tokens=False)
            gen = tok.decode(gen_ids,
                             skip_special_tokens=True).strip()
            stopped_on_eos = eos_pos is not None
            stopped_cleanly = stopped_on_eos and \
                "<|im_start|>" not in raw
            results[i] = {
                "id": rows[i]["id"], "family": rows[i]["family"],
                "generated": gen,
                "stopped_cleanly": stopped_cleanly,
                "stopped_on_eos": stopped_on_eos,
                "output_tokens": out_tokens}
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True,
                    help="HF repo id or local path")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stats-out")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-new-tokens", type=int, default=768)
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, torch_dtype=torch.bfloat16, device_map="auto")

    all_rows, stats_rows = [], []
    t0 = time.time()
    for fname in args.files:
        path = Path(fname)
        tag = path.stem
        rows = [json.loads(l) for l in
                path.read_text().splitlines() if l.strip()]
        gens = batched_generate(model, tok, rows,
                                batch_size=args.batch_size,
                                max_new_tokens=args.max_new_tokens)
        for g in gens:
            all_rows.append({
                "id": g["id"], "family": g["family"], "set": tag,
                "generated": g["generated"],
                "stopped_cleanly": g["stopped_cleanly"],
                "stopped_on_eos": g["stopped_on_eos"],
                "output_tokens": g["output_tokens"]})
            stats_rows.append({
                "id": g["id"], "set": tag,
                "stopped_on_eos": g["stopped_on_eos"],
                "stopped_cleanly": g["stopped_cleanly"],
                "output_tokens": g["output_tokens"]})
        n_stop = sum(g["stopped_cleanly"] for g in gens)
        print(f"{tag}: {len(gens)} rows, stopped_cleanly "
              f"{n_stop}/{len(gens)}", flush=True)
    Path(args.out).write_text(
        "\n".join(json.dumps(r) for r in all_rows) + "\n")
    if args.stats_out:
        toks = sorted(r["output_tokens"] for r in stats_rows)
        n = len(toks)
        stats = {
            "n": n,
            "stopped_on_eos": sum(r["stopped_on_eos"]
                                  for r in stats_rows),
            "stopped_cleanly": sum(r["stopped_cleanly"]
                                   for r in stats_rows),
            "output_tokens": {
                "mean": round(sum(toks) / n, 1) if n else None,
                "p50": toks[n // 2] if n else None,
                "p95": toks[min(n - 1, int(n * 0.95))] if n else None,
                "max": toks[-1] if n else None},
            "elapsed_s": round(time.time() - t0, 1),
            "batch_size": args.batch_size,
            "max_new_tokens": args.max_new_tokens,
            "per_record": stats_rows}
        Path(args.stats_out).write_text(json.dumps(stats, indent=1)
                                        + "\n")
        print("stats:", json.dumps(
            {k: v for k, v in stats.items() if k != "per_record"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
