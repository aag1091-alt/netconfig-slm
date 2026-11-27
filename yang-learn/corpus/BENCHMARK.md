# Bench v1 — frozen benchmark definition

Bench v1 is the union of two files, frozen together by the commit
that adds `manifest.json` in this directory:

- `../held_out.jsonl` — 8 hand-authored records (HO-01..HO-08),
  including the P2-profile revised_schema pair.
- `benchmark.jsonl` — 209 generated records (B-* ids), produced by
  `../gen_corpus.py` under the reservation rules below.

**Identity:** the freeze commit SHA is bench v1's identity. Any
change to either file creates bench v2 — never an in-place edit.

**Reservation rules** (SCALE_DESIGN.md amendment A1):

- Profile P2 appears only here and in `held_out.jsonl` — never in
  train or dev. Benchmark-only profile P6 likewise appears nowhere
  else. Train/dev profiles are P1, P3, P4, P5.
- Benchmark template families and parameter domains are disjoint
  from train/dev: bench interfaces are GigabitEthernet 1/0/21,
  1/0/22, 4/0/1 (train/dev use 1/0/1–1/0/6, 1/0/10, 2/0/1, 2/0/2,
  2/0/10); IV case values and the path_meaning leaf slice (every
  7th deduped constrained fact) are reserved the same way.
- The benchmark is never trained on and never tuned against; the
  dev split is the tuning surface.

**Composition** (generated portion): path_meaning 32 ·
instance_validity 70 · state_reconstruction 40 · typed_edit 41
(including selector-negatives) · revised_schema 26. Tokenized
with the pinned Qwen3 tokenizer: 66,300 tokens (prompt 48,719 +
completion 17,581); stats in `manifest.json`.

**Grading — mechanical only** (amendment A2): yangson verdicts
for validity items; apply-checks for state/edit items; exact-match
for edit specs and selector rejections; verbatim type expression
(+ default) for path_meaning; leading Yes/No verdict + exact rule
citation for revised_schema. No human or model grading.
