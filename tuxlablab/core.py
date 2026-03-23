"""Core VM management logic for tuxlablab.

Wraps libvirt-python for domain lifecycle operations and subprocess calls
for virt-sysprep and ansible-playbook, mirroring the feature-set of the
original laptop-lab ``vm`` bash script.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

try:
    import libvirt  # type: ignore
except ImportError:  # pragma: no cover
    libvirt = None  # type: ignore – allow import without libvirt for testing

from tuxlablab.config import Config, config as _global_config

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VMInfo:
    name: str
    state: str          # "running" | "stopped" | "paused" | "unknown"
    vcpus: int = 0
    memory_mb: int = 0
    disks: list[str] = field(default_factory=list)


@dataclass
class Distribution:
    name: str           # short name used on CLI, e.g. "centos9"
    display_name: str   # human-readable label
    image_file: str     # qcow2 filename inside images/
    playbooks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LIBVIRT_STATES = {
    0: "unknown",
    1: "running",
    2: "blocked",
    3: "paused",
    4: "stopped",   # shutting down
    5: "stopped",   # shut off
    6: "crashed",
    7: "stopped",   # suspended (PM)
}


def _libvirt_state_str(state_int: int) -> str:
    return _LIBVIRT_STATES.get(state_int, "unknown")


def _vm_xml(
    name: str,
    image_path: str,
    vcpus: int,
    ram_mb: int,
    network: str = "default",
) -> str:
    """Return libvirt domain XML for a new KVM VM."""
    return f"""<domain type='kvm'>
  <name>{name}</name>
  <memory unit='MiB'>{ram_mb}</memory>
  <vcpu placement='static'>{vcpus}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-5.2'>hvm</type>
    <boot dev='hd'/>
  </os>
  <cpu mode='host-model' check='none'/>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{image_path}'/>
      <target dev='vda' bus='virtio'/>
      <address type='pci' domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
    </disk>
    <interface type='network'>
      <source network='{network}'/>
      <model type='virtio'/>
      <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
    </interface>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
      <address type='virtio-serial' controller='0' bus='0' port='1'/>
    </channel>
    <input type='tablet' bus='usb'>
      <address type='usb' bus='0' port='1'/>
    </input>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <graphics type='spice' autoport='yes'>
      <listen type='address'/>
      <image compression='off'/>
    </graphics>
    <video>
      <model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1' primary='yes'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x0'/>
    </video>
    <memballoon model='virtio'>
      <address type='pci' domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
    </memballoon>
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
      <address type='pci' domain='0x0000' bus='0x07' slot='0x00' function='0x0'/>
    </rng>
  </devices>
</domain>"""


def _parse_dist_file(path: Path) -> Distribution | None:
    """Parse a bash-style ``.dist`` file into a :class:`Distribution`."""
    data: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                data[key.strip()] = val.strip().strip('"')
    except OSError:
        return None
    image_file = data.get("DISTFILE", "")
    if not image_file:
        return None
    playbooks_raw = data.get("DISTPLAYBOOKS", "")
    playbooks = [p for p in playbooks_raw.split() if p]
    return Distribution(
        name=path.stem,       # e.g. "centos9" from "centos9.dist"
        display_name=data.get("DISTNAME", path.stem),
        image_file=image_file,
        playbooks=playbooks,
    )


# ---------------------------------------------------------------------------
# VMManager
# ---------------------------------------------------------------------------


class VMManagerError(Exception):
    pass


class VMManager:
    """High-level interface for KVM/libvirt VM management."""

    def __init__(self, cfg: Config | None = None) -> None:
        self._cfg = cfg or _global_config
        self._conn: "libvirt.virConnect | None" = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> "libvirt.virConnect":
        if libvirt is None:
            raise VMManagerError("libvirt-python is not installed")
        if self._conn is None or self._conn.isAlive() == 0:
            self._conn = libvirt.open(self._cfg.libvirt_uri)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # VM listing
    # ------------------------------------------------------------------

    def list_vms(self) -> list[VMInfo]:
        """Return all defined VMs with their current state."""
        conn = self._connect()
        vms: list[VMInfo] = []
        for dom in conn.listAllDomains():
            state_int, _ = dom.state()
            state_str = _libvirt_state_str(state_int)
            info = VMInfo(
                name=dom.name(),
                state=state_str,
                vcpus=dom.maxVcpus(),
                memory_mb=dom.maxMemory() // 1024,
                disks=self._get_disk_paths(dom),
            )
            vms.append(info)
        vms.sort(key=lambda v: v.name)
        return vms

    def get_vm(self, hostname: str) -> VMInfo | None:
        fqdn = self._cfg.full_hostname(hostname)
        for vm in self.list_vms():
            if vm.name in (hostname, fqdn):
                return vm
        return None

    def _get_disk_paths(self, dom: "libvirt.virDomain") -> list[str]:
        paths: list[str] = []
        try:
            xml_str = dom.XMLDesc()
            root = ET.fromstring(xml_str)
            for disk in root.findall(".//disk[@device='disk']"):
                source = disk.find("source")
                if source is not None:
                    src = source.get("file") or source.get("dev") or ""
                    if src:
                        paths.append(src)
        except Exception:
            pass
        return paths

    # ------------------------------------------------------------------
    # Create VM
    # ------------------------------------------------------------------

    def create_vm(
        self,
        hostname: str,
        vcpus: int,
        ram_mb: int,
        distribution: str | None = None,
        output_lines: list[str] | None = None,
    ) -> None:
        """Create and start a new VM, running distribution playbooks when done.

        ``output_lines`` is an optional list that receives progress messages
        so callers (API streaming) can surface them.
        """

        def emit(msg: str) -> None:
            if output_lines is not None:
                output_lines.append(msg)

        cfg = self._cfg
        cfg.ensure_directories()
        fqdn = cfg.full_hostname(hostname)

        # Check not already existing
        if self.get_vm(fqdn) is not None:
            raise VMManagerError(f"VM '{fqdn}' already exists. Won't override.")

        # Resolve distribution
        dist = self._resolve_distribution(distribution)
        emit(f"Deploying {dist.display_name}")

        # Locate image
        src_image = cfg.images_dir / dist.image_file
        if not src_image.exists():
            raise VMManagerError(
                f"Image '{dist.image_file}' not found in {cfg.images_dir}. "
                "Please download it first."
            )

        vm_image = cfg.vms_dir / f"{fqdn}.qcow2"
        shutil.copy2(src_image, vm_image)
        vm_image.chmod(0o777)

        # Prepare image with virt-sysprep
        emit(f"Preparing image for {fqdn} ...")
        self._virt_sysprep(fqdn, vm_image)

        # Define and start VM via libvirt
        emit(f"Defining and starting VM {fqdn} ...")
        conn = self._connect()
        xml = _vm_xml(
            name=fqdn,
            image_path=str(vm_image),
            vcpus=vcpus,
            ram_mb=ram_mb,
        )
        dom = conn.createXML(xml, 0)
        if dom is None:
            raise VMManagerError("Failed to create VM domain.")

        # Persist definition so it survives reboots
        conn.defineXML(xml)

        # Write inventory
        inv_path = cfg.inventories_dir / f"inventory-{fqdn}"
        inv_path.write_text(
            f"{fqdn} ansible_user=root\n"
        )

        # Wait for SSH
        emit(f"Waiting for {fqdn} to come up ...")
        self._wait_for_ssh(fqdn, emit)
        emit(f"{fqdn} is up, initiating setup.")

        # Run distribution playbooks
        for pb in dist.playbooks:
            pb_path = cfg.playbooks_dir / pb
            if not pb_path.exists():
                emit(f"WARNING: playbook '{pb}' not found, skipping.")
                continue
            emit(f"Running playbook: {pb_path}")
            self._run_ansible(fqdn, str(pb_path), emit)

    # ------------------------------------------------------------------
    # Remove VM
    # ------------------------------------------------------------------

    def remove_vm(self, hostname: str) -> None:
        fqdn = self._cfg.full_hostname(hostname)
        conn = self._connect()
        try:
            dom = conn.lookupByName(fqdn)
        except Exception:
            raise VMManagerError(f"VM '{fqdn}' does not exist.")

        disks = self._get_disk_paths(dom)

        # Stop if running
        state_int, _ = dom.state()
        if _libvirt_state_str(state_int) == "running":
            dom.destroy()

        dom.undefine()

        # Remove disk files
        for disk in disks:
            try:
                Path(disk).unlink(missing_ok=True)
            except OSError:
                pass

        # Remove inventory
        inv = self._cfg.inventories_dir / f"inventory-{fqdn}"
        inv.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start_vm(self, hostname: str) -> None:
        fqdn = self._cfg.full_hostname(hostname)
        conn = self._connect()
        try:
            dom = conn.lookupByName(fqdn)
        except Exception:
            raise VMManagerError(f"VM '{fqdn}' does not exist.")
        state_int, _ = dom.state()
        if _libvirt_state_str(state_int) == "running":
            raise VMManagerError(f"VM '{fqdn}' is already running.")
        dom.create()

    def stop_vm(self, hostname: str) -> None:
        fqdn = self._cfg.full_hostname(hostname)
        conn = self._connect()
        try:
            dom = conn.lookupByName(fqdn)
        except Exception:
            raise VMManagerError(f"VM '{fqdn}' does not exist.")
        state_int, _ = dom.state()
        if _libvirt_state_str(state_int) != "running":
            raise VMManagerError(f"VM '{fqdn}' is already stopped.")
        dom.destroy()

    # ------------------------------------------------------------------
    # Run playbook
    # ------------------------------------------------------------------

    def run_playbook(
        self,
        hostname: str,
        playbook: str,
        output_lines: list[str] | None = None,
    ) -> None:
        def emit(msg: str) -> None:
            if output_lines is not None:
                output_lines.append(msg)

        cfg = self._cfg
        fqdn = cfg.full_hostname(hostname)

        if self.get_vm(fqdn) is None:
            raise VMManagerError(f"VM '{fqdn}' does not exist.")

        # Resolve playbook path
        if Path(playbook).is_absolute() and Path(playbook).exists():
            pb_path = playbook
        elif (cfg.playbooks_dir / playbook).exists():
            pb_path = str(cfg.playbooks_dir / playbook)
        else:
            raise VMManagerError(
                f"Playbook '{playbook}' not found. "
                f"Available playbooks are in {cfg.playbooks_dir}"
            )

        # Ensure inventory
        inv_path = cfg.inventories_dir / f"inventory-{fqdn}"
        if not inv_path.exists():
            cfg.inventories_dir.mkdir(parents=True, exist_ok=True)
            inv_path.write_text(
                f"{fqdn} ansible_user=root "
                f'ansible_ssh_common_args="-o StrictHostKeyChecking=no"\n'
            )

        # Ensure VM is running
        conn = self._connect()
        dom = conn.lookupByName(fqdn)
        state_int, _ = dom.state()
        if _libvirt_state_str(state_int) != "running":
            emit("VM is not running, starting ...")
            dom.create()
            self._wait_for_ssh(fqdn, emit)

        emit(f"Executing playbook: {pb_path} on {fqdn}")
        self._run_ansible(fqdn, pb_path, emit)

    # ------------------------------------------------------------------
    # Distribution helpers
    # ------------------------------------------------------------------

    def list_distributions(self) -> list[Distribution]:
        dist_dir = self._cfg.distributions_dir
        if not dist_dir.exists():
            return []
        dists: list[Distribution] = []
        for p in sorted(dist_dir.glob("*.dist")):
            d = _parse_dist_file(p)
            if d is not None:
                dists.append(d)
        return dists

    def get_distribution(self, name: str) -> Distribution | None:
        for d in self.list_distributions():
            if d.name == name:
                return d
        return None

    def _resolve_distribution(self, name: str | None) -> Distribution:
        cfg = self._cfg
        if name:
            dist = self.get_distribution(name)
            if dist is None:
                available = [d.name for d in self.list_distributions()]
                raise VMManagerError(
                    f"Distribution '{name}' is not defined. "
                    f"Available: {', '.join(available) or 'none'}"
                )
            return dist

        # Fall back to "default" symlink
        default_link = cfg.distributions_dir / "default"
        if default_link.exists():
            d = _parse_dist_file(default_link)
            if d is not None:
                d.name = "default"
                return d

        raise VMManagerError(
            "No distribution specified and no default distribution is set. "
            f"Please create a symlink: {cfg.distributions_dir}/default "
            "pointing to the desired .dist file, or pass a distribution name."
        )

    # ------------------------------------------------------------------
    # Playbook listing
    # ------------------------------------------------------------------

    def list_playbooks(self) -> list[str]:
        pb_dir = self._cfg.playbooks_dir
        if not pb_dir.exists():
            return []
        return sorted(p.name for p in pb_dir.glob("*.yml"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _virt_sysprep(self, hostname: str, image_path: Path) -> None:
        ssh_key = self._cfg.ssh_key_path
        cmd = [
            "virt-sysprep",
            "--hostname", hostname,
            "--enable", "customize",
            "-a", str(image_path),
        ]
        if ssh_key.exists():
            cmd += ["--ssh-inject", f"root:file:{ssh_key}"]
        try:
            subprocess.run(cmd, check=True, capture_output=False)
        except FileNotFoundError:
            raise VMManagerError(
                "virt-sysprep not found. "
                "Please install guestfs-tools (Fedora) or libguestfs-tools (Ubuntu)."
            )
        except subprocess.CalledProcessError as exc:
            raise VMManagerError(f"virt-sysprep failed: {exc}") from exc

    def _run_ansible(
        self,
        hostname: str,
        playbook: str,
        emit: Callable[[str], None],
    ) -> None:
        cfg = self._cfg
        inv_path = cfg.inventories_dir / f"inventory-{hostname}"
        cmd = [
            "ansible-playbook",
            "-e", f"vm_name={hostname}",
            "-i", str(inv_path),
            playbook,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if proc.stdout is None:
                raise VMManagerError("Failed to open stdout pipe for ansible-playbook.")
            for line in proc.stdout:
                emit(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                raise VMManagerError(
                    f"ansible-playbook exited with code {proc.returncode}"
                )
        except FileNotFoundError:
            raise VMManagerError(
                "ansible-playbook not found. Please install Ansible."
            )

    def _wait_for_ssh(
        self,
        hostname: str,
        emit: Callable[[str], None],
        timeout: int = 300,
        interval: int = 2,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((hostname, 22), timeout=3):
                    return
            except OSError:
                emit(f"Waiting for {hostname} to come up.")
                time.sleep(interval)
        raise VMManagerError(
            f"Timed out waiting for {hostname} to accept SSH connections."
        )
