# NetConfig SLM roadmap

_Avinash's roadmap, delivered 2026-11-26. This is the governing plan for the project; earlier slice-level plans are subordinate to it._

## North star

Build a small model that understands network devices from the evidence they expose and can help an operator decide what to do. Start with Cisco IOS XE. The long-term model should understand running configuration and operational state, recommend and generate changes, design basic checks, interpret results, and help diagnose problems.

**Configuration is the first milestone. Access-port provisioning is one example of that milestone.** Build a capability map and use it to drive the dataset and evaluation. The deliverable is the trained model and evidence of what it can do. Saved snapshots, public schemas, deterministic validators, and lab captures provide the inputs and ground truth. On-switch inference is a deployment target to measure once a checkpoint earns it.

## Capability map

| Capability | What the model should be able to do | What proves it |
|---|---|---|
| **Read running configuration** | Identify devices, interfaces, VLANs, modes, and relevant settings from normalized state or IOS XE CLI. Resolve references and identify missing or unsupported evidence. | Extract the right facts from an unfamiliar config with similar interface names and irrelevant blocks. |
| **Explain current behavior** | Tell an operator what the configuration sets up. Distinguish configured state, defaults, and observed operational state. | Correctly explain why an administratively enabled interface can still have a down link, using the evidence supplied. |
| **Identify supported settings** | Determine which fields, values, and combinations are supported by the supplied device capabilities and schema. | Give different answers for otherwise similar devices when an advertised capability differs. |
| **Check change eligibility** | Identify prerequisites, conflicts, and site-policy limits for a proposed edit. | Distinguish a valid VLAN ID, an existing VLAN, and a VLAN the site permits on the target port. |
| **Recommend configuration by purpose** | Match an operator's goal to a supplied profile, propose suitable settings, and explain important choices. | Recommend different settings for the same request when the site profile changes. |
| **Plan and generate edits** | Produce a typed change plan with exact targets and values. Handle add, remove, replace, no-op, and ordered multi-step changes. | Match hand-authored expected changes and preserve unrelated state, including on long and multi-port requests. |
| **Generate initial configuration** | Produce a coherent config for a supported task on a fresh device with explicit requirements. | Pass the greenfield controls used to compare against the released CLI model. |
| **Review a proposed change** | Inspect a candidate plan or CLI diff for missing changes, extra changes, and likely conflicts. Explain which findings are grounded in the evidence. | Catch a replacement that drops existing trunk VLANs, an extra interface change, or an unintended admin-state change. |
| **Design verification checks** | Identify the minimum evidence needed to confirm a configuration outcome and, where relevant, a functional outcome. | Ask for config readback to confirm assignment and a separate traffic check to confirm forwarding. |
| **Interpret readback and tests** | Compare observed results with the intended outcome and report what passed, what failed, and what remains unknown. | Report that configuration matches while forwarding remains unverified when only readback is supplied. |
| **Diagnose and suggest next checks** | Compare config and operational evidence, rank plausible causes, and identify a discriminating check. | Distinguish a supported diagnosis from a hypothesis when multiple causes fit the evidence. |
| **Plan remediation or rollback** | Propose a minimal corrective edit or restoration using the supplied prior state and current evidence. | Restore only the intended fields and identify when the prior state is insufficient to reconstruct a rollback. |
| **Clarify and express uncertainty** | Ask a specific question when target, scope, intent, or evidence is ambiguous. Give a direct answer when the input resolves it. | Resolve a unique `uplink` alias, ask when two match, and identify the missing evidence when none match. Apply this across all capabilities. |

## Build the first release around configuration

Train and evaluate the first checkpoint on **reading config, checking prerequisites, recommending settings, planning edits, reviewing changes, and interpreting basic readback**. Keep those tasks within the VLAN and interface scope we can label confidently. Use several scenarios to exercise them, including access-port assignment, trunk membership, native VLAN and port mode, and explicit admin-state changes. Access-port provisioning ties several abilities together. Trunk edits test precise wording and preservation. Greenfield cases show whether the new representation retains the strengths of the released model.

Use **OpenConfig for the common device vocabulary** and **public Cisco native YANG models and release-specific deviations for platform details**. Pin the schema revisions used in an experiment. Keep the compact Phase 2 plan as the edit contract. The running config supplies the current state, the schema supplies supported settings, and the operator request plus site policy supplies the desired outcome and scope. Train the model to use all four.

Broaden configuration coverage after the first benchmark works. Add families such as interface protections, traffic limits and QoS, management settings, and routing as their operation contracts and verification evidence become available. Then use the same evidence-driven format to extend into diagnostics and remediation.

## Build the dataset as a matching problem

Each example asks the model to match **the operator's words and supplied device evidence to the right answer**. Use a common record with a task, request, evidence, and expected answer. An edit expects a typed plan, no-op, or clarification. An interpretation task expects facts grounded in the input. A verification task expects a verdict tied to the readback or test evidence.

Build each scenario family around a decision and vary one thing at a time:

- **Change the words, hold the device fixed.** "Add VLAN 20," "allow only VLAN 20," and "remove VLAN 20" should produce distinct operations. Also include different phrasings that preserve the same meaning.
- **Change the device, hold the words fixed.** One matching uplink yields an edit. Two matching uplinks yield a specific question. An already-correct port yields a no-op. A missing VLAN yields a missing prerequisite.
- **Change the capabilities or policy, hold the request and config fixed.** The right answer may change when a setting is unsupported, when the requested profile forbids repurposing a trunk, or when policy explicitly authorizes enabling the port.
- **Change the evidence, hold the intended result fixed.** A config readback can establish the requested settings. A traffic test can establish a functional outcome. Missing evidence should produce a narrower conclusion.
- **Vary scale and distractors.** Include multi-port requests, similarly named ports, unrelated configuration, and ordered edits. Grade exact target membership and preservation.

Hand-author representative cases before scaling. Generate variations from the expected decision outward. Keep contrast groups, paraphrases, and close template variants together in the split. Reserve independently written scenarios and literal expected changes for evaluation. Include cases where two operations happen to produce the same final state, and score the requested operation separately when the wording specifies it.

## Build and evaluate in this order

1. **Create the capability and coverage matrix.** For each first-release capability, identify the scenario families, required inputs, expected answer format, and pass criterion. Write a few representative labeled cases for each. Flag any operator semantics that need Avinash's judgment.
2. **Close the existing Phase 2 review.** Submit the fix commit with reproduce-before and pass-after evidence for public validation, apply, and render. Verify independent preservation checks, the late-invalid-step regression, and the 28-port golden. Keep `delete_vlan` out of generated labels until XE readback settles its behavior. **Bulk generation stays held until this review clears.**
3. **Freeze an independent benchmark and run a small clean pilot.** Start with normalized state, then add matched IOS XE CLI excerpts. Use the paired cases to distinguish configuration-reading failures from intent-matching failures. Inspect the pilot labels and baseline errors before scaling the corpus.
4. **Run the agreed comparison.** Compare the released CLI checkpoint, prompted typed plans, prompted plans with YANG lookup, fine-tuned typed plans, and classifier plus slot extraction. Report greenfield and stateful results separately. Keep comparable context and decoding settings across the plan arms. Score exact intended change, unauthorized changes, no-ops, clarification in both directions, and evidence-grounded readback. Track output tokens and latency.
5. **Use the results to pick the next investment.** If the model misreads CLI, improve the configuration-reading examples. If it chooses the wrong operation, add sharper contrast cases. If it guesses policy, add cases where policy changes the answer. If fine-tuning earns its place against the baselines, test 1.7B, then fewer rows. Measure memory, latency, and control-plane impact on a specific switch and release when a smaller checkpoint is ready.

**First handoff from Muse:** deliver the comprehensive capability matrix with first-release and later coverage marked, representative examples across the configuration capabilities, and the fix commit with regression evidence. The first release succeeds when an independently written test can show that the model understood an unfamiliar IOS XE snapshot, chose the right action or question, and accounted for exactly what changed.
