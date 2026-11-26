#!/usr/bin/env python3
"""HF Jobs probe v4 (attempt 4): position scan + `end`-terminator fix.

WHERE THIS STANDS (outputs/hf-job-2/DIAGNOSIS.md + outputs/hf-probe-3/
RESULTS.json): attempt 3 proved the executed training batch DOES
supervise EOS 151645 at every row end (assertion passed on a real
collated batch, versions logged: trl 0.25.1 / transformers 4.57.6 /
peft 0.18.1), yet the trained ADAPTER scored stopped_cleanly 0.0% on
24 held-out prompts AND the fp32 merge also scored 0.0% — H2 (bf16
merge annihilation) is refuted; the adapter itself never learned to
stop (H1 residual: the loss sits between the labels and the gradient,
or the single-EOS-token signal is too weak in this formulation).

ATTEMPT 4 does two things, in order:

(A) POSITION SCAN (before any training): load run 2's merged model
    (/output/run2/merged, bf16) and teacher-force prompt + ORIGINAL
    reference config + <|im_end|> (run 2's exact training format) for
    4 held-out rows (ospf, bgp, vlan_l2, interface_mgmt). Record
    P(151645) at EVERY completion prediction slot: the value at the
    true boundary (the slot that predicts the EOS token itself), the
    max over all slots and its offset from the boundary, and the mean.
    A peak at a consistent non-zero offset => shifted supervision; no
    peak anywhere => the gradient never landed.

(B) REFORMULATED TRAINING (DIAGNOSIS §4.2): the identical probe
    recipe as attempt 3 (256 rows, 1 epoch, same QLoRA config, same
    EOS assertion, same dual adapter/fp32-merge eval, >=90% gate per
    artifact) with two data changes applied to BOTH the training
    completions and the held-out reference configs (prompts unchanged):
      1. Every config gains a final line "end" (the natural Cisco
         terminator) so every completion ends "...\\nend" followed by
         <|im_end|> — a high-frequency text token as a stepping stone
         to the special token. A post-transform check verifies every
         processed completion's last tokens are the token(s) for
         "\\nend" + 151645, and logs it in RESULTS.json.
      2. Subset selection: ~40% of the 256 rows (102) come from the
         SHORTEST quartile of eligible train rows by token length
         (spread across all 6 families as evenly as the quartile
         allows); the remaining 154 are stratified across families
         as in attempt 3 (seed 42; all rows <=1024 tokens). Short
         completions put the EOS boundary at varied, frequent
         positions instead of one token per ~440.

LAUNCH DISCIPLINE (do not skip): pip in the job command MUST be a
single invocation pinning torch==2.5.1 torchvision==0.20.1
torchaudio==2.5.1 to match the pytorch/pytorch:2.5.1-cuda12.4-cudnn9
-runtime image, plus transformers>=4.51,<4.58 trl>=0.19,<0.26
peft>=0.17,<0.19 bitsandbytes>=0.47 accelerate>=1.4 datasets>=3.2,<4.

Outputs go to /output/run4-probe (mounted bucket
aag1091/netconfig-train-output): RUN_INFO.json, RESULTS.json,
generations_adapter.jsonl, generations_merged.jsonl, DONE.json.
Attempt 3's run3-probe/ outputs are left untouched. No model is
written to the bucket; no GGUF; no scoring ladder; nothing published.
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

OUT = Path("/output/run4-probe")
RUN2_MERGED = Path("/output/run2/merged")
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
SCAN_FAMILIES = ["ospf", "bgp", "vlan_l2", "interface_mgmt"]
SEED = 42
GATE_PCT = 90.0
# Carried over from attempt 3 (attempt 1 OOM'd on step 1: full-vocab
# logits at bs=8 on the 22 GiB A10G-small): bs 2, accum 8. Effective
# batch stays 16 and the 256-row epoch still makes ~16 optimizer
# steps. run_eval generates under torch.autocast(bf16) (attempt 2
# died in generate: "expected scalar type Half but found Float"), and
# each artifact's eval is crash-isolated with incremental RESULTS
# flushes. The position scan is crash-isolated the same way.
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
    print("[probe4]", *a, flush=True)


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
    """v4 subset: tokenize with the real tokenizer (lengths measured
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


def position_scan(model, tok, rows):
    """(A) Teacher-force prompt + ORIGINAL reference config +
    <|im_end|> (run 2's exact training format) and record P(151645)
    at every completion prediction slot. Slot c is the distribution
    that predicts completion token c (slot len(ref) is the
    distribution after the final token). The boundary slot is
    c == len(ref) - 1: it predicts the EOS token itself, from the
    context 'prompt + full config' — the exact context that, in run
    2's training, was always followed by 151645."""
    import torch
    model.eval()
    try:
        dev = model.device
    except Exception:  # noqa: BLE001 - device property varies
        dev = next(model.parameters()).device
    out_rows = []
    for row in rows:
        prompt_ids = tok(prompt_of(row["intent_nl"]),
                         add_special_tokens=False)["input_ids"]
        ref_ids = tok(f"{row['config']}<|im_end|>",
                      add_special_tokens=False)["input_ids"]
        full = prompt_ids + ref_ids
        input_ids = torch.tensor([full], dtype=torch.long).to(dev)
        with torch.no_grad(), torch.autocast("cuda",
                                             dtype=torch.bfloat16):
            logits = model(input_ids).logits[0].float()
        col = torch.softmax(logits, dim=-1)[:, IM_END_ID]
        # Slot c reads logits index len(prompt_ids) + c - 1.
        slots = col[len(prompt_ids) - 1:].tolist()
        boundary_idx = len(ref_ids) - 1
        p_boundary = slots[boundary_idx]
        max_i = max(range(len(slots)), key=lambda i: slots[i])
        rec = {"id": row["id"], "family": row["family"],
               "completion_tokens": len(ref_ids),
               "config_variant": "original (run 2 training format)",
               "p_eos_at_boundary": p_boundary,
               "p_eos_max": slots[max_i],
               "max_offset_from_boundary": max_i - boundary_idx,
               "p_eos_mean": sum(slots) / len(slots)}
        out_rows.append(rec)
        log("scan", row["id"], "boundary P(151645):",
            round(p_boundary, 6), "| max:", round(slots[max_i], 6),
            "at offset", max_i - boundary_idx,
            "| mean:", round(rec["p_eos_mean"], 6))
    return out_rows


def build_trainer(model, tok, rows, peft_cfg, bf16=True,
                  output_dir="/tmp/probe4_train_out"):
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
                "eos_ids": EOS_IDS, "script": "job_probe_v4.py",
                "probe": "attempt 4: position scan on run2 merged + "
                         "end-terminator reformulation (DIAGNOSIS §4.2)",
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

    # v4 subset: 256 rows — 102 from the shortest quartile + 154
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

    durations = {"scan_s": None, "train_s": None,
                 "eval_adapter_s": None, "merge_s": None,
                 "eval_merged_s": None}
    results = {
        "script": "job_probe_v4.py",
        "dataset_sha": sha,
        "versions": vers,
        "subset_rows": len(subset),
        "subset_family_counts": fam_counts,
        "subset_short_family_counts": short_counts,
        "subset_short_rows": sum(short_counts.values()),
        "short_quartile_threshold_tokens": threshold,
        "heldout_rows": len(heldout),
        "ending_check": ending_check,
        "position_scan": None,
        "assertion": None,
        "assertion_warning": None,
        "train": None,
        "adapter_eval": None,
        "merged_eval": None,
        "durations": durations,
    }
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # ---------------------------------------------------------------
    # (A) POSITION SCAN — run 2's merged model, before any training.
    # Crash-isolated: a scan failure is recorded, never fatal.
    # ---------------------------------------------------------------
    scan_rows = []
    held_by_family = {}
    for r in heldout:
        held_by_family.setdefault(r["family"], r)
    for fam in SCAN_FAMILIES:
        if fam in held_by_family:
            scan_rows.append(held_by_family[fam])
    try:
        t_scan = time.time()
        scan_model = AutoModelForCausalLM.from_pretrained(
            str(RUN2_MERGED), torch_dtype=torch.bfloat16,
            device_map="auto")
        try:
            scan_tok = AutoTokenizer.from_pretrained(str(RUN2_MERGED))
        except Exception:  # noqa: BLE001 - fall back to base tokenizer
            scan_tok = tok
        scan_tok.eos_token = "<|im_end|>"
        results["position_scan"] = position_scan(scan_model, scan_tok,
                                                 scan_rows)
        durations["scan_s"] = round(time.time() - t_scan, 1)
        log("position scan done in", durations["scan_s"], "s")
        free(scan_model)
        del scan_model
    except Exception:  # noqa: BLE001 - record, don't die
        results["position_scan"] = {"error": traceback.format_exc()}
        log("!!! POSITION SCAN FAILED !!!\n",
            results["position_scan"]["error"])
    durations["total_min"] = round((time.time() - t0) / 60, 1)
    (OUT / "RESULTS.json").write_text(json.dumps(results, indent=2))

    # ---------------------------------------------------------------
    # (B) REFORMULATED TRAINING — identical recipe to attempt 3.
    # ---------------------------------------------------------------
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
    adapter_dir = "/tmp/adapter_probe4"
    trainer.save_model(adapter_dir)
    tok.save_pretrained(adapter_dir)
    results["train"] = {"loss": train_loss, "epochs": CFG["epochs"],
                        "steps_expected": len(subset) // (CFG["bs"]
                                                          * CFG["accum"]),
                        "log_history": log_history}
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
        merged.save_pretrained("/tmp/merged_probe4")
        tok.save_pretrained("/tmp/merged_probe4")
        durations["merge_s"] = round(time.time() - t_mg, 1)
        log("fp32 merge done in", durations["merge_s"],
            "s; saved bf16 to /tmp")
        t_ev = time.time()
        merged_eval, merged_pp = run_eval(merged, tok, heldout_eval,
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

    scan_summary = None
    if isinstance(results["position_scan"], list):
        scan_summary = {r["id"]: {
            "p_eos_at_boundary": r["p_eos_at_boundary"],
            "p_eos_max": r["p_eos_max"],
            "max_offset_from_boundary": r["max_offset_from_boundary"]}
            for r in results["position_scan"]}
    done = {"elapsed_min": round((time.time() - t0) / 60, 1),
            "dataset_sha": sha,
            "ending_check_ok": endings_ok,
            "position_scan": scan_summary,
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
