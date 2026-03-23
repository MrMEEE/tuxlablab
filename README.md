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

Copy `config/tuxlablab.conf.example` to `~/.config/tuxlablab/config.ini` and
adjust the values:

```bash
mkdir -p ~/.config/tuxlablab
cp config/tuxlablab.conf.example ~/.config/tuxlablab/config.ini
$EDITOR ~/.config/tuxlablab/config.ini
```

You can also point to a custom config file via the environment variable:

```bash
export TUXLABLAB_CONFIG=/path/to/config.ini
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
```

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
| GET | `/api/playbooks` | List playbooks |
| GET | `/api/health` | Health check |

