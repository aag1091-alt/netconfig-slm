# NetConfig SLM

A purpose-built small language model that turns plain-English intent into correct network device configurations.

**Start:** Cisco IOS XE. **Intent format:** OpenConfig-style structured intent + natural language. **Approach:** Fine-tune, don't pretrain. **License:** Apache 2.0.

> Status — 2026-11-26: dataset v1.1 (5,861 pairs, 9 families) and the first model are published. Batch 9 (ACLs, NAT/PAT, HSRP) is mid-validation toward v1.2. Phase 2 — semantic parsing into typed change plans, compiled deterministically — is in design review (see `docs/PLAN_CONTRACT.md`).

## Published artifacts

- Dataset: https://huggingface.co/datasets/aag1091/netconfig-slm-dataset
- Model (Qwen3-4B full fine-tune + GGUF for Ollama): https://huggingface.co/aag1091/netconfig-slm-qwen3-4b
- Progress log: https://slm.aag1091.com
- Prework post: https://avinashgosavi.com/post/building-a-purpose-built-small-model-the-prework/

Model v1 scores on the held-out test split (raw, untruncated generations): parse 100%, intent-match 92.5% (triaged), Batfish 98.8%. The publish bar is Batfish ≥95% and intent-match ≥90%.

## Why small?

- Runs on-prem / on the box, no config leaves the building
- Deterministic, auditable grammar learning — configs are a DSL, not open-ended reasoning
- Cheap to train: the published model is a full fine-tune that cost ~$3 on a rented A100

## How it works

1. **Generate from intent outward.** Each family generator samples a valid structured intent, renders the IOS XE config deterministically, and writes natural-language surface forms. Flagged candidates are dropped and regenerated, never patched.
2. **Gate every row.** Dedupe (exact + shape signature) → intent-match checker → ciscoconfparse2 parse → Batfish semantic validation.
3. **Score honestly.** Raw generations on the held-out split; intent failures triaged by hand before any number is published. `outputs/hf-fullft/DIAGNOSIS.md` documents why QLoRA could not teach the base model to stop (tied embeddings froze the stop token's output row) and why full fine-tuning fixed it.

## Repo map

- `scripts/` — family generators (`gen_batch*.py`), the intent-match checker (`intent_match.py`), Batfish validation runners, dataset builders, training/scoring utilities
- `src/plan/` — Phase 2 plan engine: typed device state, plan schema/validation, `apply()`, CLI renderer, state diff
- `docs/` — `V0_SCOPE.md` (locked v0 scope), `PHASE1_PLAN.md` (family expansion), `PLAN_CONTRACT.md` (Phase 2 operation contract)
- `data/` — dataset builds (`v1.0/`, `v1.1/`), generated batches, Phase 2 slice (`plan_slice/`: hand-authored golden diffs + contrast triples)
- `outputs/` — training job outputs, scoring records (`SCORED.json`), diagnoses

Model weights are not in this repo — they live on Hugging Face. Publish-scratch files that can carry auth headers (`outputs/hf-fullft/lfs-*.json`) are gitignored by design.

## Validation ladder

1. **Syntax** — `ciscoconfparse2` parses without error
2. **Semantics** — Batfish: interfaces up, VLANs present, routing adjacencies form as intended
3. **Emulation** — containerlab / CML boot check (deferred; the v1 gate)

Anything failing rung 1 or 2 is dropped from training data.
