# Verification — YANG coverage dashboard

What was checked, how, and the numbers. All commands run from the
repo root on the `coverage-dashboard` branch.

## 1. Fixture tests (pure logic)

`python3 -m unittest discover -s coverage/tests -v` — **11 tests,
all passing.** They cover:

- **Rollup math.** A fixture where child A is 1/2 (50 %) and child B
  is 9/10 (90 %): averaging child percentages would give 70 %; the
  generator's count-based rollup gives 10/12 = 83.33 %, and the test
  asserts both the value and that it differs from the average.
  Counts (incl. the combined `corpus` overlay) are asserted to sum
  unchanged through three tree levels.
- **Leaf dedupe.** Two statements collapsing to one canonical path
  are counted once; overlapping changed/preserved paths in a
  state_reconstruction record dedupe to a set.
- **Unknown / empty handling.** A subtree whose leaves are all
  excluded has total 0 and percentage `None` (rendered “n/a”), while
  an eligible-but-uncovered leaf is a genuine 0.0 %. Excluded
  (obsolete / config-false) leaves never enter a denominator.
- **Evidence rules.** typed_edit covers only its changed path;
  selector-negatives cover their attempted path; instance_validity
  uses the judged node, not instance bystanders; instance-path
  canonicalisation strips module prefixes, `[i]` indices and
  `(added)` annotations.
- **Priorities.** Glob semantics (`*` = one segment, `**` = rest)
  and first-match-wins ordering.

## 2. Real branch verified end-to-end against raw pyang output

Independent tool: `yang-learn/schema_tools.py inventory()` — the
project's own extractor, which loads **only** the closure
(`Cisco-IOS-XE-native` + the 27 cat9k deviations) and reports
**full** schema paths (choice/case segments included). The
generator loads the full config-bearing module set and reports
**canonical** (choice/case-elided) paths. Reproduce with
`coverage/tests/verify_branch.py` (~3 min, two pyang loads).

Named branch —
**`/native/interface/GigabitEthernet/switchport-config/switchport/trunk`**:

| | leaves |
|---|---:|
| schema_tools (raw, full paths) | 19 |
| generator (canonical paths) | 19 |
| equal after choice/case elision | **yes** |

The 19 raw paths include e.g.
`.../trunk/allowed/vlan/vlan-choice/except/except` and
`.../trunk/allowed/vlan-v2/vlan-choices/vlan-choice/all-case/all`;
eliding the choice segment (`vlan-choice`, `vlan-choices`) and the
case segment (`all-case`, and case names duplicating their leaf)
yields exactly the generator's 19 canonical paths
(`.../trunk/allowed/vlan/except`, `.../trunk/allowed/vlan-v2/all`, …).

Branches where the two conventions coincide and the sets must be
**exactly** equal — all verified equal:

| branch | leaves (both tools) |
|---|---:|
| `.../switchport/access` | 1 (`access/vlan/vlan`) |
| `.../switchport/voice` | 3 |
| `/native/vlan/configuration-entry/vlan-id` | 1 |

A fourth branch, `.../switchport/port-security-config`, is 34 = 34
after the same normalisation.

Where the tools legitimately differ, and why: under
`/native/vlan/configuration-entry` the generator reports 86 leaves
vs schema_tools' 25 — the difference is augments from modules
outside the closure load (e.g. `Cisco-IOS-XE-mdns-gateway`'s
`mdns-sd` subtree) that the full-platform inventory deliberately
includes, plus the convention elision above. The corpus's own
slice was built from the closure tool; the denominator is the
full-platform one. Both facts are stated on the page.

## 3. Evidence join

Corpus v1 evidence resolution (generator output, fresh run):
**2,134 leaf matches · 889 construct matches · 0 unresolved.**
Every path named by every record in all five splits resolves
against the resolved schema. Cross-checks:

- `/native/interface/GigabitEthernet/switchport-config/switchport/access/vlan/vlan`
  carries evidence from all five splits (train 361 records, dev 23,
  test 25, benchmark 68, held-out 1) and a joined bench pass of
  54/69.
- Section totals sum with the 9 direct leaves under `/native`
  (hostname, config-register, …): 154,043 + 9 = **154,052**, the
  headline denominator.
- Interface-section coverage by interface type (train): 
  AppGigabitEthernet 39, Port-channel-subinterface 36,
  GigabitEthernet 9 = 84 — the first two are the cleared sample
  records' surfaces, which is correct: they are folded into train.

## 4. Site smoke test

`coverage.json` (10.0 MB) parses; the page was served over HTTP in
both layouts it will run in — the Pages assembly (`index.html` +
`data/coverage.json`) and the local layout (`coverage/site/` +
`coverage/data/`) — and all assets returned 200. `app.js` passes
`node --check`. Rendering is lazy (children are built on first
expand), so the 187k-leaf tree never enters the DOM at once.
