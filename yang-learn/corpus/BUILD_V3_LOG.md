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

## Batch 1 — path_meaning (part: corpus/v3_parts/pm.jsonl)

- Generated 200, deduped 0, validation n/a (pyang facts), final
  200: **train 160 / dev 40** (dev quota met exactly).
  Composition: type 45, when-form 8, must-form 16 (NEW form —
  no v1/v2 record explains a must condition), plain-type 131
  (the full remaining deduped plain pool: pool exhausted).
- Target vs achieved: plan +700 train; achieved +160 train.
  The shortfall is structural, measured per key over the 192
  non-reserved constrained keys: 111 keys are POOL_EXHAUSTED
  (every concrete path already used by a v1 split — most are
  single-path facts); 36 keys are CAP_REACHED with paths still
  remaining (24 failure-class keys at the cumulative cap 3,
  12 other keys at cap 2 — the plan's caps are cumulative
  across v2+v3 and v2 already spent them); only 45 keys had
  cap headroom (each topped up by exactly 1). Per plan §7 the
  gap is reported, not padded: no 4th concrete path per fact.
- Direct-evidence coverage: **+84 leaves** (216 → 300 raw;
  normalized 129 → 183). Running total vs the 77,026 goal:
  300 (0.39%). Priority deltas: vlan-definitions 22→46,
  port-protections 68→93, port-channels 50→61, trunk-vlans
  29→33, access-voice-vlan 6→7.
- Rebalance note: the PM shortfall (~540 train records) is
  offset later in the build by letting SR/PV run to their full
  honest tuple supply (see their batch entries); IV and RS
  floors are untouched, per plan §7's protection order.

## Batch 2 — instance_validity (part: corpus/v3_parts/iv.jsonl)

Reconciliation first: the part file found on disk (463 records)
was STALE — generated before generator fixes — and failed the
plan's per-class balance audit (choice_mode 0V/14I, enum_aging
0V/12I, mac_pattern 0V/14I, when_maxvlan 0V/3I, enum_encap
2V/20I). Its verdicts were all yangson-consistent; the
imbalance came from three instance-shape bugs, found by direct
yangson probes and fixed in gen_corpus_v3.py (gen_corpus.py
untouched):

- violation_choice: protect/restrict/report are `empty` leaves
  directly under `violation` (JSON `[null]`); shutdown is a
  case container `{vlan: [null]}`. The old `{case: {}}` shape
  made valids unvalidatable and invalids reject at the wrong
  node. New `_vio_inner()` helper, also used by holdout.
- mac_pattern: `hw-address-access` is a LIST — instances are
  `[{hw-address: v}]`, not a bare container.
- enum literals: maximum-vlan/vlan is the enumeration string
  `"vlan"` (not `[10]`); maximum-vlan-voice/vlan is `"voice"`
  (generator had `"access"`).

Two further build bugs found and fixed during reconciliation:

- EXTRA_FILES pointed held-out dedupe at a nonexistent path
  (`corpus/held_out.jsonl`); the real file is
  `yang-learn/held_out.jsonl`. The guard had been silently
  skipping held-out dedupe (verified zero actual overlap for
  the PM/IV parts after the fix).
- `--force` regeneration self-deduped: `all_prompts()` includes
  the batch's own part file, so a forced re-run dropped most
  of its own output (489 of 565 in the first attempt). All
  gen_* dedupe reads now exclude their own part file.
- Collision-driven variant shifting: variantize's scenario
  space is (interface × bystander pattern) = 12 combos; v1
  used v=0..5 and v2 v=0..2, so v3 valids for classes whose
  value space v1 had fully enumerated (choice_mode,
  enum_aging, enum_encap, union_access, union_voice) collided
  on every v in 0..5 and were deduped wholesale (60 records).
  gen_iv now rebuilds a colliding record at v+6/+12/+18 —
  combos no earlier corpus used — making the scenario
  genuinely new instead of dropping it (66 records shifted in
  the final run, 0 deduped).

Two schema findings from yangson arbitration (intended labels
dropped, never relabeled):

- maximum-vlan-voice/maximum WITHOUT its vlan sibling is
  ACCEPTED — the voice pair carries no must. The case was
  removed from must_cases (the two maximum-vlan-access /
  maximum-vlan pairs do carry the sibling musts, verified).
- maximum-vlan under ACCESS mode (intended invalid, when
  requires trunk) is ACCEPTED: 3 records dropped
  (V3-IV-0889..0891). The dynamic-mode sibling case does
  reject, and stays.

- Final run: generated 1,026 (incl. the expansion below),
  balance-trimmed 58 + 4 post-validation, deduped 0,
  validation-dropped 3, **final 961: train 919 / dev 42**
  (dev quota 40, +2 group-granularity overshoot of the
  v1/v2 group-atomic split method). Per-class balance: all
  21 classes inside 45:55–55:45 (worst must_sibling 29V/24I,
  pattern_ifname 24V/20I). Part invalid share 49.6%;
  combined IV train invalid share 48.6% (cap 55%).
  Independently re-verified end-to-end: every verdict ==
  yangson verdict, every instance file byte-matches its
  prompt, zero prompt overlap vs all v1+v2 splits + held_out
  + pm part, split assignment reproduces exactly.
- Target vs achieved: plan +1,100 train (floor ≥900);
  achieved **+919 train — floor met**. The pre-expansion
  generator yielded only 510 train: the authored case lists
  were smaller than the plan's per-class estimates. The gap
  was closed honestly with batch 2b (in this same part):
  audited fresh values completing the plan's rings plus
  interior values for the range/union classes, fresh enum
  near-miss spellings, remaining violation/choice combos,
  fresh must maximum values, an extended TenGigabitEthernet
  subset, and new FortyGigabitEthernet / FiveGigabitEthernet
  subsets (both lists probed to validate). No padding: every
  added value is disjoint from the v1/v2/v3 case lists.
- Direct-evidence coverage: **+5 leaves** (300 → 305 raw;
  normalized unchanged at 183). IV's designated subjects are
  mostly leaves the PM batch already evidenced (the same
  port-security leaves under yangson arbitration); 203 IV
  train records carry construct-level subjects (choices /
  containers) that the strict rule does not count. Running
  total vs the 77,026 goal: 305 (0.40%). Priority delta:
  port-protections 93→97.

## Batch 3 — revised_schema (part: corpus/v3_parts/rs.jsonl)

- Build fix first: the generator's `main()` dispatch sat
  mid-file, before the gen_rs/gen_srte/gen_pv/gen_holdout/
  gen_finalize definitions — every subcommand past `iv` would
  have NameError'd (pm/iv were defined above it, which is why
  batches 1–2 ran). The `__main__` block now sits at the end
  of the file; no generator logic changed.
- Generated 375, deduped 0, **final 375: train 360 / dev 15**
  (dev quota 12, +3 group-granularity overshoot). New
  profiles P7–P12: 318 records (P7 48, P8 68, P9 48, P10 46,
  P11 52, P12 56); maintenance on P1/P3/P4/P5: 57. Verdicts
  183 allowed / 192 not-allowed. Verdicts are computed from
  the profile predicate specs embedded in each prompt (the
  v2 discipline); P13/P14 appear nowhere (holdout-reserved).
- Target vs achieved: plan +450 train (floor ≥350);
  achieved **+360 train — floor met**, 90 under target: the
  authored predicate grids for P7–P12 plus maintenance
  exhaust at 375 unique (profile × rule × value) prompts.
  Per plan §7 the gap is reported, not padded.
- Independently verified: zero prompt overlap vs all prior
  splits + held_out + pm/iv parts, completion verdict ==
  fact verdict on all 375, prompt embeds the named profile,
  no scenario group spans splits, split assignment
  reproduces exactly.
- Direct-evidence coverage: **+0 leaves** (305 raw / 183
  normalized). RS subjects are the same switchport leaves
  already evidenced by PM/IV — this family teaches policy
  deltas over known paths, not new paths. Running total vs
  the 77,026 goal: 305 (0.40%).

## Batch 4 — state_reconstruction + typed_edit (part: corpus/v3_parts/srte.jsonl)

- Generated 940, deduped 0, validation-dropped 0, **final
  940: SR 528 (train 512 / dev 16), TE 412 (train 398 /
  dev 14)**. SR: 8 edit kinds × 66 scenarios (set_access_vlan,
  swap_mode, set_native_vlan, add_entry, set_voice_vlan,
  remove_entry, set_allowed, set_encap). TE positives 352
  share SR scenario groups; TE negatives 60 (14.6% of TE —
  the plan's ~15%): 20 invalid_key (malformed interface
  names incl. the new `01/0/1`, `1-0-1`, `GigabitEthernet1/0/1`,
  `1/0/1a` shapes) + 40 target_not_found (well-formed keys
  absent from the state). SR dev quota 16 met exactly;
  TE dev 14 vs quota 12 (+2 group overshoot).
- Targets vs achieved: SR plan +450 → **+512 train**;
  TE plan +350 → **+398 train**. Both exceed target (the
  rebalance headroom from batch 1, taken as full honest
  tuple supply per plan §7).
- Independently verified end-to-end (fresh code, not the
  generator's): zero prompt overlap vs all prior splits +
  held_out + pm/iv/rs parts; no scenario group spans splits;
  split assignment reproduces exactly; for every SR record
  the prompt state == before_file, the completion ==
  after_file, and an independent leaf diff of before→after
  is prefix-consistent with fact.changed_paths (the fact
  lists added/removed subtree roots alongside their leaves);
  for every TE positive, an independent edit applier
  applying fact.edits to the before state reproduces the SR
  twin's after state exactly; for every TE negative, the
  error kind checks out against the schema's name pattern /
  state membership; a 40-record sample of SR after-states
  re-validates under yangson. One convention confirmed
  rather than "fixed": TE edit paths are SCHEMA paths
  (P_ALLOWED has included the `vlan-choice` segment since
  v1 — choice/case nodes are transparent in instance JSON,
  and the applier resolves them as such); SR changed_paths
  are data-tree paths. The two forms are consistent within
  each family across v1+v2+v3.
- Direct-evidence coverage: **+0 leaves** (305 raw / 183
  normalized). SR/TE edited leaves (access/native/allowed/
  voice/encap/mode/entries) were already designated subjects
  of PM/IV records; this batch deepens those leaves with
  state-transition evidence rather than evidencing new
  ones. Running total vs the 77,026 goal: 305 (0.40%).

## Batch 5 — paired_view (part: corpus/v3_parts/pv.jsonl)

- 302 unique states survived the gates (yangson validity +
  100% CLI round-trip via ciscoconfparse2, parse(render(
  state)) == state), generating 453 records: direction A
  (CLI→JSON) 302, direction B (JSON→CLI) 151 (= A // 2 per
  the plan's ratio), dropped_roundtrip 0, dropped_yangson 0,
  deduped 0, **final 453: train 433 / dev 20** (dev quota
  met exactly).
- Target vs achieved: plan +600 train at full supply
  (400 states); achieved **+433 train**. The state supply
  exhausted at 302: PV states are drawn from the SRTE
  scenario-state pool, and only 302 distinct states are both
  yangson-valid and CLI-renderable (the renderer covers the
  access/trunk/port-security subset; states carrying
  violation/mac-address/dynamic constructs are not
  CLI-renderable in this slice and never enter the pool).
  Reported, not padded, per plan §7.
- Independently verified: an external IOS XE CLI parser
  (written from the CLI text and the pinned schema shapes,
  not the generator's renderer) reproduces the JSON state
  exactly for all 453 records in both directions; instance
  files match the states; zero prompt overlap vs all prior
  splits + held_out + pm/iv/rs/srte parts; no scenario group
  spans splits; split assignment reproduces exactly.
- Direct-evidence coverage: **+0 leaves** (305 raw / 183
  normalized). PV subject paths are the same switchport
  leaves already evidenced. Running total vs the 77,026
  goal: 305 (0.40%). Tranche note: the strict designated-
  subject accounting saturates the switchport/vlan subtree
  early — PM+IV evidenced essentially every leaf the later
  families designate, so batches 3–5 deepen already-counted
  leaves (transitions, policy deltas, CLI views) instead of
  adding new ones. The 77,026 goal is a 50%-of-/native
  program goal spanning future tranches (router/ip/acl
  subtrees are untouched by tranche 1's families).
