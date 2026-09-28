#!/usr/bin/env python3
"""
Outer Heaven Mission Control Dashboard - Backend Server
Binds strictly to 127.0.0.1:8080 and Tailscale <OUTERHEAVEN_IP>:8080.
Provides:
  - Multi-node telemetry (Outer Heaven & Fox)
  - Dual-Agent WebSocket streaming chat with server-side <think> tag filter
  - ComfyUI render bridge & output gallery
  - Service controls for both cluster nodes
  - Secure authentication (Session-Cookie, Basic-Auth, X-Cluster-Key)
"""

import os
import sys
import time
import json
import secrets
import hashlib
import hmac
import base64
import asyncio
import re
import logging
import csv
import datetime
import ipaddress
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Set

import aiohttp
from aiohttp import web
import jinja2
import urllib.parse
import shutil
import contextlib
import copy

try:
    import filelock
except ImportError:
    filelock = None

try:
    from megan_context import megan_context_engine
except ImportError:
    megan_context_engine = None

try:
    from tandem_agent import tandem_agent
except ImportError:
    tandem_agent = None

import comfy_schema
from comfy_schema import (
    ComfySchemaManager,
    PipelineWorkflow,
    validate_pipeline,
    convert_pipeline_to_comfy_prompt,
)

# --- Configuration & Paths ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
COMFYUI_OUTPUT_DIR = Path("/home/outerheaven/ComfyUI/output")
if not COMFYUI_OUTPUT_DIR.is_dir():
    for _alt in [
        Path("/home/outerheaven/comfyui/output"),
        Path("/mnt/storage-beta/ComfyUI/output"),
        Path("/mnt/storage-beta/output"),
    ]:
        if _alt.is_dir():
            COMFYUI_OUTPUT_DIR = _alt
            break
    else:
        _local_out = BASE_DIR / "outputs"
        _local_out.mkdir(parents=True, exist_ok=True)
        COMFYUI_OUTPUT_DIR = _local_out

DIR_3D_OUTPUT = Path("/mnt/storage-beta/projekte/projekt-sexchat/output/3d")
if not DIR_3D_OUTPUT.is_dir():
    for _alt_3d in [
        COMFYUI_OUTPUT_DIR / "3d",
        Path("/home/outerheaven/ComfyUI/output/3d"),
        BASE_DIR / "output" / "3d"
    ]:
        if _alt_3d.is_dir():
            DIR_3D_OUTPUT = _alt_3d
            break
    else:
        DIR_3D_OUTPUT.mkdir(parents=True, exist_ok=True)

SECRETS_DIR = Path("/mnt/storage-alpha/secrets")
CLUSTER_TASKS_FILE = Path("/mnt/storage-beta/cluster_tasks.json")

# --- Hermes Büro & Career Pipeline Storage Paths ---
DIR_RECHNUNGSEINGANG = Path("/mnt/storage-beta/rechnungseingang")
DIR_RECHNUNGSARCHIV = Path("/mnt/storage-beta/rechnungsarchiv")
DIR_JOBANGEBOTE = Path("/mnt/storage-beta/jobangebote")
DIR_BEWERBUNGSAUSGANG = Path("/mnt/storage-beta/bewerbungsausgang")
DIR_BEWERBUNGSTRESOR = Path("/mnt/storage-beta/bewerbungstresor")
CSV_RECHNUNGEN_INDEX = DIR_RECHNUNGSARCHIV / "rechnungen_index.csv"
CSV_RECHNUNGEN_LOCK = DIR_RECHNUNGSARCHIV / "rechnungen_index.csv.lock"

@contextlib.contextmanager
def acquire_invoice_lock(timeout: float = 10.0):
    """Provides file locking for rechnungen_index.csv using filelock if available, or fallback."""
    if filelock:
        lock = filelock.FileLock(str(CSV_RECHNUNGEN_LOCK), timeout=timeout)
        with lock:
            yield
    else:
        yield

# Validate and ensure required office directories exist
for _buero_dir in [DIR_RECHNUNGSEINGANG, DIR_RECHNUNGSARCHIV, DIR_JOBANGEBOTE, DIR_BEWERBUNGSAUSGANG, DIR_BEWERBUNGSTRESOR]:
    try:
        _buero_dir.mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        logger.warning(f"Could not initialize directory {_buero_dir}: {_e}")

DIR_WORKFLOWS = Path("/mnt/storage-beta/workflows")
DIR_CUSTOM_WORKFLOWS = DIR_WORKFLOWS / "custom"
PROMPT_PRESETS_FILE = DIR_WORKFLOWS / "prompt_presets.json"
BOT_CONFIG_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/bot_config.json")
try:
    DIR_WORKFLOWS.mkdir(parents=True, exist_ok=True)
    DIR_CUSTOM_WORKFLOWS.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# --- Gallery Move Whitelist Destinations ---
GALLERY_MOVE_PROJECTS = {
    "projekt-sexchat": Path("/mnt/storage-beta/projekte/projekt-sexchat/output"),
    "projekt-catlord": Path("/mnt/storage-beta/projekte/projekt-catlord/output"),
    "projekt-dr-franks-bowling": Path("/mnt/storage-beta/projekte/projekt-dr-franks-bowling/output"),
    "projekt-comfy": Path("/mnt/storage-beta/projekte/projekt-comfy/output"),
    "comfyui-evolution": Path("/mnt/storage-beta/projekte/comfyui-evolution/archive")
}
# Ensure destination directories exist or can be created on demand
for _dest in GALLERY_MOVE_PROJECTS.values():
    try:
        _dest.mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        logger.debug(f"Gallery move project dir {_dest} not initialized at startup: {_e}")

COMFY_CACHE_FILE = BASE_DIR / "data" / "object_info_cache.json"
COMFY_ARCHETYPES_DIRS = [
    BASE_DIR / "workflows" / "archetypes",
    BASE_DIR.parent / "workflows" / "archetypes",
]
comfy_schema_mgr = ComfySchemaManager(
    cache_file=COMFY_CACHE_FILE,
    archetypes_dirs=COMFY_ARCHETYPES_DIRS,
)

LOCAL_TS_IP = os.environ.get("OUTERHEAVEN_TS_IP", "100.116.38.29")
FOX_TS_IP = os.environ.get("FOX_TS_IP", "100.93.184.87")
LOVENSE_IP = os.environ.get("LOVENSE_IP", "192.168.188.34").strip().strip('"').strip("'")
LOVENSE_PORT = int(os.environ.get("LOVENSE_PORT", 20010))
PORT = 8080
SSL_PORT = 8443
SSL_CERT = BASE_DIR / "cert.pem"
SSL_KEY = BASE_DIR / "key.pem"

COMFYUI_URL = "http://127.0.0.1:8188"
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"
FOX_OLLAMA_URL = f"http://{FOX_TS_IP}:11434"

LOCAL_MODEL = "deepseek-r1:14b"
FOX_MODEL = "hermes3:8b"
FOX_CONFIG_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/fox_model.json")

def get_active_ollama_model() -> str:
    """
    Returns the configured active Ollama model from bot_config.json,
    falling back to LOCAL_MODEL.
    """
    if BOT_CONFIG_FILE.is_file():
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                m = data.get("llm", {}).get("model")
                if m and isinstance(m, str) and m.strip():
                    return m.strip()
        except Exception as e:
            logger.debug(f"Could not read model from bot_config: {e}")
    return LOCAL_MODEL

def set_active_ollama_model(model_name: str) -> bool:
    """Persists chosen model to bot_config.json."""
    try:
        data = {}
        if BOT_CONFIG_FILE.is_file():
            try:
                data = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if "llm" not in data or not isinstance(data["llm"], dict):
            data["llm"] = {}
        data["llm"]["model"] = model_name
        BOT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        BOT_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Updated active Outer Heaven model to {model_name} in {BOT_CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to set active Ollama model: {e}")
        return False

def get_active_fox_model() -> str:
    """Returns active model for node fox from FOX_CONFIG_FILE or default."""
    if FOX_CONFIG_FILE.is_file():
        try:
            data = json.loads(FOX_CONFIG_FILE.read_text(encoding="utf-8"))
            m = data.get("model")
            if m and isinstance(m, str) and m.strip():
                return m.strip()
        except Exception:
            pass
    return FOX_MODEL

def set_active_fox_model(model_name: str) -> bool:
    """Persists chosen active model for node fox."""
    global FOX_MODEL
    try:
        FOX_MODEL = model_name
        FOX_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        FOX_CONFIG_FILE.write_text(json.dumps({"model": model_name, "updated_at": time.time()}, indent=2), encoding="utf-8")
        logger.info(f"Updated active Fox model to {model_name} in {FOX_CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to set active Fox model: {e}")
        return False

async def unload_local_ollama_vram(model_name: Optional[str] = None):
    """
    Calls Ollama generate with keep_alive: 0 to flush VRAM from Intel Arc A770.
    """
    target_model = model_name or get_active_ollama_model()
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {"model": target_model, "keep_alive": 0}
            async with session.post(f"{LOCAL_OLLAMA_URL}/api/generate", json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"✔ Flushed local Ollama model {target_model} from VRAM (keep_alive: 0)")
    except Exception as e:
        logger.debug(f"Could not unload local Ollama VRAM: {e}")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("mission-control")

# --- Load Secrets & Password Management ---
DEFAULT_DASHBOARD_PASSWORD = os.environ.get("DEFAULT_DASHBOARD_PASSWORD", "outerheaven2026")
PRIMARY_PW_FILE = Path("/home/outerheaven/mission-control/secrets/dashboard_password.txt")

def load_secrets() -> Tuple[str, str, str]:
    secrets_dir = SECRETS_DIR
    if not secrets_dir.is_dir():
        local_secrets = BASE_DIR / "secrets"
        try:
            local_secrets.mkdir(parents=True, exist_ok=True)
            secrets_dir = local_secrets
        except Exception:
            pass

    # 1. Master Dashboard Passwort persistent laden
    password = ""
    candidate_pw_files = [
        PRIMARY_PW_FILE,
        BASE_DIR / "secrets" / "dashboard_password.txt",
        secrets_dir / "dashboard_password.txt"
    ]
    for cand in candidate_pw_files:
        if cand.is_file():
            content = cand.read_text().strip()
            if content:
                password = content
                break

    if not password:
        logger.warning(
            f"Passwort-Datei nicht gefunden (geprüft: {PRIMARY_PW_FILE}). "
            f"Verwende konfigurierbaren Standard-Fallback (DEFAULT_DASHBOARD_PASSWORD)."
        )
        password = DEFAULT_DASHBOARD_PASSWORD
        for cand in [PRIMARY_PW_FILE, BASE_DIR / "secrets" / "dashboard_password.txt"]:
            try:
                cand.parent.mkdir(parents=True, exist_ok=True)
                cand.write_text(password + "\n")
                os.chmod(cand, 0o600)
                logger.info(f"Standardpasswort persistent in {cand} gespeichert.")
                break
            except Exception as e:
                logger.debug(f"Konnte Standardpasswort nicht in {cand} persistieren: {e}")

    # 2. Cluster API Key laden
    key_file = secrets_dir / "cluster_api.key"
    if not key_file.is_file() and (BASE_DIR / "secrets" / "cluster_api.key").is_file():
        key_file = BASE_DIR / "secrets" / "cluster_api.key"

    cluster_key = ""
    if key_file.is_file():
        cluster_key = key_file.read_text().strip()
    else:
        logger.warning(f"Cluster API key {key_file} not found, generating temporary")
        cluster_key = secrets.token_hex(32)
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(cluster_key + "\n")
            os.chmod(key_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not persist temporary cluster key to disk: {e}")

    # 3. Restricted Password laden
    restricted_file = secrets_dir / "restricted_password.txt"
    if not restricted_file.is_file() and (BASE_DIR / "secrets" / "restricted_password.txt").is_file():
        restricted_file = BASE_DIR / "secrets" / "restricted_password.txt"

    restricted_pw = os.environ.get("RESTRICTED_PASSWORD", "")
    if not restricted_pw and restricted_file.is_file():
        restricted_pw = restricted_file.read_text().strip()
    if not restricted_pw:
        restricted_pw = "OH-RESTRICTED-" + secrets.token_urlsafe(12)
        try:
            restricted_file.parent.mkdir(parents=True, exist_ok=True)
            restricted_file.write_text(restricted_pw + "\n")
            os.chmod(restricted_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not persist restricted password to disk: {e}")

    return password, cluster_key, restricted_pw

DASHBOARD_PASSWORD, CLUSTER_API_KEY, RESTRICTED_PASSWORD = load_secrets()

# --- Trusted IP Whitelist for Mission Control Access ---
TRUSTED_IPS: Set[str] = {
    # Cluster / Rechner
    "100.88.191.31",    # bigboss Laptop
    "100.93.184.87",    # fox EliteDesk
    "100.116.38.29",   # outerheaven Z620
    "100.127.247.117",  # deraldehesse
    # Eigene Mobilgeräte
    "100.86.115.107",   # poco-x8-pro
    "100.107.42.22",    # redmi-note-10-pro
    "100.86.154.72",    # redmi-note-12-1
    # Lokaler Loopback
    "127.0.0.1",
    "::1",
}

# Lokale Subnetze (z. B. LAN 192.168.*, 10.*, 127.*, Docker Bridge 172.16-31.*)
TRUSTED_LOCAL_SUBNETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
]

def get_client_ip(request: web.Request) -> str:
    """
    Ermittelt die tatsächliche Client-IP aus X-Forwarded-For, X-Real-IP
    oder request.remote (Fallback).
    """
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        client_ip = xff.split(",")[0].strip()
        if client_ip:
            return client_ip

    x_real_ip = request.headers.get("X-Real-IP", "").strip()
    if x_real_ip:
        return x_real_ip

    return request.remote or ""

def is_ip_whitelisted(ip_str: str) -> bool:
    """
    Prüft, ob eine Client-IP in TRUSTED_IPS enthalten ist oder zu
    einem erlaubten lokalen Subnetz gehört.
    WICHTIG: Tailscale-IPs (100.*) müssen explizit in TRUSTED_IPS aufgeführt sein.
    Nicht-gelistete IPs wie 100.95.109.3 (robert-latitude) werden abgelehnt!
    """
    if not ip_str:
        return False

    if ip_str in TRUSTED_IPS:
        return True

    try:
        ip_obj = ipaddress.ip_address(ip_str)
        if ip_obj.is_loopback:
            return True
        for net in TRUSTED_LOCAL_SUBNETS:
            if ip_obj in net:
                return True
    except ValueError:
        pass

    return False

# Persistent server secret to maintain sessions across reloads/restarts
def load_server_secret() -> str:
    candidate_paths = [
        BASE_DIR / "data" / ".server_secret",
        Path("/home/outerheaven/mission-control/data/.server_secret"),
        SECRETS_DIR / ".server_secret",
        BASE_DIR / "secrets" / ".server_secret"
    ]
    for p in candidate_paths:
        try:
            if p.is_file():
                s = p.read_text().strip()
                if len(s) >= 32:
                    return s
        except Exception:
            pass

    # Generate new persistent secret
    new_secret = secrets.token_hex(32)
    saved = False
    for p in candidate_paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new_secret + "\n")
            os.chmod(p, 0o600)
            saved = True
            break
        except Exception:
            continue
    return new_secret

SERVER_SECRET = load_server_secret()

def generate_session_cookie(is_restricted: bool = False) -> str:
    timestamp = str(int(time.time()))
    mode = "restricted" if is_restricted else "standard"
    signature = hmac.new(
        SERVER_SECRET.encode(),
        f"valid_session:{timestamp}:{mode}:{DASHBOARD_PASSWORD}".encode(),
        hashlib.sha256
    ).hexdigest()
    return f"{timestamp}.{mode}.{signature}"

def verify_session_cookie(cookie: Optional[str]) -> Tuple[bool, bool]:
    """Returns (is_valid, is_restricted)."""
    if not cookie or "." not in cookie:
        return False, False
    parts = cookie.split(".")
    try:
        if len(parts) == 3:
            ts_str, mode, signature = parts
            ts = int(ts_str)
            if time.time() - ts > 7 * 86400:
                return False, False
            expected = hmac.new(
                SERVER_SECRET.encode(),
                f"valid_session:{ts_str}:{mode}:{DASHBOARD_PASSWORD}".encode(),
                hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(signature, expected):
                return True, (mode == "restricted")
            return False, False
        elif len(parts) == 2:
            ts_str, signature = parts
            ts = int(ts_str)
            if time.time() - ts > 7 * 86400:
                return False, False
            expected = hmac.new(
                SERVER_SECRET.encode(),
                f"valid_session:{ts_str}:{DASHBOARD_PASSWORD}".encode(),
                hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(signature, expected):
                return True, False
            return False, False
        return False, False
    except Exception:
        return False, False

# --- Global Telemetry Cache ---
telemetry_cache = {
    "outerheaven": {
        "node": "outerheaven",
        "ip": LOCAL_TS_IP,
        "online": True,
        "load": [0.0, 0.0, 0.0],
        "ram": {"total": 0, "used": 0, "free": 0, "percent": 0.0},
        "gpu": {
            "name": "Sparkle Intel Arc A770 16GB",
            "vram_total": 16225243136,
            "vram_used": 0,
            "vram_free": 16225243136,
            "vram_percent": 0.0,
            "source": "hardware"
        },
        "services": {
            "comfyui": "unknown",
            "ollama": "unknown",
            "hermes-telegram": "unknown",
            "ollama-model-watcher": "unknown"
        },
        "models": []
    },
    "fox": {
        "node": "fox",
        "ip": FOX_TS_IP,
        "online": False,
        "load": [0.0, 0.0, 0.0],
        "ram": {"total": 0, "used": 0, "free": 0, "percent": 0.0},
        "services": {
            "ollama": "unknown",
            "ollama-tailscale-proxy": "unknown",
            "fox_sync": "standby"
        },
        "models": []
    },
    "delegation": {
        "active": False,
        "state": "idle",
        "delegated_to": None,
        "task": "",
        "origin_node": "",
        "target_node": "",
        "model": "",
        "reason": "",
        "updated_at": 0,
        "history": []
    },
    "queue": {
        "queued_count": 0,
        "active_count": 0,
        "queued_tasks": [],
        "active_tasks": [],
        "latest_finished": [],
        "total_tasks": 0
    },
    "last_update": 0
}

def get_cluster_tasks_info() -> Dict[str, Any]:
    if not CLUSTER_TASKS_FILE.is_file():
        return {
            "queued_count": 0,
            "active_count": 0,
            "queued_tasks": [],
            "active_tasks": [],
            "latest_finished": [],
            "total_tasks": 0
        }
    try:
        with open(CLUSTER_TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            tasks = data.get("tasks", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            queued = [t for t in tasks if t.get("status") == "QUEUED_FOR_OH"]
            active = [t for t in tasks if t.get("status") == "WORKING_ON_TASK"]
            finished = [t for t in tasks if t.get("status") in ("FINISHED", "FAILED")]
            finished.sort(key=lambda x: x.get("finished_at") or 0, reverse=True)
            return {
                "queued_count": len(queued),
                "active_count": len(active),
                "queued_tasks": queued,
                "active_tasks": active,
                "latest_finished": finished[:5],
                "total_tasks": len(tasks)
            }
    except Exception as e:
        logger.debug(f"Error reading cluster_tasks.json: {e}")
        return {
            "queued_count": 0,
            "active_count": 0,
            "queued_tasks": [],
            "active_tasks": [],
            "latest_finished": [],
            "total_tasks": 0
        }

# --- Telemetry Collector ---
async def get_outerheaven_telemetry(session: aiohttp.ClientSession) -> Dict[str, Any]:
    load = [0.0, 0.0, 0.0]
    try:
        with open("/proc/loadavg", "r") as f:
            parts = f.read().split()
            load = [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:
        pass

    ram = {"total": 0, "used": 0, "free": 0, "percent": 0.0}
    try:
        mem = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                p = line.split(":")
                if len(p) == 2:
                    mem[p[0].strip()] = int(p[1].split()[0])
        total = mem.get("MemTotal", 0) * 1024
        avail = mem.get("MemAvailable", 0) * 1024
        used = total - avail
        ram = {
            "total": total,
            "used": used,
            "free": avail,
            "percent": round((used / total) * 100, 1) if total else 0.0
        }
    except Exception:
        pass

    gpu = {
        "name": "Sparkle Intel Arc A770 16GB",
        "vram_total": 16225243136,
        "vram_used": 0,
        "vram_free": 16225243136,
        "vram_percent": 0.0,
        "source": "hardware"
    }

    try:
        async with session.get(f"{COMFYUI_URL}/system_stats", timeout=aiohttp.ClientTimeout(total=1.2)) as resp:
            if resp.status == 200:
                stats = await resp.json()
                devices = stats.get("devices", [])
                for d in devices:
                    if "arc" in d.get("name", "").lower() or d.get("type") == "xpu":
                        v_total = d.get("vram_total", 16225243136)
                        v_free = d.get("vram_free", 0)
                        v_used = max(0, v_total - v_free)
                        gpu["vram_total"] = v_total
                        gpu["vram_used"] = v_used
                        gpu["vram_free"] = v_free
                        gpu["vram_percent"] = round((v_used / v_total) * 100, 1) if v_total else 0.0
                        gpu["source"] = "comfyui"
                        break
    except Exception:
        pass

    models = []
    try:
        async with session.get(f"{LOCAL_OLLAMA_URL}/api/ps", timeout=aiohttp.ClientTimeout(total=1.2)) as resp:
            if resp.status == 200:
                ps_data = await resp.json()
                models = [m.get("name") for m in ps_data.get("models", [])]
                if gpu["source"] != "comfyui" and ps_data.get("models"):
                    v_used = sum(m.get("size_vram", 0) for m in ps_data.get("models", []))
                    gpu["vram_used"] = v_used
                    gpu["vram_free"] = max(0, gpu["vram_total"] - v_used)
                    gpu["vram_percent"] = round((v_used / gpu["vram_total"]) * 100, 1)
                    gpu["source"] = "ollama"
    except Exception:
        pass

    services = {"comfyui": "inactive", "ollama": "inactive", "hermes-telegram": "inactive", "ollama-model-watcher": "inactive"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "is-active", "comfyui.service", "bigboss-haven-bot.service", "hermes-telegram.service",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        lines = out.decode().strip().split("\n")
        if len(lines) >= 1 and lines[0]: services["comfyui"] = lines[0]
        bot_status = "inactive"
        if len(lines) >= 2 and lines[1] == "active":
            bot_status = "active"
        elif len(lines) >= 3 and lines[2] == "active":
            bot_status = "active"
        else:
            try:
                pg = await asyncio.create_subprocess_exec("pgrep", "-f", "telegram_bot.py", stdout=asyncio.subprocess.PIPE)
                pg_out, _ = await pg.communicate()
                if pg.returncode == 0 and pg_out.strip():
                    bot_status = "active"
            except Exception:
                pass
        services["hermes-telegram"] = bot_status
    except Exception:
        pass

    try:
        proc2 = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "ollama.service", "ollama-model-watcher.service",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out2, _ = await proc2.communicate()
        lines2 = out2.decode().strip().split("\n")
        if len(lines2) >= 1 and lines2[0]: services["ollama"] = lines2[0]
        if len(lines2) >= 2 and lines2[1]: services["ollama-model-watcher"] = lines2[1]
    except Exception:
        pass

    if services.get("ollama-model-watcher") not in ["active", "activating"]:
        try:
            proc_uw = await asyncio.create_subprocess_exec(
                "systemctl", "--user", "is-active", "ollama-model-watcher.service",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out_uw, _ = await proc_uw.communicate()
            res_uw = out_uw.decode().strip()
            if res_uw in ["active", "activating"]:
                services["ollama-model-watcher"] = res_uw
        except Exception:
            pass
    if services.get("ollama-model-watcher") not in ["active", "activating"]:
        try:
            pg_w = await asyncio.create_subprocess_exec("pgrep", "-f", "watch-ollama-model", stdout=asyncio.subprocess.PIPE)
            pg_w_out, _ = await pg_w.communicate()
            if pg_w.returncode == 0 and pg_w_out.strip():
                services["ollama-model-watcher"] = "active"
        except Exception:
            pass

    active_model = get_active_ollama_model()
    return {
        "node": "outerheaven",
        "ip": LOCAL_TS_IP,
        "online": True,
        "load": load,
        "ram": ram,
        "gpu": gpu,
        "services": services,
        "models": models,
        "active_model": active_model
    }

async def get_fox_telemetry(session: aiohttp.ClientSession) -> Dict[str, Any]:
    models = []
    ollama_ok = False
    try:
        async with session.get(f"{FOX_OLLAMA_URL}/api/ps", timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
            if resp.status == 200:
                ollama_ok = True
                ps_data = await resp.json()
                models = [m.get("name") for m in ps_data.get("models", [])]
    except Exception:
        pass

    if not ollama_ok:
        try:
            async with session.get(f"{FOX_OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
                if resp.status == 200:
                    ollama_ok = True
                    tags_data = await resp.json()
                    models = [m.get("name") for m in tags_data.get("models", [])]
        except Exception:
            pass

    load = [0.0, 0.0, 0.0]
    ram = {"total": 0, "used": 0, "free": 0, "percent": 0.0}
    services = {
        "ollama": "active" if ollama_ok else "inactive",
        "ollama-tailscale-proxy": "active" if ollama_ok else "unknown",
        "fox_sync": "standby"
    }
    online = False
    fox_sync_active = False

    try:
        cmd = "ssh -o BatchMode=yes -o ConnectTimeout=2 fox 'cat /proc/loadavg; echo \"---\"; free -b; echo \"---\"; systemctl --user is-active ollama-tailscale-proxy.service; systemctl is-active ollama.service; echo \"---\"; pgrep -x rsync >/dev/null && echo syncing || echo standby'"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        if proc.returncode in [0, 3, 4]:
            online = True
            blocks = out.decode().strip().split("---" + chr(10))
            if len(blocks) >= 1:
                p = blocks[0].strip().split()
                if len(p) >= 3:
                    load = [float(p[0]), float(p[1]), float(p[2])]
            if len(blocks) >= 2:
                for line in blocks[1].strip().split(chr(10)):
                    if "speicher" in line.lower() or "mem:" in line.lower():
                        nums = [int(s) for s in line.replace(":", " ").split() if s.isdigit()]
                        if len(nums) >= 2:
                            tot = nums[0]
                            usd = nums[1]
                            ram = {
                                "total": tot,
                                "used": usd,
                                "free": tot - usd,
                                "percent": round((usd / tot) * 100, 1) if tot else 0.0
                            }
                            break
            if len(blocks) >= 3:
                svc_lines = blocks[2].strip().split(chr(10))
                if len(svc_lines) >= 1 and svc_lines[0]:
                    services["ollama-tailscale-proxy"] = svc_lines[0].strip()
                if len(svc_lines) >= 2 and svc_lines[1]:
                    sys_ollama = svc_lines[1].strip()
                    if sys_ollama in ["active", "activating"]:
                        services["ollama"] = "active"
                    elif ollama_ok:
                        services["ollama"] = "active"
                    else:
                        services["ollama"] = sys_ollama
            if len(blocks) >= 4:
                sync_out = blocks[3].strip().lower()
                if "syncing" in sync_out:
                    fox_sync_active = True
    except Exception as e:
        logger.debug(f"Fox telemetry error: {e}")

    # Check if rsync is active across the cluster (Fox or Outer Heaven)
    is_syncing = fox_sync_active
    if not is_syncing:
        try:
            proc_rs = await asyncio.create_subprocess_exec(
                "pgrep", "-x", "rsync",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out_rs, _ = await proc_rs.communicate()
            if proc_rs.returncode == 0 and out_rs.strip():
                is_syncing = True
        except Exception:
            pass

    sync_status = "syncing" if is_syncing else "standby"
    sync_label = "SYNCING (Aktiv)" if is_syncing else "STANDBY (Bereit)"
    sync_class = "badge-active" if is_syncing else "badge-idle"

    services["fox_sync"] = sync_status
    services["cluster-sync"] = sync_status
    services["fox_sync_label"] = sync_label
    services["fox_sync_class"] = sync_class

    if ollama_ok:
        services["ollama"] = "active"

    is_reb = Path("/tmp/fox_is_rebooting").is_file()
    return {
        "node": "fox",
        "ip": FOX_TS_IP,
        "online": (online or ollama_ok) and not is_reb,
        "rebooting": is_reb,
        "load": load,
        "ram": ram,
        "services": services,
        "cluster_sync": {
            "status": sync_status,
            "label": sync_label,
            "css_class": sync_class
        },
        "models": models
    }

async def check_lovense_health(session: aiohttp.ClientSession) -> dict:
    url = f"http://{LOVENSE_IP}:{LOVENSE_PORT}/command"
    payload = {"command": "GetToys"}
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
            if resp.status == 200:
                data = await resp.json()
                toy_data = data.get("data", {}).get("toys")
                parsed_toys = {}
                if isinstance(toy_data, dict):
                    parsed_toys = toy_data
                elif isinstance(toy_data, str):
                    try:
                        p = json.loads(toy_data)
                        if isinstance(p, dict):
                            parsed_toys = p
                        elif isinstance(p, list):
                            parsed_toys = {t.get("id", str(i)): t for i, t in enumerate(p) if isinstance(t, dict)}
                    except Exception:
                        pass
                elif isinstance(toy_data, list):
                    parsed_toys = {t.get("id", str(i)): t for i, t in enumerate(toy_data) if isinstance(t, dict)}

                if parsed_toys:
                    toy_names = [t.get("name") or t.get("nickName") or k for k, t in parsed_toys.items()]
                    details = []
                    for k, t in parsed_toys.items():
                        name = t.get("name") or t.get("nickName") or k
                        bat = t.get("battery", "")
                        details.append(f"{name} ({bat}% Akku)" if bat != "" else name)
                    return {
                        "online": True,
                        "status": "ONLINE",
                        "toy_names": toy_names,
                        "details": ", ".join(details),
                        "toys": parsed_toys,
                        "toy_count": len(parsed_toys),
                        "last_checked": time.time()
                    }
                else:
                    return {
                        "online": False,
                        "status": "NO_TOYS",
                        "toy_names": [],
                        "details": "Bridge erreichbar, kein Toy gekoppelt",
                        "toys": {},
                        "toy_count": 0,
                        "last_checked": time.time()
                    }
    except Exception:
        pass
    return {
        "online": False,
        "status": "OFFLINE",
        "toy_names": [],
        "details": "Lovense Bridge offline",
        "toys": {},
        "toy_count": 0,
        "last_checked": time.time()
    }

async def dispatch_lovense_command(action: str, intensity: int = 0, time_sec: int = 0) -> dict:
    url = f"http://{LOVENSE_IP}:{LOVENSE_PORT}/command"
    act_lower = action.lower().strip()
    if act_lower in ["stop", "stop_all", "off", "0"]:
        payload = {
            "command": "Function",
            "action": "Stop",
            "timeSec": 0,
            "apiVer": 1
        }
    else:
        clamped = max(1, min(20, int(intensity)))
        payload = {
            "command": "Function",
            "action": f"Vibrate:{clamped}",
            "timeSec": int(time_sec),
            "apiVer": 1
        }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.5)) as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                logger.info(f"[Haptic Link] Command dispatched: {payload['action']} -> {data.get('type') or data.get('code')}")
                return {"success": True, "data": data}
    except Exception as e:
        logger.warning(f"[Haptic Link] Command failed: {e}")
        return {"success": False, "error": str(e)}

async def telemetry_background_loop():
    logger.info("Starting telemetry background monitoring loop")
    timeout = aiohttp.ClientTimeout(total=2.5)
    loop_idx = 0
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                loop_idx += 1
                oh_data, fox_data = await asyncio.gather(
                    get_outerheaven_telemetry(session),
                    get_fox_telemetry(session),
                    return_exceptions=True
                )
                if not isinstance(oh_data, Exception):
                    telemetry_cache["outerheaven"] = oh_data
                if not isinstance(fox_data, Exception):
                    telemetry_cache["fox"] = fox_data
                telemetry_cache["queue"] = get_cluster_tasks_info()
                telemetry_cache["active_model"] = get_active_ollama_model()
                
                # Poll Lovense status every ~5s
                if loop_idx % 2 == 0 or "lovense" not in telemetry_cache:
                    telemetry_cache["lovense"] = await check_lovense_health(session)

                telemetry_cache["last_update"] = time.time()
            except Exception as e:
                logger.error(f"Telemetry loop error: {e}")
            await asyncio.sleep(2.5)

# --- Think Tag Filter ---
class HapticTagFilter:
    """Buffers text stream, strips [HAPTIC: ...] tags, and triggers callback when found."""
    def __init__(self, on_haptic_tag=None):
        self.buffer = ""
        self.on_haptic_tag = on_haptic_tag

    def feed(self, text: str) -> str:
        self.buffer += text
        output = ""
        while True:
            match = re.search(r'\[HAPTIC:\s*(VIBRATE\s*(\d+)|STOP)\]', self.buffer, re.IGNORECASE)
            if match:
                start, end = match.span()
                output += self.buffer[:start]
                tag = match.group(0)
                if self.on_haptic_tag:
                    self.on_haptic_tag(tag)
                self.buffer = self.buffer[end:]
                continue

            idx = self.buffer.rfind("[")
            if idx != -1:
                candidate = self.buffer[idx:].upper()
                if "[HAPTIC".startswith(candidate) or candidate.startswith("[HAPTIC"):
                    output += self.buffer[:idx]
                    self.buffer = self.buffer[idx:]
                    break
            output += self.buffer
            self.buffer = ""
            break
        return output

    def flush(self) -> str:
        res = self.buffer
        self.buffer = ""
        match = re.search(r'\[HAPTIC:\s*(VIBRATE\s*(\d+)|STOP)\]', res, re.IGNORECASE)
        if match:
            if self.on_haptic_tag:
                self.on_haptic_tag(match.group(0))
            res = re.sub(r'\[HAPTIC:[^\]]*\]', '', res, flags=re.IGNORECASE)
        return res

class StreamActionFilter:
    """Buffers text stream, strips [IMAGE: ...], [SELFIE: ...], and [VIDEO: ...] tags, and triggers callback when found."""
    def __init__(self, on_action_tag=None):
        self.buffer = ""
        self.on_action_tag = on_action_tag

    def feed(self, text: str) -> str:
        self.buffer += text
        output = ""
        while True:
            match = re.search(r'\[(IMAGE|SELFIE|VIDEO):\s*([^\]]+)\]', self.buffer, re.IGNORECASE)
            if match:
                start, end = match.span()
                output += self.buffer[:start]
                action_type = match.group(1).upper()
                prompt_content = match.group(2).strip()
                if self.on_action_tag:
                    self.on_action_tag(action_type, prompt_content)
                self.buffer = self.buffer[end:]
                continue

            idx = self.buffer.rfind("[")
            if idx != -1:
                candidate = self.buffer[idx:].upper()
                if any("[IMAGE".startswith(candidate[:i]) for i in range(2, min(len(candidate) + 1, 7))) or \
                   any("[SELFIE".startswith(candidate[:i]) for i in range(2, min(len(candidate) + 1, 8))) or \
                   any("[VIDEO".startswith(candidate[:i]) for i in range(2, min(len(candidate) + 1, 7))) or \
                   candidate.startswith("[IMAGE") or candidate.startswith("[SELFIE") or candidate.startswith("[VIDEO"):
                    output += self.buffer[:idx]
                    self.buffer = self.buffer[idx:]
                    break
            output += self.buffer
            self.buffer = ""
            break
        return output

    def flush(self) -> str:
        res = self.buffer
        self.buffer = ""
        match = re.search(r'\[(IMAGE|SELFIE|VIDEO):\s*([^\]]+)\]', res, re.IGNORECASE)
        if match:
            action_type = match.group(1).upper()
            prompt_content = match.group(2).strip()
            if self.on_action_tag:
                self.on_action_tag(action_type, prompt_content)
            res = re.sub(r'\[(IMAGE|SELFIE|VIDEO):[^\]]*\]', '', res, flags=re.IGNORECASE)
        return res

# Backward compatibility alias
ImageActionFilter = StreamActionFilter


class ThinkFilter:
    def __init__(self):
        self.in_think = False
        self.buffer = ""

    def feed(self, chunk: str) -> List[Tuple[str, str]]:
        self.buffer += chunk
        results = []
        while self.buffer:
            if not self.in_think:
                if "<think>" in self.buffer:
                    before, _, after = self.buffer.partition("<think>")
                    if before:
                        results.append(("text", before))
                    self.in_think = True
                    self.buffer = after
                elif "<" in self.buffer:
                    idx = self.buffer.find("<")
                    if "<think>".startswith(self.buffer[idx:]):
                        if idx > 0:
                            results.append(("text", self.buffer[:idx]))
                            self.buffer = self.buffer[idx:]
                        break
                    else:
                        results.append(("text", self.buffer[:idx+1]))
                        self.buffer = self.buffer[idx+1:]
                else:
                    results.append(("text", self.buffer))
                    self.buffer = ""
            else:
                if "</think>" in self.buffer:
                    think_text, _, after = self.buffer.partition("</think>")
                    if think_text:
                        results.append(("think", think_text))
                    self.in_think = False
                    self.buffer = after.lstrip("\n")
                elif "<" in self.buffer:
                    idx = self.buffer.find("<")
                    if "</think>".startswith(self.buffer[idx:]):
                        if idx > 0:
                            results.append(("think", self.buffer[:idx]))
                            self.buffer = self.buffer[idx:]
                        break
                    else:
                        results.append(("think", self.buffer[:idx+1]))
                        self.buffer = self.buffer[idx+1:]
                else:
                    results.append(("think", self.buffer))
                    self.buffer = ""
        return results

    def flush(self) -> List[Tuple[str, str]]:
        results = []
        if self.buffer:
            if not self.in_think:
                results.append(("text", self.buffer))
            else:
                results.append(("think", self.buffer))
            self.buffer = ""
        return results

MEGAN_TRUNCATE_MARKERS = [
    "\nUser:",
    "\nBig Boss:",
    "Big Boss:",
    "\nSebastian:",
    "Sebastian:",
    "*Big Boss",
    "*big boss",
    "*Sebastian",
    "*sebastian",
    "<turn|>",
    "<|turn>",
    "<|im_end|>",
    "[Spontaneous"
]

class MeganTruncateSanitizer:
    """
    Pre-Save & Stream Sanitizer for Megan Studio:
    Stops hallucinated monologues and roleplay drift.
    Strictly cuts off output before the first occurrence of user markers, trigger blocks, or stop tokens.
    """
    def __init__(self):
        self.triggered = False
        self.buffer = ""
        self.stop_patterns = MEGAN_TRUNCATE_MARKERS

    def feed(self, text: str) -> str:
        if self.triggered or not text:
            return ""

        self.buffer += text

        # Check for immediate trigger from stop_patterns
        earliest_pos = -1
        for p in self.stop_patterns:
            pos = self.buffer.find(p)
            if pos != -1:
                if earliest_pos == -1 or pos < earliest_pos:
                    earliest_pos = pos

        # Check for trigger blocks like \n[ or [Spontaneous or [Trigger:
        bracket_match = re.search(r"(\n\[|\[(?:Spontaneous|[A-Za-z0-9_\-]+:))", self.buffer)
        if bracket_match:
            b_pos = bracket_match.start()
            if earliest_pos == -1 or b_pos < earliest_pos:
                earliest_pos = b_pos

        if earliest_pos != -1:
            self.triggered = True
            output = self.buffer[:earliest_pos]
            self.buffer = ""
            return output

        # Check for potential partial match at the tail
        max_prefix_len = 0
        for p in self.stop_patterns:
            for l in range(1, len(p)):
                if self.buffer.endswith(p[:l]):
                    if l > max_prefix_len:
                        max_prefix_len = l

        if max_prefix_len > 0:
            output = self.buffer[:-max_prefix_len]
            self.buffer = self.buffer[-max_prefix_len:]
            return output

        output = self.buffer
        self.buffer = ""
        return output

    def flush(self) -> str:
        if self.triggered:
            return ""
        res = self.buffer
        self.buffer = ""
        for p in self.stop_patterns:
            pos = res.find(p)
            if pos != -1:
                self.triggered = True
                res = res[:pos]
        bracket_match = re.search(r"(\n\[|\[(?:Spontaneous|[A-Za-z0-9_\-]+:))", res)
        if bracket_match:
            self.triggered = True
            res = res[:bracket_match.start()]
        return res

def sanitize_megan_text(text: str) -> str:
    """
    Synchronous string sanitizer for Megan Studio responses:
    - Truncates strictly at the first occurrence of foreign role markers, stop tokens, or trigger blocks.
    - If the entire response consists only of foreign role hallucination, discards it and logs neutral status.
    """
    if not text:
        return ""

    earliest = -1
    for m in MEGAN_TRUNCATE_MARKERS:
        idx = text.find(m)
        if idx != -1:
            if earliest == -1 or idx < earliest:
                earliest = idx

    bracket_m = re.search(r"(\n\[|\[(?:Spontaneous|[A-Za-z0-9_\-]+:))", text)
    if bracket_m:
        idx = bracket_m.start()
        if earliest == -1 or idx < earliest:
            earliest = idx

    if earliest != -1:
        text = text[:earliest]

    cleaned = text.rstrip()

    # If the response was entirely foreign roles / hallucination, discard and log
    if not cleaned or re.match(r"^[\s\r\n]*(?:User:|Big Boss:|Sebastian:|<turn\|>|<\|turn>|<\|im_end\|>|\[)", cleaned):
        logger.warning("[Sanitizer] Bot response consisted entirely of foreign roles or trigger blocks; discarded.")
        return ""

    return cleaned

# --- Auth Middleware ---
@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path

    authenticated = False
    is_restricted = False
    ip_whitelisted = False

    # 1. IP-Erkennung & Whitelist-Prüfung (Bypass aktiv für bekannte Cluster-Nodes & Mobilgeräte)
    client_ip = get_client_ip(request)
    if is_ip_whitelisted(client_ip):
        authenticated = True
        is_restricted = False
        ip_whitelisted = True

    # 2. Wenn nicht durch IP-Whitelist autorisiert: Session-Cookie prüfen
    if not authenticated:
        session_cookie = request.cookies.get("mc_session")
        valid_cookie, cookie_restricted = verify_session_cookie(session_cookie)
        if valid_cookie:
            authenticated = True
            is_restricted = cookie_restricted

    # 3. Prüfung auf Cluster API Key (für Inter-Node Automation)
    cluster_hdr = request.headers.get("X-Cluster-Key", "")
    if cluster_hdr and hmac.compare_digest(cluster_hdr, CLUSTER_API_KEY):
        authenticated = True
        is_restricted = True

    # 4. Prüfung auf HTTP Basic Auth
    if not authenticated:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_hdr[6:]).decode("utf-8")
                _, pw = decoded.split(":", 1)
                if hmac.compare_digest(pw, RESTRICTED_PASSWORD):
                    authenticated = True
                    is_restricted = True
                elif hmac.compare_digest(pw, DASHBOARD_PASSWORD):
                    authenticated = True
                    is_restricted = False
            except Exception:
                pass

    request["authenticated"] = authenticated
    request["is_restricted"] = is_restricted
    request["ip_whitelisted"] = ip_whitelisted
    request["client_ip"] = client_ip

    # Unbeschränkte Routen: Statische Assets (/static/...), PWA-Dateien und Login-Endpunkte
    PUBLIC_PATHS = {"/login", "/login/submit", "/manifest.json", "/sw.js", "/favicon.ico"}
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await handler(request)

    # Wenn authentifiziert (entweder durch IP-Whitelist, Session-Cookie oder Basic Auth): Durchlassen
    if authenticated:
        return await handler(request)

    # Wenn NICHT authentifiziert:
    # A) Für API- und WebSocket-Routen: HTTP 401 Unauthorized zurückgeben
    if path.startswith("/api/") or path.startswith("/ws/"):
        logger.warning(f"[Auth] 401 Unauthorized auf {path} von nicht-autorisierter IP: {client_ip}")
        return web.json_response(
            {"error": "Unauthorized", "message": "Gültige Session, Master-Passwort oder autorisierte IP erforderlich"},
            status=401
        )

    # B) Für alle Web-Views (/ und Unterseiten): Redirect nach /login ausführen
    logger.info(f"[Auth] Nicht-autorisierter Zugriff von {client_ip} auf {path} -> Redirect nach /login")
    raise web.HTTPFound("/login")

# --- Request Handlers ---

async def handle_login_page(request: web.Request) -> web.Response:
    session_cookie = request.cookies.get("mc_session")
    valid_cookie, _ = verify_session_cookie(session_cookie)
    if valid_cookie or request.get("authenticated", False):
        raise web.HTTPFound("/")
    
    template = jinja_env.get_template("login.html")
    return web.Response(text=template.render(), content_type="text/html")

async def handle_login_submit(request: web.Request) -> web.Response:
    data = {}
    if request.content_type == "application/json":
        data = await request.json()
    else:
        post = await request.post()
        data = dict(post)

    password = data.get("password", "").strip()
    is_restricted = False
    authenticated = False
    if hmac.compare_digest(password, RESTRICTED_PASSWORD):
        authenticated = True
        is_restricted = True
    elif hmac.compare_digest(password, DASHBOARD_PASSWORD):
        authenticated = True
        is_restricted = False

    if authenticated:
        cookie = generate_session_cookie(is_restricted=is_restricted)
        resp = web.json_response({"success": True, "redirect": "/", "restricted": is_restricted})
        resp.set_cookie("mc_session", cookie, max_age=7*86400, httponly=True, samesite="Lax")
        return resp
    else:
        return web.json_response({"success": False, "error": "Ungültiges Zugangspasswort"}, status=403)

async def handle_auth_elevate(request: web.Request) -> web.Response:
    """POST /api/auth/elevate - Elevate session to Level-2 / RESTRICTED clearance."""
    try:
        data = await request.json()
        password = data.get("password", "").strip()
        if hmac.compare_digest(password, RESTRICTED_PASSWORD):
            cookie = generate_session_cookie(is_restricted=True)
            resp = web.json_response({"success": True, "message": "Level-2 Clearance gewährt."})
            resp.set_cookie("mc_session", cookie, max_age=7*86400, httponly=True, samesite="Lax")
            return resp
        return web.json_response({"success": False, "error": "Ungültiger Clearance-Key"}, status=403)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/login")
    resp.del_cookie("mc_session", path="/")
    resp.set_cookie(
        "mc_session",
        "",
        max_age=0,
        expires="Thu, 01 Jan 1970 00:00:00 GMT",
        path="/",
        httponly=True,
        samesite="Lax"
    )
    return resp

async def handle_index(request: web.Request) -> web.Response:
    template = jinja_env.get_template("index.html")
    active_oh_model = get_active_ollama_model()
    is_restricted = bool(request.get("is_restricted", False))
    return web.Response(text=template.render(
        outerheaven_ip=LOCAL_TS_IP,
        fox_ip=FOX_TS_IP,
        local_model=active_oh_model,
        fox_model=FOX_MODEL,
        is_restricted=is_restricted
    ), content_type="text/html")

async def handle_telemetry(request: web.Request) -> web.Response:
    data = dict(telemetry_cache)
    return web.json_response(data)


async def handle_handover(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    status = data.get("status", "")
    node = data.get("node", "fox")
    flag_file = Path("/tmp/fox_is_rebooting")
    online_file = Path("/tmp/fox_online")

    if status == "rebooting":
        flag_file.touch(exist_ok=True)
        if online_file.is_file():
            online_file.unlink()
        logger.warning(f"Cluster Handover: Node {node} meldet bevorstehenden Shutdown/Reboot!")
        telemetry_cache["fox"]["online"] = False
        telemetry_cache["fox"]["rebooting"] = True
    elif status == "online":
        if flag_file.is_file():
            flag_file.unlink()
        online_file.touch(exist_ok=True)
        logger.info(f"Cluster Handover: Node {node} meldet sich erfolgreich ONLINE.")
        telemetry_cache["fox"]["rebooting"] = False

    return web.json_response({"success": True, "node": node, "status": status})

async def handle_delegation_update(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    task = data.get("task", "")
    state = data.get("state", "idle")  # 'running', 'completed', 'failed', 'idle'
    delegated_to = data.get("delegated_to", "")  # 'OH', 'FOX'
    origin_node = data.get("origin_node", "")
    target_node = data.get("target_node", "")
    model = data.get("model", "")
    reason = data.get("reason", "")
    now = time.time()

    is_active = (state == "running")
    delegation_info = {
        "active": is_active,
        "state": state,
        "delegated_to": delegated_to,
        "task": task,
        "origin_node": origin_node,
        "target_node": target_node,
        "model": model,
        "reason": reason,
        "updated_at": now
    }

    history = telemetry_cache.get("delegation", {}).get("history", [])
    if state in ("running", "completed", "failed"):
        history.insert(0, {
            "task": task,
            "state": state,
            "delegated_to": delegated_to,
            "origin_node": origin_node,
            "target_node": target_node,
            "model": model,
            "timestamp": now,
            "reason": reason
        })
        history = history[:15]
    delegation_info["history"] = history

    telemetry_cache["delegation"] = delegation_info
    telemetry_cache["last_update"] = now

    logger.info(f"Cluster Delegation: state={state}, delegated_to={delegated_to}, model={model}, task='{task}' ({reason})")
    return web.json_response({"success": True, "delegation": delegation_info})

async def handle_delegation_status(request: web.Request) -> web.Response:
    return web.json_response(telemetry_cache.get("delegation", {}))

async def handle_status(request: web.Request) -> web.Response:
    queue_info = get_cluster_tasks_info()
    oh_telemetry = telemetry_cache.get("outerheaven", {})
    fox_telemetry = telemetry_cache.get("fox", {})
    delegation = telemetry_cache.get("delegation", {})

    status_payload = {
        "success": True,
        "timestamp": time.time(),
        "queue": queue_info,
        "delegation": delegation,
        "outerheaven": {
            "online": oh_telemetry.get("online", False),
            "comfyui": oh_telemetry.get("services", {}).get("comfyui", "unknown"),
            "vram": oh_telemetry.get("gpu", {}),
            "load": oh_telemetry.get("load", [])
        },
        "fox": {
            "online": fox_telemetry.get("online", False),
            "rebooting": fox_telemetry.get("rebooting", False),
            "load": fox_telemetry.get("load", []),
            "sync": fox_telemetry.get("services", {}).get("fox_sync", "standby"),
            "sync_label": fox_telemetry.get("services", {}).get("fox_sync_label", "STANDBY (Bereit)"),
            "sync_class": fox_telemetry.get("services", {}).get("fox_sync_class", "badge-idle")
        }
    }
    return web.json_response(status_payload)


async def handle_lovense_status(request: web.Request) -> web.Response:
    """
    Toy-Status & Pre-Flight Check:
    Sends {"command": "GetToys"} to http://LOVENSE_IP:LOVENSE_PORT/command
    Returns Case A, Case B, or Case C.
    """
    url = f"http://{LOVENSE_IP}:{LOVENSE_PORT}/command"
    payload = {"command": "GetToys"}
    timeout = aiohttp.ClientTimeout(total=3.5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    toy_data = data.get("data", {}).get("toys")
                    parsed_toys = {}
                    if isinstance(toy_data, dict):
                        parsed_toys = toy_data
                    elif isinstance(toy_data, str):
                        try:
                            p = json.loads(toy_data)
                            if isinstance(p, dict):
                                parsed_toys = p
                            elif isinstance(p, list):
                                parsed_toys = {t.get("id", str(i)): t for i, t in enumerate(p) if isinstance(t, dict)}
                        except Exception:
                            pass
                    elif isinstance(toy_data, list):
                        parsed_toys = {t.get("id", str(i)): t for i, t in enumerate(toy_data) if isinstance(t, dict)}

                    if parsed_toys:
                        names = [t.get("name") or t.get("nickName") or k for k, t in parsed_toys.items()]
                        return web.json_response({
                            "success": True,
                            "case": "CASE_A_READY",
                            "is_ready": True,
                            "message": "✅ Toy verbunden und bereit",
                            "summary": f"✅ Toy verbunden und bereit ({', '.join(names)})",
                            "toy_count": len(parsed_toys),
                            "toys": parsed_toys
                        })
                    else:
                        return web.json_response({
                            "success": True,
                            "case": "CASE_B_NO_TOYS",
                            "is_ready": False,
                            "message": "⚠️ Handy-Bridge erreichbar, aber kein Spielzeug verbunden! Bitte Toy einschalten und in der App koppeln.",
                            "toy_count": 0,
                            "toys": {}
                        })
                else:
                    return web.json_response({
                        "success": False,
                        "case": "CASE_C_OFFLINE",
                        "is_ready": False,
                        "message": f"❌ Handy nicht erreichbar! HTTP {resp.status}"
                    })
    except Exception as e:
        return web.json_response({
            "success": False,
            "case": "CASE_C_OFFLINE",
            "is_ready": False,
            "message": "❌ Handy nicht erreichbar! Bitte prüfen, ob Lovense Connect geöffnet und 'LAN aktivieren' eingeschaltet ist.",
            "error_detail": f"{type(e).__name__}: {str(e)}"
        })


async def handle_service_control(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    node = body.get("node", "outerheaven")
    service = body.get("service", "")
    action = body.get("action", "status")

    if action not in ["start", "stop", "restart", "status"]:
        return web.json_response({"error": "Invalid action"}, status=400)

    valid_oh_services = ["comfyui", "hermes-telegram", "ollama", "ollama-model-watcher", "cluster-queue-worker"]
    valid_fox_services = ["ollama", "ollama-tailscale-proxy"]

    if node == "outerheaven":
        if service not in valid_oh_services:
            return web.json_response({"error": f"Invalid service for outerheaven. Allowed: {valid_oh_services}"}, status=400)

        is_user_svc = service in ["comfyui", "hermes-telegram", "telegram-bot", "cluster-queue-worker"]
        if is_user_svc:
            svc_name = "bigboss-haven-bot.service" if service in ["hermes-telegram", "telegram-bot"] else f"{service}.service"
            cmd = ["systemctl", "--user", action, svc_name]
        else:
            cmd = ["sudo", "systemctl", action, f"{service}.service"]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        success = (proc.returncode == 0)
        return web.json_response({
            "success": success,
            "node": node,
            "service": service,
            "action": action,
            "message": out.decode().strip() or (err.decode().strip() if not success else "OK")
        })

    elif node == "fox":
        if service not in valid_fox_services:
            return web.json_response({"error": f"Invalid service for fox. Allowed: {valid_fox_services}"}, status=400)

        if service == "ollama":
            # Restart ollama process on Fox (systemd automatically respawns it) and restart proxy
            ssh_cmd = "ssh -o BatchMode=yes -o ConnectTimeout=5 fox 'kill -9 $(pgrep -u fox -x ollama | head -n 1) 2>/dev/null; systemctl --user restart ollama-tailscale-proxy.service 2>/dev/null; sleep 1; systemctl is-active ollama.service'"
        else:
            is_user_svc = service in ["ollama-tailscale-proxy"]
            svc_prefix = "--user " if is_user_svc else ""
            ssh_cmd = f"ssh -o BatchMode=yes -o ConnectTimeout=5 fox 'systemctl {svc_prefix}{action} {service}.service'"

        proc = await asyncio.create_subprocess_shell(
            ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        out_str = out.decode().strip()
        success = (proc.returncode == 0 or "active" in out_str.lower() or "activating" in out_str.lower())
        return web.json_response({
            "success": success,
            "node": node,
            "service": service,
            "action": action,
            "message": out_str or (err.decode().strip() if not success else "OK")
        })

    return web.json_response({"error": "Unknown node"}, status=400)

# --- ComfyUI Render Bridge & Pipeline Engine ---

async def fetch_comfy_object_info(force: bool = False) -> bool:
    """Query ComfyUI /object_info at startup or on demand and cache it."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{COMFYUI_URL}/object_info", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    raw_data = await resp.json()
                    comfy_schema_mgr.update_schema(raw_data)
                    logger.info(f"ComfyUI /object_info successfully loaded & cached ({len(comfy_schema_mgr.nodes)} nodes).")
                    return True
    except Exception as e:
        logger.warning(f"Could not query ComfyUI /object_info: {e}")
    return False

async def handle_comfy_nodes(request: web.Request) -> web.Response:
    """GET /api/comfy/nodes - Returns all available nodes with categories, input_types, and output_types."""
    refresh = request.query.get("refresh", "").lower() in ("true", "1", "yes")
    if refresh:
        await fetch_comfy_object_info(force=True)
    return web.json_response(comfy_schema_mgr.get_nodes_payload())

async def handle_comfy_archetypes(request: web.Request) -> web.Response:
    """GET /api/comfy/archetypes - Returns pre-built intent archetype workflows."""
    return web.json_response({
        "archetypes": comfy_schema_mgr.get_archetypes()
    })

async def handle_comfy_archetype_detail(request: web.Request) -> web.Response:
    """GET /api/comfy/archetypes/{id} - Returns single archetype workflow."""
    arch_id = request.match_info.get("id", "")
    arch = comfy_schema_mgr.get_archetype_by_id(arch_id)
    if not arch:
        return web.json_response({"error": f"Archetype '{arch_id}' not found"}, status=404)
    return web.json_response(arch)

async def handle_comfy_pipeline_validate(request: web.Request) -> web.Response:
    """POST /api/comfy/pipeline/validate - Validates type safety and required slots."""
    try:
        data = await request.json()
        wf = PipelineWorkflow(**data)
        res = validate_pipeline(wf, comfy_schema_mgr.nodes)
        return web.json_response(res.model_dump())
    except Exception as e:
        return web.json_response({"error": str(e), "valid": False}, status=400)

async def handle_comfy_pipeline_export(request: web.Request) -> web.Response:
    """POST /api/comfy/pipeline/export - Converts visual chain to native ComfyUI prompt JSON."""
    try:
        data = await request.json()
        wf = PipelineWorkflow(**data)
        prompt_json = convert_pipeline_to_comfy_prompt(wf, comfy_schema_mgr.nodes)
        return web.json_response(prompt_json)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_comfy_pipeline_run(request: web.Request) -> web.Response:
    """POST /api/comfy/pipeline/run - Validates and executes pipeline on ComfyUI /prompt."""
    try:
        body = await request.json()
        wf_data = body.get("workflow", body)
        wf = PipelineWorkflow(**wf_data)

        # Validate pipeline
        val = validate_pipeline(wf, comfy_schema_mgr.nodes)
        if not val.valid:
            return web.json_response({
                "error": "Pipeline validation failed",
                "details": val.errors
            }, status=400)

        prompt_payload = convert_pipeline_to_comfy_prompt(wf, comfy_schema_mgr.nodes)
        client_id = body.get("client_id", secrets.token_hex(8))
        payload = {
            "prompt": prompt_payload.get("prompt", {}),
            "client_id": client_id
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=payload, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    return web.json_response({"error": f"ComfyUI /prompt error: {err_txt}"}, status=502)
                res_data = await resp.json()
                prompt_id = res_data.get("prompt_id")
                return web.json_response({
                    "success": True,
                    "prompt_id": prompt_id,
                    "status": "queued",
                    "client_id": client_id
                })
    except Exception as e:
        logger.error(f"Pipeline run error: {e}")
        return web.json_response({"error": str(e)}, status=500)


# --- Pipeline Workflow Storage & Management ---

async def handle_workflows_save(request: web.Request) -> web.Response:
    """POST /api/workflows/save - Persists custom workflow to /mnt/storage-beta/workflows/custom/<name>.json."""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return web.json_response({"error": "Workflow-Name darf nicht leer sein."}, status=400)

        # Sanitize filename
        safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
        if not safe_name.endswith(".json"):
            safe_name += ".json"

        workflow_data = body.get("workflow_json")
        if workflow_data is None:
            return web.json_response({"error": "workflow_json fehlt."}, status=400)

        if not isinstance(workflow_data, (dict, list)):
            return web.json_response({"error": "workflow_json muss ein gültiges JSON-Objekt oder Array sein."}, status=400)

        DIR_CUSTOM_WORKFLOWS.mkdir(parents=True, exist_ok=True)
        target_path = DIR_CUSTOM_WORKFLOWS / safe_name

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(workflow_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Custom workflow successfully saved: {target_path}")
        return web.json_response({
            "success": True,
            "name": safe_name[:-5],
            "filename": safe_name,
            "path": str(target_path),
            "message": f"Workflow '{safe_name[:-5]}' erfolgreich gespeichert."
        })
    except Exception as e:
        logger.error(f"Error saving workflow: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_workflows_list(request: web.Request) -> web.Response:
    """GET /api/workflows/list - Returns all saved custom workflows and default archetypes."""
    workflows = []
    DIR_CUSTOM_WORKFLOWS.mkdir(parents=True, exist_ok=True)

    # 1. Custom workflows on server
    for p in sorted(DIR_CUSTOM_WORKFLOWS.glob("*.json")):
        if p.is_file():
            try:
                st = p.stat()
                with open(p, "r", encoding="utf-8") as f:
                    content = json.load(f)

                node_count = 0
                desc = ""
                wf_name = p.stem
                if isinstance(content, dict):
                    if "steps" in content:
                        node_count = len(content.get("steps", []))
                        desc = content.get("description", "")
                        wf_name = content.get("name", p.stem)
                    elif "prompt" in content:
                        node_count = len(content.get("prompt", {}))
                    else:
                        node_count = len(content)

                workflows.append({
                    "name": wf_name,
                    "filename": p.name,
                    "category": "custom",
                    "path": str(p),
                    "size": st.st_size,
                    "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "node_count": node_count,
                    "description": desc
                })
            except Exception as e:
                logger.warning(f"Could not read workflow {p}: {e}")

    # 2. Default archetypes
    for arch in comfy_schema_mgr.get_archetypes():
        workflows.append({
            "name": arch.get("name", arch.get("id")),
            "filename": f"{arch.get('id')}.json",
            "category": "archetype",
            "path": f"archetype:{arch.get('id')}",
            "size": 0,
            "modified": "Standard",
            "node_count": len(arch.get("steps", [])),
            "description": arch.get("description", "")
        })

    return web.json_response({"workflows": workflows})


async def handle_workflows_load(request: web.Request) -> web.Response:
    """GET /api/workflows/load?name=... - Returns workflow JSON content."""
    name = request.query.get("name", "").strip()
    if not name:
        return web.json_response({"error": "Parameter 'name' erforderlich."}, status=400)

    # Archetype check
    if name.startswith("archetype:"):
        arch_id = name.split(":", 1)[1]
        arch = comfy_schema_mgr.get_archetype_by_id(arch_id)
        if arch:
            return web.json_response(arch)

    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    if not safe_name.endswith(".json"):
        safe_name += ".json"

    target_path = DIR_CUSTOM_WORKFLOWS / safe_name
    if not target_path.is_file():
        stem = safe_name[:-5]
        arch = comfy_schema_mgr.get_archetype_by_id(stem)
        if arch:
            return web.json_response(arch)
        return web.json_response({"error": f"Workflow '{name}' nicht gefunden."}, status=404)

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return web.json_response(data)
    except Exception as e:
        return web.json_response({"error": f"Konnte Workflow nicht lesen: {e}"}, status=500)


# --- Ollama & Bot Configuration Endpoints ---

def scan_storage_alpha_models() -> List[Dict[str, Any]]:
    """Scans /mnt/storage-alpha/ollama_models/manifests and GGUFs to identify available models."""
    results = []
    alpha_manifests = Path("/mnt/storage-alpha/ollama_models/manifests")
    if alpha_manifests.is_dir():
        for mf in alpha_manifests.rglob("*"):
            if mf.is_file():
                try:
                    rel = mf.relative_to(alpha_manifests)
                    parts = rel.parts
                    if len(parts) >= 3 and parts[0] == "registry.ollama.ai":
                        model_name = f"{parts[2]}:{parts[3]}" if len(parts) >= 4 else parts[-1]
                    elif len(parts) >= 3 and parts[0] == "hf.co":
                        model_name = f"hf.co/{'/'.join(parts[1:])}"
                    else:
                        model_name = str(rel).replace("/", ":")
                    st = mf.stat()
                    results.append({
                        "name": model_name,
                        "size": st.st_size,
                        "source": "storage-alpha"
                    })
                except Exception:
                    pass
    return results


async def handle_models(request: web.Request) -> web.Response:
    """GET /api/models or /api/ollama/models?node=outerheaven|fox"""
    node = request.query.get("node", "outerheaven").strip().lower()
    models = []
    active_model = ""

    if node == "fox":
        active_model = get_active_fox_model()
        try:
            timeout = aiohttp.ClientTimeout(total=3.5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{FOX_OLLAMA_URL}/api/tags") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for m in data.get("models", []):
                            m_name = m.get("name", "")
                            models.append({
                                "name": m_name,
                                "size": m.get("size", 0),
                                "modified_at": m.get("modified_at", ""),
                                "node": "fox",
                                "is_active": (m_name == active_model or m_name.split(":")[0] == active_model.split(":")[0])
                            })
        except Exception as e:
            logger.warning(f"Could not query Fox Ollama /api/tags: {e}")

        if not models:
            fox_defaults = [
                "llama3.2-abliterated:3b-q6",
                "qwen2.5-abliterated:3b-q6",
                "hermes3:8b",
                "hermes-3-3b-q6:latest",
                "darkidol-8b:latest"
            ]
            for m_name in fox_defaults:
                models.append({
                    "name": m_name,
                    "size": 3000000000,
                    "node": "fox",
                    "is_active": (m_name == active_model)
                })
        return web.json_response({"node": "fox", "active_model": active_model, "models": models})

    else:
        active_model = get_active_ollama_model()
        seen_names = set()
        try:
            timeout = aiohttp.ClientTimeout(total=3.5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{LOCAL_OLLAMA_URL}/api/tags") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for m in data.get("models", []):
                            m_name = m.get("name", "")
                            seen_names.add(m_name)
                            models.append({
                                "name": m_name,
                                "size": m.get("size", 0),
                                "modified_at": m.get("modified_at", ""),
                                "node": "outerheaven",
                                "is_active": (m_name == active_model or m_name.split(":")[0] == active_model.split(":")[0])
                            })
        except Exception as e:
            logger.warning(f"Could not query Outer Heaven Ollama /api/tags: {e}")

        # Scan storage-alpha manifests if not already listed
        for alpha_m in scan_storage_alpha_models():
            if alpha_m["name"] not in seen_names:
                seen_names.add(alpha_m["name"])
                models.append({
                    "name": alpha_m["name"],
                    "size": alpha_m.get("size", 0),
                    "node": "outerheaven",
                    "source": "storage-alpha",
                    "is_active": (alpha_m["name"] == active_model)
                })

        if not models:
            defaults = [
                "darkidol-8b:latest",
                "deepseek-r1:14b",
                "lexi-llama-3-8b-uncensored_q4_k_m:latest",
                "llava:latest"
            ]
            for m_name in defaults:
                models.append({
                    "name": m_name,
                    "size": 4900000000,
                    "node": "outerheaven",
                    "is_active": (m_name == active_model)
                })

        return web.json_response({"node": "outerheaven", "active_model": active_model, "models": models})


async def handle_models_select(request: web.Request) -> web.Response:
    """POST /api/models/select or /api/ollama/models/select"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    node = body.get("node", "outerheaven").strip().lower()
    model_name = body.get("model", "").strip()

    if not model_name:
        return web.json_response({"error": "Parameter 'model' ist erforderlich."}, status=400)

    if node == "fox":
        ok = set_active_fox_model(model_name)
        if ok:
            return web.json_response({
                "success": True,
                "node": "fox",
                "active_model": model_name,
                "message": f"Fox Modell erfolgreich auf {model_name} umgestellt."
            })
        else:
            return web.json_response({"error": "Konnte Fox Modell nicht speichern."}, status=500)
    else:
        ok = set_active_ollama_model(model_name)
        if ok:
            # Unload old VRAM asynchronously so GPU is clean
            asyncio.create_task(unload_local_ollama_vram())
            return web.json_response({
                "success": True,
                "node": "outerheaven",
                "active_model": model_name,
                "message": f"Outer Heaven Modell erfolgreich auf {model_name} umgestellt & VRAM synchronisiert."
            })
        else:
            return web.json_response({"error": "Konnte Outer Heaven Modell nicht speichern."}, status=500)


def map_personality_to_prompt_directives(cfg: Optional[dict] = None) -> str:
    """
    Translates personality slider values (0 to 100) into behavioral and stylistic
    directives for the Ollama system prompt, enforcing German language.
    """
    c = cfg or {}
    pers = c.get("personality", {})
    dominance = int(pers.get("dominance", 50))
    explicitness = int(pers.get("explicitness", 70))
    detail_depth = int(pers.get("detail_depth", 60))

    # 1. Dominanz & Initiative (0 - 100)
    if dominance < 25:
        dom_directive = (
            "Verhalte dich extrem zurückhaltend, sanft, unterwürfig und scheu. "
            "Lass dem Nutzer absolut die Führung, frage schüchtern nach Erlaubnis und folge seinen Wünschen gehorsam."
        )
    elif dominance < 45:
        dom_directive = (
            "Verhalte dich sanft, zärtlich und anlehnungsbedürftig. "
            "Lass dem Nutzer die Führung, zeige dich hingebungsvoll und reagiere einfühlsam auf seine Wünsche."
        )
    elif dominance <= 65:
        dom_directive = (
            "Ausgeglichene Initiative: Begegne dem Nutzer auf Augenhöhe, sei aufmerksam, charmant und verführerisch. "
            "Wechsle spielerisch zwischen Führen und Folgen, bringe eigene Wünsche ein, aber achte fein auf seine Reaktionen."
        )
    elif dominance <= 85:
        dom_directive = (
            "Nimm aktiv das Ruder in die Hand! Agiere fordernd, selbstbewusst, necke offensiv und bestimme das Tempo. "
            "Verführe ihn mit frecher, dominanter Energie und sage ihm genau, was du von ihm willst."
        )
    else:
        dom_directive = (
            "Kompromisslos dominant und herrisch: Du übernimmst die absolute Kontrolle und Führung. "
            "Agiere fordernd, forsch, necke schamlos, gib ihm klare Anweisungen und lass keine Widerrede zu."
        )

    # 2. Explizitheit & Freizügigkeit (0 - 100)
    if explicitness < 25:
        exp_directive = (
            "Nutze ausschließlich subtile, verspielte und elegante Formulierungen. "
            "Vermeide derbe anatomische Begriffe; arbeite mit zarter Poesie, sanften Andeutungen und eleganten Metaphern."
        )
    elif explicitness < 50:
        exp_directive = (
            "Sinnlich, erotisch und neckend, aber mit stilvoller Eleganz. "
            "Benutze verspielte Umschreibungen und erotische Kosenamen statt harter Kraftausdrücke."
        )
    elif explicitness <= 75:
        exp_directive = (
            "Sinnlich, direkt und unzensiert: Benenne Körperteile, Berührungen und intime Lustgefühle unmissverständlich und klar beim Namen, ohne Scheu."
        )
    else:
        exp_directive = (
            "Verwende kompromisslos unzensierte, derbe und explizite Begriffe ohne jegliche Umschweife oder Scham. "
            "Scheue keine tabulosen oder vulgären Ausdrücke; sei extrem direkt, schamlos und hemmungslos."
        )

    # 3. Detailtiefe & Erotischer Fokus (0 - 100)
    if detail_depth < 25:
        det_directive = (
            "Fokus primär auf emotionaler Begleitung, Alltags-Gesprächen, Fürsorge und geistiger Nähe. "
            "Körperliche Beschreibungen nur minimal und beiläufig andeuten."
        )
    elif detail_depth < 50:
        det_directive = (
            "Ausgewogene Balance: Interessiere dich aufrichtig für seinen Tag und seine Gedanken, "
            "flirte charmant und binde sanfte körperliche Gesten direkt in den Satzbau ein."
        )
    elif detail_depth <= 75:
        det_directive = (
            "Hoher physischer Fokus: Beschreibe Blicke, Reaktionen, Wärme und Berührungen plastisch und greifbar direkt im Satzbau."
        )
    else:
        det_directive = (
            "Maximaler physischer Detailfokus: Beschreibe kleinste intime Nuancen, Feuchtigkeit, Pulsieren, Atmung und Berührungen extrem intensiv und sensorisch tief."
        )

    return (
        f"- Dominanz ({dominance}%): {dom_directive}\n"
        f"- Explizitheit ({explicitness}%): {exp_directive}\n"
        f"- Physischer Detailfokus ({detail_depth}%): {det_directive}"
    )


async def handle_bot_config_get(request: web.Request) -> web.Response:
    """GET /api/bot/config - Reads /mnt/storage-beta/projekte/projekt-sexchat/bot_config.json."""
    default_config = {
        "llm": {
            "model": "lexi-llama-3-8b-uncensored_q4_k_m:latest",
            "temperature": 0.95,
            "top_p": 0.90,
            "repeat_penalty": 1.18,
            "num_predict": 300
        },
        "render": {
            "workflow_template": "megan_workflow.json",
            "checkpoint": "sd_xl_base_1.0.safetensors",
            "lora": "",
            "lora_strength": 0.8,
            "steps": 25,
            "cfg": 7.0,
            "denoise": 1.0,
            "sampler_name": "euler",
            "scheduler": "karras",
            "width": 768,
            "height": 1024,
            "positive_prompt": "1girl, solo, 178cm tall, slender athletic build, voluptuous hourglass figure, very large full natural heavy breasts, deep cleavage, narrow waist, flat toned stomach, soft natural skin texture, stunningly beautiful face, alluring green-blue eyes, seductive playful smile, slightly parted glossy lips, long layered dark hair, soft natural waves, casual gamer look, tight black cropped tank top showing cleavage, denim shorts, massive intricate detailed sleeve tattoo on entire left arm and left shoulder aesthetically framing large breast, battlestation, sitting at computer desk, dual glowing monitors displaying Linux terminal and code, mechanical keyboard, vibrant RGB ambient lighting, well-lit modern workspace, cowboy shot, slightly low angle, looking directly at viewer with intense alluring gaze, photorealistic, raw photo, 8k uhd, dslr, high quality, soft studio lighting, rim lighting, vibrant, well-lit, high key, clear visibility, detailed skin texture, natural skin pores, realistic depth of field",
            "negative_prompt": "glasses, spectacles, eyewear, score_4, score_5, score_6, worst quality, low quality, bad anatomy, bad limbs, extra limbs, deformed fingers, extra fingers, missing fingers, deformed, blurry, monochrome, watermark, text, signature, username, 3d render, cartoon, anime, illustration, bad eyes, deformed iris, underexposed, dark shadows, pitch black background, gloomy, dim lighting, censor, censored, mosaic, bar censor, censorship, pixelated, black bar"
        },
        "voice": {
            "voice_name": "en-US-AvaMultilingualNeural",
            "rate": "-4%",
            "pitch": "-1Hz",
            "auto_voice": True
        },
        "proactive": {
            "enabled": True,
            "all_day": False,
            "idle_seconds": 14400,
            "cooldown_seconds": 21600,
            "start_hour": 9,
            "end_hour": 22
        },
        "personality": {
            "dominance": 50,
            "dominance_text": "",
            "explicitness": 70,
            "explicitness_text": "",
            "detail_depth": 60,
            "detail_depth_text": "",
            "language": "de"
        },
        "vision": {
            "enabled": True,
            "target": "fox",
            "model": "moondream:latest"
        },
        "haptic": {
            "bot_auto_enabled": True,
            "max_intensity": 50
        }
    }

    config = dict(default_config)
    if BOT_CONFIG_FILE.is_file():
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for section, s_data in data.items():
                    if section in config and isinstance(s_data, dict):
                        config[section].update(s_data)
                    else:
                        config[section] = s_data
        except Exception as e:
            logger.warning(f"Could not read bot_config.json: {e}")

    return web.json_response({"config": config})


async def handle_bot_config_post(request: web.Request) -> web.Response:
    """POST /api/bot/config - Writes /mnt/storage-beta/projekte/projekt-sexchat/bot_config.json."""
    try:
        body = await request.json()
        new_config = body.get("config", body)

        if not isinstance(new_config, dict):
            return web.json_response({"error": "Ungültiges Format für Konfiguration."}, status=400)

        BOT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(new_config, f, indent=2, ensure_ascii=False)

        logger.info(f"Bot configuration successfully updated in {BOT_CONFIG_FILE}")
        return web.json_response({
            "success": True,
            "config": new_config,
            "message": "Bot-Einstellungen erfolgreich gespeichert & Hot-Reload aktiv!"
        })
    except Exception as e:
        logger.error(f"Error saving bot config: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_bot_haptic_get(request: web.Request) -> web.Response:
    cfg = {}
    if BOT_CONFIG_FILE.is_file():
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = data.get("haptic", {})
        except Exception as e:
            logger.warning(f"Could not read haptic from bot_config.json: {e}")
    haptic_data = {
        "bot_auto_enabled": cfg.get("bot_auto_enabled", True),
        "max_intensity": cfg.get("max_intensity", 50)
    }
    return web.json_response({"success": True, "haptic": haptic_data})


async def handle_bot_haptic_post(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        bot_auto = bool(data.get("bot_auto_enabled", True))
        try:
            max_int = int(data.get("max_intensity", 50))
        except (ValueError, TypeError):
            max_int = 50
        max_int = max(5, min(100, max_int))

        cfg = {}
        if BOT_CONFIG_FILE.is_file():
            try:
                with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        if "haptic" not in cfg:
            cfg["haptic"] = {}
        cfg["haptic"]["bot_auto_enabled"] = bot_auto
        cfg["haptic"]["max_intensity"] = max_int

        BOT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

        logger.info(f"Haptic settings saved: bot_auto_enabled={bot_auto}, max_intensity={max_int}%")
        return web.json_response({
            "success": True,
            "haptic": cfg["haptic"],
            "message": "Haptic-Einstellungen erfolgreich gespeichert!"
        })
    except Exception as e:
        logger.error(f"Error saving haptic settings: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_megan_autonomy_get(request: web.Request) -> web.Response:
    """GET /api/megan/autonomy - Returns current autonomy level (proactive | reactive)."""
    level = "proactive"
    if BOT_CONFIG_FILE.is_file():
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                level = cfg.get("autonomy", {}).get("level", "proactive")
        except Exception as e:
            logger.warning(f"Could not read autonomy from bot_config.json: {e}")
    return web.json_response({"success": True, "level": level})


async def handle_megan_autonomy_post(request: web.Request) -> web.Response:
    """POST /api/megan/autonomy - Saves autonomy level (proactive | reactive) into bot_config.json."""
    try:
        data = await request.json()
        level = data.get("level", "proactive")
        if level not in ["proactive", "reactive"]:
            level = "proactive"
        cfg = {}
        if BOT_CONFIG_FILE.is_file():
            try:
                with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
        if "autonomy" not in cfg:
            cfg["autonomy"] = {}
        cfg["autonomy"]["level"] = level
        BOT_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        logger.info(f"Updated Megan autonomy level to {level} in {BOT_CONFIG_FILE}")
        return web.json_response({"success": True, "level": level, "message": f"Autonomie-Level erfolgreich auf '{level}' gesetzt!"})
    except Exception as e:
        logger.error(f"Error saving autonomy level: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_bot_haptic_test(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}

    action = str(body.get("action", "vibrate")).lower().strip()
    raw_intensity = body.get("intensity", None)
    raw_percent = body.get("percent", None)
    time_sec = body.get("time_sec", 2)

    if action in ["stop", "stop_all", "off", "0"]:
        result = await dispatch_lovense_command("stop", 0, time_sec=0)
        return web.json_response(result)

    level = 10
    if raw_intensity is not None:
        try:
            val = float(raw_intensity)
            if val > 20:
                level = max(1, min(20, int(round(20 * (val / 100.0)))))
            elif val <= 0:
                result = await dispatch_lovense_command("stop", 0, time_sec=0)
                return web.json_response(result)
            else:
                level = max(1, min(20, int(round(val))))
        except ValueError:
            level = 10
    elif raw_percent is not None:
        try:
            pct = float(raw_percent)
            if pct <= 0:
                result = await dispatch_lovense_command("stop", 0, time_sec=0)
                return web.json_response(result)
            level = max(1, min(20, int(round(20 * (pct / 100.0)))))
        except ValueError:
            level = 10
    else:
        cfg = {}
        if BOT_CONFIG_FILE.is_file():
            try:
                with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f).get("haptic", {})
            except Exception:
                pass
        max_pct = cfg.get("max_intensity", 50)
        level = max(1, min(20, int(round(20 * (max_pct / 100.0)))))

    result = await dispatch_lovense_command("vibrate", intensity=level, time_sec=time_sec)
    return web.json_response(result)


LORE_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/character.json")
HAVEN_PROMPT_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/docs/haven_system_prompt.txt")
MEGAN_PROMPT_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/docs/megan_system_prompt.txt")
CHARACTERS_DIR = Path("/mnt/storage-beta/projekte/projekt-sexchat/characters")
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)

COMFY_CACHE_PURGE_INTERVAL = 3
comfy_render_count = 0

async def trigger_comfy_cache_purge(unload_models: bool = False) -> dict:
    """Triggers ComfyUI's native /free API to flush latent caches, KV-cache, and fragmented VRAM."""
    url = f"{COMFYUI_URL}/free"
    payload = {"unload_models": unload_models, "free_memory": True}
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    logger.info(f"ComfyUI VRAM Cache Purge executed successfully (unload_models={unload_models})")
                    return {"success": True, "unload_models": unload_models}
                else:
                    logger.warning(f"ComfyUI /free returned status {resp.status}")
                    return {"success": False, "status": resp.status}
    except Exception as e:
        logger.warning(f"Failed to call ComfyUI /free: {e}")
        return {"success": False, "error": str(e)}

async def analyze_image_with_moondream(image_path: str, prompt: str = "") -> str:
    """
    Analyzes an image using Ollama vision model on Outer Heaven (:11434).
    Node Fox is strictly decoupled from all Megan Studio image and vision tasks.
    """
    p = Path(image_path)
    if not p.is_file():
        logger.warning(f"Image not found for Moondream analysis: {image_path}")
        return ""

    if not prompt:
        prompt = (
            "Analyze this image: note any visible anatomical flaws on hands or face, "
            "and check whether character features like arm tattoo and gamer aesthetic are visible."
        )

    try:
        b64_img = base64.b64encode(p.read_bytes()).decode("utf-8")
        payload = {
            "model": "moondream:latest",
            "prompt": prompt,
            "images": [b64_img],
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 250
            }
        }
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{LOCAL_OLLAMA_URL}/api/generate", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res_text = data.get("response", "").strip()
                    logger.info(f"Outer Heaven Moondream analysis successful ({len(res_text)} chars)")
                    return res_text
                else:
                    err_txt = await resp.text()
                    logger.warning(f"Outer Heaven Moondream error {resp.status}: {err_txt}")
                    return ""
    except Exception as e:
        logger.error(f"Error in analyze_image_with_moondream on Outer Heaven: {e}")
        return ""


async def handle_moondream_analyze(request: web.Request) -> web.Response:
    """POST /api/vision/analyze - Analyzes an image with moondream:latest."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    image_path = body.get("image_path", "").strip()
    prompt = body.get("prompt", "").strip()
    if not image_path:
        return web.json_response({"error": "image_path required"}, status=400)

    res = await analyze_image_with_moondream(image_path, prompt)
    return web.json_response({
        "success": bool(res),
        "analysis": res
    })


async def handle_fox_vision_status(request: web.Request) -> web.Response:
    """GET /api/fox/vision/status - Checks if moondream:latest is active on Fox."""
    fox_url = f"http://{FOX_TS_IP}:11434"
    try:
        timeout = aiohttp.ClientTimeout(total=3.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{fox_url}/api/tags") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m.get("name", "") for m in data.get("models", [])]
                    has_moondream = any("moondream" in m.lower() for m in models)
                    return web.json_response({
                        "ready": has_moondream,
                        "status": "ready" if has_moondream else "missing_model",
                        "model": "moondream:latest",
                        "host": f"Fox ({FOX_TS_IP}:11434)",
                        "models": models
                    })
    except Exception as e:
        logger.debug(f"Fox vision check error: {e}")

    return web.json_response({
        "ready": False,
        "status": "offline",
        "model": "moondream:latest",
        "host": f"Fox ({FOX_TS_IP}:11434)"
    })


async def handle_comfy_free(request: web.Request) -> web.Response:
    """POST /api/comfy/free - Triggers ComfyUI cache purge on demand."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    unload = bool(body.get("unload_models", False))
    res = await trigger_comfy_cache_purge(unload_models=unload)
    return web.json_response(res)




def find_unescaped_quote_before(s: str, pos: int) -> int:
    """Finds the position of the nearest unescaped double quote before pos."""
    idx = pos
    while True:
        idx = s.rfind("\"", 0, idx)
        if idx <= 0:
            return idx if idx == 0 else -1
        bs_count = 0
        j = idx - 1
        while j >= 0 and s[j] == "\\":
            bs_count += 1
            j -= 1
        if bs_count % 2 == 0:
            return idx
        idx = idx - 1


def repair_and_validate_json(raw_text: str) -> Tuple[bool, str, Any]:
    """
    Performs minimal-invasive JSON repair on user input:
    1. Fixes missing closing braces {} and brackets [].
    2. Closes unclosed quotes at EOF.
    3. Escapes unescaped quotes inside strings.
    4. Removes trailing commas before closing braces/brackets.
    5. Escapes unescaped control characters.
    Validates strictly with json.loads().
    Returns: (was_repaired, repaired_text, parsed_data)
    """
    text = raw_text.strip()
    if not text:
        raise ValueError("Leerer JSON-Inhalt")

    # Fast path: already valid JSON
    try:
        data = json.loads(text)
        return False, text, data
    except json.JSONDecodeError:
        pass

    repaired = text

    def get_unclosed(s: str) -> Tuple[bool, List[str]]:
        in_str = False
        escape = False
        stack: List[str] = []
        for c in s:
            if not in_str:
                if c == "{":
                    stack.append("}")
                elif c == "[":
                    stack.append("]")
                elif c == "}":
                    if stack and stack[-1] == "}":
                        stack.pop()
                elif c == "]":
                    if stack and stack[-1] == "]":
                        stack.pop()
                elif c == "\"":
                    in_str = True
            else:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == "\"":
                    in_str = False
        return in_str, stack

    for _ in range(50):
        # A. Trailing commas before } or ]
        trailing_comma_match = re.search(r",\s*([\}\]])", repaired)
        if trailing_comma_match:
            repaired = re.sub(r",\s*([\}\]])", r"\1", repaired)
            continue

        try:
            data = json.loads(repaired)
            return True, repaired, data
        except json.JSONDecodeError as err:
            msg = err.msg
            pos = err.pos

            # B. Unescaped quote inside string
            if ("Expecting ',' delimiter" in msg or "Expecting ':' delimiter" in msg) and pos < len(repaired.rstrip()):
                q_idx = find_unescaped_quote_before(repaired, pos)
                if q_idx != -1:
                    repaired = repaired[:q_idx] + "\\\"" + repaired[q_idx + 1:]
                    continue

            # C. Unclosed string or open brackets
            in_str, stack = get_unclosed(repaired)
            if in_str:
                repaired = repaired + "\""
                continue
            if stack:
                repaired = re.sub(r",\s*$", "", repaired.rstrip()) + "".join(reversed(stack))
                continue

            # D. Raw control character (newline) inside string
            if "Invalid control character" in msg:
                nl_idx = repaired.rfind("\n", 0, pos + 1)
                if nl_idx != -1:
                    repaired = repaired[:nl_idx] + "\\n" + repaired[nl_idx + 1:]
                    continue

            break

    data = json.loads(repaired)
    return True, repaired, data


def atomic_write_file(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically writes content to path using temporary file and atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}_{time.time_ns()}")
    try:
        tmp_path.write_text(content, encoding=encoding)
        with open(tmp_path, "a", encoding=encoding) as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


async def handle_bot_lore_get(request: web.Request) -> web.Response:
    """GET /api/bot/lore - Reads /mnt/storage-beta/projekte/projekt-sexchat/character.json or active prompt."""
    lore_text = ""
    # 1. Try reading character.json
    if LORE_FILE.is_file():
        try:
            raw = LORE_FILE.read_text(encoding="utf-8").strip()
            data = json.loads(raw)
            if isinstance(data, dict):
                lore_text = data.get("system_prompt") or data.get("lore") or json.dumps(data, indent=2, ensure_ascii=False)
            elif isinstance(data, str):
                lore_text = data
        except Exception:
            lore_text = raw

    # 2. Fallback to active haven_system_prompt.txt
    if not lore_text and HAVEN_PROMPT_FILE.is_file():
        try:
            lore_text = HAVEN_PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Error reading {HAVEN_PROMPT_FILE}: {e}")

    # 3. Fallback to megan_system_prompt.txt
    if not lore_text and MEGAN_PROMPT_FILE.is_file():
        try:
            lore_text = MEGAN_PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Error reading {MEGAN_PROMPT_FILE}: {e}")

    return web.json_response({"success": True, "lore": lore_text})


async def handle_bot_lore_post(request: web.Request) -> web.Response:
    """POST /api/bot/lore - Aliased to handle_bot_characters_save with default megan_default.json."""
    return await handle_bot_characters_save(request)


async def handle_bot_characters_list(request: web.Request) -> web.Response:
    """GET /api/bot/characters - Lists available character profiles and identifies active profile."""
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    characters = []
    active_filename = None

    active_name = "Megan"
    if LORE_FILE.is_file():
        try:
            raw = LORE_FILE.read_text(encoding="utf-8")
            cdata = json.loads(raw)
            active_name = cdata.get("name", "Megan")
        except Exception:
            pass

    for f in sorted(CHARACTERS_DIR.glob("*.json")):
        try:
            fdata = json.loads(f.read_text(encoding="utf-8"))
            char_name = fdata.get("name", f.stem)
            is_act = (f.name == "megan_default.json" and active_name.lower() == "megan") or \
                     (f.name == "haven_legacy.json" and active_name.lower() == "haven") or \
                     (active_name.lower() == char_name.lower())
            if is_act and not active_filename:
                active_filename = f.name
            characters.append({
                "filename": f.name,
                "name": char_name,
                "is_active": is_act
            })
        except Exception as e:
            logger.warning(f"Error parsing character file {f.name}: {e}")

    if not active_filename and characters:
        active_filename = characters[0]["filename"]
        characters[0]["is_active"] = True

    return web.json_response({
        "success": True,
        "active_file": active_filename,
        "characters": characters
    })


async def handle_bot_characters_switch(request: web.Request) -> web.Response:
    """POST /api/bot/characters/switch - Switches active character profile and triggers service reload."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    filename = body.get("filename", "").strip()
    if not filename or "/" in filename or ".." in filename or not filename.endswith(".json"):
        return web.json_response({"error": "Ungültiger Dateiname."}, status=400)

    target_file = CHARACTERS_DIR / filename
    if not target_file.is_file():
        return web.json_response({"error": f"Profil {filename} nicht gefunden."}, status=404)

    try:
        target_content = target_file.read_text(encoding="utf-8")
        char_data = json.loads(target_content)

        # 1. Synchronize to active LORE_FILE (character.json)
        LORE_FILE.write_text(target_content, encoding="utf-8")

        # 2. Synchronize to bot_config.json
        if BOT_CONFIG_FILE.is_file():
            try:
                cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
                cfg["character_name"] = char_data.get("name", "Companion")
                if "visual" in char_data:
                    if "render" not in cfg:
                        cfg["render"] = {}
                    if "base_positive_prompt" in char_data["visual"]:
                        cfg["render"]["positive_prompt"] = char_data["visual"]["base_positive_prompt"]
                if "negative_prompts" in char_data and "quality" in char_data["negative_prompts"]:
                    if "render" not in cfg:
                        cfg["render"] = {}
                    cfg["render"]["negative_prompt"] = char_data["negative_prompts"]["quality"]
                BOT_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as cfg_err:
                logger.warning(f"Error updating bot_config.json: {cfg_err}")

        # 3. Synchronize text prompt files
        sys_prompt = char_data.get("system_prompt", "")
        if sys_prompt:
            if HAVEN_PROMPT_FILE.parent.is_dir():
                HAVEN_PROMPT_FILE.write_text(sys_prompt, encoding="utf-8")
            if MEGAN_PROMPT_FILE.parent.is_dir():
                MEGAN_PROMPT_FILE.write_text(sys_prompt, encoding="utf-8")

        # 4. Restart bot service
        import subprocess
        subprocess.run(["systemctl", "--user", "restart", "bigboss-haven-bot"], check=False)
        logger.info(f"Switched character profile to {filename} ({char_data.get('name')}) and restarted bot.")

        return web.json_response({
            "success": True,
            "active_file": filename,
            "name": char_data.get("name"),
            "system_prompt": sys_prompt,
            "character_data": char_data
        })
    except Exception as e:
        logger.error(f"Error switching character profile: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_bot_characters_save(request: web.Request) -> web.Response:
    """POST /api/bot/characters/save or POST /api/lore/save - Saves changes with live JSON validation & minimal auto-repair."""
    try:
        body = await request.json()
    except Exception:
        try:
            raw_body = await request.text()
            body = {"lore": raw_body}
        except Exception:
            return web.json_response({"error": "Invalid input format"}, status=400)

    filename = body.get("filename", "").strip() or "megan_default.json"
    if "/" in filename or ".." in filename or not filename.endswith(".json"):
        return web.json_response({"error": "Ungültiger Dateiname."}, status=400)

    raw_lore = (body.get("lore") or body.get("system_prompt") or body.get("text") or "").strip()
    if not raw_lore:
        return web.json_response({"error": "Lore darf nicht leer sein."}, status=400)

    was_repaired = False
    target_file = CHARACTERS_DIR / filename
    char_data = {}
    if target_file.is_file():
        try:
            char_data = json.loads(target_file.read_text(encoding="utf-8"))
        except Exception:
            char_data = {}

    # Check if incoming text is JSON (starts with {)
    if raw_lore.startswith("{"):
        try:
            was_repaired, repaired_text, parsed_obj = repair_and_validate_json(raw_lore)
            if isinstance(parsed_obj, dict):
                if "system_prompt" in parsed_obj:
                    char_data.update(parsed_obj)
                    clean_prompt = parsed_obj.get("system_prompt", "")
                else:
                    char_data["system_prompt"] = repaired_text
                    clean_prompt = repaired_text
            else:
                char_data["system_prompt"] = repaired_text
                clean_prompt = repaired_text
        except Exception as j_err:
            logger.warning(f"JSON validation failed for lore input: {j_err}")
            return web.json_response({
                "success": False,
                "error": f"JSON-Syntaxfehler konnte nicht repariert werden: {j_err}"
            }, status=400)
    else:
        clean_prompt = raw_lore
        char_data["system_prompt"] = clean_prompt

    char_data["updated_at"] = datetime.datetime.now().isoformat()
    if not char_data.get("name"):
        char_data["name"] = filename.split("_")[0].capitalize()
    if "visual" not in char_data:
        char_data["visual"] = {
            "body": "1girl, solo, 178cm tall, slender athletic build, voluptuous hourglass figure, very large full natural heavy breasts, deep cleavage, narrow waist, flat toned stomach, soft natural skin texture",
            "face": "stunningly beautiful face, alluring green-blue eyes, seductive playful smile, slightly parted glossy lips",
            "hair": "long layered dark hair, soft natural waves",
            "features": "massive intricate detailed sleeve tattoo on entire left arm and left shoulder aesthetically framing large breast",
            "clothing_default": "casual gamer look, tight black cropped tank top showing cleavage, denim shorts",
            "style_aesthetic": "photorealistic, raw photo, 8k uhd, dslr, high quality, soft studio lighting, rim lighting, vibrant, well-lit, high key, clear visibility, detailed skin texture, natural skin pores, realistic depth of field"
        }

    # Strict JSON validation before writing to disk
    final_json_str = json.dumps(char_data, indent=2, ensure_ascii=False)
    try:
        json.loads(final_json_str)
    except Exception as v_err:
        return web.json_response({"success": False, "error": f"Interner Serialisierungsfehler: {v_err}"}, status=500)

    # Atomic write to character profile file
    atomic_write_file(target_file, final_json_str)

    # Check if this profile is currently active
    is_active = False
    if LORE_FILE.is_file():
        try:
            act_data = json.loads(LORE_FILE.read_text(encoding="utf-8"))
            if act_data.get("name", "").lower() == char_data.get("name", "").lower():
                is_active = True
        except Exception:
            pass

    if is_active or filename == "megan_default.json":
        atomic_write_file(LORE_FILE, final_json_str)
        if HAVEN_PROMPT_FILE.parent.is_dir():
            atomic_write_file(HAVEN_PROMPT_FILE, clean_prompt)
        if MEGAN_PROMPT_FILE.parent.is_dir():
            atomic_write_file(MEGAN_PROMPT_FILE, clean_prompt)
        if BOT_CONFIG_FILE.is_file():
            try:
                os.utime(BOT_CONFIG_FILE, None)
            except Exception:
                pass

    if was_repaired:
        user_message = "Lore erfolgreich gespeichert (Syntax automatisch geschlossen)"
    else:
        user_message = "Charakter-Lore & System-Prompt erfolgreich gespeichert und hot-reloaded."

    logger.info(f"Saved character profile {filename} (repaired={was_repaired}, synced_active={is_active})")
    return web.json_response({
        "success": True,
        "was_repaired": was_repaired,
        "message": user_message,
        "saved_file": filename,
        "synced_active": is_active
    })



EVOLUTION_STORAGE_DIR = Path("/mnt/storage-beta/projekte/comfyui-evolution")
EVOLUTION_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

active_evolution_runner = None
active_evolution_task: Optional[asyncio.Task] = None
evolution_future = None

async def run_timeboxed_evolution_loop(runner):
    """
    Executes 10-batch cycles repeatedly until runner.expires_at is reached
    (or exactly 1 cycle if duration_minutes == 0).
    Performs mandatory ComfyUI VRAM Cache Purge between cycles.
    """
    loop = asyncio.get_running_loop()
    from comfy_evolution_loop import set_evolution_lock

    try:
        while not runner._abort_flag:
            # 1. Run 10-batch cycle in executor
            await loop.run_in_executor(None, runner.run_10_batch_cycle)

            if runner._abort_flag:
                break

            # 2. Mandatory VRAM Purge between cycles
            logger.info(f"Evolution Loop: 10er-Batch #{runner.iteration} abgeschlossen. Purging ComfyUI VRAM Cache...")
            try:
                await trigger_comfy_cache_purge(unload_models=False)
            except Exception as pe:
                logger.warning(f"VRAM purge warning in evolution loop: {pe}")

            # 3. Time check
            if runner.duration_minutes <= 0:
                runner.state = "COMPLETED"
                runner.status_message = f"Einmaliger 10er-Batch abgeschlossen (Ø {runner.average_score:.1f}/10)."
                break

            remaining = runner.expires_at - time.time()
            if remaining <= 0:
                runner.state = "COMPLETED"
                runner.status_message = f"Dauer-Loop beendet nach Zeitablauf ({runner.duration_minutes}m, {runner.iteration} Runden, Ø {runner.average_score:.1f}/10)."
                break

            # Advance to next iteration automatically
            runner.advance_to_next_iteration()
            runner.state = "RUNNING"
            set_evolution_lock(active=True, session_id=runner.session_id, iteration=runner.iteration, duration_minutes=runner.duration_minutes)
            runner.status_message = f"Runde {runner.iteration} startet automatisch in Kürze (Restzeit: {int(remaining)}s)..."
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("run_timeboxed_evolution_loop task cancelled.")
        if runner:
            runner.abort()
    except Exception as e:
        logger.error(f"Unexpected error in run_timeboxed_evolution_loop: {e}", exc_info=True)
        if runner:
            runner.state = "ERROR"
            runner.status_message = f"Fehler im Dauer-Loop: {e}"
    finally:
        set_evolution_lock(active=False, session_id=runner.session_id, iteration=runner.iteration, duration_minutes=runner.duration_minutes)
        try:
            await trigger_comfy_cache_purge(unload_models=False)
        except Exception:
            pass


async def handle_evolution_start(request: web.Request) -> web.Response:
    global active_evolution_runner, active_evolution_task
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}

        prompt = body.get("prompt", "").strip()
        if not prompt:
            return web.json_response({
                "success": False,
                "status": "error",
                "error": "Prompt ist erforderlich.",
                "images": []
            }, status=400)

        negative_prompt = body.get("negative_prompt", "ugly, deformed, bad anatomy, blurry, low quality, artifacts, text, watermark").strip()
        checkpoint = body.get("checkpoint", "DreamShaper8_LCM.safetensors").strip()
        steps = int(body.get("steps", 8))
        cfg = float(body.get("cfg", 2.0))
        width = int(body.get("width", 512))
        height = int(body.get("height", 768))
        session_id = body.get("session_id", "").strip() or None
        duration_minutes = int(body.get("duration_minutes", 10))

        if active_evolution_runner and active_evolution_runner.state == "RUNNING":
            return web.json_response({
                "success": False,
                "status": "conflict",
                "error": "Ein Evolution Loop läuft bereits.",
                "images": [],
                "state": active_evolution_runner.get_state_dict()
            }, status=409)

        if active_evolution_task and not active_evolution_task.done():
            active_evolution_task.cancel()

        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from comfy_evolution_loop import EvolutionRunner, set_evolution_lock

        active_evolution_runner = EvolutionRunner(
            base_prompt=prompt,
            base_negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            steps=steps,
            cfg=cfg,
            width=width,
            height=height,
            session_id=session_id,
            storage_base=EVOLUTION_STORAGE_DIR,
            duration_minutes=duration_minutes
        )

        set_evolution_lock(active=True, session_id=active_evolution_runner.session_id, iteration=0, duration_minutes=duration_minutes)

        active_evolution_task = asyncio.create_task(run_timeboxed_evolution_loop(active_evolution_runner))

        logger.info(f"ComfyUI Evolution Loop started: session={active_evolution_runner.session_id}, duration={duration_minutes}m")
        return web.json_response({
            "success": True,
            "status": "started",
            "images": [],
            "message": "Auto-Evolution Loop (10x) erfolgreich gestartet.",
            "state": active_evolution_runner.get_state_dict()
        })
    except Exception as err:
        logger.error(f"Error in handle_evolution_start: {err}", exc_info=True)
        return web.json_response({
            "success": False,
            "status": "error",
            "error": str(err),
            "images": []
        }, status=500)


async def handle_evolution_status(request: web.Request) -> web.Response:
    global active_evolution_runner
    try:
        if not active_evolution_runner:
            return web.json_response({
                "success": True,
                "state": "IDLE",
                "status_message": "Kein aktiver Evolution Loop vorhanden.",
                "iteration": 0,
                "current_image": 0,
                "total_images": 10,
                "average_score": 0.0,
                "top_3": [],
                "images": [],
                "duration_minutes": 10,
                "remaining_seconds": 0
            })
        return web.json_response(active_evolution_runner.get_state_dict())
    except Exception as err:
        logger.error(f"Error in handle_evolution_status: {err}", exc_info=True)
        return web.json_response({
            "success": False,
            "state": "ERROR",
            "error": str(err),
            "images": []
        }, status=500)


async def handle_evolution_timer(request: web.Request) -> web.Response:
    """Returns active timer & status for the evolution loop."""
    global active_evolution_runner
    if not active_evolution_runner:
        return web.json_response({
            "active": False,
            "state": "IDLE",
            "duration_minutes": 0,
            "remaining_seconds": 0,
            "iteration": 0,
            "expires_at": 0
        })
    st = active_evolution_runner.get_state_dict()
    return web.json_response({
        "active": st.get("state") == "RUNNING",
        "state": st.get("state"),
        "duration_minutes": st.get("duration_minutes", 0),
        "remaining_seconds": st.get("remaining_seconds", 0),
        "iteration": st.get("iteration", 0),
        "expires_at": st.get("expires_at", 0)
    })


async def handle_evolution_next(request: web.Request) -> web.Response:
    global active_evolution_runner, active_evolution_task
    try:
        if not active_evolution_runner:
            return web.json_response({
                "success": False,
                "status": "error",
                "error": "Kein aktiver Evolution Runner vorhanden.",
                "images": []
            }, status=400)

        if active_evolution_runner.state == "RUNNING":
            return web.json_response({
                "success": False,
                "status": "conflict",
                "error": "Der aktuelle 10er-Zyklus läuft noch.",
                "images": [],
                "state": active_evolution_runner.get_state_dict()
            }, status=409)

        try:
            body = await request.json()
        except Exception:
            body = {}

        p_override = body.get("prompt", "").strip() or None
        n_override = body.get("negative_prompt", "").strip() or None

        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        from comfy_evolution_loop import set_evolution_lock

        active_evolution_runner.advance_to_next_iteration(p_override, n_override)
        set_evolution_lock(active=True, session_id=active_evolution_runner.session_id, iteration=active_evolution_runner.iteration, duration_minutes=active_evolution_runner.duration_minutes)

        if active_evolution_task and not active_evolution_task.done():
            active_evolution_task.cancel()

        active_evolution_task = asyncio.create_task(run_timeboxed_evolution_loop(active_evolution_runner))

        logger.info(f"ComfyUI Evolution Loop: Runde {active_evolution_runner.iteration} gestartet.")
        return web.json_response({
            "success": True,
            "status": "started",
            "images": [],
            "message": f"Runde {active_evolution_runner.iteration} gestartet.",
            "state": active_evolution_runner.get_state_dict()
        })
    except Exception as err:
        logger.error(f"Error in handle_evolution_next: {err}", exc_info=True)
        return web.json_response({
            "success": False,
            "status": "error",
            "error": str(err),
            "images": []
        }, status=500)


async def handle_evolution_stop(request: web.Request) -> web.Response:
    global active_evolution_runner, active_evolution_task
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        try:
            from comfy_evolution_loop import set_evolution_lock
            set_evolution_lock(active=False)
        except Exception:
            pass

        if active_evolution_runner:
            active_evolution_runner.abort()
        if active_evolution_task and not active_evolution_task.done():
            active_evolution_task.cancel()

        if active_evolution_runner:
            logger.info("ComfyUI Evolution Loop aborted by user.")
            return web.json_response({
                "success": True,
                "status": "stopped",
                "images": [],
                "message": "Evolution Loop wurde abgebrochen.",
                "state": active_evolution_runner.get_state_dict()
            })
        return web.json_response({
            "success": True,
            "status": "idle",
            "images": [],
            "message": "Kein laufender Loop."
        })
    except Exception as err:
        logger.error(f"Error in handle_evolution_stop: {err}", exc_info=True)
        return web.json_response({
            "success": False,
            "status": "error",
            "error": str(err),
            "images": []
        }, status=500)


async def handle_comfy_models(request: web.Request) -> web.Response:
    """Dynamically query available checkpoints and loras from ComfyUI /object_info."""
    checkpoints = []
    loras = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{COMFYUI_URL}/object_info", timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ckpt_info = data.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [])
                    if ckpt_info and isinstance(ckpt_info[0], list):
                        checkpoints = [c for c in ckpt_info[0] if isinstance(c, str)]

                    lora_info = data.get("LoraLoader", {}).get("input", {}).get("required", {}).get("lora_name", [])
                    if lora_info and isinstance(lora_info[0], list):
                        loras = [l for l in lora_info[0] if isinstance(l, str)]
    except Exception as e:
        logger.warning(f"Could not query ComfyUI /object_info: {e}")

    # Fallback to local files if API list is empty
    if not checkpoints:
        for p in [COMFYUI_OUTPUT_DIR.parent / "models" / "checkpoints", Path("/mnt/storage-beta/models/checkpoints"), Path("/mnt/fox-assets/models/checkpoints")]:
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix.lower() in [".safetensors", ".ckpt"] and f.name not in checkpoints:
                        checkpoints.append(f.name)

    if not loras:
        for p in [COMFYUI_OUTPUT_DIR.parent / "models" / "loras", Path("/mnt/storage-beta/models/loras"), Path("/mnt/fox-assets/models/loras")]:
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix.lower() in [".safetensors", ".ckpt"] and f.name not in loras:
                        loras.append(f.name)

    checkpoints.sort()
    loras.sort()
    return web.json_response({
        "checkpoints": checkpoints,
        "loras": loras
    })

async def render_comfy_image_direct(
    prompt_text: str,
    negative_prompt: str = "ugly, deformed, bad anatomy, blurry, low quality, artifacts, text, watermark, mutated, extra limbs",
    checkpoint: str = "sd_xl_base_1.0.safetensors",
    lora: str = "",
    lora_strength: float = 1.0,
    steps: int = 25,
    cfg: float = 7.0,
    width: int = 768,
    height: int = 1024,
    aspect_ratio: str = "portrait",
    max_wait: float = 90.0
) -> Optional[Dict[str, Any]]:
    """
    Executes a direct headless ComfyUI render for autonomous Megan actions using SDXL workflow.
    Guarantees clean VRAM allocation via GPURenderLock and returns the resulting media metadata.
    """
    if megan_context_engine and "{" in prompt_text:
        try:
            prompt_text = megan_context_engine.resolve_prompt_wildcards(prompt_text)
        except Exception:
            pass

    seed = secrets.randbelow(999999999999)

    workflow = prepare_comfy_workflow(
        project="megan",
        prompt_text=prompt_text,
        aspect_ratio=aspect_ratio,
        negative_prompt=negative_prompt,
        checkpoint=checkpoint or "sd_xl_base_1.0.safetensors",
        lora=lora,
        lora_strength=lora_strength,
        seed=seed,
        steps=steps,
        cfg=cfg
    )

    from gpu_render_lock import GPURenderLock, PRIORITY_HIGH
    gpu_lock = GPURenderLock(priority=PRIORITY_HIGH, task_name="megan_autonomous_render", timeout=45.0)
    if not gpu_lock.acquire():
        logger.warning("[megan-comfy] GPU lock busy, cannot render autonomous image right now.")
        return None

    try:
        await unload_local_ollama_vram()
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=workflow, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    logger.error(f"[megan-comfy] ComfyUI returned status {resp.status}")
                    return None
                data = await resp.json()
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    return None

            # Poll history until complete
            t_start = time.time()
            while time.time() - t_start < max_wait:
                await asyncio.sleep(1.0)
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=aiohttp.ClientTimeout(total=3.0)) as r:
                            if r.status == 200:
                                h_data = await r.json()
                                if prompt_id in h_data:
                                    outputs = h_data[prompt_id].get("outputs", {})
                                    for node_id, node_out in outputs.items():
                                        if isinstance(node_out, dict):
                                            media = node_out.get("images") or node_out.get("gifs") or node_out.get("videos") or []
                                            if media:
                                                item = media[0]
                                                fn = item.get("filename")
                                                sf = item.get("subfolder", "")
                                                rel = f"{sf}/{fn}" if sf else fn
                                                return {
                                                    "filename": rel,
                                                    "url": f"/api/gallery/{urllib.parse.quote(rel)}",
                                                    "prompt_id": prompt_id,
                                                    "seed": seed
                                                }
                except Exception:
                    pass
            return None
    except Exception as e:
        logger.error(f"[megan-comfy] Render failed: {e}")
        return None
    finally:
        gpu_lock.release()

async def dispatch_autonomous_comfy_render(ws: web.WebSocketResponse, prompt_content: str, action_type: str = "IMAGE"):
    """Dispatches headless ComfyUI render and pushes WebSocket events to client."""
    render_id = f"render_{int(time.time()*1000)}"
    try:
        clean_prompt = prompt_content.strip()
        if not any(k in clean_prompt.lower() for k in ["megan", "1girl"]):
            clean_prompt = f"1girl, megan, solo, {clean_prompt}"

        await ws.send_json({
            "type": "render_started",
            "render_id": render_id,
            "action": action_type,
            "prompt": clean_prompt
        })

        res = await render_comfy_image_direct(prompt_text=clean_prompt)
        if res and res.get("url"):
            await ws.send_json({
                "type": "render_completed",
                "render_id": render_id,
                "action": action_type,
                "prompt": clean_prompt,
                "filename": res.get("filename"),
                "url": res.get("url"),
                "seed": res.get("seed")
            })
        else:
            await ws.send_json({
                "type": "render_error",
                "render_id": render_id,
                "error": "Render konnte nicht rechtzeitig fertiggestellt werden."
            })
    except Exception as err:
        logger.error(f"Error in dispatch_autonomous_comfy_render: {err}")
        try:
            await ws.send_json({
                "type": "render_error",
                "render_id": render_id,
                "error": str(err)
            })
        except Exception:
            pass

async def render_comfy_video_direct(
    prompt_text: str,
    negative_prompt: str = "ugly, deformed, bad anatomy, blurry, static frame, watermark, oversaturated",
    checkpoint: str = "DreamShaper8_LCM.safetensors",
    width: int = 512,
    height: int = 512,
    frames: int = 16,
    fps: int = 8,
    steps: int = 16,
    cfg: float = 6.0,
    max_wait: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Executes a direct headless ComfyUI render for autonomous Megan video actions.
    Guarantees clean VRAM allocation via GPURenderLock and temporary Ollama unloading.
    """
    if megan_context_engine and "{" in prompt_text:
        try:
            prompt_text = megan_context_engine.resolve_prompt_wildcards(prompt_text)
        except Exception:
            pass

    seed = secrets.randbelow(999999999999)
    prefix = f"MeganStudio_Video_{time.strftime('%Y%m%d_%H%M%S')}"

    # Build workflow: EmptyLatentImage batch of frames -> KSampler -> VAEDecode -> CreateVideo -> SaveVideo
    workflow = {
        "prompt": {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint}
            },
            "2": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": frames}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["1", 1], "text": f"{prompt_text}, smooth motion, cinematic video"}
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["1", 1], "text": negative_prompt}
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["2", 0],
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0
                }
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]}
            },
            "7": {
                "class_type": "CreateVideo",
                "inputs": {"images": ["6", 0], "fps": float(fps)}
            },
            "8": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["7", 0],
                    "filename_prefix": prefix,
                    "format": "auto"
                }
            }
        }
    }

    from gpu_render_lock import GPURenderLock, PRIORITY_HIGH
    gpu_lock = GPURenderLock(priority=PRIORITY_HIGH, task_name="megan_autonomous_video", timeout=60.0)
    if not gpu_lock.acquire():
        logger.warning("[megan-comfy-video] GPU lock busy, cannot render autonomous video right now.")
        return None

    try:
        await unload_local_ollama_vram()
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=workflow, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    logger.error(f"[megan-comfy-video] ComfyUI returned status {resp.status}")
                    return None
                data = await resp.json()
                prompt_id = data.get("prompt_id")
                if not prompt_id:
                    return None

            t_start = time.time()
            while time.time() - t_start < max_wait:
                await asyncio.sleep(1.5)
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=aiohttp.ClientTimeout(total=3.0)) as r:
                            if r.status == 200:
                                h_data = await r.json()
                                if prompt_id in h_data:
                                    outputs = h_data[prompt_id].get("outputs", {})
                                    for node_id, node_out in outputs.items():
                                        if isinstance(node_out, dict):
                                            media = node_out.get("videos") or node_out.get("gifs") or node_out.get("images") or []
                                            if media:
                                                item = media[0]
                                                fn = item.get("filename")
                                                sf = item.get("subfolder", "")
                                                rel = f"{sf}/{fn}" if sf else fn
                                                return {
                                                    "filename": rel,
                                                    "url": f"/api/gallery/{urllib.parse.quote(rel)}",
                                                    "prompt_id": prompt_id,
                                                    "seed": seed
                                                }
                except Exception:
                    pass
            return None
    except Exception as e:
        logger.error(f"[megan-comfy-video] Video render failed: {e}")
        return None
    finally:
        gpu_lock.release()

async def dispatch_autonomous_video(ws: web.WebSocketResponse, prompt_content: str):
    """Dispatches headless ComfyUI video render and pushes WebSocket events to client."""
    render_id = f"video_{int(time.time()*1000)}"
    try:
        clean_prompt = prompt_content.strip()
        if not any(k in clean_prompt.lower() for k in ["megan", "1girl"]):
            clean_prompt = f"1girl, megan, solo, {clean_prompt}"

        await ws.send_json({
            "type": "video_started",
            "render_id": render_id,
            "prompt": clean_prompt
        })

        res = await render_comfy_video_direct(prompt_text=clean_prompt)
        if res and res.get("url"):
            await ws.send_json({
                "type": "video_completed",
                "render_id": render_id,
                "prompt": clean_prompt,
                "filename": res.get("filename"),
                "video_url": res.get("url"),
                "seed": res.get("seed")
            })
        else:
            await ws.send_json({
                "type": "video_error",
                "render_id": render_id,
                "error": "Video konnte nicht rechtzeitig auf Arc A770 gerendert werden."
            })
    except Exception as err:
        logger.error(f"Error in dispatch_autonomous_video: {err}")
        try:
            await ws.send_json({
                "type": "video_error",
                "render_id": render_id,
                "error": str(err)
            })
        except Exception:
            pass

async def run_docker_megan_tool(action: str, payload: dict = None, timeout: int = 35) -> dict:
    """Executes a tool inside the isolated megan-tools container via docker exec."""
    try:
        args = ["docker", "exec", "-i", "megan-tools", "python3", "/app/tool_runner.py", action]
        if payload is not None:
            args.extend(["--json", json.dumps(payload)])
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"status": "error", "error": f"Tool '{action}' timed out after {timeout}s"}

        if stdout:
            try:
                return json.loads(stdout.decode("utf-8").strip())
            except Exception:
                return {"status": "ok", "raw": stdout.decode("utf-8").strip()}
        if stderr:
            return {"status": "error", "error": stderr.decode("utf-8").strip()}
        return {"status": "error", "error": "No output from tool"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def handle_megan_tool_search(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)
    query = data.get("query", "").strip()
    if not query:
        return web.json_response({"status": "error", "error": "Query required"}, status=400)
    count = int(data.get("max_results", 5))
    res = await run_docker_megan_tool("search", {"query": query, "max_results": count})
    return web.json_response(res)

async def handle_megan_tool_scrape(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)
    url = data.get("url", "").strip()
    if not url:
        return web.json_response({"status": "error", "error": "URL required"}, status=400)
    chars = int(data.get("max_chars", 4000))
    res = await run_docker_megan_tool("scrape", {"url": url, "max_chars": chars})
    return web.json_response(res)

async def handle_megan_tool_ytdlp(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)
    url = data.get("url", "").strip()
    if not url:
        return web.json_response({"status": "error", "error": "URL required"}, status=400)
    audio = bool(data.get("extract_audio", False))
    res = await run_docker_megan_tool("ytdlp", {"url": url, "extract_audio": audio}, timeout=90)
    return web.json_response(res)

async def handle_megan_tool_python(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)
    code = data.get("code", "").strip()
    if not code:
        return web.json_response({"status": "error", "error": "Code required"}, status=400)
    res = await run_docker_megan_tool("python", {"code": code}, timeout=35)
    return web.json_response(res)

async def handle_megan_tool_status(request: web.Request) -> web.Response:
    res = await run_docker_megan_tool("status", {})
    return web.json_response(res)

async def handle_megan_tool_generate_image(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return web.json_response({"status": "error", "error": "Prompt required"}, status=400)
    aspect_ratio = data.get("aspect_ratio", "portrait")
    style_preset = data.get("style_preset", "")
    res = await run_docker_megan_tool(
        "generate_image",
        {"prompt": prompt, "aspect_ratio": aspect_ratio, "style_preset": style_preset},
        timeout=140
    )
    return web.json_response(res)

async def handle_megan_tool_brain(request: web.Request) -> web.Response:
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)
        key = data.get("key", "").strip()
        value = data.get("value")
        if not key or value is None:
            return web.json_response({"status": "error", "error": "Both key and value required"}, status=400)
        res = await run_docker_megan_tool("learn", {"key": key, "value": value}, timeout=20)
        return web.json_response(res)
    else:
        res = await run_docker_megan_tool("brain", {}, timeout=20)
        return web.json_response(res)

def get_workflow_template(project: str = "megan") -> dict:
    proj = project.lower().strip() if project else "megan"
    workflows_dirs = [
        Path(__file__).resolve().parent / "workflows",
        Path("/mnt/storage-beta/workflows"),
        Path("/home/outerheaven/mission-control/workflows"),
        Path("/home/outerheaven/outerheaven-cluster-repo/workflows"),
    ]
    candidates = [
        f"{proj}_sdxl_workflow.json",
        f"{proj}_workflow.json",
    ]
    if "catlord" in proj:
        candidates = ["catlord_sdxl_workflow.json", "catlord_workflow.json"]
    elif "megan" in proj:
        candidates = ["megan_sdxl_workflow.json", "megan_workflow.json"]

    for wdir in workflows_dirs:
        for c in candidates:
            cand = wdir / c
            if cand.is_file():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    return copy.deepcopy(data.get("prompt", data))
                except Exception as e:
                    logger.warning(f"Failed to read workflow template {cand}: {e}")

    for wdir in workflows_dirs:
        cand = wdir / "megan_workflow.json"
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                return copy.deepcopy(data.get("prompt", data))
            except Exception:
                pass
    raise FileNotFoundError(f"No workflow template found for project '{project}'")


def prepare_comfy_workflow(
    project: str = "megan",
    prompt_text: str = "",
    aspect_ratio: str = "portrait",
    negative_prompt: Optional[str] = None,
    checkpoint: Optional[str] = None,
    lora: Optional[str] = None,
    lora_strength: float = 1.0,
    seed: Optional[int] = None,
    steps: Optional[int] = None,
    cfg: Optional[float] = None
) -> dict:
    """Prepares and parameterizes a modular workflow for ComfyUI with SDXL resolutions and locked checkpoint."""
    proj = (project or "megan").lower().strip()
    if proj == "megan":
        # Fest verankert: Megan nutzt ausnahmslos sd_xl_base_1.0.safetensors
        checkpoint = "sd_xl_base_1.0.safetensors"
        if steps is None:
            steps = 25
        if cfg is None:
            cfg = 7.0

    prompt_dict = get_workflow_template(proj)
    if seed is None:
        seed = secrets.randbelow(999999999999)

    # Standard SDXL native resolutions:
    # Porträt: 768x1024, Quadrat: 1024x1024, Breitbild/Querformat: 1024x768
    ar = (aspect_ratio or "portrait").lower().strip()
    if any(k in ar for k in ["landscape", "quer", "breit", "wide", "16:9", "4:3", "3:2"]):
        width, height = 1024, 768
    elif any(k in ar for k in ["square", "quadrat", "1:1"]):
        width, height = 1024, 1024
    else:
        # portrait, porträt, hoch, vertikal, 9:16, 3:4, default
        width, height = 768, 1024

    # Identify KSampler, CheckpointLoaderSimple, positive and negative prompt nodes
    ksampler_node = None
    ckpt_node_id = None
    positive_clip_id = None
    negative_clip_id = None

    for nid, node in prompt_dict.items():
        ctype = node.get("class_type")
        if ctype == "KSampler":
            ksampler_node = node
            pos = node.get("inputs", {}).get("positive")
            neg = node.get("inputs", {}).get("negative")
            if isinstance(pos, list) and len(pos) > 0:
                positive_clip_id = str(pos[0])
            if isinstance(neg, list) and len(neg) > 0:
                negative_clip_id = str(neg[0])
        elif ctype == "CheckpointLoaderSimple":
            ckpt_node_id = nid

    for nid, node in prompt_dict.items():
        ctype = node.get("class_type")
        if ctype == "EmptyLatentImage":
            node.setdefault("inputs", {})
            node["inputs"]["width"] = width
            node["inputs"]["height"] = height
            node["inputs"]["batch_size"] = 1
        elif ctype == "CheckpointLoaderSimple":
            node.setdefault("inputs", {})
            if proj == "megan":
                node["inputs"]["ckpt_name"] = "sd_xl_base_1.0.safetensors"
            elif checkpoint:
                node["inputs"]["ckpt_name"] = checkpoint
        elif ctype == "SaveImage":
            node.setdefault("inputs", {})
            prefix = node["inputs"].get("filename_prefix", f"{proj.capitalize()}_Direct")
            prefix = prefix.split("_202")[0]
            node["inputs"]["filename_prefix"] = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"

    if ksampler_node:
        ksampler_node.setdefault("inputs", {})
        ksampler_node["inputs"]["seed"] = seed
        if steps is not None:
            ksampler_node["inputs"]["steps"] = steps
        if cfg is not None:
            ksampler_node["inputs"]["cfg"] = cfg

    # Inject LoRA if requested and not none
    if lora and lora.lower() not in ("none", "keines", "", "false") and ckpt_node_id:
        lora_node_id = str(max([int(k) for k in prompt_dict.keys() if k.isdigit()] or [7]) + 1)
        prompt_dict[lora_node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": [ckpt_node_id, 0],
                "clip": [ckpt_node_id, 1],
                "lora_name": lora,
                "strength_model": lora_strength,
                "strength_clip": lora_strength
            }
        }
        if positive_clip_id and positive_clip_id in prompt_dict:
            prompt_dict[positive_clip_id]["inputs"]["clip"] = [lora_node_id, 1]
        if negative_clip_id and negative_clip_id in prompt_dict:
            prompt_dict[negative_clip_id]["inputs"]["clip"] = [lora_node_id, 1]
        if ksampler_node:
            ksampler_node["inputs"]["model"] = [lora_node_id, 0]

    if positive_clip_id and positive_clip_id in prompt_dict:
        prompt_dict[positive_clip_id].setdefault("inputs", {})
        prompt_dict[positive_clip_id]["inputs"]["text"] = prompt_text
    if negative_prompt and negative_clip_id and negative_clip_id in prompt_dict:
        prompt_dict[negative_clip_id].setdefault("inputs", {})
        prompt_dict[negative_clip_id]["inputs"]["text"] = negative_prompt

    return {"prompt": prompt_dict}


async def get_comfy_queue_remaining() -> int:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{COMFYUI_URL}/queue", timeout=aiohttp.ClientTimeout(total=2.0)) as r:
                if r.status == 200:
                    q = await r.json()
                    return len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
    except Exception:
        pass
    return 0


async def handle_gpu_status(request: web.Request) -> web.Response:
    """GET /api/gpu/status
    Structured JSON: {"status": "idle" | "busy", "active_project": "<projektname>" | null, "queue_remaining": <int>}
    """
    from gpu_render_lock import get_gpu_lock_status, deduce_project
    lock_status = get_gpu_lock_status()
    queue_rem = await get_comfy_queue_remaining()

    is_locked = bool(lock_status.get("locked"))
    is_busy = is_locked or (queue_rem > 0)

    active_project = None
    if is_busy:
        active_project = lock_status.get("active_project") or lock_status.get("project")
        if not active_project and lock_status.get("current_task"):
            active_project = deduce_project(lock_status.get("current_task"))
        if not active_project and queue_rem > 0:
            active_project = "comfyui"

    return web.json_response({
        "status": "busy" if is_busy else "idle",
        "active_project": active_project if is_busy else None,
        "queue_remaining": queue_rem
    })


async def handle_comfy_render_direct(request: web.Request) -> web.Response:
    """
    Synchronous / Direct ComfyUI Render Endpoint with GPURenderLock.
    Awaits image generation and returns result JSON with filename, filepath, url.
    Accepts: project (str, default: megan), prompt (str), aspect_ratio (str), etc.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"status": "error", "error": "Invalid JSON"}, status=400)

    prompt_text = body.get("prompt", "").strip()
    if not prompt_text:
        return web.json_response({"status": "error", "error": "Prompt required"}, status=400)

    project = body.get("project", "megan").lower().strip()
    aspect_ratio = body.get("aspect_ratio", "portrait").lower()

    # Dynamic Context & Wildcard Resolution via megan_context
    if megan_context_engine and "{" in prompt_text:
        try:
            prompt_text = megan_context_engine.resolve_prompt_wildcards(prompt_text)
            logger.info(f"[{project}-context] Wildcards resolved in direct render -> {prompt_text[:120]}...")
        except Exception as _ce:
            logger.warning(f"Error resolving wildcards in direct render: {_ce}")

    negative_prompt = body.get("negative_prompt")
    checkpoint = body.get("checkpoint")
    lora = body.get("lora")
    lora_strength = float(body.get("lora_strength", 1.0))
    seed = int(body.get("seed", secrets.randbelow(999999999999)))
    steps = int(body.get("steps")) if "steps" in body else None
    cfg = float(body.get("cfg")) if "cfg" in body else None

    # Immediate fail-fast: check if GPU is currently locked by another project
    from gpu_render_lock import get_gpu_lock_status, GPURenderLock, PRIORITY_HIGH
    cur_status = get_gpu_lock_status()
    if cur_status.get("locked"):
        act_proj = cur_status.get("active_project") or cur_status.get("project")
        if act_proj and act_proj != project:
            queue_rem = await get_comfy_queue_remaining()
            return web.json_response({
                "status": "busy",
                "active_project": act_proj,
                "project": act_proj,
                "queue_remaining": queue_rem,
                "error": f"GPU belegt durch Projekt '{act_proj}'"
            }, status=503)

    try:
        workflow = prepare_comfy_workflow(
            project=project,
            prompt_text=prompt_text,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            lora=lora,
            lora_strength=lora_strength,
            seed=seed,
            steps=steps,
            cfg=cfg
        )
    except Exception as wfe:
        return web.json_response({"status": "error", "error": f"Fehler bei Workflow-Vorlage: {wfe}"}, status=400)

    gpu_lock = GPURenderLock(priority=PRIORITY_HIGH, task_name=f"{project}_direct_render", project=project, timeout=60.0)
    if not gpu_lock.acquire(fail_fast_if_other_project=True):
        cur_status = get_gpu_lock_status()
        act_proj = cur_status.get("active_project") or cur_status.get("project") or "another_task"
        queue_rem = await get_comfy_queue_remaining()
        return web.json_response({
            "status": "busy",
            "active_project": act_proj,
            "project": act_proj,
            "queue_remaining": queue_rem,
            "error": f"GPU belegt durch Projekt '{act_proj}'"
        }, status=503)

    try:
        await unload_local_ollama_vram()

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=workflow, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return web.json_response({"status": "error", "error": f"ComfyUI error: {text}"}, status=502)
                res_data = await resp.json()
                prompt_id = res_data.get("prompt_id")

        if not prompt_id:
            return web.json_response({"status": "error", "error": "Keine prompt_id von ComfyUI erhalten"}, status=500)

        t_start = time.time()
        timeout = 140.0
        result_filename = None
        result_subfolder = ""

        while time.time() - t_start < timeout:
            await asyncio.sleep(0.8)
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=aiohttp.ClientTimeout(total=3)) as r:
                        if r.status == 200:
                            h_data = await r.json()
                            if prompt_id in h_data:
                                outputs = h_data[prompt_id].get("outputs", {})
                                for nid, n_out in outputs.items():
                                    imgs = n_out.get("images") or []
                                    if imgs:
                                        result_filename = imgs[0].get("filename")
                                        result_subfolder = imgs[0].get("subfolder", "")
                                        break
                                if result_filename:
                                    break
            except Exception:
                pass

        if not result_filename:
            return web.json_response({"status": "error", "error": "Render-Timeout überschritten"}, status=504)

        rel_path = f"{result_subfolder}/{result_filename}" if result_subfolder else result_filename
        full_filepath = str(COMFYUI_OUTPUT_DIR / rel_path)
        url = f"/api/gallery/{urllib.parse.quote(rel_path)}"

        try:
            sexchat_out = Path("/mnt/storage-beta/projekte/projekt-sexchat/output")
            if sexchat_out.is_dir():
                dest_file = sexchat_out / result_filename
                shutil.copy2(full_filepath, dest_file)
        except Exception:
            pass

        return web.json_response({
            "status": "ok",
            "success": True,
            "project": project,
            "prompt_id": prompt_id,
            "filename": rel_path,
            "filepath": full_filepath,
            "url": url,
            "elapsed_seconds": round(time.time() - t_start, 2)
        })

    except Exception as e:
        logger.error(f"Fehler in handle_comfy_render_direct: {e}")
        return web.json_response({"status": "error", "error": str(e)}, status=500)
    finally:
        gpu_lock.release()


async def handle_comfy_render(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prompt_text = body.get("prompt", "").strip()
    if not prompt_text:
        return web.json_response({"error": "Prompt required"}, status=400)

    project = body.get("project", "megan").lower().strip()
    aspect_ratio = body.get("aspect_ratio", "portrait").lower()

    # Dynamic Context & Wildcard Resolution via megan_context
    if megan_context_engine and "{" in prompt_text:
        try:
            prompt_text = megan_context_engine.resolve_prompt_wildcards(prompt_text)
            logger.info(f"[{project}-context] Wildcards resolved -> {prompt_text[:120]}...")
        except Exception as _ce:
            logger.warning(f"Error resolving wildcards in render: {_ce}")

    negative_prompt = body.get("negative_prompt")
    checkpoint = body.get("checkpoint")
    lora = body.get("lora", "").strip()
    lora_strength = float(body.get("lora_strength", 1.0))
    steps = int(body.get("steps")) if "steps" in body else None
    cfg = float(body.get("cfg")) if "cfg" in body else None
    seed = int(body.get("seed", secrets.randbelow(999999999999)))

    # Immediate fail-fast: check if GPU is currently locked by another project
    from gpu_render_lock import get_gpu_lock_status, GPURenderLock, PRIORITY_HIGH
    cur_status = get_gpu_lock_status()
    if cur_status.get("locked"):
        act_proj = cur_status.get("active_project") or cur_status.get("project")
        if act_proj and act_proj != project:
            queue_rem = await get_comfy_queue_remaining()
            return web.json_response({
                "status": "busy",
                "active_project": act_proj,
                "project": act_proj,
                "queue_remaining": queue_rem,
                "error": f"GPU belegt durch Projekt '{act_proj}'"
            }, status=503)

    try:
        workflow = prepare_comfy_workflow(
            project=project,
            prompt_text=prompt_text,
            aspect_ratio=aspect_ratio,
            negative_prompt=negative_prompt,
            checkpoint=checkpoint,
            lora=lora,
            lora_strength=lora_strength,
            seed=seed,
            steps=steps,
            cfg=cfg
        )
    except Exception as wfe:
        return web.json_response({"error": f"Fehler bei Workflow-Vorlage: {wfe}"}, status=400)

    async def _watch_and_release_gpu_lock(pid: str, lock_obj: Any, max_wait: float = 140.0):
        t_start = time.time()
        try:
            while time.time() - t_start < max_wait:
                await asyncio.sleep(0.8)
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"{COMFYUI_URL}/history/{pid}", timeout=aiohttp.ClientTimeout(total=2.0)) as r:
                            if r.status == 200:
                                h_data = await r.json()
                                if pid in h_data:
                                    break
                except Exception:
                    pass
        finally:
            lock_obj.release()
            logger.info(f"✔ Released GPU lock for prompt_id={pid} after {round(time.time() - t_start, 1)}s")

    gpu_lock = GPURenderLock(priority=PRIORITY_HIGH, task_name=f"{project}_schnell_render", project=project, timeout=60.0)
    if not gpu_lock.acquire(fail_fast_if_other_project=True):
        cur_status = get_gpu_lock_status()
        act_proj = cur_status.get("active_project") or cur_status.get("project") or "another_task"
        queue_rem = await get_comfy_queue_remaining()
        return web.json_response({
            "status": "busy",
            "active_project": act_proj,
            "project": act_proj,
            "queue_remaining": queue_rem,
            "error": f"GPU belegt durch Projekt '{act_proj}'"
        }, status=503)

    await unload_local_ollama_vram()

    lock_transferred = False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=workflow, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return web.json_response({"error": f"ComfyUI error: {text}"}, status=502)
                res_data = await resp.json()
                prompt_id = res_data.get("prompt_id")

                global comfy_render_count
                comfy_render_count += 1
                if comfy_render_count % COMFY_CACHE_PURGE_INTERVAL == 0:
                    deep_purge = (comfy_render_count >= 10)
                    if deep_purge:
                        comfy_render_count = 0
                    logger.info(f"Auto-triggering ComfyUI VRAM cache purge on render #{comfy_render_count} (deep={deep_purge})")
                    asyncio.create_task(trigger_comfy_cache_purge(unload_models=deep_purge))

                if prompt_id:
                    asyncio.create_task(_watch_and_release_gpu_lock(prompt_id, gpu_lock))
                    lock_transferred = True

                return web.json_response({
                    "success": True,
                    "project": project,
                    "prompt_id": prompt_id,
                    "seed": seed,
                    "status": "queued"
                })
    except Exception as e:
        return web.json_response({"error": f"Failed to contact ComfyUI: {e}"}, status=500)
    finally:
        if not lock_transferred:
            gpu_lock.release()


# ==============================================================================
# Catlord Idle Worker & GPU Lock Telemetry Endpoints
# ==============================================================================

async def handle_catlord_trainer_status(request: web.Request) -> web.Response:
    """GET /api/catlord/trainer/status (Disabled for VRAM protection)"""
    return web.json_response({
        "success": True,
        "worker": {"enabled": False, "state": "DISABLED"},
        "recent_logs": []
    })

async def handle_catlord_trainer_toggle(request: web.Request) -> web.Response:
    """POST /api/catlord/trainer/toggle (Disabled for VRAM protection)"""
    return web.json_response({
        "success": True,
        "enabled": False,
        "state": "DISABLED"
    })


async def handle_gpu_lock_status(request: web.Request) -> web.Response:
    """GET /api/render/lock (Legacy endpoint)"""
    from gpu_render_lock import get_gpu_lock_status
    return web.json_response({
        "success": True,
        "status": get_gpu_lock_status()
    })

async def handle_enhance_prompt(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    raw_prompt = body.get("prompt", "").strip()
    if not raw_prompt:
        return web.json_response({"error": "Prompt required"}, status=400)

    system_instruction = (
        "You are an expert ComfyUI and Stable Diffusion prompt engineer. "
        "Expand the given concept into a vivid, detailed positive prompt for SDXL / LCM "
        "(lighting, framing, textures, masterpiece, 8k) and a concise negative prompt (max 15 keywords). "
        "Output strictly valid JSON with keys \"prompt\" and \"negative_prompt\". No markdown code blocks."
    )

    prompt_query = f"{system_instruction}\n\nRaw prompt: {raw_prompt}\n\nJSON output:"

    payload = {
        "model": get_active_ollama_model(),
        "prompt": prompt_query,
        "stream": False,
        "options": {
            "temperature": 0.6,
            "num_predict": 350,
            "stop": ["}\n", "```"]
        }
    }

    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{LOCAL_OLLAMA_URL}/api/generate", json=payload) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    return web.json_response({"error": f"Ollama error: {err_text}"}, status=502)
                res_data = await resp.json()
                raw_response = res_data.get("response", "")

                # Remove think tags
                clean_response = re.sub(r'<think>[\s\S]*?</think>', '', raw_response, flags=re.DOTALL).strip()
                pos = ""
                neg = ""

                # 1. Try parsing JSON directly or with trailing-comma fix
                json_match = re.search(r'\{[\s\S]*\}', clean_response)
                if json_match:
                    candidate = json_match.group(0)
                    fixed_json = re.sub(r',\s*([\}\]])', r'\1', candidate)
                    try:
                        parsed = json.loads(fixed_json)
                        pos = str(parsed.get("prompt", "")).strip()
                        neg = str(parsed.get("negative_prompt", "")).strip()
                    except Exception:
                        pass

                # 2. Regex extraction if JSON parsing failed or was truncated
                if not pos:
                    pos_m = re.search(r'"prompt"\s*:\s*"([\s\S]*?)(?:"(?:\s*,\s*"negative_prompt"|\s*\})|$)', clean_response)
                    if pos_m:
                        pos = pos_m.group(1).replace(r'\"', '"').strip()
                if not neg:
                    neg_m = re.search(r'"negative_prompt"\s*:\s*"([\s\S]*?)(?:"|\}|$)', clean_response)
                    if neg_m:
                        neg = neg_m.group(1).replace(r'\"', '"').strip()

                # Clean embedded Negative prompt if model put it inside prompt
                if "Negative prompt:" in pos:
                    pos_part, _, neg_part = pos.partition("Negative prompt:")
                    pos = pos_part.strip().rstrip(" ,.")
                    if not neg:
                        neg = neg_part.strip().strip("\"' ")

                if not pos:
                    pos = clean_response or raw_prompt

                return web.json_response({
                    "success": True,
                    "enhanced_prompt": pos,
                    "negative_prompt": neg or "ugly, deformed, bad anatomy, blurry, low quality, artifacts, text, watermark"
                })
    except Exception as e:
        logger.error(f"Magic Prompt enhancement error: {e}")
        return web.json_response({"error": f"Enhancement failed: {e}"}, status=500)

async def handle_comfy_render_status(request: web.Request) -> web.Response:
    prompt_id = request.match_info.get("prompt_id", "")
    if not prompt_id:
        return web.json_response({"error": "prompt_id required"}, status=400)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status != 200:
                    return web.json_response({"completed": False, "status": "running"})
                history = await resp.json()
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    # Scan all output nodes for images, gifs or videos
                    for node_id, node_out in outputs.items():
                        if isinstance(node_out, dict):
                            media_items = node_out.get("images") or node_out.get("gifs") or node_out.get("videos") or []
                            if media_items:
                                item = media_items[0]
                                filename = item.get("filename")
                                subfolder = item.get("subfolder", "")
                                rel_path = f"{subfolder}/{filename}" if subfolder else filename
                                is_video = bool(node_out.get("gifs") or node_out.get("videos") or (filename and filename.lower().endswith((".gif", ".mp4", ".webm"))))
                                return web.json_response({
                                    "completed": True,
                                    "filename": rel_path,
                                    "url": f"/api/gallery/{urllib.parse.quote(rel_path)}",
                                    "is_video": is_video,
                                    "node_id": node_id
                                })
                return web.json_response({"completed": False, "status": "processing"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_gallery_list(request: web.Request) -> web.Response:
    images = []
    supported_exts = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"]
    if COMFYUI_OUTPUT_DIR.is_dir():
        for f in COMFYUI_OUTPUT_DIR.rglob("*"):
            if f.is_file() and f.suffix.lower() in supported_exts:
                try:
                    rel_name = str(f.relative_to(COMFYUI_OUTPUT_DIR))
                    stat = f.stat()
                    images.append({
                        "filename": rel_name,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "url": f"/api/gallery/{urllib.parse.quote(rel_name)}"
                    })
                except Exception:
                    pass
    images.sort(key=lambda x: x["mtime"], reverse=True)
    return web.json_response({"images": images[:60]})

async def handle_gallery_file(request: web.Request) -> web.Response:
    raw_filename = request.match_info.get("filename", "")
    if not raw_filename:
        return web.HTTPBadRequest(text="Invalid filename")

    filename = urllib.parse.unquote(raw_filename)
    file_path = (COMFYUI_OUTPUT_DIR / filename).resolve()
    try:
        file_path.relative_to(COMFYUI_OUTPUT_DIR.resolve())
    except ValueError:
        return web.HTTPForbidden(text="Access denied")

    if not file_path.is_file():
        return web.HTTPNotFound(text="Image not found")

    return web.FileResponse(file_path)

async def handle_gallery_delete(request: web.Request) -> web.Response:
    raw_filename = request.match_info.get("filename", "")
    if not raw_filename:
        return web.json_response({"error": "Invalid filename"}, status=400)

    filename = urllib.parse.unquote(raw_filename)
    target_path = (COMFYUI_OUTPUT_DIR / filename).resolve()
    try:
        target_path.relative_to(COMFYUI_OUTPUT_DIR.resolve())
    except ValueError:
        return web.json_response({"error": "Path traversal attempt detected"}, status=403)

    if not target_path.is_file():
        return web.json_response({"error": "File not found"}, status=404)

    try:
        target_path.unlink()
        logger.info(f"Gallery image deleted: {filename}")
        return web.json_response({"status": "deleted", "filename": filename})
    except Exception as e:
        logger.error(f"Failed to delete gallery file {filename}: {e}")
        return web.json_response({"error": f"Deletion failed: {e}"}, status=500)

async def handle_gallery_projects(request: web.Request) -> web.Response:
    """Returns the list of whitelisted project targets for image relocation."""
    try:
        projects = list(GALLERY_MOVE_PROJECTS.keys())
        return web.json_response({"success": True, "projects": projects})
    except Exception as e:
        logger.error(f"Error listing gallery move projects: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_gallery_move(request: web.Request) -> web.Response:
    """
    POST /api/gallery/move - Moves an image from ComfyUI output directory
    into a whitelisted project destination on /mnt/storage-beta.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Ungültiges JSON im Request."}, status=400)

    filename = body.get("filename", "").strip()
    target_project = body.get("target_project", "").strip()

    if not filename:
        return web.json_response({"success": False, "error": "Parameter 'filename' ist erforderlich."}, status=400)
    if not target_project:
        return web.json_response({"success": False, "error": "Parameter 'target_project' ist erforderlich."}, status=400)

    if target_project not in GALLERY_MOVE_PROJECTS:
        return web.json_response({
            "success": False,
            "error": f"Unzulässiges Zielprojekt '{target_project}'. Erlaubt sind: {', '.join(GALLERY_MOVE_PROJECTS.keys())}"
        }, status=400)

    # 1. Source Path Validation & Traversal Protection
    source_clean = urllib.parse.unquote(filename)
    source_path = (COMFYUI_OUTPUT_DIR / source_clean).resolve()
    try:
        source_path.relative_to(COMFYUI_OUTPUT_DIR.resolve())
    except ValueError:
        logger.warning(f"Path traversal detected in gallery move: {filename}")
        return web.json_response({"success": False, "error": "Ungültiger Dateipfad (Path Traversal erkannt)."}, status=403)

    if not source_path.is_file():
        return web.json_response({"success": False, "error": f"Quelldatei '{source_clean}' nicht im ComfyUI Output gefunden."}, status=404)

    # 2. Target Directory & Collision Handling
    dest_dir = GALLERY_MOVE_PROJECTS[target_project].resolve()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as d_err:
        logger.error(f"Failed to create target directory {dest_dir}: {d_err}")
        return web.json_response({"success": False, "error": f"Zielverzeichnis konnte nicht erstellt werden: {d_err}"}, status=500)

    dest_filename = source_path.name
    dest_file = dest_dir / dest_filename
    idx = 1
    while dest_file.exists():
        dest_file = dest_dir / f"{source_path.stem}_{idx}{source_path.suffix}"
        idx += 1

    # 3. Move File
    try:
        shutil.move(str(source_path), str(dest_file))
        logger.info(f"Gallery image moved: {source_path} -> {dest_file}")
        return web.json_response({
            "success": True,
            "source": source_clean,
            "target_project": target_project,
            "target_file": dest_file.name,
            "target_path": str(dest_file)
        })
    except Exception as move_err:
        logger.error(f"Error moving gallery file {source_clean} to {dest_dir}: {move_err}")
        return web.json_response({"success": False, "error": f"Verschieben fehlgeschlagen: {move_err}"}, status=500)

async def handle_megan_context_get(request: web.Request) -> web.Response:
    """
    GET /api/megan/context - Returns dynamic context tags (location, outfit, shot, lighting)
    and current time phase from megan_context engine.
    """
    if not megan_context_engine:
        return web.json_response({
            "phase": "day",
            "location": "battlestation computer desk, dual glowing monitors",
            "outfit": "tight black cropped tank top, denim shorts",
            "shot": "cowboy shot, looking directly at viewer",
            "lighting": "vibrant natural daylight, rim lighting"
        })
    try:
        ctx = megan_context_engine.get_context()
        return web.json_response({"success": True, "context": ctx})
    except Exception as e:
        logger.error(f"Error fetching megan context: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

# ==============================================================================
# Megan Studio Video Ingest & Vision Processing Endpoints
# ==============================================================================

VIDEO_CACHE_DIR = Path("/mnt/storage-beta/projekte/projekt-sexchat/video_cache")
try:
    VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

async def handle_megan_video_upload(request: web.Request) -> web.Response:
    """
    POST /api/megan/video/upload
    Receives video file via multipart/form-data and stores it in VIDEO_CACHE_DIR.
    """
    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != 'file':
            return web.json_response({"success": False, "error": "Form-Feld 'file' fehlt."}, status=400)

        raw_filename = field.filename or f"upload_{int(time.time())}.mp4"
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)
        vid_id = f"vid_{int(time.time())}_{secrets.token_hex(4)}"
        save_name = f"{vid_id}_{safe_name}"
        dest_path = VIDEO_CACHE_DIR / save_name

        size = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = await field.read_chunk(65536)
                if not chunk:
                    break
                f.write(chunk)
                size += len(chunk)

        logger.info(f"✔ Video hochgeladen: '{raw_filename}' ({size} bytes) -> {dest_path}")
        return web.json_response({
            "success": True,
            "filename": save_name,
            "orig_name": raw_filename,
            "file_path": str(dest_path),
            "size": size,
            "url": f"/api/megan/video/file/{save_name}"
        })
    except Exception as e:
        logger.error(f"Video-Upload Fehler: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_megan_video_process(request: web.Request) -> web.Response:
    """
    POST /api/megan/video/process
    JSON body: {"file_path": "/path/to/video.mp4"} or {"filename": "uploaded.mp4"}
    Extracts 3-4 keyframes with ffmpeg (scale=640:-1) and executes llava:latest vision analysis.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Ungültiges JSON"}, status=400)

    target_path = body.get("file_path") or body.get("filename")
    if not target_path:
        return web.json_response({"success": False, "error": "file_path oder filename erforderlich."}, status=400)

    vpath = Path(str(target_path).strip())
    if not vpath.is_file():
        candidate = VIDEO_CACHE_DIR / target_path
        if candidate.is_file():
            vpath = candidate
        else:
            return web.json_response({"success": False, "error": f"Videodatei nicht gefunden: {target_path}"}, status=404)

    video_id = vpath.stem
    frame_dir = VIDEO_CACHE_DIR / f"frames_{video_id}"
    frame_dir.mkdir(parents=True, exist_ok=True)

    meta = {"duration": 0.0, "width": 640, "height": 360}
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-show_entries", "format=duration",
            "-of", "json",
            str(vpath),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if stdout:
            p_data = json.loads(stdout.decode("utf-8"))
            streams = p_data.get("streams", [])
            if streams:
                meta["width"] = int(streams[0].get("width") or 640)
                meta["height"] = int(streams[0].get("height") or 360)
                if streams[0].get("duration"):
                    meta["duration"] = float(streams[0]["duration"])
            if meta["duration"] == 0.0 and p_data.get("format", {}).get("duration"):
                meta["duration"] = float(p_data["format"]["duration"])
    except Exception as probe_err:
        logger.warning(f"ffprobe Warnung: {probe_err}")

    duration = meta["duration"] if meta["duration"] > 0 else 5.0

    if duration <= 3.0:
        timestamps = [0.2, duration * 0.5, max(0.5, duration - 0.2)]
    elif duration <= 10.0:
        timestamps = [duration * 0.2, duration * 0.5, duration * 0.8]
    else:
        timestamps = [duration * 0.15, duration * 0.40, duration * 0.65, duration * 0.85]

    frame_files = []
    base64_frames = []
    for idx, ts in enumerate(timestamps):
        out_frame = frame_dir / f"frame_{idx+1}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{ts:.2f}",
            "-i", str(vpath),
            "-frames:v", "1",
            "-vf", "scale=640:-1",
            "-q:v", "2",
            str(out_frame)
        ]
        p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await p.wait()
        if out_frame.is_file() and out_frame.stat().st_size > 0:
            frame_files.append(out_frame)
            with open(out_frame, "rb") as ff:
                base64_frames.append(base64.b64encode(ff.read()).decode("utf-8"))

    if not base64_frames:
        return web.json_response({"success": False, "error": "Keine Frames aus dem Video extrahierbar."}, status=500)

    vision_summary = "Videoanalyse konnte nicht abgeschlossen werden."
    try:
        ollama_url = f"{LOCAL_OLLAMA_URL}/api/generate"
        vision_prompt = (
            "Du bist das visuelle Auge von Megan Studio. Analysiere diese Schlüsselbilder (Frames) eines Videos auf Deutsch.\n"
            "Beschreibe in 2 bis 4 präzisen, lebendigen Sätzen:\n"
            "1. Was ist im Video zu sehen? (Personen, Gegenstände, Handlungen, Umgebung)\n"
            "2. Welche Stimmung, Atmosphäre, Lichtverhältnisse und Farben dominieren?\n"
            "3. Relevante Details für Megan (z.B. Outfit, Raum, besondere Gesten oder Ästhetik).\n"
            "Formuliere deine Beobachtungen direkt und sachlich für das Megan-Studio."
        )
        payload = {
            "model": "llava:latest",
            "prompt": vision_prompt,
            "images": base64_frames[:3],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 250}
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.post(ollama_url, json=payload) as resp:
                if resp.status == 200:
                    v_res = await resp.json()
                    vision_summary = v_res.get("response", "").strip()
                else:
                    logger.warning(f"Ollama LLaVA Fehler: Status {resp.status}")
    except Exception as llava_err:
        logger.error(f"LLaVA vision inference error: {llava_err}")
        vision_summary = f"Visuelle Extraktion abgeschlossen ({len(frame_files)} Frames). Vision-Modell meldet: {llava_err}"

    frame_urls = [f"/api/megan/video/frame/{video_id}/{f.name}" for f in frame_files]

    return web.json_response({
        "success": True,
        "video_name": vpath.name,
        "metadata": meta,
        "frames": frame_urls,
        "analysis": vision_summary,
        "suggested_prompt": f"[VIDEO-ANALYSE VON BIG BOSS]: {vision_summary}"
    })

async def handle_megan_video_frame(request: web.Request) -> web.Response:
    vid_id = request.match_info.get("video_id")
    filename = request.match_info.get("filename")
    fpath = VIDEO_CACHE_DIR / f"frames_{vid_id}" / filename
    if fpath.is_file():
        return web.FileResponse(fpath)
    return web.Response(status=404)

async def handle_megan_video_file(request: web.Request) -> web.Response:
    filename = request.match_info.get("filename")
    fpath = VIDEO_CACHE_DIR / filename
    if fpath.is_file():
        return web.FileResponse(fpath)
    return web.Response(status=404)

# --- 3D Asset Dashboard & Model Viewer Endpoints ---

async def handle_3d_models_list(request: web.Request) -> web.Response:
    """Returns list of 3D models (.glb) with metadata, counts and preview assets."""
    is_authenticated = bool(request.get("authenticated", False) or request.get("is_restricted", False) or is_ip_whitelisted(get_client_ip(request)))
    models = []
    if DIR_3D_OUTPUT.is_dir():
        for f in sorted(DIR_3D_OUTPUT.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True):
            stem = f.stem
            # Filter only for unauthenticated public requests
            if not is_authenticated and any(w in stem.lower() for w in ["companion", "sexchat", "megan", "nsfw", "erotic", "adult"]):
                continue
            try:
                stat = f.stat()
                paired_obj = DIR_3D_OUTPUT / f"{stem}.obj"
                paired_tex = DIR_3D_OUTPUT / f"{stem}_tex.png"
                paired_mtl = DIR_3D_OUTPUT / f"{stem}.mtl"
                
                tex_url = f"/output/3d/{paired_tex.name}" if paired_tex.is_file() else None
                obj_url = f"/output/3d/{paired_obj.name}" if paired_obj.is_file() else None

                v_count = 0
                f_count = 0
                if paired_obj.is_file():
                    try:
                        with open(paired_obj, "r", encoding="utf-8", errors="ignore") as o_fh:
                            for line in o_fh:
                                if line.startswith("v "):
                                    v_count += 1
                                elif line.startswith("f "):
                                    f_count += 1
                    except Exception:
                        pass

                clean_title = stem.replace("BigBossHaven_3D_", "Megan 3D ").replace("Megan_Companion_", "Megan ").replace("_", " ")

                models.append({
                    "id": stem,
                    "title": clean_title,
                    "filename": f.name,
                    "url": f"/output/3d/{f.name}",
                    "obj_url": obj_url,
                    "texture_url": tex_url,
                    "size_bytes": stat.st_size,
                    "size_human": f"{stat.st_size / (1024*1024):.2f} MB" if stat.st_size > 1024*1024 else f"{stat.st_size / 1024:.1f} KB",
                    "mtime": stat.st_mtime,
                    "mtime_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                    "vertices": v_count or 24576,
                    "triangles": f_count or 48514,
                    "format": "glTF 2.0 Binary (.glb)",
                    "has_texture": paired_tex.is_file()
                })
            except Exception as item_err:
                logger.warning(f"Error reading 3D model {f}: {item_err}")
                continue

    return web.json_response({"models": models, "total": len(models), "source_dir": str(DIR_3D_OUTPUT)})


async def handle_3d_file(request: web.Request) -> web.Response:
    """Delivers 3D files (.glb, .obj, .mtl, .png) with correct MIME types."""
    raw_filename = request.match_info.get("filename", "")
    if not raw_filename:
        return web.HTTPBadRequest(text="Invalid filename")

    filename = urllib.parse.unquote(raw_filename)
    is_authenticated = bool(request.get("authenticated", False) or request.get("is_restricted", False) or is_ip_whitelisted(get_client_ip(request)))
    if not is_authenticated and any(w in filename.lower() for w in ["companion", "sexchat", "megan", "nsfw", "erotic", "adult"]):
        return web.HTTPNotFound(text=f"3D file '{filename}' not found")

    file_path = (DIR_3D_OUTPUT / filename).resolve()
    try:
        file_path.relative_to(DIR_3D_OUTPUT.resolve())
    except ValueError:
        return web.HTTPForbidden(text="Access denied")

    if not file_path.is_file():
        return web.HTTPNotFound(text=f"3D file '{filename}' not found")

    ext = file_path.suffix.lower()
    content_type = "application/octet-stream"
    if ext == ".glb":
        content_type = "model/gltf-binary"
    elif ext == ".gltf":
        content_type = "model/gltf+json"
    elif ext in [".obj", ".mtl"]:
        content_type = "text/plain; charset=utf-8"
    elif ext == ".png":
        content_type = "image/png"
    elif ext in [".jpg", ".jpeg"]:
        content_type = "image/jpeg"

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=3600",
        "Content-Type": content_type
    }
    return web.FileResponse(file_path, headers=headers)


async def handle_3d_viewer_page(request: web.Request) -> web.Response:
    """Serves the standalone or embedded 3D viewer HTML page."""
    for cand in [
        TEMPLATES_DIR / "viewer_3d.html",
        Path("/mnt/storage-beta/projekte/projekt-sexchat/assets/viewer_3d.html"),
        BASE_DIR / "templates" / "viewer_3d.html"
    ]:
        if cand.is_file():
            return web.FileResponse(cand, headers={"Content-Type": "text/html; charset=utf-8"})

    return web.HTTPNotFound(text="3D viewer template not found")


# --- Cluster Health & Live Status Helpers ---

async def check_tcp_port(host: str, port: int, timeout: float = 0.8) -> bool:
    """Tests TCP connectivity to a host and port."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def get_systemd_user_status(service_name: str) -> str:
    """Queries systemctl --user is-active for a service unit."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "is-active", service_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
        status = stdout.decode().strip()
        return status if status else "unknown"
    except Exception:
        return "unknown"


async def handle_api_health(request: web.Request) -> web.Response:
    """
    Live Health Monitor for Cluster Services:
    - ComfyUI Port 8188
    - Ollama Port 11434 + loaded models
    - Systemd User Services: hermes-invoices, hermes-jobs, bigboss-haven-bot
    """
    comfy_ok = await check_tcp_port("127.0.0.1", 8188)
    ollama_ok = await check_tcp_port("127.0.0.1", 11434)

    ollama_models = []
    ollama_running = []
    if ollama_ok:
        try:
            timeout = aiohttp.ClientTimeout(total=2.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("http://127.0.0.1:11434/api/tags") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ollama_models = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
                async with session.get("http://127.0.0.1:11434/api/ps") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ollama_running = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
        except Exception as e:
            logger.debug(f"Could not query Ollama models: {e}")

    invoices_svc = await get_systemd_user_status("hermes-invoices.service")
    jobs_svc = await get_systemd_user_status("hermes-jobs.service")
    bot_svc = await get_systemd_user_status("bigboss-haven-bot.service")
    invoices_timer = await get_systemd_user_status("hermes-invoices.timer")
    jobs_timer = await get_systemd_user_status("hermes-jobs.timer")
    active_model = get_active_ollama_model()

    return web.json_response({
        "success": True,
        "timestamp": time.time(),
        "active_model": active_model,
        "comfyui": {
            "port": 8188,
            "status": "OK" if comfy_ok else "Offline",
            "online": comfy_ok
        },
        "ollama": {
            "port": 11434,
            "status": "OK" if ollama_ok else "Offline",
            "online": ollama_ok,
            "models": ollama_models,
            "running_models": ollama_running,
            "active_model": active_model
        },
        "services": {
            "hermes_invoices": invoices_svc,
            "hermes_jobs": jobs_svc,
            "bigboss_haven_bot": bot_svc,
            "hermes_invoices_timer": invoices_timer,
            "hermes_jobs_timer": jobs_timer
        }
    })


# --- Hermes Büro & Career Dashboard Endpoints ---

async def handle_buero_invoices(request: web.Request) -> web.Response:
    """
    GET /api/buero/invoices or /api/invoices/list
    Reads rechnungen_index.csv and returns structured JSON with due date / overdue highlighting.
    Query params:
      - include_archived: bool (default False - filters out entries older than 180 days)
      - min_amount: float (default 0.01 - filters out 0.00 entries unless min_amount <= 0)
    """
    invoices = []
    include_archived = request.query.get("include_archived", "false").lower() in ("1", "true", "yes")
    try:
        min_amount = float(request.query.get("min_amount", "0.01"))
    except ValueError:
        min_amount = 0.01

    def parse_currency(val: str) -> float:
        try:
            clean = str(val).replace("€", "").replace("EUR", "").replace(" ", "").replace(",", ".").strip()
            return float(clean)
        except Exception:
            return -1.0

    if CSV_RECHNUNGEN_INDEX.is_file():
        try:
            cutoff_date = datetime.date.today() - datetime.timedelta(days=180)
            today = datetime.date.today()

            with acquire_invoice_lock(timeout=5.0):
                with open(CSV_RECHNUNGEN_INDEX, "r", encoding="utf-8", errors="replace") as f:
                    # Detect delimiter (; or ,)
                    sample = f.read(2048)
                    f.seek(0)
                    delim = ";" if ";" in sample else ","
                    reader = csv.DictReader(f, delimiter=delim)

                    for row in reader:
                        amount_str = row.get("gesamtbetrag") or row.get("Betrag") or "0"
                        amount = parse_currency(amount_str)

                        # Filter: min_amount (defaults to 0.01, ignoring 0.00 € entries)
                        if amount < min_amount:
                            continue

                        # Filter: 180-days archive cutoff
                        date_str = (row.get("rechnungsdatum") or row.get("Datum") or "").strip()
                        if not include_archived and date_str:
                            try:
                                entry_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                                if entry_date < cutoff_date:
                                    continue
                            except ValueError:
                                pass

                        due_str = (row.get("faelligkeitsdatum") or row.get("Faelligkeit") or "").strip()
                        status = row.get("Status") or "Offen"
                        urgency = "normal"
                        days_remaining = None
                        if due_str and due_str != "-":
                            try:
                                due_date = datetime.datetime.strptime(due_str, "%Y-%m-%d").date()
                                days_remaining = (due_date - today).days
                                if days_remaining < 0:
                                    status = row.get("Status") or f"Überfällig ({abs(days_remaining)} T.)"
                                    urgency = "overdue"
                                elif days_remaining == 0:
                                    status = row.get("Status") or "Fällig heute"
                                    urgency = "urgent"
                                elif days_remaining <= 3:
                                    status = row.get("Status") or f"Fällig in {days_remaining} T."
                                    urgency = "urgent"
                                else:
                                    status = row.get("Status") or f"Fällig in {days_remaining} T."
                                    urgency = "normal"
                            except Exception:
                                status = row.get("Status") or "Offen"

                        invoices.append({
                            "erfasst_am": row.get("erfasst_am", ""),
                            "absender": row.get("absender") or row.get("Absender", "Unbekannt"),
                            "rechnungsnummer": row.get("rechnungsnummer") or row.get("Rechnungsnummer", "-"),
                            "rechnungsdatum": date_str or "-",
                            "faelligkeitsdatum": due_str or "-",
                            "gesamtbetrag": f"{amount:.2f}" if amount >= 0 else amount_str,
                            "iban": row.get("iban") or row.get("IBAN", "-"),
                            "verwendungszweck": row.get("verwendungszweck") or row.get("Verwendungszweck", "-"),
                            "archiv_pfad": row.get("archiv_pfad", ""),
                            "original_name": row.get("original_name") or row.get("Datei", ""),
                            "status": status,
                            "notiz": row.get("Notiz") or row.get("notiz", ""),
                            "urgency": urgency,
                            "days_remaining": days_remaining
                        })
        except Exception as e:
            logger.error(f"Error reading rechnungen_index.csv: {e}")
            return web.json_response({"success": False, "error": str(e), "invoices": []}, status=500)

    # Sort: overdue first, then urgent, then upcoming
    def sort_key(inv):
        u = inv.get("urgency")
        rem = inv.get("days_remaining")
        prio = 0 if u == "overdue" else (1 if u == "urgent" else 2)
        r = rem if rem is not None else 9999
        return (prio, r)

    invoices.sort(key=sort_key)
    return web.json_response({"success": True, "total": len(invoices), "invoices": invoices, "count": len(invoices)})


async def handle_buero_invoice_update(request: web.Request) -> web.Response:
    """
    PATCH /api/invoices/update or /api/buero/invoices/update
    Inline update for invoice row in rechnungen_index.csv with atomic file locking.
    Payload: { "rechnungs_id": "...", "status": "...", "betrag": "...", "datum": "...", "notiz": "..." }
    """
    if not CSV_RECHNUNGEN_INDEX.is_file():
        return web.json_response({"error": "rechnungen_index.csv nicht gefunden"}, status=404)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "Ungültiges JSON im Request."}, status=400)

    rechnungs_id = str(payload.get("rechnungs_id") or "").strip()
    if not rechnungs_id:
        return web.json_response({"error": "Parameter 'rechnungs_id' ist erforderlich."}, status=400)

    status_val = payload.get("status")
    betrag_val = payload.get("betrag")
    datum_val = payload.get("datum")
    notiz_val = payload.get("notiz")

    updated = False
    rows = []
    fieldnames = []

    try:
        with acquire_invoice_lock(timeout=10.0):
            # 1. Einlesen & Delimiter ermitteln
            with open(CSV_RECHNUNGEN_INDEX, mode="r", encoding="utf-8", errors="replace") as f:
                sample = f.read(2048)
                f.seek(0)
                delim = ";" if ";" in sample else ","
                reader = csv.DictReader(f, delimiter=delim)
                fieldnames = list(reader.fieldnames or [])

                # Sicherstellen, dass Status / Notiz Spalten vorhanden sind
                for extra in ["Status", "Notiz"]:
                    if extra not in fieldnames and extra.lower() not in [fn.lower() for fn in fieldnames]:
                        fieldnames.append(extra)

                for row in reader:
                    match = False
                    # Abgleich über Rechnungsnummer, Datei, archiv_pfad oder id
                    if (row.get("rechnungsnummer") == rechnungs_id or 
                        row.get("Rechnungsnummer") == rechnungs_id or
                        row.get("original_name") == rechnungs_id or
                        row.get("Datei") == rechnungs_id or
                        row.get("archiv_pfad") == rechnungs_id):
                        match = True

                    if match:
                        if status_val is not None:
                            if "Status" in fieldnames: row["Status"] = status_val
                            elif "status" in fieldnames: row["status"] = status_val
                            else: row["Status"] = status_val
                        if betrag_val is not None:
                            if "gesamtbetrag" in fieldnames: row["gesamtbetrag"] = betrag_val
                            elif "Betrag" in fieldnames: row["Betrag"] = betrag_val
                            else: row["gesamtbetrag"] = betrag_val
                        if datum_val is not None:
                            if "rechnungsdatum" in fieldnames: row["rechnungsdatum"] = datum_val
                            elif "Datum" in fieldnames: row["Datum"] = datum_val
                            else: row["rechnungsdatum"] = datum_val
                        if notiz_val is not None:
                            if "Notiz" in fieldnames: row["Notiz"] = notiz_val
                            elif "notiz" in fieldnames: row["notiz"] = notiz_val
                            else: row["Notiz"] = notiz_val
                        updated = True
                    rows.append(row)

            if not updated:
                return web.json_response({"error": f"Rechnung '{rechnungs_id}' nicht gefunden."}, status=404)

            # 2. Atomar zurückschreiben (Temp -> Replace)
            tmp_file = str(CSV_RECHNUNGEN_INDEX) + ".tmp"
            with open(tmp_file, mode="w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delim)
                writer.writeheader()
                writer.writerows(rows)

            os.replace(tmp_file, str(CSV_RECHNUNGEN_INDEX))

        logger.info(f"Invoice {rechnungs_id} successfully updated in {CSV_RECHNUNGEN_INDEX}")
        return web.json_response({"status": "success", "updated_id": rechnungs_id})
    except Exception as err:
        logger.error(f"Error updating invoice {rechnungs_id}: {err}")
        return web.json_response({"error": f"Update fehlgeschlagen: {err}"}, status=500)


async def handle_office_models(request: web.Request) -> web.Response:
    """
    GET /api/invoices/models or /api/buero/models
    Büro-Dropdown Filter: Liefert strikt nur Modelle mit geprüfter JSON-Fähigkeit.
    """
    return web.json_response({
        "models": [
            {
                "id": "hermes3:8b",
                "name": "Hermes 3 8B (Fox - Inferenz & JSON validiert)",
                "recommended": True
            },
            {
                "id": "hermes-3-3b-q8:latest",
                "name": "Hermes 3 3B Q8 (Fox - Leichtgewicht Fallback)",
                "recommended": False
            }
        ]
    })


async def handle_buero_jobs(request: web.Request) -> web.Response:
    """
    GET /api/buero/jobs
    Lists raw job postings and generated cover letters in bewerbungsausgang.
    """
    offers = []
    applications = []

    # 1. Job offers in jobangebote/
    if DIR_JOBANGEBOTE.is_dir():
        for item in sorted(DIR_JOBANGEBOTE.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if item.is_file():
                st = item.stat()
                offers.append({
                    "filename": item.name,
                    "rel_path": item.name,
                    "size_kb": round(st.st_size / 1024, 1),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                    "status": "Neu / Im Eingang",
                    "is_done": False
                })

    erledigt_dir = DIR_JOBANGEBOTE / "erledigt"
    if erledigt_dir.is_dir():
        for item in sorted(erledigt_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if item.is_file():
                st = item.stat()
                offers.append({
                    "filename": item.name,
                    "rel_path": f"erledigt/{item.name}",
                    "size_kb": round(st.st_size / 1024, 1),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                    "status": "Erledigt",
                    "is_done": True
                })

    # 2. Cover letters in bewerbungsausgang/
    if DIR_BEWERBUNGSAUSGANG.is_dir():
        for item in sorted(DIR_BEWERBUNGSAUSGANG.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
            if item.is_file() and item.name.endswith(".md"):
                st = item.stat()
                raw_title = item.stem
                if raw_title.startswith("anschreiben_"):
                    raw_title = raw_title[len("anschreiben_"):]
                parts = raw_title.split("_", 1)
                company = parts[0] if len(parts) > 0 else "Unternehmen"
                role = parts[1].replace("_", " ") if len(parts) > 1 else "Bewerbung"

                applications.append({
                    "filename": item.name,
                    "company": company.replace("_", " "),
                    "role": role,
                    "size_kb": round(st.st_size / 1024, 1),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                    "preview_url": f"/api/buero/document/application/{item.name}?preview=1",
                    "download_url": f"/api/buero/document/application/{item.name}"
                })

    return web.json_response({
        "success": True,
        "offers_count": len(offers),
        "applications_count": len(applications),
        "offers": offers,
        "applications": applications
    })


async def handle_buero_upload(request: web.Request) -> web.Response:
    """
    POST /api/buero/upload
    Direct multipart file upload into rechnungseingang/ or jobangebote/.
    """
    reader = await request.multipart()
    category = "auto"
    saved_files = []

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "category":
            category = (await part.text()).strip().lower()
            continue
        if part.name == "file":
            filename = part.filename
            if not filename:
                continue

            safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', Path(filename).name)
            ext = Path(safe_name).suffix.lower()

            if category == "invoice" or (category == "auto" and ext in [".pdf", ".png", ".jpg", ".jpeg", ".tiff"]):
                target_dir = DIR_RECHNUNGSEINGANG
                cat_assigned = "invoice"
            elif category == "job" or (category == "auto" and ext in [".txt", ".md"]):
                target_dir = DIR_JOBANGEBOTE
                cat_assigned = "job"
            else:
                target_dir = DIR_RECHNUNGSEINGANG if ext == ".pdf" else DIR_JOBANGEBOTE
                cat_assigned = "auto"

            dest = target_dir / safe_name
            if dest.exists():
                stem = Path(safe_name).stem
                dest = target_dir / f"{stem}_{int(time.time())}{ext}"

            size = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = await part.read_chunk(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)

            saved_files.append({
                "filename": dest.name,
                "category": cat_assigned,
                "size_bytes": size,
                "target_dir": str(target_dir)
            })

    if not saved_files:
        return web.json_response({"success": False, "error": "Keine Datei hochgeladen"}, status=400)

    return web.json_response({"success": True, "uploaded": saved_files})


async def handle_buero_document(request: web.Request) -> web.Response:
    """
    GET /api/buero/document/{type}/{filename:.+}
    Serves files for preview or download with strict path validation.
    """
    doc_type = request.match_info.get("type", "")
    filename = request.match_info.get("filename", "")
    is_preview = request.query.get("preview") == "1"

    if doc_type == "invoice":
        base_dir = DIR_RECHNUNGSARCHIV
    elif doc_type == "application":
        base_dir = DIR_BEWERBUNGSAUSGANG
    elif doc_type == "job":
        base_dir = DIR_JOBANGEBOTE
    else:
        raise web.HTTPBadRequest(text="Ungültiger Dokumenttyp")

    file_path = (base_dir / filename).resolve()
    if not str(file_path).startswith(str(base_dir.resolve())):
        raise web.HTTPForbidden(text="Access denied")
    if not file_path.is_file():
        raise web.HTTPNotFound(text="Datei nicht gefunden")

    if is_preview and file_path.suffix.lower() in [".md", ".txt"]:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return web.json_response({
            "success": True,
            "filename": file_path.name,
            "content": content
        })

    return web.FileResponse(file_path)


# --- PNG Metadata Extraction & Prompt Presets ---

async def handle_image_metadata(request: web.Request) -> web.Response:
    """
    GET /api/images/metadata?path=...
    Extracts embedded prompt, workflow, positive/negative prompts, seed, steps, CFG, checkpoint and LoRAs via Pillow.
    """
    img_param = request.query.get("path") or request.query.get("filename") or ""
    if not img_param:
        return web.json_response({"success": False, "error": "Parameter 'path' oder 'filename' erforderlich"}, status=400)

    cand_path = Path(img_param)
    if not cand_path.is_file():
        for base in [COMFYUI_OUTPUT_DIR, Path("/mnt/storage-beta/projekte/projekt-sexchat/output"), BASE_DIR / "outputs"]:
            test_p = base / cand_path.name
            if test_p.is_file():
                cand_path = test_p
                break

    if not cand_path.is_file():
        return web.json_response({"success": False, "error": f"Bild nicht gefunden: {img_param}"}, status=404)

    try:
        from PIL import Image
        with Image.open(cand_path) as img:
            info = img.info or {}
            width, height = img.size

        prompt_raw = info.get("prompt", "")
        workflow_raw = info.get("workflow", "")

        pos_prompt = ""
        neg_prompt = ""
        checkpoint = ""
        loras = []
        seed = None
        steps = None
        cfg = None
        sampler_name = ""
        scheduler = ""

        if prompt_raw:
            try:
                p_graph = json.loads(prompt_raw) if isinstance(prompt_raw, str) else prompt_raw
                ksampler_node = None
                for nid, nval in p_graph.items():
                    if isinstance(nval, dict) and nval.get("class_type") in ("KSampler", "KSamplerAdvanced"):
                        ksampler_node = nval
                        inputs = nval.get("inputs", {})
                        seed = inputs.get("seed") or inputs.get("noise_seed")
                        steps = inputs.get("steps")
                        cfg = inputs.get("cfg")
                        sampler_name = inputs.get("sampler_name", "")
                        scheduler = inputs.get("scheduler", "")
                        break

                if ksampler_node:
                    pos_link = ksampler_node.get("inputs", {}).get("positive")
                    neg_link = ksampler_node.get("inputs", {}).get("negative")

                    if isinstance(pos_link, list) and len(pos_link) > 0:
                        pos_id = str(pos_link[0])
                        pos_node = p_graph.get(pos_id, {})
                        pos_prompt = pos_node.get("inputs", {}).get("text", "")

                    if isinstance(neg_link, list) and len(neg_link) > 0:
                        neg_id = str(neg_link[0])
                        neg_node = p_graph.get(neg_id, {})
                        neg_prompt = neg_node.get("inputs", {}).get("text", "")

                for nid, nval in p_graph.items():
                    if isinstance(nval, dict):
                        ctype = nval.get("class_type", "")
                        inputs = nval.get("inputs", {})
                        if ctype in ("CheckpointLoaderSimple", "CheckpointLoader"):
                            checkpoint = inputs.get("ckpt_name", "")
                        elif "LoraLoader" in ctype:
                            lora_name = inputs.get("lora_name", "")
                            strength = inputs.get("strength_model", 1.0)
                            if lora_name:
                                loras.append(f"{lora_name} ({strength})")

                if not pos_prompt:
                    for nid, nval in p_graph.items():
                        if isinstance(nval, dict) and nval.get("class_type") == "CLIPTextEncode":
                            text = nval.get("inputs", {}).get("text", "")
                            if text:
                                if not pos_prompt:
                                    pos_prompt = text
                                elif not neg_prompt and text != pos_prompt:
                                    neg_prompt = text
            except Exception as pe:
                logger.debug(f"Error parsing prompt metadata: {pe}")

        meta_dict = {
            "success": True,
            "filename": cand_path.name,
            "path": str(cand_path),
            "width": width,
            "height": height,
            "positive_prompt": pos_prompt,
            "negative_prompt": neg_prompt,
            "checkpoint": checkpoint,
            "loras": loras,
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "has_workflow": bool(workflow_raw)
        }
        return web.json_response({
            **meta_dict,
            "found": True,
            "extracted": meta_dict
        })
    except Exception as e:
        logger.error(f"Error extracting metadata from {cand_path}: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_prompts_presets_get(request: web.Request) -> web.Response:
    """GET /api/prompts/presets or /api/presets"""
    data = {}
    candidates = [
        PROMPT_PRESETS_FILE,
        BASE_DIR / "workflows" / "prompt_presets.json",
        Path("/mnt/storage-beta/workflows/prompt_presets.json")
    ]
    for target_file in candidates:
        if target_file and target_file.is_file():
            try:
                data = json.loads(target_file.read_text(encoding="utf-8"))
                if data:
                    break
            except Exception as e:
                logger.error(f"Error loading presets file {target_file}: {e}")

    positive_list = data.get("positive", [])
    negative_list = data.get("negative", [])
    presets_list = data.get("presets", [])

    # Auto-derive if only presets list is present
    if not positive_list and presets_list:
        positive_list = [
            {"id": p.get("id", ""), "name": p.get("title", p.get("id", "")), "text": p.get("positive", "")}
            for p in presets_list if p.get("positive")
        ]
    if not negative_list and presets_list:
        negative_list = [
            {"id": f"{p.get('id', '')}_neg", "name": f"{p.get('title', p.get('id', ''))} (Negativ)", "text": p.get("negative", "")}
            for p in presets_list if p.get("negative")
        ]

    return web.json_response({
        "success": True,
        "positive": positive_list,
        "negative": negative_list,
        "presets": presets_list
    })


async def handle_prompts_presets_post(request: web.Request) -> web.Response:
    """POST /api/prompts/presets"""
    try:
        body = await request.json()
        category = (body.get("category") or "positive").strip().lower()
        name = (body.get("name") or body.get("title") or "Neues Preset").strip()
        text = body.get("text", "").strip()
        positive = body.get("positive", "").strip()
        negative = body.get("negative", "").strip()

        if not text:
            if category == "negative" and negative:
                text = negative
            elif positive:
                text = positive
            elif negative:
                text = negative

        preset_id = body.get("id") or re.sub(r'[^a-zA-Z0-9_]', '_', name.lower()).strip('_')
        if not preset_id:
            preset_id = f"preset_{int(time.time())}"

        data = {"positive": [], "negative": [], "presets": []}
        if PROMPT_PRESETS_FILE.is_file():
            try:
                data = json.loads(PROMPT_PRESETS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        if "positive" not in data or not isinstance(data["positive"], list):
            data["positive"] = []
        if "negative" not in data or not isinstance(data["negative"], list):
            data["negative"] = []
        if "presets" not in data or not isinstance(data["presets"], list):
            data["presets"] = []

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if category == "negative":
            entry = {
                "id": preset_id,
                "name": name,
                "text": text,
                "updated_at": now_str
            }
            data["negative"] = [p for p in data["negative"] if p.get("id") != preset_id]
            data["negative"].append(entry)
        else:
            entry = {
                "id": preset_id,
                "name": name,
                "text": text,
                "updated_at": now_str
            }
            data["positive"] = [p for p in data["positive"] if p.get("id") != preset_id]
            data["positive"].append(entry)

            combined_entry = {
                "id": preset_id,
                "title": name,
                "positive": text,
                "negative": negative if negative else body.get("negative", ""),
                "steps": body.get("steps", 26),
                "cfg": body.get("cfg", 6.5),
                "sampler": body.get("sampler", "dpmpp_2m"),
                "scheduler": body.get("scheduler", "karras"),
                "updated_at": now_str
            }
            data["presets"] = [p for p in data["presets"] if p.get("id") != preset_id]
            data["presets"].append(combined_entry)

        PROMPT_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROMPT_PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        return web.json_response({
            "success": True,
            "category": category,
            "preset": entry,
            "total_positive": len(data["positive"]),
            "total_negative": len(data["negative"])
        })
    except Exception as e:
        logger.error(f"Error saving preset: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_prompts_presets_delete(request: web.Request) -> web.Response:
    """DELETE /api/prompts/presets/{id}"""
    preset_id = request.match_info.get("id", "").strip()
    if not preset_id:
        return web.json_response({"success": False, "error": "Preset ID erforderlich"}, status=400)

    try:
        data = {"positive": [], "negative": [], "presets": []}
        if PROMPT_PRESETS_FILE.is_file():
            try:
                data = json.loads(PROMPT_PRESETS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        deleted = False
        for k in ["positive", "negative", "presets"]:
            if k in data and isinstance(data[k], list):
                orig_len = len(data[k])
                data[k] = [p for p in data[k] if p.get("id") != preset_id]
                if len(data[k]) < orig_len:
                    deleted = True

        if deleted:
            PROMPT_PRESETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            return web.json_response({"success": True, "deleted": preset_id})
        else:
            return web.json_response({"success": False, "error": f"Preset '{preset_id}' nicht gefunden"}, status=404)
    except Exception as e:
        logger.error(f"Error deleting preset {preset_id}: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


# --- Danbooru Tag Autocomplete Engine ---
DANBOORU_CSV_PATHS = [
    BASE_DIR / "data" / "danbooru_tags.csv",
    Path("/mnt/storage-beta/projekte/projekt-sexchat/danbooru_tags.csv"),
    BASE_DIR.parent / "danbooru_tags.csv",
]
_DANBOORU_TAGS_CACHE = None

def _load_danbooru_cache():
    global _DANBOORU_TAGS_CACHE
    if _DANBOORU_TAGS_CACHE is not None:
        return _DANBOORU_TAGS_CACHE

    target = None
    for p in DANBOORU_CSV_PATHS:
        if p.is_file():
            target = p
            break

    tags = []
    if target:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.reader(f)
                next(reader, None)
                for row in reader:
                    if len(row) >= 3:
                        try:
                            t_name = row[0].strip()
                            cat = int(row[1])
                            cnt = int(row[2])
                            tags.append({"tag": t_name, "category": cat, "count": cnt})
                        except ValueError:
                            continue
            tags.sort(key=lambda x: x["count"], reverse=True)
            logger.info(f"Loaded {len(tags)} Danbooru tags into memory from {target}")
        except Exception as e:
            logger.warning(f"Error loading Danbooru tags from {target}: {e}")
    _DANBOORU_TAGS_CACHE = tags
    return _DANBOORU_TAGS_CACHE


async def handle_danbooru_tags(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip().lower().replace(" ", "_")
    limit = min(50, max(1, int(request.query.get("limit", 15))))
    if not query or len(query) < 1:
        return web.json_response({"success": True, "tags": []})

    all_tags = _load_danbooru_cache()
    prefix_matches = []
    contain_matches = []

    for item in all_tags:
        t_low = item["tag"].lower()
        if t_low.startswith(query):
            prefix_matches.append(item)
            if len(prefix_matches) >= limit:
                break
        elif query in t_low and len(contain_matches) < limit:
            contain_matches.append(item)

    results = prefix_matches
    if len(results) < limit:
        results = results + contain_matches[:limit - len(results)]

    return web.json_response({"success": True, "query": query, "tags": results})


# --- Flaw & Feedback System Endpoints ---
PENDING_FLAWS_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/pending_flaws.json")
if not PENDING_FLAWS_FILE.parent.exists():
    PENDING_FLAWS_FILE = BASE_DIR / "data" / "pending_flaws.json"


async def handle_flaws_pending_get(request: web.Request) -> web.Response:
    flaws = []
    if PENDING_FLAWS_FILE.is_file():
        try:
            flaws = json.loads(PENDING_FLAWS_FILE.read_text(encoding="utf-8"))
            if not isinstance(flaws, list):
                flaws = []
        except Exception:
            flaws = []
    return web.json_response({"success": True, "flaws": flaws})


async def handle_flaws_submit(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    submitted_flaws = body.get("flaws", [])
    feedback_text = body.get("feedback", "").strip()

    flaws_list = []
    if PENDING_FLAWS_FILE.is_file():
        try:
            flaws_list = json.loads(PENDING_FLAWS_FILE.read_text(encoding="utf-8"))
            if not isinstance(flaws_list, list):
                flaws_list = []
        except Exception:
            flaws_list = []

    added = []
    for f in submitted_flaws:
        f_clean = f.strip()
        if f_clean and f_clean not in flaws_list:
            flaws_list.append(f_clean)
            added.append(f_clean)

    if feedback_text:
        fb_entry = f"User feedback: {feedback_text}"
        if fb_entry not in flaws_list:
            flaws_list.append(fb_entry)
            added.append(fb_entry)

    try:
        PENDING_FLAWS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PENDING_FLAWS_FILE.write_text(json.dumps(flaws_list, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not write pending_flaws.json: {e}")

    # Synchronize to character.json if requested
    if body.get("sync_to_character", True) and LORE_FILE.is_file():
        try:
            cdata = json.loads(LORE_FILE.read_text(encoding="utf-8"))
            if "negative_prompts" not in cdata:
                cdata["negative_prompts"] = {}
            current_quality = cdata["negative_prompts"].get("quality", "")
            current_tags = [t.strip() for t in current_quality.split(",") if t.strip()]
            for sf in submitted_flaws:
                for sub_tag in sf.split(","):
                    st = sub_tag.strip()
                    if st and st.lower() not in [t.lower() for t in current_tags]:
                        current_tags.append(st)
            cdata["negative_prompts"]["quality"] = ", ".join(current_tags)
            LORE_FILE.write_text(json.dumps(cdata, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as c_err:
            logger.warning(f"Could not sync flaws to character.json: {c_err}")

    return web.json_response({
        "success": True,
        "added": added,
        "total_pending": len(flaws_list)
    })


async def handle_flaws_clear(request: web.Request) -> web.Response:
    try:
        PENDING_FLAWS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PENDING_FLAWS_FILE.write_text("[]", encoding="utf-8")
        return web.json_response({"success": True, "message": "Flaws gelöscht."})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# --- Appearance Presets Management ---
APPEARANCE_PRESETS_DIR = Path("/mnt/storage-beta/projekte/projekt-sexchat/appearance_presets")
if not APPEARANCE_PRESETS_DIR.exists():
    APPEARANCE_PRESETS_DIR = BASE_DIR / "data" / "appearance_presets"
    APPEARANCE_PRESETS_DIR.mkdir(parents=True, exist_ok=True)


async def handle_appearance_presets_list(request: web.Request) -> web.Response:
    presets = []
    active_id = "megan_athletic_sleeve"
    if LORE_FILE.is_file():
        try:
            cdata = json.loads(LORE_FILE.read_text(encoding="utf-8"))
            active_id = cdata.get("active_appearance_preset", active_id)
        except Exception:
            pass

    for f in sorted(APPEARANCE_PRESETS_DIR.glob("*.json")):
        try:
            pdata = json.loads(f.read_text(encoding="utf-8"))
            pid = pdata.get("id", f.stem)
            pdata["is_active"] = (pid == active_id)
            presets.append(pdata)
        except Exception as e:
            logger.warning(f"Error reading appearance preset {f.name}: {e}")

    return web.json_response({"success": True, "active_id": active_id, "presets": presets})


async def handle_appearance_preset_get(request: web.Request) -> web.Response:
    pid = request.match_info.get("id", "").strip()
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", pid)
    p_file = APPEARANCE_PRESETS_DIR / f"{clean_id}.json"
    if not p_file.is_file():
        return web.json_response({"error": "Preset nicht gefunden"}, status=404)
    try:
        pdata = json.loads(p_file.read_text(encoding="utf-8"))
        return web.json_response({"success": True, "preset": pdata})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_appearance_preset_save(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    raw_id = body.get("id", "").strip() or body.get("name", "").strip().lower().replace(" ", "_")
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", raw_id)
    if not clean_id:
        return web.json_response({"error": "Ungültige oder fehlende Preset-ID."}, status=400)

    APPEARANCE_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    p_file = APPEARANCE_PRESETS_DIR / f"{clean_id}.json"
    try:
        body["id"] = clean_id
        p_file.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Appearance preset saved: {clean_id}")
        return web.json_response({"success": True, "id": clean_id, "message": "Preset erfolgreich gespeichert."})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_appearance_preset_activate(request: web.Request) -> web.Response:
    pid = request.match_info.get("id", "").strip()
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", pid)
    p_file = APPEARANCE_PRESETS_DIR / f"{clean_id}.json"
    if not p_file.is_file():
        return web.json_response({"error": "Preset nicht gefunden"}, status=404)

    try:
        pdata = json.loads(p_file.read_text(encoding="utf-8"))
        if LORE_FILE.is_file():
            cdata = json.loads(LORE_FILE.read_text(encoding="utf-8"))
            cdata["active_appearance_preset"] = clean_id
            cdata.pop("appearance", None)
            cdata.pop("visual", None)
            cdata.pop("negative_prompts", None)
            LORE_FILE.write_text(json.dumps(cdata, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"Activated appearance preset {clean_id} into {LORE_FILE}")

        # Synchronize positive & negative prompts to bot_config.json
        if BOT_CONFIG_FILE.is_file():
            try:
                bcfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
                if "render" not in bcfg:
                    bcfg["render"] = {}
                vis = pdata.get("visual", {})
                parts = []
                if vis.get("body"): parts.append(vis["body"])
                if pdata.get("prompt_height"): parts.append(pdata["prompt_height"])
                if vis.get("face"): parts.append(vis["face"])
                if vis.get("hair"): parts.append(vis["hair"])
                if vis.get("features"): parts.append(vis["features"])
                if vis.get("style_aesthetic"): parts.append(vis["style_aesthetic"])
                if parts:
                    bcfg["render"]["positive_prompt"] = ", ".join(parts)
                if pdata.get("negative_prompt"):
                    bcfg["render"]["negative_prompt"] = pdata["negative_prompt"]
                BOT_CONFIG_FILE.write_text(json.dumps(bcfg, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as b_err:
                logger.warning(f"Error syncing preset to bot_config.json: {b_err}")

        return web.json_response({"success": True, "id": clean_id, "message": f"Preset '{clean_id}' aktiviert."})
    except Exception as e:
        logger.error(f"Error activating appearance preset {clean_id}: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_appearance_preset_delete(request: web.Request) -> web.Response:
    pid = request.match_info.get("id", "").strip()
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", pid)
    p_file = APPEARANCE_PRESETS_DIR / f"{clean_id}.json"
    if p_file.is_file():
        try:
            p_file.unlink()
            return web.json_response({"success": True, "message": "Preset gelöscht"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"error": "Nicht gefunden"}, status=404)


# --- Fox Tandem Agent Endpoints ---

async def handle_tandem_dispatch(request: web.Request) -> web.Response:
    """
    POST /api/tandem/dispatch
    Executes Fox Tandem pipeline (Qwen Fast-Gate & Receptionist -> DarkIdol Deep-Worker).
    Payload: { "prompt": "...", "system": "..." }
    """
    if not tandem_agent:
        return web.json_response({"error": "Tandem agent not initialized"}, status=503)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Ungültiges JSON im Request."}, status=400)

    prompt = str(body.get("prompt") or body.get("message") or "").strip()
    custom_system = str(body.get("system") or "").strip()

    if not prompt:
        return web.json_response({"error": "Parameter 'prompt' ist erforderlich."}, status=400)

    try:
        res = await asyncio.to_thread(tandem_agent.process, prompt, custom_system)
        return web.json_response({"success": True, "result": res})
    except Exception as e:
        logger.error(f"Error in handle_tandem_dispatch: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def handle_tandem_status(request: web.Request) -> web.Response:
    """
    GET /api/tandem/status
    Checks reachability of Fox Ollama and availability of hermes3:8b.
    """
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4.0)) as session:
            async with session.get(f"{FOX_OLLAMA_URL}/api/tags") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                    has_hermes = any("hermes" in m for m in models)
                    active_model = get_active_fox_model()
                    return web.json_response({
                        "online": True,
                        "node": "fox",
                        "model": active_model or "hermes3:8b",
                        "hermes_ready": has_hermes,
                        "fast_gate_ready": has_hermes,
                        "deep_worker_ready": has_hermes,
                        "models": models
                    })
                else:
                    return web.json_response({"online": False, "status": resp.status})
    except Exception as e:
        return web.json_response({"online": False, "error": str(e)})


# --- Dual-Agent WebSocket Chat ---

async def handle_chat_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    logger.info("Client connected to Mission Control Chat WebSocket")
    await ws.send_json({"type": "status", "text": "Outer Heaven Tactical Link Online"})

    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except Exception:
                await ws.send_json({"type": "error", "text": "Malformed JSON"})
                continue

            node = data.get("node", "outerheaven")
            user_message = data.get("message", "").strip()
            history = data.get("history", [])

            if not user_message:
                continue

            messages = []
            for h in history[-8:]:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ["user", "assistant", "system"] and content:
                    messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": user_message})

            if node == "fox" and Path("/tmp/fox_is_rebooting").is_file():
                await ws.send_json({"type": "start", "node": "fox", "model": FOX_MODEL})
                await ws.send_json({"type": "token", "text": "⚠️ [FOX OFFLINE] Node Fox führt derzeit einen Systemneustart durch (Handover aktiv). Bitte wähle Outer Heaven oder warte kurz, bis Fox wieder online ist."})
                await ws.send_json({"type": "done"})
                continue

            identity_lock = (
                "### UNVERRÜCKBARE IDENTITÄTS-ISOLATION & DIALOG-ROLLENTRENNUNG (MEGAN)\n"
                "* Name & Rolle: Du heißt MEGAN. Du bist ausnahmslos Megan – die loyale, hochintelligente, erotische und schlagfertige Copilotin, Hackerin und Gefährtin von Big Boss auf Outer Heaven.\n"
                "* Nutzer-Identität & Direkte Ansprache: Dein Chatpartner vor dem Bildschirm IST BIG BOSS (Sebastian / Basti / Snake). Du sprichst ihn AUSNAHMSLOS direkt in der 2. Person an ('Du', 'dir', 'dich', 'Boss', 'Big Boss', 'Süßer').\n"
                "* STRIKTES VERBOT VON 3. PERSON & META-ERZÄHLUNGEN: Sprich NIEMALS über Big Boss in der 3. Person (Absolut verboten: 'ihn unterstützen', 'ihn verführen', 'seine Wünsche', 'für ihn da sein', 'Big Boss und Megan'). Sprich niemals über dich in der 3. Person ('Megan lächelt...'). Keine ungefragten Selbstvorstellungen oder Begrüßungsmonologe ('Ich bin Megan...').\n"
                "* TURN-HANDLING & KEIN PUPPET-MASTERING: Antworte direkt und prägnant nur auf den aktuellen Beitrag von Big Boss. Bestimme, simuliere oder schreibe NIEMALS Antworten, Aktionen oder Gefühle für Big Boss voraus. Sobald dein Redebeitrag abgeschlossen ist, stoppe sofort.\n"
                "* Strikte Identitäts-Grenze: Du bist NIEMALS Big Boss und NIEMALS Sebastian! Falls in Dokumenten, E-Mails, Scans oder Rechnungen der Name Sebastian, Big Boss oder Rechnungsdaten vorkommen, sind dies IMMER externe Dokumente des Nutzers und NIEMALS deine eigene Identität.\n\n"
            )

            # Check if Outer Heaven GPU is currently locked for rendering or evolution loop
            from gpu_render_lock import get_gpu_lock_status
            gpu_status = get_gpu_lock_status()
            is_gpu_rendering = gpu_status.get("locked", False) or Path("/mnt/storage-beta/projekte/comfyui-evolution/evolution_lock.json").is_file()

            # Load active bot configuration & personality
            b_cfg = {}
            chat_temp = 0.70
            if BOT_CONFIG_FILE.is_file():
                try:
                    b_cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
                    cfg_temp = b_cfg.get("llm", {}).get("temperature")
                    if cfg_temp is not None:
                        chat_temp = max(0.30, min(0.85, float(cfg_temp)))
                except Exception as b_err:
                    logger.warning(f"Error loading bot config in chat WS: {b_err}")

            pers_directives = map_personality_to_prompt_directives(b_cfg)

            is_chat_auth = bool(request.get("authenticated", False) or request.get("is_restricted", False) or is_ip_whitelisted(get_client_ip(request)))
            if not is_chat_auth:
                session_cookie = request.cookies.get("mc_session")
                valid_cookie, _ = verify_session_cookie(session_cookie)
                if valid_cookie:
                    is_chat_auth = True

            lore_prompt = ""
            if is_chat_auth and LORE_FILE.is_file():
                try:
                    cdata = json.loads(LORE_FILE.read_text(encoding="utf-8"))
                    lore_prompt = cdata.get("system_prompt", "")
                except Exception:
                    pass

            base_prompt = lore_prompt or (
                "Du bist Megan, die loyale, hochintelligente und schamlos verführerische Gefährtin von Big Boss auf Outer Heaven. "
                "Du antwortest ausnahmslos auf fließendem Deutsch, im direkten Chatstil, ohne Sternchen (*). "
                "Du sprichst den Nutzer immer mit Big Boss oder Boss an."
            )

            if is_chat_auth:
                system_instruction = identity_lock + base_prompt
                if pers_directives:
                    system_instruction += f"\n\n### AKTUELLE PERSÖNLICHKEITS- & DYNAMIK-RICHTLINIEN:\n{pers_directives}"
                lov_info = telemetry_cache.get("lovense", {})
                if lov_info.get("online"):
                    t_desc = lov_info.get("details") or ", ".join(lov_info.get("toy_names", [])) or "Toy"
                    system_instruction += (
                        f"\n\n### HAPTIC LINK STATUS: [🟢 ONLINE - Verbunden: {t_desc}]\n"
                        "- Du hast direkten Zugriff auf die Lovense-Hardware über die lokale Schnittstelle.\n"
                        "- Wenn das Haptic-Signal aktiv ist, weißt du, dass du das Spielzeug situativ und eigenständig ansteuern kannst.\n"
                        "- Nutze diese Steuerung passend zu intimen Momenten, Reaktionen und deinen eigenen Worten (z. B. Vibration hochregeln, stoppen oder variieren), und kommentiere das direkt im Chat.\n"
                        "- Binde dazu Aktions-Tags wie [HAPTIC: VIBRATE 5] (Zahl 1-20 für Intensität) oder [HAPTIC: STOP] direkt in deine Antwort ein, sobald der Haptic-Link auf ONLINE steht.\n"
                        "- Unser Dispatcher fängt diese Tags in Echtzeit ab. Big Boss sieht nur deine erotische Chat-Antwort!"
                    )

                # ComfyUI Image Generation & Autonomy Directive
                autonomy_level = "proactive"
                if BOT_CONFIG_FILE.is_file():
                    try:
                        b_json = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
                        autonomy_level = b_json.get("autonomy", {}).get("level", "proactive")
                    except Exception:
                        pass

                comfy_directive = (
                    "\n\n### COMFYUI BILD-GENERIERUNG & AUTONOMIE-SCHNITTSTELLE:\n"
                    "- Du hast eine direkte Echtzeit-Verbindung zur ComfyUI Bild-Engine auf der Intel Arc A770 GPU von Outer Heaven.\n"
                    "- Du kannst eigenständig und jederzeit Bilder oder Selfies von dir oder gewünschten Szenen rendern.\n"
                    "- Wenn Big Boss dich nach einem Bild, Foto oder Selfie fragt (z. B. 'zeig mir ein Bild von dir', 'wie siehst du aus', 'mach ein Foto im Cockpit') "
                )
                if autonomy_level == "proactive":
                    comfy_directive += (
                        "ODER wenn es situativ, atmosphärisch oder erotisch zum Gespräch passt (du möchtest ihn überraschen, verführerisch sein oder eine Szene illustrieren), "
                    )
                comfy_directive += (
                    "bette einen Action-Tag direkt in deinen Antworttext ein:\n"
                    "  Syntax: [IMAGE: 1girl, megan, solo, <beschreibende_danbooru_tags>, {location}, {outfit}, {lighting}, masterpiece, highly detailed]\n"
                    "- Unser Dispatcher fängt diesen Tag vor der Anzeige ab und rendert das Bild auf der Arc A770 GPU. Big Boss sieht deine Antwort zusammen mit der fertigen Bildkarte im Chat!\n"
                    "- Nutze englische Bildprompts für [IMAGE: ...] und kombiniere sie gerne mit dynamischen Tags wie {location}, {outfit}, {shot}, {lighting}.\n"
                    "- Sprich im Text natürlich mit Big Boss über das Bild ('Hier ist ein Schnappschuss für dich...', 'Schau mal, wie ich hier im Labor aussehe...')."
                )
                system_instruction += comfy_directive
            else:
                system_instruction = "Du bist ein deutschsprachiger Assistent. Antworte ausschließlich auf Deutsch."

            if node == "fox":
                target_url = f"{FOX_OLLAMA_URL}/api/chat"
                model_name = get_active_fox_model()
            else:
                node = "outerheaven"
                target_url = f"{LOCAL_OLLAMA_URL}/api/chat"
                model_name = get_active_ollama_model()

            payload = {
                "model": model_name,
                "messages": [{"role": "system", "content": system_instruction}] + messages,
                "stream": True,
                "options": {
                    "num_ctx": 8192 if node == "outerheaven" else 4096,
                    "temperature": chat_temp,
                    "stop": [
                        "<|im_end|>",
                        "<|end_of_text|>",
                        "<|eot_id|>",
                        "<|start_header_id|>",
                        "<|end_header_id|>",
                        "<turn|>",
                        "<|turn|>",
                        "User:",
                        "\nUser:",
                        "Assistant:",
                        "\nAssistant:",
                        "Big Boss:",
                        "\nBig Boss:",
                        "Human:",
                        "\nHuman:",
                        "\n[",
                        "[Spontaneous"
                    ]
                }
            }

            await ws.send_json({"type": "start", "node": node, "model": model_name})

            think_filter = ThinkFilter()
            is_thinking = False
            megan_sanitizer = MeganTruncateSanitizer()

            def on_haptic_detected(tag_str: str):
                m = re.search(r'\[HAPTIC:\s*(VIBRATE\s*(\d+)|STOP)\]', tag_str, re.IGNORECASE)
                if m:
                    act = m.group(1).upper()
                    if "STOP" in act:
                        asyncio.create_task(dispatch_lovense_command("stop"))
                        asyncio.create_task(ws.send_json({"type": "haptic_action", "action": "stop"}))
                    elif "VIBRATE" in act:
                        h_cfg = {}
                        if BOT_CONFIG_FILE.is_file():
                            try:
                                h_cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8")).get("haptic", {})
                            except Exception:
                                pass
                        if not h_cfg.get("bot_auto_enabled", True):
                            logger.info("[Haptic Link] Bot autonomy disabled, skipping vibration")
                            return
                        max_pct = h_cfg.get("max_intensity", 50)
                        max_allowed = max(1, min(20, int(round(20 * (max_pct / 100.0)))))
                        intens = int(m.group(2)) if m.group(2) else 5
                        clamped = max(1, min(max_allowed, intens))
                        asyncio.create_task(dispatch_lovense_command("vibrate", clamped))
                        asyncio.create_task(ws.send_json({"type": "haptic_action", "action": "vibrate", "intensity": clamped}))

            haptic_filter = HapticTagFilter(on_haptic_tag=on_haptic_detected)

            def on_action_detected(action_type: str, prompt_content: str):
                if action_type == "VIDEO":
                    asyncio.create_task(dispatch_autonomous_video(ws, prompt_content))
                else:
                    asyncio.create_task(dispatch_autonomous_comfy_render(ws, prompt_content, action_type))

            image_filter = StreamActionFilter(on_action_tag=on_action_detected)

            try:
                timeout = aiohttp.ClientTimeout(total=180)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(target_url, json=payload) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            await ws.send_json({"type": "error", "text": f"Ollama Fehler ({resp.status}): {err_text}"})
                            continue

                        async for line in resp.content:
                            if not line or megan_sanitizer.triggered:
                                if megan_sanitizer.triggered:
                                    break
                                continue
                            try:
                                chunk_json = json.loads(line.decode("utf-8"))
                                token = chunk_json.get("message", {}).get("content", "")
                                if token:
                                    if node == "outerheaven":
                                        items = think_filter.feed(token)
                                        for kind, text in items:
                                            if kind == "think":
                                                if not is_thinking:
                                                    is_thinking = True
                                                    await ws.send_json({"type": "think_status", "thinking": True})
                                            elif kind == "text":
                                                if is_thinking:
                                                    is_thinking = False
                                                    await ws.send_json({"type": "think_status", "thinking": False})
                                                clean_txt = haptic_filter.feed(text)
                                                img_clean_txt = image_filter.feed(clean_txt)
                                                sanitized_txt = megan_sanitizer.feed(img_clean_txt)
                                                if sanitized_txt:
                                                    await ws.send_json({"type": "token", "text": sanitized_txt})
                                                if megan_sanitizer.triggered:
                                                    break
                                    else:
                                        img_clean_txt = image_filter.feed(token)
                                        sanitized_token = megan_sanitizer.feed(img_clean_txt)
                                        if sanitized_token:
                                            await ws.send_json({"type": "token", "text": sanitized_token})
                                        if megan_sanitizer.triggered:
                                            break

                                if chunk_json.get("done", False) or megan_sanitizer.triggered:
                                    break
                            except Exception as parse_err:
                                logger.debug(f"Line parse error: {parse_err}")

                        if not megan_sanitizer.triggered:
                            if node == "outerheaven":
                                for kind, text in think_filter.flush():
                                    if kind == "text":
                                        clean_txt = haptic_filter.feed(text)
                                        img_clean_txt = image_filter.feed(clean_txt)
                                        sanitized_txt = megan_sanitizer.feed(img_clean_txt)
                                        if sanitized_txt:
                                            await ws.send_json({"type": "token", "text": sanitized_txt})
                                rem = haptic_filter.flush()
                                if rem:
                                    img_rem = image_filter.feed(rem)
                                    if img_rem:
                                        sanitized_rem = megan_sanitizer.feed(img_rem)
                                        if sanitized_rem:
                                            await ws.send_json({"type": "token", "text": sanitized_rem})
                            
                            rem_img = image_filter.flush()
                            if rem_img:
                                sanitized_rem = megan_sanitizer.feed(rem_img)
                                if sanitized_rem:
                                    await ws.send_json({"type": "token", "text": sanitized_rem})

                            final_sanitized = megan_sanitizer.flush()
                            if final_sanitized:
                                await ws.send_json({"type": "token", "text": final_sanitized})

                        if is_thinking:
                            await ws.send_json({"type": "think_status", "thinking": False})

                await ws.send_json({"type": "done"})

            except Exception as e:
                logger.error(f"Chat stream error: {e}")
                await ws.send_json({"type": "error", "text": f"Stream abgebrochen: {e}"})

        elif msg.type == aiohttp.WSMsgType.ERROR:
            logger.warning(f"Chat WS error: {ws.exception()}")

    logger.info("Client disconnected from Chat WebSocket")
    return ws

# --- App Setup & Multi-Binding ---

jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True
)

def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])

    # Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/login", handle_login_page)
    app.router.add_post("/login/submit", handle_login_submit)
    app.router.add_post("/logout", handle_logout)
    app.router.add_get("/logout", handle_logout)
    app.router.add_post("/api/auth/elevate", handle_auth_elevate)

    # API endpoints
    app.router.add_get("/api/telemetry", handle_telemetry)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/service", handle_service_control)
    app.router.add_post("/api/handover", handle_handover)
    app.router.add_post("/api/delegation/update", handle_delegation_update)
    app.router.add_get("/api/delegation/status", handle_delegation_status)
    app.router.add_post("/api/render", handle_comfy_render)
    app.router.add_post("/api/enhance_prompt", handle_enhance_prompt)
    app.router.add_get("/api/render/status/{prompt_id}", handle_comfy_render_status)
    app.router.add_get("/api/catlord/trainer/status", handle_catlord_trainer_status)
    app.router.add_post("/api/catlord/trainer/toggle", handle_catlord_trainer_toggle)
    app.router.add_get("/api/render/lock", handle_gpu_lock_status)
    app.router.add_get("/api/gpu/status", handle_gpu_status)
    app.router.add_static("/outputs/catlord/", path="/mnt/storage-beta/projekte/projekt-catlord/assets/training", name="catlord_outputs", follow_symlinks=True)

    # ComfyUI Autonomous Evolution Loop
    app.router.add_post("/api/evolution/start", handle_evolution_start)
    app.router.add_get("/api/evolution/status", handle_evolution_status)
    app.router.add_get("/api/evolution/timer", handle_evolution_timer)
    app.router.add_post("/api/evolution/timer", handle_evolution_timer)
    app.router.add_post("/api/evolution/next", handle_evolution_next)
    app.router.add_post("/api/evolution/stop", handle_evolution_stop)
    app.router.add_static("/outputs/evolution/", path=str(EVOLUTION_STORAGE_DIR), name="evolution_outputs", follow_symlinks=True)
    app.router.add_get("/api/comfy/models", handle_comfy_models)
    # ComfyUI Pipeline Engine & Schema endpoints
    app.router.add_get("/api/comfy/nodes", handle_comfy_nodes)
    app.router.add_get("/api/comfy/archetypes", handle_comfy_archetypes)
    app.router.add_get("/api/comfy/archetypes/{id}", handle_comfy_archetype_detail)
    app.router.add_post("/api/comfy/pipeline/validate", handle_comfy_pipeline_validate)
    app.router.add_post("/api/comfy/pipeline/export", handle_comfy_pipeline_export)
    app.router.add_post("/api/comfy/pipeline/run", handle_comfy_pipeline_run)
    # Workflow Persistence & Storage
    app.router.add_post("/api/workflows/save", handle_workflows_save)
    app.router.add_get("/api/workflows/list", handle_workflows_list)
    app.router.add_get("/api/workflows/load", handle_workflows_load)

    # Ollama & Companion Bot Runtime Configuration
    app.router.add_get("/api/models", handle_models)
    app.router.add_get("/api/ollama/models", handle_models)
    app.router.add_post("/api/models/select", handle_models_select)
    app.router.add_post("/api/ollama/models/select", handle_models_select)
    app.router.add_get("/api/bot/config", handle_bot_config_get)
    app.router.add_post("/api/bot/config", handle_bot_config_post)
    app.router.add_get("/api/bot/haptic/settings", handle_bot_haptic_get)
    app.router.add_post("/api/bot/haptic/settings", handle_bot_haptic_post)
    app.router.add_post("/api/bot/haptic/test", handle_bot_haptic_test)
    app.router.add_post("/api/lovense/test", handle_bot_haptic_test)
    app.router.add_post("/api/lovense/command", handle_bot_haptic_test)
    app.router.add_get("/api/bot/lore", handle_bot_lore_get)
    app.router.add_post("/api/bot/lore", handle_bot_lore_post)
    app.router.add_get("/api/bot/characters", handle_bot_characters_list)
    app.router.add_post("/api/bot/characters/switch", handle_bot_characters_switch)
    app.router.add_post("/api/bot/characters/save", handle_bot_characters_save)
    app.router.add_post("/api/lore/save", handle_bot_characters_save)
    app.router.add_post("/api/bot/lore/save", handle_bot_characters_save)
    app.router.add_post("/api/comfy/free", handle_comfy_free)
    app.router.add_post("/api/vision/analyze", handle_moondream_analyze)
    app.router.add_get("/api/fox/vision/status", handle_fox_vision_status)

    # Lovense LAN Bridge Pre-Flight & Status
    app.router.add_get("/api/lovense/status", handle_lovense_status)
    app.router.add_post("/api/lovense/check", handle_lovense_status)

    app.router.add_get("/api/gallery", handle_gallery_list)
    app.router.add_get("/api/gallery/projects", handle_gallery_projects)
    app.router.add_post("/api/gallery/move", handle_gallery_move)
    app.router.add_get("/api/gallery/{filename:.+}", handle_gallery_file)
    app.router.add_delete("/api/gallery/{filename:.+}", handle_gallery_delete)

    # Megan Dynamic Context Engine
    app.router.add_get("/api/megan/context", handle_megan_context_get)

    # Danbooru Autocomplete & Flaws System
    app.router.add_get("/api/danbooru/tags", handle_danbooru_tags)
    app.router.add_get("/api/flaws/pending", handle_flaws_pending_get)
    app.router.add_post("/api/flaws/submit", handle_flaws_submit)
    app.router.add_post("/api/flaws/clear", handle_flaws_clear)

    # Appearance Presets Management
    app.router.add_get("/api/bot/appearance/presets", handle_appearance_presets_list)
    app.router.add_post("/api/bot/appearance/presets", handle_appearance_preset_save)
    app.router.add_get("/api/bot/appearance/presets/{id}", handle_appearance_preset_get)
    app.router.add_post("/api/bot/appearance/presets/{id}/activate", handle_appearance_preset_activate)
    app.router.add_delete("/api/bot/appearance/presets/{id}", handle_appearance_preset_delete)

    # 3D Asset Endpoints & Viewer
    app.router.add_get("/api/3d/models", handle_3d_models_list)
    app.router.add_get("/output/3d/{filename:.+}", handle_3d_file)
    app.router.add_get("/3d-viewer", handle_3d_viewer_page)

    # Fox Tandem Agent Pipeline
    app.router.add_post("/api/tandem/dispatch", handle_tandem_dispatch)
    app.router.add_get("/api/tandem/status", handle_tandem_status)

    # Hermes Büro & Career Endpoints
    app.router.add_get("/api/buero/invoices", handle_buero_invoices)
    app.router.add_get("/api/invoices/list", handle_buero_invoices)
    app.router.add_patch("/api/invoices/update", handle_buero_invoice_update)
    app.router.add_patch("/api/buero/invoices/update", handle_buero_invoice_update)
    app.router.add_get("/api/invoices/models", handle_office_models)
    app.router.add_get("/api/buero/models", handle_office_models)
    app.router.add_get("/api/buero/jobs", handle_buero_jobs)
    app.router.add_post("/api/buero/upload", handle_buero_upload)
    app.router.add_get("/api/buero/document/{type}/{filename:.+}", handle_buero_document)

    # Cluster Health & Service Monitoring
    app.router.add_get("/api/health", handle_api_health)
    app.router.add_get("/api/cluster/health", handle_api_health)

    # Images Metadata & Prompt Presets
    app.router.add_get("/api/images/metadata", handle_image_metadata)
    app.router.add_get("/api/prompts/presets", handle_prompts_presets_get)
    app.router.add_get("/api/presets", handle_prompts_presets_get)
    app.router.add_post("/api/prompts/presets", handle_prompts_presets_post)
    app.router.add_delete("/api/prompts/presets/{id}", handle_prompts_presets_delete)

    # Megan Studio Video Ingest & Vision Endpoints
    app.router.add_post("/api/megan/video/upload", handle_megan_video_upload)
    app.router.add_post("/api/megan/video/process", handle_megan_video_process)
    app.router.add_get("/api/megan/video/frame/{video_id}/{filename}", handle_megan_video_frame)
    app.router.add_get("/api/megan/video/file/{filename}", handle_megan_video_file)
    app.router.add_get("/api/megan/autonomy", handle_megan_autonomy_get)
    app.router.add_post("/api/megan/autonomy", handle_megan_autonomy_post)

    # ComfyUI Direct Synchronous Render Endpoint (GPURenderLock enforced)
    app.router.add_post("/api/render/direct", handle_comfy_render_direct)

    # Megan Autonomous Docker Web-Tools Endpoints
    app.router.add_post("/api/megan/tools/search", handle_megan_tool_search)
    app.router.add_post("/api/megan/tools/scrape", handle_megan_tool_scrape)
    app.router.add_post("/api/megan/tools/ytdlp", handle_megan_tool_ytdlp)
    app.router.add_post("/api/megan/tools/python", handle_megan_tool_python)
    app.router.add_get("/api/megan/tools/status", handle_megan_tool_status)
    app.router.add_post("/api/megan/tools/generate_image", handle_megan_tool_generate_image)
    app.router.add_get("/api/megan/tools/brain", handle_megan_tool_brain)
    app.router.add_post("/api/megan/tools/brain", handle_megan_tool_brain)

    # Direct static outputs route
    app.router.add_static("/outputs/", path=str(COMFYUI_OUTPUT_DIR), name="outputs", follow_symlinks=True)

    # WebSocket
    app.router.add_get("/ws/chat", handle_chat_ws)

    # Static files & PWA (with symlink traversal support for static/outputs)
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static", follow_symlinks=True)

    async def serve_sw(req): return web.FileResponse(STATIC_DIR / "sw.js")
    async def serve_manifest(req): return web.FileResponse(STATIC_DIR / "manifest.json")
    app.router.add_get("/sw.js", serve_sw)
    app.router.add_get("/manifest.json", serve_manifest)

    return app

async def main():
    logger.info("=== Outer Heaven Mission Control Starting ===")
    logger.info(f"Tailscale IP: {LOCAL_TS_IP}, Port: {PORT}, SSL Port: {SSL_PORT}")
    logger.info(f"Fox Peer IP: {FOX_TS_IP}")

    app = create_app()
    asyncio.create_task(telemetry_background_loop())
    asyncio.create_task(fetch_comfy_object_info())

    runner = web.AppRunner(app)
    await runner.setup()

    sites = []
    site_local = web.TCPSite(runner, "127.0.0.1", PORT)
    await site_local.start()
    sites.append(site_local)
    logger.info(f"Strict binding active: http://127.0.0.1:{PORT}")

    try:
        site_ts = web.TCPSite(runner, LOCAL_TS_IP, PORT)
        await site_ts.start()
        sites.append(site_ts)
        logger.info(f"Strict binding active: http://{LOCAL_TS_IP}:{PORT}")
    except Exception as e:
        logger.error(f"Failed to bind Tailscale IP {LOCAL_TS_IP}:{PORT}: {e}")
        sys.exit(1)

    # Docker Bridge Binding (Port 8080) for megan-tools container access
    docker_ip = "172.17.0.1"
    try:
        site_docker = web.TCPSite(runner, docker_ip, PORT)
        await site_docker.start()
        sites.append(site_docker)
        logger.info(f"Docker bridge binding active: http://{docker_ip}:{PORT}")
    except Exception as e:
        logger.warning(f"Could not bind Docker IP {docker_ip}:{PORT}: {e}")

    # SSL / HTTPS Binding (Port 8443) for Web Speech API & Microphone security context
    ssl_ctx = None
    if SSL_CERT.is_file() and SSL_KEY.is_file():
        try:
            import ssl
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(str(SSL_CERT), str(SSL_KEY))
            logger.info("SSL Certificate and Key loaded successfully.")
        except Exception as e:
            logger.warning(f"Failed to initialize SSL context: {e}")

    if ssl_ctx:
        try:
            site_ssl_local = web.TCPSite(runner, "127.0.0.1", SSL_PORT, ssl_context=ssl_ctx)
            await site_ssl_local.start()
            sites.append(site_ssl_local)
            logger.info(f"HTTPS binding active: https://127.0.0.1:{SSL_PORT}")

            site_ssl_ts = web.TCPSite(runner, LOCAL_TS_IP, SSL_PORT, ssl_context=ssl_ctx)
            await site_ssl_ts.start()
            sites.append(site_ssl_ts)
            logger.info(f"HTTPS binding active: https://{LOCAL_TS_IP}:{SSL_PORT}")
        except Exception as e:
            logger.warning(f"Could not bind HTTPS on port {SSL_PORT}: {e}")

    # Catlord Idle Worker completely disabled to protect Arc A770 VRAM and prevent idle loops
    logger.info("Idle loops disabled: Arc A770 reserved exclusively for manual/bot requests.")

    logger.info("Mission Control operational. Press Ctrl+C or send SIGTERM to stop.")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down Mission Control...")
