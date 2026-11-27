#!/usr/bin/env python3
"""HF Jobs yang-learn run 1: FULL fine-tune of Qwen3-4B-Base on the
yang-learn corpus v1 train split (1,047 records), frozen at
netconfig-slm commit 5189d33ccc4211a310338133661be452f65b643f.
Approved by Avinash 2026-11-26 against a $2.50 HARD CAP, enforced
by the job's timeoutSeconds=3600 at a100-large rates ($2.50/hr);
expected $0.61-0.74 by the measured run-3 throughput (843 tok/s).

PLUMBING — run 3's verified pattern, unchanged where it matters:
  * TRL prompt/completion format, completion-only loss;
    tokenizer.eos_token = "<|im_end|>" passed as processing_class
    (load-bearing: TRL appends the processing tokenizer's eos;
    the override keeps exactly one trailing 151645).
  * Pre-training assertion (probe pattern): a real collated batch
    must have every row's last non-pad label == 151645. Recorded
    in RESULTS.json; training continues even if it fails, flagged
    loudly (evidence over aborts).
  * Full fine-tune, bf16, no quantization, no PEFT, embeddings
    TIED (standard for full FT; do NOT untie). lr 1e-5, cosine,
    warmup 0.05, epochs 2, bs 4 x accum 4 (effective 16),
    max_length 1536 (corpus max record is 834 tokens pinned-
    tokenizer: zero truncation), gradient checkpointing.
  * pip in the job command is a single invocation pinning
    torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 to match
    the pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime image, plus
    transformers>=4.51,<4.58 trl>=0.19,<0.26 accelerate>=1.4
    datasets>=3.2,<4.

DIFFERENCES FROM RUN 3 (data-driven, deliberate):
  * Records already carry prompt/completion text; the ChatML wrap
    is applied to the record's prompt, and the completion is the
    record's completion + <|im_end|>. Run 3's Cisco `end`-line
    terminator is NOT used: it was specific to device configs;
    these completions are JSON states, edit specs, and verdicts.
  * Dataset guard: files are fetched from the frozen GitHub
    commit and verified by sha256 (below) plus row counts —
    never silently train on a stale or different corpus.
  * Slim in-job eval = generation only: greedy generations on the
    frozen bench v1 (benchmark.jsonl 209 + held_out.jsonl 8) and
    the dev split (78), saved raw to the bucket. The authoritative
    mechanical scoring (yangson verdicts, apply-checks,
    exact-match, structured scorers per SCALE_DESIGN A2) runs
    back home from those generations — $0, no second job.

Outputs go to /output/yang-learn-run1 (mounted HF bucket
aag1091/netconfig-train-output): RUN_INFO.json, RESULTS.json,
DONE.json, generations_benchmark.jsonl, generations_dev.jsonl,
model/ (bf16 safetensors + tokenizer + generation_config.json
with eos [151645, 151643]). Nothing is published by this job.
"""
import gc
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

OUT = Path("/output/yang-learn-run1")
COMMIT = "5189d33ccc4211a310338133661be452f65b643f"
RAW = f"https://raw.githubusercontent.com/aag1091-alt/netconfig-slm/{COMMIT}"
FILES = {
    "train": ("yang-learn/corpus/train.jsonl",
              "ff9dd20595f65e739be12a3c5ad9233346c3ff567fcef80ee26379e7f4aeebc2",
              1047),
    "benchmark": ("yang-learn/corpus/benchmark.jsonl",
                  "e8c440553f706b53552a3c051ce3c52e0b7a1178e82d6a78a9b7ebfe2be33b3b",
                  209),
    "held_out": ("yang-learn/held_out.jsonl",
                 "32bc824d2e8d1ea829372b33c68a43bd5c5ab85ca58c79044f870380129c72f6",
                 8),
    "dev": ("yang-learn/corpus/dev.jsonl",
            "f2a27fd21f590803e40145318170094359a2316e8dd43c99ba213c9610adf37d",
            78),
}
BASE = "Qwen/Qwen3-4B-Base"
IM_END_ID = 151645          # <|im_end|> — ChatML turn end
ENDOFTEXT_ID = 151643       # <|endoftext|> — Qwen3-Base native eos
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 768
CFG = dict(lr=1e-5, warmup=0.05, weight_decay=0.0, max_grad_norm=1.0,
           epochs=2, bs=4, accum=4, max_len=1536,
           grad_ckpt=True, logging_steps=10)

VERSION_PKGS = ["trl", "transformers", "datasets", "torch",
                "accelerate"]


def log(*a):
    print("[yangft1]", *a, flush=True)


def versions():
    out = {}
    for pkg in VERSION_PKGS:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except Exception as e:  # noqa: BLE001 - record, don't die
            out[pkg] = f"ERROR: {e}"
    return out


def pip_freeze():
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout.strip().splitlines()
    except Exception as e:  # noqa: BLE001
        return [f"ERROR: {e}"]


def fetch(tag, dest):
    path, want_sha, want_rows = FILES[tag]
    urllib.request.urlretrieve(f"{RAW}/{path}", dest)
    raw = Path(dest).read_bytes()
    got_sha = hashlib.sha256(raw).hexdigest()
    rows = [json.loads(l) for l in raw.decode().splitlines()
            if l.strip()]
    return rows, {"tag": tag, "path": path, "sha256": got_sha,
                  "sha_ok": got_sha == want_sha, "rows": len(rows),
                  "rows_ok": len(rows) == want_rows}


def prompt_of(text):
    return f"<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n"


def completion_text(row):
    return f"{row['completion']}<|im_end|>"


def to_pc(row):
    return {"prompt": prompt_of(row["prompt"]),
            "completion": completion_text(row)}


def verify_endings(tok, rows, tag):
    """Every processed completion must tokenize to exactly one
    trailing 151645 and no other 151645 inside. Never raises."""
    bad = []
    for r in rows:
        ids = tok(completion_text(r),
                  add_special_tokens=False)["input_ids"]
        if not ids or ids[-1] != IM_END_ID or ids.count(IM_END_ID) != 1:
            bad.append({"id": r["id"], "tail": ids[-4:]})
    return {"tag": tag, "checked": len(rows),
            "all_ok": not bad, "bad": bad[:5]}


# --- probe assertion, verbatim pattern from run 3 -------------------
def assert_eos_labels(trainer, expect=IM_END_ID):
    """Take a real batch (real TRL processing + real collator, via
    the train dataloader) and check every row's last non-pad label
    == expect (151645). Never raises: failures are recorded."""
    result = {"passed": False, "expect": expect, "method": None,
              "n_rows": 0, "per_row_last_labels": [],
              "row0_last5_token_ids": None, "row0_last_label": None,
              "error": None}
    try:
        batch = None
        try:
            loader = trainer.get_train_dataloader()
            batch = next(iter(loader))
            result["method"] = "train_dataloader"
        except Exception as e:  # noqa: BLE001 - fall back to manual
            result["method"] = f"dataloader_failed ({e}); manual_collate"
            ds = trainer.train_dataset
            feats = [ds[i] for i in range(min(CFG["bs"], len(ds)))]
            batch = trainer.data_collator(feats)
        labels = batch["labels"]
        input_ids = batch["input_ids"]
        attn = batch.get("attention_mask")
        labels = labels.tolist() if hasattr(labels, "tolist") else labels
        input_ids = (input_ids.tolist() if hasattr(input_ids, "tolist")
                     else input_ids)
        if attn is not None and hasattr(attn, "tolist"):
            attn = attn.tolist()
        last_labels = []
        for i, row_labels in enumerate(labels):
            nonpad = [t for t in row_labels if t != -100]
            last_labels.append(nonpad[-1] if nonpad else None)
            if i == 0:
                if attn is not None:
                    ids0 = [t for t, m in zip(input_ids[0], attn[0])
                            if m == 1]
                else:
                    ids0 = [t for t in input_ids[0] if t != ENDOFTEXT_ID]
                result["row0_last5_token_ids"] = ids0[-5:]
                result["row0_last_label"] = last_labels[0]
        result["per_row_last_labels"] = last_labels
        result["n_rows"] = len(last_labels)
        result["passed"] = bool(last_labels) and all(
            t == expect for t in last_labels)
    except Exception:  # noqa: BLE001 - record, don't die
        result["error"] = traceback.format_exc()
    return result


def run_generations(model, tok, rows, tag, out_name):
    """Greedy generation, trained model only. Writes raw
    generations to OUT/out_name; returns the stopped_cleanly
    aggregate. Authoritative scoring runs back home."""
    import torch
    model.eval()
    try:
        dev = model.device
    except Exception:  # noqa: BLE001 - device property varies
        dev = next(model.parameters()).device
    gens = []
    per_family = {}
    n_stop = 0
    for row in rows:
        inputs = tok(prompt_of(row["prompt"]),
                     return_tensors="pt").to(dev)
        with torch.no_grad(), torch.autocast("cuda",
                                             dtype=torch.bfloat16):
            out = model.generate(**inputs,
                                 max_new_tokens=MAX_NEW_TOKENS,
                                 do_sample=False, eos_token_id=EOS_IDS,
                                 pad_token_id=ENDOFTEXT_ID)
        gen_ids = out[0][inputs["input_ids"].shape[1]:]
        raw = tok.decode(gen_ids, skip_special_tokens=False)
        gen = tok.decode(gen_ids, skip_special_tokens=True).strip()
        ids = gen_ids.tolist()
        stopped_via_eos = any(t in EOS_IDS for t in ids)
        stopped_cleanly = stopped_via_eos and "<|im_start|>" not in raw
        gens.append({"id": row["id"], "family": row["family"],
                     "set": tag, "generated": gen,
                     "stopped_cleanly": stopped_cleanly})
        fam = per_family.setdefault(row["family"], {"n": 0, "stop": 0})
        fam["n"] += 1
        fam["stop"] += int(stopped_cleanly)
        n_stop += int(stopped_cleanly)
    (OUT / out_name).write_text(
        "\n".join(json.dumps(g) for g in gens) + "\n")
    n = len(rows)
    overall = {"n": n, "stopped_cleanly": n_stop,
               "stopped_cleanly_pct": round(100 * n_stop / n, 1),
               "per_family": {f: {"n": s["n"], "stopped_cleanly": s["stop"],
                                  "stopped_cleanly_pct":
                                      round(100 * s["stop"] / s["n"], 1)}
                              for f, s in sorted(per_family.items())}}
    log(f"generations {tag}:", overall)
    return overall


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    vers = versions()
    run_info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "base": BASE, "cfg": CFG, "corpus_commit": COMMIT,
                "eos_ids": EOS_IDS,
                "script": "job_train_yanglearn_v1.py",
                "run": "yang-learn run 1: full fine-tune on corpus "
                       "v1 train (1,047 records); Avinash-approved "
                       "$2.50 hard cap via timeoutSeconds=3600, "
                       "2026-11-26",
                "versions": vers, "pip_freeze": pip_freeze()}
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("output mount OK, RUN_INFO written; versions:", vers)

    work = Path("/tmp/work")
    work.mkdir(exist_ok=True)
    data, checks = {}, []
    for tag in FILES:
        rows, chk = fetch(tag, work / f"{tag}.jsonl")
        data[tag] = rows
        checks.append(chk)
    run_info["files"] = checks
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("fetch checks:", json.dumps(checks))

    # Guard (run 3's discipline, sha256-pinned): never silently
    # train on a stale or different corpus.
    if not all(c["sha_ok"] and c["rows_ok"] for c in checks):
        (OUT / "DONE.json").write_text(json.dumps(
            {"status": "aborted_dataset_guard", "checks": checks},
            indent=2))
        log("ABORTED: dataset guard tripped; no training done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE)
    # Make the ChatML stop token authoritative for training + generation.
    tok.eos_token = "<|im_end|>"

    ending_check = {
        "train": verify_endings(tok, data["train"], "train"),
        "benchmark": verify_endings(tok, data["benchmark"], "benchmark")}
    endings_ok = all(v["all_ok"] for v in ending_check.values())
    log("ending check:", json.dumps(ending_check))
    if not endings_ok:
        log("!!! ENDING CHECK FAILED — see RESULTS.json; continuing "
            "so downstream behavior is on record !!!")

    durations = {"train_s": None, "save_s": None, "gen_s": None}
    results = {
        "script": "job_train_yanglearn_v1.py",
        "corpus_commit": COMMIT,
        "versions": vers,
        "train_rows": len(data["train"]),
        "ending_check": ending_check,
        "assertion": None,
        "assertion_warning": None,
        "train": None,
        "generations": None,
        "durations": durations,
    }
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # ---------------------------------------------------------------
    # FULL FINE-TUNE — bf16 weights, no quantization, no PEFT.
    # ---------------------------------------------------------------
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    ds = Dataset.from_list([to_pc(r) for r in data["train"]])
    model = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16, device_map="auto")
    model.config.use_cache = False
    n_params = sum(p.numel() for p in model.parameters())
    n_trainable = sum(p.numel() for p in model.parameters()
                      if p.requires_grad)
    run_info.update({"model_params": n_params,
                     "trainable_params": n_trainable,
                     "tie_word_embeddings":
                         model.config.tie_word_embeddings})
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log(f"model loaded: {n_params:,} params, {n_trainable:,} "
        f"trainable, tied={model.config.tie_word_embeddings}")

    sft_cfg = SFTConfig(output_dir="/tmp/train_out",
                        per_device_train_batch_size=CFG["bs"],
                        gradient_accumulation_steps=CFG["accum"],
                        num_train_epochs=CFG["epochs"],
                        learning_rate=CFG["lr"],
                        lr_scheduler_type="cosine",
                        warmup_ratio=CFG["warmup"],
                        weight_decay=CFG["weight_decay"],
                        max_grad_norm=CFG["max_grad_norm"],
                        logging_steps=CFG["logging_steps"],
                        save_strategy="no", bf16=True,
                        max_length=CFG["max_len"],
                        gradient_checkpointing=CFG["grad_ckpt"],
                        gradient_checkpointing_kwargs={
                            "use_reentrant": False},
                        completion_only_loss=True, report_to=[])
    # processing_class=tok is load-bearing (run 3): TRL appends its
    # processing tokenizer's eos; the overridden tokenizer keeps
    # exactly one trailing 151645.
    trainer = SFTTrainer(model=model, args=sft_cfg, train_dataset=ds,
                         processing_class=tok)

    # PRE-TRAINING ASSERTION (probe pattern): a real collated batch
    # must supervise 151645 at every row's last non-pad label.
    assertion = assert_eos_labels(trainer)
    results["assertion"] = assertion
    results["assertion_warning"] = (
        None if assertion["passed"] else
        "ASSERTION FAILED: the collated batch does not supervise "
        "151645 at every row end — see assertion detail. Training "
        "and generation continued so downstream behavior is on "
        "record.")
    if assertion["passed"]:
        log("ASSERTION PASSED: every row's last non-pad label == 151645;",
            "row0 last5 ids:", assertion["row0_last5_token_ids"],
            "row0 last label:", assertion["row0_last_label"])
    else:
        log("!!! ASSERTION FAILED !!! last labels:",
            assertion["per_row_last_labels"], "error:",
            assertion["error"])
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    t_train = time.time()
    result = trainer.train()
    durations["train_s"] = round(time.time() - t_train, 1)
    train_loss = result.training_loss
    log("train done; loss:", train_loss, "in", durations["train_s"], "s")
    results["train"] = {"loss": train_loss, "epochs": CFG["epochs"],
                        "steps_expected": -(-len(data["train"])
                                            // (CFG["bs"] * CFG["accum"]))
                        * CFG["epochs"],
                        "log_history": trainer.state.log_history}
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # Save the trained model (bf16 safetensors) + tokenizer +
    # generation_config.json carrying the EOS pair, so any consumer
    # gets correct stopping.
    t_save = time.time()
    from transformers import GenerationConfig
    gen_cfg = GenerationConfig(eos_token_id=EOS_IDS, do_sample=False,
                               pad_token_id=ENDOFTEXT_ID)
    trainer.model.generation_config = gen_cfg
    model_dir = OUT / "model"
    trainer.save_model(str(model_dir))
    gen_cfg.save_pretrained(str(model_dir))
    tok.save_pretrained(str(model_dir))
    durations["save_s"] = round(time.time() - t_save, 1)
    log("model saved to", model_dir, "in", durations["save_s"], "s")

    # SLIM EVAL: generation only, on the frozen bench v1 and dev.
    # Authoritative mechanical scoring runs back home.
    t_gen = time.time()
    bench_rows = data["benchmark"] + data["held_out"]
    gen_bench = run_generations(trainer.model, tok, bench_rows,
                                "benchmark", "generations_benchmark.jsonl")
    gen_dev = run_generations(trainer.model, tok, data["dev"],
                              "dev", "generations_dev.jsonl")
    durations["gen_s"] = round(time.time() - t_gen, 1)
    results["generations"] = {"benchmark": gen_bench, "dev": gen_dev}
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    done = {"status": "completed",
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "corpus_commit": COMMIT,
            "ending_check_ok": endings_ok,
            "assertion_passed": assertion["passed"],
            "train_loss": train_loss,
            "bench_stopped_cleanly_pct":
                gen_bench["stopped_cleanly_pct"],
            "dev_stopped_cleanly_pct": gen_dev["stopped_cleanly_pct"],
            "model_dir": "model/"}
    (OUT / "DONE.json").write_text(json.dumps(done, indent=2))
    log("ALL DONE in", done["elapsed_min"], "min;",
        "assertion:", assertion["passed"],
        "| train loss:", train_loss,
        "| bench stopped_cleanly%:", done["bench_stopped_cleanly_pct"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "DONE.json").write_text(json.dumps(
                {"status": "error",
                 "error": traceback.format_exc()}, indent=2))
        except Exception:  # noqa: BLE001 - bucket may be unreachable
            pass
        raise
