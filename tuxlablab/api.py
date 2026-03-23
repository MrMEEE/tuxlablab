"""FastAPI REST API + HTML web-interface for tuxlablab."""

from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from tuxlablab.config import config
from tuxlablab.core import VMManager, VMManagerError

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent

app = FastAPI(
    title="tuxlablab",
    description="Python-based VM administration API",
    version="1.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory=str(_HERE / "web" / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(_HERE / "web" / "templates"))

_manager = VMManager()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CreateVMRequest(BaseModel):
    hostname: str
    vcpus: int = 2
    ram_mb: int = 2048
    distribution: str | None = None


class RunPlaybookRequest(BaseModel):
    playbook: str


# ---------------------------------------------------------------------------
# REST API  (/api/*)
# ---------------------------------------------------------------------------


@app.get("/api/vms", tags=["vms"])
def api_list_vms():
    """Return all defined VMs with their state."""
    try:
        vms = _manager.list_vms()
    except VMManagerError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return [
        {
            "name": v.name,
            "state": v.state,
            "vcpus": v.vcpus,
            "memory_mb": v.memory_mb,
            "disks": v.disks,
        }
        for v in vms
    ]


@app.get("/api/vms/{name}", tags=["vms"])
def api_get_vm(name: str):
    """Return details for a single VM."""
    vm = _manager.get_vm(name)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found.")
    return {
        "name": vm.name,
        "state": vm.state,
        "vcpus": vm.vcpus,
        "memory_mb": vm.memory_mb,
        "disks": vm.disks,
    }


@app.post("/api/vms", status_code=202, tags=["vms"])
def api_create_vm(req: CreateVMRequest, background_tasks: BackgroundTasks):
    """Create a new VM (runs asynchronously)."""
    output_lines: list[str] = []

    def _task():
        try:
            _manager.create_vm(
                req.hostname,
                req.vcpus,
                req.ram_mb,
                req.distribution,
                output_lines,
            )
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")

    background_tasks.add_task(_task)
    return {"status": "accepted", "message": f"Creating VM '{req.hostname}'"}


@app.post("/api/vms/{name}/start", tags=["vms"])
def api_start_vm(name: str):
    """Start a stopped VM."""
    try:
        _manager.start_vm(name)
    except VMManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "message": f"VM '{name}' started."}


@app.post("/api/vms/{name}/stop", tags=["vms"])
def api_stop_vm(name: str):
    """Stop a running VM."""
    try:
        _manager.stop_vm(name)
    except VMManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "message": f"VM '{name}' stopped."}


@app.delete("/api/vms/{name}", tags=["vms"])
def api_remove_vm(name: str):
    """Remove a VM and its disk image."""
    try:
        _manager.remove_vm(name)
    except VMManagerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "message": f"VM '{name}' removed."}


@app.post("/api/vms/{name}/playbook", tags=["vms"])
def api_run_playbook(name: str, req: RunPlaybookRequest, background_tasks: BackgroundTasks):
    """Run an Ansible playbook on an existing VM."""
    output_lines: list[str] = []

    def _task():
        try:
            _manager.run_playbook(name, req.playbook, output_lines)
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")

    background_tasks.add_task(_task)
    return {"status": "accepted", "message": f"Running playbook '{req.playbook}' on '{name}'"}


@app.get("/api/distributions", tags=["distributions"])
def api_list_distributions():
    """Return all defined distributions."""
    dists = _manager.list_distributions()
    return [
        {
            "name": d.name,
            "display_name": d.display_name,
            "image_file": d.image_file,
            "playbooks": d.playbooks,
        }
        for d in dists
    ]


@app.get("/api/playbooks", tags=["playbooks"])
def api_list_playbooks():
    """Return all available playbooks."""
    return {"playbooks": _manager.list_playbooks()}


@app.get("/api/health", tags=["misc"])
def api_health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Streaming endpoint for real-time VM creation output
# ---------------------------------------------------------------------------


@app.post("/api/vms/stream", tags=["vms"])
def api_create_vm_stream(req: CreateVMRequest):
    """Create a VM and stream progress as Server-Sent Events."""
    output_lines: list[str] = []
    done = threading.Event()

    def _task():
        try:
            _manager.create_vm(
                req.hostname,
                req.vcpus,
                req.ram_mb,
                req.distribution,
                output_lines,
            )
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")
        finally:
            done.set()

    t = threading.Thread(target=_task, daemon=True)
    t.start()

    def _generate() -> Generator[str, None, None]:
        sent = 0
        while not done.is_set() or sent < len(output_lines):
            while sent < len(output_lines):
                yield f"data: {output_lines[sent]}\n\n"
                sent += 1
            if not done.is_set():
                import time
                time.sleep(0.1)
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Web interface  (HTML routes)
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, tags=["web"])
def web_index(request: Request):
    """Main dashboard – list all VMs."""
    error: str | None = None
    vms = []
    dists = []
    try:
        vms = _manager.list_vms()
        dists = _manager.list_distributions()
    except VMManagerError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "vms": vms, "distributions": dists, "error": error},
    )


@app.get("/vms/create", response_class=HTMLResponse, tags=["web"])
def web_create_vm_form(request: Request):
    """VM creation form."""
    dists = _manager.list_distributions()
    return templates.TemplateResponse(
        "create_vm.html",
        {"request": request, "distributions": dists},
    )


@app.post("/vms/create", response_class=HTMLResponse, tags=["web"])
def web_create_vm(
    request: Request,
    background_tasks: BackgroundTasks,
    hostname: str = Form(...),
    cpus: int = Form(2),
    ram_mb: int = Form(2048),
    distribution: str = Form(""),
):
    """Handle VM creation form submission."""
    output_lines: list[str] = []

    def _task():
        try:
            _manager.create_vm(
                hostname,
                cpus,
                ram_mb,
                distribution or None,
                output_lines,
            )
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")

    background_tasks.add_task(_task)
    return templates.TemplateResponse(
        "create_vm_progress.html",
        {
            "request": request,
            "hostname": config.full_hostname(hostname),
        },
    )


@app.post("/vms/{name}/start", response_class=HTMLResponse, tags=["web"])
def web_start_vm(request: Request, name: str):
    """Handle start-VM form submission and redirect back."""
    try:
        _manager.start_vm(name)
    except VMManagerError:
        pass
    return RedirectResponse(url=f"/vms/{name}", status_code=303)


@app.post("/vms/{name}/stop", response_class=HTMLResponse, tags=["web"])
def web_stop_vm(request: Request, name: str):
    """Handle stop-VM form submission and redirect back."""
    try:
        _manager.stop_vm(name)
    except VMManagerError:
        pass
    return RedirectResponse(url=f"/vms/{name}", status_code=303)


@app.post("/vms/{name}/remove", response_class=HTMLResponse, tags=["web"])
def web_remove_vm(request: Request, name: str):
    """Handle remove-VM form submission and redirect to dashboard."""
    try:
        _manager.remove_vm(name)
    except VMManagerError:
        pass
    return RedirectResponse(url="/", status_code=303)


@app.post("/vms/{name}/playbook", response_class=HTMLResponse, tags=["web"])
def web_run_playbook(
    request: Request,
    name: str,
    background_tasks: BackgroundTasks,
    playbook: str = Form(...),
):
    """Handle run-playbook form submission."""
    output_lines: list[str] = []

    def _task():
        try:
            _manager.run_playbook(name, playbook, output_lines)
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")

    background_tasks.add_task(_task)
    return templates.TemplateResponse(
        "playbook_progress.html",
        {
            "request": request,
            "hostname": name,
            "playbook": playbook,
        },
    )


@app.get("/vms/{name}", response_class=HTMLResponse, tags=["web"])
def web_vm_detail(request: Request, name: str):
    """VM detail / management page."""
    vm = _manager.get_vm(name)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found.")
    playbooks = _manager.list_playbooks()
    return templates.TemplateResponse(
        "vm_detail.html",
        {"request": request, "vm": vm, "playbooks": playbooks},
    )


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


def run_server() -> None:  # pragma: no cover
    """Start the uvicorn server (called by ``tuxlablab-server`` script)."""
    uvicorn.run(
        "tuxlablab.api:app",
        host=config.host,
        port=config.port,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run_server()
