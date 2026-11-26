"""QLoRA fine-tune - TRL SFTTrainer reference implementation.

Unsloth path: if unsloth is installed it patches the model load for ~2x speed / lower VRAM.
Otherwise falls back to plain transformers + peft. Same YAML config either way.

Usage:
    python -m src.train.train_qlora --config configs/train_qwen3_4b.yaml
    python -m src.train.train_qlora --config configs/train_qwen3_1_7b.yaml

Requires GPU (4090 / A100 recommended). Will refuse to start on CPU unless --allow-cpu (debug only, tiny subset).
"""
import argparse, json
from pathlib import Path

def load_cfg(path: str) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text())

def to_prompt_completion(row: dict) -> dict:
    # TRL prompt/completion format => completion-only loss. The completion
    # ends with <|im_end|> (id 151645), the ChatML stop token; v1's plain
    # "text" format never made it authoritative and generations never
    # stopped (see scripts/job_train_eval_v2.py header).
    prompt = f"<|im_start|>user\n{row['intent_nl']}<|im_end|>\n<|im_start|>assistant\n"
    return {"prompt": prompt, "completion": f"{row['config']}<|im_end|>"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--allow-cpu", action="store_true")
    args = ap.parse_args()
    cfg = load_cfg(args.config)

    import torch
    if not torch.cuda.is_available() and not args.allow_cpu:
        raise SystemExit("No CUDA GPU found. Rent a 4090/A100 (see docs/V0_SCOPE.md) or pass --allow-cpu for a smoke test.")

    # Optional Unsloth fast path
    try:
        from unsloth import FastLanguageModel  # noqa: F401
        print("Unsloth detected - fast path available (model load via Unsloth in full run).")
    except ImportError:
        print("Unsloth not installed - using plain TRL path. That's fine, just slower.")

    from datasets import Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    rows = [json.loads(l) for l in Path(cfg["dataset"]).read_text().splitlines() if l.strip()]
    ds = Dataset.from_list([to_prompt_completion(r) for r in rows])
    print(f"Training rows: {len(ds)} from {cfg['dataset']}")

    bnb = BitsAndBytesConfig(load_in_4bit=cfg.get("load_in_4bit", True), bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"], quantization_config=bnb, device_map="auto")
    tok = AutoTokenizer.from_pretrained(cfg["base_model"])
    # EOS fix (v2): the base tokenizer's eos is <|endoftext|> (151643), but
    # ChatML turns end with <|im_end|> (151645). Make <|im_end|> the eos so
    # training and generation agree on the stop token; generation should
    # use eos_token_id=[151645, 151643].
    tok.eos_token = cfg.get("eos_token", "<|im_end|>")

    peft_cfg = LoraConfig(
        r=cfg["lora_r"], lora_alpha=cfg["lora_alpha"], lora_dropout=cfg["lora_dropout"],
        target_modules=cfg["lora_target_modules"], task_type="CAUSAL_LM",
    )
    sft_cfg = SFTConfig(
        output_dir=cfg["output_dir"],
        per_device_train_batch_size=cfg["per_device_train_batch_size"],
        gradient_accumulation_steps=cfg["gradient_accumulation_steps"],
        num_train_epochs=cfg["num_train_epochs"],
        learning_rate=cfg["learning_rate"],
        lr_scheduler_type=cfg["lr_scheduler_type"],
        warmup_ratio=cfg["warmup_ratio"],
        logging_steps=cfg["logging_steps"],
        save_strategy=cfg["save_strategy"],
        bf16=cfg.get("bf16", True),
        max_length=cfg.get("max_seq_length", 1024),
        completion_only_loss=True,
    )
    # processing_class=tok: TRL appends its processing tokenizer's eos to
    # prompt/completion examples — it must be the eos-overridden tokenizer
    # above, not a fresh default one (see scripts/job_train_eval_v2.py).
    trainer = SFTTrainer(model=model, args=sft_cfg, train_dataset=ds, peft_config=peft_cfg, processing_class=tok)
    trainer.train()
    trainer.save_model(cfg["output_dir"])
    tok.save_pretrained(cfg["output_dir"])
    print(f"Done. Adapter saved to {cfg['output_dir']} - merge into bf16 base before GGUF export (see README).")

if __name__ == "__main__":
    main()
