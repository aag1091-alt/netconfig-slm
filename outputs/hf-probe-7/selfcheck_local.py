#!/usr/bin/env python3
"""Local CPU self-check for job_probe_v7.py (attempt 7).

Three parts:

(1) DATA PIPELINE — unchanged from attempt 4 (reuse of the v5
    self-check's logic): v7's select_subset / select_heldout /
    verify_endings against the local data/v1.0 files with the cached
    Qwen3 tokenizer (via the `tokenizers` lib behind an HF-style
    shim). Verifies 256 rows, exactly 102 shortest-quartile rows,
    subset + held-out id lists IDENTICAL to attempt 4's in-job
    selection (outputs/hf-probe-4/RUN_INFO.json), endings all_ok.

(2) CONFIG GUARDS — CFG carries epochs=1, lr=2e-4, head_lr=1e-5,
    warmup=0.1; build_trainer's source sets logging_steps=2,
    max_grad_norm=1.0, gradient_checkpointing=True and returns the
    HeadSplit trainer class.

(3) HEAD + TWO-TIER OPTIMIZER — the attempt-7 change:
    * LoraConfig accepts modules_to_save=["lm_head"] (built exactly
      as main() builds it, from P7.CFG).
    * Meta-device Qwen3-4B from the cached config wraps cleanly with
      get_peft_model; tie_report behaves as in attempt 5.
    * split_param_groups(peft_model, 2e-4, 1e-5, 0.0): head group is
      EXACTLY the active lm_head module's params (by module
      ownership, id-compared), adapter group is every other
      trainable param, union == all trainable params, intersection
      empty, group LRs are 2e-4 / 1e-5, head totals 388,956,160.
    * Functional override test: HeadSplitSFTTrainer.create_optimizer
      is executed on a bare instance (cls.__new__ + stub model/args)
      and the resulting AdamW's param groups are verified against
      the split — same tensors, same LRs, report recorded.

Run with ~/trlcheck-venv/bin/python (peft 0.18.1 / transformers
4.57.6 / trl 0.25.1 / torch 2.5.1+cpu — the versions the job
resolves). Log is written to docs/train-probe-v7-selfcheck.txt.
"""
import glob
import inspect
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path("/home/hatch/workspace/projects/small-ai-model-for-networking/netconfig-slm")
sys.path.insert(0, str(ROOT / "scripts"))
import job_probe_v7 as P7  # noqa: E402

from tokenizers import Tokenizer  # noqa: E402

SNAP = glob.glob(str(Path.home() / ".cache/huggingface/hub/"
                     "models--Qwen--Qwen3-4B-Base/snapshots/*"))[0]
TOK_PATH = str(Path(SNAP) / "tokenizer.json")
CFG_PATH = str(Path(SNAP) / "config.json")
LOG_PATH = ROOT / "docs/train-probe-v7-selfcheck.txt"


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

    subset, counts, short_counts, threshold = P7.select_subset(
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

    heldout = P7.select_heldout(test_rows)
    check("heldout identical to attempt 4",
          [r["id"] for r in heldout] == run4["heldout_ids"],
          f"24 rows: {[r['id'] for r in heldout][:4]}...")
    check("heldout families",
          Counter(r["family"] for r in heldout) == Counter(
              {f: 4 for f in counts}), "4 per family")

    end_train = P7.verify_endings(tok, subset, "train_subset")
    check("ending check (train subset)", end_train["all_ok"],
          json.dumps(end_train))
    end_held = P7.verify_endings(tok, heldout, "heldout_refs")
    check("ending check (held-out refs)", end_held["all_ok"],
          json.dumps(end_held))

    # ---------------- (2) config guards ----------------------------
    check("CFG values",
          P7.CFG["epochs"] == 1 and P7.CFG["lr"] == 2e-4
          and P7.CFG["head_lr"] == 1e-5 and P7.CFG["warmup"] == 0.1,
          f"epochs={P7.CFG['epochs']}, lr={P7.CFG['lr']}, "
          f"head_lr={P7.CFG['head_lr']}, warmup={P7.CFG['warmup']}")
    bt_src = inspect.getsource(P7.build_trainer)
    check("build_trainer guards",
          "logging_steps=2" in bt_src and "max_grad_norm=1.0" in bt_src
          and "gradient_checkpointing=True" in bt_src
          and "_make_trainer_class" in bt_src
          and '"/tmp/probe7_train_out"' in bt_src,
          "logging_steps=2, max_grad_norm=1.0, GC on, head-split "
          "trainer class, probe7 output dir")

    # ---------------- (3) head + two-tier optimizer ----------------
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoConfig, AutoModelForCausalLM

    cfg = LoraConfig(r=P7.CFG["lora_r"], lora_alpha=P7.CFG["lora_alpha"],
                     lora_dropout=P7.CFG["lora_dropout"],
                     target_modules=P7.CFG["target"],
                     modules_to_save=["lm_head"],
                     task_type="CAUSAL_LM")
    check("LoraConfig accepts modules_to_save",
          list(cfg.modules_to_save) == ["lm_head"],
          f"modules_to_save={cfg.modules_to_save}")

    # Meta device is load-bearing: a real CPU build of the 4B model
    # is ~8 GB in bf16 and OOM-kills this VM (v5 self-check, run 1).
    with torch.device("meta"):
        model = AutoModelForCausalLM.from_config(
            AutoConfig.from_pretrained(CFG_PATH))
    check("base config tie flag",
          model.config.tie_word_embeddings is True,
          f"tie_word_embeddings={model.config.tie_word_embeddings}, "
          f"vocab={model.config.vocab_size}, "
          f"hidden={model.config.hidden_size}")

    base_tie = P7.tie_report(model)
    check("tie_report on raw base", base_tie["error"] is None
          and base_tie["active_weights_tied"] is True,
          json.dumps(base_tie))

    with torch.device("meta"):
        peft_model = get_peft_model(model, cfg)
    tie = P7.tie_report(peft_model)
    check("tie_report on PeftModel", tie["error"] is None,
          json.dumps(tie))
    lines.append(f"tie after wrap: {json.dumps(tie)}")

    eff = P7.active_saved_module(
        peft_model.get_base_model().get_output_embeddings())
    check("effective lm_head module",
          tuple(eff.weight.shape) == (151936, 2560)
          and eff.weight.requires_grad is True,
          f"{type(eff).__name__} weight shape "
          f"{tuple(eff.weight.shape)}, "
          f"requires_grad={eff.weight.requires_grad}")

    # --- split_param_groups: the partition itself ------------------
    groups, report = P7.split_param_groups(peft_model, 2e-4, 1e-5, 0.0)
    check("split returns two groups", len(groups) == 2,
          f"report={json.dumps(report)}")
    check("split group LRs",
          groups[0]["lr"] == 2e-4 and groups[1]["lr"] == 1e-5,
          f"adapter lr={groups[0]['lr']}, head lr={groups[1]['lr']}")
    head_ids = {id(p) for p in eff.parameters()}
    adapter_ids = {id(p) for p in groups[0]["params"]}
    group_head_ids = {id(p) for p in groups[1]["params"]}
    trainable_ids = {id(p) for p in peft_model.parameters()
                     if p.requires_grad}
    check("head group == active lm_head params (module ownership)",
          group_head_ids == head_ids,
          f"{len(group_head_ids)} tensors vs module's "
          f"{len(head_ids)}")
    check("partition exact: union == trainable, disjoint",
          adapter_ids | group_head_ids == trainable_ids
          and not (adapter_ids & group_head_ids),
          f"adapter {len(adapter_ids)} + head {len(group_head_ids)} "
          f"== trainable {len(trainable_ids)}")
    check("head param total",
          report["head_params"] == 151936 * 2560
          and report["adapter_params"] > 0,
          f"head={report['head_params']:,}, "
          f"adapter={report['adapter_params']:,}")

    # --- functional test of the create_optimizer override ----------
    trainer_cls = P7._make_trainer_class()
    check("trainer class overrides create_optimizer",
          "create_optimizer" in trainer_cls.__dict__,
          f"class {trainer_cls.__name__}, "
          f"module {trainer_cls.__module__}")
    inst = trainer_cls.__new__(trainer_cls)
    inst.model = peft_model
    inst.optimizer = None
    inst.args = SimpleNamespace(learning_rate=2e-4, weight_decay=0.0,
                                adam_beta1=0.9, adam_beta2=0.999,
                                adam_epsilon=1e-8)
    opt = inst.create_optimizer()
    check("create_optimizer returns AdamW",
          isinstance(opt, torch.optim.AdamW),
          f"{type(opt).__name__}")
    check("optimizer group LRs",
          [g["lr"] for g in opt.param_groups] == [2e-4, 1e-5],
          f"{[g['lr'] for g in opt.param_groups]}")
    check("optimizer groups match the split",
          {id(p) for p in opt.param_groups[0]["params"]} == adapter_ids
          and {id(p) for p in opt.param_groups[1]["params"]}
          == group_head_ids,
          "adapter/head tensor sets identical to split_param_groups")
    check("optimizer report recorded on trainer",
          getattr(inst, "optimizer_group_report", None) is not None
          and inst.optimizer_group_report["head_params"]
          == 151936 * 2560,
          json.dumps(getattr(inst, "optimizer_group_report", None)))

    lines.append("ALL PROBE V7 SELF-CHECKS PASSED")
    out = "\n".join(lines) + "\n"
    LOG_PATH.write_text(out)
    print(out)


if __name__ == "__main__":
    main()
