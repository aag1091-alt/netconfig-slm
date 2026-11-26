#!/usr/bin/env python3
"""Local CPU self-check for job_probe_v5.py (attempt 5).

Two parts:

(1) DATA PIPELINE — unchanged from attempt 4, so a light re-run:
    v5's select_subset / select_heldout / verify_endings against the
    local data/v1.0 files with the cached Qwen3 tokenizer (via the
    `tokenizers` lib behind an HF-style shim). Verifies 256 rows,
    exactly 102 shortest-quartile rows, subset + held-out id lists
    IDENTICAL to attempt 4's in-job selection (outputs/hf-probe-4/
    RUN_INFO.json), and endings all_ok on subset + held-out refs.

(2) modules_to_save=["lm_head"] — the attempt-5 change:
    * LoraConfig accepts modules_to_save=["lm_head"].
    * A Qwen3-4B model built from the cached config on the meta
      device wraps cleanly with get_peft_model: observe the tie
      behavior PEFT produces (config tie flag; whether the effective
      lm_head weight IS the embed_tokens weight; requires_grad) via
      job_probe_v5.tie_report, and confirm the lm_head parameters
      are trainable (~389M) alongside the LoRA params.

Run with ~/trlcheck-venv/bin/python (peft 0.18.1 / transformers
4.57.6 / torch 2.5.1+cpu — the same versions the job resolves).
Log is written to docs/train-probe-v5-selfcheck.txt.
"""
import glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/home/hatch/workspace/projects/small-ai-model-for-networking/netconfig-slm")
sys.path.insert(0, str(ROOT / "scripts"))
import job_probe_v5 as P5  # noqa: E402

from tokenizers import Tokenizer  # noqa: E402

SNAP = glob.glob(str(Path.home() / ".cache/huggingface/hub/"
                     "models--Qwen--Qwen3-4B-Base/snapshots/*"))[0]
TOK_PATH = str(Path(SNAP) / "tokenizer.json")
CFG_PATH = str(Path(SNAP) / "config.json")


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
            (ROOT / "docs/train-probe-v5-selfcheck.txt").write_text(
                "\n".join(lines) + "\n")
            raise SystemExit("\n".join(lines))

    # ---------------- (1) data pipeline (identical to v4) ----------
    tok = TokShim(TOK_PATH)
    probe_ids = tok("test<|im_end|>")["input_ids"]
    check("tokenizer shim", probe_ids[-1] == 151645,
          f"'test<|im_end|>' -> {probe_ids}")

    train_rows = [json.loads(l) for l in
                  (ROOT / "data/v1.0/train.jsonl").read_text().splitlines()
                  if l.strip()]
    test_rows = [json.loads(l) for l in
                 (ROOT / "data/v1.0/test.jsonl").read_text().splitlines()
                 if l.strip()]
    lines.append(f"local data: {len(train_rows)} train / "
                 f"{len(test_rows)} test")

    subset, counts, short_counts, threshold = P5.select_subset(
        train_rows, tok)
    check("subset size", len(subset) == 256, f"{len(subset)} rows")
    check("subset family counts", len(counts) == 6, f"{counts}")
    n_short = sum(short_counts.values())
    check("short-quartile rows", n_short == 102,
          f"{n_short} rows, per-family {short_counts}, "
          f"threshold {threshold} tokens")

    run4 = json.loads((ROOT / "outputs/hf-probe-4/RUN_INFO.json")
                      .read_text())
    check("subset identical to attempt 4",
          [r["id"] for r in subset] == run4["subset_ids"],
          "id list (incl. order) matches run4 RUN_INFO subset_ids")
    check("subset counts identical to attempt 4",
          counts == run4["subset_family_counts"]
          and short_counts == run4["subset_short_family_counts"]
          and threshold == run4["short_quartile_threshold_tokens"],
          f"counts {counts}, threshold {threshold}")

    heldout = P5.select_heldout(test_rows)
    check("heldout identical to attempt 4",
          [r["id"] for r in heldout] == run4["heldout_ids"],
          f"24 rows: {[r['id'] for r in heldout][:4]}...")
    check("heldout families",
          Counter(r["family"] for r in heldout) == Counter(
              {f: 4 for f in counts}), "4 per family")

    end_train = P5.verify_endings(tok, subset, "train_subset")
    check("ending check (train subset)", end_train["all_ok"],
          json.dumps(end_train))
    end_held = P5.verify_endings(tok, heldout, "heldout_refs")
    check("ending check (held-out refs)", end_held["all_ok"],
          json.dumps(end_held))

    # ---------------- (2) modules_to_save=["lm_head"] --------------
    import torch  # noqa: F401  (meta-device build below)
    from peft import LoraConfig, get_peft_model
    from transformers import AutoConfig, AutoModelForCausalLM

    cfg = LoraConfig(r=P5.CFG["lora_r"], lora_alpha=P5.CFG["lora_alpha"],
                     lora_dropout=P5.CFG["lora_dropout"],
                     target_modules=P5.CFG["target"],
                     modules_to_save=["lm_head"],
                     task_type="CAUSAL_LM")
    check("LoraConfig accepts modules_to_save",
          list(cfg.modules_to_save) == ["lm_head"],
          f"modules_to_save={cfg.modules_to_save}")

    # Meta device is load-bearing: a real CPU build of the 4B model
    # is ~8 GB in bf16 and OOM-kills this VM (first self-check run).
    with torch.device("meta"):
        model = AutoModelForCausalLM.from_config(
            AutoConfig.from_pretrained(CFG_PATH))
    check("base config tie flag",
          model.config.tie_word_embeddings is True,
          f"tie_word_embeddings={model.config.tie_word_embeddings}, "
          f"vocab={model.config.vocab_size}, "
          f"hidden={model.config.hidden_size}")

    base_tie = P5.tie_report(model)
    check("tie_report on raw base", base_tie["error"] is None
          and base_tie["active_weights_tied"] is True,
          json.dumps(base_tie))

    with torch.device("meta"):
        peft_model = get_peft_model(model, cfg)
    tie = P5.tie_report(peft_model)
    check("tie_report on PeftModel", tie["error"] is None,
          json.dumps(tie))
    lines.append(f"tie after wrap: {json.dumps(tie)}")
    lines.append(f"peft_config.modules_to_tie after wrap: "
                 f"{getattr(cfg, 'modules_to_tie', None)}")

    eff = P5.active_saved_module(
        peft_model.get_base_model().get_output_embeddings())
    check("effective lm_head module",
          tuple(eff.weight.shape) == (151936, 2560),
          f"{type(eff).__name__} weight shape "
          f"{tuple(eff.weight.shape)}, "
          f"requires_grad={eff.weight.requires_grad}")
    check("lm_head weight requires grad",
          eff.weight.requires_grad is True,
          "the saved head copy is trainable")

    trainable = [(n, p.numel()) for n, p in
                 peft_model.named_parameters() if p.requires_grad]
    head_params = sum(n_ for name, n_ in trainable
                      if "lm_head" in name)
    total_trainable = sum(n_ for _, n_ in trainable)
    check("trainable param census",
          head_params == 151936 * 2560 and total_trainable > head_params,
          f"lm_head trainable={head_params:,} "
          f"({head_params / 1e6:.0f}M), total trainable="
          f"{total_trainable:,} across {len(trainable)} tensors")

    lines.append("ALL PROBE V5 SELF-CHECKS PASSED")
    out = "\n".join(lines) + "\n"
    (ROOT / "docs/train-probe-v5-selfcheck.txt").write_text(out)
    print(out)


if __name__ == "__main__":
    main()
