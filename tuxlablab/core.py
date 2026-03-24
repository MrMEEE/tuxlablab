"""Core VM management logic for tuxlablab.

Wraps libvirt-python for domain lifecycle operations and subprocess calls
for virt-sysprep and ansible-playbook, mirroring the feature-set of the
original laptop-lab ``vm`` bash script.
"""

from __future__ import annotations

import os
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile
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
import tuxlablab.db as _db

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


def _dist_from_row(row: dict) -> Distribution:
    """Convert a DB row dict to a :class:`Distribution`."""
    playbooks_raw = row.get("playbooks", "") or ""
    return Distribution(
        name=row["name"],
        display_name=row["display_name"],
        image_file=row["image_file"],
        playbooks=[p for p in playbooks_raw.split() if p],
    )


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


def _libvirt_system_users() -> list[str]:
    users: list[str] = []
    for candidate in ("qemu", "libvirt-qemu"):
        try:
            pwd.getpwnam(candidate)
        except KeyError:
            continue
        users.append(candidate)
    return users


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
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
  </os>
  <cpu mode='host-model' check='none'/>
  <devices>
    <emulator>/usr/libexec/qemu-kvm</emulator>
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
        <graphics type='vnc' autoport='yes'>
            <listen type='address'/>
        </graphics>
    <video>
            <model type='virtio' heads='1' primary='yes'/>
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
    """Parse a bash-style ``.dist`` file into a :class:`Distribution`.

    Kept for the migration / import-dist-files helper.
    """
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
        name=path.stem,
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

    def _grant_libvirt_disk_access(self, vm_image: Path) -> None:
        """Grant qemu/libvirt-qemu ACL access to VM disk paths for qemu:///system."""
        if self._cfg.libvirt_uri != "qemu:///system":
            return

        setfacl = shutil.which("setfacl")
        if not setfacl:
            return

        users = _libvirt_system_users()
        if not users:
            return

        dirs = [self._cfg.dc_home, self._cfg.images_dir, self._cfg.vms_dir, vm_image.parent]
        dirs.extend(vm_image.parent.parents)
        seen: set[Path] = set()
        unique_dirs: list[Path] = []
        for d in dirs:
            rd = d.resolve()
            if rd in seen or not rd.exists():
                continue
            seen.add(rd)
            unique_dirs.append(rd)

        for user in users:
            for d in unique_dirs:
                subprocess.run(
                    [setfacl, "-m", f"u:{user}:rx", str(d)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            subprocess.run(
                [setfacl, "-m", f"u:{user}:rw", str(vm_image)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _ensure_libvirt_path_traversal(self, vm_image: Path) -> None:
        """Fallback permissions to allow qemu:///system to traverse user paths."""
        if self._cfg.libvirt_uri != "qemu:///system":
            return

        for d in [vm_image.parent, *vm_image.parent.parents]:
            if not d.exists():
                continue
            try:
                mode = d.stat().st_mode
                new_mode = mode | stat.S_IXOTH
                if new_mode != mode and os.access(d, os.W_OK):
                    d.chmod(new_mode)
            except OSError:
                continue

        try:
            mode = vm_image.stat().st_mode
            new_mode = mode | stat.S_IROTH | stat.S_IWOTH
            if new_mode != mode and os.access(vm_image, os.W_OK):
                vm_image.chmod(new_mode)
        except OSError:
            pass

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
                vcpus=self._get_vcpus_from_xml(dom),
                memory_mb=dom.maxMemory() // 1024,
                disks=self._get_disk_paths(dom),
            )
            vms.append(info)
        vms.sort(key=lambda v: v.name)
        return vms

    def _get_vcpus_from_xml(self, dom: "libvirt.virDomain") -> int:
        """Read vCPU count from domain XML (works for running and stopped domains)."""
        try:
            root = ET.fromstring(dom.XMLDesc())
            vcpu_el = root.find("vcpu")
            if vcpu_el is not None and vcpu_el.text:
                return int(vcpu_el.text)
        except Exception:
            pass
        return 0

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
        vm_image.chmod(0o660)
        self._grant_libvirt_disk_access(vm_image)
        self._ensure_libvirt_path_traversal(vm_image)

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

        # Store inventory in the DB (replaces per-file inventory)
        _db.upsert_vm_inventory(fqdn, ansible_user="root")

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

        # Remove inventory from the DB
        _db.delete_vm_inventory(fqdn)

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

        # Ensure inventory exists in the DB
        if _db.get_vm_inventory(fqdn) is None:
            _db.upsert_vm_inventory(fqdn, ansible_user="root")

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
    # Distribution helpers (backed by SQLite)
    # ------------------------------------------------------------------

    def list_distributions(self) -> list[Distribution]:
        """Return all distributions from the SQLite database."""
        return [_dist_from_row(r) for r in _db.list_distributions()]

    def get_distribution(self, name: str) -> Distribution | None:
        row = _db.get_distribution(name)
        return _dist_from_row(row) if row else None

    def _resolve_distribution(self, name: str | None) -> Distribution:
        if name:
            dist = self.get_distribution(name)
            if dist is None:
                available = [d.name for d in self.list_distributions()]
                raise VMManagerError(
                    f"Distribution '{name}' is not defined. "
                    f"Available: {', '.join(available) or 'none'}"
                )
            return dist

        # Fall back to a distribution named "default" in the DB
        dist = self.get_distribution("default")
        if dist is not None:
            return dist

        # Last resort: use the first defined distribution
        all_dists = self.list_distributions()
        if all_dists:
            return all_dists[0]

        raise VMManagerError(
            "No distribution specified and no distributions are defined. "
            "Add one via: tuxlablab dist-add"
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
        # Build inventory content from the DB record (or sensible default)
        inv_row = _db.get_vm_inventory(hostname)
        if inv_row:
            ansible_user = inv_row["ansible_user"]
            ssh_args = inv_row["ssh_args"]
        else:
            ansible_user = "root"
            ssh_args = "-o StrictHostKeyChecking=no"
        inv_content = (
            f"{hostname} ansible_user={ansible_user} "
            f'ansible_ssh_common_args="{ssh_args}"\n'
        )

        # Write a temporary inventory file using a temp directory so cleanup
        # is handled safely by the context manager, even if an exception occurs.
        with tempfile.TemporaryDirectory(prefix="tuxlablab-") as tmpdir:
            tmp_inv = str(Path(tmpdir) / "inventory.ini")
            Path(tmp_inv).write_text(inv_content)

            cmd = [
                "ansible-playbook",
                "-e", f"vm_name={hostname}",
                "-i", tmp_inv,
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
