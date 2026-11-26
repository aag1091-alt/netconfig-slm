# Validation Round 1 — Intent-Match Audit of Dataset v0.1

**Date:** 2026-11-23
**Dataset:** `data/v0.1/dataset.jsonl` (580 rows, as published to Hugging Face)
**Checker:** `scripts/intent_match.py` (stdlib only)
**Machine-readable results:** `data/v0.1/intent_match_results.jsonl`
**Scope:** measure and report only — no dataset file was modified.

## Why this round exists

The existing validation ladder (ciscoconfparse2 parse → Batfish semantic
checks) proves every config is valid IOS XE. It never checks that a config
actually does what its intent asked. This round closes that gap: for every
row, the checker extracts the load-bearing entities from `intent_nl`
(cross-checked against `intent_oc` where present) and asserts each one
appears in the config **in the right role** — a VLAN name inside its own
`vlan <id>` block, a static route's destination and next-hop on the same
`ip route` line, a BGP neighbor IP paired with the stated remote-as, an
OSPF prefix with the correct wildcard mask and area, and so on.

## Checker results

**580 / 580 rows pass (100%), across 3,686 assertions.**

| Family | Rows pass | Assertions |
|---|---:|---:|
| vlan_l2 | 97 / 97 | 887 |
| trunk_svi | 97 / 97 | 661 |
| bgp | 97 / 97 | 604 |
| ospf | 97 / 97 | 561 |
| interface_mgmt | 95 / 95 | 552 |
| static_route | 97 / 97 | 421 |
| **Total** | **580 / 580** | **3,686** |

### Failed rows by failure type

**None.** There were zero intent→config mismatches in the final run: no
wrong VLAN name, no wrong next-hop, no wrong remote-as, no wrong wildcard,
no missing SVI, no missing passive interface — nothing in any category.

### Why the 100% is credible (not a vacuous checker)

- **Negative controls:** 14 hand-mutated configs (wrong VLAN name, wrong
  next-hop, wrong remote-as, wrong OSPF wildcard, wrong subnet mask,
  dropped trunk VLAN, wrong advertised network, wrong SVI IP, flipped
  shutdown, wrong aggregate, deleted passive-interface, telnet instead of
  SSH, deleted passive-default…) were fed to the checker. **14 / 14 were
  caught**, each under the correct failure type.
- **Coverage guards:** the checker fails a row loudly
  (`*_coverage`) when an intent's core entities cannot be extracted at
  all, so an unrecognised phrasing cannot silently pass. Building those
  guards surfaced — and fixed — real extraction gaps during this round
  (split VTY blocks, "SVIs for all three VLANs" ordered pairing, shared-AS
  BGP peers, 19 OSPF rows whose passive-interface phrasings were initially
  unextracted). After the fixes, an audit confirms every OSPF row that
  mentions "passive" runs a passive check, and every family's core
  entities are asserted on every row.
- **Hostname:** every config's `hostname` line matches the expansion of
  `device.hostname` (r1→router1, sw1→switch1, core1→core1). 523 rows
  differ as literal strings but the mapping is 100% consistent — a
  deliberate convention, not an error.

## Sampled read-through (human judgement)

Deterministic sample: `random.Random(7)`, 10 rows per family, 60 rows.
Each intent + config read in full and judged:

| Family | Full match | Partial | Mismatch |
|---|---:|---:|---:|
| vlan_l2 | 10 | 0 | 0 |
| trunk_svi | 8 | 2 | 0 |
| static_route | 10 | 0 | 0 |
| ospf | 10 | 0 | 0 |
| bgp | 10 | 0 | 0 |
| interface_mgmt | 10 | 0 | 0 |
| **Total** | **58** | **2** | **0** |

### Every non-full-match row

- **trunk-0005** — partial. Everything requested is present and correct
  (trunk Gi…/Te1/1/1, allowed 100/200/300, native 100, SVI 172.16.100.1/24),
  but the config also asserts `vlan 100 / name Servers` — a VLAN name the
  intent never gave. Invented data, not a wrong value.
- **trunk-0028** — partial. Same pattern: config adds
  `vlan 33 / name Zone33`, unrequested; all requested items correct.

Notes on judgement calls: iface-0013's VTY 0–15 split across
`line vty 0 4` + `line vty 5 15` with identical settings was judged a full
match (semantically complete). Conventional boilerplate that invents no
data (`no shutdown` on a configured interface) was judged full.

## Reverse-direction findings (config says more than the intent)

These are not intent-match failures — everything asked for is present —
but they are consistency observations a v0.2 pass should decide on:

1. **Invented VLAN names in trunk_svi: 32 instances.** Configs create
   `vlan <id>` blocks with names (Sales, Engineering, Servers, Zone33…)
   that appear nowhere in the intent. trunk-0002 alone invents two
   (`vlan 10 / name Sales`, `vlan 20 / name Engineering`). A model trained
   on this learns to hallucinate plausible VLAN names.
2. **Unrequested boilerplate, inconsistently applied.**
   `bgp router-id` appears in 95 / 97 BGP configs but is stated in only
   2 intents. Interface descriptions appear in 266 rows whose intents
   never mention a description. `ip ssh version 2` is present in some
   SSH rows whose intent omits the version (iface-0092, iface-0080) and
   absent in others (iface-0008).
3. **Glued `!` separators: 19 rows.** Some configs glue the block
   separator onto the previous line — `ip ssh version 2!`
   (iface-0073…0080), `no shutdown!` (iface-0044),
   `access-list 10 permit 192.168.188.0 0.0.0.255!` (iface-0088).
   Cosmetic, but a visible tokenisation inconsistency.
4. **Non-canonical aggregate prefixes: 2 rows.** bgp-0093 states
   `10.43.0.0/15` and bgp-0094 states `10.51.0.0/15`; intent and config
   agree literally, but neither base is the true /15 network address
   (10.42.0.0, 10.50.0.0) — IOS would normalise on entry.

## Verdict

**v0.1 is clean enough to train on.** Across 3,686 role-aware assertions
there is not one case of a config doing something different from what its
intent asked, and a 60-row human read-through found zero mismatches.
Everything found is in the "config adds extra, unrequested material"
direction — worth fixing for v0.2 because models imitate extras, but
none of it teaches wrong behaviour for a stated requirement.

### Fix list for v0.2 (none blocking)

1. **Stop inventing VLAN names in trunk_svi** (32 rows): add the name to
   the intent, or drop the `vlan <id>` / `name` block from the config.
   The only substantive item.
2. **Canonicalise the two aggregate prefixes** (bgp-0093, bgp-0094) in
   intent and config, or consciously keep the literal form.
3. **Normalise glued-`!` lines** (19 rows) so every separator sits on
   its own line.
4. **Decide the boilerplate policy once** — `bgp router-id`, interface
   descriptions, `ip ssh version 2`: either state them in intents or
   document them as fixed config-side conventions in the dataset card.
   No data change required if documented.
5. **Keep `scripts/intent_match.py` in the loop:** re-run it on every
   future batch before publish; its coverage guards turn new intent
   phrasings into loud failures instead of silent passes.
