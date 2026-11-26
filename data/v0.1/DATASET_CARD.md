---
license: apache-2.0
language:
  - en
task_categories:
  - text-generation
size_categories:
  - n<1K
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

# NetConfig SLM — Dataset v0.1

Instruction-style examples that map a network configuration request to a
Cisco IOS XE configuration. Each example carries the request three ways: a
natural-language intent (`intent_nl`), an OpenConfig-style structured intent
(`intent_oc`, included where the mapping is clean), and the target IOS XE
CLI configuration (`config`).

This is **v0.1**: a small, fully validated starter corpus for the NetConfig
SLM project — a purpose-built small language model for network management
and configuration, built in public, starting with Cisco IOS XE. It is a
grammar-learning dataset: deterministic, everyday configuration tasks, not
open-ended network engineering advice.

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
| `vlan_l2` | 97 | Create VLAN, name it, access-port assignment | VTP, private VLANs, MSTP tuning |
| `trunk_svi` | 97 | 802.1Q trunks, allowed VLANs, SVI + IP | Advanced LACP, QinQ |
| `static_route` | 97 | IPv4 statics, default route, floating statics | IPv6, PBR |
| `ospf` | 97 | Single-area OSPF, network statements / interface mode, passive-interface | Multi-area, redistribution, auth |
| `bgp` | 97 | eBGP peering, network advertisement, basic policy | iBGP route reflectors, communities, complex route-maps |
| `interface_mgmt` | 95 | Interface description/IP/no-shutdown, SSH/vty basics (scrubbed) | Full AAA/TACACS, SNMPv3 |

## How the data was made

- **100 hand-written seed examples** define the format and style.
- **480 synthetic examples** were generated programmatically (template /
  parameter variation over the seed patterns) — not mined from production
  networks and not copied from Cisco documentation.
- **Scrubbing rules:** no secrets, no type-7 hashes, no SNMP community
  strings (a `REDACTED` placeholder is used where a command requires the
  slot); IP addresses are RFC1918 or IETF documentation ranges only;
  hostnames normalized (`sw1`, `r1`, …).

## Validation

Every example in this dataset passed both rungs of the project's validation
ladder before inclusion — **580/580**:

1. **Syntax parse** with ciscoconfparse2.
2. **Semantic validation** with Batfish (snapshot parses as CISCO_IOS with
   zero initialization issues).

Three OSPF examples failed their first Batfish run (an OSPF process with no
router-id and no addressed interface leaves Batfish nothing to derive a
router-id from); they were corrected, re-validated individually, and pass.
A third rung — emulation in containerlab — is a spot check in v0 and a
required gate in v1; `emulation_ok` is therefore `null` throughout v0.1.
Per-run validation logs ship with the project repository.

## Splits

90 / 5 / 5 train / validation / test, stratified by family, reproducible
(random seed **42**):

| Split | Rows | Per family |
|---|---:|---|
| train | 520 | 87 (85 for `interface_mgmt`) |
| validation | 30 | 5 |
| test | 30 | 5 |

The test split is held out and is never trained on. `dataset.jsonl` contains
the full 580-row corpus; `stats.json` records the full counts and the
integrity checks (all IDs unique, no duplicate intent/config pairs).

## Intended use

Fine-tuning and evaluating small language models for network configuration
generation (the project fine-tunes Qwen3-4B with QLoRA first, then steps
down to 1.7B / 0.6B), and evaluating config-generation systems on parse and
semantic-validity rates per family. Training converts each row to chat
format with `intent_nl` as the user turn and `config` as the assistant turn.

## Limitations

- **Synthetic, template-generated data.** The synthetic majority varies
  parameters over a limited set of patterns; phrasing and structural
  diversity are narrower than real-world requests. Expect a model trained
  only on v0.1 to be brittle outside these patterns.
- **Cisco IOS XE only.** No other vendors or OSes yet; the OpenConfig
  intent field is the intended bridge, not a tested one.
- **Narrow v0 scope.** Only the six families above, and only the "covers"
  column — see the exclusions in the family table.
- **Validation is static.** Batfish checks snapshot-level semantics; it
  does not prove a config behaves correctly on a live device, and no
  example in v0.1 has been boot-tested in an emulator.
- **Small.** 580 examples is a pipeline-proving starter set, not the ~5,000
  pairs the full v0 targets.
