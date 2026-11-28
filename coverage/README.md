# YANG coverage dashboard

A static status page that shows, as an expandable multi-level tree,
how much of the **IOS XE 26.2.1 native YANG surface** (Catalyst 9K
family set) the yang-learn corpus v1 exercises — every node carries
covered/total counts and a percentage computed from **descendant
leaf counts** (never an average of child percentages).

**Schema coverage only.** Nothing here is a device claim: a covered
leaf means a corpus record exercises that schema path, not that any
IOS XE device accepts a configuration. Schema-valid ≠
device-accepted (see `yang-learn/SCALE_DESIGN.md` §8).

## Headline (corpus v1, this commit)

| metric | value |
|---|---|
| Denominator — eligible leaves under `/native` | **154,052** |
| Covered by the training split | 106 leaves · **0.07 %** |
| Covered by corpus train+dev+test | 122 leaves · **0.08 %** |
| Covered by benchmark / held-out splits | 29 / 4 leaves |
| Denominator if interface-type replicas are counted once | 68,248 (labelled view, not the headline) |
| Run-1 model pass on bench v1 | 160/217 · 73.7 % (family-level in the page) |

Top-level sections (eligible leaves · covered by train):

| section | total | train-covered | % |
|---|---:|---:|---:|
| interface | 98,209 | 84 | 0.09 |
| router | 24,293 | 0 | 0.00 |
| ip | 6,103 | 0 | 0.00 |
| crypto | 2,563 | 0 | 0.00 |
| policy | 2,297 | 0 | 0.00 |
| template | 1,881 | 0 | 0.00 |
| ipv6 | 1,474 | 0 | 0.00 |
| flow | 1,466 | 0 | 0.00 |
| aaa | 1,176 | 0 | 0.00 |
| snmp-server | 979 | 0 | 0.00 |
| … (277 sections in total) | | | |
| **vlan** | **117** | **22** | **18.80** |

The corpus v1 slice is deliberately narrow — `/native/vlan` plus the
interface switchport subtrees — and the dashboard says so in numbers
rather than hiding it behind a scoped denominator.

## The metric

**Denominator.** Unique *effective writable* leaf/leaf-list
canonical paths in the resolved schema, where:

- *Resolved schema* = pyang's resolution of the bundle's
  Cisco-IOS-XE modules with all augments applied, rooted at the
  `native` container of `Cisco-IOS-XE-native`. Load set: every
  `Cisco-IOS-XE-*.yang` in the bundle **except** the classes
  excluded by rule below, and except deviation files **not
  advertised in the bundle's own `yang-set-cat9k.xml`** (the bundle
  directory is shared across platform sets; e.g.
  `Cisco-IOS-XE-vlan-vxe-deviation` belongs to another set and marks
  `/native/vlan/internal/allocation/policy` not-supported — it is
  not applied). The advertised Cisco-IOS-XE deviation subset is
  exactly the 27 deviations the project already uses in
  `yang-learn/schema_tools.py`.
- *Canonical path* = the schema path with **choice/case segments
  elided** — the corpus's own convention, since a choice is not
  addressable in instance data (e.g. the trunk allowed-VLAN case
  leaves live at `.../trunk/allowed/vlan/{add,except,none,…}`).
  859 transparent paths arise from more than one statement
  (choice-case collisions); each is counted **once**.
- *Effective writable* = `config true` (pyang's effective
  `i_config`) and status not `obsolete`. Deprecated leaves (4,603)
  are still supported and **are** counted.

**Exclusions — each counted, none silent** (full table on the page):

| class | count | basis |
|---|---:|---|
| Obsolete leaves in the tree | 33,627 | excluded from the denominator; corpus evidence on them is still shown, flagged. (The device still serves many obsolete nodes — the project's yangson library keeps them — but the metric follows the stated rule.) |
| Config-false leaves in the `/native` tree | 0 | none exist; XE keeps state in `*-oper` modules |
| Operational-state modules (`*-oper`) | 156 modules | excluded by rule |
| RPC modules (`*-rpc`, `*-actions-rpc`) | 43 modules | excluded by rule |
| Notification modules (`*-events`) | 20 modules | excluded by rule |
| `deviate not-supported` statements (advertised deviations) | 778 | those nodes are removed by the device profile and never enter the tree |
| Type-only modules | 33 modules | no data nodes |

**Not inventoried ≠ 0 %.** The page renders unmeasured surfaces
distinctly (purple “not inventoried”), never as zero coverage:

- OpenConfig modules (63), IETF models (23), SNMP MIB modules
  (113), tailf infrastructure (14), other vendor/helper modules
  (32) in `yang-set-cat9k` — separate model surfaces, out of scope.
- 46 standalone config roots in loaded Cisco-IOS-XE modules outside
  `/native` (the wireless `*-cfg` family and friends), totalling
  2,225 eligible leaves — counted, listed on the page, not
  inventoried.

**Duplicate surfaces.** The same configuration surface replicated
across ~30 interface-type lists (`GigabitEthernet`,
`TenGigabitEthernet`, `Port-channel`, …) is counted per canonical
path in the headline (the paths *are* distinct), and the page also
publishes the interface-type-normalised denominator (68,248) as a
labelled secondary view. The legacy `switchport-wrapper` subtree
(15 leaves) is an alias surface of `switchport-config`; both are
real canonical paths and both are counted.

**Coverage.** A corpus record covers exactly the leaves it directly
exercises — never siblings, never a whole subtree:

| family | evidence |
|---|---|
| path_meaning | the fact's path |
| instance_validity | the judged node(s) (`fact.path`, else the record's source paths). Bystander leaves present in the instance are scaffolding, not coverage |
| state_reconstruction | every leaf in the before/after states (`changed_paths` + `preserved_paths`, canonicalised from instance-path form) |
| typed_edit | only the path(s) the edit directly changes (`edits[]` / `attempted_edits[]` paths, `edited_path`) — including selector-negative records, whose attempted path is the subject |
| revised_schema | the schema node the profile question is about (source paths) |

Records that name a **construct** (container / list / choice / case —
e.g. path_meaning structure records) attach to that tree node as
*construct references* and cover **no** leaves. Two hand-authored
held-out records are hand-mapped in the generator (`YL-HO-04`,
`YL-HO-05`: the mode leaf on the wrapper surface + the mode choice
construct); `YL-HO-07/08` name no schema node and contribute only
family-level pass data. Evidence resolution for corpus v1:
**2,134 leaf matches, 889 construct matches, 0 unresolved.**

**Overlays are separate.** Training (train), corpus (train+dev+test),
dev, test, benchmark, and held-out coverage are independent overlays
in the data and the page — benchmark coverage never inflates the
training number.

**Model pass rates.** Run-1 bench-v1 scores
(`coverage/data/run1_scores.json`, a snapshot of
`outputs/yang-learn-run1/scores.json` — vendored because `outputs/`
is not committed) are joined per item to the leaves the item
exercises: 189 of 217 items join; per-leaf pass counts appear in the
tree. The remaining 28 items (construct-only or nodeless) are
reported at family level only, exactly as far as the evidence
supports.

**Priorities.** `priorities.yaml` holds heuristic, maintainer-chosen
labels (interface admin/description, access/voice VLANs, trunk
VLANs, port channels, port protections, VLAN definitions). They are
**judgement, not telemetry** — no usage data exists or is claimed —
and are displayed separately from coverage. Edit the file and
re-run the generator to change them.

## Provenance

- Bundle: YangModels/yang @ `9c23db975096b67843858128f37947a0c30a2d45`,
  `vendor/cisco/xe/2621` (IOS XE 26.2.1), `yang-set-cat9k` module set.
- Inventory tool: pyang 2.7.1 (one non-fatal `BAD_DEVIATION` warning
  in the full load; recorded in the JSON's provenance block).
- Corpus: yang-learn corpus v1, frozen at commit `5189d33`
  (train 1,047 · dev 78 · test 160 · benchmark 209) plus the 8
  hand-authored `held_out.jsonl` records.
- The 17.18.1 YANG slice is a different release and is **out of
  scope** here; this dashboard is 26.2.1 only.

## Files

```
coverage/
  generate_coverage.py   inventory + evidence join + rollups -> JSON
  regen.sh               the one regeneration command (finds a pyang python)
  check_freshness.py     stale-JSON guard (fingerprints; --full = regen + compare)
  build_site.sh          the one site assembly (used by both publish paths)
  publish_gh_pages.sh    gh-pages fallback publisher (dry run unless --push)
  Makefile               regen / check / check-full / test / site shortcuts
  priorities.yaml        heuristic priority labels (editable)
  data/coverage.json     generated, committed — the page's only input
  data/run1_scores.json  vendored snapshot of the run-1 bench scores
  site/                  index.html + style.css + app.js (no framework)
  tests/test_rollup.py   fixture tests for rollup/dedupe/evidence
  tests/test_freshness.py fixture tests for the freshness fingerprints
  tests/verify_branch.py end-to-end branch check vs schema_tools.py
  workflows/coverage-pages.yml  Pages workflow, staged here until the
                         gh token gains the `workflow` scope to land
                         it in .github/workflows/ (see DEPLOYMENT.md)
  VERIFICATION.md        what was verified, with numbers
  DEPLOYMENT.md          publish paths, freshness contract, first deploy
```

## Refresh procedure

1. `bash coverage/regen.sh` (or `make -C coverage regen`; ~2
   minutes; the script finds a python with pyang — the repo venv —
   and accepts `--bundle` etc. via the generator). Regeneration is
   deterministic: same inputs → same JSON apart from
   `meta.generated_at`.
2. Review the printed headline + resolution counts (unresolved must
   be 0 or explained).
3. Commit the regenerated `coverage/data/coverage.json`. The JSON
   records `meta.fingerprint` — hashes of every input — and
   `coverage/check_freshness.py` (fast; `--full` also regenerates
   and compares) fails if the corpus, bundle, or generator changed
   after generation, so stale numbers can't be republished as
   current. The page footer shows the fingerprint and generation
   time.
4. Publishing: on merge to `main`, the `coverage-pages` workflow
   republishes GitHub Pages — after passing the freshness check.
   It triggers on pushes touching `coverage/**` or
   `yang-learn/corpus/**`, and can be run manually
   (`workflow_dispatch`). Until the workflow lands in
   `.github/workflows/` (token `workflow` scope), the gh-pages
   branch fallback in `DEPLOYMENT.md` is the deploy path.

Local preview: serve the `coverage/` directory
(`python3 -m http.server` inside `coverage/`) and open
`/site/` — the page fetches `../data/coverage.json`. On Pages, the
workflow assembles the site with `data/coverage.json` alongside, and
the page's primary fetch path resolves there.

## Tests

```
python3 -m unittest discover -s coverage/tests -v   # pure-logic fixtures
.venv/bin/python coverage/tests/verify_branch.py    # end-to-end, ~3 min
```
