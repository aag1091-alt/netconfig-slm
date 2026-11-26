#!/usr/bin/env python3
"""HF Jobs run 3: FULL fine-tune of Qwen3-4B-Base on the full
NetConfig SLM dataset v1.0 (4,496 train rows), `end`-terminator
reformulation, slim in-job eval. Approved by Avinash 2026-11-24
(~$5-7 envelope) after the 8-probe QLoRA bisection closed.

WHY FULL FT (outputs/hf-job-2/DIAGNOSIS.md + probe attempts 1-8,
$3.72 total): run 2 (QLoRA, 3 epochs) learned content perfectly
(train loss 0.0447, key-token coverage 0.986) but scored
stopped_cleanly 0.0% — the boundary P(151645) sits at ~4.3e-4, flat,
exactly at the right position. Eight probes retired every
LoRA-family route: the collated batch provably supervises 151645
(assertion passed every attempt); the bf16 merge is exonerated
(adapter and fp32-merge both 0.0%); the `end`-terminator
reformulation (probe v4) helped most (adapter 8.3% / merged 20.8%
at 1 epoch) but regressed to 0.0% at 3 epochs (knife-edge);
modules_to_save=["lm_head"] NaN'd mechanically at two unrelated
LRs; LoRA-on-lm_head (untied) trained cleanly but moved the EOS
row only delta_l2 0.0057 (adapter 4.2%). Root cause: Qwen3-4B-Base's
TIED lm_head/embedding row for <|im_end|> (151645) — a chat-format
token barely trained in the base model — is FROZEN under LoRA, so
adapters can only steer hidden states toward a fixed, poorly
conditioned output row. Full fine-tuning gives that row real
full-parameter gradient. Embeddings/lm_head stay TIED here — that
is fine and standard for full FT; do NOT untie.

FORMULATION (run 2's verified plumbing + probe v4's data fix):
  * TRL prompt/completion format, completion-only loss;
    tokenizer.eos_token = "<|im_end|>" passed as processing_class
    (load-bearing: TRL appends the processing tokenizer's eos;
    the override keeps exactly one trailing 151645).
  * Every train completion AND every test reference config gains
    Cisco's natural final line `end` before <|im_end|> (probe v4's
    add_terminator, verbatim); endings verified on ALL rows with
    probe v4's verify_endings and recorded.
  * Pre-training assertion (probe pattern, verbatim): a real
    collated batch from the actual train dataloader must have
    every row's last non-pad label == 151645. Recorded in
    RESULTS.json; training continues even if it fails, flagged
    loudly (the probes' discipline: evidence over aborts).
  * max_length 1536 (DIAGNOSIS §4: run 2's 1024 truncated 50 rows
    and ate their EOS; p95 is 792, max 1,421).
  * pip freeze + library versions + full config into RUN_INFO.json
    (run 2 logged no versions — a process gap, not repeated).

TRAINING: full fine-tune — AutoModelForCausalLM in bf16, NO
quantization, NO PEFT, all ~4.02B params trainable. lr 1e-5,
cosine, warmup_ratio 0.05, weight_decay 0.0, max_grad_norm 1.0,
epochs 2, per_device bs 4 x accum 4 (effective 16) => ~562 steps,
gradient_checkpointing (use_reentrant=False), logging_steps 10.
Memory on A100-80GB: bf16 weights ~8GB + bf16 grads ~8GB + AdamW
moments (fp32 upcast ~32GB worst case) + checkpointed activations
— fits with room.

SLIM IN-JOB EVAL (the run-2 lesson: its 504-generation base+ft
eval with coverage/parse cost ~3 of the 4.1 billed hours): NO
base-model eval, NO coverage metrics. After training: greedy
generation (eos [151645, 151643], max_new_tokens 512, as run 2)
on ALL 252 test prompts with the TRAINED model only; per-row
stopped_cleanly computed in-job and aggregated into RESULTS.json.
The authoritative scoring (Batfish >= 95% AND intent-match >= 90%
triaged on RAW generations — the publish bar set by Avinash
2026-11-23) runs back home from generations_test.jsonl.

LAUNCH DISCIPLINE (do not skip): pip in the job command MUST be a
single invocation pinning torch==2.5.1 torchvision==0.20.1
torchaudio==2.5.1 to match the pytorch/pytorch:2.5.1-cuda12.4
-cudnn9-runtime image, plus transformers>=4.51,<4.58
trl>=0.19,<0.26 accelerate>=1.4 datasets>=3.2,<4. (peft and
bitsandbytes are NOT used by this script.)

Outputs go to /output/run3-fullft (mounted HF bucket
aag1091/netconfig-train-output): RUN_INFO.json, RESULTS.json,
DONE.json, generations_test.jsonl, model/ (bf16 safetensors +
tokenizer + generation_config.json with eos [151645, 151643]).
Nothing is published by this job.
"""
import gc
import importlib.metadata
import json
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

OUT = Path("/output/run3-fullft")
DS_REPO = "aag1091/netconfig-slm-dataset"
DS = f"https://huggingface.co/datasets/{DS_REPO}/resolve/main"
BASE = "Qwen/Qwen3-4B-Base"
EXPECTED_SHA = "ecf3d5a9a67ab9a52342d3a2ed6ed91b01392831"
MIN_TRAIN_ROWS = 4000
IM_END_ID = 151645          # <|im_end|> — ChatML turn end
ENDOFTEXT_ID = 151643       # <|endoftext|> — Qwen3-Base native eos
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 512
CFG = dict(lr=1e-5, warmup=0.05, weight_decay=0.0, max_grad_norm=1.0,
           epochs=2, bs=4, accum=4, max_len=1536,
           grad_ckpt=True, logging_steps=10)

VERSION_PKGS = ["trl", "transformers", "datasets", "torch",
                "accelerate"]


def log(*a):
    print("[fullft3]", *a, flush=True)


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


def fetch(name, dest):
    urllib.request.urlretrieve(f"{DS}/{name}", dest)
    return [json.loads(l) for l in Path(dest).read_text().splitlines()
            if l.strip()]


def dataset_revision():
    """Current HEAD sha of the HF dataset repo (provenance)."""
    req = urllib.request.Request(
        f"https://huggingface.co/api/datasets/{DS_REPO}",
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["sha"]


def prompt_of(intent):
    return f"<|im_start|>user\n{intent}<|im_end|>\n<|im_start|>assistant\n"


# --- probe v4 data transform, verbatim ------------------------------
def add_terminator(config):
    """§4.2 reformulation: append Cisco's natural final line `end`
    after the config's last line. Every v1.0 config ends with a
    single trailing newline, so the result ends "...\\nend"."""
    if config.endswith("\n"):
        return config + "end"
    return config + "\nend"


def completion_text(row):
    """The full completion string for a row, terminator applied."""
    return f"{add_terminator(row['config'])}<|im_end|>"


def to_pc(row):
    """TRL prompt/completion pair: loss applies to the completion
    only; the completion ends "...\\nend" + <|im_end|>."""
    return {"prompt": prompt_of(row["intent_nl"]),
            "completion": completion_text(row)}


def verify_endings(tok, rows, tag):
    """Post-transform check (probe v4, verbatim): every processed
    completion's last tokens must be the token(s) for "end" (as the
    final config line) followed by 151645, with the line break
    supplied by the token just before the "end" span. The check is
    done on the FULL completion's tokens rather than on
    tok("\\nend") in isolation: BPE can merge the "\\n" into the
    preceding line's final token (e.g. "0\\n" as one token), so a
    standalone tokenization of "\\nend" is not the reference.
    Never raises."""
    end_ids = tok("\nend", add_special_tokens=False)["input_ids"]
    bad = []
    for r in rows:
        ids = tok(completion_text(r),
                  add_special_tokens=False)["input_ids"]
        ok = False
        if ids and ids[-1] == IM_END_ID:
            # The final "end" is 1-2 tokens; the token before its span
            # must end with the line break.
            for k in (1, 2):
                if len(ids) < k + 2:
                    continue
                if tok.decode(ids[-(k + 1):-1]) == "end":
                    before = ids[-(k + 2):-(k + 1)]
                    if before and tok.decode(before).endswith("\n"):
                        ok = True
                    break
        if not ok:
            bad.append({"id": r["id"], "tail": ids[-4:]})
    return {"tag": tag, "checked": len(rows),
            "end_token_ids": end_ids,
            "all_ok": not bad, "bad": bad[:5]}


# --- probe assertion, verbatim pattern ------------------------------
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


def run_eval(model, tok, rows):
    """Slim generation pass (run 3): greedy, trained model only,
    ALL test rows. Record shape matches run 2's generations files
    (id, family, generated, stopped_cleanly); writes
    generations_test.jsonl to OUT and returns the aggregate."""
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
        inputs = tok(prompt_of(row["intent_nl"]),
                     return_tensors="pt").to(dev)
        # Autocast bf16 wrap carried over from the probes (attempt 2
        # died in generate on a dtype mismatch); harmless on a pure
        # bf16 full model and keeps the forward uniform.
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
                     "generated": gen, "stopped_cleanly": stopped_cleanly})
        fam = per_family.setdefault(row["family"],
                                    {"n": 0, "stop": 0})
        fam["n"] += 1
        fam["stop"] += int(stopped_cleanly)
        n_stop += int(stopped_cleanly)
    (OUT / "generations_test.jsonl").write_text(
        "\n".join(json.dumps(g) for g in gens) + "\n")
    n = len(rows)
    overall = {"n": n, "stopped_cleanly": n_stop,
               "stopped_cleanly_pct": round(100 * n_stop / n, 1),
               "per_family": {f: {"n": s["n"], "stopped_cleanly": s["stop"],
                                  "stopped_cleanly_pct":
                                      round(100 * s["stop"] / s["n"], 1)}
                              for f, s in sorted(per_family.items())}}
    log("eval overall:", overall)
    return overall


def free(obj):
    import torch
    del obj
    gc.collect()
    torch.cuda.empty_cache()


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    vers = versions()
    run_info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "base": BASE, "cfg": CFG, "dataset": DS,
                "expected_dataset_sha": EXPECTED_SHA,
                "eos_ids": EOS_IDS,
                "script": "job_train_fullft_v3.py",
                "run": "run 3: full fine-tune (Avinash-approved "
                       "~$5-7 envelope, 2026-11-24) after the "
                       "8-probe QLoRA bisection; end-terminator "
                       "reformulation; slim in-job eval",
                "versions": vers, "pip_freeze": pip_freeze()}
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("output mount OK, RUN_INFO written; versions:", vers)

    work = Path("/tmp/work")
    work.mkdir(exist_ok=True)
    train_rows = fetch("train.jsonl", work / "train.jsonl")
    test_rows = fetch("test.jsonl", work / "test.jsonl")
    sha = dataset_revision()
    run_info.update({"dataset_sha": sha,
                     "train_rows": len(train_rows),
                     "test_rows": len(test_rows)})
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log(f"dataset {sha[:12]} rows: {len(train_rows)} train / "
        f"{len(test_rows)} test")

    # Guards (run 2's discipline + the sha pin): never silently
    # train on a stale or different dataset.
    if sha != EXPECTED_SHA or len(train_rows) < MIN_TRAIN_ROWS:
        (OUT / "DONE.json").write_text(json.dumps(
            {"status": "aborted_dataset_guard",
             "dataset_sha": sha, "expected_sha": EXPECTED_SHA,
             "train_rows": len(train_rows),
             "test_rows": len(test_rows),
             "reason": f"sha match: {sha == EXPECTED_SHA}; train "
                       f"rows {len(train_rows)} >= {MIN_TRAIN_ROWS}: "
                       f"{len(train_rows) >= MIN_TRAIN_ROWS}"},
            indent=2))
        log("ABORTED: dataset guard tripped; no training done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE)
    # Make the ChatML stop token authoritative for training + generation.
    tok.eos_token = "<|im_end|>"

    # Ending verification on ALL rows (probe v4): every train
    # completion and every test reference must end with the
    # token(s) for "\nend" + 151645 after the transform.
    ending_check = {
        "train": verify_endings(tok, train_rows, "train"),
        "test_refs": verify_endings(tok, test_rows, "test_refs")}
    endings_ok = (ending_check["train"]["all_ok"]
                  and ending_check["test_refs"]["all_ok"])
    log("ending check:", json.dumps(ending_check))
    if not endings_ok:
        log("!!! ENDING CHECK FAILED — see RESULTS.json; continuing "
            "so downstream behavior is on record !!!")

    durations = {"train_s": None, "save_s": None, "eval_s": None}
    results = {
        "script": "job_train_fullft_v3.py",
        "dataset_sha": sha,
        "versions": vers,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "ending_check": ending_check,
        "assertion": None,
        "assertion_warning": None,
        "train": None,
        "eval": None,
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
    # processing_class=tok is load-bearing (see v2): TRL appends its
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
        "and eval continued so downstream behavior is on record.")
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

    # SLIM EVAL: trained model only, all 252 test prompts.
    t_ev = time.time()
    eval_overall = run_eval(trainer.model, tok, test_rows)
    durations["eval_s"] = round(time.time() - t_ev, 1)
    results["eval"] = eval_overall
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    done = {"status": "completed",
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "dataset_sha": sha,
            "ending_check_ok": endings_ok,
            "assertion_passed": assertion["passed"],
            "train_loss": train_loss,
            "stopped_cleanly_pct": eval_overall["stopped_cleanly_pct"],
            "model_dir": "model/"}
    (OUT / "DONE.json").write_text(json.dumps(done, indent=2))
    log("ALL DONE in", done["elapsed_min"], "min;",
        "assertion:", assertion["passed"],
        "| train loss:", train_loss,
        "| stopped_cleanly%:", done["stopped_cleanly_pct"])


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
