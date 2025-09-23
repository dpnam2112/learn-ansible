# Firewall Role (nftables)

This Ansible role configures a **modern nftables firewall** with safe defaults:

- IPv4 + IPv6 in one ruleset (`table inet`).
- Default policies: **INPUT → DROP**, **OUTPUT → ACCEPT**.
- Baseline allows: loopback, `ESTABLISHED,RELATED`, SSH.
- ICMP/ICMPv6 allowed by default (configurable).
- Extend with inbound/outbound rules. Each rule supports `action: accept | reject | drop`.

---

## Requirements

- Package: `nftables`  
- Run playbook with `become: true`

---

## Variables

```yaml
fw_default_input_policy:  DROP       # default INPUT policy
fw_default_output_policy: ACCEPT     # default OUTPUT policy
fw_allow_icmp:            true       # allow ICMPv4 + ICMPv6
fw_inbound_rules:  []                # list of inbound rules
fw_outbound_rules: []                # list of outbound rules
fw_nft_conf_path: /etc/nftables.conf # persisted config
````

### Rule schema

**Inbound rules**

```yaml
fw_inbound_rules:
  - { proto: tcp, port: 22, action: accept, comment: "SSH" }
  - { proto: tcp, port: 80, action: accept, comment: "HTTP" }
  - { proto: tcp, port: 23, action: reject, comment: "No Telnet" }
  - { proto: icmp, action: drop, comment: "Drop ping" }
  - { proto: tcp, port: 443, src: 10.0.0.0/8, action: accept, comment: "HTTPS from LAN" }
```

**Outbound rules**

```yaml
fw_outbound_rules:
  - { proto: udp, port: 53,  action: accept, comment: "DNS" }
  - { proto: udp, port: 123, action: accept, comment: "NTP" }
  - { proto: tcp, port: 443, action: accept, comment: "HTTPS" }
  - { proto: tcp, port: 25,  action: reject, comment: "Block SMTP" }
```

---

## Usage Examples

**Minimal (just SSH allowed in)**

```yaml
- hosts: all
  become: true
  roles:
    - role: firewall
```

**Web server**

```yaml
- hosts: web
  become: true
  roles:
    - role: firewall
      vars:
        fw_inbound_rules:
          - { proto: tcp, port: 80,  action: accept, comment: "HTTP" }
          - { proto: tcp, port: 443, action: accept, comment: "HTTPS" }
```

**Strict egress (only DNS, NTP, HTTPS)**

```yaml
- hosts: app
  become: true
  roles:
    - role: firewall
      vars:
        fw_default_output_policy: DROP
        fw_outbound_rules:
          - { proto: udp, port: 53,  action: accept, comment: "DNS" }
          - { proto: udp, port: 123, action: accept, comment: "NTP" }
          - { proto: tcp, port: 443, action: accept, comment: "HTTPS" }
```
