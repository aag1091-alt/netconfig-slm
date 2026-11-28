# yang-learn corpus v3 — build log (tranche 1)

Plan: `yang-learn/CORPUS_V3_PLAN.md` (commit `fa85fbc`).
Tranche framing: board #117 — combined train target 4,000–6,000
is tranche 1 toward 50% direct-evidence coverage of the pinned
IOS XE 26.2.1 /native inventory (77,026 of 154,052 eligible
leaves, per the published coverage snapshot). Accounting rules:
plan §0 — direct evidence only (a leaf counts iff a TRAIN
record's designated subject path IS that leaf; bystanders and
preserved values never count; held-out strictly excluded from
the numerator); common-task and interface-normalized views
reported separately. Accounting tool: `yang-learn/v3_coverage.py`
(calibrated against the published snapshot: it reproduces
154,052 / 106 / 2,134-leaf / 889-construct / 0-unresolved exactly
on corpus v1 train).

## Batch 0 — baseline (v1+v2 train, before any v3 record)

- Records: 1,418 (v1 train 1,047 + v2 train additions 371).
- Direct-evidence covered leaves: **216** raw view
  (216 / 154,052 = 0.14% of the full inventory; 216 / 77,026 =
  0.28% of the tranche-program goal). Dashboard evidence rule on
  the same files also yields 216 (the looser rule adds nothing on
  v1+v2: SR/TE bystander leaves coincide with leaves other
  records already exercise directly).
- Interface-normalized view: **129 / 68,248**.
- Priority sections (covered / total): interface-admin-description
  0/108; access-voice-vlan 6/72; trunk-vlans 29/252;
  port-channels 50/5,383; port-protections 68/21,934;
  vlan-definitions 22/117.
- In-scope subtree ceiling reminder (plan §0): the VLAN +
  interface-switchport subtree holds 2,952 leaves; tranche-1
  direct-evidence ceiling ≈ 600–900 leaves. Everything outside
  the subtree is out of scope for this tranche by design (gap
  reason: not yet scoped — later tranches).

## Pre-build probes (plan §3 gate + §2/§5 assumptions)

All run against the scoped yangson library as-is
(`corpus/lib_v1.json`, bundle cat9k @ YangModels/yang 9c23db9):

- **maximum-addresses gate RESOLVED — no library change needed.**
  All three maximum leaves validate and reject correctly today:
  `…/maximum-address-config/maximum/maximum` (1..4096),
  `…/maximum-vlan-access/maximum` (1..4096), and the deprecated
  `…/maxcount/max-addresses` (**1..4097**), each rejecting an
  out-of-range value with YangTypeError and the expected-node
  fragment naming the leaf. V2's RawMemberError must have come
  from a different instance shape than the plan assumed. The IV
  maximum classes (range_maximum / range_maxva / range_maxcount,
  ≈150 records planned) proceed with NO scoped-library edit, so
  no re-validation of v1/v2 verdicts is triggered.
- Other interface lists validate for IV/SR use: TenGigabitEthernet,
  TwentyFiveGigE, HundredGigE, FortyGigabitEthernet, FastEthernet,
  TwoGigabitEthernet, FiveGigabitEthernet, Wlan-GigabitEthernet,
  AppGigabitEthernet. Port-channel's key is uint32, so interface
  name "1/0/1" fails there (RawTypeError) — Port-channel IV/SR
  scenarios are a different shape and are skipped this tranche
  (gap: `GROUPING_NOT_INSTANTIATED` for name-keyed Port-channel
  scenarios).
- `cisco-mac-address-type` is a string pattern type
  (`[0-9a-fA-F]+\.[0-9a-fA-F]+\.[0-9a-fA-F]+`, dotted hex, exactly
  three groups): the `mac_pattern` IV class works (colon form
  rejected, YangTypeError at `hw-address`).
- `violation_choice` works: two cases present → SchemaError
  (inside validate_all's rejection classes), fragment `violation`.
- Generic `maximum-vlan` when-violation works (SchemaError,
  fragment `maximum-address-config`) — the `when_maxvlan` class
  from the plan is buildable.
- **`must_sibling` yields SemanticError**, which is NOT in
  gen_corpus.validate_all's REJECTION_CLASSES
  ({RawTypeError, YangTypeError, SchemaError}). Handling
  (documented, class-scoped): gen_corpus_v3.py validates the
  must_sibling class with a dedicated pass that accepts
  SemanticError as the rejection signal, still requiring the
  expected-node fragment check. Verdicts remain yangson-only;
  gen_corpus.py is not edited.
- **`1/0/1.` actually MATCHES the interface-name pattern** (the
  pattern allows a trailing dot followed by zero digits), so it
  cannot serve as a TE invalid_key shape; the mechanical pattern
  assertion overrides the plan's example list. TE negative shapes
  used instead: `01/0/1`, `1-0-1`, `GigabitEthernet1/0/1`,
  `1/0/1a`.
- PM pool measured (pyang inventory, non-reserved constrained
  facts): 1,043 concrete instances remain across 81 facts (range
  322, union 253, when 240, enum 120, pattern 78, other 30);
  plain-type facts remaining: 131 (empty 109, string 22). Under
  the plan's cumulative caps (≤3 per fact across v2+v3 for the
  failure classes, ≤2 otherwise; v2 already used 230 PM type
  records over 125 facts), measured headroom is 369 records
  before path exhaustion — PM will land below its 700 target;
  gap reason per fact class will be `CAP_REACHED` /
  `POOL_EXHAUSTED`, reported in the PM batch entry. Per plan §7
  the shortfall is NOT padded.

<!-- per-batch entries are appended below as batches land -->
