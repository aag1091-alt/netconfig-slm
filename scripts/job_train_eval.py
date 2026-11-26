#!/usr/bin/env python3
"""HF Jobs entrypoint: QLoRA smoke train of Qwen3-4B-Base on NetConfig SLM
dataset v0.1, with base-vs-fine-tuned eval on the held-out test split.

Mirrors src/train/train_qlora.py + configs/train_qwen3_4b.yaml and
src/eval/evaluate.py, self-contained for a job container (no repo imports).

Outputs go to /output (mounted HF bucket aag1091/netconfig-train-output):
  RUN_INFO.json, train_log.json, eval_base.json, eval_ft.json,
  generations_base.jsonl, generations_ft.jsonl, adapter/, merged/,
  DONE.json
In-job parse % uses ciscoconfparse2 directly; the authoritative scoring
(Batfish + intent-match) runs back home on generations_ft.jsonl.
"""
import gc
import json
import re
import time
from pathlib import Path

OUT = Path("/output")
DS = "https://huggingface.co/datasets/aag1091/netconfig-slm-dataset/resolve/main"
BASE = "Qwen/Qwen3-4B-Base"
CFG = dict(lora_r=16, lora_alpha=32, lora_dropout=0.05,
           target=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj",
                   "up_proj", "down_proj"],
           bs=4, accum=4, epochs=3, lr=2e-4, warmup=0.03, max_len=1024)

KEY_TOKEN = re.compile(
    r"\b(?:vlan\s*\d+|\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?|GigabitEthernet\S+"
    r"|TenGigabitEthernet\S+|AS\s*\d+|\d{5})\b", re.I)


def log(*a):
    print("[job]", *a, flush=True)


def fetch(name, dest):
    import urllib.request
    urllib.request.urlretrieve(f"{DS}/{name}", dest)
    return [json.loads(l) for l in Path(dest).read_text().splitlines()
            if l.strip()]


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


def run_eval(model, tok, rows, tag):
    import torch
    model.eval()
    im_end = tok.convert_tokens_to_ids("<|im_end|>")
    gens, per = [], {}
    for row in rows:
        inputs = tok(prompt_of(row["intent_nl"]),
                     return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=512,
                                 do_sample=False, eos_token_id=im_end,
                                 pad_token_id=tok.eos_token_id)
        gen = tok.decode(out[0][inputs["input_ids"].shape[1]:],
                         skip_special_tokens=True).strip()
        gens.append({"id": row["id"], "family": row["family"],
                     "generated": gen})
        fam = per.setdefault(row["family"], {"n": 0, "parse": 0, "cov": 0.0})
        fam["n"] += 1
        fam["parse"] += int(parse_ok(gen))
        fam["cov"] += coverage(row["config"], gen)
    summary = {f: {"n": s["n"],
                   "parse_ok_pct": round(100 * s["parse"] / s["n"], 1),
                   "mean_coverage": round(s["cov"] / s["n"], 3)}
               for f, s in sorted(per.items())}
    n = sum(s["n"] for s in per.values())
    overall = {"n": n,
               "parse_ok_pct": round(
                   100 * sum(s["parse"] for s in per.values()) / n, 1),
               "mean_coverage": round(
                   sum(s["cov"] for s in per.values()) / n, 3)}
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
    (OUT / "RUN_INFO.json").write_text(json.dumps(
        {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "base": BASE, "cfg": CFG, "dataset": DS}, indent=2))
    log("output mount OK, RUN_INFO written")

    work = Path("/tmp/work")
    work.mkdir(exist_ok=True)
    train_rows = fetch("train.jsonl", work / "train.jsonl")
    test_rows = fetch("test.jsonl", work / "test.jsonl")
    log(f"rows: {len(train_rows)} train / {len(test_rows)} test")

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(BASE)

    # Stage 1: base model eval (bf16)
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16, device_map="auto")
    base_eval = run_eval(base_model, tok, test_rows, "base")
    free(base_model)

    # Stage 2: QLoRA train (mirrors src/train/train_qlora.py)
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    def to_text(r):
        return {"text": f"<|im_start|>user\n{r['intent_nl']}<|im_end|>\n"
                        f"<|im_start|>assistant\n{r['config']}<|im_end|>"}

    ds = Dataset.from_list([to_text(r) for r in train_rows])
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
                        max_length=CFG["max_len"], report_to=[])
    trainer = SFTTrainer(model=model, args=sft_cfg, train_dataset=ds,
                         peft_config=peft_cfg)
    result = trainer.train()
    (OUT / "train_log.json").write_text(json.dumps(
        {"train_loss": result.training_loss,
         "log_history": trainer.state.log_history}, indent=2))
    adapter_dir = OUT / "adapter"
    trainer.save_model(str(adapter_dir))
    tok.save_pretrained(str(adapter_dir))
    log("adapter saved; train_loss:", result.training_loss)
    free(model)

    # Stage 3: merge on CPU, save merged, eval fine-tuned on GPU
    from peft import PeftModel
    cpu_base = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.bfloat16, device_map="cpu")
    merged_model = PeftModel.from_pretrained(
        cpu_base, str(adapter_dir)).merge_and_unload()
    merged_dir = Path("/tmp/merged")
    merged_model.save_pretrained(str(merged_dir))
    tok.save_pretrained(str(merged_dir))
    free(cpu_base)
    free(merged_model)
    merged_out = OUT / "merged"
    import shutil
    shutil.copytree(merged_dir, merged_out)
    log("merged model saved")

    ft_model = AutoModelForCausalLM.from_pretrained(
        str(merged_dir), torch_dtype=torch.bfloat16, device_map="auto")
    ft_eval = run_eval(ft_model, tok, test_rows, "ft")
    free(ft_model)

    (OUT / "DONE.json").write_text(json.dumps(
        {"elapsed_min": round((time.time() - t0) / 60, 1),
         "base_eval": base_eval, "ft_eval": ft_eval,
         "train_loss": result.training_loss}, indent=2))
    log("ALL DONE in", round((time.time() - t0) / 60, 1), "min")


if __name__ == "__main__":
    main()
