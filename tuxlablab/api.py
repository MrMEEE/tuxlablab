"""FastAPI REST API + HTML web-interface for tuxlablab."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import tuxlablab.db as _db
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


class UpsertDistributionRequest(BaseModel):
    display_name: str
    image_file: str
    playbooks: str = ""


class UpsertSettingRequest(BaseModel):
    value: str


# ---------------------------------------------------------------------------
# REST API – VMs
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
                req.hostname, req.vcpus, req.ram_mb, req.distribution, output_lines,
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


# ---------------------------------------------------------------------------
# REST API – Distributions (DB-backed CRUD)
# ---------------------------------------------------------------------------


@app.get("/api/distributions", tags=["distributions"])
def api_list_distributions():
    """Return all distributions stored in the database."""
    return _db.list_distributions()


@app.get("/api/distributions/{name}", tags=["distributions"])
def api_get_distribution(name: str):
    """Return a single distribution by name."""
    row = _db.get_distribution(name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Distribution '{name}' not found.")
    return row


@app.put("/api/distributions/{name}", tags=["distributions"])
def api_upsert_distribution(name: str, req: UpsertDistributionRequest):
    """Create or update a distribution."""
    _db.upsert_distribution(
        name=name,
        display_name=req.display_name,
        image_file=req.image_file,
        playbooks=req.playbooks,
    )
    return {"status": "ok", "name": name}


@app.delete("/api/distributions/{name}", tags=["distributions"])
def api_delete_distribution(name: str):
    """Delete a distribution."""
    removed = _db.delete_distribution(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Distribution '{name}' not found.")
    return {"status": "ok", "name": name}


# ---------------------------------------------------------------------------
# REST API – Settings (DB-backed)
# ---------------------------------------------------------------------------


@app.get("/api/settings", tags=["settings"])
def api_list_settings():
    """Return all lab settings stored in the database."""
    return _db.list_settings()


@app.get("/api/settings/{key}", tags=["settings"])
def api_get_setting(key: str):
    """Return a single setting value."""
    val = _db.get_setting(key)
    return {"key": key, "value": val}


@app.put("/api/settings/{key}", tags=["settings"])
def api_set_setting(key: str, req: UpsertSettingRequest):
    """Set a lab setting value."""
    _db.set_setting(key, req.value)
    return {"status": "ok", "key": key, "value": req.value}


# ---------------------------------------------------------------------------
# REST API – Playbooks + health
# ---------------------------------------------------------------------------


@app.get("/api/playbooks", tags=["playbooks"])
def api_list_playbooks():
    """Return all available playbooks."""
    return {"playbooks": _manager.list_playbooks()}


@app.get("/api/health", tags=["misc"])
def api_health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------


@app.post("/api/vms/stream", tags=["vms"])
def api_create_vm_stream(req: CreateVMRequest):
    """Create a VM and stream progress as Server-Sent Events."""
    output_lines: list[str] = []
    done = threading.Event()

    def _task():
        try:
            _manager.create_vm(
                req.hostname, req.vcpus, req.ram_mb, req.distribution, output_lines,
            )
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")
        finally:
            done.set()

    threading.Thread(target=_task, daemon=True).start()

    def _generate() -> Generator[str, None, None]:
        sent = 0
        while not done.is_set() or sent < len(output_lines):
            while sent < len(output_lines):
                yield f"data: {output_lines[sent]}\n\n"
                sent += 1
            if not done.is_set():
                time.sleep(0.1)
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Web interface – VM routes
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
            _manager.create_vm(hostname, cpus, ram_mb, distribution or None, output_lines)
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")

    background_tasks.add_task(_task)
    return templates.TemplateResponse(
        "create_vm_progress.html",
        {"request": request, "hostname": config.full_hostname(hostname)},
    )


@app.post("/vms/{name}/start", response_class=HTMLResponse, tags=["web"])
def web_start_vm(request: Request, name: str):
    try:
        _manager.start_vm(name)
    except VMManagerError:
        pass
    return RedirectResponse(url=f"/vms/{name}", status_code=303)


@app.post("/vms/{name}/stop", response_class=HTMLResponse, tags=["web"])
def web_stop_vm(request: Request, name: str):
    try:
        _manager.stop_vm(name)
    except VMManagerError:
        pass
    return RedirectResponse(url=f"/vms/{name}", status_code=303)


@app.post("/vms/{name}/remove", response_class=HTMLResponse, tags=["web"])
def web_remove_vm(request: Request, name: str):
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
    output_lines: list[str] = []

    def _task():
        try:
            _manager.run_playbook(name, playbook, output_lines)
        except VMManagerError as exc:
            output_lines.append(f"ERROR: {exc}")

    background_tasks.add_task(_task)
    return templates.TemplateResponse(
        "playbook_progress.html",
        {"request": request, "hostname": name, "playbook": playbook},
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
# Web interface – Distributions management
# ---------------------------------------------------------------------------


@app.get("/distributions", response_class=HTMLResponse, tags=["web"])
def web_distributions(request: Request):
    """Distributions management page."""
    dists = _db.list_distributions()
    return templates.TemplateResponse(
        "distributions.html",
        {"request": request, "distributions": dists},
    )


@app.post("/distributions/add", response_class=HTMLResponse, tags=["web"])
def web_distribution_add(
    request: Request,
    name: str = Form(...),
    display_name: str = Form(...),
    image_file: str = Form(...),
    playbooks: str = Form(""),
):
    _db.upsert_distribution(
        name=name, display_name=display_name, image_file=image_file, playbooks=playbooks,
    )
    return RedirectResponse(url="/distributions", status_code=303)


@app.post("/distributions/{name}/delete", response_class=HTMLResponse, tags=["web"])
def web_distribution_delete(request: Request, name: str):
    _db.delete_distribution(name)
    return RedirectResponse(url="/distributions", status_code=303)


# ---------------------------------------------------------------------------
# Web interface – Settings management
# ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse, tags=["web"])
def web_settings(request: Request):
    """Lab settings management page."""
    settings = _db.list_settings()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings},
    )


@app.post("/settings/{key}", response_class=HTMLResponse, tags=["web"])
def web_setting_update(request: Request, key: str, value: str = Form(...)):
    """Update a single setting value."""
    _db.set_setting(key, value)
    return RedirectResponse(url="/settings", status_code=303)


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
