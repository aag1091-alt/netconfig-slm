# Related work notes — 2026-11-26

Saved from Avinash's research brief + Muse's read of the CNSM 2024 paper. Working note; promote into the repo as docs/RELATED_WORK.md on his approval.

## The candidate "base": Hollósi, Ficzere, Varga — CNSM 2024

"Generative AI for Low-Level NETCONF Configuration in Network Management Based on YANG Models" (BME Budapest). Most-cited CNSM 2024 paper (24 citations, OpenAlex).

What they actually did:
- 12 hand-written greenfield scenarios (set IP, gateway, static route, RIP, DNS, NTP, one TSN bridge case, VLAN+IP, dst/src NAT, one combined) against a small fixed topology. No current-state input, no edit semantics.
- 5 LLMs zero-shot: GPT-3.5, GPT-4, GPT-4o, Gemini, Llama3-7B (local). Plus a pipeline: the LLM picks which YANG modules it needs from a name+description list (no embeddings), selected YANGs are pasted into the prompt, model emits NETCONF XML. Pipeline tested on GPT-4o and Llama3 only.
- Validation: yanglint (libyang) + human judgment; VALID / MINOR (conceptually right, namespace/model faults) / MAJOR.
- Results (36 cases per arm): raw GPT-4o 14 valid; GPT-4o+YANG-lookup 24 valid (their headline). Raw Llama3: 0 valid. Llama3+YANG: conceptually fine, syntax errors remain. Authors' conclusion: promising, "not yet mature enough for immediate industrial application in complex cases."
- Claim discipline (Avinash, 2026-11-26): their Llama result shows their small-model prompting setup struggled to produce valid NETCONF XML. It does NOT establish that a prompted small model cannot emit a correct typed plan — the plan schema and decoding constraints may end up doing most of the work. Whether a prompted small model can produce correct typed plans is exactly what the Phase 2 comparison tests.
- Error classes: XML namespace anomalies, vendor-YANG leakage (Cisco's ietf-routing reading vs RFC 8349), YANG `choice` keyword misunderstood, hallucinated/missed modules in lookup.
- Artifacts: paper only. No code, data, or model release found.

Verdict (Muse, 2026-11-26): NOT a base to build on — nothing released, and the method is the opposite bet (no training, general LLMs, greenfield NETCONF payloads, schema conformance as validity). It IS the closest prior work and the natural baseline. Differences that constitute NetConfig's contribution: fine-tuned small model; stateful edits (S → Δ) with preservation of unrelated state scored; clarification as a trained output; compact plan representation (deletes their #1 error class — namespaces — by construction); automated semantic validation (parse + intent + Batfish + state-diff vs hand-authored goldens) at thousands-of-cases scale vs human grading of 252.

Adopt from them:
1. YANG-lookup prompting, adapted (approved by Avinash 2026-11-26): a variant of the prompted-plan arm using their module-selection idea (name+description list, model picks), but taking current state as input and emitting the compact plan. Report it as "YANG-lookup prompting adapted to stateful edits" — NOT a reproduction of Hollósi et al. (a reproduction would generate NETCONF XML on their greenfield task). Freeze rules: same base checkpoint, device context, plan schema, and decoding constraints across both prompted-plan variants, so the comparison isolates the effect of retrieval. Any claim that fine-tuning adds value WITH YANG lookup requires a fine-tuned arm given the same retrieved context.
2. Their error taxonomy as a cross-check on ours; treat YANG `choice` as a design warning for plan ops.
3. Cite prominently — it's the reference reviewers will check against.

## Other prior work (from Avinash's brief)

- openconfig/public — vendor-neutral config/state models; vocabulary for the state our plans change.
- YangModels/yang (vendor/cisco/xe) — IOS XE native models + release-specific OpenConfig deviations; decides what the compiler can support per device/release.
- openconfig/ygot — Go codegen from pinned YANG; option if the compiler moves to Go.
- SLM_netconfig (arXiv 2512.02861) — fine-tunes SLMs NL→config from vendor docs; precedent; compare data pipelines.
- NetConfEval (HF) — NL → structured JSON for network policies (reachability); structured-intent precedent; our target is stateful device edits, a different question.
- Cisco DevNet Catalyst 9300 Guest Shell + Ollama demo (2024) — on-switch inference precedent.

## Contribution statement (Avinash's draft — adopted)

"How small can a model get while translating operator language into OpenConfig-backed, state-aware device edits, with explicit clarification and measured preservation of unrelated state?" A public benchmark + real switch measurements make it stronger than model size or schema use alone.

## Data-technique rules to fold into generation

- Generate from meaning outward (state + edit first, request second) — already our method.
- Paraphrases teach invariance; contrast pairs teach sensitivity; keep the words fixed and change the state (one "uplink" → plan; two → clarify).
- Split integrity: contrast/clarification groups stay together — never straddle train/eval. Independently written requests are eval-only.
- Teacher paraphrases allowed for phrasing only, with verb/identifier/condition preservation checks before acceptance ("add" must not drift to "set").
- Train on observed failures with a frozen benchmark; error tags: wrong operation, dropped port, wrong alias, unnecessary clarification.
- Classifier arm (approved by Avinash 2026-11-26): operation classifier + slot extraction gets its OWN arm, with the same inventory and alias resolver as the plan models, required to emit the same plan or `clarify`, scored by the same scorer — a meaningful on-switch baseline.
- On-switch (Guest Shell) measurements — peak RSS, cold start, p95 latency, control-plane CPU on one Catalyst SKU + IOS XE release — are a later phase; needs Avinash's SKU choice.
