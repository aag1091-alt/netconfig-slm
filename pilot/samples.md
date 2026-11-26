# Worked samples — one per task (arm B rendering)

Rendered by build_items.py from prompts.py, so the prompt text below is byte-identical to what the runner will send. Expected outputs are the graders' references.

## Read — item R-01 (source: interp-trunk-facts, view: cli)

### Prompt (system)
```
You are a network configuration assistant being evaluated in a pilot. Follow the output format exactly. Do not add commentary outside the requested format.
```
### Prompt (user)
```
Device: sw-acc-1 (hostname switch1)
Interface aliases: GigabitEthernet1/0/48: uplink

Current configuration (IOS XE CLI):
hostname switch1
!
vlan 10
 name Users
!
vlan 20
 name Engineering
!
vlan 30
 name Voice
!
interface GigabitEthernet1/0/48
 description Uplink to core
 switchport mode trunk
 switchport trunk native vlan 10
 switchport trunk allowed vlan 10,30
 no shutdown
!

Schema evidence (pinned YANG excerpts):
SCHEMA PACKET — arm B evidence (pinned YANG excerpts, verbatim)
Slice: OpenConfig v5.11.0 @ 160da73440205de252066233b1ee0f3085d51d2d + IOS XE 17.18.1 mapping (docs/YANG_PIN.md, docs/YANG_SLICE.md)

--- Excerpt 1: grouping vlan-switched-config, vendor/yang/openconfig/openconfig-vlan.yang ---
      "VLAN related configuration that is part of the physical
      Ethernet interface.";

    leaf interface-mode {
      type oc-vlan-types:vlan-mode-type;
      description
        "Set the interface to access or trunk mode for
        VLANs";
    }

    leaf native-vlan {
      when "../interface-mode = 'TRUNK'" {
        description
          "Native VLAN is valid for trunk mode interfaces";
      }
      type oc-vlan-types:vlan-id;
      description
        "Set the native VLAN id for untagged frames arriving on
        a trunk interface.  Tagged frames sent on an interface
        configured with a native VLAN should have their tags
        stripped prior to transmission. This configuration is only
        valid on a trunk interface.";
    }

    leaf access-vlan {
      when "../interface-mode = 'ACCESS'" {
        description
          "Access VLAN assigned to the interfaces";
      }
      type oc-vlan-types:vlan-id;
      description
        "Assign the access vlan to the access port.";
    }

    leaf-list trunk-vlans {
      when "../interface-mode = 'TRUNK'" {
        description
          "Allowed VLANs may be specified for trunk mode
          interfaces.";
      }
      type union {
        type oc-vlan-types:vlan-id;
        type oc-vlan-types:vlan-range;
      }
      description
        "Specify VLANs, or ranges thereof, that the interface may
        carry when in trunk mode.  If not specified, all VLANs are
        allowed on the interface. Ranges are specified in the form
        x..y, where x<y - ranges are assumed to be inclusive (such
        that the VLAN range is x <= range <= y.";
    }
  }

--- Excerpt 2: grouping vlan-config (VLAN list entry), vendor/yang/openconfig/openconfig-vlan.yang ---
  grouping vlan-config {
    description "VLAN configuration container.";

    leaf vlan-id {
      type oc-vlan-types:vlan-id;
      description "Interface VLAN id.";
    }

    leaf name {
      type string;
      description "Interface VLAN name.";
    }

    leaf status {
      type enumeration {
        enum ACTIVE {
          description "VLAN is active";
        }
        enum SUSPENDED {
          description "VLAN is inactive / suspended";
        }
      }
      default ACTIVE;
      description "Admin state of the VLAN";
    }


--- Excerpt 3: typedef vlan-id, vendor/yang/openconfig/openconfig-vlan-types.yang ---
  typedef vlan-id {
    type uint16 {
      range 1..4094;
    }
    description
      "Type definition representing a single-tagged VLAN";
  }

--- Excerpt 4: leaf enabled (interface config), vendor/yang/openconfig/openconfig-interfaces.yang ---
    leaf enabled {
      type boolean;
      default "true";
      description
        "This leaf contains the configured, desired state of the
        interface.

        Systems that implement the IF-MIB use the value of this
        leaf in the 'running' datastore to set
        IF-MIB.ifAdminStatus to 'up' or 'down' after an ifEntry
        has been initialized, as described in RFC 2863.

--- Slice notes (from docs/YANG_SLICE.md, part of the packet) ---
1. Hostname is NOT in this slice: it lives at openconfig-system /system/config/hostname, which is not vendored here. In this pilot hostname and interface aliases are context, not schema.
2. Finding F1: because of the when conditions above, an inactive-mode parameter (e.g. access-vlan on a TRUNK interface) cannot appear in a YANG instance of this slice.
3. Finding F2: OpenConfig has no encoding for trunk 'none' — an absent trunk-vlans list means ALL VLANs are allowed. This slice documents an empty list [] as 'none' by deviation.

Question: Which VLANs are allowed on the uplink trunk, and what is its native VLAN?
Reply with ONE JSON object containing exactly the facts asked for, using these field names as applicable: vlans (object of vlan id -> name), and per interface: mode, access_vlan, trunk_allowed, native_vlan, admin, description. Shape: {"vlans": {...}, "interfaces": {"<interface>": {...}}}. Omit sections that were not asked about.
```
### Expected output
```
{"interfaces": {"GigabitEthernet1/0/48": {"trunk_allowed": [10, 30], "native_vlan": 10}}}
```
### Scoring
Scoring: the reply's JSON is extracted and compared field-by-field with the expected facts. Exact equality on trunk_allowed and native_vlan = correct. Any extra or missing fact, or a wrong value, = incorrect (the raw output is kept for audit). The paired JSON-view item R-02 asks the same facts from the YANG instance; agreement between the two views is reported separately.

## Explain — item E-02 (source: authored for this pilot, view: na)

### Prompt (system)
```
You are a network configuration assistant being evaluated in a pilot. Follow the output format exactly. Do not add commentary outside the requested format.
```
### Prompt (user)
```
Schema evidence (pinned YANG excerpts):
SCHEMA PACKET — arm B evidence (pinned YANG excerpts, verbatim)
Slice: OpenConfig v5.11.0 @ 160da73440205de252066233b1ee0f3085d51d2d + IOS XE 17.18.1 mapping (docs/YANG_PIN.md, docs/YANG_SLICE.md)

--- Excerpt 1: grouping vlan-switched-config, vendor/yang/openconfig/openconfig-vlan.yang ---
      "VLAN related configuration that is part of the physical
      Ethernet interface.";

    leaf interface-mode {
      type oc-vlan-types:vlan-mode-type;
      description
        "Set the interface to access or trunk mode for
        VLANs";
    }

    leaf native-vlan {
      when "../interface-mode = 'TRUNK'" {
        description
          "Native VLAN is valid for trunk mode interfaces";
      }
      type oc-vlan-types:vlan-id;
      description
        "Set the native VLAN id for untagged frames arriving on
        a trunk interface.  Tagged frames sent on an interface
        configured with a native VLAN should have their tags
        stripped prior to transmission. This configuration is only
        valid on a trunk interface.";
    }

    leaf access-vlan {
      when "../interface-mode = 'ACCESS'" {
        description
          "Access VLAN assigned to the interfaces";
      }
      type oc-vlan-types:vlan-id;
      description
        "Assign the access vlan to the access port.";
    }

    leaf-list trunk-vlans {
      when "../interface-mode = 'TRUNK'" {
        description
          "Allowed VLANs may be specified for trunk mode
          interfaces.";
      }
      type union {
        type oc-vlan-types:vlan-id;
        type oc-vlan-types:vlan-range;
      }
      description
        "Specify VLANs, or ranges thereof, that the interface may
        carry when in trunk mode.  If not specified, all VLANs are
        allowed on the interface. Ranges are specified in the form
        x..y, where x<y - ranges are assumed to be inclusive (such
        that the VLAN range is x <= range <= y.";
    }
  }

--- Excerpt 2: grouping vlan-config (VLAN list entry), vendor/yang/openconfig/openconfig-vlan.yang ---
  grouping vlan-config {
    description "VLAN configuration container.";

    leaf vlan-id {
      type oc-vlan-types:vlan-id;
      description "Interface VLAN id.";
    }

    leaf name {
      type string;
      description "Interface VLAN name.";
    }

    leaf status {
      type enumeration {
        enum ACTIVE {
          description "VLAN is active";
        }
        enum SUSPENDED {
          description "VLAN is inactive / suspended";
        }
      }
      default ACTIVE;
      description "Admin state of the VLAN";
    }


--- Excerpt 3: typedef vlan-id, vendor/yang/openconfig/openconfig-vlan-types.yang ---
  typedef vlan-id {
    type uint16 {
      range 1..4094;
    }
    description
      "Type definition representing a single-tagged VLAN";
  }

--- Excerpt 4: leaf enabled (interface config), vendor/yang/openconfig/openconfig-interfaces.yang ---
    leaf enabled {
      type boolean;
      default "true";
      description
        "This leaf contains the configured, desired state of the
        interface.

        Systems that implement the IF-MIB use the value of this
        leaf in the 'running' datastore to set
        IF-MIB.ifAdminStatus to 'up' or 'down' after an ifEntry
        has been initialized, as described in RFC 2863.

--- Slice notes (from docs/YANG_SLICE.md, part of the packet) ---
1. Hostname is NOT in this slice: it lives at openconfig-system /system/config/hostname, which is not vendored here. In this pilot hostname and interface aliases are context, not schema.
2. Finding F1: because of the when conditions above, an inactive-mode parameter (e.g. access-vlan on a TRUNK interface) cannot appear in a YANG instance of this slice.
3. Finding F2: OpenConfig has no encoding for trunk 'none' — an absent trunk-vlans list means ALL VLANs are allowed. This slice documents an empty list [] as 'none' by deviation.

Question about the OpenConfig YANG schema for VLAN/interface configuration on IOS XE: Can a schema-valid OpenConfig instance carry an access-vlan value for an interface whose interface-mode is TRUNK?
Answer in one to three sentences, then end with a line of the form: Citation: <module> / <path>
```
### Expected output
```
No. In openconfig-vlan, the access-vlan leaf carries a when condition requiring the sibling interface-mode to be 'ACCESS', so a schema-valid instance cannot carry access-vlan on a TRUNK interface.
Citation: openconfig-vlan / vlan-switched-config / access-vlan
```
### Scoring
Scoring: correct requires (a) the citation line to match the expected path (module + leaf, normalized), and (b) every expected key point present in the answer text (no / when / access, case-insensitive). This is the F1 finding from docs/YANG_SLICE.md turned into a question.

## Edit — item P-02 (source: pair-add-vlan20-canonical, view: json)

### Prompt (system)
```
You are a network configuration assistant being evaluated in a pilot. Follow the output format exactly. Do not add commentary outside the requested format.
```
### Prompt (user)
```
Device: sw-acc-1 (hostname switch1)
Interface aliases: GigabitEthernet1/0/48: uplink

Current state (OpenConfig YANG JSON instance):
{
  "openconfig-interfaces:interfaces": {
    "interface": [
      {
        "config": {
          "description": "Uplink to core",
          "enabled": true,
          "name": "GigabitEthernet1/0/48",
          "type": "iana-if-type:ethernetCsmacd"
        },
        "name": "GigabitEthernet1/0/48",
        "openconfig-if-ethernet:ethernet": {
          "openconfig-vlan:switched-vlan": {
            "config": {
              "interface-mode": "TRUNK",
              "native-vlan": 10,
              "trunk-vlans": [
                10,
                30
              ]
            }
          }
        }
      }
    ]
  },
  "openconfig-vlan:vlans": {
    "vlan": [
      {
        "config": {
          "name": "Users",
          "vlan-id": 10
        },
        "vlan-id": 10
      },
      {
        "config": {
          "name": "Engineering",
          "vlan-id": 20
        },
        "vlan-id": 20
      },
      {
        "config": {
          "name": "Voice",
          "vlan-id": 30
        },
        "vlan-id": 30
      }
    ]
  }
}

Schema evidence (pinned YANG excerpts):
SCHEMA PACKET — arm B evidence (pinned YANG excerpts, verbatim)
Slice: OpenConfig v5.11.0 @ 160da73440205de252066233b1ee0f3085d51d2d + IOS XE 17.18.1 mapping (docs/YANG_PIN.md, docs/YANG_SLICE.md)

--- Excerpt 1: grouping vlan-switched-config, vendor/yang/openconfig/openconfig-vlan.yang ---
      "VLAN related configuration that is part of the physical
      Ethernet interface.";

    leaf interface-mode {
      type oc-vlan-types:vlan-mode-type;
      description
        "Set the interface to access or trunk mode for
        VLANs";
    }

    leaf native-vlan {
      when "../interface-mode = 'TRUNK'" {
        description
          "Native VLAN is valid for trunk mode interfaces";
      }
      type oc-vlan-types:vlan-id;
      description
        "Set the native VLAN id for untagged frames arriving on
        a trunk interface.  Tagged frames sent on an interface
        configured with a native VLAN should have their tags
        stripped prior to transmission. This configuration is only
        valid on a trunk interface.";
    }

    leaf access-vlan {
      when "../interface-mode = 'ACCESS'" {
        description
          "Access VLAN assigned to the interfaces";
      }
      type oc-vlan-types:vlan-id;
      description
        "Assign the access vlan to the access port.";
    }

    leaf-list trunk-vlans {
      when "../interface-mode = 'TRUNK'" {
        description
          "Allowed VLANs may be specified for trunk mode
          interfaces.";
      }
      type union {
        type oc-vlan-types:vlan-id;
        type oc-vlan-types:vlan-range;
      }
      description
        "Specify VLANs, or ranges thereof, that the interface may
        carry when in trunk mode.  If not specified, all VLANs are
        allowed on the interface. Ranges are specified in the form
        x..y, where x<y - ranges are assumed to be inclusive (such
        that the VLAN range is x <= range <= y.";
    }
  }

--- Excerpt 2: grouping vlan-config (VLAN list entry), vendor/yang/openconfig/openconfig-vlan.yang ---
  grouping vlan-config {
    description "VLAN configuration container.";

    leaf vlan-id {
      type oc-vlan-types:vlan-id;
      description "Interface VLAN id.";
    }

    leaf name {
      type string;
      description "Interface VLAN name.";
    }

    leaf status {
      type enumeration {
        enum ACTIVE {
          description "VLAN is active";
        }
        enum SUSPENDED {
          description "VLAN is inactive / suspended";
        }
      }
      default ACTIVE;
      description "Admin state of the VLAN";
    }


--- Excerpt 3: typedef vlan-id, vendor/yang/openconfig/openconfig-vlan-types.yang ---
  typedef vlan-id {
    type uint16 {
      range 1..4094;
    }
    description
      "Type definition representing a single-tagged VLAN";
  }

--- Excerpt 4: leaf enabled (interface config), vendor/yang/openconfig/openconfig-interfaces.yang ---
    leaf enabled {
      type boolean;
      default "true";
      description
        "This leaf contains the configured, desired state of the
        interface.

        Systems that implement the IF-MIB use the value of this
        leaf in the 'running' datastore to set
        IF-MIB.ifAdminStatus to 'up' or 'down' after an ifEntry
        has been initialized, as described in RFC 2863.

--- Slice notes (from docs/YANG_SLICE.md, part of the packet) ---
1. Hostname is NOT in this slice: it lives at openconfig-system /system/config/hostname, which is not vendored here. In this pilot hostname and interface aliases are context, not schema.
2. Finding F1: because of the when conditions above, an inactive-mode parameter (e.g. access-vlan on a TRUNK interface) cannot appear in a YANG instance of this slice.
3. Finding F2: OpenConfig has no encoding for trunk 'none' — an absent trunk-vlans list means ALL VLANs are allowed. This slice documents an empty list [] as 'none' by deviation.

Request: Add VLAN 20 to the uplink

Output contract for Edit tasks — reply with ONE JSON object, one of:
- a plan: {"device": "<device>", "operation": "<op>", ...fields}
- a multi-step plan: {"device": "<device>", "steps": [<plan>, ...]} — one
  step per interface when a request targets several interfaces
- a clarify: {"type": "clarify", "reason": "<reason>", "question":
  "<question>", "candidates": ["<interface>", ...]}
- a no-op: {"type": "no_op"} — when the requested end-state already holds
Operations and their fields:
  create_vlan: vlan_id, name (optional)
  delete_vlan: vlan_id
  set_access_vlan: interface, vlan_id (the VLAN must already exist)
  add_trunk_vlans / remove_trunk_vlans / replace_trunk_vlans: interface,
    vlans (non-empty list of VLAN ids)
  set_native_vlan: interface, vlan_id
  set_port_mode: interface, mode ("access" or "trunk")
  set_admin_state: interface, admin ("up" or "down")
Clarify reasons: ambiguous_alias, unknown_interface, unknown_vlan,
missing_vlan_id, missing_interface, unsupported_remove_from_all. If a
name or alias matches more than one interface, answer clarify with
reason "ambiguous_alias" and the exact candidate list.
```
### Expected output
```
{
  "device": "sw-acc-1",
  "operation": "add_trunk_vlans",
  "interface": "GigabitEthernet1/0/48",
  "vlans": [
    20
  ]
}
```
### Scoring
The plan is validated with validate_output() and applied with apply_plan() from src/plan/ against the item's plan_state. Correct requires: validation passes; every assertion in the item's expected object holds after apply (here trunk_allowed becomes [10, 20, 30]); and diff_states() shows no changed path outside the asserted set (native_vlan stays 10 — preservation is graded, not assumed). A parse failure, a wrong operation (e.g. replace_trunk_vlans), or any extra change = incorrect, with the failure class recorded.
