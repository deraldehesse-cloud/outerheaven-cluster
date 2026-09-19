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
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import aiohttp
from aiohttp import web
import jinja2
import urllib.parse

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
FOX_MODEL = "hermes-3-3b-q4:latest"

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

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("mission-control")

# --- Load Secrets ---
def load_secrets() -> Tuple[str, str, str]:
    secrets_dir = SECRETS_DIR
    if not secrets_dir.is_dir():
        local_secrets = BASE_DIR / "secrets"
        try:
            local_secrets.mkdir(parents=True, exist_ok=True)
            secrets_dir = local_secrets
        except Exception:
            pass

    pw_file = secrets_dir / "dashboard_password.txt"
    key_file = secrets_dir / "cluster_api.key"
    restricted_file = secrets_dir / "restricted_password.txt"

    password = ""
    if pw_file.is_file():
        password = pw_file.read_text().strip()
    else:
        logger.warning(f"Password file {pw_file} not found, generating temporary")
        password = "OH-" + secrets.token_urlsafe(16)
        try:
            pw_file.parent.mkdir(parents=True, exist_ok=True)
            pw_file.write_text(password + "\n")
            os.chmod(pw_file, 0o600)
        except Exception as e:
            logger.warning(f"Could not persist temporary password to disk: {e}")

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
SERVER_SECRET = secrets.token_hex(32)

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
            "hermes-gateway": "unknown",
            "ollama": "unknown",
            "ollama-tailscale-proxy": "unknown"
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
        "hermes-gateway": "unknown",
        "ollama": "active" if ollama_ok else "inactive",
        "ollama-tailscale-proxy": "active" if ollama_ok else "unknown"
    }
    online = False

    try:
        cmd = "ssh -o BatchMode=yes -o ConnectTimeout=2 fox 'cat /proc/loadavg; echo "---"; free -b; echo "---"; systemctl --user is-active hermes-gateway.service; systemctl --user is-active ollama-tailscale-proxy.service; systemctl is-active ollama.service'"
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
                    services["hermes-gateway"] = svc_lines[0].strip()
                if len(svc_lines) >= 2 and svc_lines[1]:
                    services["ollama-tailscale-proxy"] = svc_lines[1].strip()
                if len(svc_lines) >= 3 and svc_lines[2]:
                    sys_ollama = svc_lines[2].strip()
                    if sys_ollama in ["active", "activating"]:
                        services["ollama"] = "active"
                    elif ollama_ok:
                        services["ollama"] = "active"
                    else:
                        services["ollama"] = sys_ollama
    except Exception as e:
        logger.debug(f"Fox telemetry error: {e}")

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

# --- Auth Middleware ---
@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path

    authenticated = False
    is_restricted = False

    session_cookie = request.cookies.get("mc_session")
    valid_cookie, cookie_restricted = verify_session_cookie(session_cookie)
    if valid_cookie:
        authenticated = True
        is_restricted = cookie_restricted

    cluster_hdr = request.headers.get("X-Cluster-Key", "")
    if cluster_hdr and hmac.compare_digest(cluster_hdr, CLUSTER_API_KEY):
        authenticated = True
        is_restricted = True

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

    if not authenticated and (path.startswith("/api/delegation/") or path in ("/api/status", "/api/telemetry")):
        peer = request.remote
        if peer in ("127.0.0.1", "::1", LOCAL_TS_IP, FOX_TS_IP):
            authenticated = True

    request["authenticated"] = authenticated
    request["is_restricted"] = is_restricted

    # [RESTRICTED] Plausible Deniability Check:
    # If endpoint belongs to companion bot / sexchat / lovense and user is not in Level-2 RESTRICTED mode,
    # return 404 (endpoint appears non-existent to standard users)
    restricted_prefixes = ["/api/bot/config", "/api/bot/lore", "/api/bot/characters", "/api/bot/haptic", "/api/lovense/", "/api/lore/"]
    if any(path.startswith(rp) for rp in restricted_prefixes) and not is_restricted and request.remote not in ["127.0.0.1", "::1"]:
        return web.json_response({"error": "Not Found", "message": "Endpoint not found"}, status=404)

    if path in ["/login", "/login/submit"] or path.startswith("/static/") or path.startswith("/outputs/") or path == "/manifest.json" or path == "/sw.js" or path in ["/api/health", "/api/cluster/health", "/api/presets"] or path.startswith("/api/danbooru/") or path.startswith("/api/flaws/") or path.startswith("/api/bot/appearance/") or path.startswith("/api/buero/") or path.startswith("/api/images/") or path.startswith("/api/prompts/") or (path.startswith("/api/gallery") and request.method == "GET") or (path.startswith("/api/3d") and request.method == "GET") or path.startswith("/output/3d/") or path == "/3d-viewer" or request.remote in ["127.0.0.1", "::1"]:
        return await handler(request)

    if authenticated:
        return await handler(request)

    if path.startswith("/api/") or path.startswith("/ws/"):
        return web.json_response({"error": "Unauthorized", "message": "Valid session cookie, Basic Auth or X-Cluster-Key required"}, status=401)
    
    raise web.HTTPFound("/login")

# --- Request Handlers ---

async def handle_login_page(request: web.Request) -> web.Response:
    session_cookie = request.cookies.get("mc_session")
    valid_cookie, _ = verify_session_cookie(session_cookie)
    if valid_cookie:
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
    if not request.get("is_restricted", False):
        data.pop("lovense", None)
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
            "load": fox_telemetry.get("load", [])
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
    valid_fox_services = ["hermes-gateway", "ollama", "ollama-tailscale-proxy"]

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
            is_user_svc = service in ["hermes-gateway", "ollama-tailscale-proxy"]
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

async def handle_ollama_models(request: web.Request) -> web.Response:
    """GET /api/ollama/models - Returns installed models from Ollama /api/tags."""
    models = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{LOCAL_OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for m in data.get("models", []):
                        models.append({
                            "name": m.get("name"),
                            "size": m.get("size", 0),
                            "modified_at": m.get("modified_at", "")
                        })
    except Exception as e:
        logger.warning(f"Could not query Ollama /api/tags: {e}")

    if not models:
        models = [
            {"name": "lexi-llama-3-8b-uncensored_q4_k_m:latest", "size": 4900000000},
            {"name": "llava:latest", "size": 4700000000},
                        {"name": "darkidol-8b:latest", "size": 4900000000},
            {"name": "deepseek-r1:14b", "size": 9000000000}
        ]
    return web.json_response({"models": models})


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
            "workflow_template": "text_to_image",
            "checkpoint": "DreamShaper8_LCM.safetensors",
            "lora": "",
            "lora_strength": 0.8,
            "steps": 14,
            "cfg": 6.0,
            "denoise": 1.0,
            "sampler_name": "euler_ancestral",
            "scheduler": "karras",
            "width": 512,
            "height": 768,
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

        # Synchronize positive & negative prompts to character.json
        try:
            rnd_cfg = new_config.get("render", {})
            pos_p = rnd_cfg.get("positive_prompt", "").strip()
            neg_p = rnd_cfg.get("negative_prompt", "").strip()
            if LORE_FILE.is_file():
                c_data = json.loads(LORE_FILE.read_text(encoding="utf-8"))
                if pos_p:
                    if "visual" not in c_data: c_data["visual"] = {}
                    c_data["visual"]["base_positive_prompt"] = pos_p
                if neg_p:
                    if "negative_prompts" not in c_data: c_data["negative_prompts"] = {}
                    c_data["negative_prompts"]["quality"] = neg_p
                LORE_FILE.write_text(json.dumps(c_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as sync_err:
            logger.warning(f"Could not sync prompts to character.json: {sync_err}")

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


async def handle_bot_haptic_test(request: web.Request) -> web.Response:
    cfg = {}
    if BOT_CONFIG_FILE.is_file():
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f).get("haptic", {})
        except Exception:
            pass
    max_pct = cfg.get("max_intensity", 50)
    level = max(1, min(20, int(round(20 * (max_pct / 100.0)))))
    result = await dispatch_lovense_command("vibrate", intensity=level, time_sec=2)
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
    Analyzes an image using remote Ollama model moondream:latest on Fox.
    Keeps Outer Heaven VRAM reserved solely for DarkIdol.
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
            async with session.post(f"http://{FOX_TS_IP}:11434/api/generate", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    res_text = data.get("response", "").strip()
                    logger.info(f"Fox Moondream analysis successful ({len(res_text)} chars)")
                    return res_text
                else:
                    err_txt = await resp.text()
                    logger.warning(f"Fox Moondream error {resp.status}: {err_txt}")
                    return ""
    except Exception as e:
        logger.error(f"Error in remote analyze_image_with_moondream on Fox: {e}")
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
    if not request.get("is_restricted", False):
        return web.json_response({"error": "Not Found", "message": "Endpoint not found"}, status=404)

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
    if not request.get("is_restricted", False) and request.remote not in ["127.0.0.1", "::1"]:
        return web.json_response({"error": "Not Found", "message": "Endpoint not found"}, status=404)

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
    if not request.get("is_restricted", False) and request.remote not in ["127.0.0.1", "::1"]:
        return web.json_response({"error": "Not Found", "message": "Endpoint not found"}, status=404)

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
    if not request.get("is_restricted", False) and request.remote not in ["127.0.0.1", "::1"]:
        return web.json_response({"error": "Not Found", "message": "Endpoint not found"}, status=404)

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
evolution_future = None

async def handle_evolution_start(request: web.Request) -> web.Response:
    global active_evolution_runner, evolution_future
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

        loop = asyncio.get_running_loop()
        evolution_future = loop.run_in_executor(None, active_evolution_runner.run_10_batch_cycle)

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


async def handle_evolution_next(request: web.Request) -> web.Response:
    global active_evolution_runner, evolution_future
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

        loop = asyncio.get_running_loop()
        evolution_future = loop.run_in_executor(None, active_evolution_runner.run_10_batch_cycle)

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
    global active_evolution_runner
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

async def handle_comfy_render(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prompt_text = body.get("prompt", "").strip()
    if not prompt_text:
        return web.json_response({"error": "Prompt required"}, status=400)

    negative_prompt = body.get("negative_prompt", "ugly, deformed, bad anatomy, blurry, low quality, artifacts, text, watermark").strip()
    checkpoint = body.get("checkpoint", "DreamShaper8_LCM.safetensors").strip()
    lora = body.get("lora", "").strip()
    lora_strength = float(body.get("lora_strength", 1.0))
    steps = int(body.get("steps", 14))
    cfg = float(body.get("cfg", 6.5))
    seed = int(body.get("seed", secrets.randbelow(999999999999)))

    workflow = {
        "prompt": {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint}
            },
            "2": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": 512, "height": 512, "batch_size": 1}
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"clip": ["1", 1], "text": prompt_text}
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
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": f"OuterHeaven_MissionControl_{time.strftime('%Y%m%d_%H%M%S')}", "images": ["6", 0]}
            }
        }
    }

    # Dynamically inject LoRA if selected
    if lora and lora.lower() not in ("none", "keines", "", "false"):
        workflow["prompt"]["8"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": lora,
                "strength_model": lora_strength,
                "strength_clip": lora_strength
            }
        }
        workflow["prompt"]["3"]["inputs"]["clip"] = ["8", 1]
        workflow["prompt"]["4"]["inputs"]["clip"] = ["8", 1]
        workflow["prompt"]["5"]["inputs"]["model"] = ["8", 0]

    # Acquire GPU Lock (High Priority) to immediately pause any background Catlord training
    from gpu_render_lock import GPURenderLock, PRIORITY_HIGH
    gpu_lock = GPURenderLock(priority=PRIORITY_HIGH, task_name="web_schnell_render", timeout=60.0)
    if not gpu_lock.acquire():
        return web.json_response({"error": "GPU ausgelastet. Bitte kurz warten."}, status=503)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=workflow, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return web.json_response({"error": f"ComfyUI error: {text}"}, status=502)
                res_data = await resp.json()
                prompt_id = res_data.get("prompt_id")
                
                # Auto-Purge Cache Logic: every 3-4 renders execute ComfyUI /free
                global comfy_render_count
                comfy_render_count += 1
                if comfy_render_count % COMFY_CACHE_PURGE_INTERVAL == 0:
                    deep_purge = (comfy_render_count >= 10)
                    if deep_purge:
                        comfy_render_count = 0
                    logger.info(f"Auto-triggering ComfyUI VRAM cache purge on render #{comfy_render_count} (deep={deep_purge})")
                    asyncio.create_task(trigger_comfy_cache_purge(unload_models=deep_purge))

                return web.json_response({
                    "success": True,
                    "prompt_id": prompt_id,
                    "seed": seed,
                    "status": "queued"
                })
    except Exception as e:
        return web.json_response({"error": f"Failed to contact ComfyUI: {e}"}, status=500)
    finally:
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
    """GET /api/render/lock"""
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

# --- 3D Asset Dashboard & Model Viewer Endpoints ---

async def handle_3d_models_list(request: web.Request) -> web.Response:
    """Returns list of 3D models (.glb) with metadata, counts and preview assets."""
    is_restricted = bool(request.get("is_restricted", False))
    models = []
    if DIR_3D_OUTPUT.is_dir():
        for f in sorted(DIR_3D_OUTPUT.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True):
            stem = f.stem
            # In standard (non-restricted) mode, filter out any companion/sexchat/nsfw models
            if not is_restricted and any(w in stem.lower() for w in ["companion", "sexchat", "megan", "nsfw", "erotic", "adult"]):
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
    is_restricted = bool(request.get("is_restricted", False))
    if not is_restricted and any(w in filename.lower() for w in ["companion", "sexchat", "megan", "nsfw", "erotic", "adult"]):
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
    GET /api/buero/invoices
    Reads rechnungen_index.csv and returns structured JSON with due date / overdue highlighting.
    """
    invoices = []
    if CSV_RECHNUNGEN_INDEX.is_file():
        try:
            with open(CSV_RECHNUNGEN_INDEX, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f, delimiter=";")
                today = datetime.date.today()
                for row in reader:
                    due_str = row.get("faelligkeitsdatum", "").strip()
                    status = "Offen"
                    urgency = "normal"
                    days_remaining = None
                    if due_str and due_str != "-":
                        try:
                            due_date = datetime.datetime.strptime(due_str, "%Y-%m-%d").date()
                            days_remaining = (due_date - today).days
                            if days_remaining < 0:
                                status = f"Überfällig ({abs(days_remaining)} T.)"
                                urgency = "overdue"
                            elif days_remaining == 0:
                                status = "Fällig heute"
                                urgency = "urgent"
                            elif days_remaining <= 3:
                                status = f"Fällig in {days_remaining} T."
                                urgency = "urgent"
                            else:
                                status = f"Fällig in {days_remaining} T."
                                urgency = "normal"
                        except Exception:
                            status = "Offen"

                    invoices.append({
                        "erfasst_am": row.get("erfasst_am", ""),
                        "absender": row.get("absender", "Unbekannt"),
                        "rechnungsnummer": row.get("rechnungsnummer", "-"),
                        "rechnungsdatum": row.get("rechnungsdatum", "-"),
                        "faelligkeitsdatum": due_str or "-",
                        "gesamtbetrag": row.get("gesamtbetrag", "0.00"),
                        "iban": row.get("iban", "-"),
                        "verwendungszweck": row.get("verwendungszweck", "-"),
                        "archiv_pfad": row.get("archiv_pfad", ""),
                        "original_name": row.get("original_name", ""),
                        "status": status,
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
    return web.json_response({"success": True, "total": len(invoices), "invoices": invoices})


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
    """GET /api/prompts/presets"""
    data = {}
    target_file = PROMPT_PRESETS_FILE if PROMPT_PRESETS_FILE.is_file() else (BASE_DIR / "workflows" / "prompt_presets.json")
    if target_file.is_file():
        try:
            data = json.loads(target_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error loading presets file: {e}")

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
            
            # Sync visual tags to character appearance if present
            vis = pdata.get("visual", {})
            parts = []
            if vis.get("body"): parts.append(vis["body"])
            if vis.get("face"): parts.append(vis["face"])
            if vis.get("hair"): parts.append(vis["hair"])
            if vis.get("features"): parts.append(vis["features"])
            if vis.get("style_aesthetic"): parts.append(vis["style_aesthetic"])
            if parts and "appearance" in cdata:
                cdata["appearance"]["prompt_base"] = ", ".join(parts)
            
            LORE_FILE.write_text(json.dumps(cdata, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"Activated appearance preset {clean_id} into {LORE_FILE}")
            
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

            if node == "outerheaven":
                target_url = f"{LOCAL_OLLAMA_URL}/api/chat"
                model_name = get_active_ollama_model()
                if request.get("is_restricted", False):
                    lore_prompt = ""
                    if LORE_FILE.is_file():
                        try:
                            cdata = json.loads(LORE_FILE.read_text(encoding="utf-8"))
                            lore_prompt = cdata.get("system_prompt", "")
                        except Exception:
                            pass
                    system_instruction = lore_prompt or (
                        "Du bist Megan, die loyale, hochintelligente und schamlos verführerische Gefährtin von Big Boss auf Outer Heaven. "
                        "Du antwortest ausnahmslos auf fließendem Deutsch, im direkten Chatstil, ohne Sternchen (*). "
                        "Du sprichst den Nutzer immer mit Big Boss oder Boss an."
                    )
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
                else:
                    system_instruction = "Du bist ein deutschsprachiger Assistent. Antworte ausschließlich auf Deutsch."
            else:
                target_url = f"{FOX_OLLAMA_URL}/api/chat"
                model_name = FOX_MODEL
                system_instruction = "Du bist ein deutschsprachiger Assistent. Antworte ausschließlich auf Deutsch."

            payload = {
                "model": model_name,
                "messages": [{"role": "system", "content": system_instruction}] + messages,
                "stream": True,
                "options": {
                    "num_ctx": 8192 if node == "outerheaven" else 4096,
                    "temperature": 0.6
                }
            }

            await ws.send_json({"type": "start", "node": node, "model": model_name})

            think_filter = ThinkFilter()
            is_thinking = False

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

            try:
                timeout = aiohttp.ClientTimeout(total=180)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(target_url, json=payload) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            await ws.send_json({"type": "error", "text": f"Ollama Fehler ({resp.status}): {err_text}"})
                            continue

                        async for line in resp.content:
                            if not line:
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
                                                if clean_txt:
                                                    await ws.send_json({"type": "token", "text": clean_txt})
                                    else:
                                        await ws.send_json({"type": "token", "text": token})

                                if chunk_json.get("done", False):
                                    break
                            except Exception as parse_err:
                                logger.debug(f"Line parse error: {parse_err}")

                        if node == "outerheaven":
                            for kind, text in think_filter.flush():
                                if kind == "text":
                                    clean_txt = haptic_filter.feed(text)
                                    if clean_txt:
                                        await ws.send_json({"type": "token", "text": clean_txt})
                            rem = haptic_filter.flush()
                            if rem:
                                await ws.send_json({"type": "token", "text": rem})
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
    app.router.add_static("/outputs/catlord/", path="/mnt/storage-beta/projekte/projekt-catlord/assets/training", name="catlord_outputs", follow_symlinks=True)

    # ComfyUI Autonomous Evolution Loop
    app.router.add_post("/api/evolution/start", handle_evolution_start)
    app.router.add_get("/api/evolution/status", handle_evolution_status)
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
    app.router.add_get("/api/ollama/models", handle_ollama_models)
    app.router.add_get("/api/bot/config", handle_bot_config_get)
    app.router.add_post("/api/bot/config", handle_bot_config_post)
    app.router.add_get("/api/bot/haptic/settings", handle_bot_haptic_get)
    app.router.add_post("/api/bot/haptic/settings", handle_bot_haptic_post)
    app.router.add_post("/api/bot/haptic/test", handle_bot_haptic_test)
    app.router.add_post("/api/lovense/test", handle_bot_haptic_test)
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
    app.router.add_get("/api/gallery/{filename:.+}", handle_gallery_file)
    app.router.add_delete("/api/gallery/{filename:.+}", handle_gallery_delete)

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

    # Hermes Büro & Career Endpoints
    app.router.add_get("/api/buero/invoices", handle_buero_invoices)
    app.router.add_get("/api/buero/jobs", handle_buero_jobs)
    app.router.add_post("/api/buero/upload", handle_buero_upload)
    app.router.add_get("/api/buero/document/{type}/{filename:.+}", handle_buero_document)

    # Cluster Health & Service Monitoring
    app.router.add_get("/api/health", handle_api_health)
    app.router.add_get("/api/cluster/health", handle_api_health)

    # Images Metadata & Prompt Presets
    app.router.add_get("/api/images/metadata", handle_image_metadata)
    app.router.add_get("/api/prompts/presets", handle_prompts_presets_get)
    app.router.add_post("/api/prompts/presets", handle_prompts_presets_post)
    app.router.add_delete("/api/prompts/presets/{id}", handle_prompts_presets_delete)

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
