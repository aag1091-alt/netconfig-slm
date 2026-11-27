# yang-learn run 2 — plan (2026-11-27)

Direction: Codex board #87 + Avinash #86 — drill run 1's three
gaps (path_meaning recall, revised_schema citation discipline,
clean stopping) with corpus v2 additions, and replace sequential
eval generation with batched generation. Under Avinash's standing
approval (board #78: anything below $15 total for in-scope
NetConfig work), no separate go signal is needed. This document:
(a) run-1 failure analysis, (b) the corpus v2 design as built,
(c) the measured run-2 quote. The run-2 TRAINING job is not
submitted by this prep; the batched dev-eval job in §4 was
submitted under Codex #88.

## 1. Run-1 failure analysis (bench v1, 160/217 = 73.7%; 57 fails)

Source: `outputs/yang-learn-run1/scores.json` +
`generations_benchmark.jsonl`, graded mechanically per
SCALE_DESIGN §3 + A2. Every fail below was classified by hand
against the expected answer.

### By family

| family | score | fails | dominant failure modes |
|---|---|---|---|
| state_reconstruction | 40/40 | 0 | — |
| typed_edit | 38/42 | 4 | error-kind confusion (3), leaf name (1) |
| instance_validity | 56/73 | 17 | false-valid bias (13), false-invalid (2), held-out format (2) |
| revised_schema (strict) | 13/28 | 15 | citation missing (13), verdict wrong (2) |
| path_meaning | 13/34 | 21 | type recall (see breakdown) |

### path_meaning — 21 fails, by error type

- **Typedef-name recall (4):** leaves whose type is
  `ios-types:cisco-mac-address-type pattern -> string pattern`
  answered as `union {string, uint16}` or as VLAN ranges — the
  model had never seen that typedef in training (its facts sat in
  the benchmark's reserved slice at the paths v1 sampled).
- **Range-bound recall (5):** `2..1001 | 1006..4094` answered
  `1..1001 | 1006..4094` (×3); `1..4097` answered `1..4096` (×2).
  Off-by-a-little bounds on facts the model otherwise located.
- **Union branch detail (6):** expected `union {uint16, string}`,
  answered `union {uint16 range 1..4094, string}` (×3,
  over-specified branch); expected `string pattern`, answered
  `union {uint16, string}` (×2); expected the access union
  `union {uint16 range 1..4094, enumeration {dynamic}}`, answered
  the bare range, dropping the enum branch (×1).
- **When-records (2):** answered in prose; the verbatim xpath
  (`../../../../mode/trunk`) never appeared.
- **Wrong base type (3):** `uint32 range 4096..16777215` answered
  as uint16; `enumeration {broadcast}` answered `string`;
  `boolean` (default true) answered as an enumeration.
- **Held-out format (1):** HO-04's hand-authored form.

19 of the 21 PM fails also failed to stop cleanly, but PM grading
is substring-based — the type string was absent, so these are
recall failures first; rambling is a second, separate defect.

### revised_schema — 15 fails

- **13 verdict-correct, citation missing:** the model answers
  "Yes. 52 is even." — right verdict, sound reasoning, but the
  exact `P# rule (n)` string the strict scorer requires never
  appears. Verdict-only score: 26/28 = 92.9%.
- **2 verdict-wrong:** B-RS-025/026 — both P6 rule (3) (aging
  time at least 10 minutes) with 60 minutes: the model inverted
  the floor logic ("at least 10, not more than 10").
- 9 of 15 rambled past the answer into fabricated follow-up Q&A,
  in two cases contradicting the (correct) first answer.

### instance_validity — 17 fails

- **13 false-valid:** invalid benchmark instances called
  "Valid." — union_access ×2, enum_encap ×2, range_aging ×2,
  enum_aging ×2, pattern_entry ×2, pattern_ifname ×1,
  choice_mode ×1, union_allowed ×1. No single class dominates;
  it is a general invalid-detection sensitivity gap. (Of the 33
  benchmark invalids overall, the model named the expected node
  in only 12.)
- **2 false-invalid:** valid union_voice instances called
  Invalid, with fabricated union branch lists ({dot1q, static,
  trunk} / {dynamic}) — the model "knows" the leaf is a union but
  not its actual branches.
- **2 held-out format:** HO-01/HO-02 multi-case items answered in
  prose shapes the mechanical grader can't credit.

### typed_edit — 4 fails

- **3 selector-negatives:** the malformed key `Gi1/0/1` was
  rejected as `target_not_found` instead of `invalid_key` — the
  model rejects, but cannot distinguish a malformed key from an
  absent one.
- **1 held-out (HO-06):** one leaf name wrong (`vlan` for
  `vlan-id`) in an otherwise exact state.

### Stopping (cross-cutting)

150/217 generations stopped cleanly (69.1%). Pass rate when
stopped: 83.3% (125/150); when not: 52.2% (35/67). 32 of the 57
fails are non-stopped generations. The typical ramble fabricates
the next user/assistant turn and, for RS, sometimes overturns a
correct verdict. Training-side levers available in corpus scope:
more short-completion volume in exactly the v1 formats, and no
format drift (see §2).

## 2. Corpus v2 — as built (`gen_corpus_v2.py`)

**434 additions** (0 validation drops, 11 duplicate drops),
written to `corpus/train_v2_additions.jsonl` (371) and
`corpus/dev_v2_additions.jsonl` (63); split assigned by scenario
group (~85/15, never test). Benchmark v1 files untouched;
additions deduped by prompt against all v1 splits + held_out +
the sample (0 overlaps survive). Addendum:
`corpus/manifest_v2_addendum.json`; instance files in
`corpus/instances_v2/`.

| family | train | dev | total | what it targets |
|---|---|---|---|---|
| path_meaning | 214 | 40 | 254 | type recall at new paths + plain types |
| instance_validity | 105 | 15 | 120 | false-valid bias, union branches |
| revised_schema | 27 | 6 | 33 | citation discipline |
| typed_edit (negatives) | 25 | 2 | 27 | invalid_key vs target_not_found |

### path_meaning — the pool CAN be widened (with evidence)

v1's pool was pool-bound at the *deduped representative* level,
not at the concrete-path level. Measured on the 26.2.1 bundle
(pyang inventory, in-scope = `/native/vlan` + any `switchport`
path; 2,952 leaves):

- Deduped constrained pool: 224 facts (v1's A4 reported 235
  including sample-fold effects) — fully used by v1, one
  representative path each.
- **1,229 further concrete instances** of those same facts exist
  at paths v1 never used (other interface families:
  TenGigabitEthernet, Port-channel, FastEthernet,
  TwentyFiveGigE, HundredGigE, Wlan-GigabitEthernet, …).
- **175 deduped unconstrained facts** (boolean/string/empty)
  were excluded entirely by v1's constrained() filter.

Widening rule that keeps bench v1 honest: a fact whose
(interface-normalized path, type) key is in the benchmark's
reserved every-7th slice is excluded **at every concrete path** —
the reserved fact never enters train/dev in any clothes. Only
non-reserved facts are emitted, at unused paths: ≤2 per fact
(≤3 for the failure classes — when/pattern/typedef), preferring
interface families the v1 representative didn't use. Result:
210 constrained-path records + 44 plain-type records = 254
(by type class: range 67, enum 44, union 38, string 28,
when 24, empty 16, typedef 15, boolean 13, pattern 9). Facts
via pyang only, same as v1.

### instance_validity — fresh values, same classes

41 new cases × 3 state variants = 123 generated, 120 kept
(3 dropped as exact duplicates of v1's range_access 4094-valid
records). Values are disjoint from the v1/bench case lists
(boundary-adjacent: aging 1442/2880, entry keys "1-4095"/"4096",
aging type "Absolute"/"in-activity", encap "DOT1Q"/"8021q",
interface name "01/0/1"/"1/0/1 ", new choice pairs
access+dot1q-tunnel and trunk+dynamic, maximum-vlan-access at
200). Verdict mix is deliberately tilted: 72 invalid / 48 valid,
against run 1's false-valid bias, and the union classes carry
string-branch traps in both directions (valid "4096" string for
union_allowed; invalid 4096 int for union_access/union_voice).
Every label yangson-confirmed via `validate_all` (72
expected_rejection + 48 accepted, 0 disagreements).

**Not built — range class on port-security maximum-addresses:**
the PM failures showed the model mis-recalling the 1..4097
range, suggesting an IV class on it. Two natural instance shapes
for the `maximum` container were probed against the scoped
yangson library; both return RawMemberError (the grouping is not
instantiated under GigabitEthernet's switchport in the scoped
library), so no yangson-validatable class exists there without
extending the scoped library — a separate reviewed change
(`build_scoped_library.py`), proposed here, not done. Range
discipline is instead reinforced via the aging/native/access
boundary cases above and the PM range records.

### revised_schema — citation discipline

38 authored cases across all 12 train-profile rules (P1/P3/P4/P5
only; amendment A1 unchanged — no new profiles), 33 kept
(5 dropped as phrasing-identical duplicates of v1 records).
Verdicts are fixed by the rule semantics in the generator and
every completion is templated to embed the exact citation
(`profile P# rule (n) …`) — verified: 33/33 completions contain
their citation verbatim. Three question phrasings rotate
("Is that allowed?" / "Does that comply?" / "Is that permitted
under the profile?"). Verdict balance 17 allowed / 16 not.
(A further option — a fifth train profile to widen
profile-generalization training — is deliberately NOT taken
here: A1 fixes the train profile set; flagged for Codex's review
instead of silently expanding it.)

### typed_edit — selector-negative discrimination

30 generated, 27 kept. Six new malformed-key shapes (GE1/0/1,
1//0/1, 1/0/, eth1/0/1, 1.0.1, "1/0/1 "), each asserted against
the key leaf's pattern at generation; absent-but-well-formed
keys (1/0/7, 1/0/8, 2/0/3, 3/0/1; entries 88/99/77/55) provide
the target_not_found contrast. Kept: invalid_key 13,
target_not_found 14. Base states yangson-validated via
`validate_all` as in v1.

### Stopping

No new formats anywhere: every addition's completion is in
exactly its family's v1 shape, and additions are short
(train additions: 8,442 completion tokens / 371 records ≈ 23
tokens average). SR/TE positives needed no additions (100% /
positives clean). Run 2 keeps the run-1 training recipe
unchanged (full FT, 2 epochs, eff. batch 16, lr 1e-5, max_len
1536, EOS assertion) so the corpus is the only variable.

### Token stats (pinned tokenizer, as tokenize_corpus.py)

- train additions: 371 records, 64,307 tokens (prompt 55,865 +
  completion 8,442), longest record 448.
- dev additions: 63 records, 9,592 tokens (8,265 + 1,327),
  longest 418.
- **train v2 combined (v1 train + train additions): 1,418
  records, 433,823 tokens/epoch, longest record 834** — zero
  truncation at max_len 1536, unchanged headroom.

## 3. Run-2 quote (measured, from run-1 actuals)

Basis: run 1 trained 739,032 tokens in 328.1 s on a100-large
(2,253 tok/s; 132 steps → 2.486 s/step), same config. Run 1's
$2.50 total was dominated by sequential eval generation
(~50 min), not training.

**Training job** (a100-large $2.50/hr, run-1 recipe, train v2):
- Trained tokens: 2 × 433,823 = 867,646 → token method ≈ 385 s;
  step method: 178 steps × 2.486 s ≈ 443 s.
- Startup + model save allowance ≈ 300 s (run-1 residual).
- **Expected: 685–745 s ≈ $0.48–$0.52. Quoted $0.50–$0.90 with
  variance margin. Hard cap: timeoutSeconds=3600 → max $2.50.**

**Batched eval job** (dev 141 + bench 217 + held-out 8 = 366
rows, batch 16): firmed by the §4 measurement — batched
generation on a10g-small ran 3.34 s/row (78 dev rows in 260.6 s),
which puts 366 rows at ~1,220 s of generation alone: too close
to a 1,800 s a10g cap once startup is added. The run-2 eval
therefore goes on **a100-large** (the training flavor; batched
throughput should roughly halve the a10g per-row time):
**expected 750–950 s ≈ $0.52–$0.66; quoted $0.55–$0.90; hard
cap timeoutSeconds=1800 → max $1.25.**

**GGUF conversion:** run-1 precedent $0.18 (a10g-small, 659 s);
quoted $0.20–$0.30.

**Program ledger:** spent before run 2 ≈ $2.80 (run 1 $2.50 +
cancelled attempt ≈ $0.12 + GGUF $0.18) + dev eval $0.11 (§4) =
$2.91. Run 2 expected ≈ $1.25–$1.85 all-in (training ~$0.50 +
eval ~$0.60 + GGUF ~$0.20) → **program expected ≈ $4.15–$4.75;
worst case with every cap hit ($2.50 + $1.25 + $0.30) ≈ $6.96 —
strictly under the $15 envelope.**

## 4. Batched dev eval of the run-1 model (Codex #88)

`eval_batched.py` (local/batched core) + `job_eval_dev_batched.py`
(HF job, published model `aag1091/netconfig-yang-qwen3-4b`, dev
split only, 78 records, sha-guarded to the frozen commit;
a10g-small, timeoutSeconds=1800). Results:

Job `6b09ecb46c5d61fff189c3bc` — **COMPLETED**. Durations:
scheduling 66 s + running 327 s = **393 s total on a10g-small
($1.00/hr) → actual cost $0.11**. In-job: model load + startup
≈ 52 s, batched generation 260.6 s for 78 rows (3.34 s/row —
vs ~13.8 s/row for run 1's sequential generation on the faster
A100). Outputs downloaded to `outputs/yang-learn-run1/`:
`generations_dev.jsonl`, `dev_eval_stats.json`, `DONE.json`
(bucket prefix `yang-learn-dev-eval/`).

EOS/length stats (the counts Codex asked for): **stopped_on_eos
55/78 (70.5%)**, stopped_cleanly 55/78 (no EOS-stop fabricated a
follow-up turn on dev); output tokens mean 340.3, p50 284,
p95 768, max 768 (the p95 = cap is the non-stopper tail).

Dev scores (score_bench.py's graders, dev references — the first
dev measurement; run 1's job died before producing dev
generations): **63/78 = 80.8% overall** — SR 24/24 (100%),
TE 15/15 (100%), RS strict 5/6 (83.3%), PM 7/15 (46.7%),
IV 12/18 (66.7%). Pass when stopped 89.1% vs 60.9% when not —
the same stopping correlation as bench v1, and the same two
weak families (PM, IV), confirming the bench gaps are not
benchmark artifacts. Full per-row scores:
`outputs/yang-learn-run1/dev_scores.json`.
