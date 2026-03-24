"""SQLite database layer for tuxlablab.

All variable / user-managed data is stored here:
  - distributions  (name, display_name, image_file, playbooks)
  - vm_inventory   (per-VM ansible connection details)
  - settings       (lab configuration key/value pairs, e.g. labdomain)
"""

from __future__ import annotations

import ipaddress
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS distributions (
    name         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    image_file   TEXT NOT NULL,
    playbooks    TEXT NOT NULL DEFAULT '',
    download_url TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS vm_inventory (
    hostname     TEXT PRIMARY KEY,
    ansible_user TEXT NOT NULL DEFAULT 'root',
    ssh_args     TEXT NOT NULL DEFAULT '-o StrictHostKeyChecking=no'
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DEFAULT_DC_HOME = str(Path.home() / "ansible" / "localdc")
_DEFAULT_SSH_KEY = str(Path.home() / ".ssh" / "id_rsa.pub")

# Full settings set (replaces options formerly stored in config/*.conf).
_SETTINGS_DEFAULTS: dict[str, str] = {
    "labdomain": "mylab.lan",
    "labgw": "192.168.124.1",
    "labdhcpstart": "192.168.124.2",
    "labdhcpend": "192.168.124.250",
    "rhnusername": "",
    "rhnpassword": "",
    "rhn_ca_cert": "",
    "rhn_entitlement_cert": "",
    "rhn_entitlement_key": "",
    "dc_home": _DEFAULT_DC_HOME,
    "ssh_key_path": _DEFAULT_SSH_KEY,
    "libvirt_uri": "qemu:///system",
    "host": "0.0.0.0",
    "port": "8080",
}

_REQUIRED_SETTING_KEYS = {
    "labdomain",
    "labgw",
    "labdhcpstart",
    "labdhcpend",
    "dc_home",
    "ssh_key_path",
    "libvirt_uri",
    "host",
    "port",
}

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[A-Za-z0-9-]{1,63}(?<!-))(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


def _is_valid_ipv4(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(parsed, ipaddress.IPv4Address)


def _is_valid_host(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    if value == "localhost":
        return True
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(_HOSTNAME_RE.match(value))


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _default_db_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return data_home / "tuxlablab" / "tuxlablab.db"


def _db_path() -> Path:
    """Return the path to the SQLite database file."""
    env_db = os.environ.get("TUXLABLAB_DB")
    if env_db:
        return Path(env_db)
    return _default_db_path()


def init_db(path: Path | None = None) -> None:
    """Create tables and seed default settings (idempotent)."""
    db = path or _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(_DDL)
        # Migrate existing databases that predate the download_url column.
        try:
            conn.execute("ALTER TABLE distributions ADD COLUMN download_url TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migrate all RHEL 9.3 distributions to 9.7 in existing databases.
        conn.execute(
            "UPDATE distributions"
            " SET image_file   = REPLACE(image_file,   'rhel-9.3-', 'rhel-9.7-'),"
            "     display_name = REPLACE(display_name, '9.3',       '9.7')"
            " WHERE image_file LIKE 'rhel-9.3-%'"
        )
        # Rename the rhel93 distribution key to rhel97 (insert then delete).
        conn.execute(
            "INSERT OR IGNORE INTO distributions"
            " SELECT 'rhel97', display_name, image_file, playbooks, download_url"
            " FROM distributions WHERE name = 'rhel93'"
        )
        conn.execute("DELETE FROM distributions WHERE name = 'rhel93'")
        # Ensure all known settings keys exist without overriding user values.
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            list(_SETTINGS_DEFAULTS.items()),
        )
        conn.commit()


@contextmanager
def get_db(path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding an open, row-factory-enabled connection."""
    db = path or _db_path()
    init_db(db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # WAL (Write-Ahead Logging) allows concurrent reads while a write is in
    # progress, improving throughput when multiple requests hit the DB at once.
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Distributions
# ---------------------------------------------------------------------------


def list_distributions(db_path: Path | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT name, display_name, image_file, playbooks, download_url FROM distributions ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_distribution(name: str, db_path: Path | None = None) -> dict | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT name, display_name, image_file, playbooks, download_url FROM distributions WHERE name = ?",
            (name,),
        ).fetchone()
    return dict(row) if row else None


def upsert_distribution(
    name: str,
    display_name: str,
    image_file: str,
    playbooks: str = "",
    download_url: str = "",
    db_path: Path | None = None,
) -> None:
    """Insert or replace a distribution record."""
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO distributions (name, display_name, image_file, playbooks, download_url)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                display_name = excluded.display_name,
                image_file   = excluded.image_file,
                playbooks    = excluded.playbooks,
                download_url = excluded.download_url
            """,
            (name, display_name, image_file, playbooks, download_url),
        )


def delete_distribution(name: str, db_path: Path | None = None) -> bool:
    """Delete a distribution. Returns True if a row was removed."""
    with get_db(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM distributions WHERE name = ?", (name,)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# VM Inventory
# ---------------------------------------------------------------------------


def upsert_vm_inventory(
    hostname: str,
    ansible_user: str = "root",
    ssh_args: str = "-o StrictHostKeyChecking=no",
    db_path: Path | None = None,
) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO vm_inventory (hostname, ansible_user, ssh_args)
            VALUES (?, ?, ?)
            ON CONFLICT(hostname) DO UPDATE SET
                ansible_user = excluded.ansible_user,
                ssh_args     = excluded.ssh_args
            """,
            (hostname, ansible_user, ssh_args),
        )


def get_vm_inventory(hostname: str, db_path: Path | None = None) -> dict | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT hostname, ansible_user, ssh_args FROM vm_inventory WHERE hostname = ?",
            (hostname,),
        ).fetchone()
    return dict(row) if row else None


def delete_vm_inventory(hostname: str, db_path: Path | None = None) -> None:
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM vm_inventory WHERE hostname = ?", (hostname,))


def list_vm_inventories(db_path: Path | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT hostname, ansible_user, ssh_args FROM vm_inventory ORDER BY hostname"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def setting_keys() -> list[str]:
    return sorted(_SETTINGS_DEFAULTS.keys())


def is_valid_setting_key(key: str) -> bool:
    return key in _SETTINGS_DEFAULTS


def validate_setting_value(key: str, value: str, db_path: Path | None = None) -> str:
    if key not in _SETTINGS_DEFAULTS:
        allowed = ", ".join(setting_keys())
        raise ValueError(f"Unsupported setting '{key}'. Allowed keys: {allowed}")

    if key in _REQUIRED_SETTING_KEYS and not value.strip():
        raise ValueError(f"Setting '{key}' cannot be empty")

    if key in {"labgw", "labdhcpstart", "labdhcpend"} and not _is_valid_ipv4(value):
        raise ValueError(f"Setting '{key}' must be a valid IPv4 address")

    if key == "host" and not _is_valid_host(value):
        raise ValueError(
            "Setting 'host' must be a valid IP address, hostname, or 'localhost'"
        )

    if key == "port":
        try:
            port = int(value)
        except ValueError as exc:
            raise ValueError("Setting 'port' must be an integer") from exc
        if port < 1 or port > 65535:
            raise ValueError("Setting 'port' must be between 1 and 65535")

    if key in {"labdhcpstart", "labdhcpend"}:
        if key == "labdhcpstart":
            start_ip = ipaddress.IPv4Address(value)
            end_ip = ipaddress.IPv4Address(
                get_setting("labdhcpend", _SETTINGS_DEFAULTS["labdhcpend"], db_path=db_path)
            )
        else:
            start_ip = ipaddress.IPv4Address(
                get_setting("labdhcpstart", _SETTINGS_DEFAULTS["labdhcpstart"], db_path=db_path)
            )
            end_ip = ipaddress.IPv4Address(value)
        if start_ip > end_ip:
            raise ValueError("DHCP range is invalid: labdhcpstart must be <= labdhcpend")

    return value


def get_setting(key: str, default: str = "", db_path: Path | None = None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str, db_path: Path | None = None) -> None:
    validated = validate_setting_value(key, value, db_path=db_path)
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, validated),
        )


def list_settings(db_path: Path | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings ORDER BY key"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Migration helper: import legacy .dist files into the database
# ---------------------------------------------------------------------------


def import_dist_files(dist_dir: Path, db_path: Path | None = None) -> int:
    """Scan *dist_dir* for ``*.dist`` files and insert them into the DB.

    Returns the number of distributions imported.
    """
    imported = 0
    for p in sorted(dist_dir.glob("*.dist")):
        data: dict[str, str] = {}
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    data[key.strip()] = val.strip().strip('"')
        except OSError:
            continue
        image_file = data.get("DISTFILE", "")
        if not image_file:
            continue
        upsert_distribution(
            name=p.stem,
            display_name=data.get("DISTNAME", p.stem),
            image_file=image_file,
            playbooks=data.get("DISTPLAYBOOKS", ""),
            db_path=db_path,
        )
        imported += 1
    return imported
