#!/usr/bin/env python3
"""
OuterHeaven Metal-Bridge
REST-Bridge auf metal-dgear zur Steuerung und Überwachung der
Catlord-Reverse-Engineering-Pipeline, REA-Sandbox (Docker) und Web-IDE Services.
"""

import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Header, Response
from fastapi.responses import HTMLResponse
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

# Catlord Pfade
CATLORD_REPO_DIR = Path("/mnt/storage-fast/outerheaven-cluster-repo/projects/catlord")
if not CATLORD_REPO_DIR.exists():
    CATLORD_REPO_DIR = Path("/home/bigboss/outerheaven-cluster-repo/projects/catlord")
CATLORD_VOICE_NOTES_DIR = CATLORD_REPO_DIR / "voice-notes"
CATLORD_REA_FINDINGS_DIR = CATLORD_REPO_DIR / "rea-findings"
CATLORD_STORAGE_NOTE_DIR = Path("/mnt/storage-beta/projects/catlord-media/gdrive-sync/Note ")
CATLORD_MEDIA_DIR = Path("/mnt/storage-beta/projects/catlord-media")

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
    filter_keyword: Optional[str] = None


class CatlordVoiceNoteRequest(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = "idea"
    content: str


class CatlordAskDeepSeekRequest(BaseModel):
    prompt: str
    model: Optional[str] = "catlord-hermes:latest"
    package: Optional[str] = None
    include_lore: Optional[bool] = False
    save_to_findings: Optional[bool] = True
    save_to_drafts: Optional[bool] = False


class SandboxAnalyzeRequest(BaseModel):
    package: str
    keyword: Optional[str] = None
    custom_prompt: Optional[str] = None


# Spezialisierte Ollama-Agenten auf metal-dgear
OLLAMA_AGENTS = {
    "catlord-hermes:latest": {
        "id": "catlord-hermes:latest",
        "name": "catlord-hermes:latest",
        "title": "Hermes (Story & Dialoge)",
        "role": "Lead-Writer & Narrative-Designer",
        "description": "Hermes 3 8B – Story, Dialoge, Charaktere: Sebastian, Gert Baert, Elliot, Cash/Loco (Casper), Holly",
        "base_model": "hermes3:8b",
        "badge": "STORY",
        "icon": "🎭",
        "default_for": "story"
    },
    "catlord-architect:latest": {
        "id": "catlord-architect:latest",
        "name": "catlord-architect:latest",
        "title": "Architect (Konzepte)",
        "role": "Game-Design-Architekt & Struktur-Assistent",
        "description": "Qwen 2.5 7B – Game-Design, Strukturierung von Voice-Notes, Mechaniken & Inventar-Logik",
        "base_model": "qwen2.5:7b",
        "badge": "CONCEPT",
        "icon": "📐",
        "default_for": "concepts"
    },
    "metal-coder:latest": {
        "id": "metal-coder:latest",
        "name": "metal-coder:latest",
        "title": "Coder (Kotlin/Game)",
        "role": "Senior Android- & Cluster-Entwickler",
        "description": "DeepSeek-Coder 6.7B – Code-Analyse, Kotlin/Python, Jetpack Compose & REA-Sandbox",
        "base_model": "deepseek-coder:6.7b-instruct-q4_K_M",
        "badge": "CODE",
        "icon": "💻",
        "default_for": "code"
    },
    "metal-fast:latest": {
        "id": "metal-fast:latest",
        "name": "metal-fast:latest",
        "title": "Fast (Quick Chat)",
        "role": "Technischer Blitz-Assistent",
        "description": "Qwen 2.5 3B – Blitzschneller Assistent für kurze, präzise Antworten ohne Einleitungsfloskeln",
        "base_model": "qwen2.5:3b",
        "badge": "FAST",
        "icon": "⚡",
        "default_for": "quick"
    }
}


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

    def create_job(self, apk_name: str, target_dir: str, filter_keyword: Optional[str] = None) -> Dict[str, Any]:
        job = {
            "id": f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            "apk_name": apk_name,
            "target_dir": target_dir,
            "filter_keyword": filter_keyword,
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
            "/sandbox",
            "/api/status",
            "/api/models",
            "/api/dropzone",
            "/api/pipeline/trigger",
            "/api/pipeline/status",
            "/api/findings/latest",
            "/api/sandbox/status",
            "/api/sandbox/decompile",
            "/api/sandbox/jobs",
            "/api/sandbox/outputs",
            "/api/sandbox/analyze",
            "/api/sandbox/container/restart",
            "/api/catlord/ask-deepseek",
            "/api/catlord/ask-agent",
            "/api/catlord/voice-note",
            "/api/catlord/docs",
            "/api/catlord/media-stats",
        ]
    }


@app.get("/sandbox", response_class=HTMLResponse)
def sandbox_dashboard():
    """
    Kompakte Dark-Mode HTML-Webview für die Web-IDE (code-server).
    Bietet Status der Dropzone-Dateien und JADX-Dekompilierungs-Trigger per Fetch.
    """
    html_content = r"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OuterHeaven REA Sandbox Dashboard</title>
    <style>
        :root {
            --bg: #1e1e1e;
            --surface: #252526;
            --surface-elevated: #2d2d30;
            --border: #3c3c3c;
            --primary: #0e639c;
            --primary-hover: #1177bb;
            --accent: #4ec9b0;
            --warning: #ce9178;
            --danger: #f48771;
            --success: #89d185;
            --text: #cccccc;
            --text-bright: #ffffff;
            --text-muted: #858585;
            --font-mono: Consolas, "Courier New", monospace;
            --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg);
            color: var(--text);
            font-family: var(--font-sans);
            font-size: 13px;
            line-height: 1.5;
            padding: 16px;
        }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 14px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 16px;
        }
        .header-title {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        h1 {
            font-size: 16px;
            font-weight: 600;
            color: var(--text-bright);
        }
        .badge {
            display: inline-block;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
            text-transform: uppercase;
        }
        .badge-running { background: rgba(137, 209, 133, 0.2); color: var(--success); border: 1px solid var(--success); }
        .badge-stopped { background: rgba(244, 135, 113, 0.2); color: var(--danger); border: 1px solid var(--danger); }
        .badge-blue { background: rgba(14, 99, 156, 0.3); color: #4fc1ff; border: 1px solid #4fc1ff; }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }
        @media (max-width: 800px) {
            .grid { grid-template-columns: 1fr; }
        }
        .card {
            background-color: var(--surface);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 14px;
            margin-bottom: 16px;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            font-weight: 600;
            font-size: 13px;
            color: var(--text-bright);
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }
        th, td {
            text-align: left;
            padding: 6px 8px;
            border-bottom: 1px solid var(--border);
        }
        th { color: var(--text-muted); font-weight: normal; }
        tr:hover { background-color: var(--surface-elevated); }
        .btn {
            background-color: var(--primary);
            color: #fff;
            border: none;
            padding: 5px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            font-family: inherit;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: background 0.15s ease;
        }
        .btn:hover { background-color: var(--primary-hover); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-secondary { background-color: #3a3d41; }
        .btn-secondary:hover { background-color: #45494e; }
        .btn-danger { background-color: #8b2b2b; }
        .btn-danger:hover { background-color: #a83232; }
        .form-group {
            margin-bottom: 10px;
        }
        label {
            display: block;
            margin-bottom: 4px;
            color: var(--text-muted);
            font-size: 11px;
            text-transform: uppercase;
            font-weight: 600;
        }
        input, select {
            width: 100%;
            background-color: var(--bg);
            border: 1px solid var(--border);
            color: var(--text-bright);
            padding: 6px 8px;
            border-radius: 4px;
            font-family: inherit;
            font-size: 12px;
        }
        input:focus, select:focus {
            outline: none;
            border-color: var(--accent);
        }
        .log-box {
            background-color: var(--bg);
            border: 1px solid var(--border);
            border-radius: 4px;
            padding: 10px;
            font-family: var(--font-mono);
            font-size: 11px;
            color: #d4d4d4;
            max-height: 240px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .status-bar {
            padding: 8px 12px;
            border-radius: 4px;
            margin-bottom: 12px;
            display: none;
            font-size: 12px;
        }
        .status-info { background: rgba(14, 99, 156, 0.2); border: 1px solid var(--primary); color: #4fc1ff; }
        .status-success { background: rgba(137, 209, 133, 0.2); border: 1px solid var(--success); color: var(--success); }
        .status-error { background: rgba(244, 135, 113, 0.2); border: 1px solid var(--danger); color: var(--danger); }
        .actions-row {
            display: flex;
            gap: 8px;
            margin-top: 12px;
            flex-wrap: wrap;
        }
    </style>
</head>
<body>
    <header>
        <div class="header-title">
            <span style="font-size: 18px;">🔬</span>
            <h1>OuterHeaven REA Sandbox Dashboard</h1>
            <span class="badge badge-blue">metal-dgear</span>
            <span id="container-badge" class="badge badge-running">Prüfe...</span>
        </div>
        <div>
            <button class="btn btn-secondary" onclick="loadAll()">🔄 Aktualisieren</button>
        </div>
    </header>

    <div id="status-bar" class="status-bar"></div>

    <div class="grid">
        <!-- Linke Spalte: Dropzone APKs & Steuerung -->
        <div>
            <div class="card">
                <div class="card-header">
                    <span>📦 Dropzone APKs (<span id="dropzone-count">0</span>)</span>
                    <span style="font-size:11px; color:var(--text-muted); font-family:var(--font-mono)">workspace/catlord-dropzone</span>
                </div>
                <div style="overflow-x:auto; max-height: 260px;">
                    <table>
                        <thead>
                            <tr>
                                <th>APK Datei</th>
                                <th>Größe</th>
                                <th>Datum</th>
                                <th>Aktion</th>
                            </tr>
                        </thead>
                        <tbody id="dropzone-table">
                            <tr><td colspan="4" style="color:var(--text-muted)">Lade Dropzone-Dateien...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <span>⚙️ JADX Dekompilierung auslösen</span>
                    <span class="badge badge-blue">JADX 1.5.0</span>
                </div>
                <form id="decompile-form" onsubmit="triggerDecompile(event)">
                    <div class="form-group">
                        <label for="apk-select">Ausgewählte APK:</label>
                        <select id="apk-select" required onchange="onApkSelectChange()">
                            <option value="">-- APK auswählen --</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="target-folder">Ziel-Unterordner (in rea-output/):</label>
                        <input type="text" id="target-folder" placeholder="z.B. catlord/InnocentWitches" />
                    </div>
                    <div class="form-group">
                        <label for="filter-kw">Gezielte Code-Suche / Keyword-Filter (optional):</label>
                        <input type="text" id="filter-kw" placeholder="z.B. save, crypto, auth, vibe, camera, token" />
                    </div>
                    <div class="actions-row">
                        <button type="submit" id="btn-decompile" class="btn">
                            <span>▶</span> Dekompilierung starten (JADX)
                        </button>
                        <button type="button" class="btn btn-secondary" onclick="triggerPipeline()">
                            <span>⚡</span> Full Pipeline
                        </button>
                        <button type="button" class="btn btn-danger" onclick="restartContainer()">
                            <span>🔄</span> Container Restart
                        </button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Rechte Spalte: Sandbox Status, Container Info & Jobs Log -->
        <div>
            <div class="card">
                <div class="card-header">
                    <span>🐳 Sandbox Container Status</span>
                    <span id="container-status-text" style="font-size: 11px; color: var(--accent);">--</span>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 12px; margin-bottom: 10px;">
                    <div><span style="color:var(--text-muted)">Container:</span> <strong id="ci-name">rea-sandbox</strong></div>
                    <div><span style="color:var(--text-muted)">Image:</span> <span id="ci-image">rea-sandbox:latest</span></div>
                    <div><span style="color:var(--text-muted)">JADX Version:</span> <span id="ci-jadx">1.5.0</span></div>
                    <div><span style="color:var(--text-muted)">Host PID:</span> <span id="ci-pid">--</span></div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <span>📜 Letzte Decompile-Jobs & Logs</span>
                    <button class="btn btn-secondary" style="padding:2px 8px; font-size:11px;" onclick="loadJobs()">Reload Logs</button>
                </div>
                <div id="job-info" style="font-size: 12px; margin-bottom: 8px; color: var(--text-bright);">Keine laufenden Jobs.</div>
                <div id="log-box" class="log-box">Warte auf Job-Informationen...</div>
            </div>
        </div>
    </div>

    <!-- Untere Sektion: Code-Analyse & LLM Findings mit metal-coder:latest -->
    <div class="card" style="margin-top: 16px;">
        <div class="card-header">
            <span>🔬 REA Code-Analyse & Findings</span>
            <span class="badge badge-blue">metal-coder:latest</span>
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
            <div>
                <form id="analyze-form" onsubmit="triggerAnalyze(event)">
                    <div class="form-group">
                        <label for="pkg-select">Dekompiliertes Paket (in rea-output/):</label>
                        <select id="pkg-select" required onchange="onPkgSelectChange()">
                            <option value="">-- Paket auswählen --</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label for="analyze-kw">Fokus-Keyword / Suchbegriff (optional):</label>
                        <input type="text" id="analyze-kw" placeholder="z.B. auth, crypto, token, vibe, billing, network" />
                    </div>
                    <div class="form-group">
                        <label for="analyze-prompt">Spezifische Analyse-Anweisung (optional):</label>
                        <input type="text" id="analyze-prompt" placeholder="z.B. Finde API-Endpunkte und Token-Handling" />
                    </div>
                    <div class="actions-row">
                        <button type="submit" id="btn-analyze" class="btn">
                            <span>🤖</span> Mit metal-coder:latest analysieren
                        </button>
                        <button type="button" class="btn btn-secondary" onclick="loadSelectedFindings()">
                            <span>📄</span> Letzte Findings laden
                        </button>
                    </div>
                </form>
            </div>
            <div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <span style="font-weight: 600; font-size: 12px; color: var(--text-bright);" id="findings-title">llm_findings.md</span>
                    <span id="findings-mtime" style="font-size: 11px; color: var(--text-muted);">--</span>
                </div>
                <div id="findings-box" class="log-box" style="max-height: 220px; white-space: pre-wrap; font-family: var(--font-mono); font-size: 11px;">Wähle ein Paket aus, um gespeicherte Findings anzuzeigen oder eine neue Analyse mit metal-coder:latest zu starten...</div>
            </div>
        </div>
    </div>

    <script>
        function showStatus(text, type='info') {
            const el = document.getElementById('status-bar');
            el.className = 'status-bar status-' + type;
            el.textContent = text;
            el.style.display = 'block';
            if (type !== 'info') {
                setTimeout(() => { el.style.display = 'none'; }, 6000);
            }
        }

        async function loadSandboxStatus() {
            try {
                const res = await fetch('/api/sandbox/status');
                const data = await res.json();
                const badge = document.getElementById('container-badge');
                const stText = document.getElementById('container-status-text');
                if (data.is_running) {
                    badge.className = 'badge badge-running';
                    badge.textContent = 'RUNNING';
                    stText.textContent = 'Online (PID: ' + (data.pid || '--') + ')';
                    document.getElementById('ci-pid').textContent = data.pid || '--';
                    document.getElementById('ci-image').textContent = data.image || 'rea-sandbox:latest';
                    document.getElementById('ci-jadx').textContent = data.jadx_version || '1.5.0';
                } else {
                    badge.className = 'badge badge-stopped';
                    badge.textContent = 'STOPPED';
                    stText.textContent = 'Gestoppt / Nicht aktiv';
                }
            } catch(e) {
                console.error('Status error:', e);
            }
        }

        async function loadDropzone() {
            try {
                const res = await fetch('/api/dropzone');
                const data = await res.json();
                document.getElementById('dropzone-count').textContent = data.files.length;
                const tbody = document.getElementById('dropzone-table');
                const select = document.getElementById('apk-select');
                const currentVal = select.value;

                if (!data.files || data.files.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="color:var(--text-muted)">Keine APKs in der Dropzone gefunden.</td></tr>';
                    select.innerHTML = '<option value="">-- Keine APKs verfügbar --</option>';
                    return;
                }

                tbody.innerHTML = '';
                select.innerHTML = '<option value="">-- APK auswählen --</option>';

                data.files.forEach(f => {
                    const row = document.createElement('tr');
                    const dateStr = f.modified_at ? f.modified_at.substring(0, 16).replace('T', ' ') : '--';
                    row.innerHTML = `
                        <td style="font-weight:600; color:var(--text-bright); word-break:break-all;">\${f.name}</td>
                        <td style="color:var(--accent);">\${f.size_formatted}</td>
                        <td style="color:var(--text-muted); font-size:11px;">\${dateStr}</td>
                        <td><button class="btn btn-secondary" style="padding:2px 8px; font-size:11px;" onclick="selectApk('\${f.name}')">Wählen</button></td>
                    `;
                    tbody.appendChild(row);

                    const opt = document.createElement('option');
                    opt.value = f.name;
                    opt.textContent = `\${f.name} (\${f.size_formatted})`;
                    if (f.name === currentVal) opt.selected = true;
                    select.appendChild(opt);
                });
            } catch(e) {
                console.error('Dropzone error:', e);
            }
        }

        function selectApk(name) {
            const select = document.getElementById('apk-select');
            select.value = name;
            onApkSelectChange();
        }

        function onApkSelectChange() {
            const apkName = document.getElementById('apk-select').value;
            if (!apkName) return;
            const target = document.getElementById('target-folder');
            const base = apkName.replace(/\\.apk$/i, '');
            target.value = 'catlord/' + base;
        }

        async function loadJobs() {
            try {
                const res = await fetch('/api/sandbox/jobs');
                const data = await res.json();
                const logBox = document.getElementById('log-box');
                const jobInfo = document.getElementById('job-info');
                if (!data.jobs || data.jobs.length === 0) {
                    jobInfo.textContent = 'Bisher keine Jobs in der Sandbox ausgeführt.';
                    logBox.textContent = 'Kein Log-Output vorhanden.';
                    return;
                }
                const latest = data.jobs[0];
                const stColor = latest.status === 'completed' ? 'var(--success)' : (latest.status === 'running' ? 'var(--accent)' : 'var(--danger)');
                const exitStr = latest.exit_code !== null ? latest.exit_code : 'läuft';
                jobInfo.innerHTML = `Job <strong>\${latest.id}</strong> (\${latest.apk_name}) &bull; Status: <span style="color:\${stColor}; font-weight:bold;">\${latest.status.toUpperCase()}</span> (Exit: \${exitStr})`;
                logBox.textContent = latest.log_tail || 'Kein Log-Output verfügbar.';
            } catch(e) {
                console.error('Jobs error:', e);
            }
        }

        async function triggerDecompile(event) {
            event.preventDefault();
            const apk = document.getElementById('apk-select').value;
            if (!apk) {
                showStatus('Bitte zuerst eine APK auswählen.', 'error');
                return;
            }
            const target = document.getElementById('target-folder').value.trim() || null;
            const kw = document.getElementById('filter-kw').value.trim() || null;
            const btn = document.getElementById('btn-decompile');
            btn.disabled = true;
            btn.textContent = '⏳ Starte JADX...';
            showStatus('Dekompilierungs-Job für ' + apk + ' wird gestartet...', 'info');

            try {
                const res = await fetch('/api/sandbox/decompile', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ apk_name: apk, target_subfolder: target, filter_keyword: kw })
                });
                const data = await res.json();
                if (res.ok) {
                    showStatus('Job gestartet: ' + (data.job_id || data.message), 'success');
                    loadJobs();
                } else {
                    showStatus('Fehler: ' + (data.detail || 'Konnte Job nicht starten'), 'error');
                }
            } catch(e) {
                showStatus('Netzwerkfehler: ' + e.message, 'error');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>▶</span> Dekompilierung starten (JADX)';
            }
        }

        async function restartContainer() {
            if (!confirm('Möchtest du den rea-sandbox Docker-Container wirklich neustarten?')) return;
            showStatus('Starte Container neu...', 'info');
            try {
                const res = await fetch('/api/sandbox/container/restart', { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    showStatus('Container erfolgreich neugestartet!', 'success');
                    loadSandboxStatus();
                } else {
                    showStatus('Fehler beim Container-Neustart: ' + (data.detail || data.error), 'error');
                }
            } catch(e) {
                showStatus('Fehler: ' + e.message, 'error');
            }
        }

        async function triggerPipeline() {
            showStatus('Triggere Dropzone-Pipeline (/api/pipeline/trigger)...', 'info');
            try {
                const res = await fetch('/api/pipeline/trigger', { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    showStatus('Pipeline angestoßen: ' + (data.message || 'Läuft'), 'success');
                    loadJobs();
                } else {
                    showStatus('Pipeline Fehler: ' + data.detail, 'error');
                }
            } catch(e) {
                showStatus('Fehler: ' + e.message, 'error');
            }
        }

        async function loadOutputs() {
            try {
                const res = await fetch('/api/sandbox/outputs');
                const data = await res.json();
                const select = document.getElementById('pkg-select');
                const currentVal = select.value;
                if (!data.folders || data.folders.length === 0) {
                    select.innerHTML = '<option value="">-- Keine dekompilierten Pakete gefunden --</option>';
                    return;
                }
                select.innerHTML = '<option value="">-- Paket auswählen --</option>';
                data.folders.forEach(f => {
                    const opt = document.createElement('option');
                    opt.value = f.relative_path || f.name;
                    opt.textContent = `${f.name} (${f.file_count} Dateien${f.has_findings ? ' • Findings vorhanden' : ''})`;
                    if (opt.value === currentVal || f.name === currentVal) opt.selected = true;
                    select.appendChild(opt);
                });
            } catch(e) {
                console.error('Outputs error:', e);
            }
        }

        function onPkgSelectChange() {
            loadSelectedFindings();
        }

        async function loadSelectedFindings() {
            const select = document.getElementById('pkg-select');
            const pkg = select.value;
            if (!pkg) return;
            const cleanPkg = pkg.split('/').pop();
            const titleEl = document.getElementById('findings-title');
            const box = document.getElementById('findings-box');
            const mtimeEl = document.getElementById('findings-mtime');
            titleEl.textContent = `llm_findings.md (${cleanPkg})`;
            box.textContent = 'Lade Findings...';
            try {
                const res = await fetch('/api/findings/latest?package=' + encodeURIComponent(cleanPkg));
                if (res.ok) {
                    const data = await res.json();
                    box.textContent = data.content || 'Kein Inhalt vorhanden.';
                    mtimeEl.textContent = data.modified_at ? data.modified_at.substring(0, 19).replace('T', ' ') : '';
                } else {
                    box.textContent = `Noch keine llm_findings.md für '${cleanPkg}' vorhanden. Nutze den Button oben, um eine Analyse mit metal-coder:latest zu starten.`;
                    mtimeEl.textContent = '--';
                }
            } catch(e) {
                box.textContent = 'Fehler beim Laden: ' + e.message;
            }
        }

        async function triggerAnalyze(event) {
            event.preventDefault();
            const pkg = document.getElementById('pkg-select').value;
            if (!pkg) {
                showStatus('Bitte zuerst ein dekompiliertes Paket auswählen.', 'error');
                return;
            }
            const kw = document.getElementById('analyze-kw').value.trim() || null;
            const prompt = document.getElementById('analyze-prompt').value.trim() || null;
            const btn = document.getElementById('btn-analyze');
            const box = document.getElementById('findings-box');
            btn.disabled = true;
            btn.textContent = '⏳ Analysiere mit metal-coder...';
            showStatus('Analyse für ' + pkg + ' mit metal-coder:latest gestartet...', 'info');
            box.textContent = '⏳ metal-coder:latest analysiert Code auf metal-dgear (kann ca. 10–30 Sekunden dauern)...';

            try {
                const res = await fetch('/api/sandbox/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ package: pkg, keyword: kw, custom_prompt: prompt })
                });
                const data = await res.json();
                if (res.ok) {
                    showStatus('Analyse erfolgreich abgeschlossen!', 'success');
                    box.textContent = data.analysis || 'Analyse fertig, aber keine Ausgabe.';
                    document.getElementById('findings-mtime').textContent = 'Gerade eben';
                } else {
                    showStatus('Fehler: ' + (data.detail || 'Analyse fehlgeschlagen'), 'error');
                    box.textContent = 'Fehler bei der Analyse: ' + (data.detail || 'Unbekannt');
                }
            } catch(e) {
                showStatus('Netzwerkfehler: ' + e.message, 'error');
                box.textContent = 'Netzwerkfehler: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>🤖</span> Mit metal-coder:latest analysieren';
            }
        }

        function loadAll() {
            loadSandboxStatus();
            loadDropzone();
            loadJobs();
            loadOutputs();
        }

        // Init & Auto-Refresh
        loadAll();
        setInterval(loadAll, 10000);
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)


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


@app.get("/api/models")
def get_models():
    """
    Liefert die Liste der auf metal-dgear verfügbaren spezialisierten Ollama-Agenten
    inklusive Beschreibung, Rolle und aktuellem Laufzeitstatus.
    """
    ollama_info = get_ollama_status()
    running_models = ollama_info.get("active_models", [])
    installed_models = ollama_info.get("available_models", [])

    models_list = []
    for model_id, meta in OLLAMA_AGENTS.items():
        is_installed = any(model_id in m for m in installed_models)
        is_running = any(model_id in m for m in running_models)
        models_list.append({
            "id": model_id,
            "name": meta["name"],
            "title": meta["title"],
            "role": meta["role"],
            "description": meta["description"],
            "base_model": meta["base_model"],
            "badge": meta["badge"],
            "icon": meta["icon"],
            "is_installed": is_installed,
            "is_active": is_running,
            "default_for": meta["default_for"]
        })

    return {
        "node": "metal-dgear",
        "count": len(models_list),
        "models": models_list,
        "running_models": running_models,
        "installed_models": installed_models
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
    job = job_manager.create_job(apk_name=req.apk_name, target_dir=target_sub, filter_keyword=req.filter_keyword)

    # Asynchron im Hintergrund ausführen
    def run_decompile_job(job_id: str, apk_filename: str, out_folder: str, filter_kw: Optional[str]):
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
            
            # Gezielte Keyword-Suche falls angegeben
            if res.returncode == 0 and filter_kw and filter_kw.strip():
                kw = filter_kw.strip()
                sources_dir = SANDBOX_OUTPUT_DIR / out_folder / "sources"
                matched_files = []
                code_snippets = ""
                
                if sources_dir.exists():
                    try:
                        grep_cmd = [
                            "grep", "-rnwil",
                            "--exclude-dir=androidx", "--exclude-dir=android",
                            "--exclude-dir=kotlin", "--exclude-dir=kotlinx",
                            "--exclude-dir=com/google", "--exclude-dir=org/intellij",
                            kw, str(sources_dir)
                        ]
                        grep_res = subprocess.run(grep_cmd, capture_output=True, text=True, timeout=30)
                        if grep_res.returncode == 0 and grep_res.stdout.strip():
                            matched_files = [f for f in grep_res.stdout.strip().split("\n") if f]
                    except Exception as ge:
                        tail += f"\n[Grep Fehler: {str(ge)}]"

                    if matched_files:
                        tail += f"\n[Keyword '{kw}': {len(matched_files)} Klassen gefunden]"
                        for cfile in matched_files[:3]:
                            try:
                                rel_path = Path(cfile).relative_to(SANDBOX_OUTPUT_DIR / out_folder)
                                with open(cfile, "r", encoding="utf-8", errors="replace") as f:
                                    lines = [line for line in f if "@kotlin.Metadata" not in line][:40]
                                code_snippets += f"\n\n### Datei: {rel_path}\n```java\n{''.join(lines)}\n```"
                            except Exception:
                                pass

                        prompt_text = f"""Du bist ein Senior Android Reverse-Engineering-Experte.
Der Benutzer sucht gezielt nach dem Keyword / Aspekt '{kw}' in der APK '{pkg_name}'.
Hier sind gefundene Klassenfragmente:

{code_snippets}

Fasse in 3 prägnanten Punkten auf Deutsch zusammen:
1. Relevanz des Begriffs '{kw}' im dekompilierten Code
2. Identifizierte Funktionen / Datenstrukturen / Netzwerk- & Krypto-Bezüge
3. Handlungsempfehlung / Modding-Potenzial für diesen Aspekt"""

                        try:
                            payload = {
                                "model": "metal-coder:latest",
                                "prompt": prompt_text,
                                "stream": False,
                                "options": {"num_predict": 400, "temperature": 0.2}
                            }
                            ores = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=60)
                            if ores.status_code != 200:
                                payload["model"] = DEFAULT_OLLAMA_MODEL
                                ores = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=60)

                            if ores.status_code == 200:
                                findings_text = ores.json().get("response", "")
                                if findings_text:
                                    findings_path = SANDBOX_OUTPUT_DIR / out_folder / "llm_findings.md"
                                    findings_content = f"""# LLM Analyse: {pkg_name} (Filter: {kw})

- **Modell:** `metal-coder:latest`
- **Suchbegriff:** `{kw}`
- **Gefundene Treffer:** {len(matched_files)} Klassen
- **Zeitpunkt:** {datetime.now(timezone.utc).isoformat()}

---

{findings_text}
"""
                                    findings_path.write_text(findings_content, encoding="utf-8")
                                    tail += f"\n[metal-coder Analyse für '{kw}' in llm_findings.md gespeichert]"
                        except Exception as oe:
                            tail += f"\n[Ollama Fehler: {str(oe)}]"
                    else:
                        tail += f"\n[Keyword '{kw}': Keine Klassen gefunden]"

            if res.returncode == 0:
                job_manager.update_job(job_id, status="completed", exit_code=0, log_tail=tail)
            else:
                job_manager.update_job(job_id, status="failed", exit_code=res.returncode, log_tail=tail)
        except Exception as e:
            job_manager.update_job(job_id, status="failed", exit_code=-1, log_tail=str(e))

    thread = threading.Thread(
        target=run_decompile_job,
        args=(job["id"], req.apk_name, target_sub, req.filter_keyword),
        daemon=True
    )
    thread.start()

    return {
        "status": "started",
        "message": f"Dekompilierung für '{req.apk_name}' gestartet (Filter: '{req.filter_keyword or 'Standard'}').",
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


@app.post("/api/sandbox/analyze")
def analyze_decompiled_package(req: SandboxAnalyzeRequest):
    """
    Analysiert ein dekompiliertes Paket in rea-output mit metal-coder:latest.
    """
    pkg_clean = req.package.strip().strip("/")
    if not pkg_clean:
        raise HTTPException(status_code=400, detail="Paketname darf nicht leer sein.")

    pkg_dir = SANDBOX_OUTPUT_DIR / pkg_clean
    if not pkg_dir.exists():
        pkg_dir = OUTPUT_BASE_DIR / pkg_clean
        if not pkg_dir.exists():
            raise HTTPException(status_code=404, detail=f"Paket-Ordner '{pkg_clean}' nicht in {SANDBOX_OUTPUT_DIR} gefunden.")

    kw = (req.keyword or "").strip()
    sources_dir = pkg_dir / "sources"
    matched_snippets = []

    if kw and sources_dir.exists():
        grep_cmd = [
            "grep", "-rnwil",
            "--exclude-dir=androidx", "--exclude-dir=android",
            "--exclude-dir=kotlin", "--exclude-dir=kotlinx",
            "--exclude-dir=com/google", "--exclude-dir=org/intellij",
            kw, str(sources_dir)
        ]
        try:
            grep_res = subprocess.run(grep_cmd, capture_output=True, text=True, timeout=20)
            if grep_res.returncode == 0 and grep_res.stdout.strip():
                files = [f for f in grep_res.stdout.strip().split("\n") if f][:3]
                for f in files:
                    try:
                        rel = Path(f).relative_to(pkg_dir)
                        with open(f, "r", encoding="utf-8", errors="replace") as fp:
                            lines = [line for line in fp if "@kotlin.Metadata" not in line][:40]
                        matched_snippets.append(f"### Datei: {rel}\n```java\n{''.join(lines)}\n```")
                    except Exception:
                        pass
        except Exception as e:
            print(f"Grep error: {e}")

    # Fallback to existing findings or top files
    if not matched_snippets and sources_dir.exists():
        for p in sources_dir.rglob("*.java"):
            if "android" not in str(p) and "androidx" not in str(p):
                try:
                    rel = p.relative_to(pkg_dir)
                    with open(p, "r", encoding="utf-8", errors="replace") as fp:
                        lines = [line for line in fp][:35]
                    matched_snippets.append(f"### Datei: {rel}\n```java\n{''.join(lines)}\n```")
                    if len(matched_snippets) >= 2:
                        break
                except Exception:
                    pass

    context_code = "\n\n".join(matched_snippets) if matched_snippets else "Keine spezifischen Quellcode-Dateien extrahiert."
    prompt_user = req.custom_prompt.strip() if req.custom_prompt else f"Analysiere den dekompilierten Code des Pakets '{pkg_clean}' (Fokus: {kw or 'Architektur, Netzwerk & Spiellogik'})."

    prompt_text = f"""Du bist Senior Reverse-Engineering-Analyst und nutzt metal-coder.
Paket: {pkg_clean}
Fokus: {kw or 'Gesamtüberblick'}

Gefundene Code-Fragmente:
{context_code}

Aufgabe: {prompt_user}

Liefere eine präzise technische Analyse auf Deutsch:
1. Relevante Klassen, Schnittstellen und Logik
2. Sicherheitsrelevante Aspekte (Tokens, API-Endpunkte, Krypto, Storage)
3. Modding- & Hooking-Ansätze (Frida / Xposed / Smali Patches)"""

    try:
        payload = {
            "model": "metal-coder:latest",
            "prompt": prompt_text,
            "stream": False,
            "options": {"num_ctx": 8192, "num_predict": 1024, "temperature": 0.2}
        }
        res = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=120)
        if res.status_code != 200:
            payload["model"] = DEFAULT_OLLAMA_MODEL
            res = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=120)

        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama meldet: {res.text}")

        analysis_text = res.json().get("response", "").strip()
        findings_file = pkg_dir / "llm_findings.md"
        findings_md = f"""# LLM Analyse: {pkg_clean}

- **Modell:** `metal-coder:latest`
- **Fokus / Keyword:** `{kw or 'Allgemein'}`
- **Zeitpunkt:** {datetime.now(timezone.utc).isoformat()}

---

{analysis_text}
"""
        findings_file.write_text(findings_md, encoding="utf-8")
        return {
            "status": "success",
            "package": pkg_clean,
            "keyword": kw,
            "analysis": analysis_text,
            "findings_file": str(findings_file),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 🐾 Catlord Studio Endpunkte
# ==========================================

@app.post("/api/catlord/ask-deepseek")
def ask_deepseek(req: CatlordAskDeepSeekRequest):
    """
    Sendet einen Story-, Konzept- oder Code-Prompt an einen der spezialisierten
    Ollama-Agenten auf metal-dgear (z.B. catlord-hermes:latest, catlord-architect:latest,
    metal-coder:latest, metal-fast:latest), optional angereichert mit Catlord-Lore.
    """
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt darf nicht leer sein.")

    prompt_clean = req.prompt.strip()
    pkg_name = req.package.strip() if req.package else None

    # Modell ermitteln mit Validierung und Fallback
    target_model = (req.model or "catlord-hermes:latest").strip()
    valid_models = list(OLLAMA_AGENTS.keys()) + [DEFAULT_OLLAMA_MODEL]
    if target_model not in valid_models:
        ollama_status = get_ollama_status()
        if target_model not in ollama_status.get("available_models", []):
            target_model = "catlord-hermes:latest"

    # Kontext sammeln
    context_blocks = []

    # 1. Catlord Lore laden falls angefordert
    if req.include_lore:
        lore_files = [
            ("charaktere.md", "Charaktere & Rollen"),
            ("gameplay.md", "Gameplay & Mechaniken")
        ]
        for fname, desc in lore_files:
            fpath = CATLORD_REPO_DIR / "docs" / fname
            if fpath.exists():
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    if text.strip():
                        context_blocks.append(f"### Catlord Lore ({desc} - {fname}):\n{text[:3500]}")
                except Exception:
                    pass

    # 2. Optionaler Paket-Kontext (nur wenn explizit ein fremdes Paket übergeben wurde)
    if pkg_name and pkg_name.lower() not in ("catlord", "catlord-app", "general", "allgemein"):
        pkg_dir = OUTPUT_BASE_DIR / pkg_name
        if pkg_dir.exists():
            for fname in ["llm_analysis_context.md", "llm_findings.md"]:
                fpath = pkg_dir / fname
                if fpath.exists():
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="replace")
                        if text.strip():
                            context_blocks.append(f"### Kontext ({fname}) für '{pkg_name}':\n{text[:3000]}")
                            break
                    except Exception:
                        pass

    # Prompt zusammenstellen
    if context_blocks:
        context_str = "\n\n".join(context_blocks)
        full_prompt = f"Hier ist relevanter Kontext für die Aufgabe:\n{context_str}\n\nAufgabe / Frage:\n{prompt_clean}"
    else:
        full_prompt = prompt_clean

    # Modell-spezifische Parameter
    num_ctx = 8192 if ("coder" in target_model or "architect" in target_model) else 4096
    temperature = 0.75 if "hermes" in target_model else (0.2 if "coder" in target_model else 0.4)

    payload = {
        "model": target_model,
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": 1024,
            "temperature": temperature
        }
    }

    try:
        ollama_res = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=300
        )
        if ollama_res.status_code != 200:
            # Fallback auf Default-Modell falls gewähltes Modell nicht antwortet
            payload["model"] = DEFAULT_OLLAMA_MODEL
            ollama_res = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=300)

        if ollama_res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Ollama meldet Status {ollama_res.status_code}: {ollama_res.text}")

        data = ollama_res.json()
        response_text = data.get("response", "").strip()
        if not response_text:
            response_text = f"Keine Antwort vom Agenten '{target_model}' generiert."
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Timeout (300s) bei der Antwortgenerierung durch '{target_model}' auf metal-dgear.")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Fehler bei Verbindung zu Ollama: {str(e)}")

    now = datetime.now()
    ts_slug = now.strftime("%Y%m%d_%H%M%S")
    model_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '_', target_model.split(':')[0]).strip('_')
    first_line = prompt_clean.splitlines()[0]
    title_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '_', first_line[:25].strip()).strip('_') or "draft"

    saved_draft_name = None
    saved_draft_path = None
    saved_finding_name = None
    saved_finding_path = None

    # 1. Speichern in ai-drafts falls gewünscht
    if req.save_to_drafts:
        saved_draft_name = f"{ts_slug}_{model_slug}_{title_slug}.md"
        draft_content = f"""# AI Entwurf: {first_line[:50]}

- **Modell:** `{target_model}`
- **Datum:** {now.strftime("%Y-%m-%d %H:%M:%S")}
- **Lore-Kontext einbezogen:** {"Ja" if req.include_lore else "Nein"}
- **Prompt:** {prompt_clean}

---

{response_text}
"""
        try:
            drafts_dir = CATLORD_REPO_DIR / "docs" / "ai-drafts"
            drafts_dir.mkdir(parents=True, exist_ok=True)
            draft_file = drafts_dir / saved_draft_name
            draft_file.write_text(draft_content, encoding="utf-8")
            saved_draft_path = str(draft_file)
        except Exception as de:
            print(f"[Catlord] Fehler beim Speichern in ai-drafts: {de}")

    # 2. Speichern in rea-findings falls gewünscht
    if req.save_to_findings and not req.save_to_drafts:
        slug_pkg = re.sub(r'[^a-zA-Z0-9_\-]+', '_', (pkg_name or "catlord").lower()).strip('_')
        saved_finding_name = f"{ts_slug}_{slug_pkg}_{model_slug}.md"
        findings_md = f"""# Agent Antwort: {pkg_name or 'Catlord'}

- **Modell:** `{target_model}`
- **Paket:** `{pkg_name or 'Catlord'}`
- **Prompt:** {prompt_clean}
- **Zeitpunkt:** {now.strftime("%Y-%m-%d %H:%M:%S")}

---

{response_text}
"""
        try:
            rf_dir = CATLORD_REPO_DIR / "rea-findings"
            rf_dir.mkdir(parents=True, exist_ok=True)
            saved_file = rf_dir / saved_finding_name
            saved_file.write_text(findings_md, encoding="utf-8")
            saved_finding_path = str(saved_file)
        except Exception as se:
            print(f"[Catlord] Fehler beim Speichern der Findings: {se}")

    return {
        "status": "success",
        "model": target_model,
        "package": pkg_name,
        "prompt": prompt_clean,
        "response": response_text,
        "include_lore": req.include_lore,
        "saved_to_findings": (saved_finding_name is not None),
        "saved_to_drafts": (saved_draft_name is not None),
        "file_name": saved_draft_name or saved_finding_name,
        "file_path": saved_draft_path or saved_finding_path,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/api/catlord/ask-agent")
def ask_agent(req: CatlordAskDeepSeekRequest):
    """Alias für /api/catlord/ask-deepseek mit flexibler Agenten-Auswahl."""
    return ask_deepseek(req)


@app.post("/api/catlord/voice-note")
def save_catlord_voice_note(req: CatlordVoiceNoteRequest):
    """
    Speichert eine Catlord-Voice-Note als Markdown-Datei im Projekt unter
    projects/catlord/voice-notes/ und synchronisiert sie nach
    /mnt/storage-beta/projects/catlord-media/gdrive-sync/Note /
    """
    if not req.content or not req.content.strip():
        raise HTTPException(status_code=400, detail="Inhalt der Voice-Note darf nicht leer sein.")

    now = datetime.now()
    timestamp_slug = now.strftime("%Y%m%d_%H%M%S")
    human_date = now.strftime("%Y-%m-%d %H:%M:%S")

    raw_category = (req.category or "idea").strip()
    slug_category = re.sub(r'[^a-zA-Z0-9_\-]+', '_', raw_category.lower()).strip('_') or "idea"
    raw_title = (req.title or "").strip()

    if not raw_title:
        first_line = req.content.strip().splitlines()[0]
        words = first_line.split()[:5]
        raw_title = " ".join(words) if words else f"Notiz {slug_category.capitalize()}"
        if len(raw_title) > 40:
            raw_title = raw_title[:40].rstrip()

    slug_title = re.sub(r'[^a-zA-Z0-9_\-]+', '_', raw_title.strip()).strip('_')
    if not slug_title:
        slug_title = "note"
    filename = f"{timestamp_slug}_{slug_category}_{slug_title}.md"

    md_content = f"""# {raw_title}

- **Datum:** {human_date}
- **Kategorie:** {raw_category}
- **Quelle:** Catlord Studio App

## Notiz
{req.content.strip()}
"""

    # 1. Lokal im Git-Repo speichern
    CATLORD_VOICE_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    local_path = CATLORD_VOICE_NOTES_DIR / filename
    try:
        local_path.write_text(md_content, encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim lokalen Speichern: {str(e)}")

    # 2. Auf NFS/Storage kopieren (/mnt/storage-beta/projects/catlord-media/gdrive-sync/Note /)
    nfs_copied = False
    storage_path_str = None
    try:
        if CATLORD_STORAGE_NOTE_DIR.exists():
            storage_path = CATLORD_STORAGE_NOTE_DIR / filename
            storage_path.write_text(md_content, encoding="utf-8")
            nfs_copied = True
            storage_path_str = str(storage_path)
        else:
            # Fallback via SSH auf outerheaven
            res = subprocess.run([
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "outerheaven",
                f"cat > '/mnt/storage-beta/projects/catlord-media/gdrive-sync/Note /{filename}'"
            ], input=md_content.encode("utf-8"), capture_output=True, timeout=10)
            if res.returncode == 0:
                nfs_copied = True
                storage_path_str = f"outerheaven:/mnt/storage-beta/projects/catlord-media/gdrive-sync/Note /{filename}"
    except Exception as e:
        print(f"[Catlord] Warnung: Fehler beim NFS-Sync der Voice-Note: {e}")

    return {
        "status": "success",
        "filename": filename,
        "title": raw_title,
        "category": raw_category,
        "timestamp": human_date,
        "local_path": str(local_path),
        "nfs_copied": nfs_copied,
        "storage_path": storage_path_str
    }


@app.get("/api/catlord/docs")
def list_catlord_docs(folder: Optional[str] = Query(None, description="Optionaler Ordnerfilter: docs, prompts, voice-notes, rea-findings")):
    """
    Listet alle Markdown- und Textdateien aus projects/catlord/docs/,
    prompts/, voice-notes/ und rea-findings/ auf.
    """
    valid_folders = ["docs", "prompts", "voice-notes", "rea-findings"]
    target_folders = [folder] if folder and folder in valid_folders else valid_folders

    docs = []
    for f in target_folders:
        dir_path = CATLORD_REPO_DIR / f
        if not dir_path.exists():
            continue

        for root, _, files in os.walk(dir_path):
            for fname in files:
                if fname.endswith((".md", ".txt")):
                    fpath = Path(root) / fname
                    rel_to_folder = fpath.relative_to(dir_path)
                    try:
                        st = fpath.stat()
                        title = fname
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                                for line in fp:
                                    sline = line.strip()
                                    if sline.startswith("# "):
                                        title = sline[2:].strip()
                                        break
                                    elif sline.startswith("## ") and title == fname:
                                        title = sline[3:].strip()
                        except Exception:
                            pass

                        docs.append({
                            "folder": f,
                            "filename": fname,
                            "relative_path": f"{f}/{rel_to_folder}",
                            "title": title,
                            "size_bytes": st.st_size,
                            "size_formatted": format_bytes(st.st_size),
                            "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                            "mtime": st.st_mtime
                        })
                    except Exception:
                        pass

    # Sortiere: Neueste zuerst
    docs.sort(key=lambda x: x["mtime"], reverse=True)
    return {
        "count": len(docs),
        "docs": docs
    }


@app.get("/api/catlord/docs/{folder}/{filename}")
def get_catlord_doc_content(
    folder: str,
    filename: str,
    format: Optional[str] = Query(None, description="Format: 'raw' oder 'json'"),
    accept: Optional[str] = Header(None)
):
    """
    Liefert den Inhalt einer ausgewählten Markdown- oder Textdatei aus
    docs, prompts, voice-notes oder rea-findings mit Path-Traversal-Schutz.
    """
    valid_folders = ["docs", "prompts", "voice-notes", "rea-findings"]
    if folder not in valid_folders:
        raise HTTPException(status_code=400, detail=f"Ungültiger Ordner: '{folder}'. Erlaubt sind: {valid_folders}")

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Ungültiger Dateiname.")

    if not filename.endswith((".md", ".txt")):
        raise HTTPException(status_code=400, detail="Nur .md- und .txt-Dateien sind abrufbar.")

    fpath = CATLORD_REPO_DIR / folder / filename
    if not fpath.exists() or not fpath.is_file():
        raise HTTPException(status_code=404, detail=f"Datei '{folder}/{filename}' nicht gefunden.")

    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
        st = fpath.stat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Lesen der Datei: {str(e)}")

    if format == "raw" or (accept and "text/plain" in accept and format != "json"):
        return Response(content=content, media_type="text/plain; charset=utf-8")

    return {
        "folder": folder,
        "filename": filename,
        "relative_path": f"{folder}/{filename}",
        "content": content,
        "size_bytes": st.st_size,
        "size_formatted": format_bytes(st.st_size),
        "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    }


@app.get("/api/catlord/media-stats")
def get_catlord_media_stats():
    """
    Liefert eine Übersicht über die vorhandenen Datasets und Videos von
    Elliot & Käch unter /mnt/storage-beta/projects/catlord-media/
    """
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".webm"}

    base_dir = CATLORD_MEDIA_DIR

    def scan_dir(path: Path):
        img_count = 0
        vid_count = 0
        total_size = 0
        latest_mtime = 0.0

        if not path.exists():
            return {
                "exists": False,
                "images": 0,
                "videos": 0,
                "total_files": 0,
                "size_bytes": 0,
                "size_formatted": "0 B",
                "subfolders": []
            }

        subfolders = []
        try:
            for root, dirs, files in os.walk(path, followlinks=True):
                rpath = Path(root)
                rel = rpath.relative_to(path)
                folder_imgs = 0
                folder_vids = 0
                folder_size = 0

                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    fpath = rpath / fname
                    try:
                        st = fpath.stat()
                        fsize = st.st_size
                        total_size += fsize
                        folder_size += fsize
                        if st.st_mtime > latest_mtime:
                            latest_mtime = st.st_mtime
                    except Exception:
                        continue

                    if ext in image_exts:
                        img_count += 1
                        folder_imgs += 1
                    elif ext in video_exts:
                        vid_count += 1
                        folder_vids += 1

                if str(rel) != "." and (folder_imgs > 0 or folder_vids > 0):
                    subfolders.append({
                        "name": rpath.name,
                        "relative_path": str(rel),
                        "images": folder_imgs,
                        "videos": folder_vids,
                        "size_bytes": folder_size,
                        "size_formatted": format_bytes(folder_size)
                    })
        except Exception as e:
            print(f"[Catlord] Fehler beim Scannen von {path}: {e}")

        return {
            "exists": True,
            "images": img_count,
            "videos": vid_count,
            "total_files": img_count + vid_count,
            "size_bytes": total_size,
            "size_formatted": format_bytes(total_size),
            "latest_modified": datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat() if latest_mtime > 0 else None,
            "subfolders": subfolders
        }

    elliot_stats = scan_dir(base_dir / "elliot")
    kaech_stats = scan_dir(base_dir / "kaech")
    raw_footage_stats = scan_dir(base_dir / "raw-footage")
    references_stats = scan_dir(base_dir / "references")
    gdrive_datasets_stats = scan_dir(base_dir / "gdrive-sync" / "dataset")

    total_images = elliot_stats["images"] + kaech_stats["images"] + raw_footage_stats["images"]
    total_videos = elliot_stats["videos"] + kaech_stats["videos"] + raw_footage_stats["videos"]

    return {
        "media_dir": str(base_dir),
        "accessible": base_dir.exists(),
        "totals": {
            "elliot_images": elliot_stats["images"],
            "elliot_videos": elliot_stats["videos"],
            "kaech_images": kaech_stats["images"],
            "kaech_videos": kaech_stats["videos"],
            "total_images": total_images,
            "total_videos": total_videos,
        },
        "details": {
            "elliot": elliot_stats,
            "kaech": kaech_stats,
            "raw_footage": raw_footage_stats,
            "references": references_stats,
            "dataset_all": gdrive_datasets_stats
        }
    }


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8444, reload=False)
