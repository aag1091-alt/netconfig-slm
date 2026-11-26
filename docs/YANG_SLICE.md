# The YANG slice: field mapping, instance format, findings, and the training-order experiment

Status: foundation slice, hand-authored representatives only (bulk
generation remains HELD). Pin record: `docs/YANG_PIN.md` — OpenConfig
**v5.11.0** (`160da73440205de252066233b1ee0f3085d51d2d`) and Cisco
native IOS XE **17.18.1** (YangModels/yang `e13b05c`, folder `17181`),
platform target Catalyst 9300. Artifacts: `vendor/yang/`,
`data/yang_slice/instances.jsonl`,
`data/yang_slice/paired_goldens.jsonl`,
`scripts/check_yang_instances.py`, `scripts/check_paired_goldens.py`.

## 1. Two formats, never conflated (normative)

This project now has two distinct machine formats, and data must
never mix them:

- **The instance** describes *what is configured*. It is YANG-modeled
  data: a JSON encoding (RFC 7951 style, §3) of the pinned OpenConfig
  slice. It contains no intentions, no verbs, no questions. If a
  record could be sent to a device datastore as configuration, it is
  an instance.
- **The plan** describes *what to do about it*. It is the existing
  contract format (`docs/PLAN_CONTRACT.md`): typed operations and
  clarifications over our plan state (`src/plan/`). A plan is an
  answer to a request, never a state.

The plan state in `src/plan/state.py` remains the engine's working
state. The instance is a *view* of that state — specifically its
active-mode projection (§4, finding F1) — produced for training and
evaluation in the YANG-first direction. The expected answer to a
request is a plan (or no_op / clarify / facts), identical whichever
view the state was shown in; that identity is what the paired goldens
(`data/yang_slice/paired_goldens.jsonl`) assert mechanically.

## 2. Field mapping

Paths are XPath-style. OpenConfig prefixes: `oc-if` =
openconfig-interfaces, `oc-eth` = openconfig-if-ethernet, `oc-vlan` =
openconfig-vlan. Cisco native: `Cisco-IOS-XE-interfaces.yang` is a
*submodule* of `Cisco-IOS-XE-native.yang` sharing the `ios` prefix;
switchport config lives in its `interface-switchport-grouping`
(line 1435), instantiated under each interface list beneath
`/ios:native/ios:interface`.

| Our field (`src/plan` state) | OpenConfig path (v5.11.0) | Cisco native XE 17.18.1 path | Notes / deviations |
|---|---|---|---|
| `device` (binding) | — none — | — none — | Device identity is session/context in both models (a datastore belongs to a device; no leaf names it). Stays in our context layer (contract §4a). |
| `hostname` | `/oc-sys:system/oc-sys:config/oc-sys:hostname` (openconfig-system — **not vendored**, import closure too broad for this slice; path verified in the pinned release file) | `/ios:native/ios:hostname` (leaf in Cisco-IOS-XE-native) | Real device state in both models, but **context in our slice** by deliberate scoping: the model never changes it (binding, contract §4a). Finding F6. |
| `vlans` — existence + `name` | `/oc-vlan:vlans/oc-vlan:vlan[vlan-id]/oc-vlan:config/{vlan-id, name}` (`vlan-id`: `oc-vlan-types:vlan-id`, uint16 **1..4094**) | `Cisco-IOS-XE-vlan` augments `/ios:native/ios:vlan` with `list configuration-entry`, key `vlan-id` a **string with a range pattern** (`"1-10,15"`), `name` leaf inside the entry | Native's key is a creation idiom (a pattern that can name many VLANs), not a state identity — finding F4. OC also has `config/status` (ACTIVE/SUSPENDED, default ACTIVE) with **no counterpart in our state** — finding F7. |
| `interfaces.<name>` (identity) | `/oc-if:interfaces/oc-if:interface[name]/oc-if:config/oc-if:name` | `/ios:native/ios:interface/ios:GigabitEthernet[name]` (one list per interface type, in the interfaces submodule) | OC has one generic list; native has per-type lists. |
| `mode` | `.../oc-eth:ethernet/oc-vlan:switched-vlan/oc-vlan:config/oc-vlan:interface-mode` (type `oc-vlan-types:vlan-mode-type`, enum **ACCESS / TRUNK**) | `.../ios:switchport-wrapper/ios:switchport/ios:mode` (enum `access` / `trunk`) | Same distinction, different case convention. |
| `access_vlan` | `.../switched-vlan/config/access-vlan` (vlan-id) — **`when "../interface-mode = 'ACCESS'"`** | `.../switchport/ios:access/ios:vlan` (uint16 1..4094) | The OC `when` makes the leaf *inexpressible* on a trunk port — finding F1. Native has no `when` here. |
| `trunk_allowed` — explicit list | `.../switched-vlan/config/trunk-vlans` (leaf-list, union of `vlan-id` \| `vlan-range` string `"x..y"`) — **`when "../interface-mode = 'TRUNK'"`** | `.../switchport/ios:trunk/ios:allowed/ios:vlan` → `choice vlan-choice`, case `vlans`: leaf-list of union uint16 \| **string range-expression** (`"1-20,30"` syntax) | Range syntax differs: OC `"10..20"`, native `"10-20,30"`. Native's node is operation-shaped — finding F3. |
| `trunk_allowed` — `"all"` | **No explicit encoding.** The module's description: if `trunk-vlans` is not specified, all VLANs are allowed. Encode `"all"` by **omitting** the leaf-list. | `choice vlan-choice`, case `all` (leaf of type `empty`) — native *can* say it | Asymmetry — finding F2. Absence-as-meaning is something a model reading JSON must learn. |
| `trunk_allowed` — `[]` ("none") | **Not representable.** An empty leaf-list is indistinguishable from an absent one (= all). Our instance encoding writes `[]` literally as a documented deviation; a strict OC consumer would read it as "all". Instances are training/eval artifacts, never device-bound payloads. | `choice vlan-choice`, case `none` (type `empty`) — representable | Asymmetry — finding F2. The dangerous direction: never generate a device-bound OC payload from this encoding without resolving F2. |
| trunk add/remove (plan ops, not state) | — (state-shaped model: the *result* is a new `trunk-vlans` value) | `choice vlan-choice` cases `add` / `remove` / `except` are delta-shaped leaves inside the state tree | Native models the *operation* where OC models the *state*. Our plan ops map almost 1:1 onto native's add/remove cases; replace maps to the `vlans` case. Finding F3. |
| `native_vlan` | `.../switched-vlan/config/native-vlan` (vlan-id) — **`when "../interface-mode = 'TRUNK'"`** | `.../switchport/ios:native/ios:vlan` (uint16 1..4094) | Same `when` consequence as `access_vlan` (F1). |
| `admin` (`"up"`/`"down"`) | `/oc-if:interfaces/oc-if:interface[name]/oc-if:config/oc-if:enabled` (boolean; up = `true`; default `true` when absent) | `ios:shutdown` leaf, type `empty`, in `interface-common-grouping` — **presence means down** | Opposite polarity encodings: a boolean vs a presence flag. The pair-admin-bring-up golden exists precisely to pin this. |
| `description` | `/oc-if:interfaces/oc-if:interface[name]/oc-if:config/oc-if:description` (string) | `ios:description` leaf in `interface-common-grouping` | Direct counterpart in both. |
| `aliases` | — **none** — | — **none** — | **No YANG counterpart in either model.** Aliases are our context layer: operator inventory metadata (contract §4a), carried identically in the `context` field of both views of every paired record. Running-config has no alias concept either (description is a different thing and is mapped above). |
| stored inactive parameters (`access_vlan` on a trunk port; `trunk_allowed` / `native_vlan` on an access port) | **Not representable in a valid instance** — the `when` conditions forbid the leaves in the other mode | Representable (no `when`; real XE running-config also retains inactive switchport lines) — but our `render_config()` does not emit them either | Our state stores these deliberately (mode round-trip semantics, contract §8). Instances and CLI excerpts both carry the **active-mode projection** only. Finding F1 — the headline finding of this slice. |
| *(instance-only constant)* `type` | `/oc-if:interfaces/oc-if:interface[name]/oc-if:config/oc-if:type` = `"iana-if-type:ethernetCsmacd"` | (implied by which per-type list the interface sits in) | Not a field of our state; written as a constant in instances because the OC grouping carries it. |

## 3. The JSON instance format

RFC 7951-style JSON encoding of the pinned slice, with these
documented pragmatic choices:

- Top level has exactly two keys: `"openconfig-interfaces:interfaces"`
  and `"openconfig-vlan:vlans"` (module-prefixed, per RFC 7951).
  `openconfig-vlan:switched-vlan` appears under
  `"openconfig-if-ethernet:ethernet"` inside each interface entry,
  following the augment path.
- Only `config` containers are serialized. Operational `state`
  containers are out of scope: an instance says what is *configured*.
- Identityrefs are prefixed strings (`"iana-if-type:ethernetCsmacd"`);
  leaf-lists are JSON arrays; `vlan-range` members are `"x..y"`
  strings inside `trunk-vlans`.
- Device, hostname and aliases are **not** in the instance. They are
  the record's `context` (see the paired goldens), identical for both
  views.
- `"all"` trunk membership is encoded by omitting `trunk-vlans`;
  `[]` ("none") is written literally with the F2 caveat.
- An instance is derived from a plan state by the active-mode
  projection: for each interface, only the parameters of its current
  mode are emitted.

`data/yang_slice/instances.jsonl` holds 11 labeled instances
(4 valid, 7 invalid). Each record: `{id, verdict, violated, instance,
note}`; invalid records name the exact constraint from the pinned
modules (typedef range, `when` condition, boolean type, enum
membership, unknown node).

## 4. What validation was actually performed

Stated exactly, per the project's evidence rules:

- **Module validation (authoritative):** `pyang 2.7.1` (installed
  into the project `.venv` for this slice) loads the full vendored
  OpenConfig set — including the three Cisco XE deviation modules for
  the OpenConfig models — with **zero errors** (exit 0). This proves
  the pinned modules are well-formed and their imports resolve from
  `vendor/yang/openconfig/`.
- **Instance validation (derived, NOT authoritative):** libyang /
  yanglint is **not available** in this environment — the pip
  `libyang` package fails to build (no C compiler toolchain on the
  VM) and no `yanglint` binary is installed. JSON instances were
  therefore checked by `scripts/check_yang_instances.py`, a
  hand-written encoding of the mapped constraints from the pinned
  modules (typedef ranges, the `vlan-mode-type` enumeration, the
  three `when` conditions, leaf types, unknown-node detection
  against the slice's node registry). It reproduces the labeled
  verdict on **11/11** instances, naming the violated constraint for
  each invalid one. When yanglint becomes available it should be run
  over the same file; any disagreement is a bug in the derived
  checker, not in the labels.
- **Paired-golden equivalence:** `scripts/check_paired_goldens.py`
  verifies **9/9** records: context consistency (aliases equal in
  both views' context), `cli_state == render_config(plan_state)`
  exactly, fact equality across all three views (plan projection =
  YANG-derived facts = CLI-parsed facts, plus per-`depends_on`-path
  agreement), an independent ciscoconfparse2 parse of every CLI
  excerpt, and every expected answer through the real engine
  (`validate_output` / `apply_plan` / `cascades.authorized_paths` /
  `validate_clarify` / alias resolution).

## 5. Design findings — where the pinned models pushed back

These are the places the real schemas contradicted or constrained
our current state model. They are design inputs, not inconveniences.

- **F1 — OpenConfig cannot store inactive-mode parameters.**
  `access-vlan` exists only `when` the mode is ACCESS;
  `native-vlan` and `trunk-vlans` only `when` TRUNK. Our plan state
  deliberately stores parameters for both modes (a port keeps its
  access VLAN while it is a trunk, and mode round-trips restore it —
  supervisor-approved semantics). A valid OC instance therefore
  carries only the **active-mode projection** of our state. Two
  consequences. (a) For pairing, this is convenient: our CLI renderer
  also emits only active-mode parameters, so the JSON and CLI views
  agree exactly. (b) For evaluation, it is a real limit: *neither*
  view can support a question about an inactive parameter ("what
  VLAN will this port land on if it goes back to access?"). Such
  questions must be asked against the plan-state view only, or
  excluded from JSON/CLI-view scoring — otherwise we would grade the
  format for information it cannot hold. The plan state remains the
  full record; the instance is a projection, and the projection rule
  is now written down (§3) instead of being implicit.
- **F2 — "all" and "none" are asymmetric in OpenConfig.** OC encodes
  "all" only by omission and cannot encode "none" at all; Cisco
  native encodes both explicitly (`all` / `none` empty leaves in the
  `vlan-choice`). Our `"all"` → omission encoding means absence
  carries meaning in the JSON view — a model must learn to read a
  *missing* leaf-list as "all", and our literal `[]` for "none" is a
  deviation that a strict consumer would misread as "all". Safe
  while instances are training/eval artifacts; a blocker to resolve
  before any device-bound OC payload is ever produced.
- **F3 — Cisco native's allowed-VLAN node is operation-shaped.**
  The `vlan-choice` (vlans / add / remove / except / all / none)
  embeds deltas in the state tree, where OC's `trunk-vlans` is pure
  state. If a native-YANG view is ever added, our add/remove plan
  ops map onto it almost directly — and the contrast between the two
  modeling styles is itself a useful training signal about what
  "state" means.
- **F4 — Native VLAN creation is keyed by a range pattern.** The
  `configuration-entry` list key is a string like `"1-10,15"`: one
  native "entry" can create many VLANs. Our model creates one VLAN
  per operation and treats the VLAN set as state. The native form is
  a provisioning idiom, not a state identity; a native view would
  need a normalization rule before it could be paired with ours.
- **F5 — Correction to PLAN_CONTRACT.md §1.** The contract's
  shorthand "≈ Cisco-IOS-XE-switchport" names a module that does not
  exist as a configuration module in 17.18.1 (only
  `Cisco-IOS-XE-switchport-oper.yang` and a deviation file do).
  Switchport configuration lives in `interface-switchport-grouping`
  inside `Cisco-IOS-XE-interfaces.yang`. The pin record
  (`docs/YANG_PIN.md`) supersedes the shorthand.
- **F6 — Hostname is YANG state but our context.** Both models carry
  hostname as ordinary configuration. We keep it in the context
  layer for this slice because the contract binds answers to a
  device and never edits hostname. That is a scoping choice to
  revisit if hostname edits ever enter scope — not a gap in the
  models.
- **F7 — OC VLAN `status` has no counterpart in our state.**
  `vlans/vlan/config/status` (ACTIVE / SUSPENDED, default ACTIVE) is
  a real configured field we do not model. Instances omit it (the
  default applies). A future slice can add it; today, a request
  about suspending a VLAN is out of scope and should clarify as
  unsupported rather than be silently mapped to deletion.

## 6. The training-order experiment (designed, not run)

Step 5 of the YANG-first direction: does *staging* — learning the
YANG-modeled form before the CLI form — produce a better reader of
unfamiliar device text than mixing the forms from the start? This
experiment belongs to the comparison phase (contract §6 freeze rules
apply); nothing here has been run, and bulk generation remains held
until the supervisor clears it.

**Arms** (same Qwen3-4B-Base checkpoint, same scenarios, ~same total
training-token budget):

- **Mixed:** every scenario appears in both views (JSON instance and
  CLI excerpt), interleaved throughout training.
- **YANG-first:** stage 1 trains on the JSON view only; stage 2
  trains on the mixed set. Total tokens matched to Mixed — because a
  JSON view costs more tokens per scenario than a CLI excerpt,
  budgets are equalized by *token count*, not example count, and the
  per-view token counts are reported with the results.

**Evaluation:** independently written scenarios (not paraphrases of
training rows), each presented in **both** input formats, scored
with the contract scorers — plan correctness, preservation
(authorized-paths diff), clarification scored both ways (asking when
unnecessary counts against; failing to ask counts against), and the
emission-completeness rule for long plans.

**The diagnostic reading** (the point of running both formats):

- **JSON-right / CLI-wrong** on the same scenario = a *reading*
  problem: the decision was available to the model in one encoding
  and not extracted from the other. More CLI-view reading data (or
  excerpt normalization) is the fix, not more decision data.
- **Both-wrong** = a *decision* problem: the model read the state
  and still chose the wrong operation. More contrast data on the
  decision itself is the fix.
- **YANG-first beats Mixed on unfamiliar CLI scenarios** = the
  staging itself transferred: learning the typed state first made
  the surface form easier to read. That is the claim the YANG-first
  direction stands or falls on, and it can only be seen in the
  cross-format cells — which is why the evaluation is paired.

Known confound to control: per F1, both views show the active-mode
projection, so the experiment measures reading/decision transfer for
projected state only; questions about inactive parameters are out of
scope for both arms equally.

## 7. Files in this slice

- `docs/YANG_PIN.md` — the pin record and re-pin procedure.
- `docs/YANG_SLICE.md` — this document.
- `vendor/yang/openconfig/` (13 modules) and
  `vendor/yang/cisco-xe-17181/` (9 modules) — the pinned schemas.
- `data/yang_slice/instances.jsonl` — 11 labeled instances.
- `data/yang_slice/paired_goldens.jsonl` — 9 records: 7 paired cases
  (canonical add; one add/replace/remove contrast set with three
  requests; access assignment with admin preservation; no-op;
  ambiguous-alias clarify; admin bring-up; multi-port) and 2
  state→English interpretation pairs (minority direction).
- `scripts/check_yang_instances.py`,
  `scripts/check_paired_goldens.py` — the checkers described in §4.
