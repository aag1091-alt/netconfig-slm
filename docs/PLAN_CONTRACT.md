# NetConfig Phase 2 — Plan Contract (VLAN + Trunk Slice)

Status: REVIEW DRAFT — Avinash reviews this contract before any
large-scale plan-label generation. The golden cases in
`data/plan_slice/golden_diffs.jsonl` are the executable form of this
contract: `apply()` and `render()` are graded against hand-authored
expectations, never the source of them.

## 1. The model

A device configuration is a typed tree with references. Given current
state `S` and an operator request `x`, a model predicts a typed change
plan `Δ`; deterministic code computes `S' = apply(S, Δ)` and renders
IOS XE CLI. A candidate is accepted iff three distinct checks pass:

1. **Schema-valid** — `S'` satisfies the plan schema and device-state
   invariants (this contract, enforced by `src/plan/schema.py`
   *before* apply).
2. **Intent achieved** — the diff `S → S'` contains every change the
   request authorized (the operation's primary paths).
3. **No unintended changes** — the diff contains nothing else, except
   the documented `delete_vlan` cascades (§3).

The slice covers one switch's VLANs and interface L2 parameters.
State shape (`src/plan/state.py`):

```json
{
  "device": "sw-acc-1",
  "hostname": "switch1",
  "vlans": {"20": {"name": "Engineering"}},
  "interfaces": {
    "GigabitEthernet1/0/48": {
      "mode": "trunk", "access_vlan": 1,
      "trunk_allowed": [10, 20, 30],   // or "all"; [] means `none`
      "native_vlan": 10, "admin": "up",
      "description": "Uplink to core", "aliases": ["uplink"]
    }
  }
}
```

Mapping to standards: `vlans` ≈ OpenConfig
`openconfig-vlan:vlans` / Cisco native `Cisco-IOS-XE-vlan`; interface
L2 fields ≈ `openconfig-vlan:interface` / `Cisco-IOS-XE-switchport`;
`trunk_allowed` is a leaf-list with the IOS XE special values `all`
and `none` represented as the string `"all"` and the empty list.
`aliases` are inventory metadata (device context), not config — they
are never rendered. The existing dataset's `intent_oc` field is the
static ancestor of this representation; the plan adds *operations on
state*, which `intent_oc` cannot express.

## 2. Operations

All plans are JSON objects: `{"device", "operation", ...fields}`.
A multi-part request is `{"device", "steps": [plan, ...]}`; steps
apply in order and later steps see earlier steps' state. A model may
instead return a **clarify** object (§5). One operation names exactly
one interface: a change spanning N interfaces is a multi-step plan
with one step per interface (**repeated steps** — there is no
multi-interface operation; §4b, §8).

| Operation | Fields (typed) | Preconditions (validated) |
|---|---|---|
| `create_vlan` | `vlan_id: int`, `name?: str` | id in 1–4094, not reserved 1002–1005 |
| `delete_vlan` | `vlan_id: int` | id in range (existence not required — see §3) |
| `set_access_vlan` | `interface: str`, `vlan_id: int` | interface in inventory; **VLAN exists in S** |
| `add_trunk_vlans` | `interface: str`, `vlans: [int]+` | interface in inventory; ids valid, unique |
| `remove_trunk_vlans` | `interface: str`, `vlans: [int]+` | same; plus apply-time rule §3 |
| `replace_trunk_vlans` | `interface: str`, `vlans: [int]+` | same |
| `set_native_vlan` | `interface: str`, `vlan_id: int` | interface in inventory; id valid |
| `set_port_mode` | `interface: str`, `mode: "access"\|"trunk"` | interface in inventory |
| `set_admin_state` | `interface: str`, `admin: "up"\|"down"` | interface in inventory |

## 3. State-transition semantics (normative)

- **add_trunk_vlans** = set union on `trunk_allowed`. Existing members
  are preserved — this is IOS `allowed vlan add`. On a trunk whose
  allowed set is `"all"` it is a **no-op** (every VLAN is already
  allowed). It never changes `native_vlan`, `mode`, or `admin`.
- **remove_trunk_vlans** = set difference (IOS `allowed vlan remove`).
  Removing a VLAN that is not in an explicit list is an **idempotent
  no-op**, not an error. Removing from `"all"` is a **PlanError**: the
  honest rendering would expand ~4,094 ids, so the compiler refuses
  and the operator must state the intended list via
  `replace_trunk_vlans`. A plan model must learn this refusal boundary.
  In **training data**, however, this situation is not labeled with
  the rejected plan: the model's expected output is a **clarify**
  with reason `unsupported_remove_from_all` (§5) asking the operator
  for the intended allow-list, with zero state change. The rejected
  plan remains an engine-level refusal, covered by engine tests
  (`gold-remove-from-all-errors`, `scripts/test_entry_points.py`) —
  not a training label.
- **replace_trunk_vlans** = the allowed set becomes exactly the given
  list (bare IOS `allowed vlan <list>`, "allow only"). Unlisted
  existing VLANs are dropped. The schema requires a non-empty list;
  emptying a trunk is expressed in state as `[]` (`allowed vlan none`)
  and is reachable today only via remove/delete cascades — a
  deliberate slice restriction, revisit if operators need it.
- **set_access_vlan** changes only `access_vlan`. It does **not**
  switch the port's mode (a trunk stays a trunk; the value takes
  effect if mode later changes — IOS stores the parameter the same
  way) and never touches `admin`. The VLAN must already exist:
  the compiler does not invent state (contrast IOS, which silently
  creates the VLAN — silent invention is exactly the failure class
  this architecture exists to remove).
- **set_port_mode** changes only `mode`. `access_vlan`,
  `trunk_allowed`, and `native_vlan` values are preserved, as IOS
  preserves them across mode toggles.
- **set_native_vlan** changes only `native_vlan`. It does not add the
  VLAN to the allowed set (IOS doesn't either — a native VLAN not in
  the allowed list is a real, if unusual, configuration).
- **create_vlan** creates the VLAN, or renames it if it exists with a
  different `name`; an identical create is a no-op.
- **delete_vlan** removes the VLAN and cascades, mirroring what IOS XE
  leaves behind: access ports assigned to it get `access_vlan = null`
  (inactive), and the id is stripped from explicit trunk allowed
  lists. `"all"` trunks are untouched (the id simply no longer
  exists). `native_vlan` pointers are **not** rewritten — IOS leaves
  the configured native VLAN pointing at the deleted id. Deleting a
  nonexistent VLAN is a no-op (IOS accepts `no vlan <id>` silently).
  Permitted cascade paths are derived from the operation and the
  before-state by `src/plan/cascades.py`, **independently of
  `apply()`'s output** — scoring asserts a diff contains nothing
  outside primary paths ∪ that derived set, so a cascade (or a bug)
  can never authorize itself. **PENDING LAB VERIFICATION
  (2026-11-26 ruling, §8):** these cascade semantics encode what we
  believe IOS XE leaves behind, but no real XE readback yet
  distinguishes configured access assignment / trunk membership from
  operational inactivity — and the CLI mini-interpreter encodes the
  same assumptions, so its agreement proves nothing about device
  behavior. Until readback is available, `delete_vlan` cascade
  labels are **excluded from the first generated batch**; the golden
  case remains an engine test flagged `"lab_verification":
  "pending"`.
- **set_admin_state** changes only `admin`. No other operation touches
  `admin` — "leave its admin state alone" is the default for every
  other op, and training/eval cases grade it explicitly.

Per-op invariant (checked in goldens): the diff of any accepted plan
is a subset of the op's primary paths ∪ documented cascades.

## 4. Two defect classes this design removes structurally

**(a) Hostname slips.** In the v1.0 CLI dataset, the hostname is part
of the generated text, and run 3 produced 6 wrong-hostname configs —
the model had to *retype* a fact it was given. Here the device binding
lives in context, outside generation: a plan carries `device` as a
binding assertion (validated to equal the context device), and **no
operation can rename the device** — there is no `set_hostname` op and
`apply()` rejects any plan whose `device` disagrees with S. The
renderer takes the hostname from state. A plan model *cannot* emit a
wrong hostname because it never emits a hostname at all.

**(b) List truncation.** Run 3's dominant vlan_l2 failure was
enumeration: generations stopped mid-list at the token cap (see the
cause-tagged breakdown: 11 of 12 real vlan_l2 failures are
truncations; the ports it did emit were correctly assigned). A plan
names each port/VLAN **once**; the deterministic renderer expands
them into CLI blocks. Output length grows with the *change*, not with
the size of the device. A 28-port assignment is a 28-step plan — one
step per interface, each step naming its port exactly once (repeated
steps are the multi-port representation, §2) — and a missing or extra
step changes the resulting state, which exact-membership scoring
catches (golden `gold-multistep-28-ports` asserts the changed
interface set exactly, with per-port preservation of unrelated
fields). Completeness is checkable exactly (§6b), which free-text
CLI never allowed.

## 5. Clarify outputs

```json
{"type": "clarify", "reason": "ambiguous_alias",
 "question": "Two interfaces match 'uplink': GigabitEthernet1/0/47 and GigabitEthernet1/0/48. Which one?",
 "candidates": ["GigabitEthernet1/0/47", "GigabitEthernet1/0/48"]}
```

Reasons: `ambiguous_alias`, `unknown_interface`, `unknown_vlan`,
`missing_vlan_id`, `missing_interface`, `unsupported_remove_from_all`.
A clarify output changes no state. Scoring is symmetric (§6c):
guessing on an ambiguous request is an error, and clarifying a
resolvable request is an error. `unsupported_remove_from_all` is the
training-facing answer to a remove-from-"all" request (§3): instead
of emitting a plan the compiler must refuse, the model asks for the
intended allow-list.

## 6. Rules for generation, validation, and comparison

**(a) Binding.** The caller supplies the target device in context;
plans bind to it. Alias resolution ("the uplink") happens against the
context inventory: exactly one match → that interface; zero → clarify
`unknown_interface`; more than one → clarify `ambiguous_alias` with
candidates. The plan itself always names the canonical interface.

**(b) Completeness before apply.** Validation runs *before* `apply()`
and is all-or-nothing: plan JSON must parse, every required typed
field must be present and in range, the interface must exist, and
VLAN lists must be exact and complete. A plan that omits a port or
truncates a list fails validation or fails exact-membership scoring —
it is **never partially applied**. `apply()` re-validates and raises
`PlanError` rather than guessing. Enforcement lives at the public
entry points, not in callers' discipline (2026-11-26 fix):
`apply_plan()` and `render_delta()` both run the full validation
first — device binding on the outer plan **and** every step, empty
`steps` rejected, step *i* validated against the state produced by
steps 0..*i*−1 — and validation is total (malformed values return
errors, never raise). An invalid plan produces **neither** a
candidate state **nor** any CLI; `render_delta()` additionally
dry-runs `apply_plan()` so apply-time refusals (remove-from-"all")
also yield no CLI. `render_config()` gates on state sanity
(`validate_state`). `scripts/test_entry_points.py` exercises all of
this through the public entry points.

**(c) Comparison freeze (prompted vs fine-tuned).** Any comparison of
plan emitters must hold constant: the same base checkpoint
(`Qwen/Qwen3-4B-Base`), the same context format (this contract's state
JSON), the same decoding constraints (grammar-constrained to the plan
schema) and the same token budget. Report **greenfield** (empty-ish
starting state) and **stateful** (operations on populated state)
results separately — they exercise different skills. Score state
change exactly: `must_change ⊆ diff` and `diff ⊆ authorized`
(exact-membership for port/VLAN lists — a diff that adds the right
VLAN to 27 of 28 ports scores as a miss on that case, with the
shortfall counted). Clarification is scored both ways per §5, and on
content: the **reason must match** and the **candidate set must equal
the inventory-derived candidate set exactly** (no missing or extra
candidates). Also record output tokens and latency per emitter; the
plan representation is expected to win on tokens, and the claim must
be measured, not asserted.

**Emission completeness (model-side).** The goldens prove the
*engine* handles a complete plan exactly; whether a *model* can emit
a complete plan is a separate question, tested separately. The
benchmark therefore includes the 28-step long-list case (§4b) as an
emission test under the same frozen token budget: a plan that stops
early fails completeness-before-apply (§6b) and scores as a miss,
with the emitted step count reported. For that case the eval must
report **plan-token vs CLI-token counts** side by side — repeated
steps repeat the operation/interface fields, so the plan format's
token advantage over CLI text is quantified on the hardest case, not
assumed from short ones.

## 7. Verification assets in this review package

- `data/plan_slice/golden_diffs.jsonl` — hand-authored before/after
  literals per operation (15 cases, incl. the 28-step long-list and
  mode round-trip cases); `scripts/test_golden_diffs.py` grades
  `apply()` against them. Cascade authorization in scoring is
  derived by `src/plan/cascades.py` from the operation and
  before-state only — never from `apply()`'s output.
- `scripts/test_entry_points.py` — rejection tests through the
  public entry points (`apply_plan`, `render_delta`,
  `render_config`): wrong device bindings (outer and per-step),
  empty/malformed `steps`, a standalone valid-step-then-invalid-step
  regression, remove-from-"all" via apply and render, and a
  malformed-input fuzz set asserting errors-not-exceptions.
- `data/plan_slice/contrast_triples.jsonl` — same-state add / replace
  / remove triples, an admin-state pair, and an ambiguous-alias
  clarify case, all hand-authored.
- `scripts/cli_lab_check.py` — an independent mini-interpreter for
  the rendered delta-CLI subset; it recomputes the state change from
  `render()` output and checks it against the golden diffs (a second,
  separate code path — the renderer is graded, not trusted). Batfish
  on the rendered full config is the planned third check; it needs
  the Mac-hosted Batfish server and is a later addition.

## 8. Supervisor rulings (2026-11-26)

Recorded from Avinash's review of the public repo at commit
`f1b5189`. Bulk label generation remains HELD until the fixes these
rulings required are in and verified.

- **Trunk add / remove / replace — APPROVED.** Union, difference,
  and exact replacement semantics as specified in §3; explicit no-op
  cases (add of a present member, remove of an absent member, add to
  "all") are kept as labeled cases. Unsupported removal from "all"
  is represented in training as clarification
  (`unsupported_remove_from_all`), not as a rejected plan.
- **Mode, access/native VLAN, and admin state — APPROVED.** Each
  operation changes only the requested configured field; inactive
  parameters survive mode toggles (see the mode round-trip golden,
  `gold-mode-round-trip`).
- **VLAN deletion cascades — HELD.** Cascade labels wait for a real
  IOS XE readback distinguishing configured access assignment and
  trunk membership from operational inactivity (§3). If readback is
  still unavailable when generation starts, `delete_vlan` is left
  out of the first generated batch and recorded as pending lab
  verification.
- **Device binding and clarification — APPROVED.** Caller-bound
  identity; plans name canonical interfaces; clarification on
  ambiguity. Binding is enforced on the outer plan and on every step
  (§6b). Clarification is scored on reason AND candidate-set
  membership (§6c).
- **Comparison arms — APPROVED (unchanged).** The YANG-lookup
  variant ("adapted to stateful edits") and the classifier +
  slot-extraction arm remain part of the frozen comparison (§6c).
