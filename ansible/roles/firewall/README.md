# Firewall Role

This Ansible role configures a simple **iptables-based firewall** with **secure defaults**:

- SSH (port 22) is **always allowed**.
- Loopback traffic and established/related connections are allowed.
- Default incoming policy: **DROP**
- Default outgoing policy: **ACCEPT** (configurable)
- You can extend with custom inbound (`fw_extra_rules`) and outbound (`fw_outbound_rules`) rules.

Supports persistence on RHEL (via `iptables-services`) and Debian/Ubuntu (via `iptables-persistent`).

---

## Requirements

- Ansible collections:
  ```bash
  ansible-galaxy collection install ansible.posix community.general
````

* Packages will be installed automatically:

  * RHEL/Rocky/Alma: `iptables`, `iptables-services`
  * Debian/Ubuntu: `iptables`, `iptables-persistent`

---

## Role Variables

### General

| Variable                   | Default  | Description                            |
| -------------------------- | -------- | -------------------------------------- |
| `fw_default_input_policy`  | `DROP`   | Default policy for INPUT chain         |
| `fw_default_output_policy` | `ACCEPT` | Default policy for OUTPUT chain        |
| `fw_allow_icmp`            | `false`  | Whether to allow ICMP (ping) inbound   |
| `fw_persist`               | `false`  | Whether to persist rules across reboot |

### Inbound rules

```yaml
fw_extra_rules: []
```

Each item is a dict:

* `proto`: tcp/udp/icmp
* `port`: port number (optional for icmp)
* `source`: CIDR or IP (optional, default: any)
* `comment`: description

Example:

```yaml
fw_extra_rules:
  - { proto: tcp, port: 80, comment: "Allow HTTP" }
  - { proto: tcp, port: 443, source: 10.0.0.0/8, comment: "Allow HTTPS from LAN" }
```

### Outbound rules

```yaml
fw_outbound_rules: []
```

Each item is a dict:

* `proto`: tcp/udp
* `port`: port number
* `dest`: destination CIDR or IP (optional, default: any)
* `comment`: description

Example:

```yaml
fw_outbound_rules:
  - { proto: udp, port: 53, dest: 0.0.0.0/0, comment: "Allow DNS" }
  - { proto: tcp, port: 443, comment: "Allow HTTPS outbound" }
```

---

## Usage

### Minimal example (only SSH allowed in)

```yaml
- hosts: all
  become: true
  roles:
    - role: firewall
```

### Web server example

```yaml
- hosts: web
  become: true
  roles:
    - role: firewall
      vars:
        fw_allow_icmp: true
        fw_extra_rules:
          - { proto: tcp, port: 80, comment: "HTTP" }
          - { proto: tcp, port: 443, comment: "HTTPS" }
```

### Strict egress policy (only DNS + NTP + HTTPS)

```yaml
- hosts: app
  become: true
  roles:
    - role: firewall
      vars:
        fw_default_output_policy: DROP
        fw_outbound_rules:
          - { proto: udp, port: 53, dest: 0.0.0.0/0, comment: "DNS" }
          - { proto: udp, port: 123, dest: 0.0.0.0/0, comment: "NTP" }
          - { proto: tcp, port: 443, dest: 0.0.0.0/0, comment: "HTTPS" }
        fw_persist: true
```

---

## Notes

* Order matters: SSH and loopback rules are applied first to avoid lockouts.
* If you set `fw_default_output_policy: DROP`, remember to allow DNS/NTP/HTTPS as needed.
* Persistence is optional (`fw_persist: true`) and OS-family aware.
* For IPv6, you’ll need to extend this role with `ip6tables` tasks or migrate to `nftables`.
