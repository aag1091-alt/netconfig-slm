#!/usr/bin/env python3
"""Local CPU self-check for job_train_fullft_v3.py (run 3, full FT).

Three parts:

(1) DATA TRANSFORM — the job module's add_terminator /
    completion_text / verify_endings run against the LOCAL
    data/v1.0 files (the same rows the Hub serves at sha ecf3d5a)
    with the cached Qwen3 tokenizer (via the `tokenizers` lib
    behind an HF-style shim, the probe self-check pattern):
    ALL 4,496 train completions and ALL 252 test references must
    verify (endings all_ok), and every transformed train
    completion must end with the `end` line as text.

(2) TRAINABLE-PARAMETER CENSUS (meta device; a real CPU build of
    the 4B model OOM-kills this VM — see the probe self-checks):
    build Qwen3ForCausalLM from the cached config under
    torch.device("meta"); total params must be ~4.02B, ALL of
    them trainable by default (full FT — no PEFT freezing),
    tie_word_embeddings True, and lm_head.weight must BE
    embed_tokens.weight (tied — run 3 does not untie).

(3) CONFIG — build the SFTConfig exactly as the job's main()
    does (values from the module's CFG) and assert the
    hyperparameters land as specified (lr 1e-5, cosine, warmup
    0.05, wd 0.0, clip 1.0, epochs 2, bs 4 x accum 4, max_length
    1536, grad ckpt + use_reentrant False, completion-only loss);
    expected optimizer steps = ceil(4496/16) * 2 = 562.

Run with ~/trlcheck-venv/bin/python (transformers 4.57.6 /
trl 0.25.1 / torch 2.5.1+cpu — the versions the job resolves).
Log is written to docs/train-fullft-v3-selfcheck.txt.
"""
import glob
import json
import math
import sys
from pathlib import Path

ROOT = Path("/home/hatch/workspace/projects/small-ai-model-for-networking"
            "/netconfig-slm")
sys.path.insert(0, str(ROOT / "scripts"))
import job_train_fullft_v3 as FT  # noqa: E402

from tokenizers import Tokenizer  # noqa: E402

SNAP = glob.glob(str(Path.home() / ".cache/huggingface/hub/"
                     "models--Qwen--Qwen3-4B-Base/snapshots/*"))[0]
TOK_PATH = str(Path(SNAP) / "tokenizer.json")
CFG_PATH = str(Path(SNAP) / "config.json")
LOG_PATH = ROOT / "docs/train-fullft-v3-selfcheck.txt"


class TokShim:
    """Minimal HF-AutoTokenizer-style callable over `tokenizers`."""

    def __init__(self, path):
        self.t = Tokenizer.from_file(path)

    def __call__(self, texts, add_special_tokens=False):
        if isinstance(texts, str):
            enc = self.t.encode(texts,
                                add_special_tokens=add_special_tokens)
            return {"input_ids": enc.ids}
        encs = self.t.encode_batch(texts,
                                   add_special_tokens=add_special_tokens)
        return {"input_ids": [e.ids for e in encs]}

    def decode(self, ids, skip_special_tokens=False):
        return self.t.decode(ids,
                             skip_special_tokens=skip_special_tokens)


def main():
    lines = []

    def check(name, ok, detail=""):
        lines.append(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
        print(lines[-1], flush=True)
        if not ok:
            LOG_PATH.write_text("\n".join(lines) + "\n")
            raise SystemExit("\n".join(lines))

    # ---------------- (1) data transform --------------------------
    tok = TokShim(TOK_PATH)
    probe_ids = tok("test<|im_end|>")["input_ids"]
    check("tokenizer shim", probe_ids[-1] == 151645,
          f"'test<|im_end|>' -> {probe_ids}")

    train_rows = [json.loads(l) for l in
                  (ROOT / "data/v1.0/train.jsonl").read_text()
                  .splitlines() if l.strip()]
    test_rows = [json.loads(l) for l in
                 (ROOT / "data/v1.0/test.jsonl").read_text()
                 .splitlines() if l.strip()]
    lines.append(f"local data: {len(train_rows)} train / "
                 f"{len(test_rows)} test")
    check("row counts", len(train_rows) == 4496
          and len(test_rows) == 252,
          f"{len(train_rows)} train, {len(test_rows)} test")

    end_train = FT.verify_endings(tok, train_rows, "train")
    check("train endings (all 4,496)", end_train["all_ok"]
          and end_train["checked"] == 4496,
          f"checked={end_train['checked']} all_ok={end_train['all_ok']} "
          f"bad={end_train['bad']}")
    end_test = FT.verify_endings(tok, test_rows, "test_refs")
    check("test-ref endings (all 252)", end_test["all_ok"]
          and end_test["checked"] == 252,
          f"checked={end_test['checked']} all_ok={end_test['all_ok']} "
          f"bad={end_test['bad']}")

    text_ok = all(
        FT.add_terminator(r["config"]).endswith("\nend")
        for r in train_rows)
    sample = FT.completion_text(train_rows[0])
    check("terminator text", text_ok
          and sample.endswith("<|im_end|>")
          and sample[: -len("<|im_end|>")].endswith("\nend"),
          f"sample completion tail: ...{sample[-30:]!r}")

    # ---------------- (2) meta-device census ----------------------
    import torch
    from transformers import AutoConfig, AutoModelForCausalLM
    config = AutoConfig.from_pretrained(CFG_PATH)
    with torch.device("meta"):
        model = AutoModelForCausalLM.from_config(config)
    total = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters()
                    if p.requires_grad)
    embed = model.get_input_embeddings().weight
    head = model.get_output_embeddings().weight
    check("param census ~4.02B", 4.01e9 < total < 4.03e9,
          f"total={total:,} trainable={trainable:,}")
    check("all params trainable (full FT)", trainable == total,
          f"{trainable:,}/{total:,}")
    check("embeddings included", embed.numel() == 151936 * 2560,
          f"embed_tokens params={embed.numel():,}")
    check("tied head (not untied)",
          bool(config.tie_word_embeddings) and head is embed,
          f"tie_word_embeddings={config.tie_word_embeddings}, "
          f"lm_head.weight is embed_tokens.weight: {head is embed}")
    del model

    # ---------------- (3) SFTConfig -------------------------------
    import inspect
    from trl import SFTConfig
    C = FT.CFG
    # bf16 is the ONE field substituted locally: TrainingArguments
    # refuses bf16=True on this CPU-only venv ("Your setup doesn't
    # support bf16/gpu"). The job itself passes bf16=True on an
    # A100 — asserted from the job source just below.
    sft_cfg = SFTConfig(output_dir="/tmp/selfcheck_out",
                        per_device_train_batch_size=C["bs"],
                        gradient_accumulation_steps=C["accum"],
                        num_train_epochs=C["epochs"],
                        learning_rate=C["lr"],
                        lr_scheduler_type="cosine",
                        warmup_ratio=C["warmup"],
                        weight_decay=C["weight_decay"],
                        max_grad_norm=C["max_grad_norm"],
                        logging_steps=C["logging_steps"],
                        save_strategy="no", bf16=False,
                        max_length=C["max_len"],
                        gradient_checkpointing=C["grad_ckpt"],
                        gradient_checkpointing_kwargs={
                            "use_reentrant": False},
                        completion_only_loss=True, report_to=[])
    check("job passes bf16=True (source)",
          "bf16=True" in inspect.getsource(FT.main),
          "main() SFTConfig call carries bf16=True (substituted "
          "False in the local object build: CPU-only venv)")
    ok = (sft_cfg.learning_rate == 1e-5
          and sft_cfg.lr_scheduler_type == "cosine"
          and abs(sft_cfg.warmup_ratio - 0.05) < 1e-12
          and sft_cfg.weight_decay == 0.0
          and sft_cfg.max_grad_norm == 1.0
          and sft_cfg.num_train_epochs == 2
          and sft_cfg.per_device_train_batch_size == 4
          and sft_cfg.gradient_accumulation_steps == 4
          and sft_cfg.max_length == 1536
          and sft_cfg.gradient_checkpointing is True
          and sft_cfg.gradient_checkpointing_kwargs
          .get("use_reentrant") is False
          and sft_cfg.completion_only_loss is True)
    check("SFTConfig hyperparameters", ok,
          f"lr={sft_cfg.learning_rate} sched={sft_cfg.lr_scheduler_type} "
          f"warmup={sft_cfg.warmup_ratio} wd={sft_cfg.weight_decay} "
          f"clip={sft_cfg.max_grad_norm} epochs={sft_cfg.num_train_epochs} "
          f"bs={sft_cfg.per_device_train_batch_size}x"
          f"{sft_cfg.gradient_accumulation_steps} "
          f"max_len={sft_cfg.max_length} "
          f"ckpt={sft_cfg.gradient_checkpointing} "
          f"{sft_cfg.gradient_checkpointing_kwargs} "
          f"completion_only={sft_cfg.completion_only_loss}")
    steps = math.ceil(len(train_rows) / (C["bs"] * C["accum"])) \
        * C["epochs"]
    check("expected optimizer steps", steps == 562, f"{steps}")

    lines.append("ALL CHECKS PASSED")
    LOG_PATH.write_text("\n".join(lines) + "\n")
    print(f"log written to {LOG_PATH}")


if __name__ == "__main__":
    main()
