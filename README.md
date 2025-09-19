# Vagrant + KVM (libvirt) Setup

## 1) Install prerequisites

**Ubuntu/Debian**

```bash
sudo apt update
sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils \
  virt-manager qemu-utils ebtables dnsmasq-base

# Optional but recommended
sudo apt install -y vagrant
```

**Add your user to groups (no sudo for vagrant later):**

```bash
sudo usermod -aG libvirt,kvm $USER
# Apply group change without reboot (or log out/in):
newgrp libvirt
```

**Enable the libvirt service:**

```bash
sudo systemctl enable --now libvirtd
virsh list --all  # should run without errors
```

---

## 2) Install the libvirt provider plugin

```bash
vagrant plugin install vagrant-libvirt
vagrant plugin list  # should show vagrant-libvirt
```

> ⚠️ Don’t run `vagrant` with `sudo`. The plugin installs for **your user**, not root.

---

## 3) Choose a libvirt-compatible box

Boxes are **provider-specific**. Pick one that lists `libvirt` support on Vagrant Cloud.

Recommended official, minimal images:

```bash
# Ubuntu 22.04 (Jammy)
vagrant box add generic/ubuntu2204 --provider=libvirt

# Ubuntu 24.04 (Noble)
vagrant box add generic/ubuntu2404 --provider=libvirt
```

---

## 4) Minimal `Vagrantfile` (single VM)

```ruby
# Vagrantfile
Vagrant.configure("2") do |config|
  config.vm.box = "generic/ubuntu2404"

  config.vm.provider :libvirt do |lv|
    lv.cpus   = 2
    lv.memory = 2048
    # lv.uri = "qemu:///system"  # default; usually fine
  end

  # Optional: NAT + private network with static IP
  # config.vm.network "private_network", ip: "192.168.121.10" # libvirt default NAT pool
end
```

Bring it up:

```bash
vagrant up --provider=libvirt
vagrant ssh
```

Destroy when done:

```bash
vagrant destroy -f
```

---

## 5) Multi-machine example (db, app, db2)

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "generic/ubuntu2204"

  machines = {
    "db"  => { ip: "192.168.121.20", ram: 2048, cpus: 2 },
    "app" => { ip: "192.168.121.21", ram: 2048, cpus: 2 },
    "db2" => { ip: "192.168.121.22", ram: 2048, cpus: 2 },
  }

  machines.each do |name, spec|
    config.vm.define name do |m|
      m.vm.hostname = "#{name}.local"

      # Private NAT network (default libvirt network is 192.168.121.0/24)
      m.vm.network "private_network", ip: spec[:ip]

      m.vm.provider :libvirt do |lv|
        lv.cpus   = spec[:cpus]
        lv.memory = spec[:ram]
        # lv.machine_type = "q35"       # optional
        # lv.nested       = true        # enable nested virt if your CPU supports it
      end

      # Provisioning hook (swap for Ansible if you prefer)
      m.vm.provision "shell", inline: <<~SHELL
        sudo apt-get update -y
        echo "Hello from #{name}" | sudo tee /etc/motd
      SHELL
    end
  end
end
```

**Usage**

```bash
vagrant up --provider=libvirt          # all machines
vagrant status
vagrant ssh app
```

---

## 6) Networking notes

* **Default**: NAT via libvirt’s `default` network (`192.168.121.0/24`). VMs can reach the internet; host can reach VMs via the private IPs.

* **Bridged (LAN-visible)**:

  ```ruby
  # Example: attach a bridged NIC to app
  # m.vm.network "public_network", bridge: "enp3s0"
  ```

  Use when you need your VMs to be reachable by other hosts on your LAN.

* **Host-only alt**: less common with libvirt (NAT private network usually covers the use case).

---

## 7) Common pitfalls & fixes

* **“Provider 'libvirt' could not be found”**
  You ran `sudo vagrant up`. Don’t. Plugins are user-scoped. Run without sudo.

* **“The box … doesn’t support the provider requested (libvirt)”**
  You used a VirtualBox-only box (e.g., `ubuntu/jammy64`). Switch to a libvirt box (e.g., `generic/ubuntu2204`), or (not recommended) convert with:

  ```bash
  vagrant plugin install vagrant-mutate
  vagrant box add ubuntu/jammy64 --provider=virtualbox
  vagrant mutate ubuntu/jammy64 libvirt
  ```

* **`[fog][WARNING] Unrecognized arguments: libvirt_ip_command`**
  Harmless. Remove any old `libvirt_ip_command` options from your `Vagrantfile`.

* **Permission denied connecting to hypervisor**
  Ensure you’re in `libvirt` and `kvm` groups, then re-login or `newgrp libvirt`.

* **Libvirt network missing**

  ```bash
  sudo virsh net-start default
  sudo virsh net-autostart default
  ```

---

## Quick cheatsheet

```bash
vagrant box list
vagrant status
vagrant halt              # stop VMs
vagrant reload            # reboot + apply Vagrantfile changes
vagrant destroy -f        # delete VMs
```

