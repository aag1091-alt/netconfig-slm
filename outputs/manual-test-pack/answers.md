# Manual test pack — reference configs (answer key)

Kept separate from `intents.md` on purpose. Do not paste these to the model.

## 1. bgp-0763 (bgp)

```
hostname router4
!
router bgp 64607
 bgp router-id 10.255.25.153
 bgp log-neighbor-changes
 neighbor 192.0.2.155 remote-as 64953
 neighbor 192.0.2.155 description Regional ISP
 neighbor 192.0.2.73 remote-as 64884
 neighbor 192.0.2.73 description Tier-1 transit
 neighbor 192.0.2.73 shutdown
 !
 address-family ipv4
  network 10.96.0.0 mask 255.255.0.0
  network 10.238.0.0 mask 255.255.0.0
  neighbor 192.0.2.155 activate
 exit-address-family
```

## 2. bgp-0238 (bgp)

```
hostname br2
!
router bgp 64515
 bgp router-id 10.255.244.169
 bgp log-neighbor-changes
 neighbor 203.0.113.77 remote-as 65220
 neighbor 203.0.113.77 description ISP uplink
 neighbor 192.0.2.160 remote-as 64582
 neighbor 192.0.2.160 description Regional transit
 !
 address-family ipv4
  network 10.104.0.0 mask 255.255.0.0
  network 192.168.73.0 mask 255.255.255.0
  network 10.197.116.0 mask 255.255.252.0
  network 10.38.128.0 mask 255.255.240.0
  neighbor 203.0.113.77 activate
  neighbor 192.0.2.160 activate
  neighbor 192.0.2.160 maximum-prefix 10000
 exit-address-family
```

## 3. iface-0133 (interface_mgmt)

```
hostname router5
!
interface GigabitEthernet0/0/0
 description Link to core switch
 ip address 10.16.117.1 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/1/0
 description DMZ interface
 ip address 10.177.69.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1/1
 description Server segment
 ip address 10.195.96.1 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/0/2
 description LAN gateway
 ip address 10.89.49.1 255.255.255.128
 no shutdown
!
ip domain-name example.org
ip ssh version 2
!
line vty 0 4
 exec-timeout 5 0
 login local
 transport input ssh
```

## 4. iface-0605 (interface_mgmt)

```
hostname router2
!
interface GigabitEthernet0/0/2
 description Internet edge
 ip address 10.191.152.1 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/1/1
 description Link to core switch
 ip address 10.3.38.1 255.255.255.252
 ip address 192.168.158.1 255.255.255.0 secondary
 no shutdown
!
```

## 5. ospf-0249 (ospf)

```
hostname wan-rtr1
!
interface GigabitEthernet0/0/0
 description OSPF link area 0
 ip address 10.0.157.1 255.255.255.252
 ip ospf 1 area 0
 no shutdown
!
router ospf 1
 router-id 10.255.61.183
 network 172.30.203.0 0.0.0.255 area 0
 network 10.73.0.0 0.0.3.255 area 0
 network 172.31.59.0 0.0.0.15 area 0
 network 172.27.134.0 0.0.0.255 area 0
```

## 6. ospf-0478 (ospf)

```
hostname router6
!
interface GigabitEthernet0/0/1
 description OSPF link area 0
 ip address 10.0.232.1 255.255.255.252
 ip ospf 1 area 0
 ip ospf network point-to-point
 no shutdown
!
interface GigabitEthernet0/1/1
 description OSPF link area 0
 ip address 10.0.184.1 255.255.255.252
 ip ospf 1 area 0
 no shutdown
!
interface GigabitEthernet0/0/3
 description OSPF link area 0
 ip address 10.0.161.1 255.255.255.252
 ip ospf 1 area 0
 no shutdown
!
router ospf 1
 router-id 10.255.13.161
 network 172.25.86.0 0.0.0.255 area 0
 network 10.162.169.0 0.0.0.255 area 0
 network 10.226.5.0 0.0.0.3 area 0
 passive-interface GigabitEthernet0/0/0
```

## 7. static-0771 (static_route)

```
hostname campus-rtr1
!
interface GigabitEthernet0/0/1
 description Internet edge
 ip address 10.0.100.1 255.255.255.252
 no shutdown
!
interface GigabitEthernet0/1/0
 description Branch link
 ip address 10.0.166.1 255.255.255.252
 no shutdown
!
ip route 192.168.94.0 255.255.255.192 GigabitEthernet0/0/1 10.0.100.2
ip route 172.18.194.0 255.255.255.248 GigabitEthernet0/1/0 10.0.166.2
ip route 10.213.220.0 255.255.255.192 10.145.77.248
ip route 172.21.96.0 255.255.240.0 10.64.204.141
ip route 10.249.0.0 255.255.0.0 192.0.2.132
ip route 172.25.132.0 255.255.254.0 198.51.100.111
ip route 192.168.64.0 255.255.240.0 192.0.2.120
ip route 172.29.0.0 255.255.0.0 10.1.33.213
ip route 192.168.94.0 255.255.255.192 192.0.2.111 250
ip route 172.29.0.0 255.255.0.0 10.124.232.74 100
ip route 0.0.0.0 0.0.0.0 GigabitEthernet0/0/1
```

## 8. static-0220 (static_route)

```
hostname soho-rtr1
!
ip route 192.168.220.0 255.255.252.0 10.185.88.52
ip route 192.168.38.0 255.255.255.0 203.0.113.41
ip route 10.108.17.0 255.255.255.0 10.80.21.158
ip route 10.56.60.2 255.255.255.255 198.51.100.84
ip route 192.168.38.0 255.255.255.0 198.51.100.211 250
ip route 0.0.0.0 0.0.0.0 10.184.41.41
```

## 9. trunk-0475 (trunk_svi)

```
hostname soho-sw1
!
interface GigabitEthernet1/0/3
 description Trunk uplink to core
 switchport mode trunk
 switchport trunk allowed vlan 82,98,99,106,107,118,122,134,146,161,162,168
 no shutdown
!
interface TenGigabitEthernet1/0/1
 switchport mode trunk
 switchport trunk allowed vlan 113,121,138,151
 no shutdown
!
interface Vlan82
 ip address 172.18.113.1 255.255.255.192
 no shutdown
!
interface Vlan106
 ip address 10.106.86.1 255.255.254.0
 no shutdown
!
interface Vlan118
 description SVI gateway
 ip address 192.168.27.1 255.255.255.224
 no shutdown
!
interface Vlan134
 ip address 10.134.19.1 255.255.255.128
 no shutdown
!
```

## 10. trunk-0174 (trunk_svi)

```
hostname dist1
!
interface GigabitEthernet1/0/1
 description Trunk to distribution
 switchport mode trunk
 switchport trunk allowed vlan 170
 no shutdown
!
interface Vlan170
 ip address 10.170.113.1 255.255.255.0
 no shutdown
!
```

## 11. vlan-0539 (vlan_l2)

```
hostname dist-sw1
!
vlan 374
 name Facilities
!
vlan 746
 name VoiceMail
!
vlan 663
 name QA
!
interface GigabitEthernet3/0/2
 description Camera drop
 switchport mode access
 switchport access vlan 374
 no shutdown
!
interface GigabitEthernet3/0/24
 description Camera drop
 switchport mode access
 switchport access vlan 374
 no shutdown
!
interface GigabitEthernet3/0/19
 description Camera drop
 switchport mode access
 switchport access vlan 374
 no shutdown
!
interface GigabitEthernet3/0/13
 switchport mode access
 switchport access vlan 746
 no shutdown
!
interface GigabitEthernet3/0/4
 switchport mode access
 switchport access vlan 663
 no shutdown
!
interface GigabitEthernet3/0/14
 switchport mode access
 switchport access vlan 663
 no shutdown
!
interface GigabitEthernet3/0/3
 switchport mode access
 switchport access vlan 663
 no shutdown
!
```

## 12. vlan-0720 (vlan_l2)

```
hostname lab-sw1
!
vlan 193
 name Floor2
!
vlan 914
 name AV
!
vlan 209
 name HR
!
interface GigabitEthernet2/0/9
 switchport mode access
 switchport access vlan 193
 no shutdown
!
interface GigabitEthernet2/0/47
 switchport mode access
 switchport access vlan 193
 no shutdown
!
interface GigabitEthernet2/0/45
 switchport mode access
 switchport access vlan 193
 no shutdown
!
interface GigabitEthernet2/0/40
 description Desk port
 switchport mode access
 switchport access vlan 914
 no shutdown
!
interface GigabitEthernet2/0/44
 description Checkout counter
 switchport mode access
 switchport access vlan 209
 no shutdown
!
```
