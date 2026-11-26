#!/usr/bin/env python3
"""HF Jobs probe v5 (attempt 5): train the tied output head.

WHERE THIS STANDS (outputs/hf-job-2/DIAGNOSIS.md + outputs/hf-probe-4/
RESULTS.json): attempts 3-4 established that (i) the executed training
batch DOES supervise EOS 151645 at every row end (assertion passes
in-job on a real collated batch); (ii) the bf16 merge is exonerated —
attempt 3's adapter AND fp32 merge both scored stopped_cleanly 0.0%;
(iii) there is no shifted-supervision bug — attempt 4's position scan
found run 2's P(151645) peak exactly AT the true boundary (offset 0 on
all 4 scanned rows) but only at ~4.3e-4, ~1000x too weak; and (iv) the
§4.2 reformulation (`end` line before <|im_end|> + short-completion
oversampling) produced the first movement ever: adapter 8.3% /
fp32-merged 20.8% stopped_cleanly — real, but far under the >=90%
gate.

ATTEMPT 5 tests the remaining prime suspect: Qwen3-4B-Base's lm_head
is TIED to the input embeddings and FROZEN under plain QLoRA, and the
151645 (<|im_end|>) row of that head is barely trained in the base
model — LoRA can only steer hidden states toward a fixed,
poorly-conditioned output row. So attempt 5 keeps the attempt-4
recipe EXACTLY (same reformulated data with the `end` line + ending
verification, same 256-row subset with 102 shortest-quartile rows,
seed 42, same EOS assertion, same dual adapter/fp32-merge eval, same
>=90% gate per artifact) and changes only the training surface:

  * LoraConfig gains modules_to_save=["lm_head"], so the output head
    trains in full precision alongside the adapters. PEFT's own
    tied-module handling decides what happens to the embed_tokens
    tie — nothing is forced by hand; the observed tie state is
    recorded in RUN_INFO (config flag + whether the effective
    lm_head weight IS the embed_tokens weight tensor).
  * gradient_checkpointing=True (use_reentrant=False) for the extra
    optimizer memory (lm_head is 151936x2560, ~389M trainable
    params once saved in full precision).
  * A movement verification lands in RESULTS.json ("lm_head_check"):
    after training, the L2 norm of (effective lm_head row 151645 -
    the pre-training base row) and the row's own norm, for BOTH the
    adapter artifact and the fp32 merge — proving the head actually
    moved, or recording that it did not.

The position scan is DROPPED (attempt 4 answered it: peak at offset
0, magnitude ~4.3e-4).

LAUNCH DISCIPLINE (do not skip): pip in the job command MUST be a
single invocation pinning torch==2.5.1 torchvision==0.20.1
torchaudio==2.5.1 to match the pytorch/pytorch:2.5.1-cuda12.4-cudnn9
-runtime image, plus transformers>=4.51,<4.58 trl>=0.19,<0.26
peft>=0.17,<0.19 bitsandbytes>=0.47 accelerate>=1.4 datasets>=3.2,<4.

Outputs go to /output/run5-probe (mounted bucket
aag1091/netconfig-train-output): RUN_INFO.json, RESULTS.json,
generations_adapter.jsonl, generations_merged.jsonl, DONE.json.
Earlier attempts' outputs are left untouched. No model is written to
the bucket; no GGUF; no scoring ladder; nothing published.
"""
import gc
import importlib.metadata
import json
import math
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

OUT = Path("/output/run5-probe")
DS_REPO = "aag1091/netconfig-slm-dataset"
DS = f"https://huggingface.co/datasets/{DS_REPO}/resolve/main"
BASE = "Qwen/Qwen3-4B-Base"
MIN_TRAIN_ROWS = 4000
IM_END_ID = 151645          # <|im_end|> — ChatML turn end
ENDOFTEXT_ID = 151643       # <|endoftext|> — Qwen3-Base native eos
EOS_IDS = [IM_END_ID, ENDOFTEXT_ID]
MAX_NEW_TOKENS = 512
SUBSET_ROWS = 256
SHORT_ROWS = 102            # ~40% of the subset, from the shortest quartile
HELDOUT_PER_FAMILY = 4
SEED = 42
GATE_PCT = 90.0
# Carried over from attempt 3 (attempt 1 OOM'd on step 1: full-vocab
# logits at bs=8 on the 22 GiB A10G-small): bs 2, accum 8. Effective
# batch stays 16 and the 256-row epoch still makes ~16 optimizer
# steps. run_eval generates under torch.autocast(bf16) (attempt 2
# died in generate: "expected scalar type Half but found Float"), and
# each artifact's eval is crash-isolated with incremental RESULTS
# flushes, as are the lm_head movement checks.
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
    print("[probe5]", *a, flush=True)


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


def _draw(pool_by_family, want, rng, exclude_ids):
    """Seeded stratified draw of `want` rows from per-family pools:
    even quotas first, then any shortfall redistributed round-robin
    across families that still have rows. Returns (rows, counts)."""
    fams = sorted(pool_by_family)
    pools, counts = {}, {}
    for fam in fams:
        pool = [r for r in pool_by_family[fam]
                if r["id"] not in exclude_ids]
        rng.shuffle(pool)
        pools[fam] = pool
        counts[fam] = 0
    got = []
    if not fams or want <= 0:
        return got, counts
    base = want // len(fams)
    rem = want - base * len(fams)
    quota = {fam: base + (1 if i < rem else 0)
             for i, fam in enumerate(fams)}
    remaining = want
    for fam in fams:
        take = min(quota[fam], len(pools[fam]))
        got.extend(pools[fam][:take])
        counts[fam] = take
        pools[fam] = pools[fam][take:]
        remaining -= take
    while remaining > 0:
        progressed = False
        for fam in fams:
            if remaining <= 0:
                break
            if pools[fam]:
                got.append(pools[fam].pop(0))
                counts[fam] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return got, counts


def select_subset(train_rows, tok, total=SUBSET_ROWS, n_short=SHORT_ROWS,
                  seed=SEED, max_tokens=1024):
    """Subset selection (unchanged since v4): tokenize with the real tokenizer (lengths measured
    on the TRANSFORMED training text); eligible rows are <=
    max_tokens. `n_short` rows come from the shortest quartile of
    eligible rows (nearest-rank 25th percentile), spread across
    families as evenly as the quartile allows; the remaining
    total - n_short rows are stratified across families from all
    eligible rows (attempt-3 style, seed 42).
    Returns (rows, family_counts, short_family_counts, threshold)."""
    texts = [prompt_of(r["intent_nl"]) + completion_text(r)
             for r in train_rows]
    enc = tok(texts, add_special_tokens=False)["input_ids"]
    eligible = [(r, len(ids)) for r, ids in zip(train_rows, enc)
                if len(ids) <= max_tokens]
    lengths = sorted(n for _, n in eligible)
    q_idx = max(0, math.ceil(0.25 * len(lengths)) - 1)
    threshold = lengths[q_idx]
    short_pool, all_pool = {}, {}
    for r, n in eligible:
        all_pool.setdefault(r["family"], []).append(r)
        if n <= threshold:
            short_pool.setdefault(r["family"], []).append(r)
    rng = random.Random(seed)
    picked, picked_ids = [], set()
    short_picked, short_counts = _draw(short_pool, n_short, rng,
                                       picked_ids)
    for r in short_picked:
        picked.append(r)
        picked_ids.add(r["id"])
    rest_picked, rest_counts = _draw(all_pool, total - len(picked),
                                     rng, picked_ids)
    picked.extend(rest_picked)
    counts = {fam: short_counts.get(fam, 0) + rest_counts.get(fam, 0)
              for fam in sorted(set(short_counts) | set(rest_counts))}
    rng.shuffle(picked)
    return picked, counts, short_counts, threshold


def select_heldout(test_rows, per_family=HELDOUT_PER_FAMILY):
    """First `per_family` test rows per family, in file order
    (identical to attempt 3)."""
    by_family = {}
    for row in test_rows:
        by_family.setdefault(row["family"], []).append(row)
    picked = []
    for fam in sorted(by_family):
        picked.extend(by_family[fam][:per_family])
    return picked


def verify_endings(tok, rows, tag):
    """Post-transform check: every processed completion's last tokens
    must be the token(s) for "end" (as the final config line) followed
    by 151645, with the line break supplied by the token just before
    the "end" span. The check is done on the FULL completion's tokens
    rather than on tok("\\nend") in isolation: BPE can merge the "\\n"
    into the preceding line's final token (e.g. "0\\n" as one token),
    so a standalone tokenization of "\\nend" is not the reference.
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


def active_saved_module(module):
    """If `module` is a PEFT ModulesToSaveWrapper, return the active
    adapter's saved copy — the module that actually runs and trains.
    Plain modules pass through unchanged."""
    saved = getattr(module, "modules_to_save", None)
    if saved is not None and len(saved):
        active = getattr(module, "active_adapters", None) or ["default"]
        if active[0] in saved:
            return saved[active[0]]
    return module


def output_embedding_row(model, row=IM_END_ID):
    """Row `row` of the model's EFFECTIVE lm_head weight (the one
    that produces logits), as a CPU float32 tensor. Goes through
    PeftModel delegation and the modules_to_save wrapper, so it
    reads the trained copy when one exists."""
    base = (model.get_base_model()
            if hasattr(model, "get_base_model") else model)
    mod = active_saved_module(base.get_output_embeddings())
    return mod.weight.detach()[row].float().cpu()


def tie_report(model):
    """Record how the lm_head/embed_tokens tie looks on THIS model
    object (raw base, PeftModel, or merged): the config flag, whether
    the effective (active) lm_head weight IS the embed_tokens weight
    tensor, whether the underlying original modules are still tied,
    and whether the effective lm_head weight requires grad. Takes
    the framework's behavior as-is — nothing is forced. Never
    raises."""
    info = {"tie_word_embeddings_config": None,
            "lm_head_module_type": None,
            "embed_tokens_module_type": None,
            "active_weights_tied": None,
            "original_weights_tied": None,
            "lm_head_weight_requires_grad": None,
            "error": None}
    try:
        base = (model.get_base_model()
                if hasattr(model, "get_base_model") else model)
        info["tie_word_embeddings_config"] = getattr(
            base.config, "tie_word_embeddings", None)
        in_mod = base.get_input_embeddings()
        out_mod = base.get_output_embeddings()
        info["lm_head_module_type"] = type(out_mod).__name__
        info["embed_tokens_module_type"] = type(in_mod).__name__
        in_active = active_saved_module(in_mod)
        out_active = active_saved_module(out_mod)
        info["active_weights_tied"] = bool(
            in_active.weight is out_active.weight)
        info["lm_head_weight_requires_grad"] = bool(
            out_active.weight.requires_grad)
        in_orig = getattr(in_mod, "original_module", in_mod)
        out_orig = getattr(out_mod, "original_module", out_mod)
        info["original_weights_tied"] = bool(
            in_orig.weight is out_orig.weight)
    except Exception:  # noqa: BLE001 - record, don't die
        info["error"] = traceback.format_exc()
    return info


def lm_head_movement(base_row, model, tag):
    """Compare row 151645 of the model's effective lm_head against
    the pre-training base row: L2 norm of the difference (did the
    head move?) and of the row itself. Never raises."""
    import torch
    try:
        row = output_embedding_row(model)
        return {"tag": tag, "row": IM_END_ID,
                "delta_l2": float(torch.linalg.vector_norm(row
                                                           - base_row)),
                "row_l2": float(torch.linalg.vector_norm(row)),
                "base_row_l2": float(
                    torch.linalg.vector_norm(base_row))}
    except Exception:  # noqa: BLE001 - record, don't die
        return {"tag": tag, "error": traceback.format_exc()}


def build_trainer(model, tok, rows, peft_cfg, bf16=True,
                  output_dir="/tmp/probe5_train_out"):
    """SFTTrainer with the exact v2 SFTConfig (probe: epochs=1).
    Attempt 5 adds gradient checkpointing (use_reentrant=False) for
    the extra optimizer memory of the full-precision lm_head that
    modules_to_save puts into training (~389M params)."""
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
                        gradient_checkpointing=True,
                        gradient_checkpointing_kwargs={
                            "use_reentrant": False},
                        max_length=CFG["max_len"],
                        completion_only_loss=True, report_to=[])
    # processing_class=tok is load-bearing (see v2): TRL appends its
    # processing tokenizer's eos; the overridden tokenizer keeps
    # exactly one trailing 151645.
    return SFTTrainer(model=model, args=sft_cfg, train_dataset=ds,
                      peft_config=peft_cfg, processing_class=tok)


def assert_eos_labels(trainer, expect=IM_END_ID):
    """THE probe assertion (carried over from v3): take a real batch
    (real TRL processing + real collator, via the train dataloader)
    and check every row's last non-pad label == expect (151645).
    With the reformulation this also implicitly covers the new
    `end`-line ending. Never raises: failures are recorded."""
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
    """Greedy eval with v2's stopped_cleanly definition. `rows` carry
    the TRANSFORMED reference config (coverage is measured against
    it). Writes generations_<tag>.jsonl to OUT; returns
    (overall, per_prompt)."""
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
        # Carried over from attempt 3: the raw PeftModel generate
        # path mixes dtypes (attempt 2 died in lm_head: "expected
        # scalar type Half but found Float"). Autocast bf16 — the v2
        # compute dtype — makes every Linear in the forward consistent.
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
                "eos_ids": EOS_IDS, "script": "job_probe_v5.py",
                "probe": "attempt 5: attempt-4 reformulation + "
                         "modules_to_save=['lm_head'] (train the tied "
                         "output head); position scan dropped "
                         "(answered in attempt 4)",
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

    # Guard (same as v2/v3): never silently train on a stale dataset.
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

    # Subset (identical to attempt 4): 256 rows — 102 from the shortest quartile + 154
    # stratified — plus the same 24 held-out prompts as attempt 3.
    subset, fam_counts, short_counts, threshold = select_subset(
        train_rows, tok)
    heldout = select_heldout(test_rows)
    # Eval rows carry the TRANSFORMED reference config (coverage and
    # the reformulated boundary); prompts/intents unchanged.
    heldout_eval = [{**r, "config": add_terminator(r["config"])}
                    for r in heldout]
    # Ending verification (spec §B.1): every processed completion —
    # training subset and held-out references — must end with the
    # token(s) for "\nend" + 151645.
    ending_check = {
        "train_subset": verify_endings(tok, subset, "train_subset"),
        "heldout_refs": verify_endings(tok, heldout, "heldout_refs")}
    endings_ok = (ending_check["train_subset"]["all_ok"]
                  and ending_check["heldout_refs"]["all_ok"])
    log("ending check:", json.dumps(ending_check))
    if not endings_ok:
        log("!!! ENDING CHECK FAILED — see RESULTS.json; continuing "
            "so downstream behavior is on record !!!")
    run_info.update({"subset_rows": len(subset),
                     "subset_family_counts": fam_counts,
                     "subset_short_family_counts": short_counts,
                     "subset_short_rows": sum(short_counts.values()),
                     "short_quartile_threshold_tokens": threshold,
                     "subset_ids": [r["id"] for r in subset],
                     "heldout_rows": len(heldout),
                     "heldout_ids": [r["id"] for r in heldout],
                     "ending_check": ending_check})
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    log(f"subset: {len(subset)} rows {fam_counts}; short-quartile "
        f"rows: {sum(short_counts.values())} {short_counts} "
        f"(threshold {threshold} tokens); heldout: {len(heldout)}")

    durations = {"train_s": None,
                 "eval_adapter_s": None, "merge_s": None,
                 "eval_merged_s": None}
    results = {
        "script": "job_probe_v5.py",
        "dataset_sha": sha,
        "versions": vers,
        "subset_rows": len(subset),
        "subset_family_counts": fam_counts,
        "subset_short_family_counts": short_counts,
        "subset_short_rows": sum(short_counts.values()),
        "short_quartile_threshold_tokens": threshold,
        "heldout_rows": len(heldout),
        "ending_check": ending_check,
        "tie_info": None,
        "lm_head_check": None,
        "assertion": None,
        "assertion_warning": None,
        "train": None,
        "adapter_eval": None,
        "merged_eval": None,
        "durations": durations,
    }
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # ---------------------------------------------------------------
    # REFORMULATED TRAINING — attempt 4's recipe, with the output
    # head itself in training (modules_to_save=["lm_head"]).
    # ---------------------------------------------------------------
    from peft import LoraConfig
    from transformers import BitsAndBytesConfig
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=bnb, device_map="auto")
    # Pre-training reference: row 151645 of the base model's lm_head,
    # captured BEFORE any wrapping/training. The post-training
    # movement checks (adapter + merged) compare against this row.
    base_lm_head_row = output_embedding_row(model)
    results["lm_head_check"] = {
        "base_row_l2": float(
            torch.linalg.vector_norm(base_lm_head_row)),
        "adapter": None, "merged": None}
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))
    log("base lm_head row 151645 captured; L2:",
        results["lm_head_check"]["base_row_l2"])
    peft_cfg = LoraConfig(r=CFG["lora_r"], lora_alpha=CFG["lora_alpha"],
                          lora_dropout=CFG["lora_dropout"],
                          target_modules=CFG["target"],
                          modules_to_save=["lm_head"],
                          task_type="CAUSAL_LM")
    trainer = build_trainer(model, tok, subset, peft_cfg)

    # TIE RECORD: Qwen3-4B ties lm_head to embed_tokens. PEFT's own
    # tied-module handling decides what modules_to_save=["lm_head"]
    # does to that tie — record the outcome, force nothing.
    tie = tie_report(trainer.model)
    results["tie_info"] = tie
    run_info["lm_head_tie"] = tie
    (OUT / "RUN_INFO.json").write_text(json.dumps(run_info, indent=2))
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))
    log("lm_head tie state:", json.dumps(tie))

    # PRE-TRAINING ASSERTION (carried over): a real collated batch
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
    train_s = round(time.time() - t_train, 1)
    durations["train_s"] = train_s
    train_loss = result.training_loss
    log_history = trainer.state.log_history
    log("train done; loss:", train_loss, "in", train_s, "s")
    adapter_dir = "/tmp/adapter_probe5"
    trainer.save_model(adapter_dir)
    tok.save_pretrained(adapter_dir)
    results["train"] = {"loss": train_loss, "epochs": CFG["epochs"],
                        "steps_expected": len(subset) // (CFG["bs"]
                                                          * CFG["accum"]),
                        "log_history": log_history}
    # MOVEMENT CHECK (adapter): did lm_head row 151645 actually move
    # under modules_to_save? (Never raises; records its own errors.)
    results["lm_head_check"]["adapter"] = lm_head_movement(
        base_lm_head_row, trainer.model, "adapter")
    log("lm_head movement (adapter):",
        json.dumps(results["lm_head_check"]["adapter"]))
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # Per-artifact crash isolation (carried over from attempt 3):
    # each eval runs in its own try/except; the adapter result is
    # flushed to the bucket BEFORE the merged eval is attempted.
    adapter_eval = None
    try:
        t_ev = time.time()
        adapter_eval, adapter_pp = run_eval(trainer.model, tok,
                                            heldout_eval, "adapter")
        durations["eval_adapter_s"] = round(time.time() - t_ev, 1)
        results["adapter_eval"] = {**adapter_eval,
                                    "per_prompt": adapter_pp}
    except Exception:  # noqa: BLE001 - record, don't die
        results["adapter_eval"] = {"error": traceback.format_exc()}
        log("!!! ADAPTER EVAL FAILED !!!\n",
            results["adapter_eval"]["error"])
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))
    free(trainer.model)
    del trainer

    # Eval (b): fp32 merge, saved/evaluated in bf16.
    merged_eval = None
    try:
        t_mg = time.time()
        from peft import PeftModel
        fp32_base = AutoModelForCausalLM.from_pretrained(
            BASE, torch_dtype=torch.float32, device_map="auto")
        merged = PeftModel.from_pretrained(
            fp32_base, adapter_dir).merge_and_unload()
        merged = merged.to(torch.bfloat16)
        durations["merge_s"] = round(time.time() - t_mg, 1)
        log("fp32 merge done in", durations["merge_s"], "s")
        # The /tmp save is a byproduct (kept from attempt 4's flow);
        # isolate it so a save quirk with the trained-head weights
        # can never take the merged EVAL down with it.
        try:
            merged.save_pretrained("/tmp/merged_probe5")
            tok.save_pretrained("/tmp/merged_probe5")
            log("merged model saved bf16 to /tmp")
        except Exception:  # noqa: BLE001 - byproduct only
            log("!!! MERGED /tmp SAVE FAILED (eval continues) !!!\n",
                traceback.format_exc())
        # MOVEMENT CHECK (merged): the trained head must survive the
        # fp32 merge — same comparison against the base row.
        results["lm_head_check"]["merged"] = lm_head_movement(
            base_lm_head_row, merged, "merged")
        log("lm_head movement (merged):",
            json.dumps(results["lm_head_check"]["merged"]))
        (OUT / "RESULTS.json").write_text(json.dumps(results,
                                                     indent=2))
        t_ev = time.time()
        merged_eval, merged_pp = run_eval(merged, tok, heldout_eval,
                                          "merged")
        durations["eval_merged_s"] = round(time.time() - t_ev, 1)
        results["merged_eval"] = {**merged_eval,
                                  "per_prompt": merged_pp}
        free(merged)
    except Exception:  # noqa: BLE001 - record, don't die
        results["merged_eval"] = {"error": traceback.format_exc()}
        if results["lm_head_check"]["merged"] is None:
            results["lm_head_check"]["merged"] = {
                "tag": "merged",
                "error": "merged artifact unavailable: the merge "
                         "block failed before the movement check"}
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
            "ending_check_ok": endings_ok,
            "tie_info": results["tie_info"],
            "lm_head_check": results["lm_head_check"],
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
