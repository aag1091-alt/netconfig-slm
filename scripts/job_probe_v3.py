#!/usr/bin/env python3
"""HF Jobs probe v3: does the v2 QLoRA recipe actually teach stopping?

WHY THIS PROBE EXISTS (outputs/hf-job-2/DIAGNOSIS.md): run 2 trained on
the full v1.0 dataset with the v2 EOS fix and still scored
stopped_cleanly 0.0% — a teacher-forced probe of the shipped GGUF showed
the end-of-config boundary distribution is flat and context-independent,
i.e. the EOS position received no effective gradient in the training
that actually executed, even though the data pipeline verifies correct
four independent ways. Two live hypotheses:
  H1: something in the executed stack nullified EOS supervision at
      scale (invisible to source reading, single-row self-checks, logs).
  H2: the adapter learned EOS but the bf16 merge annihilated it (only
      the merged artifact was ever evaluated in runs 1-2).

THIS PROBE (DIAGNOSIS §5 Step 1) bisects them for <$1:
  * 256 train rows (stratified across the 6 families, all <=1024
    tokens, seed 42) + 24 held-out test prompts (4 per family).
  * Logs the environment (pip freeze + library versions) into
    RUN_INFO.json — run 2 logged no versions; this probe must.
  * PRE-TRAINING ASSERTION: a real batch from the real train
    dataloader (real TRL processing + real collator) must have every
    row's last non-pad label == 151645 (<|im_end|>). If it fails, the
    run STILL trains + evals so downstream behavior is on record, but
    RESULTS.json flags it loudly.
  * Trains the identical v2 QLoRA recipe for 1 epoch (~16 steps).
  * Evaluates BOTH artifacts on the 24 held-out prompts with v2's
    stopped_cleanly definition: (a) the adapter on its 4-bit base,
    (b) an fp32 merge saved/evaluated in bf16. Gate: >=90% clean
    stops on either artifact before any full retrain is considered.

LAUNCH DISCIPLINE (do not skip): pip in the job command MUST be a
single invocation pinning torch==2.5.1 torchvision==0.20.1
torchaudio==2.5.1 to match the pytorch/pytorch:2.5.1-cuda12.4-cudnn9
-runtime image, plus transformers>=4.51,<4.58 trl>=0.19,<0.26
peft>=0.17,<0.19 bitsandbytes>=0.47 accelerate>=1.4 datasets>=3.2,<4.

Outputs go to /output/run3-probe (mounted bucket
aag1091/netconfig-train-output): RUN_INFO.json, RESULTS.json,
generations_adapter.jsonl, generations_merged.jsonl, DONE.json.
No merged model is written to the bucket; no GGUF; no scoring ladder.
"""
import gc
import importlib.metadata
import json
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

OUT = Path("/output/run3-probe")
DS_REPO = "aag1091/netconfig-slm-dataset"
DS = f"https://huggingface.co/datasets/{DS_REPO}/resolve/main"
BASE = "Qwen/Qwen3-4B-Base"
MIN_TRAIN_ROWS = 4000
IM_END_ID = 151645          # <|im_end|> — ChatML turn end
ENDOFTEXT_ID = 151643       # <|endoftext|> — Qwen3-Base native eos
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 512
SUBSET_ROWS = 256
HELDOUT_PER_FAMILY = 4
SEED = 42
GATE_PCT = 90.0
# Resubmission patch (run 1 OOM'd on step 1: full-vocab logits at bs=8
# on the 22 GiB A10G-small): bs 8->2, accum 2->8. Effective batch stays
# 16 and the 256-row epoch still makes ~16 optimizer steps.
# Attempt-3 patch (run 2 trained fine, then the adapter eval crashed
# in generate: "expected scalar type Half but found Float"): run_eval
# generates under torch.autocast(bf16), and each artifact's eval is
# crash-isolated in main() with results flushed incrementally.
CFG = dict(lora_r=16, lora_alpha=32, lora_dropout=0.05,
           target=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj",
                   "up_proj", "down_proj"],
           bs=2, accum=8, epochs=1, lr=2e-4, warmup=0.03, max_len=1024)

KEY_TOKEN = re.compile(
    r"\b(?:vlan\s*\d+|\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?|GigabitEthernet\S+"
    r"|TenGigabitEthernet\S+|AS\s*\d+|\d{5})\b", re.I)

VERSION_PKGS = ["trl", "transformers", "peft", "datasets", "torch",
                "accelerate", "bitsandbytes"]


def log(*a):
    print("[probe]", *a, flush=True)


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


def coverage(ref_config, gen_config):
    ref = set(t.lower() for t in KEY_TOKEN.findall(ref_config))
    if not ref:
        return 1.0
    gen = gen_config.lower()
    return sum(1 for t in ref if t in gen) / len(ref)


def prompt_of(intent):
    return f"<|im_start|>user\n{intent}<|im_end|>\n<|im_start|>assistant\n"


def to_pc(row):
    """TRL prompt/completion pair (identical to v2): loss applies to
    the completion only, and the completion ends with <|im_end|>."""
    return {"prompt": prompt_of(row["intent_nl"]),
            "completion": f"{row['config']}<|im_end|>"}


def select_subset(train_rows, tok, total=SUBSET_ROWS, seed=SEED,
                  max_tokens=1024):
    """Tokenize train rows with the real tokenizer; keep rows whose
    full prompt+completion is <= max_tokens; stratified-sample `total`
    rows across families (seeded). Returns (rows, family_counts)."""
    texts = [prompt_of(r["intent_nl"]) + r["config"] + "<|im_end|>"
             for r in train_rows]
    enc = tok(texts, add_special_tokens=False)["input_ids"]
    by_family = {}
    for row, ids in zip(train_rows, enc):
        if len(ids) <= max_tokens:
            by_family.setdefault(row["family"], []).append(row)
    families = sorted(by_family)
    base = total // len(families)
    rem = total - base * len(families)
    rng = random.Random(seed)
    picked, counts = [], {}
    for i, fam in enumerate(families):
        pool = list(by_family[fam])
        rng.shuffle(pool)
        take = base + (1 if i < rem else 0)
        picked.extend(pool[:take])
        counts[fam] = take
    rng.shuffle(picked)
    return picked, counts


def select_heldout(test_rows, per_family=HELDOUT_PER_FAMILY):
    """First `per_family` test rows per family, in file order."""
    by_family = {}
    for row in test_rows:
        by_family.setdefault(row["family"], []).append(row)
    picked = []
    for fam in sorted(by_family):
        picked.extend(by_family[fam][:per_family])
    return picked


def build_trainer(model, tok, rows, peft_cfg, bf16=True,
                  output_dir="/tmp/probe_train_out"):
    """SFTTrainer with the exact v2 SFTConfig (probe: epochs=1)."""
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    ds = Dataset.from_list([to_pc(r) for r in rows])
    sft_cfg = SFTConfig(output_dir=output_dir,
                        per_device_train_batch_size=CFG["bs"],
                        gradient_accumulation_steps=CFG["accum"],
                        num_train_epochs=CFG["epochs"],
                        learning_rate=CFG["lr"], lr_scheduler_type="cosine",
                        warmup_ratio=CFG["warmup"], logging_steps=10,
                        save_strategy="no", bf16=bf16,
                        max_length=CFG["max_len"],
                        completion_only_loss=True, report_to=[])
    # processing_class=tok is load-bearing (see v2): TRL appends its
    # processing tokenizer's eos; the overridden tokenizer keeps
    # exactly one trailing 151645.
    return SFTTrainer(model=model, args=sft_cfg, train_dataset=ds,
                      peft_config=peft_cfg, processing_class=tok)


def assert_eos_labels(trainer, expect=IM_END_ID):
    """THE probe assertion: take a real batch (real TRL processing +
    real collator, via the train dataloader) and check every row's
    last non-pad label == expect (151645). Also captures row 0's last
    5 token ids and last label. Never raises: failures are recorded."""
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


def run_eval(model, tok, rows, tag):
    """Greedy eval with v2's stopped_cleanly definition. Writes
    generations_<tag>.jsonl to OUT; returns (overall, per_prompt)."""
    import torch
    model.eval()
    try:
        dev = model.device
    except Exception:  # noqa: BLE001 - PeftModel device property varies
        dev = next(model.parameters()).device
    gens, per_prompt = [], []
    n_stop, cov_sum = 0, 0.0
    for row in rows:
        inputs = tok(prompt_of(row["intent_nl"]),
                     return_tensors="pt").to(dev)
        # Attempt-3 patch: the raw PeftModel generate path mixes
        # dtypes (attempt 2 died in lm_head: "expected scalar type
        # Half but found Float"). Autocast bf16 — the v2 compute
        # dtype — makes every Linear in the forward consistent.
        with torch.no_grad(), torch.autocast("cuda",
                                             dtype=torch.bfloat16):
            out = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS,
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
        per_prompt.append({"id": row["id"], "family": row["family"],
                           "stopped_cleanly": stopped_cleanly})
        n_stop += int(stopped_cleanly)
        cov_sum += coverage(row["config"], gen)
    n = len(rows)
    overall = {"n": n,
               "stopped_cleanly_pct": round(100 * n_stop / n, 1),
               "mean_coverage": round(cov_sum / n, 3),
               "gate_pass": (100 * n_stop / n) >= GATE_PCT}
    (OUT / f"generations_{tag}.jsonl").write_text(
        "\n".join(json.dumps(g) for g in gens) + "\n")
    log(tag, "eval overall:", overall)
    return overall, per_prompt


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
                "eos_ids": EOS_IDS, "script": "job_probe_v3.py",
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

    # Guard (same as v2): never silently train on a stale dataset.
    if len(train_rows) < MIN_TRAIN_ROWS:
        (OUT / "DONE.json").write_text(json.dumps(
            {"status": "aborted_stale_dataset",
             "dataset_sha": sha, "train_rows": len(train_rows),
             "test_rows": len(test_rows),
             "reason": f"train rows {len(train_rows)} < {MIN_TRAIN_ROWS}"},
            indent=2))
        log("ABORTED: stale dataset guard tripped; no training done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE)
    # Make the ChatML stop token authoritative for training + generation.
    tok.eos_token = "<|im_end|>"

    # Probe subset: 256 stratified train rows (<=1024 tokens) +
    # 24 held-out prompts (4 per family from the test split).
    subset, fam_counts = select_subset(train_rows, tok)
    heldout = select_heldout(test_rows)
    run_info.update({"subset_rows": len(subset),
                     "subset_family_counts": fam_counts,
                     "subset_ids": [r["id"] for r in subset],
                     "heldout_rows": len(heldout),
                     "heldout_ids": [r["id"] for r in heldout]})
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log(f"subset: {len(subset)} rows {fam_counts}; "
        f"heldout: {len(heldout)} prompts")

    # QLoRA train — identical recipe to v2, 1 epoch.
    from peft import LoraConfig
    from transformers import BitsAndBytesConfig
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=bnb, device_map="auto")
    peft_cfg = LoraConfig(r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"],
                          lora_dropout=CFG["lora_dropout"],
                          target_modules=CFG["target"],
                          task_type="CAUSAL_LM")
    trainer = build_trainer(model, tok, subset, peft_cfg)

    # PRE-TRAINING ASSERTION (the point of the probe): a real collated
    # batch must supervise 151645 at every row's last non-pad label.
    assertion = assert_eos_labels(trainer)
    if assertion["passed"]:
        log("ASSERTION PASSED: every row's last non-pad label == 151645;",
            "row0 last5 ids:", assertion["row0_last5_token_ids"],
            "row0 last label:", assertion["row0_last_label"])
    else:
        log("!!! ASSERTION FAILED !!! last labels:",
            assertion["per_row_last_labels"], "error:",
            assertion["error"])

    t_train = time.time()
    result = trainer.train()
    train_s = round(time.time() - t_train, 1)
    train_loss = result.training_loss
    log_history = trainer.state.log_history
    log("train done; loss:", train_loss, "in", train_s, "s")
    adapter_dir = "/tmp/adapter_probe"
    trainer.save_model(adapter_dir)
    tok.save_pretrained(adapter_dir)

    # Attempt-3 patch: per-artifact crash isolation. Each eval runs
    # in its own try/except; a failure is recorded in RESULTS.json
    # instead of killing the job (attempt 2 lost BOTH measurements
    # when the adapter eval crashed). The adapter result is flushed
    # to the bucket BEFORE the merged eval is attempted.
    durations = {"train_s": train_s, "eval_adapter_s": None,
                 "merge_s": None, "eval_merged_s": None}
    results = {
        "script": "job_probe_v3.py",
        "dataset_sha": sha,
        "versions": vers,
        "subset_rows": len(subset),
        "subset_family_counts": fam_counts,
        "heldout_rows": len(heldout),
        "assertion": assertion,
        "assertion_warning": (
            None if assertion["passed"] else
            "ASSERTION FAILED: the collated batch does not supervise "
            "151645 at every row end — see assertion detail. Training "
            "and eval continued so downstream behavior is on record."),
        "train": {"loss": train_loss, "epochs": CFG["epochs"],
                  "steps_expected": len(subset) // (CFG["bs"]
                                                     * CFG["accum"]),
                  "log_history": log_history},
        "adapter_eval": None,
        "merged_eval": None,
        "durations": durations,
    }

    # Eval (a): the adapter on its 4-bit base.
    adapter_eval = None
    try:
        t_ev = time.time()
        adapter_eval, adapter_pp = run_eval(trainer.model, tok,
                                            heldout, "adapter")
        durations["eval_adapter_s"] = round(time.time() - t_ev, 1)
        results["adapter_eval"] = {**adapter_eval,
                                    "per_prompt": adapter_pp}
    except Exception:  # noqa: BLE001 - record, don't die
        results["adapter_eval"] = {"error": traceback.format_exc()}
        log("!!! ADAPTER EVAL FAILED !!!\n",
            results["adapter_eval"]["error"])
    # Flush the adapter result before the merged eval is attempted.
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))
    free(trainer.model)
    del trainer

    # Eval (b): fp32 merge, saved/evaluated in bf16 (bisects H2).
    merged_eval = None
    try:
        t_mg = time.time()
        from peft import PeftModel
        fp32_base = AutoModelForCausalLM.from_pretrained(
            BASE, torch_dtype=torch.float32, device_map="auto")
        merged = PeftModel.from_pretrained(
            fp32_base, adapter_dir).merge_and_unload()
        merged = merged.to(torch.bfloat16)
        merged.save_pretrained("/tmp/merged_probe")
        tok.save_pretrained("/tmp/merged_probe")
        durations["merge_s"] = round(time.time() - t_mg, 1)
        log("fp32 merge done in", durations["merge_s"],
            "s; saved bf16 to /tmp")
        t_ev = time.time()
        merged_eval, merged_pp = run_eval(merged, tok, heldout,
                                          "merged")
        durations["eval_merged_s"] = round(time.time() - t_ev, 1)
        results["merged_eval"] = {**merged_eval,
                                  "per_prompt": merged_pp}
        free(merged)
    except Exception:  # noqa: BLE001 - record, don't die
        results["merged_eval"] = {"error": traceback.format_exc()}
        log("!!! MERGED EVAL FAILED !!!\n",
            results["merged_eval"]["error"])

    results["gate"] = {
        "rule": f"stopped_cleanly >= {GATE_PCT}% on "
                f"{len(heldout)} held-out prompts",
        "adapter_pass": (adapter_eval["gate_pass"]
                         if adapter_eval else None),
        "merged_pass": (merged_eval["gate_pass"]
                        if merged_eval else None)}
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    done = {"elapsed_min": round((time.time() - t0) / 60, 1),
            "dataset_sha": sha,
            "assertion_passed": assertion["passed"],
            "adapter_stopped_cleanly_pct":
                (adapter_eval["stopped_cleanly_pct"]
                 if adapter_eval else None),
            "merged_stopped_cleanly_pct":
                (merged_eval["stopped_cleanly_pct"]
                 if merged_eval else None),
            "train_loss": train_loss}
    if adapter_eval is None and merged_eval is None:
        done["status"] = "error"
        done["eval_errors"] = {
            "adapter": results["adapter_eval"]["error"],
            "merged": results["merged_eval"]["error"]}
        (OUT / "DONE.json").write_text(json.dumps(done, indent=2))
        log("ALL DONE in", done["elapsed_min"], "min; assertion:",
            assertion["passed"],
            "| BOTH EVALS FAILED — see RESULTS.json")
        raise SystemExit(1)
    done["status"] = "completed"
    (OUT / "DONE.json").write_text(json.dumps(done, indent=2))
    log("ALL DONE in", done["elapsed_min"], "min;",
        "assertion:", assertion["passed"],
        "| adapter stop%:", done["adapter_stopped_cleanly_pct"],
        "| merged stop%:", done["merged_stopped_cleanly_pct"])


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
