# HF QLoRA smoke train — wrap-up (job 6b046ae8c97b8ed4fcd5cb6a)

- Stage: COMPLETED. Created 2026-11-23T16:33:44Z, started 16:33:49Z, finished 17:47:10Z.
- Durations (job API): scheduling 4s, running 4,400s, total 4,405s = 73.4 min.
- Flavor a10g-large at $1.50/hr → estimated cost $1.84 (4,405/3,600 × $1.50).
- In-job elapsed: 72.8 min. Train runtime 1,621.5s, 201 steps, 3 epochs.
- Train loss: 0.3791799716540237.

## Correction to the watcher's stated facts
The job script fetched train/test from the HF dataset repo's main branch at
fetch time. v0.2 was published to that repo at 16:33 UTC, the same minute the
job was created. Evidence the job actually trained/evaluated on **v0.2**, not
v0.1: (1) all 60 generation IDs match data/v0.2/test.jsonl (60/60; only 2/60
match v0.1 test); (2) 201 steps × effective batch 16 ÷ 3 epochs ≈ 1,060 train
rows = v0.2 train size (v0.1 train is 520, which would give ~99 steps).

## In-job evals (60 held-out test rows, ciscoconfparse2 parse + key-token coverage)
- Base Qwen3-4B-Base: parse 100.0%, mean coverage 0.706.
  Per family coverage: bgp 0.78, interface_mgmt 0.642, ospf 0.729,
  static_route 0.677, trunk_svi 0.61, vlan_l2 0.80. Parse 100% everywhere.
- Fine-tuned: parse 100.0%, mean coverage 0.983.
  Per family coverage: bgp 0.90, all other five families 1.00.
  Weakest fine-tuned family in-job: BGP.

## Home ladder on generations_ft.jsonl (raw, exactly as generated)
Scored against data/v0.2/test.jsonl matched by id (see correction above);
temp dataset-shaped file: ft_as_dataset.jsonl.
- ciscoconfparse2 parse: 60/60 pass (100%) — note this rung only requires a
  non-empty parse, so it is lenient.
- Batfish: 0/60 pass (0%). Every row PARTIALLY_UNRECOGNIZED (CISCO_IOS) with
  1–6 init issues. Full output: validate_ft_batfish.txt.
- Intent-match (scripts/intent_match.py): 33/60 pass (55.0%).
  Per family: bgp 6/10, interface_mgmt 7/10, ospf 4/10, static_route 6/10,
  trunk_svi 5/10, vlan_l2 5/10.
  Failures: 27 hostname + 1 bgp_network across 27 failing rows.

## Root cause
All 60/60 generations contain hallucinated fake-turn continuations after the
first complete config (garbled turn markers, then further unrelated
configs/prompts). The model did not stop after one config within the
512-token generation budget, so every raw output is a polluted multi-config
blob — that is what Batfish rejects.

## Diagnostic (not the headline): first config only
Generations truncated at the first fake-turn marker (ft_first_config_only.jsonl):
- Parse: 60/60.
- Intent-match: 44/60 (73.3%). Per family: bgp 6/10, interface_mgmt 7/10,
  ospf 8/10, static_route 6/10, trunk_svi 8/10, vlan_l2 9/10.
  Failures: 15 hostname + 1 bgp_network. Excluding hostname, 59/60 first
  configs pass every intent check — the config content itself is largely
  learned; hostname is systematically wrong in 15/60 first configs
  (model defaults toward router1/switch1-style names).
- Batfish (first-config-only): 60/60 PASS (100%, CISCO_IOS, 0 init issues).
  Full output: validate_ft_first_only_batfish.txt. So the first configs are
  Batfish-clean; the raw 0/60 is entirely continuation pollution.

## Recommendation
Do NOT publish as the first model repo yet. The locked v0 scope says no
publish until evals pass a bar set after the first run; on the raw outputs
the model clears neither a Batfish bar (0%) nor an intent-match bar (55%).
Fix order: (1) stopping/EOS — make generation emit and honour the end token
so one prompt yields exactly one config, then re-run the home ladder;
(2) hostname fidelity (15/60 first configs wrong). If the first-config-only
Batfish result is strong, the content bar is close and the blocker is
generation hygiene, not training quality.
