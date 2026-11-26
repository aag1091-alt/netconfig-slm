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

# NetConfig SLM — Dataset v1.1

Instruction-style examples that map a network configuration request to a
Cisco IOS XE configuration. Each example carries the request three ways: a
natural-language intent (`intent_nl`), an OpenConfig-style structured intent
(`intent_oc`, included where the mapping is clean), and the target IOS XE
CLI configuration (`config`).

This is **v1.1** of the NetConfig SLM dataset — a purpose-built small
language model for network management and configuration, built in public,
starting with Cisco IOS XE. It is a grammar-learning dataset:
deterministic, everyday configuration tasks, not open-ended network
engineering advice. **v1.1 opens Phase 1** (the expansion toward complete
IOS XE device configuration): three new Layer-2 families — EtherChannel,
spanning tree, and port security — add 861 examples, growing the corpus
from the complete v1.0 Cisco v0 dataset (5,000) to **5,861 examples**
across 9 families. No retrain accompanies v1.1; retraining happens at
milestones, bundling several new families.

## Dataset structure

One JSON object per line (JSONL):

| Field | Description |
|---|---|
| `id` | Unique example ID (e.g. `vlan-0001`, `ether-0001`) |
| `family` | One of the 9 task families below |
| `intent_nl` | The request as a user would type it, with concrete IDs, names, interfaces, IPs |
| `intent_oc` | OpenConfig-style structured intent (optional; the multi-vendor bridge) |
| `device` | Target device context: vendor `cisco`, OS `ios_xe`, version, role, hostname |
| `config` | Complete, applicable IOS XE CLI config block (no `enable`/`configure terminal` wrappers) |
| `source` | `handwritten` or `synthetic` |
| `source_ref` | Provenance reference or `null` |
| `validation` | `parse_ok` / `batfish_ok` / `emulation_ok` flags (see Validation) |
| `notes` | Free-form notes |

### Task families

| Family | Count | Covers | Explicitly out of scope |
|---|---:|---|---|
| `vlan_l2` | 834 | Create VLAN, name it, access-port assignment | VTP, private VLANs |
| `trunk_svi` | 834 | 802.1Q trunks, allowed VLANs, SVI + IP | QinQ |
| `static_route` | 834 | IPv4 statics, default route, floating statics | IPv6, PBR |
| `ospf` | 834 | Single-area OSPF, network statements / interface mode, passive-interface | Multi-area, redistribution, auth (multi-area arrives in a later Phase-1 batch) |
| `bgp` | 834 | eBGP peering, network advertisement, basic policy | iBGP route reflectors, communities, complex route-maps |
| `interface_mgmt` | 830 | Interface description/IP/no-shutdown, SSH/vty basics (scrubbed) | Full AAA/TACACS, SNMPv3 |
| `etherchannel` | 287 | `channel-group` LACP active/passive and static `on`, Port-channel trunks (allowed/native VLANs) and access bundles, `port-channel min-links`, global `port-channel load-balance` | PAgP negotiation details, LACP port priority tuning |
| `stp` | 287 | `spanning-tree mode` rapid-pvst/pvst/mst, per-VLAN priority, PortFast + BPDU guard (per-port and by-default), root guard / loop guard / PortFast trunk on uplinks | MST region/instance mapping, STP cost tuning |
| `port_security` | 287 | `switchport port-security` with maximum, violation shutdown/restrict/protect, sticky and static MACs, aging time/type | Dynamic ARP inspection, DHCP snooping (separate features) |

## How the data was made

- **100 hand-written seed examples** define the format and style.
- **480 synthetic examples** (batches 1–3) were generated programmatically
  (template / parameter variation over the seed patterns).
- **600 further synthetic examples** (batch 4) use harder, multi-part
  requests — several VLANs, peers, routes or interfaces per example — and
  passed a generator-side intent-match self-check before validation.
- **900 further synthetic examples** (batch 5) widen the variety again:
  branch / campus / DC-leaf / small-office device roles and hostname
  styles, terse and verbose phrasings, up to 5 VLANs or 6 static routes
  per example, and six constructs admitted after a clean probe pass.
- **1,200 further synthetic examples** (batch 6) add work-framed
  phrasings: ~22% of intents are wrapped in ticket / ops-note framing,
  with new head/frame styles across families and larger requests.
- **1,720 further synthetic examples** (batch 7, the final Cisco v0 wave)
  complete the v0 corpus at exactly 5,000 (see the v1.0 changelog entry).
- **861 further synthetic examples** (batch 8, Phase 1 batch 1) add the
  three L2 families above, 287 per family. Every new CLI construct was
  probe-passed against Batfish before generation (10/10 probe configs,
  PASSED with zero init issues). The generator's intent-match gate is the
  dataset checker itself (`scripts/intent_match.py`, extended with
  checkers for the three new families), so the generation gate and the
  audit cannot drift apart; the new checkers were mutation-tested (6/6
  hand-corrupted rows caught). Dedupe rejected 0 candidates and the gate
  dropped 0 — and all 861 rows then passed both validation rungs, so
  batch 8 contributes its full 861 rows with zero drops at any gate.
  Configs are deliberately kept short (≤4 ports per row, ≤8 allowed
  VLANs): the one real defect class of the v1.0-trained model was
  truncation on long port lists.
- Nothing is mined from production networks or copied from Cisco
  documentation.
- **Scrubbing rules:** no secrets, no type-7 hashes, no SNMP community
  strings (a `REDACTED` placeholder is used where a command requires the
  slot); assigned IP addresses are RFC1918 or IETF documentation ranges
  only; generated static MACs are locally-administered unicast;
  hostnames normalized (`sw1`, `r1`, …).

## Validation

Every example passed both rungs of the project's validation ladder before
inclusion — **5,861/5,861**:

1. **Syntax parse** with ciscoconfparse2.
2. **Semantic validation** with Batfish (snapshot parses as CISCO_IOS with
   zero initialization issues).

A third rung — emulation in containerlab — is a spot check in v0 and a
required gate in v1; `emulation_ok` is therefore `null` throughout.

**Intent-match audit.** A dedicated checker (`scripts/intent_match.py`)
additionally verifies that each config does what its intent asked —
entities extracted from the intent (VLAN IDs/names, interfaces, IPs,
ASNs, next-hops, per-peer BGP attributes, and for the Phase-1 families:
channel-group / Port-channel numbers, STP mode and guard keywords,
port-security violation mode and maximum count…) are asserted in the
config in the right role. For v1.1 the checker passes 5,619/5,861 rows;
the 242 flagged rows are exactly the two documented extraction-limit
classes already triaged for v0.3–v1.0, with zero real mismatches:

- **147 `vlan_l2` rows** where the checker mis-pairs port groups across
  multiple assignment sentences — all intent port→VLAN pairs in those
  rows were independently re-parsed clause-by-clause and verified
  against the configs in both directions.
- **95 `trunk_svi` rows** in the "… is a trunk permitting VLANs …"
  style, where the checker's trunk-coverage extraction fails — all
  intent trunks (allowed-VLAN sets and native VLANs) were independently
  re-parsed and verified against the configs.

All 861 batch-8 rows pass the checker outright (the generator gate is
the same code). For the two legacy phrasing styles the generator-side
gate (mutation-tested at every batch) is authoritative. Per-run
validation logs ship with the project repository.

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
- **VLAN blocks** in the L2 Phase-1 families are bare (`vlan <id>`
  without a `name` line) unless the intent names the VLAN: in those
  families the VLAN is context, not the subject of the request.

## Splits

90 / 5 / 5 train / validation / test, stratified by family, reproducible
(random seed **42**):

| Split | Rows | Per family |
|---|---:|---|
| train | 5,273 | 750 (746 for `interface_mgmt`; 259 for each Phase-1 family) |
| validation | 294 | 42 (14 for each Phase-1 family) |
| test | 294 | 42 (14 for each Phase-1 family) |

The test split is held out and is never trained on. `dataset.jsonl`
contains the full 5,861-row corpus; `stats.json` records the full counts,
the integrity checks (all IDs unique, no duplicate intent/config pairs)
and the batch-8 drop report (zero drops).

## Changelog

### v1.1 (2026-11-25)

- **+861 examples** (batch 8, Phase 1 batch 1 — the L2 cluster): three
  new families at 287 each — `etherchannel`, `stp`, `port_security`;
  corpus grows 5,000 → **5,861** across 9 families.
- **New CLI surface, probe-passed first:** LACP/static EtherChannel with
  Port-channel trunks and access bundles, min-links and load-balance;
  spanning-tree modes, per-VLAN priorities, PortFast/BPDU guard in
  per-port, list and by-default forms, root/loop guard; port-security
  maximum/violation/sticky/static-MAC/aging.
- **Zero drops at every gate:** dedupe 0, intent-match gate 0, parse
  861/861, Batfish 861/861 (CISCO_IOS, 0 init issues).
- **Checker extended:** `scripts/intent_match.py` gains checkers for
  the three new families and now serves as the generator gate itself;
  mutation-tested 6/6. Full-corpus audit flags only the same 242
  legacy extraction-limit rows as v1.0; all 861 new rows pass.
- **No retrain:** the published model remains the v1.0 full fine-tune;
  the next retrain is a milestone decision bundling several Phase-1
  families.
- No data fixes: v1.0 rows are carried over unchanged.

### v1.0 (2026-11-23)

- **+1,720 examples** (batch 7, the final Cisco wave): 287 per family
  (285 for `interface_mgmt`); corpus grows 3,280 → **5,000** — the
  locked v0 target (2026-11-22: 6 families, ~5,000 validated pairs) is
  complete at exactly 5,000 rows (834 per family, 830 `interface_mgmt`).
- **`interface_mgmt` shape space widened** with four new block
  combinations (routed-port + console, mixed SSH, ports + banner,
  loopback + banner), combining blocks individually probe-passed in
  batches 4–6; no new CLI constructs.
- **Generation gate re-verified:** the intent-match self-check gate was
  mutation-tested 22/22; dedupe rejected 20 candidates on
  shape-signature collisions.
- **Intent-match audit triaged:** full-corpus checker run passes
  4,758/5,000; all 242 flagged rows independently re-verified
  (1,922 VLAN port pairs + 179 trunks) — zero real mismatches, checker
  unchanged (see Validation).
- No data fixes: v0.4 rows are carried over unchanged.
- **Milestone:** Cisco v0 dataset growth stops here. Next step is the
  full QLoRA training run on this dataset (train split: 4,496 rows).

### v0.4 (2026-11-23)

- **+1,200 examples** (batch 6): 200 per family; corpus grows
  2,080 → 3,280. ~22% of intents wrapped in ticket / ops-note framing
  ("Change request:", "Ops note:", "Ticket:", "Work order:"), new
  head/frame styles for static routing, OSPF, BGP and interface/mgmt,
  and larger multi-part requests (up to 6 VLANs / 8 ports per VLAN,
  4 SVIs, 8 static routes, 5 OSPF network statements).
- **No new CLI constructs:** the two new `interface_mgmt` structures
  (routed-port + SSH + console, loopback + SSH + console) combine
  blocks individually probe-passed in batch 5.
- **Generation gate re-verified:** the intent-match self-check gate was
  mutation-tested 16/16; dedupe rejected 18 candidates on
  shape-signature collisions.
- **Intent-match audit triaged:** full-corpus checker run passes
  3,154/3,280; all 126 flagged rows independently re-verified
  (1,062 VLAN port pairs + 78 trunks) — zero real mismatches, checker
  unchanged (see Validation).
- No data fixes: v0.3 rows are carried over unchanged.

### v0.3 (2026-11-23)

- **+900 examples** (batch 5): 150 per family; corpus grows
  1,180 → 2,080. Wider variety: branch / campus / DC-leaf / small-office
  roles and hostname styles, terse and verbose phrasings, larger
  multi-part requests (up to 5 VLANs, 6 statics, 4 SVIs, 3 BGP peers).
- **Six constructs newly admitted** after a clean Batfish probe pass:
  OSPF `ip ospf network point-to-point`, `banner motd`, SSH VTY
  `access-class` ACLs, 3-peer BGP with combined local-preference +
  maximum-prefix + default-originate policies, static default routes via
  exit interface, and /32 host routes.
- **Generation gate hardened:** batch 5's generator renders each trunk
  allowed-VLAN expression once and reuses it verbatim in intent and
  config (with an explicit drift check); the intent-match self-check
  gate was mutation-tested 15/15.
- No data fixes: v0.2 rows are carried over unchanged.

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
configuration generation (the project fine-tuned Qwen3-4B on v1.0 —
full-parameter fine-tune after QLoRA stopping failures were diagnosed —
and evaluates step-down sizes), and evaluating config-generation systems
on parse and semantic-validity rates per family. Training converts each
row to chat format with `intent_nl` as the user turn and `config` as the
assistant turn.

## Limitations

- **Synthetic, template-generated data.** The synthetic majority varies
  parameters over a limited set of patterns; phrasing and structural
  diversity are narrower than real-world requests. Expect a model
  trained only on this dataset to be brittle outside these patterns.
- **Cisco IOS XE only.** No other vendors or OSes yet; the OpenConfig
  intent field is the intended bridge, not a tested one.
- **Growing scope.** v1.0 covered six families; Phase 1 (from v1.1)
  expands family-by-family toward complete IOS XE device configuration,
  then full-device composition (one intent → one coherent multi-section
  config). Until a family lands here, it is not covered at all.
- **Validation is static.** Batfish checks snapshot-level semantics; it
  does not prove a config behaves correctly on a live device, and no
  example has been boot-tested in an emulator.
