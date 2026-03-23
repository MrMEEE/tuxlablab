"""Click-based CLI for tuxlablab – mirrors the laptop-lab ``vm`` bash script."""

from __future__ import annotations

import sys

import click

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

    Equivalent to the laptop-lab ``vm`` tool.

    \b
    Quick-start examples:
      vm create test123 --cpus 2 --memory 2048
      vm create apache --cpus 2 --memory 2048 --dist apache
      vm list
      vm start test123
      vm stop test123
      vm playbook test123 apache.yml
      vm remove test123
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
      vm create test123 --cpus 2 --memory 2048
      vm create apache  --cpus 2 --memory 2048 --dist apache
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
    PLAYBOOK  Playbook filename (in the playbooks directory) or an
              absolute path. If omitted, available playbooks are listed.
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
# distributions
# ---------------------------------------------------------------------------


@main.command(name="distributions")
def cmd_distributions() -> None:
    """List available distributions."""
    mgr = _manager()
    dists = mgr.list_distributions()
    if not dists:
        click.echo(
            "No distributions defined. "
            f"Create .dist files in {config.distributions_dir}"
        )
        return
    click.echo("Available distributions:")
    for d in dists:
        click.echo(f"  {d.name:20s}  {d.display_name}")
        if d.playbooks:
            click.echo(f"  {'':20s}  playbooks: {', '.join(d.playbooks)}")


@main.command(name="dists", hidden=True)
@click.pass_context
def cmd_dists(ctx: click.Context) -> None:
    """Alias for 'distributions'."""
    ctx.invoke(cmd_distributions)


@main.command(name="dist", hidden=True)
@click.pass_context
def cmd_dist(ctx: click.Context) -> None:
    """Alias for 'distributions'."""
    ctx.invoke(cmd_distributions)


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
