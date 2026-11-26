# How NetConfig uses OpenConfig and YANG

_Written 2026-11-26 to answer Avinash's question directly: are we using the open-source OpenConfig/YANG models as an interpreter or a generator — and were we already using them? Short answer: so far, as a **vocabulary**, hand-modeled. Not yet as an interpreter, and not as a code generator. This document records exactly what is true today and what the roadmap changes._

## The three ways a project can use YANG models

| Mode | What it means | Example tooling |
|---|---|---|
| **Vocabulary** | The model's field names, types, and structure shape your own data formats, but your code never reads the YANG files. | — (a design discipline) |
| **Interpreter** | Software loads the YANG modules at runtime and validates or manipulates data against them — the models themselves are the schema. | libyang / yanglint, pyang |
| **Generator** | Code is generated from the YANG modules (typed structs, bindings), and you program against the generated types. | OpenConfig's ygot (Go) |

## What NetConfig does today

**Phase 1 (published dataset + model) — vocabulary only.**
Every dataset row carries an `intent_oc` field: the intent expressed as a structured object whose shape and field vocabulary follow OpenConfig's way of describing interfaces, VLANs, and routing — written and maintained by hand in our generators. The actual OpenConfig YANG files are never loaded. Cisco native YANG models are not used at all. Validation is ciscoconfparse2 (CLI syntax), our own intent-match checker (does the config express the intent?), and Batfish (semantic network analysis; Batfish has its own internal models, but that is Batfish's machinery, not ours).

**Phase 2 (plan engine, current) — vocabulary only, one level deeper.**
The device state in `src/plan/state.py` and the plan operations in `docs/PLAN_CONTRACT.md` use OpenConfig-informed concepts (interfaces as keyed objects, VLAN membership as explicit lists, configured vs operational fields kept apart). The schema, `apply()`, and the renderer are hand-written Python. No YANG file is interpreted, and no code is generated from YANG. The contract's correctness rests on hand-authored golden diffs and the independent CLI lab check — agreement with our own written contract, not with a device or a published model (IOS XE behavior remains unverified; see the contract's lab-verification notes).

## What the roadmap changes

Per `docs/ROADMAP.md` ("Use OpenConfig for the common device vocabulary and public Cisco native YANG models and release-specific deviations for platform details. Pin the schema revisions used in an experiment"):

1. **Pin revisions.** Each experiment names the exact OpenConfig release and IOS XE model revision it uses (from openconfig/public and YangModels/yang `vendor/cisco/xe`). No floating "latest."
2. **Map fields to paths.** Each field of the plan state gets an explicit mapping to its OpenConfig YANG path, and — where the platform differs — to the Cisco native path plus the published deviation. This turns "OpenConfig-style" from a claim into a checkable table. The capability matrix's capabilities evidence (what a given device supports) is derived from the native models for the pinned release.
3. **Add an interpreter check (planned).** Validate rendered state against the pinned models with a libyang-style validator as an additional lab rung, alongside parse/intent/Batfish. This is the same role yanglint played in Hollósi et al. (CNSM 2024) — the models acting as an independent schema referee instead of our own code agreeing with itself.
4. **Generator mode stays deferred.** ygot generates Go bindings; the compiler is Python today. If the compiler ever moves to Go, generated bindings become the natural implementation of the state model. Until then, the hand-written state model plus the field-to-path mapping table is the deliberate choice — the mapping keeps the vocabulary honest without a codegen dependency.

## Why vocabulary-first was the right order

The published model's measured failures were enumeration/truncation and hostname slips — representation problems, not schema-knowledge problems. Borrowing OpenConfig's vocabulary fixed the shape of the intent and state formats with zero tooling risk. The interpreter rung earns its place now for a different reason: the supervisor review showed our validators can agree with our own contract while both diverge from a real device — an external, published schema is one of the few referees that cannot share our bugs.
