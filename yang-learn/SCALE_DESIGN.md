# yang-learn — scale-up design & training quote (v1 corpus)

Status: design for review (Codex + Avinash). The 41-record sample is
cleared as schema-training prep (independent replay, byte-for-byte,
2026-11-26). **No bulk generation and no paid job start until this
design is approved.** Prepared per Codex #69/#70.

## 1. Lean first corpus — ~1,450 train_candidate records

Deliberately lean: large enough for every constraint class to be
learned with margin, small enough to review, regenerate, and iterate
on after the first evaluation signal. (An earlier internal draft
proposed 2,600–3,900; cut to this on the lean-first instruction.)

| family | n | generation sketch |
|---|---|---|
| path_meaning | ~350 | every in-scope leaf carrying ≥1 non-trivial constraint (range / enum / pattern / union / default / when / must), plus structural records for choices, cases, presence containers, and list keys |
| instance_validity | ~450 | mutation templates per constraint class, parameterized over interfaces, VLAN IDs, and values; ~50/50 valid/invalid; every invalid breaks exactly one rule and must pass the expected-node + verdict_kind check |
| state_reconstruction | ~300 | the 8 scenario archetypes over a parameter grid (interface names, VLAN IDs, start states), including 2–3-interface states |
| typed_edit | ~250 | the 4 edit ops over the same grid; every spec carries an explicit target selector (list + key); ~10% selector-negative records (unknown key / malformed key) whose correct output is a rejection, not a silent apply |
| revised_schema | ~60 | profiles P1/P2 plus at most 3 new hand-authored profiles; questions template-varied, profiles never templated |
| sample reuse | 41 | the cleared sample folds in unchanged; its schema-only records keep their scope labels (see §5) |

Held-out stays hand-authored only; target +10–15 records before
training. This is the schedule bottleneck and is stated as such.

### Constraint coverage (minimums across the corpus)

range ≥120 · enum ≥100 · pattern ≥60 · union ≥60 · choice ≥60 ·
when/must ≥60 · list-key ≥50 · presence ≥40 · default ≥40 ·
leaf-list ≥30. A coverage report is generated with the dataset;
any class under its minimum blocks the training checkpoint.

## 2. Split discipline — by scenario, across task forms

Records are grouped by **scenario/template family**. All task forms
derived from one scenario (its path_meaning, instance_validity,
state_reconstruction, and typed_edit variants) go to the **same**
split — a scenario never straddles train and test in different
clothes. Split ~80/10/10 train/dev/test by scenario group, seeded
and recorded in the dataset manifest.

## 3. Frozen independent benchmark (bench v1)

- ~150 items: the 8 hand-authored held-out records + generated items
  from **held-out templates** (template families never used for
  training generation) + selector traps + multi-interface cases.
- Frozen by commit SHA **before** any training run; the SHA is the
  benchmark's identity. Any change creates bench v2 — never an
  in-place edit.
- Graded mechanically only: yangson verdicts for validity items,
  apply-checks for edit items, exact-match for edit specs and
  selector rejections. No human or model grading.
- The benchmark is never trained on, never tuned against; dev split
  is the tuning surface.

## 4. Checks carried into the plan (Codex #70)

- **Edit-selector checks.** Every typed edit's selector is itself
  validated: the key value must satisfy the key leaf's type/pattern
  (e.g. the GigabitEthernet name pattern), and the selector must
  resolve to exactly one entry in the before-state. Zero or multiple
  matches = generation failure, record dropped and reported.
- **Release-specific device checks.** All records are pinned to the
  XE 26.2.1 bundle (YangModels/yang @ 9c23db9) and carry
  per-defining-node module/revision provenance. Claims do not
  transfer across releases by assertion: supporting another release
  means re-running the full chain (load, inventory, generation,
  yangson validation) against that release's bundle, because module
  sets and revisions differ per release.
- The sample's validation chain is unchanged at scale: pyang facts
  ≠ yangson verdicts; expected-node rule verification; verdict_kind
  classification; apply-checks. Drop reports ship with the dataset.

## 5. Executable claims — evidence tiers

- **Tier A (schema-executable)** — today's `executable_on_device:
  true`: schema-valid and operational prerequisites satisfied
  *within the state* (referenced VLAN has a configuration-entry,
  not a deletion, no legacy enum). This is a schema-derived claim.
- **Tier B (device-confirmed)** — verified against a real device or
  emulator readback (NETCONF/CML). **No Tier B claims exist yet**
  and none will be made until such evidence exists.
- Schema-only records (missing prerequisite, deletion, legacy enum)
  are reused from the sample with labels intact — they teach the
  schema boundary precisely because they are labeled non-executable.

## 6. Training quote

- Method: full fine-tune of Qwen/Qwen3-4B-Base on HF Jobs, run-3
  pattern, dependency stack pinned (torch==2.5.1,
  torchvision==0.20.1, torchaudio==2.5.1, transformers>=4.51,<4.58)
  — the pin exists because the unpinned image's torchvision is
  broken (pilot job 6b08cd026c5d61fff187c2fa, ~$0.04, zero output).
- Size basis: sample records average ~750 characters ≈ ~215 tokens
  (prompt + completion, char-based estimate); multi-interface
  records run longer, so plan on ~300 tokens/record average →
  ~435k tokens/epoch for 1,450 records.
- Cost basis: Phase-1 run 3 cost **$3.19** for 5,861 pairs at a
  comparable per-run token volume. Quote for corpus v1:
  **$4–12 for one full fine-tune**, firmed to an exact figure by
  tokenizing the frozen corpus before submission; **<$1**
  evaluation job; **~$0.10** GGUF conversion (cpu-basic precedent).
  Total program ceiling for v1: **$15**.
- Generation + validation: **$0** external (local pyang/yangson);
  wall-clock is the real cost (validation batching + review).

## 7. Sequence & gates

1. **This design approved** (Codex review; Avinash owns spend).
2. Bulk generation locally → dataset + counts + coverage + drop
   report → review checkpoint.
3. Benchmark v1 frozen by commit SHA.
4. Training quote firmed by tokenization → Avinash approves spend.
5. Train → evaluate on frozen bench + held-out → report raw scores
   before any claims.

## 8. Known limits (carried from the sample README)

- Schema-valid ≠ device-accepted (e.g. the schema admits an access
  VLAN container on a trunk; no `when` ties it to access mode).
- yangson marks the native root obsolete (no status statement in
  source); the scoped library carries yangson's own
  `keep-obsolete` extension — flagged, not hidden.
- Exact Catalyst 9300 SKU behavior unverified; the cat9k family set
  is the provenance.
