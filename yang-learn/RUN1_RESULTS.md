# yang-learn run 1 — results (2026-11-26)

Full fine-tune of Qwen/Qwen3-4B-Base on corpus v1 train (1,047
records, commit 5189d33), HF job `6b08f2266c5d61fff1883299`,
a100-large, run-3 config (epochs 2, eff. batch 16, max_len 1536,
lr 1e-5). Script: `job_train_yanglearn_v1.py`. Grader:
`score_bench.py`. Raw generations + full scores:
`outputs/yang-learn-run1/` (local) and the job bucket folder
`yang-learn-run1/` in `aag1091/netconfig-train-output`.

## Job outcome — trained fully, killed in the generation phase

The job hit its 3,600 s timeout (stage ERROR, "Job timeout") at
3,601 s total. Everything that mattered completed first, in the
script's save-before-generate order:

- Dataset guard passed (sha256 + row counts on all four files).
- Ending checks all_ok; **EOS assertion PASSED** (every row's last
  non-pad label == 151645).
- Training: 132 steps in **328.1 s**, final train loss **0.1837**.
- Model saved: bf16 safetensors (2 shards, ~8.05 GB) +
  tokenizer + generation_config (eos [151645, 151643]).
- Benchmark generations completed: 217/217 (150 stopped cleanly,
  69.1%). Dev generations did not complete; no DONE.json.

Cost: **$2.50** (the cap, exactly as designed) + ~$0.12 estimated
for the cancelled first submission (wrong timeout field name,
cancelled inside 4 minutes, resubmitted correctly). Program total
≈ **$2.62** of the $15 envelope.

Timeline lesson: training was 328 s of the 3,594 s. Startup plus
**sequential** greedy generation ate the rest (217 bench rows ≈
50 min; non-stopping rows burn the full 768-token budget). Future
eval passes should batch generation or run as a dedicated job
with its own timeout.

## Bench v1 scores — mechanical grading (SCALE_DESIGN §3 + A2)

| family | pass | % |
|---|---|---|
| state_reconstruction | 40/40 | **100.0** |
| typed_edit | 38/42 | 90.5 |
| instance_validity (verdict) | 56/73 | 76.7 |
| revised_schema (strict: verdict + citation) | 13/28 | 46.4 |
| revised_schema (verdict only, diagnostic) | 26/28 | 92.9 |
| path_meaning (verbatim type expression) | 13/34 | 38.2 |
| **overall** | **160/217** | **73.7** |

Benchmark-only: 156/209 (74.6%). Held-out only: 4/8 (50.0%).
Pass rate when the generation stopped cleanly: 83.3% (125/150);
when it did not: 52.2% (35/67). Of 33 invalid IV items, the model
named the expected node in 12.

## Reading of the failures (spot-checked against the grader)

- **State manipulation transferred perfectly**: SR is 100% on
  unseen bench interfaces; the one held-out SR-form failure
  (HO-06) is a single wrong leaf name (`vlan` for `vlan-id`)
  deep in an otherwise identical state.
- **Profile reasoning is strong, citation discipline is not**:
  RS verdicts are 92.9% right on profiles never seen in training
  (P2/P6), but the model usually omits the exact `P# rule (n)`
  citation the strict scorer requires.
- **Type-expression recall is the real gap**: PM fails are wrong
  types (e.g. answering `union {string, uint16}` for a pattern
  typedef leaf), not formatting near-misses.
- **Stopping discipline**: a third of generations ramble past a
  correct answer into fabricated follow-up Q&A; correctness and
  clean stopping are strongly correlated.

## Status — PUBLISHED 2026-11-26

Published at Avinash's direction ("publish whenever ready, no
need to wait for me"):

- **https://huggingface.co/aag1091/netconfig-yang-qwen3-4b**
  (public, apache-2.0) — bf16 safetensors (both shards verified
  at exact sizes), tokenizer files, and the model card with the
  bench v1 table and caveats. Folder commit `8d2c8ef`.
- **GGUF Q4_K_M added** (commit `dcc6f59`): conversion job
  `6b09127054072e8b71694012` (a10g-small, 659 s, **$0.18**),
  llama.cpp convert + quantize, 2,497,280,256 bytes, verified
  in-repo. Program spend now ≈ **$2.80** total.

Still open as review decisions: Codex's read of these results,
and whether run 2 drills the PM/RS gaps (citation format,
typedef recall) or expands corpus coverage first. Dev-set
generations were never produced (the training job died during
that phase); a batched eval job can produce them under the
standing <$15 approval whenever wanted.
