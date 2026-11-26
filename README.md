# NetConfig SLM

A purpose-built small language model that turns plain-English intent into correct network device configurations.

**Start:** Cisco IOS XE. **Intent format:** OpenConfig YANG (vendor-neutral) + natural language. **Approach:** Fine-tune, don't pretrain. **License target:** Apache 2.0 (Qwen3 base).

> Status: v0 scaffolding — Nov 22, 2026. No dataset or model yet. See `docs/V0_SCOPE.md` for what's in/out.

## Why small?

- Runs on-prem / on the box, no config leaves the building
- Deterministic, auditable grammar learning — configs are a DSL, not open-ended reasoning
- Demystify fine-tuning: full run should cost ~$2-3 on a rented 4090

## v0 in one line

~5k validated intent → config pairs for everyday Cisco tasks (VLAN, trunk, SVI, static route, OSPF, BGP basics), QLoRA fine-tune of Qwen3-4B-Base, then step down to 1.7B / 0.6B to find the floor.

## Repo layout

```
configs/          training + data configs (YAML)
data/
  raw/            source PDFs / guide extracts (not redistributed if licensed)
  examples/       hand-written seed examples (committed)
  processed/      validated JSONL dataset (generated)
src/
  data/           extraction, synthetic generation, scrubbing
  validate/       ciscoconfparse2 -> Batfish -> emulation ladder
  train/          QLoRA training (Unsloth / TRL)
  eval/           held-out + validity benchmark
scripts/          CLI entry points
tests/
docs/
```

## Quick start (once data exists)

```bash
pip install -r requirements.txt
python scripts/validate_dataset.py --input data/processed/train.jsonl
python -m src.train.train_qlora --config configs/train_qwen3_4b.yaml
python -m src.eval.evaluate --model outputs/qwen3-4b-netconfig --split test
```

## Validation ladder

1. **Syntax** — `ciscoconfparse2` parses without error
2. **Semantics** — Batfish: interfaces up, VLANs present, routing adjacencies form as intended
3. **Emulation** — containerlab / CML boot check (v0: optional, v1: required for release)

Anything failing rung 1 or 2 is dropped from training data.

## Publishing (planned)

- HF Hub: merged bf16 safetensors + GGUF Q4_K_M / Q8_0 repos
- Dataset: Parquet + dataset card on HF Hub
- Ollama: `ollama run hf.co/<user>/netconfig-slm-GGUF`

## Links

- Prework post (draft, not yet published): `../files/2026-11-08-building-a-purpose-built-small-model-the-prework.md`
- Research notes: `../claude-source/blog-post-prework-research-notes.md`
- Reference paper: arXiv 2512.02861 — Network Self-Configuration based on Fine-Tuned SLMs (~10.2k pairs from IOS XE guides)
