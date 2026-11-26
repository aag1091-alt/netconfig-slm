"""Synthetic data generation interface.

Two jobs, both LLM-assisted but validator-gated:
1. intent_writer: given a validated config block (from a guide), write the NL intent a user would have typed.
2. config_expander: given a seed intent + parameter variations (VLAN ids, IPs, interfaces), generate new intent/config pairs.

NEVER write directly to processed/. Output goes to data/raw/synthetic_candidates.jsonl,
then scripts/validate_dataset.py must pass rung 1+2 before promotion.

Provider is pluggable: set SYNTH_MODEL env to any OpenAI-compatible endpoint.
This stub defines the interface and prompt templates; wiring a live model is a deliberate step.
"""
import json, os
from pathlib import Path

INTENT_WRITER_PROMPT = """You are writing training data for a network config model.
Given this Cisco IOS XE config, write ONE natural-language instruction that a network engineer would type to request exactly this config. Be specific: include all VLAN IDs, names, interfaces, IPs, AS numbers present in the config. Do not add anything not in the config. One sentence or two short sentences.

Config:
{config}

Instruction:"""

VARIATION_PROMPT = """Given this seed example, create {n} variations by changing only the parameters listed: {vary}.
Keep the same config structure and IOS XE syntax. Return JSONL with fields intent_nl and config.

Seed intent: {intent}
Seed config:
{config}
"""

def write_intent_for_config(config: str, llm_call=None) -> str | None:
    """llm_call(prompt)->str injected for testability. Returns None if no provider configured."""
    if llm_call is None:
        return None  # caller decides provider; we don't silently call anything
    return llm_call(INTENT_WRITER_PROMPT.format(config=config)).strip()

def load_seeds(path: str = "data/examples/seed.jsonl") -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]

if __name__ == "__main__":
    seeds = load_seeds()
    print(f"{len(seeds)} seed examples loaded. Provider configured: {bool(os.getenv('SYNTH_MODEL'))}")
    print("This is an interface stub - no generation run without an explicit provider + validator pass.")
