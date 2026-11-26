# Phase 1 — Expansion Plan (Batch 8 onward)

Date: 2026-11-25 · Status: PLAN — not yet started
Author: Muse (research prep for the small-model side chat)

## Why now

- v0 shipped: dataset v1.0 (exactly 5,000 pairs) and model
  `aag1091/netconfig-slm-qwen3-4b` published 2026-11-25 ~04:21 PT
  (Batfish 98.8%, intent-match 92.5% triaged, parse 100%).
- Avinash asked 2026-11-25 ~06:52 PT: "so what next will you keep on
  expanding we need to be able to configure Iosxe device completely"
  — and Muse committed the same morning to start Phase 1 batches under
  his standing mandate (Nov 23: run the data pipeline non-stop, keep
  growing past the ~5,000-pair Cisco target, publish HF version bumps
  at each milestone; Nov 24 direction: phased family expansion plus
  full-device composition).
- Gap as of 2026-11-25 15:28 PT: nothing Phase 1 has started. Last
  generated batches are b7 (2026-11-23 22:04). No `gen_batch8.py`,
  no Phase 1 doc, no new batch logs exist yet. This plan is the
  kickoff spec so Batch 8 can start without another round of scoping.

## Batch 8 (Batch 1 of Phase 1) — L2 cluster

First families, in the order Avinash listed them Nov 24
("L2 security/STP/EtherChannel" first):

| # | Family | Prefix | Target rows | New CLI surface |
|---|--------|--------|-------------|-----------------|
| 1 | etherchannel | `ether` | ~287 | `channel-group <n> mode active/passive/on`, `interface Port-channel<n>`, LACP, `port-channel load-balance`, `port-channel min-links`, trunk allowed-VLANs on Po |
| 2 | stp | `stp` | ~287 | `spanning-tree mode rapid-pvst/pvst/mst`, `spanning-tree portfast`, `bpduguard`, `rootguard`, `loopguard`, `spanning-tree vlan <id> priority`, edge-port config |
| 3 | port_security | `psec` | ~287 | `switchport port-security`, `maximum`, `violation shutdown/restrict/protect`, `mac-address sticky`, `aging time/type` |

Total: ~861 new pairs → corpus 5,000 → ~5,861 (dataset v1.1 on
publish). Sizing follows batch 7 (287/family, 285 interface_mgmt).

Why these three first:
- Smallest new CLI surface; all interface-level, no new routing
  protocol — Batfish validation stays straightforward.
- They compose directly with the existing `vlan_l2` / `trunk_svi`
  families (same ports/VLANs), which is the first step toward
  Phase 2 full-device composition.
- L2 security is the top of Avinash's own expansion list.

## Recipe (same as batch 7 — do not reinvent)

`scripts/gen_batch7.py` (1,510 lines) is the template. `gen_batch8.py`
must keep:
- Dedupe vs full corpus + within batch: exact pair + shape signature
  (intent+config with numbers/names/descriptions normalized).
- Intent-match self-check gate (`scripts/intent_match.py` `check_row`)
  BEFORE any Batfish validation: every load-bearing entity in the
  intent must appear in the config in the right role. New per-family
  `check_row` rules are required for: channel-group / Port-channel
  numbers, STP mode + guard keywords, violation mode + maximum count.
  Flagged candidates are dropped and regenerated, never patched.
- IPs valid and inside RFC1918 / documentation ranges.
- `hostname` first line on every generated config (Batfish rule).
- Write `data/generated/{family}_b8.jsonl` with validation flags null;
  fill from the validator run afterwards (`scripts/fill_b8_flags.py`,
  modeled on `fill_b7_flags.py`).
- Validation ladder unchanged: ciscoconfparse2 parse →
  Batfish (host `macbooks-macbook-pro` via the Tailscale tunnel proxy;
  see `scripts/run_b7_validation.py`) → per-family Batfish questions
  in `src/validate/validate.py` (new questions needed for the three
  new families — EtherChannel bundle state, STP port roles/guards,
  port-security violations).
- Batch-4 lesson, still binding: any expression reused across a config
  (e.g. allowed-VLAN list on a Port-channel and its member ports) is
  rendered ONCE and reused verbatim.
- Run-3 lesson, still binding: keep configs short — the one real
  defect class in the published model is truncation on long port
  lists. Bound port/channel lists per row.

## Mac / Batfish dependency (the honest constraint)

- Batfish gates every batch and lives in Docker on Avinash's Mac.
  The Mac was awake and Batfish answered this morning
  (2026-11-25 ~06:05 PT, after being unreachable ~04:05–22:05 PT the
  day before). Verify the Mac is awake BEFORE starting the Batfish
  rung; if it is asleep, generation can proceed but validation waits.
- The 30-minute batfish-rung-watch cron was removed after the publish;
  re-arm a watch if validation needs to wait on the Mac.

## Publish / versioning

- After Batch 8 passes the ladder: build dataset v1.1, publish the HF
  version bump, add a Data Generation Log entry (entry 21), update
  the public log at slm.aag1091.com.
- Do NOT retrain per batch. Retrain at milestones only (e.g. after
  Batch 9, or at ~7,500 pairs) — full fine-tune run 3 cost $3.19, and
  the next retrain should bundle several new families so the spend
  buys a real capability jump.

## Sequence after Batch 8

- Batch 9: ACLs, NAT/PAT, HSRP.
- Batch 10: OSPF multi-area, BGP route-maps / prefix-lists /
  communities, AAA + SNMP + syslog/NTP hardening.
- Phase 2 (after the Batch 8–10 families land): full-device
  composition — one intent → one coherent multi-section config
  (VLANs matching SVIs, interface IPs matching routing, ACLs
  referenced where applied). Output length is the known constraint
  (see run-3 truncation note above); composition training needs
  longer sequences and cross-section consistency checks.
- Phase 3: QoS, IPv6 dual-stack, site-to-site IPsec, multicast,
  NetFlow, plus modification tasks ("change this existing config").
- v1 gate (unchanged, per locked scope): full emulation needs
  Avinash's CML license — Cisco images, not yet available.

## First execution step

In the small-model side chat: write `scripts/gen_batch8.py` modeled
on `gen_batch7.py` for the three families above, run generation, then
intent-match gate, then parse + Batfish validation (Mac awake check
first). No new approval is needed — standing mandate + his explicit
"keep on expanding" this morning already cover it.
