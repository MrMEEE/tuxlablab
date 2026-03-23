"""SQLite database layer for tuxlablab.

All variable / user-managed data is stored here:
  - distributions  (name, display_name, image_file, playbooks)
  - vm_inventory   (per-VM ansible connection details)
  - settings       (lab configuration key/value pairs, e.g. labdomain)
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from tuxlablab.config import config as _global_config

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS distributions (
    name         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    image_file   TEXT NOT NULL,
    playbooks    TEXT NOT NULL DEFAULT ''
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

# Default lab settings that are pre-populated on first run.
_DEFAULT_SETTINGS: list[tuple[str, str]] = [
    ("labdomain",     "mylab.lan"),
    ("labgw",         "192.168.124.1"),
    ("labdhcpstart",  "192.168.124.2"),
    ("labdhcpend",    "192.168.124.250"),
    ("rhnusername",   ""),
    ("rhnpassword",   ""),
]


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    """Return the path to the SQLite database file."""
    return Path(_global_config.dc_home) / "tuxlablab.db"


def init_db(path: Path | None = None) -> None:
    """Create tables and seed default settings (idempotent)."""
    db = path or _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(_DDL)
        # Insert defaults only if settings table is empty
        cur = conn.execute("SELECT COUNT(*) FROM settings")
        if cur.fetchone()[0] == 0:
            conn.executemany(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                _DEFAULT_SETTINGS,
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
            "SELECT name, display_name, image_file, playbooks FROM distributions ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_distribution(name: str, db_path: Path | None = None) -> dict | None:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT name, display_name, image_file, playbooks FROM distributions WHERE name = ?",
            (name,),
        ).fetchone()
    return dict(row) if row else None


def upsert_distribution(
    name: str,
    display_name: str,
    image_file: str,
    playbooks: str = "",
    db_path: Path | None = None,
) -> None:
    """Insert or replace a distribution record."""
    with get_db(db_path) as conn:
        conn.execute(
            """
            INSERT INTO distributions (name, display_name, image_file, playbooks)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                display_name = excluded.display_name,
                image_file   = excluded.image_file,
                playbooks    = excluded.playbooks
            """,
            (name, display_name, image_file, playbooks),
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


def get_setting(key: str, default: str = "", db_path: Path | None = None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str, db_path: Path | None = None) -> None:
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
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
