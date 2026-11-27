# yang-learn — schema-derived training prep (REVIEW SAMPLE)

Goal (Avinash's direction, 2026-11-26): before any example-driven
config training, the model should first **learn the YANG models
themselves** — the actual schema of the target platform — so later
tasks rest on grounded structure instead of surface pattern-matching.

This directory is a **small, reviewable sample** (41 generated records
+ 8 hand-authored held-out records), not a training set. Bulk
generation and any weight updates are held until this sample is
reviewed and a training scope/cost can be quoted.

## Platform pin

- Source: YangModels/yang @ `9c23db975096b67843858128f37947a0c30a2d45`,
  folder `vendor/cisco/xe/2621` (IOS XE **26.2.1**).
- Device family: Catalyst 9300, via the bundle's Catalyst 9K set.
  `capability-cat9k.xml` is a NETCONF `<hello>` that advertises the
  YANG-library content id `5b4084baa5d5112c0d4ecd19439afbf6`; the
  actual module enumeration is `yang-set-cat9k.xml` (783 modules).
  There is no per-9300-SKU capability file in the bundle, so exact
  SKU-level behavior is **unverified**; the family set is the
  provenance.
- Roots loaded: `Cisco-IOS-XE-native` (rev 2026-08-01) with all 27
  cat9k deviation modules from the yang-set, plus the standalone
  `Cisco-IOS-XE-vlan` and `Cisco-IOS-XE-ethernet` modules. pyang load:
  **zero errors, zero warnings** (~51s). `Cisco-IOS-XE-interfaces` is
  a submodule of native; the switchport grouping lives in module
  `Cisco-IOS-XE-switch`.
- The 17.18.1 / OpenConfig material elsewhere in this repo is a
  different version line and stays version-labeled as such.

## Method — label/verdict independence

- **Labels & facts** come from pyang's parsed tree (`schema_tools.py`):
  types (with typedef chains resolved), ranges, defaults, `when`/`must`
  conditions, and module/file/line provenance for every fact.
- **Instance verdicts** come from **yangson** (1.7.9), an independent
  implementation (`validate_instances.py`), using a scoped RFC 7895
  library (`scoped-library.json`, built by `build_scoped_library.py`
  from the yang-set filtered to the pyang-resolved closure — 64
  modules). No component both writes a label and judges it.
- The generator (`gen_records.py`) drops any record whose yangson
  verdict disagrees with its intended label, and reports the drop.
  Revision 2 run: **41 kept, 0 dropped** (14/14 validity verdicts
  matched at the *rule* level — see below; all 16 reconstruction
  before/after states valid; all 4 edit specs pass an apply-check).

## Revision 2 — per Codex review of the first sample

1. **Origin & provenance.** Every record carries `origin`:
   `generated_fact` (extracted from the schema by pyang),
   `authored_structure` (read from the source by hand), or
   `template_instance` (a manually constructed instance/edit
   template). No record has a null `source` anymore: validity, edit,
   reconstruction, and profile records carry the module, revision,
   and the exact schema paths they exercise.
2. **Rule-level verification.** An invalid case no longer passes on
   the verdict alone: the yangson rejection must implicate the
   *expected node* and be a constraint rejection (RawTypeError /
   YangTypeError / SchemaError). Each validity record's
   `validation.verdict_kind` records `expected_rejection`,
   `accepted`, or `validator_exception` — an encoding error on the
   generator's side is classified as an exception and the record is
   dropped, never counted as a rejection. This run: 10/10 invalid
   cases are `expected_rejection`.
3. **Held-out revised schema.** `held_out.jsonl` now includes two
   hand-authored `revised_schema` records under a **new profile
   P2** (native VLAN must be 999; access VLANs even) that never
   appears in the train-candidate set.
4. **Edit forms separated.** Full-state completions are family
   `state_reconstruction` (task_form `full_state`); family
   `typed_edit` now returns the *edit spec itself* (task_form
   `edit_spec`), machine-checked by applying it to the before-state
   and comparing to the after-state. Both forms carry
   `executable_on_device`: edits that reference a VLAN with no
   configuration-entry (missing prerequisite), deletion cases, and
   legacy-enum cases are labeled schema-only (`false`, with
   `schema_only_reason`) and kept separate from executable labels.
5. **Wording.** The nonmandatory mode choice is "at most one case"
   everywhere (it was "exactly one" in three places).

## Revision 3 — per Codex #69/#70 (sample cleared)

Codex replayed revision 2 independently: all 41 records regenerated
with zero drops and `records.jsonl` matched byte-for-byte; the
sample is cleared as schema-training prep. Two further fixes landed
with the scale-up design (`SCALE_DESIGN.md`):

1. **Defining-node provenance.** Provenance now cites the module
   that *textually defines* each node (via the statement's source
   file, which pyang preserves through grouping expansions and
   augments): configuration-entry paths cite `Cisco-IOS-XE-vlan`,
   switchport grouping paths cite `Cisco-IOS-XE-switch`, the
   interface key cites `Cisco-IOS-XE-interfaces` — each with its own
   revision, grouped per record under `source.modules`. (Revision 2
   cited the instantiating module, Cisco-IOS-XE-native, for all.)
2. **Edit target selectors.** Every typed edit now carries an
   explicit `target` (list path + list key, e.g. GigabitEthernet
   `name = "1/0/1"`), and the apply-check resolves the target by key
   instead of by position — the precondition for multi-interface
   states at scale.

## Record families (records.jsonl, split = train_candidate)

| family | n | what it teaches |
|---|---|---|
| path_meaning | 11 | a leaf's type/range/default/meaning, or a `when` condition, at an exact schema path, with source provenance |
| instance_validity | 14 | judge a full JSON instance valid/invalid; every invalid case breaks exactly one rule (union range, key pattern, enum, choice exclusivity, `when`) and the rejection is verified at that rule's node |
| state_reconstruction | 8 | apply one narrow edit and return the full resulting state; changed vs preserved paths computed mechanically; both states yangson-valid; `executable_on_device` flags schema-only cases |
| typed_edit | 4 | return the typed edit spec (not the state); verified by applying the spec to the before-state |
| revised_schema | 4 | questions answered under an overriding deployment profile (excerpt embedded in the prompt) — tests using a *given* rule over the memorized base schema |

`held_out.jsonl` (8 records) is **hand-authored**, kept separate from
the generator's templates, and covers discrimination cases the
templates don't: union branch vs JSON type, pattern edge cases
(including `"10-5"`, which the pattern provably cannot reject), a
two-conjunct `when`, the wrapper-vs-config mode contrast, a different
choice-exclusivity pair, and a two-interface preserve-everything-else
edit, plus two revised-schema cases under a fresh profile (P2) the
train-candidate set never uses. Its instance verdicts were confirmed with yangson at authoring
time and are recorded per record.

## Platform assumptions & caveats (read before scaling)

1. **Canonical surface = `switchport-config`.** The native model
   carries three parallel switchport surfaces (switchport-config,
   switchport-wrapper, and a plain switchport instantiation) plus
   obsolete-marked duplicate groupings. Records use switchport-config
   except the explicit wrapper contrast records (YL-PM-07, YL-HO-04).
2. **JSON encoding namespaces.** Grouping-defined nodes carry the
   grouping's home module as their JSON prefix
   (`Cisco-IOS-XE-switch:...` under the native tree), and augmented
   nodes carry their augmenting module (`Cisco-IOS-XE-vlan:...`).
   This follows libyang/yangson namespace rules and is visible in
   every instance in `instances/`.
3. **keep-obsolete.** IOS XE marks many still-served nodes
   `status obsolete` (129 occurrences in native.yang). yangson would
   exclude them from validation, so the scoped library sets
   `yangson-yl:keep-obsolete: true` — matching device behavior of
   continuing to serve the tree.
4. **Validation builds the model from the source .yang files on
   every run** (~40s); no serialized/pickled model is loaded or
   trusted.
5. **Schema-valid ≠ device-accepted.** Example: the access-VLAN
   container carries no `when` tying it to access mode, so the schema
   admits it on a trunk port where the CLI would not. These records
   teach the published schema, not unwritten device rules.

## Reproduce

```bash
# bundle: clone YangModels/yang @ 9c23db9, use vendor/cisco/xe/2621
.venv/bin/python yang-learn/build_scoped_library.py \
    --out yang-learn/scoped-library.json
.venv/bin/python yang-learn/gen_records.py   # rewrites records.jsonl + instances/
```

`vendor/` holds the five directly-cited modules plus the two cat9k
set files for review convenience; the full closure comes from the
pinned bundle above.

## Corpus v1 (2026-11-26)

The scale-up is built: `gen_corpus.py` generates the full corpus
under SCALE_DESIGN.md (as amended), validates every record through
the same pyang-facts ≠ yangson-verdicts chain, and writes
`corpus/{train,dev,test,benchmark}.jsonl` + `corpus/manifest.json`
(actual split/coverage/drop counts and pinned-tokenizer stats).
Final run: train 1,047 · dev 78 · test 160 · benchmark 209, zero
drops. Bench v1 (benchmark + the 8 hand-authored held-out records)
is defined in `corpus/BENCHMARK.md` and frozen by commit.
`tokenize_corpus.py` reproduces the token stats. Training awaits
Avinash's approval of the firm quote in SCALE_DESIGN.md (A5).
