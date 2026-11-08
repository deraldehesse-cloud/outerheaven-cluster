#!/usr/bin/env python3
"""
OuterHeaven Metal-Bridge
REST-Bridge auf metal-dgear zur Steuerung und Überwachung der
Catlord-Reverse-Engineering-Pipeline, REA-Sandbox (Docker) und Web-IDE Services.
"""

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psutil
import requests
import uvicorn

# Pfadkonfiguration
DROPZONE_DIR = Path("/mnt/storage-fast/rea-sandbox/workspace/catlord-dropzone")
OUTPUT_BASE_DIR = Path("/mnt/storage-fast/rea-sandbox/output/catlord")
SANDBOX_OUTPUT_DIR = Path("/mnt/storage-fast/rea-sandbox/output")
SANDBOX_WORKSPACE_DIR = Path("/mnt/storage-fast/rea-sandbox/workspace")
PIPELINE_SCRIPT = Path("/mnt/storage-fast/scripts/process-catlord-apks.sh")
PIPELINE_LOG = OUTPUT_BASE_DIR / "pipeline.log"
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = "deepseek-coder:6.7b-instruct-q4_K_M"
DOCKER_CONTAINER_NAME = "rea-sandbox"

app = FastAPI(
    title="OuterHeaven Metal-Bridge API",
    version="1.1.0",
    description="REST-Bridge für Web-IDE, REA-Sandbox, Catlord-Pipeline und mobile Control-App auf metal-dgear",
)

# CORS aktivieren für mobile App und Browser-Zugriffe
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# Datenmodelle
# ==========================================

class DecompileRequest(BaseModel):
    apk_name: str
    target_subfolder: Optional[str] = None


# ==========================================
# State Tracking
# ==========================================

class PipelineTracker:
    def __init__(self):
        self.is_running: bool = False
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[str] = None
        self.end_time: Optional[str] = None
        self.last_exit_code: Optional[int] = None

    def check_running(self) -> bool:
        if self.process is not None:
            ret = self.process.poll()
            if ret is None:
                self.is_running = True
                return True
            else:
                self.is_running = False
                self.last_exit_code = ret
                if self.end_time is None:
                    self.end_time = datetime.now(timezone.utc).isoformat()
                self.process = None
                return False

        # Fallback: Prüfen ob ein externer Prozess läuft
        try:
            res = subprocess.run(["pgrep", "-f", "process-catlord-apks.sh"], capture_output=True, text=True)
            self.is_running = (res.returncode == 0)
        except Exception:
            self.is_running = False
        return self.is_running


class SandboxJobManager:
    def __init__(self):
        self.jobs: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def create_job(self, apk_name: str, target_dir: str) -> Dict[str, Any]:
        job = {
            "id": f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            "apk_name": apk_name,
            "target_dir": target_dir,
            "status": "running",
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "exit_code": None,
            "log_tail": "",
        }
        with self._lock:
            self.jobs.insert(0, job)
            if len(self.jobs) > 20:
                self.jobs.pop()
        return job

    def update_job(self, job_id: str, status: str, exit_code: Optional[int] = None, log_tail: str = ""):
        with self._lock:
            for job in self.jobs:
                if job["id"] == job_id:
                    job["status"] = status
                    job["exit_code"] = exit_code
                    if log_tail:
                        job["log_tail"] = log_tail
                    if status in ("completed", "failed"):
                        job["end_time"] = datetime.now(timezone.utc).isoformat()
                    break

    def get_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.jobs)


tracker = PipelineTracker()
job_manager = SandboxJobManager()


# ==========================================
# Hilfsfunktionen
# ==========================================

def format_bytes(size: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def check_service_status(service_name: str, user: bool = False) -> str:
    """Checks systemd status using systemctl."""
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd.extend(["is-active", service_name])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        status = res.stdout.strip()
        return status if status else ("inactive" if res.returncode != 0 else "active")
    except Exception as e:
        return f"error: {str(e)}"


def get_ollama_status() -> Dict[str, Any]:
    """Queries local Ollama instance for running and installed models."""
    status_info = {
        "service_active": check_service_status("ollama.service", user=False) == "active",
        "reachable": False,
        "active_models": [],
        "available_models": [],
        "default_model": DEFAULT_OLLAMA_MODEL,
        "default_model_available": False,
    }
    try:
        ps_res = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=2)
        if ps_res.status_code == 200:
            status_info["reachable"] = True
            data = ps_res.json()
            status_info["active_models"] = [m.get("name") for m in data.get("models", [])]

        tags_res = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
        if tags_res.status_code == 200:
            status_info["reachable"] = True
            data = tags_res.json()
            models = [m.get("name") for m in data.get("models", [])]
            status_info["available_models"] = models
            status_info["default_model_available"] = any(DEFAULT_OLLAMA_MODEL in m for m in models)
    except Exception:
        pass
    return status_info


def get_docker_sandbox_status() -> Dict[str, Any]:
    """Inspects the rea-sandbox container via docker inspect."""
    try:
        res = subprocess.run(
            ["docker", "inspect", DOCKER_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode != 0:
            return {
                "container_name": DOCKER_CONTAINER_NAME,
                "status": "not_found",
                "is_running": false,
                "error": res.stderr.strip()
            }
        
        data = json.loads(res.stdout)[0]
        state = data.get("State", {})
        config = data.get("Config", {})
        
        status_str = state.get("Status", "unknown")
        is_running = state.get("Running", False)
        started_at = state.get("StartedAt", "")
        pid = state.get("Pid", 0)
        image = config.get("Image", "")

        # Try to get JADX version inside container
        jadx_version = "unknown"
        if is_running:
            try:
                j_res = subprocess.run(
                    ["docker", "exec", DOCKER_CONTAINER_NAME, "jadx", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if j_res.returncode == 0:
                    jadx_version = j_res.stdout.strip()
            except Exception:
                pass

        return {
            "container_name": DOCKER_CONTAINER_NAME,
            "status": status_str,
            "is_running": is_running,
            "started_at": started_at,
            "pid": pid,
            "image": image,
            "jadx_version": jadx_version,
            "error": None
        }
    except Exception as e:
        return {
            "container_name": DOCKER_CONTAINER_NAME,
            "status": "error",
            "is_running": False,
            "error": str(e)
        }


# ==========================================
# API Endpoints: System & Dashboard
# ==========================================

@app.get("/")
def root():
    return {
        "service": "OuterHeaven Metal-Bridge",
        "node": "metal-dgear",
        "tailscale_ip": "100.96.88.84",
        "endpoints": [
            "/api/status",
            "/api/dropzone",
            "/api/pipeline/trigger",
            "/api/pipeline/status",
            "/api/findings/latest",
            "/api/sandbox/status",
            "/api/sandbox/decompile",
            "/api/sandbox/jobs",
            "/api/sandbox/outputs",
            "/api/sandbox/container/restart",
        ]
    }


@app.get("/api/status")
def get_status():
    """System status: CPU, RAM, services, Ollama models, and pipeline status."""
    vmem = psutil.virtual_memory()
    disk = psutil.disk_usage("/mnt/storage-fast") if os.path.exists("/mnt/storage-fast") else psutil.disk_usage("/")
    
    code_server_status = check_service_status("code-server.service", user=True)
    ollama_info = get_ollama_status()
    is_pipeline_active = tracker.check_running()
    sandbox_status = get_docker_sandbox_status()

    if ollama_info["active_models"]:
        active_model = ollama_info["active_models"][0]
    elif ollama_info["default_model_available"]:
        active_model = DEFAULT_OLLAMA_MODEL
    elif ollama_info["available_models"]:
        active_model = ollama_info["available_models"][0]
    else:
        active_model = "None (Ollama offline/empty)"

    return {
        "node": "metal-dgear",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "cores": psutil.cpu_count(logical=True),
        },
        "ram": {
            "total_bytes": vmem.total,
            "used_bytes": vmem.used,
            "available_bytes": vmem.available,
            "percent": vmem.percent,
            "total_formatted": format_bytes(vmem.total),
            "used_formatted": format_bytes(vmem.used),
        },
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "percent": disk.percent,
            "free_formatted": format_bytes(disk.free),
        },
        "services": {
            "code_server": {
                "unit": "code-server.service",
                "status": code_server_status,
                "is_active": (code_server_status == "active"),
                "port": 8443,
                "url": "http://100.96.88.84:8443",
            },
            "ollama": {
                "unit": "ollama.service",
                "status": "active" if ollama_info["service_active"] else "inactive",
                "is_active": ollama_info["service_active"],
                "reachable": ollama_info["reachable"],
                "port": 11434,
                "active_model": active_model,
                "running_models": ollama_info["active_models"],
                "installed_models": ollama_info["available_models"],
            },
            "rea_sandbox": sandbox_status
        },
        "pipeline": {
            "is_running": is_pipeline_active,
            "start_time": tracker.start_time,
            "end_time": tracker.end_time,
            "last_exit_code": tracker.last_exit_code,
        }
    }


@app.get("/api/dropzone")
def get_dropzone():
    """Lists files inside the catlord dropzone folder."""
    if not DROPZONE_DIR.exists():
        return {"dropzone_dir": str(DROPZONE_DIR), "files": [], "count": 0}

    files = []
    for entry in DROPZONE_DIR.iterdir():
        if entry.is_file():
            stat = entry.stat()
            files.append({
                "name": entry.name,
                "size_bytes": stat.st_size,
                "size_formatted": format_bytes(stat.st_size),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "is_apk": entry.name.lower().endswith(".apk"),
            })

    files.sort(key=lambda x: x["modified_at"], reverse=True)

    return {
        "dropzone_dir": str(DROPZONE_DIR),
        "count": len(files),
        "apk_count": sum(1 for f in files if f["is_apk"]),
        "files": files,
    }


@app.post("/api/pipeline/trigger")
def trigger_pipeline():
    """Triggers the Catlord reverse engineering pipeline script in the background."""
    if tracker.check_running():
        return {
            "status": "already_running",
            "message": "Catlord-Pipeline läuft bereits im Hintergrund.",
            "start_time": tracker.start_time,
        }

    if not PIPELINE_SCRIPT.exists():
        raise HTTPException(status_code=500, detail=f"Runner-Skript {PIPELINE_SCRIPT} nicht gefunden.")

    OUTPUT_BASE_DIR.mkdir(parents=True, exist_ok=True)
    
    log_file = open(PIPELINE_LOG, "w")
    devnull = open(os.devnull, "r")

    proc = subprocess.Popen(
        ["/bin/bash", str(PIPELINE_SCRIPT)],
        stdin=devnull,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=str(PIPELINE_SCRIPT.parent),
        preexec_fn=os.setpgrp,
    )

    tracker.process = proc
    tracker.is_running = True
    tracker.start_time = datetime.now(timezone.utc).isoformat()
    tracker.end_time = None
    tracker.last_exit_code = None

    return {
        "status": "started",
        "message": "Catlord-Pipeline erfolgreich im Hintergrund gestartet.",
        "pid": proc.pid,
        "start_time": tracker.start_time,
        "log_file": str(PIPELINE_LOG),
    }


@app.get("/api/pipeline/status")
def get_pipeline_status():
    """Returns current pipeline execution status and latest log output."""
    is_running = tracker.check_running()
    log_content = ""
    if PIPELINE_LOG.exists():
        try:
            with open(PIPELINE_LOG, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                log_content = "".join(lines[-40:])
        except Exception as e:
            log_content = f"Fehler beim Lesen des Logs: {str(e)}"

    return {
        "is_running": is_running,
        "start_time": tracker.start_time,
        "end_time": tracker.end_time,
        "last_exit_code": tracker.last_exit_code,
        "log_tail": log_content,
    }


@app.get("/api/findings/latest")
def get_latest_findings(
    package: Optional[str] = Query(None, description="Optionaler Paketname für gezielte Findings"),
    format: Optional[str] = Query(None, description="Format: 'text' oder 'json'"),
    accept: Optional[str] = Header(None)
):
    """Returns the latest llm_findings.md from /mnt/storage-fast/rea-sandbox/output/catlord/."""
    if not OUTPUT_BASE_DIR.exists():
        raise HTTPException(status_code=404, detail=f"Ausgabeverzeichnis {OUTPUT_BASE_DIR} existiert nicht.")

    candidates = []
    all_packages = set()

    for root, dirs, files in os.walk(OUTPUT_BASE_DIR):
        rel_pkg = Path(root).relative_to(OUTPUT_BASE_DIR).parts
        if rel_pkg:
            all_packages.add(rel_pkg[0])

        for fname in files:
            if "findings" in fname.lower() and fname.endswith(".md"):
                fpath = Path(root) / fname
                pkg_name = rel_pkg[0] if rel_pkg else "general"
                mtime = fpath.stat().st_mtime
                candidates.append({
                    "path": fpath,
                    "package": pkg_name,
                    "filename": fname,
                    "mtime": mtime,
                })

    if not candidates:
        raise HTTPException(status_code=404, detail="Keine Findings-Dateien (.md) in Catlord-Output gefunden.")

    candidates.sort(key=lambda x: x["mtime"], reverse=True)

    selected = None
    if package:
        for c in candidates:
            if c["package"].lower() == package.lower():
                selected = c
                break
        if not selected:
            raise HTTPException(status_code=404, detail=f"Keine Findings für Paket '{package}' gefunden.")
    else:
        llm_findings = [c for c in candidates if c["filename"] == "llm_findings.md"]
        selected = llm_findings[0] if llm_findings else candidates[0]

    try:
        content = selected["path"].read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Lesen der Datei: {str(e)}")

    modified_iso = datetime.fromtimestamp(selected["mtime"], tz=timezone.utc).isoformat()

    if format == "text" or (accept and "text/plain" in accept and format != "json"):
        return Response(content=content, media_type="text/markdown; charset=utf-8")

    return {
        "package": selected["package"],
        "filename": selected["filename"],
        "file_path": str(selected["path"]),
        "modified_at": modified_iso,
        "content": content,
        "available_packages": sorted(list(all_packages)),
    }


# ==========================================
# API Endpoints: REA-Sandbox (Docker)
# ==========================================

@app.get("/api/sandbox/status")
def get_sandbox_status():
    """Gibt den detaillierten Docker-Container-Status von rea-sandbox zurück."""
    return get_docker_sandbox_status()


@app.post("/api/sandbox/container/restart")
def restart_sandbox_container():
    """Startet den rea-sandbox Docker-Container neu."""
    try:
        res = subprocess.run(
            ["docker", "restart", DOCKER_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=15
        )
        if res.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Fehler beim Neustart: {res.stderr.strip()}")
        
        return {
            "status": "restarted",
            "message": f"Container {DOCKER_CONTAINER_NAME} erfolgreich neugestartet.",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timeout beim Neustarten des Containers")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sandbox/decompile")
def decompile_apk_in_sandbox(req: DecompileRequest, background_tasks: BackgroundTasks):
    """
    Startet einen gezielten JADX-Dekompilierungsjob innerhalb des rea-sandbox Containers.
    """
    apk_file = DROPZONE_DIR / req.apk_name
    if not apk_file.exists():
        raise HTTPException(status_code=404, detail=f"APK '{req.apk_name}' existiert nicht in der Dropzone.")

    pkg_name = req.apk_name[:-4] if req.apk_name.lower().endswith(".apk") else req.apk_name
    target_sub = req.target_subfolder if req.target_subfolder else f"catlord/{pkg_name}"
    
    # Sicherstellen, dass das Zielverzeichnis existiert
    target_full_path = SANDBOX_OUTPUT_DIR / target_sub
    target_full_path.mkdir(parents=True, exist_ok=True)

    # Job registrieren
    job = job_manager.create_job(apk_name=req.apk_name, target_dir=target_sub)

    # Asynchron im Hintergrund ausführen
    def run_decompile_job(job_id: str, apk_filename: str, out_folder: str):
        cmd = [
            "docker", "exec", DOCKER_CONTAINER_NAME,
            "jadx",
            "-d", f"/output/{out_folder}",
            f"/workspace/catlord-dropzone/{apk_filename}",
            "--no-imports",
            "--show-bad-code"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            tail = (res.stdout + "\n" + res.stderr)[-500:]
            if res.returncode == 0:
                job_manager.update_job(job_id, status="completed", exit_code=0, log_tail=tail)
            else:
                job_manager.update_job(job_id, status="failed", exit_code=res.returncode, log_tail=tail)
        except Exception as e:
            job_manager.update_job(job_id, status="failed", exit_code=-1, log_tail=str(e))

    thread = threading.Thread(
        target=run_decompile_job,
        args=(job["id"], req.apk_name, target_sub),
        daemon=True
    )
    thread.start()

    return {
        "status": "started",
        "message": f"Dekompilierung für '{req.apk_name}' gestartet.",
        "job": job
    }


@app.get("/api/sandbox/jobs")
def get_sandbox_jobs():
    """Gibt die Liste aller kürzlich ausgeführten Sandbox-Dekompilierungsjobs zurück."""
    return {
        "count": len(job_manager.jobs),
        "jobs": job_manager.get_jobs()
    }


@app.get("/api/sandbox/outputs")
def get_sandbox_outputs():
    """
    Listet alle dekompilierten Pakete und Artefakte in /mnt/storage-fast/rea-sandbox/output/ auf.
    """
    if not SANDBOX_OUTPUT_DIR.exists():
        return {"output_dir": str(SANDBOX_OUTPUT_DIR), "folders": [], "count": 0}

    folders = []
    
    # Durchsuche Ordner bis Tiefe 2 (z. B. catlord/QueensBrothel)
    for root, dirs, files in os.walk(SANDBOX_OUTPUT_DIR):
        rpath = Path(root)
        rel = rpath.relative_to(SANDBOX_OUTPUT_DIR)
        
        # Ignoriere Root-Level und zu tiefe Verschachtelungen
        if len(rel.parts) == 0 or len(rel.parts) > 2:
            continue
        
        # Wenn der Ordner "sources" oder "resources" ist, überspringen (gehört zum übergeordneten Paket)
        if rel.parts[-1] in ("sources", "resources"):
            continue

        # Prüfe ob dieser Ordner ein APK-Output-Ordner ist
        has_sources = (rpath / "sources").exists()
        has_findings = any("findings" in f.lower() for f in files)
        
        # Berechne Gesamtgröße & Dateianzahl
        total_size = 0
        file_count = 0
        latest_mtime = rpath.stat().st_mtime
        
        for p in rpath.rglob("*"):
            if p.is_file():
                file_count += 1
                try:
                    st = p.stat()
                    total_size += st.st_size
                    if st.st_mtime > latest_mtime:
                        latest_mtime = st.st_mtime
                except Exception:
                    pass

        if file_count > 0:
            folders.append({
                "name": rel.parts[-1],
                "relative_path": str(rel),
                "file_count": file_count,
                "size_bytes": total_size,
                "size_formatted": format_bytes(total_size),
                "modified_at": datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat(),
                "has_sources": has_sources,
                "has_findings": has_findings,
            })

    # Sortiere neueste zuerst
    folders.sort(key=lambda x: x["modified_at"], reverse=True)

    return {
        "output_dir": str(SANDBOX_OUTPUT_DIR),
        "count": len(folders),
        "folders": folders
    }


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8444, reload=False)
