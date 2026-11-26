# YANG pin record — VLAN/interface slice

Pinned 2026-11-26. Every entry below was verified live against the
source repo on that date (API listings + file downloads); nothing is
pinned from memory. Companion docs: `docs/YANG_SLICE.md` (field
mapping + format rules + the training-order experiment),
`docs/OPENCONFIG_AND_YANG.md` (usage modes).

## Platform target

- **Device class:** Cisco Catalyst 9300 (IOS XE switching).
- **Release:** IOS XE **17.18.1** — folder `17181` in YangModels/yang.
  Chosen because it is the newest 17.x release folder actually
  present in the repo (folders seen: 16101 … 1791, **17181**, plus
  the newer 2611/2621 line, which is out of scope for a 17.x pin),
  and because the folder carries Catalyst-9000-specific material
  (e.g. `Cisco-IOS-XE-interfaces-cat9k-deviation.yang`), i.e. it is
  a switching-platform release, not a routing-only one.

## Pin 1 — OpenConfig (openconfig/public)

- **Tag:** `v5.11.0` (newest release tag at pin time)
- **Commit:** `160da73440205de252066233b1ee0f3085d51d2d`
- **Vendored to:** `vendor/yang/openconfig/`
- **In-scope modules** (the slice's schema):
  `openconfig-interfaces`, `openconfig-if-ethernet`,
  `openconfig-vlan`, `openconfig-vlan-types`
- **Import closure vendored alongside** (required for the in-scope
  modules to load): `openconfig-if-aggregate`,
  `openconfig-extensions`, `openconfig-types`,
  `openconfig-yang-types`, `openconfig-transport-types`,
  `openconfig-platform-types`, `ietf-interfaces`,
  `ietf-yang-types`, `iana-if-type`
- **Module validation:** `pyang 2.7.1` loads the full vendored set
  with zero errors (see `docs/YANG_SLICE.md` §4 for exactly what was
  and was not validated).
- **Identified but NOT vendored:** `openconfig-system` — the
  hostname leaf lives there (`/system/config/hostname`, verified in
  the release file), but its import closure (aaa, alarms, platform,
  procmon, snmp, …) is far outside this slice. Hostname therefore
  stays in the context layer for this slice (see YANG_SLICE.md).

## Pin 2 — Cisco native IOS XE models (YangModels/yang)

- **Repo commit:** `e13b05c4ddcbfab9170e60dddbbe9eaf6a831964`
  (HEAD at retrieval, 2026-11-16 commit date)
- **Path:** `vendor/cisco/xe/17181/` (873 files in the folder)
- **Vendored to:** `vendor/yang/cisco-xe-17181/`
- **Vendored files** (mapping reference for the slice):
  - `Cisco-IOS-XE-native.yang` — root tree (`/ios:native`)
  - `Cisco-IOS-XE-interfaces.yang` — interface lists + the
    `interface-switchport-grouping` used by them
  - `Cisco-IOS-XE-vlan.yang` — augments `/ios:native/ios:vlan`
    (`configuration-entry` list)
  - `Cisco-IOS-XE-switchport-deviation.yang`,
    `Cisco-IOS-XE-interfaces-cat9k-deviation.yang` — platform
    deviations touching the slice
  - `cisco-xe-openconfig-interfaces-deviation.yang`,
    `cisco-xe-switching-openconfig-interfaces-deviation.yang`,
    `cisco-xe-switching-openconfig-if-ethernet-deviation.yang`,
    `cisco-xe-switching-openconfig-vlan-deviation.yang` — Cisco's
    published deviations against the OpenConfig modules on XE
    switching platforms
- **Not vendored:** the remaining ~860 files of the release folder,
  including the native modules' own import closure. The XE files are
  vendored as the *mapping reference*; no claim is made that they
  load standalone (their imports — `cisco-semver`,
  `Cisco-IOS-XE-l2vpn`, … — are not in this repo). There is **no**
  `Cisco-IOS-XE-switchport.yang` config module in 17.18.1 (only a
  `…-switchport-oper.yang` state module and the deviation file):
  switchport *configuration* lives in the grouping inside
  `Cisco-IOS-XE-interfaces.yang`. This corrects the contract's
  earlier "≈ Cisco-IOS-XE-switchport" shorthand
  (`docs/PLAN_CONTRACT.md` §1).

## Retrieval

Downloaded 2026-11-26 from `raw.githubusercontent.com` at the
commits above. To re-pin: update this file, replace the vendored
files, re-run `scripts/check_yang_instances.py` and
`scripts/check_paired_goldens.py`, and record the change in the
experiment that uses the pin (roadmap: schema revisions are pinned
per experiment).
