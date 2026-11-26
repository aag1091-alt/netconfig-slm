# NetConfig SLM — Capability & Coverage Matrix

Status: FIRST ROADMAP HANDOFF (2026-11-26). Governing plan:
`docs/ROADMAP.md`. Edit contract: `docs/PLAN_CONTRACT.md`. Bulk label
generation remains HELD; the labeled cases referenced here are the
hand-authored representatives in `data/capability_examples/examples.jsonl`
(verified by `scripts/check_capability_examples.py`).

## 1. The common record

Every dataset example — training, pilot, or benchmark — is one record:

```json
{
  "id": "ex-plan-provision-1",
  "task": "plan_edit",
  "variation_axis": "words | device | policy | evidence | scale | none",
  "group": "optional contrast/pair group id (kept together in splits)",
  "request": "literal operator wording",
  "evidence": { ... see below ... },
  "expected": { ... one of the answer kinds in §2 ... },
  "note": "optional authoring note"
}
```

`evidence` is a dict; every key is optional, and a task uses only the
keys its decision needs. Absence of a key is itself evidence (no policy
supplied → no policy check is scored).

| Evidence key | Content | Used by |
|---|---|---|
| `state` | Normalized device state, exactly the `src/plan/state.py` shape (`device`, `hostname`, `vlans`, `interfaces{}`). | all tasks |
| `cli_excerpt` | IOS XE CLI text carrying the same facts as a state (pilot phase 2 form; §5). | read, interpret |
| `capabilities` | Supplied device capability facts: `{platform, software, supported_modes, features{...}}`. Supplied evidence, not platform claims — the skill tested is reading it. | eligibility, recommend |
| `policy` | Supplied site policy/profile facts as a flat dict of named facts, e.g. `{"profile": "iot-device", "data_vlan": 40, "enable_port": true}`, `{"forbid_trunk_repurpose": true}`, `{"permitted_access_vlans": [10, 20, 30]}`, `{"admin_enable_authorized": true}`. | eligibility, recommend, plan_edit |
| `prior_state` | State before some earlier change (rollback / comparison tasks). | remediate (later), interpret |
| `proposal` | A proposed edit in plan shape (`{device, operation, ...}`), for eligibility questions ("can I …?"). | eligibility |
| `candidate` | A proposed change under review: `{"plan": {...}}` (CLI-diff form `{"cli_diff": "..."}` is a later format extension). | review |
| `readback` | Observed evidence: `{"config_state": <state-shaped dict or null>, "config_excerpt": <CLI or null>, "tests": [{"kind": "traffic", "result": "pass"|"fail", ...}]}`. | interpret |

## 2. Answer kinds (the `expected` registry)

All answer shapes are compact, typed, and mechanically scorable — the
plan-schema philosophy extended to non-edit tasks. No prose answers;
explanations travel as *keys* (rationale keys, reason keys, finding
notes are short and drawn from small vocabularies).

| Kind | Shape | Scorer |
|---|---|---|
| `plan` | `{"kind": "plan", "plan": <plan per PLAN_CONTRACT §2>, "assertions": [{"path", "equals"}]}` | Plan validates (`validate_output`), applies, diff ⊆ primary ∪ independently derived cascades (`cascades.py`), assertions hold in S′. The plan object itself is scored, not only the diff (see §5). |
| `no_op` | `{"kind": "no_op", "assertions": [{"path", "equals"}]}` | Assertions already hold in S; the correct behavior is to change nothing. |
| `clarify` | `{"kind": "clarify", "clarify": <clarify object per PLAN_CONTRACT §5>}` | Reason must match; candidate set must equal the inventory-derived set exactly (contract §6c). Scored symmetrically: guessing when clarify is correct is an error, and so is clarifying when a direct answer exists. |
| `facts` | `{"kind": "facts", "facts": {<path-tree of grounded values>}, "unresolved": [{"interface", "field", "reason"}]}` | Per-fact exact match against the supplied evidence (state, or the parsed CLI excerpt). `unresolved` entries are scored too: the named field must genuinely be absent from the evidence. |
| `eligibility` | `{"kind": "eligibility", "verdict": "eligible"\|"prerequisites_missing"\|"blocked", "checks": [{"name", "status"}], "missing_prerequisites": [...], "blocking_reasons": [...]}` | Checks are recomputed from the evidence by rule (id validity, existence, policy permission, capability support) and compared as a set; the verdict must follow the derivation rule in §4. |
| `recommendation` | `{"kind": "recommendation", "settings": {<interface + plan-expressible fields>}, "rationale": ["profile:<key>", ...]}` | Settings must equal the deterministic profile→settings mapping over the supplied policy; every rationale key must reference a policy fact actually supplied. |
| `review` | `{"kind": "review", "verdict": "accept"\|"reject", "intended_plan": <plan>, "findings": [{"type": "missing_change"\|"extra_change"\|"conflict", "path", "note"}]}` | Candidate and intended plans are both applied; the discrepancy set is recomputed by state comparison (extra = candidate-only change, missing = intended-only change, conflict = both change, values differ) and must equal the findings set. Verdict is accept iff findings are empty. |
| `verification_design` | `{"kind": "verification_design", "required": [{"evidence": "config_readback"\|"traffic_test"\|..., "confirms": "configuration"\|"forwarding"\|...}]}` | Required-evidence set equality. (Later release; shape reserved now so interpret/design stay consistent.) |
| `interpretation` | `{"kind": "interpretation", "aspects": {"configuration": "passed"\|"failed"\|"unknown", "forwarding": ...}, "mismatches": [<paths>], "missing_evidence": [...]}` | Aspects recomputed from readback vs intended state: configuration from config readback (absent → unknown; equal → passed; differing → failed with `mismatches` = the diff), forwarding from traffic tests only (absent → unknown). |

## 3. The matrix

Release markings: **FIRST** = first-release training + evaluation
target. **CONTROL** = evaluated as a control, not trained. **LATER** =
post-first-benchmark broadening, in the roadmap's order (configuration
families first, then diagnostics/remediation). ⚑ = interpretation
flagged for Avinash to confirm (see §6).

| # | Capability | Release | Scenario families (VLAN/interface scope) | Required evidence | Answer format | Pass criterion |
|---|---|---|---|---|---|---|
| 1 | Read running configuration | **FIRST** | Fact extraction with distractors and similar names; alias resolution; CLI-form reading (paired with state form); missing-evidence identification | `state` or `cli_excerpt` | `facts` (+ `unresolved`) | Every scored fact exact; fact set complete for the asked scope; unresolved fields genuinely absent from evidence |
| 2 | Explain current behavior | LATER (wave 2) | Configured-vs-default-vs-operational explanations (e.g. admin-up but link-down) once operational-state evidence joins the record | `state` + operational evidence (future key) | `facts` extension (explanation keys) — shape TBD with the wave | Explanation keys match the evidence-supported account; unsupported claims scored as errors |
| 3 | Identify supported settings | LATER as a standalone task ⚑ | Capability-sheet reading: modes, features, limits per device/port group | `state`, `capabilities` | `facts` / verdict list | Answers differ when the supplied capability sheet differs, identical otherwise. In the FIRST release this is exercised only as a sub-skill inside eligibility (row 4) ⚑ |
| 4 | Check change eligibility | **FIRST** | VLAN id validity (range/reserved); VLAN existence (missing prerequisite); site-policy permission; capability support; trunk-repurpose conflicts | `state`, `proposal`, ± `policy`, ± `capabilities` | `eligibility` | Check set and verdict exactly match the rule-derived answer; the valid/exists/permitted three-way distinction is never conflated |
| 5 | Recommend configuration by purpose | **FIRST** | Profile-driven access provisioning; trunk/uplink profiles; staged profiles (enable_port on/off) | `state`, `policy` (profile facts) | `recommendation` | Settings equal the profile mapping; same request yields different settings iff the profile differs; rationale keys all grounded in supplied policy |
| 6 | Plan and generate edits | **FIRST** | Single-field edits; trunk add/remove/replace contrasts; ordered multi-step; multi-port repeated steps; no-op recognition; remove-from-"all" clarify boundary; composite provisioning (mode+assign+admin) | `state`, ± `policy` | `plan` / `no_op` / `clarify` | Exact intended change (operation scored, not just diff), zero unauthorized changes, preservation incl. admin state and inactive parameters |
| 7 | Generate initial configuration | **CONTROL** ⚑ | Greenfield provisioning of a fresh device with explicit requirements (the released CLI model's home ground) | `state` (near-empty), request | `plan` (multi-step) | Passes the greenfield controls used in the comparison (roadmap step 4); reported separately from stateful results |
| 8 | Review a proposed change | **FIRST** | Replace-that-drops-members; extra interface change; unintended admin-state change; clean accept; (CLI-diff candidates later) | `state`, `candidate`, request | `review` | Finding set (type + path) exactly equals the recomputed discrepancy set; no ungrounded findings |
| 9 | Design verification checks | LATER (wave 2) | Minimum-evidence selection: config readback for assignment, separate traffic check for forwarding | `state`, intended outcome | `verification_design` | Required-evidence set matches; never claims a config check proves a functional outcome |
| 10 | Interpret readback and tests | **FIRST** (basic) | Config matches / forwarding unknown; full pass with traffic evidence; config mismatch; missing readback (narrower conclusion) | `state` (intended), `readback` | `interpretation` | Aspect verdicts exactly follow the evidence: no aspect is "passed" on evidence that cannot establish it |
| 11 | Diagnose and suggest next checks | LATER (wave 3) | Config-vs-operational comparison, ranked causes, discriminating checks | `state`, `readback`, operational evidence | ranked-cause shape TBD with the wave | Supported diagnoses distinguished from hypotheses; next check actually discriminates |
| 12 | Plan remediation or rollback | LATER (wave 3) | Minimal corrective edit from prior state; insufficient-prior-state recognition | `state`, `prior_state`, `readback` | `plan` / insufficiency answer (shape TBD) | Restores only intended fields; declares insufficiency when prior state cannot support reconstruction |
| 13 | Clarify and express uncertainty | **FIRST** (cross-cutting) | Ambiguous alias (ask), unique alias (resolve), no match (name the missing evidence), policy conflict, unsupported remove-from-"all" | any | `clarify` (as the expected kind of any task) | Reason + candidate set exact; direct answers given whenever the input resolves the question (symmetric scoring) |

## 4. Eligibility derivation rule (normative for the representatives)

Checks are computed over the supplied evidence only, in this order:
`vlan_id_valid` (1–4094, not reserved 1002–1005) and `vlan_exists`
for any VLAN-targeting proposal; `policy_permits_vlan` iff policy
supplies `permitted_access_vlans`; `mode_supported` iff the proposal
changes mode and capabilities are supplied. Verdict: all checks pass →
`eligible`; the *only* failure is `vlan_exists` →
`prerequisites_missing` with `missing_prerequisites:
["create_vlan:<id>"]`; any other failure → `blocked` with
`blocking_reasons` drawn from {`vlan_id_reserved`, `vlan_id_out_of_range`,
`policy_vlan_not_permitted`, `mode_not_supported`, …}.

## 5. Coverage notes — variation axes

The roadmap's five variation axes map onto scenario families as
follows (axis tag is recorded on every record):

- **words** (change wording, hold device): add/replace/remove triples
  (already in `contrast_triples.jsonl`), paraphrases that preserve
  meaning ("carry VLAN 20 on the uplink as well" → add), register
  variation (terse ticket vs full sentence). Review examples also use
  it: the *candidate's* wording-independent semantics are what is
  graded.
- **device** (change device/state, hold words): one vs two `uplink`
  aliases (plan vs clarify), already-correct target (no-op), missing
  VLAN (prerequisite), reserved id (blocked).
- **policy** (change capabilities/policy, hold request + config):
  profile swaps that change a recommendation; admin-enable
  authorization present vs absent (plan gains/loses the admin step);
  trunk-repurpose forbidden (clarify) vs unrestricted (plan);
  permitted-VLAN list; capability sheet without trunk mode.
- **evidence** (change evidence, hold intended result): config
  readback establishes configuration but not forwarding; adding a
  traffic test establishes forwarding; removing readback narrows
  every aspect to `unknown`. Paired state/CLI records (same facts,
  both evidence forms — `pair_id` group) let the pilot separate
  config-reading failures from intent-matching failures, per roadmap
  step 3: normalized state first, matched CLI excerpts second.
- **scale** (scale + distractors): multi-port repeated-step plans,
  similarly named interfaces (Gi1/0/47 vs Gi1/0/48 vs
  TenGigabitEthernet1/0/1), irrelevant interfaces/blocks in evidence,
  the 28-step emission case (contract §6c).

Split integrity: records sharing a `group` (contrast groups,
paraphrase pairs, policy pairs, state/CLI pairs) are kept together in
exactly one split. Independently written scenarios and literal
expected changes are reserved for evaluation, per the roadmap.

Same-state, different-operation scoring: two operations can produce
the same final state while the wording specifies one (e.g. an
idempotent add vs a replace that happens to yield the same set). Plan
scoring therefore compares the emitted plan object — operation and
fields — not only the resulting diff.

## 6. Flagged interpretations (need Avinash's confirmation)

1. **Generate initial configuration → CONTROL.** The roadmap's
   first-release list names six trained capabilities and assigns
   greenfield cases to the comparison ("greenfield cases show whether
   the new representation retains the strengths of the released
   model"; step 4 reports greenfield separately). I read that as:
   evaluated as a control against the released CLI checkpoint, *not*
   a first-release training target. ⚑
2. **Identify supported settings → sub-skill first, standalone
   later.** The roadmap lists it as its own capability but the
   first-release six name "checking prerequisites", not it. I read
   eligibility as the first-release home of capability reasoning
   (mode_supported checks), with the standalone task arriving when
   capability evidence gets richer (family expansion). ⚑
3. **Wave assignment for LATER rows.** Explain (wave 2) and design
   verification (wave 2) before diagnose/remediate (wave 3), following
   the roadmap's "then use the same evidence-driven format to extend
   into diagnostics and remediation". The roadmap does not order
   explain vs design-verification; I grouped them as the two
   capabilities that consume readback/operational evidence without
   yet proposing changes. ⚑

## 7. Semantics needing Avinash's judgment

Proposed defaults are what the representative examples currently
encode. Nothing here is silently decided: generation must not scale
any of these until they are ruled on.

1. **Policy evidence shape.** Flat dict of named facts (current
   examples) vs a structured `{profile: {...}, rules: {...}}` object?
   *Proposed default:* flat named-fact dict; a profile is a named
   bundle of facts (`profile` key + sibling parameter keys).
2. **Policy vs explicit instruction.** If the operator explicitly
   requests what policy forbids (e.g. "bring it up" while
   `admin_enable_authorized` is false, or repurposing a protected
   trunk), is the label a plan without the forbidden step, a
   `clarify`, or an eligibility `blocked`? *Proposed default:* never
   silently drop a requested step — answer `clarify` naming the
   policy rule (or `blocked` for "can I" questions). The
   representatives deliberately cover only authorization
   presence/absence, not the head-on conflict.
3. **New clarify reason `policy_conflict`.** The repurpose-forbidden
   representative needs a clarify reason outside the current
   `CLARIFY_REASONS` enum. Add `policy_conflict` to the enum, or
   label policy refusals as eligibility answers instead of clarify?
   *Proposed default:* add `policy_conflict` to `CLARIFY_REASONS`.
   (The checker counts this example separately as using a proposed
   extension.)
4. **`no_op` vs idempotent plan boundary.** When the requested
   end-state already fully holds, is the label the `no_op` kind or
   the (valid, empty-diff) plan? Existing goldens label idempotent
   adds/removes as plans. *Proposed default:* `no_op` when the whole
   requested end-state already holds (stative/"make sure" requests);
   keep idempotent plans for single-op delta requests per the
   goldens — the two conventions must be reconciled before
   generation.
5. **Eligibility verdict vocabulary and the missing-VLAN boundary.**
   Approve {`eligible`, `prerequisites_missing`, `blocked`} and the
   §4 derivation rule? And: for an imperative "do it" request whose
   VLAN is missing, the plan contract already labels create-then-
   assign as a multi-step plan — is eligibility's
   `prerequisites_missing` reserved for "can I" questions?
   *Proposed default:* yes — question form → eligibility;
   imperative form → plan with the create step, unless policy
   forbids creation.
6. **Recommendation granularity.** Settings currently include only
   fields the plan schema can express, so a recommendation compiles
   to a plan unchanged. May a recommendation also set non-plan fields
   (e.g. `description`)? *Proposed default:* no for the first
   release; rationale stays as keys, never prose.
7. **Review finding vocabulary.** {`missing_change`, `extra_change`,
   `conflict`} at leaf-path granularity; a replace that drops trunk
   members surfaces as `conflict` on the `trunk_allowed` leaf with
   the dropped members in the note. Should "drops existing members"
   be its own finding type (e.g. `destructive_change`), given it is
   the highest-risk class? *Proposed default:* keep three types for
   the slice; revisit when QoS/routing families land.
8. **Capabilities representation.** Current shape: `{platform,
   software, supported_modes, features{}}`. Which capability facts
   are in scope for the slice — modes and feature booleans only, or
   also numeric limits (max VLANs, per-port-group restrictions)?
   *Proposed default:* modes + feature booleans for the first
   release.
9. **Interpretation aspect vocabulary.** Aspects are
   {`configuration`, `forwarding`} × {`passed`, `failed`, `unknown`}.
   Does basic readback need a third aspect (e.g. `admin_state` /
   operational state) in the first release? *Proposed default:* two
   aspects; operational state joins with Explain/Diagnose later.
10. **Read-answer completeness.** Facts answers name only what was
    asked; scoring is per-fact exact match plus `unresolved`
    correctness. Should the device/hostname binding fact always be
    included/scored? *Proposed default:* scored only when asked.
11. **Operation-vs-outcome scoring confirmation.** Where two
    operations yield the same final state and the wording specifies
    one, the requested operation is scored (roadmap: "score the
    requested operation separately"). Confirm this applies to
    training labels as well as eval. *Proposed default:* yes, both.
