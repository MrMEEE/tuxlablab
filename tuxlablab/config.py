"""Configuration management for tuxlablab."""

from __future__ import annotations

from pathlib import Path

import tuxlablab.db as _db


_DEFAULT_DC_HOME = str(Path.home() / "ansible" / "localdc")
_DEFAULT_SSH_KEY = str(Path.home() / ".ssh" / "id_rsa.pub")
_PACKAGE_DIR = Path(__file__).resolve().parent
_INSTALL_ROOT = _PACKAGE_DIR.parent

_DEFAULTS = {
    "labdomain": "mylab.lan",
    "labgw": "192.168.124.1",
    "labdhcpstart": "192.168.124.2",
    "labdhcpend": "192.168.124.250",
    "rhnusername": "",
    "rhnpassword": "",
    "dc_home": _DEFAULT_DC_HOME,
    "ssh_key_path": _DEFAULT_SSH_KEY,
    "libvirt_uri": "qemu:///system",
    "host": "0.0.0.0",
    "port": "8080",
}


class Config:
    """Holds runtime configuration backed by the SQLite settings table."""

    def _get(self, key: str) -> str:
        return _db.get_setting(key, _DEFAULTS[key])

    @property
    def labdomain(self) -> str:
        return self._get("labdomain")

    @property
    def labgw(self) -> str:
        return self._get("labgw")

    @property
    def labdhcpstart(self) -> str:
        return self._get("labdhcpstart")

    @property
    def labdhcpend(self) -> str:
        return self._get("labdhcpend")

    @property
    def rhnusername(self) -> str:
        return self._get("rhnusername")

    @property
    def rhnpassword(self) -> str:
        return self._get("rhnpassword")

    @property
    def dc_home(self) -> Path:
        return Path(self._get("dc_home"))

    @property
    def ssh_key_path(self) -> Path:
        return Path(self._get("ssh_key_path"))

    @property
    def libvirt_uri(self) -> str:
        return self._get("libvirt_uri")

    @property
    def host(self) -> str:
        return self._get("host")

    @property
    def port(self) -> int:
        raw = self._get("port")
        try:
            return int(raw)
        except ValueError:
            return int(_DEFAULTS["port"])

    # Derived paths -----------------------------------------------------------

    @property
    def images_dir(self) -> Path:
        return self.dc_home / "images"

    @property
    def vms_dir(self) -> Path:
        return self.dc_home / "vms"

    @property
    def distributions_dir(self) -> Path:
        return self.dc_home / "distributions"

    @property
    def playbooks_dir(self) -> Path:
        return _INSTALL_ROOT / "playbooks"

    @property
    def inventories_dir(self) -> Path:
        return self.dc_home / "inventories"

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        for d in (
            self.images_dir,
            self.vms_dir,
            self.distributions_dir,
            self.inventories_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def full_hostname(self, name: str) -> str:
        """Return name with labdomain appended if not already present."""
        if name.endswith(f".{self.labdomain}"):
            return name
        return f"{name}.{self.labdomain}"

    def short_hostname(self, fqdn: str) -> str:
        """Strip labdomain suffix if present."""
        suffix = f".{self.labdomain}"
        if fqdn.endswith(suffix):
            return fqdn[: -len(suffix)]
        return fqdn


# Module-level singleton
config = Config()
