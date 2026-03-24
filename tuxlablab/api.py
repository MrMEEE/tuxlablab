"""FastAPI REST API + HTML web-interface for tuxlablab."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from pathlib import Path
import ssl
import urllib.request

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import tuxlablab.db as _db
from tuxlablab.config import config
from tuxlablab.core import VMManager, VMManagerError
from tuxlablab.distribution_presets import get_preset, presets_for_web
from tuxlablab.rh_download import (
    RHDownloadError,
    get_rhel_kvm_download_info,
    rhel_version_from_filename,
)

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
_download_status_lock = threading.Lock()
_download_status: dict[str, dict[str, str | int | None]] = {}


def _set_download_status(
    name: str,
    *,
    state: str,
    downloaded: int = 0,
    total: int | None = None,
    message: str = "",
) -> None:
    with _download_status_lock:
        _download_status[name] = {
            "state": state,
            "downloaded": downloaded,
            "total": total,
            "message": message,
        }


def _get_download_status(name: str) -> dict[str, str | int | None]:
    with _download_status_lock:
        row = _download_status.get(name)
        if row is None:
            return {
                "state": "idle",
                "downloaded": 0,
                "total": None,
                "message": "",
            }
        return dict(row)

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
    download_url: str = ""


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
        download_url=req.download_url,
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
    if not _db.is_valid_setting_key(key):
        raise HTTPException(status_code=404, detail=f"Unknown setting '{key}'")
    val = _db.get_setting(key)
    return {"key": key, "value": val}


@app.put("/api/settings/{key}", tags=["settings"])
def api_set_setting(key: str, req: UpsertSettingRequest):
    """Set a lab setting value."""
    try:
        _db.set_setting(key, req.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
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


def _vm_create_stream_response(
    hostname: str,
    vcpus: int,
    ram_mb: int,
    distribution: str | None,
):
    """Create a VM and stream progress as Server-Sent Events."""
    output_lines: list[str] = []
    done = threading.Event()

    def _task():
        try:
            _manager.create_vm(
                hostname, vcpus, ram_mb, distribution, output_lines,
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


@app.post("/api/vms/stream", tags=["vms"])
def api_create_vm_stream(req: CreateVMRequest):
    """Create a VM and stream progress as Server-Sent Events."""
    return _vm_create_stream_response(
        hostname=req.hostname,
        vcpus=req.vcpus,
        ram_mb=req.ram_mb,
        distribution=req.distribution,
    )


@app.get("/api/vm-create-stream", tags=["vms"])
def api_create_vm_stream_get(
    hostname: str,
    vcpus: int = 2,
    ram_mb: int = 2048,
    distribution: str | None = None,
):
    """Create a VM and stream progress for web EventSource clients."""
    return _vm_create_stream_response(
        hostname=hostname,
        vcpus=vcpus,
        ram_mb=ram_mb,
        distribution=distribution or None,
    )


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
        request=request,
        name="index.html",
        context={"request": request, "vms": vms, "distributions": dists, "error": error},
    )


@app.get("/vms/create", response_class=HTMLResponse, tags=["web"])
def web_create_vm_form(request: Request):
    """VM creation form."""
    dists = _manager.list_distributions()
    return templates.TemplateResponse(
        request=request,
        name="create_vm.html",
        context={"request": request, "distributions": dists},
    )


@app.post("/vms/create", response_class=HTMLResponse, tags=["web"])
def web_create_vm(
    request: Request,
    hostname: str = Form(...),
    cpus: int = Form(2),
    ram_mb: int = Form(2048),
    distribution: str = Form(""),
):
    """Handle VM creation form submission."""
    return templates.TemplateResponse(
        request=request,
        name="create_vm_progress.html",
        context={
            "request": request,
            "hostname": config.full_hostname(hostname),
            "raw_hostname": hostname,
            "cpus": cpus,
            "ram_mb": ram_mb,
            "distribution": distribution,
        },
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
        request=request,
        name="playbook_progress.html",
        context={"request": request, "hostname": name, "playbook": playbook},
    )


@app.get("/vms/{name}", response_class=HTMLResponse, tags=["web"])
def web_vm_detail(request: Request, name: str):
    """VM detail / management page."""
    vm = _manager.get_vm(name)
    if vm is None:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found.")
    playbooks = _manager.list_playbooks()
    return templates.TemplateResponse(
        request=request,
        name="vm_detail.html",
        context={"request": request, "vm": vm, "playbooks": playbooks},
    )


# ---------------------------------------------------------------------------
# Web interface – Distributions management
# ---------------------------------------------------------------------------


@app.get("/distributions", response_class=HTMLResponse, tags=["web"])
def web_distributions(request: Request):
    """Distributions management page."""
    dists = _db.list_distributions()
    images_dir = config.images_dir
    available_playbooks = _manager.list_playbooks()
    for d in dists:
        d["image_present"] = (images_dir / d["image_file"]).exists()
        d["is_rhel"] = d["image_file"].startswith("rhel-")
    return templates.TemplateResponse(
        request=request,
        name="distributions.html",
        context={
            "request": request,
            "distributions": dists,
            "preset_options": presets_for_web(),
            "available_playbooks": available_playbooks,
        },
    )


@app.post("/distributions/add-preset", response_class=HTMLResponse, tags=["web"])
def web_distribution_add_preset(
    request: Request,
    family: str = Form(...),
    version: str = Form(...),
    playbooks: list[str] = Form([]),
):
    try:
        preset = get_preset(family, version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    selected_playbooks = " ".join([p.strip() for p in playbooks if p.strip()])

    _db.upsert_distribution(
        name=preset["name"],
        display_name=preset["display_name"],
        image_file=preset["image_file"],
        playbooks=selected_playbooks or preset["playbooks"],
        download_url=preset["download_url"],
    )
    return RedirectResponse(url="/distributions", status_code=303)


@app.post("/distributions/add", response_class=HTMLResponse, tags=["web"])
def web_distribution_add(
    request: Request,
    name: str = Form(...),
    display_name: str = Form(...),
    image_file: str = Form(...),
    playbooks: list[str] = Form([]),
    download_url: str = Form(""),
):
    selected_playbooks = " ".join([p.strip() for p in playbooks if p.strip()])
    _db.upsert_distribution(
        name=name, display_name=display_name, image_file=image_file,
        playbooks=selected_playbooks, download_url=download_url,
    )
    return RedirectResponse(url="/distributions", status_code=303)


@app.post("/distributions/{name}/update-url", response_class=HTMLResponse, tags=["web"])
def web_distribution_update_url(request: Request, name: str, download_url: str = Form("")):
    """Update only the download_url for an existing distribution."""
    dist = _db.get_distribution(name)
    if dist is None:
        raise HTTPException(status_code=404, detail=f"Distribution '{name}' not found.")
    _db.upsert_distribution(
        name=dist["name"],
        display_name=dist["display_name"],
        image_file=dist["image_file"],
        playbooks=dist["playbooks"],
        download_url=download_url.strip(),
    )
    return RedirectResponse(url="/distributions", status_code=303)


@app.post("/distributions/{name}/download", response_class=JSONResponse, tags=["web"])
def web_distribution_download(request: Request, name: str):
    """Start a background download of a distribution image."""
    dist = _db.get_distribution(name)
    if dist is None:
        raise HTTPException(status_code=404, detail=f"Distribution '{name}' not found.")

    image_file = dist["image_file"]
    images_dir = config.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)
    dest = images_dir / image_file

    # Always prefer a manually configured download_url (any distro, no cert needed).
    url = dist.get("download_url", "").strip()
    cert_info: dict[str, str] | None = None

    if not url and image_file.startswith("rhel-"):
        # No stored URL: construct the Red Hat CDN URL and resolve entitlement certs.
        try:
            version = rhel_version_from_filename(image_file)
            info = get_rhel_kvm_download_info(
                rhel_version=version,
                ca_cert=_db.get_setting("rhn_ca_cert") or "",
                cert=_db.get_setting("rhn_entitlement_cert") or "",
                key=_db.get_setting("rhn_entitlement_key") or "",
            )
        except RHDownloadError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        url = info["url"]
        cert_info = info

    if not url:
        raise HTTPException(
            status_code=400,
            detail="No download URL configured for this distribution.",
        )

    existing = _get_download_status(name)
    if existing["state"] == "running":
        return JSONResponse(
            {"status": "already-running", "name": name},
            status_code=409,
        )

    def _download():
        part_path = dest.with_suffix(dest.suffix + ".part")
        resumed = part_path.stat().st_size if part_path.exists() else 0
        headers: dict[str, str] = {}
        if resumed > 0:
            headers["Range"] = f"bytes={resumed}-"

        context: ssl.SSLContext | None = None
        if cert_info is not None:
            context = ssl.create_default_context(cafile=cert_info["ca_cert"])
            context.load_cert_chain(
                certfile=cert_info["cert"],
                keyfile=cert_info["key"],
            )

        _set_download_status(
            name,
            state="running",
            downloaded=resumed,
            total=None,
            message="Downloading...",
        )

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=context, timeout=30) as resp:
                total: int | None = None
                content_range = resp.headers.get("Content-Range")
                if content_range and "/" in content_range:
                    total_part = content_range.rsplit("/", 1)[-1].strip()
                    if total_part.isdigit():
                        total = int(total_part)
                elif resp.headers.get("Content-Length"):
                    total = int(resp.headers["Content-Length"]) + resumed

                downloaded = resumed
                _set_download_status(
                    name,
                    state="running",
                    downloaded=downloaded,
                    total=total,
                    message="Downloading...",
                )

                mode = "ab" if resumed > 0 else "wb"
                with part_path.open(mode) as file_obj:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        file_obj.write(chunk)
                        downloaded += len(chunk)
                        _set_download_status(
                            name,
                            state="running",
                            downloaded=downloaded,
                            total=total,
                            message="Downloading...",
                        )

            part_path.replace(dest)
            _set_download_status(
                name,
                state="completed",
                downloaded=dest.stat().st_size if dest.exists() else 0,
                total=dest.stat().st_size if dest.exists() else None,
                message="Download complete",
            )
        except Exception as exc:
            _set_download_status(
                name,
                state="error",
                downloaded=0,
                total=None,
                message=str(exc),
            )

    threading.Thread(target=_download, daemon=True).start()
    return JSONResponse({"status": "started", "name": name})


@app.get("/api/distributions/{name}/download-status", tags=["distributions"])
def api_distribution_download_status(name: str):
    """Get current background download status for one distribution."""
    dist = _db.get_distribution(name)
    if dist is None:
        raise HTTPException(status_code=404, detail=f"Distribution '{name}' not found.")

    status = _get_download_status(name)
    image_path = config.images_dir / dist["image_file"]
    if status["state"] == "idle" and image_path.exists():
        size = image_path.stat().st_size
        return {
            "state": "completed",
            "downloaded": size,
            "total": size,
            "message": "Image already present",
        }
    return status


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
        request=request,
        name="settings.html",
        context={
            "request": request,
            "settings": settings,
        },
    )


@app.post("/settings/{key}", response_class=HTMLResponse, tags=["web"])
def web_setting_update(request: Request, key: str, value: str = Form(...)):
    """Update a single setting value."""
    try:
        _db.set_setting(key, value)
    except ValueError as exc:
        settings = _db.list_settings()
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={"request": request, "settings": settings, "error": str(exc)},
            status_code=400,
        )
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
