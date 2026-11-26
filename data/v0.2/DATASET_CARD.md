---
license: apache-2.0
language:
  - en
task_categories:
  - text-generation
size_categories:
  - 1K<n<10K
configs:
  - config_name: default
    data_files:
      - split: train
        path: train.jsonl
      - split: validation
        path: val.jsonl
      - split: test
        path: test.jsonl
---

# NetConfig SLM — Dataset v0.2

Instruction-style examples that map a network configuration request to a
Cisco IOS XE configuration. Each example carries the request three ways: a
natural-language intent (`intent_nl`), an OpenConfig-style structured intent
(`intent_oc`, included where the mapping is clean), and the target IOS XE
CLI configuration (`config`).

This is **v0.2** of the NetConfig SLM dataset — a purpose-built small
language model for network management and configuration, built in public,
starting with Cisco IOS XE. It is a grammar-learning dataset:
deterministic, everyday configuration tasks, not open-ended network
engineering advice. v0.2 doubles v0.1 with a harder generation wave and
applies the fixes from the first intent-match audit (see Changelog).

## Dataset structure

One JSON object per line (JSONL):

| Field | Description |
|---|---|
| `id` | Unique example ID (e.g. `vlan-0001`) |
| `family` | One of the 6 task families below |
| `intent_nl` | The request as a user would type it, with concrete IDs, names, interfaces, IPs |
| `intent_oc` | OpenConfig-style structured intent (optional in v0; the multi-vendor bridge) |
| `device` | Target device context: vendor `cisco`, OS `ios_xe`, version, role, hostname |
| `config` | Complete, applicable IOS XE CLI config block (no `enable`/`configure terminal` wrappers) |
| `source` | `handwritten` or `synthetic` |
| `source_ref` | Provenance reference or `null` |
| `validation` | `parse_ok` / `batfish_ok` / `emulation_ok` flags (see Validation) |
| `notes` | Free-form notes |

### Task families (v0 scope)

| Family | Count | Covers | Explicitly out of scope in v0 |
|---|---:|---|---|
| `vlan_l2` | 197 | Create VLAN, name it, access-port assignment | VTP, private VLANs, MSTP tuning |
| `trunk_svi` | 197 | 802.1Q trunks, allowed VLANs, SVI + IP | Advanced LACP, QinQ |
| `static_route` | 197 | IPv4 statics, default route, floating statics | IPv6, PBR |
| `ospf` | 197 | Single-area OSPF, network statements / interface mode, passive-interface | Multi-area, redistribution, auth |
| `bgp` | 197 | eBGP peering, network advertisement, basic policy | iBGP route reflectors, communities, complex route-maps |
| `interface_mgmt` | 195 | Interface description/IP/no-shutdown, SSH/vty basics (scrubbed) | Full AAA/TACACS, SNMPv3 |

## How the data was made

- **100 hand-written seed examples** define the format and style.
- **480 synthetic examples** (batches 1–3) were generated programmatically
  (template / parameter variation over the seed patterns).
- **600 further synthetic examples** (batch 4) use harder, multi-part
  requests — several VLANs, peers, routes or interfaces per example — and
  passed a generator-side intent-match self-check before validation.
- Nothing is mined from production networks or copied from Cisco
  documentation.
- **Scrubbing rules:** no secrets, no type-7 hashes, no SNMP community
  strings (a `REDACTED` placeholder is used where a command requires the
  slot); assigned IP addresses are RFC1918 or IETF documentation ranges
  only; hostnames normalized (`sw1`, `r1`, …).

## Validation

Every example passed both rungs of the project's validation ladder before
inclusion — **1,180/1,180**:

1. **Syntax parse** with ciscoconfparse2.
2. **Semantic validation** with Batfish (snapshot parses as CISCO_IOS with
   zero initialization issues).

A third rung — emulation in containerlab — is a spot check in v0 and a
required gate in v1; `emulation_ok` is therefore `null` throughout v0.

**Intent-match audit.** A dedicated checker (`scripts/intent_match.py`)
additionally verifies that each config does what its intent asked —
entities extracted from the intent (VLAN IDs/names, interfaces, IPs,
ASNs, next-hops, per-peer BGP attributes…) are asserted in the config in
the right role. Round 1 audited v0.1 (580/580, 3,686 assertions, plus a
60-row human read-through: 58 full matches, 2 partial, 0 mismatches) and
produced the fix list applied in v0.2 (see Changelog). The full v0.2
corpus passes the same checker, and every row changed by the fixes was
re-run through both ladder rungs. Per-run validation logs ship with the
project repository.

## Conventions

Configs may include conventional boilerplate that the intent does not
restate; treat these as fixed conventions, not extra requirements:

- **BGP `bgp router-id`** is present in BGP configs even when the intent
  does not state one (derived from the device's addressing plan).
- **Interface `description` lines** echo the role labels in the request
  (e.g. "Trunk to distribution") or a standard role label for the
  interface's function.
- **`ip ssh version 2`** appears on SSH/management rows as the standard
  SSH setting.
- **Hostnames** in configs are the expanded form of the device hostname
  (e.g. `sw1` → `switch1`, `r1` → `router1`).

## Splits

90 / 5 / 5 train / validation / test, stratified by family, reproducible
(random seed **42**):

| Split | Rows | Per family |
|---|---:|---|
| train | 1,060 | 177 (175 for `interface_mgmt`) |
| validation | 60 | 10 |
| test | 60 | 10 |

The test split is held out and is never trained on. `dataset.jsonl`
contains the full 1,180-row corpus; `stats.json` records the full counts,
the integrity checks (all IDs unique, no duplicate intent/config pairs)
and the v0.2 fix log.

## Changelog

### v0.2 (2026-11-23)

- **+600 examples** (batch 4): 100 per family, harder multi-part
  requests; corpus grows 580 → 1,180.
- **Removed 32 invented VLAN name lines** in `trunk_svi` configs — names
  the intent never gave (round-1 audit's one substantive finding; the
  `vlan <id>` blocks themselves are kept).
- **Canonicalised two BGP aggregate prefixes** (bgp-0093, bgp-0094) in
  intent and config: 10.43.0.0/15 → 10.42.0.0/15 and
  10.51.0.0/15 → 10.50.0.0/15 (the true /15 network bases, as IOS would
  normalise on entry).
- **Normalised glued `!` separators** (48 lines in 36 rows): every block
  separator now sits on its own line.
- **Documented the boilerplate conventions** (above) instead of leaving
  them implicit.

### v0.1 (2026-11-23)

- First publish: 580 validated pairs (100 hand-written seeds + 480
  synthetic), 90/5/5 splits.

## Intended use

Fine-tuning and evaluating small language models for network
configuration generation (the project fine-tunes Qwen3-4B with QLoRA
first, then steps down to 1.7B / 0.6B), and evaluating
config-generation systems on parse and semantic-validity rates per
family. Training converts each row to chat format with `intent_nl` as
the user turn and `config` as the assistant turn.

## Limitations

- **Synthetic, template-generated data.** The synthetic majority varies
  parameters over a limited set of patterns; phrasing and structural
  diversity are narrower than real-world requests. Expect a model
  trained only on v0.2 to be brittle outside these patterns.
- **Cisco IOS XE only.** No other vendors or OSes yet; the OpenConfig
  intent field is the intended bridge, not a tested one.
- **Narrow v0 scope.** Only the six families above, and only the
  "covers" column — see the exclusions in the family table.
- **Validation is static.** Batfish checks snapshot-level semantics; it
  does not prove a config behaves correctly on a live device, and no
  example has been boot-tested in an emulator.
- **Still growing.** 1,180 examples is on the way to the ~5,000 pairs
  the full v0 targets.
