"""Configuration management for tuxlablab."""

from __future__ import annotations

import configparser
import os
from pathlib import Path


_DEFAULT_DC_HOME = str(Path.home() / "ansible" / "localdc")
_DEFAULT_SSH_KEY = str(Path.home() / ".ssh" / "id_rsa.pub")

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

_CONFIG_SEARCH_PATHS = [
    Path.home() / ".config" / "tuxlablab" / "config.ini",
    Path("/etc/tuxlablab/config.ini"),
]


def _find_config_file() -> Path | None:
    env_path = os.environ.get("TUXLABLAB_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    for candidate in _CONFIG_SEARCH_PATHS:
        if candidate.exists():
            return candidate
    return None


def _load_config() -> dict[str, str]:
    cfg: dict[str, str] = dict(_DEFAULTS)
    path = _find_config_file()
    if path is None:
        return cfg
    parser = configparser.ConfigParser()
    parser.read(path)
    section = "tuxlablab"
    if section not in parser:
        return cfg
    for key in _DEFAULTS:
        if parser.has_option(section, key):
            cfg[key] = parser.get(section, key)
    return cfg


class Config:
    """Holds all runtime configuration for tuxlablab."""

    def __init__(self) -> None:
        raw = _load_config()
        self.labdomain: str = raw["labdomain"]
        self.labgw: str = raw["labgw"]
        self.labdhcpstart: str = raw["labdhcpstart"]
        self.labdhcpend: str = raw["labdhcpend"]
        self.rhnusername: str = raw["rhnusername"]
        self.rhnpassword: str = raw["rhnpassword"]
        self.dc_home: Path = Path(raw["dc_home"])
        self.ssh_key_path: Path = Path(raw["ssh_key_path"])
        self.libvirt_uri: str = raw["libvirt_uri"]
        self.host: str = raw["host"]
        self.port: int = int(raw["port"])

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
        return self.dc_home / "playbooks"

    @property
    def inventories_dir(self) -> Path:
        return self.dc_home / "inventories"

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        for d in (
            self.images_dir,
            self.vms_dir,
            self.distributions_dir,
            self.playbooks_dir,
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
