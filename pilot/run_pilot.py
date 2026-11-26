#!/usr/bin/env python3
"""Pilot runner — NOT part of the freeze execution; run only after review.

Loads pilot/items.jsonl, renders prompts via pilot/prompts.py, and runs
both arms (A: no schema packet; B: with packet) on the pinned checkpoint
with greedy decoding. Writes pilot/results/raw_outputs.jsonl with one
record per (item, arm): {item_id, task, arm, prompt_sha256, raw_output}.

Intended runtime: an HF Job (GPU) that clones this repo at the freeze
commit and runs this script. --dry-run renders prompts and prints their
hashes without loading the model (used to smoke-test the freeze).
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prompts as P  # noqa: E402

CHECKPOINT = "Qwen/Qwen3-4B-Base"
REVISION = "906bfd4b4dc7f14ee4320094d8b41684abff8539"
MAX_NEW_TOKENS = {"R": 256, "E": 384, "P": 512}


def load_items():
    with open(os.path.join(HERE, "items.jsonl"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def prompt_sha(item, arm):
    text = json.dumps(P.build_messages(item, arm), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "results",
                                                  "raw_outputs.jsonl"))
    args = ap.parse_args()
    items = load_items()

    if args.dry_run:
        for item in items:
            for arm in ("A", "B"):
                print(item["id"], arm, prompt_sha(item, arm))
        print(f"{len(items)} items x 2 arms")
        return 0

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(CHECKPOINT, revision=REVISION)
    model = AutoModelForCausalLM.from_pretrained(
        CHECKPOINT, revision=REVISION, torch_dtype=torch.bfloat16,
        device_map="cuda")
    model.eval()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as out:
        for item in items:
            for arm in ("A", "B"):
                messages = P.build_messages(item, arm)
                text = tok.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True)
                inputs = tok(text, return_tensors="pt").to("cuda")
                with torch.no_grad():
                    gen = model.generate(
                        **inputs, do_sample=False,
                        max_new_tokens=MAX_NEW_TOKENS[item["task"]],
                        pad_token_id=tok.eos_token_id)
                raw = tok.decode(gen[0][inputs["input_ids"].shape[1]:],
                                 skip_special_tokens=True)
                out.write(json.dumps({
                    "item_id": item["id"], "task": item["task"],
                    "arm": arm, "prompt_sha256": prompt_sha(item, arm),
                    "checkpoint": CHECKPOINT, "revision": REVISION,
                    "raw_output": raw,
                }, sort_keys=True) + "\n")
                out.flush()
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
