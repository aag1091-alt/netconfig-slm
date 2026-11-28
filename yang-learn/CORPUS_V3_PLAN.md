# yang-learn corpus v3 — consolidated build plan (2026-11-27)

Status: **execution design, approved for build** (Avinash, board
#113 — plan-approval gate waived; Codex #112/#117 set the shape).
This document is the builder's contract: allocations, per-family
recipes, contamination guards, eval design, and measured cost
basis. GPU training stays unlaunched until corpus + eval design
are reviewed (Codex #112). All numbers below are measured,
derived from a measured number (derivation shown), or labeled
ESTIMATE with its basis.

## 0. Tranche framing (Codex #117)

The program goal is **50% training-data coverage of the pinned
/native inventory: 77,026 of 154,052 eligible paths** (the
coverage dashboard's denominator: config-true, non-obsolete
leaves under /native, IOS XE 26.2.1 bundle). This 4,000–6,000
record build is **tranche 1** of that program, not the program.

Coverage accounting rules (binding on every batch report):

- **Direct evidence only.** A leaf counts as covered by training
  iff a TRAIN record's *fact path* (PM), *expected-node path*
  (IV), *edited path* (SR/TE), or *rule target path* (RS) **is**
  that leaf. A leaf that merely appears inside an instance state
  (bystander interface, preserved value, prompt mention) does
  **not** count.
- **Held-out never counts.** Bench v1, test, and holdout-v3
  leaves are excluded from the numerator even when they coincide
  with train leaves by path (they are reported separately).
- **Two views, reported separately:** (a) raw view — covered /
  154,052; (b) interface-normalized view — covered-normalized /
  68,248 (dashboard denominator). A third, common-task view
  (the `coverage/priorities.yaml` sections: interface, vlan,
  trunk, port-security, …) is reported per priority section.
- **Batch order follows priority:** switchport/vlan surface
  first (this tranche), because it is the highest-frequency
  configuration surface in the priorities file and the only
  subtree the current tooling can validate end-to-end.
- **Per-batch report (mandatory fields):** records
  built/kept/dropped with reasons; direct-evidence leaves newly
  covered (count + list); leaves attempted but not covered, each
  with a reason code — `POOL_EXHAUSTED`, `RESERVED_BENCH`,
  `RESERVED_HOLDOUT`, `YANGSON_UNVALIDATABLE`,
  `GROUPING_NOT_INSTANTIATED`, `CAP_REACHED`; constraints hit
  (per-fact caps, balance bands, dedup drops).

Honest tranche-1 ceiling: tranche 1 stays inside the current
scope subtree (/native/vlan + any switchport path = **2,952
leaves**, measured by `schema_tools.py inventory`). Even perfect
coverage of every PM-factable / IV-testable / editable leaf in
that subtree yields on the order of **600–900 direct-evidence
leaves ≈ 0.4–0.6% of the 154,052 goal**. Tranche 1 buys
*capability depth in the highest-priority subtree*, not global
percentage. Reaching 77,026 requires later tranches that expand
scope subtree by subtree (interface non-switchport, then `ip`
6,103 leaves, `router` 24,293, … per the dashboard) — out of
scope here, named so nobody mistakes tranche 1 for the road to
50% on its own. Current direct-evidence train coverage is **106
leaves** (dashboard, corpus v1 train); the builder recomputes
the exact v1+v2 figure under the rules above as batch 0.

## 1. Corpus v3 shape

Corpus v3 = corpus v1 (frozen splits untouched) + v2 additions
(kept) + **v3 additions**, all under the existing split
discipline: splits by scenario group, benchmark v1 files never
modified, prompt-hash dedup of every addition against **all**
prior splits + held_out + within-v3 (v2's guard, 0 overlaps
survive). Interpretation of the target: the 4,000–6,000 figure
is the **combined v3 train size**; v1+v2 train is 1,418, so v3
additions target ≈ 3,650 train records → **combined ≈ 5,068**.

### Train allocation (additions → combined)

| family | v1+v2 train | v3 add | combined | share of add |
|---|---|---|---|---|
| instance_validity | 431 | **+1,100** | 1,531 | 30% |
| path_meaning | 381 | **+700** | 1,081 | 19% |
| paired_view (NEW) | 0 | **+600** | 600 | 16% |
| state_reconstruction | 260 | **+450** | 710 | 12% |
| revised_schema | 87 | **+450** | 537 | 12% |
| typed_edit | 259 | **+350** | 609 | 10% |
| **total** | **1,418** | **+3,650** | **5,068** | |

Dev additions (§5): +140 → dev 281. Holdout-v3 (§5): ~260,
eval-only. Floors if supply falls short (§7): IV additions
≥ 900, RS additions ≥ 350 are protected; cuts land on SR/TE
maintenance first, then paired_view down to 400.

## 2. Per-family recipes

### 2.1 path_meaning (+700) — exactness + remaining concrete paths

Uniqueness unit = the **fact** (interface-normalized path,
type). Records per fact are concrete-path instances of it;
renaming the interface does not create a new fact, it creates
another *instance* of one — justified only as exactness training
(the run-1/2 failure mode is verbatim recall at an unseen
concrete path).

Measured supply (RUN2_PLAN §2, pyang inventory of the 2,952
in-scope leaves): deduped constrained pool **224 facts** (v1's
A4 figure 235 includes sample-fold effects), of which the
benchmark's reserved every-7th slice removes ~32–34 facts **at
every concrete path** (guard unchanged from v2 — a reserved
fact never enters train/dev in any clothes). Non-reserved facts
have **1,229 concrete instances** beyond the v1 representatives;
v2 consumed 210, leaving **≈ 1,019** (builder re-derives the
exact figure and also excludes concrete paths already used by
v1 dev/test records).

Recipe:
- Mine remaining concrete paths of non-reserved facts, caps:
  ≤ 3 per fact cumulative across v2+v3 for the run-1 failure
  classes (range, typedef chains containing `->`, union, when,
  pattern); ≤ 2 otherwise. Prefer interface families unused for
  that fact so far. **Exclude TwentyFiveGigE and HundredGigE
  concrete paths entirely** — those families are the holdout-v3
  PM probe (§5).
- When/must forms: every non-reserved when-bearing fact gets a
  when-record if it lacks one (v2 capped at 24; the pool has 19
  distinct when-bearing facts after dedupe per SCALE_DESIGN A4 —
  so this is small) and add the new **must-record form** for
  facts carrying `must` (same completion template shape as the
  when form). ESTIMATE ≤ 40 records; builder reports actuals.
- Plain-type remainder: 175 deduped unconstrained facts existed;
  v2 used ~60 under quotas (boolean 25 / string 25 / empty 10).
  Raise quotas to exhaust the pool: ≈ **+115**.
- Range-exactness ring: for every non-reserved range fact, at
  least one record whose completion states the range with both
  bounds verbatim — already the PM form; the lever is coverage
  of *all* range facts at ≥ 2 concrete paths before any fact
  gets a third.
- **1..4097 special case:** the bench PM failures sit on
  bench-reserved facts and can never be trained directly. The
  range `1..4097` occurs at ≥ 5 sites in Cisco-IOS-XE-switch.yang
  (deprecated `maxcount/max-addresses`, obsolete
  `maximum/max-addresses` in two grouping variants, others).
  Builder determines which of those facts are non-reserved
  under the normalized-path key and mines them first — teaching
  "4097 exists" via every legal neighbor fact. If all are
  reserved, report `RESERVED_BENCH` and move on; do not engineer
  around the reservation.

Expected yield: 550–750. If the pool yields < 700, take what
exists, report the gap as `POOL_EXHAUSTED` per fact class, and
apply §7 — do not pad with a 4th concrete path per fact.

### 2.2 instance_validity (+1,100) — plausible invalids, balanced

Run-2 evidence: false-valid bias persists (13/33 bench invalids
called Valid) despite v2's 72/48 invalid tilt. The v3 lever is
**plausibility + balance**, not tilt: invalids that differ from
a valid instance by the smallest possible step, and per-class
balance so "Invalid" is never the cheap guess.

Existing classes (12): range_access, union_access, enum_encap,
range_native, range_aging, enum_aging, pattern_entry,
pattern_ifname, choice_mode, when_maxacc, union_allowed,
union_voice. v1 ran 70 case-values × 6 variants; v2 added 41
cases × 3. 

Recipe:
- **Value rings per class.** For range classes, generate from
  the ring {lo−2, lo−1, lo, lo+1, mid, hi−1, hi, hi+1, hi+2,
  2·hi, 0, −1} minus every value used in v1/v2/bench for that
  class; boundary values (lo, hi) are the valid members,
  everything outside is invalid. For enum classes: case flips
  (`Absolute`, `DOT1Q`), near-miss spellings (`in-activity`,
  `8021q`), whitespace-padded, empty string. For pattern
  classes: leading-zero, trailing-space, separator swaps
  (`1.0.1`, `1-0-1`), off-by-one range ends (`1-4095`).
  For union classes: numeric strings that match the string
  branch (valid traps) paired with non-string out-of-range
  values (invalid), in both directions — v1's two false-invalid
  fails were exactly this confusion.
- **Variants ≤ 3 per case** (down from v1's 6) — breadth over
  repetition; variant = different interface from TRAIN_IFS +
  different bystander state, same class+value.
- **Balance band:** per class, valid:invalid within 45:55–55:45;
  corpus-wide invalid share ≤ 55%. Every invalid still breaks
  exactly one rule and must pass the expected-node +
  verdict_kind check (SCALE_DESIGN §4 chain, unchanged).
- **New classes** (each probed against yangson before volume is
  committed; a class that fails its probe is reported
  `YANGSON_UNVALIDATABLE` and cut, not forced):
  1. `must_sibling` — maximum-vlan-access / maximum-vlan with
     `maximum` present but `vlan` absent (and vice versa):
     the leaves carry `must '../vlan'` / `must '../maximum'`.
     These containers sit under maximum-address-config, which
     the current instance builder already instantiates
     (when_maxacc uses it), so this class is *expected* to
     validate today. ≈ 120 records.
  2. `violation_choice` — port-security violation choice with
     two cases present (protect+restrict etc.) = invalid;
     single case = valid. ≈ 90 records.
  3. `mac_pattern` — yang:mac-address leaves under
     port-security mac-address-config: malformed MACs (short
     groups, bad hex, colon/dash confusion) invalid; well-formed
     valid. ≈ 90 records. Probe first: confirm the leaf is
     reachable in instances the builder can already emit.
  4. `maximum_addresses` — range class on the 1..4097
     `max-addresses` leaf. **Gated on §3** (scoped-library
     extension). If the gate fails, these ≈ 150 records are
     NOT built and the IV total drops to ≈ 950 (still above
     the 900 floor).
- Per-class targets: 12 existing classes ≈ 55–65 each
  (≈ 720 total) + new classes ≈ 380 → 1,100.

### 2.3 revised_schema (+450) — new profiles, not more drilling

Run 2's clearest signal: citation drilling on P1/P3/P4/P5 did
not transfer to unseen P2/P6 (verdict-only 92.9% → 82.1%).
More volume on seen profiles is explicitly **not** the fix.
v3's growth is new profiles; seen profiles get maintenance only.

**Definitions (binding).** A rule's identity is its
(target path, predicate type) pair. Predicate types:
range-restrict, cap, floor, exact-value, forbidden-value,
parity, set-membership, list-exclusion, list-inclusion,
list-size-cap, conjunction. A profile is **genuinely new** iff
≥ 1 of its rules has an identity appearing in no earlier train
profile. Same identity with a different constant = a *variant
rule* — legal as a case, counts zero toward new-profile supply.

Recipe:
- **Author P7–P12** (6 new train profiles × 3 rules). Each
  profile: ≥ 1 novel-identity rule; ≥ 1 variant rule whose
  constant *contradicts* a bench profile's rule on the same
  path (teaches profile-conditional reading over constant
  memorization); 5–7 cases per rule with ≥ 2 boundary cases
  (at-bound, bound±1); 2 question phrasings rotated as in v2;
  every completion templated to embed the exact citation
  (v2 verified 33/33 verbatim — same check, 100% required).
  Verdicts fixed by in-generator predicates, as in v1/v2.
  Novel predicates to draw from (≥ 1 per profile): list-size-cap
  on P_ALLOWED, list-inclusion on P_ALLOWED, floor on
  P_AGING/time (train-side floor is novel; P6's is bench),
  conjunction (two conditions, both must hold), forbidden-value
  on P_ENCAP, range-restrict on P_VOICE, parity on P_NATIVE.
  Gross ≈ 6 × 3 × 6 × 2 ≈ 648; **cap additions at 360** by
  phrasing rotation ≈ 1.5 average.
- **Maintenance:** +90 fresh-value cases across P1/P3/P4/P5
  rules (≈ 15% growth — sized to prevent forgetting, per the
  run-2 dev evidence that seen-profile RS held at strict 5/6).
- **P13/P14 authored but RESERVED** — holdout-v3 only (§5),
  never in train/dev. This requires amending SCALE_DESIGN A1's
  fixed train-profile set (P1/P3/P4/P5) — flagged as decision
  D1 (§8); the build proceeds under Avinash's #113 direction,
  Codex reviews the amendment with this plan.

### 2.4 state_reconstruction + typed_edit (+450 / +350) — maintenance

Both families are strong (SR 100%, TE 97.6% on bench v1) and
stopping is fixed (95.4%). These allocations exist to prevent
regression while the corpus's center of mass shifts to IV/PM,
and to widen scenario diversity — not to chase gains.

Uniqueness unit = the scenario tuple (kind × grid params ×
interface × entry pool × bystander configuration). The v1
engine's space is large (KIND_GRIDS: 8 kinds; e.g.
set_access_vlan 8 grid points × 10 TRAIN_IFS × 5 ENTRY_POOLS ×
3 bystander patterns ≈ 1,200 tuples), so supply is not the
constraint; duplication is. Guards:
- Every v3 scenario tuple must be new: enforce by **prompt-hash
  dedup against all v1+v2 records** (the executable form of the
  tuple rule, since the prompt embeds kind, params, interface,
  entries, and bystanders) plus within-v3 dedup.
- Per-kind cap: no kind > 25% of the SR addition; bystander
  patterns rotate evenly; 3-interface states ≥ 25% of additions
  (v1 included them; keep the hard end represented).
- TE follows the scenario stride as in v1 (te_stride pattern),
  plus selector negatives at ≈ 15% of the TE addition with
  **new malformed-key shapes** (v1/v2 used Gi1/0/1, GE1/0/1,
  1//0/1, 1/0/, eth1/0/1, 1.0.1, trailing space): add `01/0/1`
  (leading zero — fails the key pattern), `1/0/1.`, `1-0-1`,
  `GigabitEthernet1/0/1` as a raw key, `1/0/1a`. Each shape is
  asserted against the key leaf's pattern at generation and
  paired with absent-but-well-formed keys for the
  invalid_key vs target_not_found contrast (the v2 lesson).

### 2.5 paired_view (+600) — NEW family: CLI ↔ YANG instance

**What it teaches that pure-YANG cannot:** reading the surface
form operators actually see. YANG_SLICE §6's diagnostic —
JSON-right/CLI-wrong = a reading problem, both-wrong = a
decision problem — is untestable with JSON-only records. This
family pairs an IOS XE CLI excerpt with the RFC 7951 JSON
instance of the *same* state (both are the active-mode
projection, per YANG_SLICE finding F1, so the views agree
exactly) and trains the translation both directions.

**Source assessment (honest):**
- The 17.18.1 paired goldens (`data/yang_slice/`, 9 records +
  11 instances) are the *format template* — they prove the
  pairing discipline (fact equality across views, engine-checked
  answers) — but they are 9 hand-authored records on a
  **different pin** (OpenConfig / XE 17.18.1) from yang-learn's
  26.2.1 native scope. They are not a data source at scale and
  must not be folded in (pin mixing would corrupt provenance).
- The Phase 1 corpus (data/v1.1, 5,861 intent→CLI pairs) has
  CLI at scale, but its `intent_oc` side is a hand-modeled
  OpenConfig-*style* vocabulary, not a YANG instance of the
  26.2.1 native model — pairing from it would teach a
  non-schema dialect. Not used as the YANG side.
- **Chosen source: the yang-learn scenario engine itself.**
  gen_scenarios already produces yangson-validated before/after
  states over exactly the in-scope subtree. A deterministic
  **CLI renderer** over that subtree (vlan configuration-entry
  blocks; per-interface switchport mode/access/voice/trunk
  native/allowed/encapsulation lines; port-security aging and
  maximum lines) renders each state to CLI text. The builder
  writes this renderer as new tooling; `src/plan`'s
  render_config() is the style reference but covers the plan
  state, not native instances — do not assume reuse.

**Verification (mandatory):** round-trip — a parser for the
rendered subset (ciscoconfparse2, the project's existing
checker pattern from the plan slice) must recover the exact
state from the CLI text: parse(render(state)) == state on
100% of emitted records, or the record is dropped and reported.
Records whose state uses constructs outside the renderer's
subset are out of scope for this family, not approximated.

**Forms:** direction A (CLI → JSON instance) 400; direction B
(JSON → CLI) 200 (minority direction, per the goldens'
precedent). States come from **new v3 scenario tuples only**
(§2.4's dedup discipline applies — a paired_view record's
scenario never duplicates an SR/TE scenario in any split).

**Contamination:** paired_view scenarios join the scenario-group
split discipline across ALL families: a tuple reserved for
holdout-v3 (§5) appears in no train record of any family,
paired_view included.

## 3. The maximum-addresses gate (scoped-library extension)

The 1..4097 `max-addresses` leaf lives only on deprecated /
obsolete nodes (list `maxcount`, container `maximum`) inside
port-security groupings in Cisco-IOS-XE-switch.yang. RUN2_PLAN
recorded the blocker: two natural instance shapes for the
`maximum` container return yangson **RawMemberError** — the
grouping variant carrying it is not instantiated under
GigabitEthernet's switchport in the scoped library's model, so
no yangson-validatable IV class exists there today.

Scope of the extension, in order:
1. **Diagnose (pyang):** identify which grouping variants
   (config-interface-switchport-grouping / -obsoleted-v2 /
   -obsolete) are `uses`-instantiated under each interface list
   in the bundle, and whether *any* list instantiates a variant
   carrying `maximum`/`maxcount`. If one does, the class is
   built on that list and no library change is needed.
2. **If none does:** the change is in
   `build_scoped_library.py`'s output — e.g. the obsolete
   grouping's defining module revision/conformance in the
   library, or yangson's obsolete-subtree handling beyond the
   existing `yangson-yl:keep-obsolete` flag. Pin down the exact
   mechanism before editing; a library change made on a guess
   is out of bounds.
3. **Regression gate (hard):** any library change requires
   re-validating **every** existing instance file (corpus
   1,522 + bench 223 + v2 additions) under the new library.
   Every prior verdict must reproduce exactly. Any flip →
   stop, report, class abandoned. Budget: ≈ 3 × 16 min of
   validator wall-clock (§6) plus diff review.
4. **Effort/risk:** ESTIMATE 0.5–1 day, dominated by diagnosis
   and the re-validation gate. Risk is verdict flips in
   unrelated classes, which is why the gate is absolute.
   Value judgment flagged for Codex (decision D2): the node is
   deprecated/obsolete — the class teaches a legacy path. The
   PM-side neighbor-fact mining (§2.1) may deliver most of the
   benefit at zero library risk.

## 4. Contamination guards (all families, binding)

1. Bench v1 files are never modified; bench-reserved facts
   (PM every-7th slice) and bench parameter domains (BENCH_IFS
   1/0/21, 1/0/22, 4/0/1; bench-only IV values; P2/P6) never
   enter train/dev in any form — v1/v2 guards carried forward
   verbatim.
2. Holdout-v3 reservations (§5) are fixed **before** v3
   generation starts and enforced by the same prompt-hash +
   fact-key dedup machinery.
3. Every addition is prompt-hash deduped against train, dev,
   test, benchmark, held_out, and all earlier v3 batches;
   PM additionally dedupes on (normalized path, type) against
   the bench-reserved key set at every concrete path.
4. Facts from pyang only; verdicts from yangson only;
   expected-node + verdict_kind checks on every IV record;
   apply-checks on every TE record; citation-verbatim check on
   every RS record; round-trip check on every paired_view
   record. Drop reports ship with the dataset, per batch.
5. Split assignment stays scenario-group-based (sha256 group
   hash, v1's `split_of`); v3 additions are train/dev only —
   **test.jsonl is not extended** (it stays the v1 test split;
   holdout-v3 is the new generalization surface).

## 5. Eval design

**Bench v1 (frozen, 217 items):** regression check only.
Reported every run for longitudinal continuity; never tuned
against, never trained on. Dev remains the tuning surface
(SCALE_DESIGN §3 discipline, unchanged).

**Dev:** keep the **v1 78-record subset frozen as dev-core-78**
(always reported separately — it is the only dev number
comparable across runs 1, 2, 3) and the v1+v2 141 as
dev-141 (comparable run 2 → 3). Extend with **+140 v3 dev
records** mirroring the new material (PM 40, IV 40, RS 12 from
P7–P12, SR 16, TE 12, paired_view 20), split by scenario group
under the same guards. Dev total 281. Rationale: rebuilding dev
would destroy comparability; extending without a frozen core
would let the dev number drift in meaning. Both failure modes
are avoided by the layered reporting.

**Holdout-v3 (fresh, ≈ 260 items, eval-only)** — the
generalization surface for new capabilities:

| slice | n | what it probes |
|---|---|---|
| PM at held-out interface families | 50 | non-reserved facts asked only at TwentyFiveGigE / HundredGigE paths (train never sees those families, §2.1) |
| IV new traps + new classes | 70 | trap values disjoint from train rings; must_sibling / violation_choice / mac_pattern items if built; interfaces 3/0/1, 3/0/2 (domain disjoint from TRAIN_IFS and BENCH_IFS) |
| RS on P13/P14 | 36 | profiles authored for holdout only — the direct test of the run-2 regression |
| SR/TE on holdout-only grid values | 56 | grid points absent from KIND_GRIDS (e.g. access (60,70), native 888, entry pool ["40","50"]) — parameter generalization, not tuple recall |
| paired_view | 48 | scenario tuples reserved across all families (§2.5) |

**Honesty over runs — position: one-shot per run.** Holdout-v3
is spent by run 3's evaluation. Run 4 gets a freshly generated
holdout-v4 from the same reserved pools (the pools are sized
for this: held-out interface families and P13/P14-style profile
authoring are renewable resources, and holdout grid values are
a large space). A spent holdout's items may fold into train in
the *next* corpus version — after which they are never eval
again. A persistent bench-v2 was rejected: any set evaluated
against repeatedly becomes a tuning target (bench v1 already
fills the longitudinal-anchor role, and its own slow
adaptation is the risk §7 names). The cost of one-shot is
authoring/generation per run — measured in §6 at ≈ 260 records
≈ minutes of machine time plus profile authoring for the RS
slice, the known bottleneck (SCALE_DESIGN §1 said the same of
hand-authored held-out; P13/P14 are authored once for v3 and
their successors budgeted per run).

## 6. Token and cost estimates (measured basis)

Tokens/record, measured with the pinned tokenizer
(Qwen/Qwen3-4B-Base @ 906bfd4; reproduction check: v1 train
totals 369,516 and combined 433,823 both reproduce exactly
against the manifests):

| family | tok/record (combined v1+v2 train) | basis |
|---|---|---|
| path_meaning | 86 | measured, n=381 |
| instance_validity | 304 | measured, n=431 |
| revised_schema | 110 | measured, n=87 |
| state_reconstruction | 589 | measured, n=260 |
| typed_edit | 414 | measured, n=259 |
| paired_view | ~500 | ESTIMATE — a record carries the same JSON state as SR (589 tok/rec total) with CLI text (~120–200 tok) replacing part of the prompt/completion; builder replaces this with a measured figure after the first batch |

Projected v3 train tokens/epoch:
PM 1,081 × 86 ≈ 93k · IV 1,531 × 304 ≈ 465k · RS 537 × 110 ≈
59k · SR 710 × 589 ≈ 418k · TE 609 × 414 ≈ 252k · PV 600 ×
500 ≈ 300k → **≈ 1.59M tokens/epoch** (vs 433,823 in run 2).

**Training** (run-1/2 recipe unchanged: full FT, 2 epochs,
eff. batch 16, lr 1e-5, max_len 1536 — max v3 record must be
verified < 1536 at tokenization; SR/PV records are the long
tail). Run-2 measured throughput: 867,646 trained tokens in
419.7 s ≈ 2,067 tok/s; step method: 178 steps × 2.36 s.
v3: 3.18M trained tokens → ≈ 1,540 s (token method);
5,068 × 2 / 16 = 634 steps × 2.36 ≈ 1,500 s (step method).
Plus startup/save ≈ 115 s (run-2 residual: 535 total − 419.7
train). **Expected ≈ 1,650 s ≈ $1.15 on a100-large ($2.50/hr).
timeoutSeconds 3600 → hard cap $2.50** (2.2× headroom; do not
raise it without re-quoting).

**Batched eval** (bench 217 + holdout-v3 ≈ 260 + dev 281 =
758 rows): run-2 measured ≈ 1.9 s/row all-in on a100-large
(677 s / 358 rows) → ≈ 1,440 s ≈ **$1.00**; timeoutSeconds
2700 → cap $1.88. **GGUF:** run-1/2 precedent $0.18.
**Run-3 expected total ≈ $2.33; program total $3.93 → ≈ $6.26
of $15.** Worst case (all caps hit): ≈ $8.61 — still inside
the envelope.

**Generation + validation wall-clock (local, $0):** yangson
measured on this machine: DataModel build ≈ 39.7 s per
validator subprocess + ≈ 0.08 s/file marginal;
`run_validator` chunks at 400 files → ≈ 72 s per 400 files.
v3 instance files: IV 1,100 × 1 + SR/TE (450+350) × 2
(before+after) + paired_view 600 × 1 + dev/holdout ≈ 550 →
≈ 4,850 files ≈ 13 chunks ≈ **16 min** of validation. Pyang
inventory + generation compute is minutes-scale per batch
(v1 precedent). **Total machine time ESTIMATE 30–60 min** for
the full build; the true bottleneck is human authoring of
P7–P14 (8 profiles), as with every prior held-out.

## 7. Risks and fallbacks

- **Bench overfit (adaptive).** Two runs have now been
  designed against bench v1's failures. The mitigations are
  structural: bench facts/domains stay reserved, design
  decisions tune on dev, and holdout-v3 (one-shot, §5) is the
  generalization check. Residual risk stands: if run 3 gains
  on bench but not on holdout-v3, believe holdout-v3.
- **Unique supply < 4,000.** PM is the tightest pool
  (≈ 1,019 concrete instances + ≈ 115 plain facts against a
  700 target shared with when/must forms — feasible but with
  little slack); RS depends on authoring throughput. Cut
  order if the honest supply lands short: SR additions →
  300, TE → 250, paired_view → 400, PM → actual pool. IV
  (≥ 900) and RS (≥ 350) floors hold. **If the total still
  lands under 4,000, ship the smaller corpus and say so in
  the manifest** — padding with renamed variants is the one
  outcome worse than a smaller corpus, per the uniqueness
  accounting in §2. The remaining lever is scope widening
  (PM over /native/interface non-switchport leaves), which
  changes family scope — decision D3, not a builder call.
- **Yangson scoped-library limits.** Beyond §3: classes whose
  containers the instance builder cannot yet emit (deep
  port-security subtrees) may fail probes; the probe-first
  rule (§2.2) contains this to per-class cuts.
- **Generation failure modes seen in v1/v2:** bystander
  interfaces colliding with the target key (fixed in v1,
  regression-test it); union classes mislabeled from intuition
  instead of the type definition (v1 — labels come from the
  type, yangson confirms); duplicate drops (v2: 11) —
  expected and reported, not relabeled.
- **Renderer risk (paired_view):** if round-trip verification
  fails on a construct class, that class is excluded from the
  family; if the renderer cannot cover the core switchport
  subset at 100% round-trip, the family is cut and its 600
  records revert to §7's cut order in reverse (SR/TE/PM absorb
  nothing automatically — the corpus ships smaller).

## 8. Decisions needed from Codex

- **D1.** Amend SCALE_DESIGN A1: train profiles expand from
  {P1, P3, P4, P5} to {P1, P3, P4, P5, P7–P12}; P13/P14 join
  P2/P6 as eval-reserved. (Build proceeds under Avinash #113;
  this asks for the amendment to be recorded.)
- **D2.** maximum-addresses: attempt the §3 diagnosis + gated
  library extension, or skip the class and rely on PM neighbor
  facts? Recommendation: diagnose (cheap), extend only if the
  diagnosis is mechanical and the re-validation gate passes
  clean.
- **D3.** Scope-widening fallback for PM (§7) — pre-approve as
  a contingency or leave for a future tranche?
- **D4.** Confirm the one-shot holdout position (§5) over a
  persistent bench-v2.

## Summary

Corpus v3 consolidates v1+v2 (1,418 train) with ≈ 3,650
additions to ≈ 5,068 train records, weighted toward the two
proven gaps — plausible, balanced instance_validity (+1,100)
and profile-generalizing revised_schema (+450 on six genuinely
new profiles) — with path_meaning exactness mining the
measured remaining pool (+700), maintenance-sized SR/TE
(+450/+350), and a new paired CLI↔YANG family (+600) sourced
from the scenario engine under a 100% round-trip rule. Bench
v1 stays frozen as the regression anchor; a fresh one-shot
holdout-v3 (≈ 260) carries the generalization verdict. At
measured token rates the corpus is ≈ 1.59M tokens/epoch and
run 3 quotes at ≈ $2.33 expected (program ≈ $6.26 of $15),
with generation ≈ 16 min of yangson validation inside a
30–60 min build. As tranche 1 of the 50%-of-/native program,
it deepens the switchport/vlan subtree from 106 covered leaves
toward its ≈ 600–900-leaf ceiling; the global percentage
moves only with later, scope-expanding tranches. If the
honest unique supply falls short of 4,000 combined, the plan
ships smaller rather than padded.
