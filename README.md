# tuxlablab
Python-based VM administration – a full-featured port of [laptop-lab](https://github.com/MrMEEE/laptop-lab)

## Features

* **Create** KVM/QEMU VMs from distribution images with one command
* **List** all VMs (running and stopped) at a glance
* **Start / Stop** existing VMs
* **Remove** VMs and their disk images
* **Run Ansible playbooks** on any VM
* **Distribution system** – define reusable VM templates with `.dist` files
* **REST API** – full JSON API served by FastAPI (with auto-generated Swagger docs at `/docs`)
* **Web interface** – dashboard, VM detail pages, and creation form
* **CLI** – `vm` command mirroring the original laptop-lab bash script

## Requirements

* Python 3.9+
* KVM/libvirt (`libvirtd` running)
* `virt-sysprep` / `guestfs-tools` (for image preparation)
* `ansible` / `ansible-playbook` (for post-creation setup)

## Installation

```bash
pip install -e .
# or
pip install -r requirements.txt
```

## Configuration

Runtime settings are stored in the SQLite database and can be updated from:

* CLI: `tuxlablab settings`
* Web UI: `/settings`
* REST API: `/api/settings`

Database location:

* Set `TUXLABLAB_DB` to an absolute path to control where the SQLite DB is stored.
* If unset, default path is `${XDG_DATA_HOME:-~/.local/share}/tuxlablab/tuxlablab.db`.

Examples:

```bash
# list all settings
tuxlablab settings

# read one value
tuxlablab settings labdomain

# update a value
tuxlablab settings labdomain mylab.lan
```

## CLI usage

```bash
# Create a VM (2 vCPUs, 2048 MB RAM, default distribution)
vm create test123 --cpus 2 --memory 2048

# Create a VM with a specific distribution
vm create apache --cpus 2 --memory 2048 --dist apache

# List all VMs
vm list

# Start / stop a VM
vm start test123
vm stop test123

# Run an Ansible playbook on a VM
vm playbook test123 apache.yml

# Remove a VM (including its disk)
vm remove test123

# List available distributions
vm distributions

# Start the web/API server
vm server
# or
tuxlablab-server

# Install/start a user systemd service (with linger)
vm service-install

# Remove the user systemd service
vm service-uninstall
```

### User systemd service

Use the CLI to manage a user-level systemd unit:

```bash
# install + start and try to enable linger for current user
tuxlablab service-install

# custom unit name
tuxlablab service-install --name tuxlablab-web

# remove unit and stop service
tuxlablab service-uninstall

# optionally disable linger while uninstalling
tuxlablab service-uninstall --disable-linger
```

The unit file is created under `~/.config/systemd/user/` and runs
`python -m tuxlablab.api` using your current Python environment.

## Web interface

Start the server and open `http://localhost:8080` in your browser:

```bash
vm server
```

The interactive Swagger API documentation is available at `http://localhost:8080/docs`.

## Distributions

Create `.dist` files in `~/ansible/localdc/distributions/`:

```bash
# centos9.dist
DISTNAME="CentOS Stream 9"
DISTFILE="CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2"
DISTPLAYBOOKS=""
```

To set a default distribution create a symlink:

```bash
cd ~/ansible/localdc/distributions
ln -s centos9.dist default
```

Distribution templates are provided in the `distributions/` directory of this
repository.

## Directory layout

```
~/ansible/localdc/
├── images/          # Base qcow2 images
├── vms/             # Per-VM qcow2 disk copies
├── distributions/   # *.dist definition files  (+ optional 'default' symlink)
├── playbooks/       # Ansible playbooks
└── inventories/     # Auto-generated per-VM inventory files
```

## REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/vms` | List all VMs |
| GET | `/api/vms/{name}` | Get VM details |
| POST | `/api/vms` | Create VM (async) |
| POST | `/api/vms/stream` | Create VM with SSE progress stream |
| POST | `/api/vms/{name}/start` | Start VM |
| POST | `/api/vms/{name}/stop` | Stop VM |
| DELETE | `/api/vms/{name}` | Remove VM |
| POST | `/api/vms/{name}/playbook` | Run playbook on VM |
| GET | `/api/distributions` | List distributions |
| GET | `/api/settings` | List settings |
| GET | `/api/settings/{key}` | Get setting |
| PUT | `/api/settings/{key}` | Set setting |
| GET | `/api/playbooks` | List playbooks |
| GET | `/api/health` | Health check |

