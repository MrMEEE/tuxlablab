"""Click-based CLI for tuxlablab."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import click

import tuxlablab.db as _db
from tuxlablab.config import config
from tuxlablab.core import VMManager, VMManagerError

pass_manager = click.make_pass_decorator(VMManager, ensure=True)


def _manager() -> VMManager:
    return VMManager()


# ---------------------------------------------------------------------------
# Helper output
# ---------------------------------------------------------------------------

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    click.echo(f"{GREEN}{msg}{RESET}")


def _err(msg: str) -> None:
    click.echo(f"{RED}{msg}{RESET}", err=True)


def _warn(msg: str) -> None:
    click.echo(f"{YELLOW}{msg}{RESET}", err=True)


def _run_cmd(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise click.ClickException(f"Command not found: {cmd[0]}")
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise click.ClickException(detail)


def _normalize_unit_name(name: str) -> str:
    unit = name if name.endswith(".service") else f"{name}.service"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", unit):
        raise click.ClickException(
            "Invalid service name. Use letters, numbers, '_', '.', '@', '-' only."
        )
    return unit


def _build_user_service_text(
    db_path: str | None = None,
    python_exec: str | None = None,
) -> str:
    python_path = python_exec or os.environ.get("TUXLABLAB_PYTHON") or sys.executable
    exec_cmd = f"{shlex.quote(python_path)} -m tuxlablab.api"
    lines = [
        "[Unit]",
        "Description=tuxlablab user service",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={exec_cmd}",
        "Restart=on-failure",
        "RestartSec=3",
        "Environment=PYTHONUNBUFFERED=1",
        f"WorkingDirectory={Path.home()}",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    if db_path:
        lines.insert(11, f"Environment=TUXLABLAB_DB={db_path}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name="tuxlablab")
@click.pass_context
def main(ctx: click.Context) -> None:
    """tuxlablab – Python-based VM administration.

    \b
    Quick-start examples:
      tuxlablab create test123 --cpus 2 --memory 2048
      tuxlablab create apache --cpus 2 --memory 2048 --dist apache
      tuxlablab list
      tuxlablab start test123
      tuxlablab stop test123
      tuxlablab playbook test123 apache.yml
      tuxlablab remove test123
      tuxlablab dist-add centos9 "CentOS 9" centos9.qcow2
      tuxlablab settings
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# list / ls
# ---------------------------------------------------------------------------


@main.command(name="list")
def cmd_list() -> None:
    """List all VMs (running and stopped)."""
    mgr = _manager()
    vms = mgr.list_vms()
    if not vms:
        click.echo("No VMs defined.")
        return

    running = [v for v in vms if v.state == "running"]
    stopped = [v for v in vms if v.state != "running"]

    if running:
        click.echo(f"{GREEN}Running VMs{RESET}")
        for v in running:
            click.echo(f"  {v.name}  ({v.vcpus} vCPU, {v.memory_mb} MB RAM)")

    if stopped:
        click.echo(f"{RED}Stopped / Paused VMs{RESET}")
        for v in stopped:
            click.echo(f"  {v.name}  [{v.state}]")


@main.command(name="ls", hidden=True)
@click.pass_context
def cmd_ls(ctx: click.Context) -> None:
    """Alias for 'list'."""
    ctx.invoke(cmd_list)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@main.command(name="create")
@click.argument("hostname")
@click.option("--cpus", "-c", default=2, show_default=True, type=int, help="Number of vCPUs")
@click.option("--memory", "-m", default=2048, show_default=True, type=int, help="RAM in MB")
@click.option("--dist", "-d", default=None, help="Distribution name (e.g. centos9)")
def cmd_create(hostname: str, cpus: int, memory: int, dist: str | None) -> None:
    """Create a new virtual machine.

    HOSTNAME can be a short name (domain suffix is appended automatically)
    or a fully-qualified name including the lab domain.

    \b
    Examples:
      tuxlablab create test123 --cpus 2 --memory 2048
      tuxlablab create apache  --cpus 2 --memory 2048 --dist apache
    """
    lines: list[str] = []
    mgr = _manager()
    try:
        mgr.create_vm(hostname, cpus, memory, dist, lines)
        for line in lines:
            click.echo(line)
        _ok(f"VM '{config.full_hostname(hostname)}' created successfully.")
    except VMManagerError as exc:
        for line in lines:
            click.echo(line)
        _err(str(exc))
        sys.exit(1)


# ---------------------------------------------------------------------------
# remove / rm
# ---------------------------------------------------------------------------


@main.command(name="remove")
@click.argument("hostname")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def cmd_remove(hostname: str, yes: bool) -> None:
    """Remove a VM and delete its disk image.

    HOSTNAME is the VM name (short or FQDN).
    """
    fqdn = config.full_hostname(hostname)
    if not yes:
        click.confirm(f"Remove VM '{fqdn}' and all its disks?", abort=True)
    mgr = _manager()
    try:
        mgr.remove_vm(hostname)
        _ok(f"VM '{fqdn}' removed.")
    except VMManagerError as exc:
        _err(str(exc))
        sys.exit(1)


@main.command(name="rm", hidden=True)
@click.argument("hostname")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def cmd_rm(ctx: click.Context, hostname: str, yes: bool) -> None:
    """Alias for 'remove'."""
    ctx.invoke(cmd_remove, hostname=hostname, yes=yes)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@main.command(name="start")
@click.argument("hostname")
def cmd_start(hostname: str) -> None:
    """Start a stopped VM."""
    mgr = _manager()
    try:
        mgr.start_vm(hostname)
        _ok(f"VM '{config.full_hostname(hostname)}' started.")
    except VMManagerError as exc:
        _err(str(exc))
        sys.exit(1)


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@main.command(name="stop")
@click.argument("hostname")
def cmd_stop(hostname: str) -> None:
    """Stop a running VM (hard power-off, equivalent to virsh destroy)."""
    mgr = _manager()
    try:
        mgr.stop_vm(hostname)
        _ok(f"VM '{config.full_hostname(hostname)}' stopped.")
    except VMManagerError as exc:
        _err(str(exc))
        sys.exit(1)


# ---------------------------------------------------------------------------
# playbook / pb
# ---------------------------------------------------------------------------


@main.command(name="playbook")
@click.argument("hostname")
@click.argument("playbook", required=False)
def cmd_playbook(hostname: str, playbook: str | None) -> None:
    """Run an Ansible playbook on an existing VM.

    \b
    HOSTNAME  Target VM (short name or FQDN)
    PLAYBOOK  Playbook filename (in playbooks directory) or absolute path.
              If omitted, available playbooks are listed.
    """
    mgr = _manager()
    if not playbook:
        available = mgr.list_playbooks()
        if available:
            click.echo("Available playbooks:")
            for pb in available:
                click.echo(f"  {pb}")
        else:
            click.echo("No playbooks found.")
        return

    lines: list[str] = []
    try:
        mgr.run_playbook(hostname, playbook, lines)
        for line in lines:
            click.echo(line)
        _ok("Playbook completed successfully.")
    except VMManagerError as exc:
        for line in lines:
            click.echo(line)
        _err(str(exc))
        sys.exit(1)


@main.command(name="pb", hidden=True)
@click.argument("hostname")
@click.argument("playbook", required=False)
@click.pass_context
def cmd_pb(ctx: click.Context, hostname: str, playbook: str | None) -> None:
    """Alias for 'playbook'."""
    ctx.invoke(cmd_playbook, hostname=hostname, playbook=playbook)


# ---------------------------------------------------------------------------
# distributions (list)
# ---------------------------------------------------------------------------


@main.command(name="distributions")
def cmd_distributions() -> None:
    """List distributions stored in the database."""
    mgr = _manager()
    dists = mgr.list_distributions()
    if not dists:
        click.echo(
            "No distributions defined. "
            "Add one with: tuxlablab dist-add <name> <display-name> <image-file>"
        )
        return
    click.echo("Available distributions:")
    _pad = " " * 22
    for d in dists:
        click.echo(f"  {d.name:20s}  {d.display_name}")
        click.echo(f"{_pad}image: {d.image_file}")
        if d.playbooks:
            click.echo(f"{_pad}playbooks: {', '.join(d.playbooks)}")


@main.command(name="dists", hidden=True)
@click.pass_context
def cmd_dists(ctx: click.Context) -> None:
    """Alias for 'distributions'."""
    ctx.invoke(cmd_distributions)


# ---------------------------------------------------------------------------
# dist-add
# ---------------------------------------------------------------------------


@main.command(name="dist-add")
@click.argument("name")
@click.argument("display_name")
@click.argument("image_file")
@click.option(
    "--playbooks", "-p", default="",
    help="Space-separated list of playbook filenames to run after creation"
)
def cmd_dist_add(name: str, display_name: str, image_file: str, playbooks: str) -> None:
    """Add or update a distribution in the database.

    \b
    NAME          Short identifier (e.g. centos9)
    DISPLAY_NAME  Human-readable label (e.g. "CentOS Stream 9")
    IMAGE_FILE    qcow2 filename inside the images directory
    """
    _db.upsert_distribution(
        name=name,
        display_name=display_name,
        image_file=image_file,
        playbooks=playbooks,
    )
    _ok(f"Distribution '{name}' saved.")


# ---------------------------------------------------------------------------
# dist-remove
# ---------------------------------------------------------------------------


@main.command(name="dist-remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def cmd_dist_remove(name: str, yes: bool) -> None:
    """Remove a distribution from the database."""
    if not yes:
        click.confirm(f"Remove distribution '{name}'?", abort=True)
    removed = _db.delete_distribution(name)
    if removed:
        _ok(f"Distribution '{name}' removed.")
    else:
        _err(f"Distribution '{name}' not found.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# dist-import  (import legacy .dist files)
# ---------------------------------------------------------------------------


@main.command(name="dist-import")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
def cmd_dist_import(directory: str) -> None:
    """Import legacy .dist files from DIRECTORY into the database."""
    from pathlib import Path
    n = _db.import_dist_files(Path(directory))
    if n:
        _ok(f"Imported {n} distribution(s) from {directory}")
    else:
        _warn(f"No .dist files found in {directory}")


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


@main.command(name="settings")
@click.argument("key", required=False)
@click.argument("value", required=False)
def cmd_settings(key: str | None, value: str | None) -> None:
    """View or change lab settings stored in the database.

    \b
    No arguments:    list all settings
    KEY only:        show the value of KEY
    KEY VALUE:       set KEY to VALUE

    \b
    Available settings: labdomain, labgw, labdhcpstart, labdhcpend,
                        rhnusername, rhnpassword, dc_home, ssh_key_path,
                        libvirt_uri, host, port
    """
    if key is None:
        rows = _db.list_settings()
        if not rows:
            click.echo("No settings found.")
            return
        click.echo("Lab settings:")
        for row in rows:
            k, v = row["key"], row["value"]
            display = "***" if "password" in k.lower() and v else v
            click.echo(f"  {k:20s}  {display}")
        return

    if not _db.is_valid_setting_key(key):
        _err(
            f"Unknown setting '{key}'. "
            f"Allowed keys: {', '.join(_db.setting_keys())}"
        )
        sys.exit(1)

    if value is None:
        val = _db.get_setting(key)
        click.echo(f"{key} = {val!r}")
        return

    try:
        _db.set_setting(key, value)
    except ValueError as exc:
        _err(str(exc))
        sys.exit(1)
    _ok(f"Setting '{key}' updated.")


# ---------------------------------------------------------------------------
# service-install / service-uninstall
# ---------------------------------------------------------------------------


@main.command(name="service-install")
@click.option("--name", default="tuxlablab", show_default=True, help="Systemd user service name")
@click.option(
    "--db-path",
    default=None,
    help="SQLite DB path to inject via TUXLABLAB_DB (default: current env value if set)",
)
@click.option(
    "--python",
    "python_exec",
    default=None,
    help="Python interpreter path used in ExecStart (defaults to current interpreter)",
)
@click.option(
    "--enable-linger/--no-enable-linger",
    default=True,
    show_default=True,
    help="Run 'loginctl enable-linger' for the current user",
)
def cmd_service_install(
    name: str,
    db_path: str | None,
    python_exec: str | None,
    enable_linger: bool,
) -> None:
    """Install and start a user-level systemd service for tuxlablab."""
    unit = _normalize_unit_name(name)
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_file = service_dir / unit

    resolved_db_path = db_path or os.environ.get("TUXLABLAB_DB")

    service_dir.mkdir(parents=True, exist_ok=True)
    service_file.write_text(
        _build_user_service_text(
            db_path=resolved_db_path,
            python_exec=python_exec,
        )
    )

    _run_cmd(["systemctl", "--user", "daemon-reload"])
    _run_cmd(["systemctl", "--user", "enable", "--now", unit])

    if enable_linger:
        user = os.environ.get("USER") or ""
        try:
            _run_cmd(["loginctl", "enable-linger", user])
        except click.ClickException as exc:
            _warn(
                "Service installed and started, but linger could not be enabled automatically. "
                f"You may need elevated permissions: loginctl enable-linger {user}\n"
                f"Details: {exc}"
            )

    _ok(f"Installed and started user service '{unit}'.")
    click.echo(f"Unit file: {service_file}")


@main.command(name="service-uninstall")
@click.option("--name", default="tuxlablab", show_default=True, help="Systemd user service name")
@click.option(
    "--disable-linger",
    is_flag=True,
    help="Also run 'loginctl disable-linger' for the current user",
)
def cmd_service_uninstall(name: str, disable_linger: bool) -> None:
    """Stop and remove the tuxlablab user-level systemd service."""
    unit = _normalize_unit_name(name)
    service_file = Path.home() / ".config" / "systemd" / "user" / unit

    try:
        _run_cmd(["systemctl", "--user", "disable", "--now", unit])
    except click.ClickException as exc:
        _warn(f"Could not disable/stop '{unit}': {exc}")

    if service_file.exists():
        service_file.unlink()

    _run_cmd(["systemctl", "--user", "daemon-reload"])

    if disable_linger:
        user = os.environ.get("USER") or ""
        try:
            _run_cmd(["loginctl", "disable-linger", user])
        except click.ClickException as exc:
            _warn(
                "Service removed, but linger could not be disabled automatically. "
                f"You may need elevated permissions: loginctl disable-linger {user}\n"
                f"Details: {exc}"
            )

    _ok(f"Uninstalled user service '{unit}'.")


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------


@main.command(name="server")
@click.option("--host", default=None, help="Bind address (default from config)")
@click.option("--port", default=None, type=int, help="Port (default from config)")
@click.option("--reload", is_flag=True, help="Enable auto-reload (development mode)")
def cmd_server(host: str | None, port: int | None, reload: bool) -> None:
    """Start the tuxlablab web/API server."""
    import uvicorn

    h = host or config.host
    p = port or config.port
    click.echo(f"Starting tuxlablab server on http://{h}:{p}")
    uvicorn.run(
        "tuxlablab.api:app",
        host=h,
        port=p,
        reload=reload,
    )
