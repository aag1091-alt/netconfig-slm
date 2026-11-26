# Paper outline — NetConfig

Drafted 2026-11-26. Status markers: **[DONE]** = results in hand, **[HELD]** = waits on the Phase 2 comparison, **[OPEN]** = needs Avinash's decision.

## Working titles

1. **Recommended:** *Plans, Not Payloads: Semantic Parsing and Deterministic Compilation for Stateful Network Configuration with Small Language Models*
2. *How Small Can It Get? State-Aware Device Edits from Operator Language, with Explicit Clarification and Measured Preservation*
3. *Editing Running Networks: Typed Change Plans for Small On-Device Models*

## Abstract skeleton

[Problem] Prior work on language models for network configuration generates configurations for a fresh device. Operators, however, edit running devices: a request means a change against existing state, and a correct change must leave everything else untouched. [Approach] We present a two-stage architecture — a small language model translates an operator request and a snapshot of device state into a compact, typed change plan; deterministic code validates the plan against the device schema, applies it, and renders the CLI. Acceptance requires three distinct checks: the resulting state is schema-valid, the requested intent is achieved, and unrelated state is preserved. Ambiguity is a trained output: when a request under-specifies the target, the correct behavior is to ask. [Evidence 1 — DONE] A generation-and-validation pipeline (intent-first synthesis; dedupe, intent-match, parse, and Batfish gates) produced a 5,861-pair Cisco IOS XE corpus; a fully fine-tuned Qwen3-4B scores 100% parse / 92.5% intent / 98.8% Batfish on held-out greenfield generation, and a cause-tagged analysis of its failures shows 11 of 12 residual errors are output-length truncations — the defect class the plan representation removes structurally. [Evidence 2 — HELD] On a frozen, independently authored benchmark of stateful VLAN/trunk edits, we compare five arms — direct CLI generation, prompted plans, prompted plans with YANG-lookup context (adapted from Hollósi et al.), fine-tuned plans, and a classifier + slot-extraction baseline — on correct state change, unintended changes, clarification accuracy in both directions, output tokens, and latency. [Result slots: TBD.] [Claim] The results identify how much reliability comes from training, how much from narrowing the output space, and how small a model can get while remaining trustworthy enough to propose changes to a running device. All data, models, code, and the benchmark are public.

## Research questions

- **RQ1 (size):** How small can a model get while translating operator language into OpenConfig-backed, state-aware device edits? (4B → 1.7B → 0.6B step-down on the winning approach.)
- **RQ2 (attribution):** How much reliability comes from training, and how much from narrowing the output space — plan schema, decoding constraints, deterministic compilation? (Prompted vs fine-tuned vs classifier arms; the YANG-lookup variant isolates the effect of retrieval.)
- **RQ3 (restraint):** Can a small model learn when *not* to act — asking on ambiguous requests without over-asking on clear ones? (Clarification scored in both directions.)
- **RQ4 (defect classes):** Does the plan representation eliminate the observed failure classes of direct CLI generation — truncation on long enumerations and hostname slips — and what replaces them? (Cause-tagged errors, same taxonomy across arms.)

## Contributions (as they will be claimed)

1. A formulation of network configuration as **stateful editing** (S, request → Δ) with preservation of unrelated state as a first-class, measured criterion — vs the greenfield payload generation of prior work.
2. A **plan + compiler architecture** in which the model emits a small typed change plan (9 operations + clarify for the evaluated slice) and deterministic code owns validation, application, and rendering — including device binding outside generation, so hostname errors are structurally impossible.
3. A **benchmark designed to catch shared bugs**: hand-authored expected diffs and invariants (the engine is graded against them, never their source), same-state contrast triples, an independent CLI interpreter as a second scorer, and a frozen split with group-integrity (contrast/clarification groups never straddle train/eval).
4. A **five-arm comparison** under a frozen protocol (same checkpoint, context, schema, constraints, token budget) that attributes accuracy to training vs output-space narrowing vs retrieval — including a faithful adaptation of the strongest published prompted approach (Hollósi et al., CNSM 2024) and a non-generative classifier baseline.
5. Public artifacts: dataset, models, code, benchmark.

## Section plan

**1. Introduction.** Operations reality (changes to running devices); the greenfield bias of prior work; the three acceptance checks; contributions; results preview [HELD slots].

**2. Related work.** Positioning table (below); Hollósi et al. CNSM 2024 (closest work; prompted NETCONF generation, YANG lookup); SLM_netconfig (arXiv 2512.02861; fine-tuning from vendor docs); NetConfEval (NL → policy JSON; not device edits); grammar-constrained decoding (EMNLP 2023); Cisco DevNet Guest Shell demo (on-switch inference precedent).

| Work | Task | Current state? | Trained? | Output | Validation | Artifacts |
|---|---|---|---|---|---|---|
| Hollósi et al. 2024 | NL → config | No | No (prompting + YANG lookup) | NETCONF XML | yanglint + human, 252 cases | Paper only |
| SLM_netconfig 2025 | NL → config | No | Yes (SLM FT) | CLI | [per paper] | [per paper] |
| NetConfEval | NL → policy | No (policy-level) | Eval set | JSON | Exact/semantic match | Dataset |
| **This paper** | NL + S → edit | **Yes; preservation scored** | **Yes (SLM FT)** | **Typed plan → CLI** | **State-diff vs hand-authored goldens + Batfish, automated** | **Data, model, code, benchmark** |

**3. Approach.** State model (typed tree with references; OpenConfig vocabulary; IOS XE native models pin what the compiler supports per release). Plan schema and operation contract — add/remove/replace semantics, refusal boundary, the four design decisions **[OPEN: Avinash's ruling]**. Apply/render engine. Device binding. Completeness-before-apply (reject, never partially apply). Clarification as an output class.

**4. Data.** Generation from meaning outward. Phase 1 corpus and its gates; quality numbers per version (v1.0 5,000 → v1.1 5,861 → v1.2 pending). Phase 2 slice data: sampled (S, op) pairs, surface forms, contrast triples, clarification cases, teacher paraphrases with verb/identifier preservation checks. Split integrity. Benchmark construction and the shared-bug argument for hand-authored expected diffs.

**5. Experiments.**
- **Exp A [DONE] — direct CLI generation.** Run 3: full fine-tune, 252-row test: parse 100%, intent 92.5% triaged, Batfish 98.8%. Failure cause-tagging: vlan_l2's 12 real failures = 11 truncation, 1 list-semantics, 0 hostname. Motivates the plan representation.
- **Exp B [HELD] — the comparison.** Five arms (see Table 1 shell). Frozen protocol per the contract §6 + approved amendments: YANG-lookup arm labeled "adapted to stateful edits"; fine-tuned arm receives identical retrieved context for the retrieval claim; classifier shares inventory, alias resolver, output space, scorer. Greenfield and stateful results reported separately.
- **Exp C [planned] — step-down.** 1.7B and 0.6B on the winning arm; where does accuracy break, and on which metric first?
- **Exp D [planned] — data scaling.** Accuracy vs training rows (e.g. 25/50/100%) — how much data does the plan task actually need?
- **Exp E [stretch] — on-switch.** One Catalyst SKU + IOS XE release in Guest Shell: peak RSS, cold start, p95 latency, control-plane CPU during inference. **[OPEN: SKU choice — Avinash.]**

**6. Results.** Table shells below; per-op breakdown; clarification confusion (guesses on ambiguous / over-asking on clear); error-cause tags per arm.

**7. Discussion.** Training vs output-space narrowing (whichever way Exp B lands — the paper is honest either way: if prompted plans + constraints match fine-tuning, that *is* the finding, and the classifier arm bounds it from below). Refusal boundaries as learned behavior. Limitations: single vendor, one slice (VLAN/trunk), synthetic state, Batfish's unmodelable classes (6 found in Batch 9 probing), no production traffic.

**8. Conclusion.** The size floor for trustworthy stateful edits; what to build next (more families, full-device composition, on-switch).

**Artifacts & reproducibility.** HF dataset/model links, GitHub repo, benchmark + goldens, scoring code. Training cost accounting (~$20 total project spend to date — a point worth making: this research is cheap to reproduce).

## Results table shells

**Table 1 — Exp B main results (stateful split; greenfield reported separately).**

| Arm | Correct change % | Unintended changes (cases) | Clarify recall (ambiguous) | Clarify precision (clear) | Exact-membership shortfall (ports) | Output tokens (med) | Latency p95 |
|---|---|---|---|---|---|---|---|
| Released CLI model (control) | | | | | | | |
| Prompted plans | | | | | | | |
| Prompted plans + YANG lookup (adapted) | | | | | | | |
| Fine-tuned plans | | | | | | | |
| Fine-tuned plans + YANG lookup | | | | | | | |
| Classifier + slot extraction | | | | | | | |

**Table 2 — Error causes by arm (cause-tagged, same taxonomy as Exp A).**

| Arm | Wrong op | Dropped port/VLAN | Wrong alias | Unnecessary clarify | Missed clarify | Invalid plan | Truncation |
|---|---|---|---|---|---|---|---|
| (per arm) | | | | | | | |

**Table 3 — Exp A (done): run 3 held-out results.** Parse 252/252 · Intent 92.5% triaged (86.9% raw) · Batfish 249/252 · vlan_l2 causes: 11 truncation / 1 semantics / 0 hostname.

## Figures

1. Architecture: request + state snapshot → plan → validate → apply → render → three acceptance checks.
2. The canonical contrast triple: trunk {10, 30} under add / allow-only / remove (one state, three outcomes).
3. Exp A failure causes (bar: truncation dominance) → motivates §3.

## Venue plan

- **arXiv preprint** as soon as Exp B results exist (fits built-in-public; establishes the claim early).
- **CNSM 2027** — the natural home (Hollósi et al.'s venue); CFP not yet posted, last three cycles submitted ~late June–mid July, so plan for ~mid-2027. IEEE 2-column format.
- NOMS 2027 checked and missed: technical deadline was 2026-11-09.

## Open items for Avinash

1. The four contract decisions (gate Exp B, hence the paper's core).
2. Affiliation on the paper (Cisco vs independent) — his call, affects the title page only.
3. Exp E SKU/release choice (later; doesn't block the main paper).
4. Whether the classifier arm's result, if strong, reframes the paper toward "how much model do you need" — decide after Exp B, not before.
