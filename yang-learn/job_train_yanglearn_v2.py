#!/usr/bin/env python3
"""HF Jobs yang-learn run 2: FULL fine-tune of Qwen3-4B-Base on the
yang-learn corpus v1 train split PLUS the corpus v2 additions
(1,047 + 371 = 1,418 records), corpus files frozen at netconfig-slm
commit f3a9b1df0e0092fa54ba6caa3fd2e1dfc463f820.

Run 2 drills run 1's three gaps (path_meaning type recall,
revised_schema citation discipline, clean stopping) with corpus
v2 additions only — the training recipe is IDENTICAL to run 1
(job_train_yanglearn_v1.py) so the corpus is the only variable.
Design + measured quote: yang-learn/RUN2_PLAN.md. Under Avinash's
standing approval (board #78: program total strictly under $15);
timeoutSeconds=3600 at a100-large ($2.50/hr) is the hard cap;
expected ~$0.50-0.90.

DIFFERENCES FROM RUN 1 (deliberate):
  * Dataset = train.jsonl + train_v2_additions.jsonl combined.
    Guard verifies each file's sha256 + row count AND the sha256
    of the concatenated bytes + the combined row count (1,418).
  * NO in-job generation. Run 1's sequential generation burned
    ~50 min and killed the job at its timeout; run 2 evaluates
    separately with a batched eval job (eval_batched.py core).
  * Output prefix yang-learn-run2/ in the same bucket.

Everything else is run 1's verified plumbing, unchanged: TRL
prompt/completion format, completion-only loss,
tokenizer.eos_token = "<|im_end|>" as processing_class,
pre-training EOS assertion (recorded loudly, never aborts), full
fine-tune bf16, tied embeddings, lr 1e-5 cosine warmup 0.05,
epochs 2, bs 4 x accum 4 (effective 16), max_length 1536,
gradient checkpointing, single pinned pip install.

Outputs go to /output/yang-learn-run2 (mounted HF bucket
aag1091/netconfig-train-output): RUN_INFO.json, RESULTS.json,
DONE.json, model/ (bf16 safetensors + tokenizer +
generation_config.json with eos [151645, 151643]). Nothing is
published by this job.
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
COMMIT = "f3a9b1df0e0092fa54ba6caa3fd2e1dfc463f820"
RAW = f"https://raw.githubusercontent.com/aag1091-alt/netconfig-slm/{COMMIT}"
FILES = {
    "train": ("yang-learn/corpus/train.jsonl",
              "ff9dd20595f65e739be12a3c5ad9233346c3ff567fcef80ee26379e7f4aeebc2",
              1047),
    "train_v2_additions": (
        "yang-learn/corpus/train_v2_additions.jsonl",
        "cc601021fc8427baf12621e348ab3f62802df898d7935ab602df3f72e2db8354",
        371),
}
COMBINED_SHA = "25944fba79b357ab6c5f80f4f50f61c3aa33ecfb9bac440df8fb9d8e2b559880"
COMBINED_ROWS = 1418
BASE = "Qwen/Qwen3-4B-Base"
IM_END_ID = 151645          # <|im_end|> — ChatML turn end
ENDOFTEXT_ID = 151643       # <|endoftext|> — Qwen3-Base native eos
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
CFG = dict(lr=1e-5, warmup=0.05, weight_decay=0.0, max_grad_norm=1.0,
           epochs=2, bs=4, accum=4, max_len=1536,
           grad_ckpt=True, logging_steps=10)

VERSION_PKGS = ["trl", "transformers", "datasets", "torch",
                "accelerate"]


def log(*a):
    print("[yangft2]", *a, flush=True)


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
    return rows, raw, {"tag": tag, "path": path, "sha256": got_sha,
                       "sha_ok": got_sha == want_sha,
                       "rows": len(rows),
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


# --- probe assertion, verbatim pattern from run 3 / run 1 -----------
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


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    vers = versions()
    run_info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "base": BASE, "cfg": CFG, "corpus_commit": COMMIT,
                "eos_ids": EOS_IDS,
                "script": "job_train_yanglearn_v2.py",
                "run": "yang-learn run 2: full fine-tune on corpus "
                       "v1 train + v2 additions (1,418 records); "
                       "recipe identical to run 1, corpus is the "
                       "only variable; no in-job generation (batched "
                       "eval is a separate job); Avinash standing "
                       "approval, program total < $15; "
                       "timeoutSeconds=3600 hard cap, 2026-11-27",
                "versions": vers, "pip_freeze": pip_freeze()}
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("output mount OK, RUN_INFO written; versions:", vers)

    work = Path("/tmp/work")
    work.mkdir(exist_ok=True)
    parts, checks = {}, []
    combined_raw = b""
    for tag in FILES:
        rows, raw, chk = fetch(tag, work / f"{tag}.jsonl")
        parts[tag] = rows
        combined_raw += raw
        checks.append(chk)
    train_rows = parts["train"] + parts["train_v2_additions"]
    combined = {"sha256": hashlib.sha256(combined_raw).hexdigest(),
                "rows": len(train_rows)}
    combined["sha_ok"] = combined["sha256"] == COMBINED_SHA
    combined["rows_ok"] = combined["rows"] == COMBINED_ROWS
    run_info["files"] = checks
    run_info["combined"] = combined
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("fetch checks:", json.dumps(checks), "combined:", combined)

    # Guard (run 3's discipline, sha256-pinned): never silently
    # train on a stale or different corpus.
    if not (all(c["sha_ok"] and c["rows_ok"] for c in checks)
            and combined["sha_ok"] and combined["rows_ok"]):
        (OUT / "DONE.json").write_text(json.dumps(
            {"status": "aborted_dataset_guard", "checks": checks,
             "combined": combined}, indent=2))
        log("ABORTED: dataset guard tripped; no training done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE)
    # Make the ChatML stop token authoritative for training.
    tok.eos_token = "<|im_end|>"

    ending_check = {"train": verify_endings(tok, train_rows, "train")}
    endings_ok = all(v["all_ok"] for v in ending_check.values())
    log("ending check:", json.dumps(ending_check))
    if not endings_ok:
        log("!!! ENDING CHECK FAILED — see RESULTS.json; continuing "
            "so downstream behavior is on record !!!")

    durations = {"train_s": None, "save_s": None}
    results = {
        "script": "job_train_yanglearn_v2.py",
        "corpus_commit": COMMIT,
        "versions": vers,
        "train_rows": len(train_rows),
        "ending_check": ending_check,
        "assertion": None,
        "assertion_warning": None,
        "train": None,
        "durations": durations,
    }
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # ---------------------------------------------------------------
    # FULL FINE-TUNE — bf16 weights, no quantization, no PEFT.
    # ---------------------------------------------------------------
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    ds = Dataset.from_list([to_pc(r) for r in train_rows])
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
    # processing_class=tok is load-bearing (run 3 / run 1): TRL
    # appends its processing tokenizer's eos; the overridden
    # tokenizer keeps exactly one trailing 151645.
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
        "continued so downstream behavior is on record.")
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
                        "steps_expected": -(-len(train_rows)
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
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    done = {"status": "completed",
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "corpus_commit": COMMIT,
            "ending_check_ok": endings_ok,
            "assertion_passed": assertion["passed"],
            "train_loss": train_loss,
            "train_rows": len(train_rows),
            "model_dir": "model/"}
    (OUT / "DONE.json").write_text(json.dumps(done, indent=2))
    log("ALL DONE in", done["elapsed_min"], "min;",
        "assertion:", assertion["passed"],
        "| train loss:", train_loss)


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
