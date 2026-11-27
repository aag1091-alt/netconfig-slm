# yang-learn — schema-derived training prep (REVIEW SAMPLE)

Goal (Avinash's direction, 2026-11-26): before any example-driven
config training, the model should first **learn the YANG models
themselves** — the actual schema of the target platform — so later
tasks rest on grounded structure instead of surface pattern-matching.

This directory is a **small, reviewable sample** (37 generated records
+ 6 hand-authored held-out records), not a training set. Bulk
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
  This run: **37 kept, 0 dropped** (14/14 validity verdicts matched;
  all 16 typed-edit before/after states valid).

## Record families (records.jsonl, split = train_candidate)

| family | n | what it teaches |
|---|---|---|
| path_meaning | 11 | a leaf's type/range/default/meaning, or a `when` condition, at an exact schema path, with source provenance |
| instance_validity | 14 | judge a full JSON instance valid/invalid; every invalid case breaks exactly one rule (union range, key pattern, enum, choice exclusivity, `when`) |
| typed_edit | 8 | apply one narrow edit to a state; changed vs preserved paths are computed mechanically and both states are yangson-valid |
| revised_schema | 4 | questions answered under an overriding deployment profile (excerpt embedded in the prompt) — tests using a *given* rule over the memorized base schema |

`held_out.jsonl` (6 records) is **hand-authored**, kept separate from
the generator's templates, and covers discrimination cases the
templates don't: union branch vs JSON type, pattern edge cases
(including `"10-5"`, which the pattern provably cannot reject), a
two-conjunct `when`, the wrapper-vs-config mode contrast, a different
choice-exclusivity pair, and a two-interface preserve-everything-else
edit. Its instance verdicts were confirmed with yangson at authoring
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
