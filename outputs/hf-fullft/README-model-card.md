---
license: apache-2.0
base_model: Qwen/Qwen3-4B-Base
datasets:
  - aag1091/netconfig-slm-dataset
language:
  - en
pipeline_tag: text-generation
tags:
  - network-automation
  - cisco
  - ios-xe
  - configuration-generation
  - qwen3
---

# NetConfig SLM — Qwen3-4B fine-tuned for Cisco IOS XE config generation

A purpose-built small language model that turns plain-English network intents
into Cisco IOS XE device configurations. This is the v0 model of the
NetConfig SLM project, built in public — progress log:
[slm.aag1091.com](https://slm.aag1091.com).

- **Base model:** [Qwen/Qwen3-4B-Base](https://huggingface.co/Qwen/Qwen3-4B-Base)
- **Method:** full-parameter fine-tune (no adapters)
- **Domain:** Cisco IOS XE configuration generation from natural-language intent

## Training data

Trained on the
[aag1091/netconfig-slm-dataset](https://huggingface.co/datasets/aag1091/netconfig-slm-dataset)
v1.0 — 5,000 intent→config pairs across 6 families:

- VLAN / L2 switching
- Trunk ports & SVIs
- Static routing
- OSPF (single area)
- BGP (eBGP basics)
- Interface & management basics

Splits: 4,496 train / 252 validation / 252 test (stratified, seed 42). Every
training completion ends with a Cisco `end` line before the EOS token.

## Training

- Full-parameter fine-tune of all 4.02B parameters, bf16
- Learning rate 1e-5, cosine schedule, 2 epochs, effective batch 16,
  max length 1536
- Final train loss: **0.0254**
- Trained on Hugging Face Jobs (A100-large), run cost **$3.19**

## Evaluation

Held-out 252-row test split, **raw (untruncated) generations**, scored with
the project's home validation ladder (ciscoconfparse2 → Batfish → intent-match):

| Check | Result |
|---|---|
| Parse (ciscoconfparse2) | **100%** (252/252) |
| Batfish semantic validation | **98.8%** (249/252) |
| Intent match (raw checker) | 86.9% (219/252) |
| Intent match (triaged*) | **92.5%** (233/252) |
| Clean-stop rate | 94.8% (239/252) |

*Triaged: flagged rows in two documented checker extraction-limit classes
were independently re-verified by re-parsing; 14 of 26 flags were checker
artifacts, 12 were real mismatches.

Per-family Batfish: BGP 100%, interface & mgmt 100%, OSPF 100%,
trunk & SVI 100%, static routing 97.6%, VLAN/L2 95.2%.

These scores cleared the project's pre-registered publish bar
(Batfish ≥ 95% and intent-match ≥ 90%).

## Usage

ChatML prompt format — the system/user turn carries the intent, the
assistant turn is the configuration:

```
<|im_start|>user
Create VLAN 100 named USERS and assign GigabitEthernet1/0/1 as an access port in that VLAN on switch SW1.
<|im_end|>
<|im_start|>assistant
```

The model's `generation_config.json` already carries the EOS ids
`[151645, 151643]`. Greedy decoding was used for all evaluation numbers
above. A Q4_K_M GGUF build (`netconfig-slm-qwen3-4b-q4_k_m.gguf`) is
included in this repo for local use with llama.cpp / Ollama.

## Limitations

- **Simulator-validated only.** Correctness was established with Batfish
  simulation, not on physical hardware or an emulator (v0 scope).
- Very long access-port lists can truncate at the generation cap
  (12 of the 13 non-clean-stop generations were mid-config truncations,
  mostly long VLAN/L2 port lists).
- Occasional hostname slips (6 of 252 test generations emitted a wrong
  hostname while otherwise matching the intent).
- Scope is the 6 v0 families above on Cisco IOS XE only; no other
  vendors or feature areas yet.

## License

Apache-2.0. Base model Qwen3-4B-Base is also Apache-2.0.
