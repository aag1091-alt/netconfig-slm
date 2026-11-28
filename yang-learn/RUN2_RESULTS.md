# yang-learn run 2 — results (2026-11-27)

Full fine-tune of Qwen/Qwen3-4B-Base on corpus v1 train + the
corpus v2 additions (1,418 records; corpus files frozen at
netconfig-slm commit f3a9b1d), training job
`6b09f05e54072e8b716aa051`, a100-large, run-1 config unchanged
(epochs 2, eff. batch 16, max_len 1536, lr 1e-5) — the corpus is
the only variable vs run 1. Script: `job_train_yanglearn_v2.py`.
Evaluation is a SEPARATE batched job `6b09f2c454072e8b716aa95d`
(`job_eval_run2_batched.py`, batch 16) — no in-job generation.
Grader: `score_bench.py`. Raw generations + full scores:
`outputs/yang-learn-run2/` (local) and the bucket folder
`yang-learn-run2/` in `aag1091/netconfig-train-output`. Plan and
failure analysis that motivated this run: `RUN2_PLAN.md`.

## Jobs — both completed cleanly

- **Training**: COMPLETED, **535 s total** (scheduling 7 +
  running 527) → **$0.37**. Dataset guard passed (per-file sha256
  + row counts, and the combined-file sha256
  `25944fba…` over 1,418 rows). Ending check all_ok; **EOS
  assertion PASSED** (every row's last non-pad label == 151645).
  Training: 178 steps in 419.7 s, final train loss **0.2134**
  (run 1: 0.1837 on v1-only data — not directly comparable; the
  v2 additions are deliberately harder, targeted material).
  Model saved: bf16 safetensors (2 shards, 4,967,215,360 +
  3,077,766,632 bytes) + tokenizer + generation_config (eos
  [151645, 151643]).
- **Eval**: COMPLETED, **677 s total** → **$0.47**. Batched
  generation: dev 141 rows in 271.1 s, bench 217 rows in
  350.8 s (~1.6–1.9 s/row on A100, vs ~13.8 s/row for run 1's
  sequential generation). Stopped-on-EOS and length stats are in
  `eval_stats.json`.

## Bench v1 scores — mechanical grading, run 2 vs run 1

| family | run 2 | run 1 | Δ |
|---|---|---|---|
| state_reconstruction | 40/40 = **100.0%** | 40/40 = 100.0% | — |
| typed_edit | 41/42 = **97.6%** | 38/42 = 90.5% | +7.1 |
| path_meaning | 22/34 = **64.7%** | 13/34 = 38.2% | **+26.5** |
| instance_validity (verdict) | 53/73 = 72.6% | 56/73 = 76.7% | −4.1 |
| revised_schema (strict) | 12/28 = 42.9% | 13/28 = 46.4% | −3.6 |
| revised_schema (verdict only, diagnostic) | 23/28 = 82.1% | 26/28 = 92.9% | −10.7 |
| **overall** | **168/217 = 77.4%** | **160/217 = 73.7%** | **+3.7** |

Benchmark-only: 165/209 (78.9%, was 74.6%). Held-out only: 3/8
(37.5%, was 4/8 — n=8, a one-item swing). **Stopped cleanly:
207/217 = 95.4%** (was 69.1%). Pass rate when stopped: 79.2%
(164/207); when not: 40.0% (4/10). Of 33 invalid IV items, the
model named the expected node in 13 (was 12).

## Dev scores

- **Same 78 v1 dev records the run-1 model was measured on:
  67/78 = 85.9%** (run-1 model: 63/78 = 80.8%). Per family:
  SR 24/24, TE 15/15, RS strict 5/6, PM 11/15 (was 7/15),
  IV 12/18 (unchanged).
- Full dev incl. the 63 v2 additions (deliberately targeted,
  harder material): **100/141 = 70.9%** — SR 24/24 (100%),
  TE 16/17 (94.1%), PM 35/55 (63.6%), RS strict 7/12 (58.3%),
  IV 18/33 (54.5%). Stopped cleanly 131/141 (92.9%).

## Reading of the failures (spot-checked against the grader)

- **Stopping is fixed.** 95.4% of bench generations stop cleanly
  (was 69.1%), and 43 of the 49 remaining fails stopped cleanly
  too — the residual failures are knowledge/verdict errors, not
  rambles into fabricated follow-up turns.
- **Type recall improved a lot, not completely.** PM +26.5 pts.
  The `cisco-mac-address-type` typedef is now nearly recalled
  (near-miss names like `ios-types:mac-address pattern` instead
  of run 1's unrelated `union {string, uint16}`), but not
  verbatim; `1..4097` is still recalled as `1..4096`; the access
  union still drops its enum branch once. A few generations on
  (OBSOLETE) entries bleed stray translation fragments — a
  base-model artifact, visible in the raw generations.
- **Selector-negative drilling worked.** TE 90.5% → 97.6%
  (4 fails → 1): the model now distinguishes `invalid_key` from
  `target_not_found`.
- **IV slipped slightly** (76.7% → 72.6%). The false-valid bias
  persists — 13 of 33 invalids still called "Valid." — despite
  the invalid-tilted v2 additions (72/48). Sensitivity to
  invalid instances remains the gap; node naming barely moved
  (12 → 13 of 33).
- **RS regressed on the unseen-profile benchmark** — the
  clearest run-3 signal. Strict 46.4% → 42.9% and verdict-only
  92.9% → 82.1%: verdict-wrong fails rose (2 → ~4) and 11 fails
  are still citation-missing. Citation drilling on the train
  profiles (P1/P3/P4/P5) did not transfer to the benchmark's
  unseen P2/P6 profiles, and verdict accuracy there fell. On
  train-profile-adjacent dev, RS held (strict 5/6, verdict 6/6).
  More citation volume on seen profiles is not the fix;
  profile-generalization is.

## Regression gate

Bench v1 overall **77.4% ≥ run 1's 73.7%** → gate passed;
proceed to GGUF conversion + publish (below).

## Costs (run 2)

Training $0.37 (535 s) + eval $0.47 (677 s) + GGUF $0.18
(662 s) = **$1.02**; program total **$3.93** of the $15 envelope
(was $2.91 before run 2).

## Publish — PUBLISHED (bf16 commit 6ce42fe; GGUF commit fe7e6b4, hash-verified)

- GGUF conversion job `6b09f5e36c5d61fff189e449` COMPLETED
  (a10g-small, 662 s, $0.18, exact run-1 recipe):
  `netconfig-yang-qwen3-4b-q4_k_m.gguf` = 2,497,280,256 bytes
  in the bucket at `yang-learn-run2/gguf/` (DONE.json verified).
- Publish staging (`outputs/yang-learn-run2/model-staging/`)
  is complete and verified: both bf16 shards at exact bucket
  sizes with safetensors headers parsed (229 + 169 = 398
  tensors; index total reconciles), all tokenizer/config files
  size-verified, GGUF size-verified with GGUF magic. Two
  download incidents were caught by verification and repaired:
  a whole-file-buffering OOM (rewrote the downloader to stream)
  and silent mid-stream truncations by the proxy (shard 2 and
  index.json arrived short; a size-verifying downloader with
  retries replaced them — every staged file now matches its
  bucket size exactly).
- The folder upload (`hfup_yang_run2.py folder`, Xet) paused
  on a user approval card; once approved it COMPLETED:
  bf16 commit
  `6ce42fecc081127067177f83b09f05171762d696`. Hub API
  verified: HEAD moved from run 1's `dcc6f59` to `6ce42fe`,
  every served file at its exact staging size (shards
  4,967,215,360 + 3,077,766,632 bytes), and the run-2 model
  card (both runs' tables) is live.
- The GGUF upload (`hfup_yang_run2.py gguf`) likewise
  paused on an approval card and COMPLETED once approved:
  commit `fe7e6b43c6b802c7d2108bc54020ae3d1dd67181`.
  Final Hub API verification: HEAD = `fe7e6b4`; the served
  GGUF's LFS sha256
  (`9d487f4f89829da6948eced240e21d4eb3c1cfb0306736f9bf797f0937bca2cd`)
  matches the local run-2 GGUF exactly — proving the served
  file is run 2's, not run 1's (the two are identical in
  size, 2,497,280,256 bytes, by construction). Both bf16
  shards' LFS hashes likewise match staging. Publish is
  complete: https://huggingface.co/aag1091/netconfig-yang-qwen3-4b
  now serves the run-2 model (bf16 + Q4_K_M GGUF + run-2
  model card).
