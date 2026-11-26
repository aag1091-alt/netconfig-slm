#!/usr/bin/env python3
"""Local CPU self-check for job_probe_v8.py (attempt 8).

Five parts:

(1) DATA PIPELINE — unchanged from attempt 4 (reuse of the v5/v7
    self-check logic): v8's select_subset / select_heldout /
    verify_endings against the local data/v1.0 files with the cached
    Qwen3 tokenizer (via the `tokenizers` lib behind an HF-style
    shim). Verifies 256 rows, exactly 102 shortest-quartile rows,
    subset + held-out id lists IDENTICAL to attempt 4's in-job
    selection (outputs/hf-probe-4/RUN_INFO.json), endings all_ok.

(2) CONFIG GUARDS — CFG carries attempt 4's values (epochs=1,
    lr=2e-4, warmup=0.03) with target == attempt 4's list +
    "lm_head"; build_trainer's source sets logging_steps=2 and the
    probe8 output dir; an AST walk over the module proves NO
    LoraConfig call passes modules_to_save and NO SFTConfig call
    passes gradient_checkpointing (attempt 8's two exclusions).

(3) UNTIE + TARGETING (meta device; a real CPU build of the 4B
    model OOM-kills this VM — see v5 self-check):
    * tied sanity: the cached config (tie=True) builds a model
      whose lm_head.weight IS embed_tokens.weight.
    * P8.untied_config(cached config) flips the flag to False and
      the built model's lm_head.weight is a DISTINCT tensor.
    * LoraConfig built exactly as main() builds it wraps the
      untied model; lora_target_names finds 253 adapted modules
      (36 layers x 7 + lm_head), has_lm_head True,
      has_embed_tokens False; the lm_head LoRA factors have shapes
      A (16, 2560) / B (151936, 16); tie_report on the PeftModel
      shows the untie intact (config False, active/original not
      tied). If PEFT refuses to target lm_head even untied, this
      part FAILS and the job is NOT submitted.

(4) UNTIE_CHECK FUNCTION on tiny CPU Qwen3 models: (a) lm_head
    scrambled -> the check detects the divergence, copies the
    embedding values in, and reports max_abs_diff_after == 0;
    (b) lm_head already equal -> no copy, diff 0.

(5) ANALYTIC ADAPTER ROW vs TRUE MERGE on a tiny CPU model: with
    the lm_head LoRA factors randomized (PEFT zero-inits B, which
    would make the identity vacuous), P8.adapter_merged_lm_head_row
    must match merge_and_unload's actual lm_head row, and
    embed_movement on the merged model must read exactly 0.

Run with ~/trlcheck-venv/bin/python (peft 0.18.1 / transformers
4.57.6 / trl 0.25.1 / torch 2.5.1+cpu — the versions the job
resolves). Log is written to docs/train-probe-v8-selfcheck.txt.
"""
import ast
import glob
import inspect
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/home/hatch/workspace/projects/small-ai-model-for-networking/netconfig-slm")
sys.path.insert(0, str(ROOT / "scripts"))
import job_probe_v8 as P8  # noqa: E402

from tokenizers import Tokenizer  # noqa: E402

SNAP = glob.glob(str(Path.home() / ".cache/huggingface/hub/"
                     "models--Qwen--Qwen3-4B-Base/snapshots/*"))[0]
TOK_PATH = str(Path(SNAP) / "tokenizer.json")
CFG_PATH = str(Path(SNAP) / "config.json")
LOG_PATH = ROOT / "docs/train-probe-v8-selfcheck.txt"


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

    subset, counts, short_counts, threshold = P8.select_subset(
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

    heldout = P8.select_heldout(test_rows)
    check("heldout identical to attempt 4",
          [r["id"] for r in heldout] == run4["heldout_ids"],
          f"24 rows: {[r['id'] for r in heldout][:4]}...")
    check("heldout families",
          Counter(r["family"] for r in heldout) == Counter(
              {f: 4 for f in counts}), "4 per family")

    end_train = P8.verify_endings(tok, subset, "train_subset")
    check("ending check (train subset)", end_train["all_ok"],
          json.dumps(end_train))
    end_held = P8.verify_endings(tok, heldout, "heldout_refs")
    check("ending check (held-out refs)", end_held["all_ok"],
          json.dumps(end_held))

    # ---------------- (2) config guards ----------------------------
    check("CFG values",
          P8.CFG["epochs"] == 1 and P8.CFG["lr"] == 2e-4
          and P8.CFG["warmup"] == 0.03,
          f"epochs={P8.CFG['epochs']}, lr={P8.CFG['lr']}, "
          f"warmup={P8.CFG['warmup']}")
    check("CFG target = attempt 4 list + lm_head",
          P8.CFG["target"] == ["q_proj", "k_proj", "v_proj", "o_proj",
                               "gate_proj", "up_proj", "down_proj",
                               "lm_head"],
          f"target={P8.CFG['target']}")
    bt_src = inspect.getsource(P8.build_trainer)
    check("build_trainer guards",
          "logging_steps=2" in bt_src
          and '"/tmp/probe8_train_out"' in bt_src,
          "logging_steps=2, probe8 output dir")
    tree = ast.parse(inspect.getsource(P8))
    lora_kwargs, sft_kwargs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(
                fn, "attr", None)
            if name == "LoraConfig":
                lora_kwargs |= {kw.arg for kw in node.keywords}
            elif name == "SFTConfig":
                sft_kwargs |= {kw.arg for kw in node.keywords}
    check("no modules_to_save in any LoraConfig call",
          "modules_to_save" not in lora_kwargs,
          f"LoraConfig kwargs seen: {sorted(lora_kwargs)}")
    check("no gradient_checkpointing in any SFTConfig call",
          "gradient_checkpointing" not in sft_kwargs,
          f"SFTConfig kwargs seen: {sorted(sft_kwargs)}")

    # ---------------- (3) untie + targeting (meta device) ----------
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoConfig, AutoModelForCausalLM

    tied_cfg = AutoConfig.from_pretrained(CFG_PATH)
    check("cached config ties embeddings",
          tied_cfg.tie_word_embeddings is True,
          f"tie_word_embeddings={tied_cfg.tie_word_embeddings}, "
          f"vocab={tied_cfg.vocab_size}, "
          f"hidden={tied_cfg.hidden_size}, "
          f"layers={tied_cfg.num_hidden_layers}")
    with torch.device("meta"):
        tied_model = AutoModelForCausalLM.from_config(tied_cfg)
    check("tied build: lm_head IS embed_tokens (sanity)",
          tied_model.get_output_embeddings().weight
          is tied_model.get_input_embeddings().weight,
          "weight identity holds under tie=True")
    del tied_model

    untied_cfg = P8.untied_config(CFG_PATH)
    check("untied_config flips the flag",
          untied_cfg.tie_word_embeddings is False,
          f"tie_word_embeddings={untied_cfg.tie_word_embeddings}")
    with torch.device("meta"):
        model = AutoModelForCausalLM.from_config(untied_cfg)
    check("untied build: lm_head is a DISTINCT tensor",
          model.get_output_embeddings().weight
          is not model.get_input_embeddings().weight,
          "weight identity broken under tie=False")

    cfg = LoraConfig(r=P8.CFG["lora_r"], lora_alpha=P8.CFG["lora_alpha"],
                     lora_dropout=P8.CFG["lora_dropout"],
                     target_modules=P8.CFG["target"],
                     task_type="CAUSAL_LM")
    check("LoraConfig built as in main()",
          set(cfg.target_modules) == set(P8.CFG["target"])
          and not cfg.modules_to_save,
          f"target_modules={cfg.target_modules}, "
          f"modules_to_save={cfg.modules_to_save}")
    try:
        with torch.device("meta"):
            peft_model = get_peft_model(model, cfg)
    except Exception as e:  # noqa: BLE001 - refusal is a STOP signal
        check("PEFT accepts lm_head as a LoRA target (untied)", False,
              f"get_peft_model raised: {type(e).__name__}: {e}")
        raise
    check("PEFT accepts lm_head as a LoRA target (untied)", True,
          "get_peft_model wrapped the untied model")

    found = P8.lora_target_names(peft_model)
    check("LoRA targets found",
          found["count"] == tied_cfg.num_hidden_layers * 7 + 1
          and found["has_lm_head"] and not found["has_embed_tokens"],
          json.dumps(found))
    head_mod = None
    for name, mod in peft_model.named_modules():
        if name == "lm_head" or name.endswith(".lm_head"):
            head_mod = mod
    a_shape = tuple(head_mod.lora_A["default"].weight.shape)
    b_shape = tuple(head_mod.lora_B["default"].weight.shape)
    check("lm_head LoRA factor shapes",
          a_shape == (P8.CFG["lora_r"], tied_cfg.hidden_size)
          and b_shape == (tied_cfg.vocab_size, P8.CFG["lora_r"]),
          f"lora_A {a_shape}, lora_B {b_shape}, "
          f"module {type(head_mod).__name__}")
    tie = P8.tie_report(peft_model)
    check("tie_report on PeftModel (untie intact)",
          tie["error"] is None
          and tie["tie_word_embeddings_config"] is False
          and tie["active_weights_tied"] is False
          and tie["original_weights_tied"] is False,
          json.dumps(tie))
    del peft_model, model

    # ---------------- (4) untie_check on tiny CPU models -----------
    from transformers import Qwen3Config

    def tiny_model():
        c = Qwen3Config(vocab_size=512, hidden_size=32,
                        num_hidden_layers=1, num_attention_heads=2,
                        num_key_value_heads=1, intermediate_size=64,
                        tie_word_embeddings=False)
        return AutoModelForCausalLM.from_config(c)

    torch.manual_seed(0)
    m1 = tiny_model()
    with torch.no_grad():
        m1.get_output_embeddings().weight.copy_(
            torch.randn_like(m1.get_output_embeddings().weight))
    rec1 = P8.untie_check(m1, "tiny_scrambled")
    check("untie_check repairs a diverged lm_head",
          rec1["error"] is None and rec1["same_tensor"] is False
          and (rec1["max_abs_diff_before"] or 0) > 0.0
          and rec1["values_copied_from_embed"] is True
          and rec1["max_abs_diff_after"] == 0.0,
          json.dumps(rec1))
    m2 = tiny_model()
    with torch.no_grad():
        m2.get_output_embeddings().weight.copy_(
            m2.get_input_embeddings().weight)
    rec2 = P8.untie_check(m2, "tiny_equal")
    check("untie_check leaves an equal lm_head untouched",
          rec2["error"] is None and rec2["same_tensor"] is False
          and rec2["max_abs_diff_before"] == 0.0
          and rec2["values_copied_from_embed"] is False,
          json.dumps(rec2))
    del m1, m2

    # ---------------- (5) analytic row vs true merge ---------------
    torch.manual_seed(1)
    m3 = tiny_model()
    base_embed_row = P8.input_embedding_row(m3, 7)
    base_head_row = P8.output_embedding_row(m3, 7)
    tiny_lora = LoraConfig(r=4, lora_alpha=8, lora_dropout=0.0,
                           target_modules=["lm_head", "q_proj"],
                           task_type="CAUSAL_LM")
    peft3 = get_peft_model(m3, tiny_lora)
    # PEFT zero-inits lora_B, which would make the merge identity
    # vacuous — randomize both factors on lm_head first.
    for name, mod in peft3.named_modules():
        if (name == "lm_head" or name.endswith(".lm_head")) \
                and hasattr(mod, "lora_A"):
            with torch.no_grad():
                mod.lora_A["default"].weight.copy_(
                    torch.randn_like(mod.lora_A["default"].weight)
                    * 0.1)
                mod.lora_B["default"].weight.copy_(
                    torch.randn_like(mod.lora_B["default"].weight)
                    * 0.1)
    analytic = P8.adapter_merged_lm_head_row(peft3, 7)
    move = P8.lm_head_movement_adapter(base_head_row, peft3, row=7)
    check("adapter movement check runs, delta > 0",
          "error" not in move and move["delta_l2"] > 0.0,
          json.dumps(move))
    merged3 = peft3.merge_and_unload()
    true_row = P8.output_embedding_row(merged3, 7)
    max_diff = float((analytic - true_row).abs().max())
    check("analytic merged row == true merge row",
          max_diff < 1e-4, f"max abs diff {max_diff:.2e}")
    emb = P8.embed_movement(base_embed_row, merged3,
                            "merged_embed_tokens", row=7)
    check("embed row unmoved by the merge",
          "error" not in emb and emb["delta_l2"] == 0.0,
          json.dumps(emb))
    del peft3, merged3, m3

    lines.append("ALL PROBE V8 SELF-CHECKS PASSED")
    out = "\n".join(lines) + "\n"
    LOG_PATH.write_text(out)
    print(out)


if __name__ == "__main__":
    main()
