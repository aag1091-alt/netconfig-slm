#!/usr/bin/env python3
"""HF Jobs entrypoint v2: QLoRA train of Qwen3-4B-Base on the full
NetConfig SLM dataset (~5,000 pairs), with base-vs-fine-tuned eval on the
held-out test split. EOS/stopping-fix edition of scripts/job_train_eval.py.

WHAT WAS WRONG IN v1 (smoke run, 2026-11-23): training used plain-text
formatting and generation used the tokenizer's default eos, so the ChatML
stop token was never authoritative. Qwen3-4B-Base's tokenizer eos is
<|endoftext|> (id 151643) while ChatML turns end with <|im_end|> (id
151645); generations never reliably stopped — every output continued past
the first config with hallucinated extra turns, scoring Batfish 0/60 on
raw output (60/60 once truncated to the first config).

V2 FIXES:
  * Training data uses TRL's prompt/completion format, so loss is
    completion-only and the completion ends with <|im_end|>.
  * tokenizer.eos_token is set to "<|im_end|>" before training.
  * Generation passes eos_token_id=[151645, 151643] explicitly, and the
    merged model ships a generation_config.json carrying those ids, so
    any consumer of the published model gets correct stopping.
  * New eval metric: stopped_cleanly rate (terminated via EOS before the
    token cap, with no hallucinated "<|im_start|>" turn in the output).
  * Dataset guard: the HF dataset revision sha + split counts are
    recorded in RUN_INFO.json; if train rows < 4,000 the run aborts
    WITHOUT training (v1 silently trained on v0.2 because it fetched
    whatever was live mid-run).

LAUNCH DISCIPLINE (do not skip): pip in the job command MUST pin
  torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1
to match the pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime image, plus
  transformers>=4.51,<4.58  trl>=0.19,<0.26  peft>=0.17,<0.19
  bitsandbytes>=0.47  accelerate>=1.4  datasets>=3.2,<4  ciscoconfparse2
Smoke-run attempt 1 died when pip drifted torch past the image's build
(torchvision::nms missing -> Qwen3ForCausalLM import failure).

Outputs go to /output/run2 (mounted HF bucket aag1091/netconfig-train-output):
  RUN_INFO.json, train_log.json, eval_base.json, eval_ft.json,
  generations_base.jsonl, generations_ft.jsonl, adapter/, merged/,
  DONE.json
In-job parse % uses ciscoconfparse2 directly; the authoritative scoring
(Batfish + intent-match on RAW generations) runs back home.
Publish bar (set by Avinash 2026-11-23): Batfish >= 95% and
intent-match >= 90% on raw, untruncated generations_ft.jsonl.
"""
import gc
import json
import re
import time
import urllib.request
from pathlib import Path

OUT = Path("/output/run2")
DS_REPO = "aag1091/netconfig-slm-dataset"
DS = f"https://huggingface.co/datasets/{DS_REPO}/resolve/main"
BASE = "Qwen/Qwen3-4B-Base"
MIN_TRAIN_ROWS = 4000
IM_END_ID = 151645          # <|im_end|> — ChatML turn end
ENDOFTEXT_ID = 151643       # <|endoftext|> — Qwen3-Base native eos
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 512
CFG = dict(lora_r=16, lora_alpha=32, lora_dropout=0.05,
           target=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj",
                   "up_proj", "down_proj"],
           bs=8, accum=2, epochs=3, lr=2e-4, warmup=0.03, max_len=1024)

KEY_TOKEN = re.compile(
    r"\b(?:vlan\s*\d+|\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?|GigabitEthernet\S+"
    r"|TenGigabitEthernet\S+|AS\s*\d+|\d{5})\b", re.I)


def log(*a):
    print("[job]", *a, flush=True)


def fetch(name, dest):
    urllib.request.urlretrieve(f"{DS}/{name}", dest)
    return [json.loads(l) for l in Path(dest).read_text().splitlines()
            if l.strip()]


def dataset_revision():
    """Current HEAD sha of the HF dataset repo (provenance for RUN_INFO)."""
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


def parse_ok(text):
    try:
        from ciscoconfparse2 import CiscoConfParse
        return len(CiscoConfParse(text.splitlines()).objs) > 0
    except Exception:
        return False


def prompt_of(intent):
    return f"<|im_start|>user\n{intent}<|im_end|>\n<|im_start|>assistant\n"


def to_pc(row):
    """TRL prompt/completion pair: loss applies to the completion only,
    and the completion ends with the ChatML stop token <|im_end|>."""
    return {"prompt": prompt_of(row["intent_nl"]),
            "completion": f"{row['config']}<|im_end|>"}


def run_eval(model, tok, rows, tag):
    import torch
    model.eval()
    gens, per = [], {}
    for row in rows:
        inputs = tok(prompt_of(row["intent_nl"]),
                     return_tensors="pt").to(model.device)
        with torch.no_grad():
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
        fam = per.setdefault(row["family"],
                             {"n": 0, "parse": 0, "cov": 0.0, "stop": 0})
        fam["n"] += 1
        fam["parse"] += int(parse_ok(gen))
        fam["cov"] += coverage(row["config"], gen)
        fam["stop"] += int(stopped_cleanly)
    summary = {f: {"n": s["n"],
                   "parse_ok_pct": round(100 * s["parse"] / s["n"], 1),
                   "mean_coverage": round(s["cov"] / s["n"], 3),
                   "stopped_cleanly_pct": round(100 * s["stop"] / s["n"], 1)}
               for f, s in sorted(per.items())}
    n = sum(s["n"] for s in per.values())
    overall = {"n": n,
               "parse_ok_pct": round(
                   100 * sum(s["parse"] for s in per.values()) / n, 1),
               "mean_coverage": round(
                   sum(s["cov"] for s in per.values()) / n, 3),
               "stopped_cleanly_pct": round(
                   100 * sum(s["stop"] for s in per.values()) / n, 1)}
    (OUT / f"generations_{tag}.jsonl").write_text(
        "\n".join(json.dumps(g) for g in gens) + "\n")
    (OUT / f"eval_{tag}.json").write_text(json.dumps(
        {"model": tag, "overall": overall, "per_family": summary}, indent=2))
    log(tag, "eval overall:", overall)
    return overall


def free(obj):
    import torch
    del obj
    gc.collect()
    torch.cuda.empty_cache()


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    run_info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                             time.gmtime()),
                "base": BASE, "cfg": CFG, "dataset": DS,
                "eos_ids": EOS_IDS, "script": "job_train_eval_v2.py"}
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log("output mount OK, RUN_INFO written")

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

    # Guard: never silently train on a stale/small dataset again.
    if len(train_rows) < MIN_TRAIN_ROWS:
        (OUT / "DONE.json").write_text(json.dumps(
            {"status": "aborted_stale_dataset",
             "dataset_sha": sha, "train_rows": len(train_rows),
             "test_rows": len(test_rows),
             "reason": f"train rows {len(train_rows)} < {MIN_TRAIN_ROWS}; "
                       "the full ~5,000-pair dataset must be published "
                       "before this run is launched"}, indent=2))
        log("ABORTED: stale dataset guard tripped; no training done")
        return

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE)
    # Make the ChatML stop token authoritative for training + generation.
    tok.eos_token = "<|im_end|>"

    # Stage 1: base model eval (bf16)
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16, device_map="auto")
    base_eval = run_eval(base_model, tok, test_rows, "base")
    free(base_model)

    # Stage 2: QLoRA train (prompt/completion => completion-only loss)
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    ds = Dataset.from_list([to_pc(r) for r in train_rows])
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=bnb, device_map="auto")
    peft_cfg = LoraConfig(r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"],
                          lora_dropout=CFG["lora_dropout"],
                          target_modules=CFG["target"], task_type="CAUSAL_LM")
    sft_cfg = SFTConfig(output_dir="/tmp/train_out",
                        per_device_train_batch_size=CFG["bs"],
                        gradient_accumulation_steps=CFG["accum"],
                        num_train_epochs=CFG["epochs"],
                        learning_rate=CFG["lr"], lr_scheduler_type="cosine",
                        warmup_ratio=CFG["warmup"], logging_steps=10,
                        save_strategy="no", bf16=True,
                        max_length=CFG["max_len"],
                        completion_only_loss=True, report_to=[])
    # processing_class=tok is load-bearing: TRL appends its processing
    # tokenizer's eos to prompt/completion examples. Passing the
    # overridden tokenizer (eos=<|im_end|>) keeps exactly one trailing
    # 151645; a fresh default tokenizer would append 151643 after it.
    # Verified in docs/train-v2-selfcheck.txt (TRL 0.25.1).
    trainer = SFTTrainer(model=model, args=sft_cfg, train_dataset=ds,
                         peft_config=peft_cfg, processing_class=tok)
    result = trainer.train()
    (OUT / "train_log.json").write_text(json.dumps(
        {"train_loss": result.training_loss,
         "log_history": trainer.state.log_history}, indent=2))
    adapter_dir = OUT / "adapter"
    trainer.save_model(str(adapter_dir))
    tok.save_pretrained(str(adapter_dir))
    log("adapter saved; train_loss:", result.training_loss)
    free(model)

    # Stage 3: merge on CPU, ship generation_config with the EOS fix,
    # save merged, eval fine-tuned on GPU
    from peft import PeftModel
    from transformers import GenerationConfig
    cpu_base = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16, device_map="cpu")
    merged_model = PeftModel.from_pretrained(
        cpu_base, str(adapter_dir)).merge_and_unload()
    gen_cfg = GenerationConfig(eos_token_id=EOS_IDS, do_sample=False,
                               pad_token_id=ENDOFTEXT_ID)
    merged_model.generation_config = gen_cfg
    merged_dir = Path("/tmp/merged")
    merged_model.save_pretrained(str(merged_dir))
    gen_cfg.save_pretrained(str(merged_dir))
    tok.save_pretrained(str(merged_dir))
    free(cpu_base)
    free(merged_model)
    merged_out = OUT / "merged"
    import shutil
    shutil.copytree(merged_dir, merged_out)
    log("merged model saved (generation_config.json eos:", EOS_IDS, ")")

    ft_model = AutoModelForCausalLM.from_pretrained(
        str(merged_dir), torch_dtype=torch.bfloat16, device_map="auto")
    ft_eval = run_eval(ft_model, tok, test_rows, "ft")
    free(ft_model)

    (OUT / "DONE.json").write_text(json.dumps(
        {"status": "completed",
         "elapsed_min": round((time.time() - t0) / 60, 1),
         "dataset_sha": sha,
         "base_eval": base_eval, "ft_eval": ft_eval,
         "train_loss": result.training_loss}, indent=2))
    log("ALL DONE in", round((time.time() - t0) / 60, 1), "min")


if __name__ == "__main__":
    main()
