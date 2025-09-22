# Identity Role

This Ansible role manages basic UNIX identity resources: groups, users, and SSH keys.

## Features

* Ensures required UNIX groups exist.
* Creates and manages user accounts with customizable attributes (UID, shell, groups, etc.).
* Installs SSH public keys for each user, with optional exclusivity (removing unmanaged keys).

## Variables

```yaml
# Groups to ensure exist
unix_groups:
  - devs
  - admins

# Users to manage
users:
  - name: alice
    uid: 1001
    groups: ["devs"]
    pubkeys:
      - "ssh-rsa AAAAB3..."
    exclusive_keys: true
  - name: bob
    state: absent   # remove user
```

## Usage

```yaml
- hosts: all
  roles:
    - role: unix_identity
```
