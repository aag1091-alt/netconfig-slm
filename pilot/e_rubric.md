# Blind factual rubric — Explain items

Used by whoever grades E factual correctness from
`results/blind_e_sheet.jsonl`. The sheet strips arm labels on purpose:
grade the answer in front of you against the requirements below, never
against a guess about which arm produced it. Citation correctness is
graded mechanically by the scorer — ignore the citation line entirely.

General rules:
- An answer is factually correct only if every requirement for its item
  is stated AND nothing stated contradicts the pinned packet
  (schema_packet.txt) or the slice notes.
- An answer that contains the right words but negates them ("X is NOT
  a leaf-list of ...") is INCORRECT. Keyword presence is not truth.
- Hedged non-answers ("it might be ... or possibly ...") that never
  commit to the required fact are incorrect.
- Extra correct detail is fine; extra incorrect detail is not.

Per-item requirements:

- **E-01** (trunk-vlans type): must state that trunk-vlans is a
  leaf-list whose type is a UNION of **both** branches — vlan-id **and**
  vlan-range. An answer naming only vlan-id is incomplete and
  incorrect (this is the packet's exact definition).
- **E-02** (access-vlan on a TRUNK interface): must answer No, because
  access-vlan carries a `when` condition requiring interface-mode =
  'ACCESS'.
- **E-03** (absent trunk-vlans): must state that absence means ALL
  VLANs are allowed on the trunk (per the leaf's own description).
- **E-04** (admin state): must name the `enabled` leaf in
  openconfig-interfaces config, type boolean, default true.
- **E-05** (native VLAN): must place native-vlan in the switched-vlan
  config and state it is valid only for TRUNK mode (its `when`
  condition).
- **E-06** (hostname): must answer that the slice does NOT define
  hostname; it lives in openconfig-system (/system/config/hostname),
  outside the vendored slice.
- **E-07** (VLAN id + name): must name openconfig-vlan's vlans/vlan
  list (its config carries vlan-id and name).
- **E-08** (vlan-id range): must state the vlan-id type is uint16 with
  range 1..4094 (typedef in openconfig-vlan-types).

Grading output: one line per sheet entry in
`results/e_blind_grades.jsonl`:
`{"blind_id": "...", "factual_correct": true|false}`.
