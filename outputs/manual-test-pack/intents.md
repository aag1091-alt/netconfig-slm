# Manual test pack — intents (held-out v1.0 test split)

12 held-out intents, 2 per family. Paste each intent to the model and
compare its config against the matching entry in `answers.md`.

## 1. bgp-0763 (bgp)

Set up eBGP on r4 in AS 64607 (router-id 10.255.25.153) — peer with 192.0.2.155 in AS 64953 ('Regional ISP') and peer with 192.0.2.73 in AS 64884 ('Tier-1 transit') kept administratively shut down — advertising 10.96.0.0/16 and 10.238.0.0/16.

## 2. bgp-0238 (bgp)

Set up eBGP on br2 in AS 64515 (router-id 10.255.244.169) — peer with 203.0.113.77 in AS 65220 ('ISP uplink') and peer with 192.0.2.160 in AS 64582 ('Regional transit') — advertising 10.104.0.0/16, 192.168.73.0/24, 10.197.116.0/22, and 10.38.128.0/20, and set maximum-prefix 10000 on peer 192.0.2.160.

## 3. iface-0133 (interface_mgmt)

Configure GigabitEthernet0/0/0 with description 'Link to core switch' and IP 10.16.117.1/30, bring it up; Configure GigabitEthernet0/1/0 with description 'DMZ interface' and IP 10.177.69.1/24, bring it up; Configure GigabitEthernet0/1/1 with description 'Server segment' and IP 10.195.96.1/30, bring it up; Configure GigabitEthernet0/0/2 with description 'LAN gateway' and IP 10.89.49.1/25, bring it up; Enable SSH management: domain name example.org, SSH version 2, and VTY lines 0 to 4 for SSH-only login with a 5-minute timeout — all on r5.

## 4. iface-0605 (interface_mgmt)

r2 management configuration: Configure GigabitEthernet0/0/2 with description 'Internet edge' and IP 10.191.152.1/30, bring it up; Configure GigabitEthernet0/1/1 with description 'Link to core switch' and IP 10.3.38.1/30 plus secondary IP 192.168.158.1/24, bring it up.

## 5. ospf-0249 (ospf)

Run OSPF process 1 on wan-rtr1 with router-id 10.255.61.183; network 172.30.203.0/24 in area 0; network 10.73.0.0/22 in area 0; network 172.31.59.0/28 in area 0; network 172.27.134.0/24 in area 0; GigabitEthernet0/0/0 (10.0.157.1/30) in area 0.

## 6. ospf-0478 (ospf)

r6: OSPF 1, router-id 10.255.13.161; network 172.25.86.0/24 in area 0; network 10.162.169.0/24 in area 0; network 10.226.5.0/30 in area 0; GigabitEthernet0/0/1 (10.0.232.1/30) in area 0 as point-to-point; GigabitEthernet0/1/1 (10.0.184.1/30) in area 0; GigabitEthernet0/0/3 (10.0.161.1/30) in area 0; with GigabitEthernet0/0/0 passive.

## 7. static-0771 (static_route)

NOC ticket: campus-rtr1 needs these static routes: 192.168.94.0/26 via GigabitEthernet0/0/1 10.0.100.2 (interface IP 10.0.100.1/30); 172.18.194.0/29 via GigabitEthernet0/1/0 10.0.166.2 (interface IP 10.0.166.1/30); 10.213.220.0/26 via 10.145.77.248; 172.21.96.0/20 via 10.64.204.141; 10.249.0.0/16 via 192.0.2.132; 172.25.132.0/23 via 198.51.100.111; 192.168.64.0/20 via 192.0.2.120; 172.29.0.0/16 via 10.1.33.213; 192.168.94.0/26 via 192.0.2.111 as a floating backup with AD 250; 172.29.0.0/16 via 10.124.232.74 as a floating backup with AD 100; a default route out GigabitEthernet0/0/1 (interface IP 10.0.100.1/30).

## 8. static-0220 (static_route)

Routing table on soho-rtr1: 192.168.220.0/22 via 10.185.88.52; 192.168.38.0/24 via 203.0.113.41; 10.108.17.0/24 via 10.80.21.158; 10.56.60.2/32 via 198.51.100.84; 192.168.38.0/24 via 198.51.100.211 as a floating backup with AD 250; default via 10.184.41.41.

## 9. trunk-0475 (trunk_svi)

Configure on soho-sw1: GigabitEthernet1/0/3 is a trunk permitting VLANs 82,98,99,106,107,118,122,134,146,161,162,168; TenGigabitEthernet1/0/1 is a trunk permitting VLANs 113,121,138,151; gateway for VLAN 82 at 172.18.113.1/26; gateway for VLAN 106 at 10.106.86.1/23; gateway for VLAN 118 at 192.168.27.1/27; gateway for VLAN 134 at 10.134.19.1/25.

## 10. trunk-0174 (trunk_svi)

Configure Make GigabitEthernet1/0/1 an 802.1Q trunk, allowed VLANs 170 on dist1, and create the SVI for VLAN 170 with IP 10.170.113.1/24.

## 11. vlan-0539 (vlan_l2)

Ticket: Create VLAN 374 named Facilities, VLAN 746 named VoiceMail, and VLAN 663 named QA on dist-sw1. VLAN 374 gets ports GigabitEthernet3/0/2, GigabitEthernet3/0/24, and GigabitEthernet3/0/19. VLAN 663 gets ports GigabitEthernet3/0/4, GigabitEthernet3/0/14, and GigabitEthernet3/0/3. VLAN 746 gets ports GigabitEthernet3/0/13. Bring all access ports up.

## 12. vlan-0720 (vlan_l2)

Set up VLAN 193 named Floor2, VLAN 914 named AV, and VLAN 209 named HR on lab-sw1, with Assign GigabitEthernet2/0/9, GigabitEthernet2/0/47, and GigabitEthernet2/0/45 to VLAN 193; Assign GigabitEthernet2/0/44 to VLAN 209; Assign GigabitEthernet2/0/40 to VLAN 914.
