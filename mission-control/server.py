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

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("mission-control")

# --- Load Secrets ---
def load_secrets() -> Tuple[str, str]:
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

    return password, cluster_key

DASHBOARD_PASSWORD, CLUSTER_API_KEY = load_secrets()
SERVER_SECRET = secrets.token_hex(32)

def generate_session_cookie() -> str:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SERVER_SECRET.encode(),
        f"valid_session:{timestamp}:{DASHBOARD_PASSWORD}".encode(),
        hashlib.sha256
    ).hexdigest()
    return f"{timestamp}.{signature}"

def verify_session_cookie(cookie: Optional[str]) -> bool:
    if not cookie or "." not in cookie:
        return False
    try:
        ts_str, signature = cookie.split(".", 1)
        ts = int(ts_str)
        # Session valid for 7 days
        if time.time() - ts > 7 * 86400:
            return False
        expected = hmac.new(
            SERVER_SECRET.encode(),
            f"valid_session:{ts_str}:{DASHBOARD_PASSWORD}".encode(),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False

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
            "systemctl", "--user", "is-active", "comfyui.service", "hermes-telegram.service",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        lines = out.decode().strip().split("\n")
        if len(lines) >= 1 and lines[0]: services["comfyui"] = lines[0]
        if len(lines) >= 2 and lines[1]: services["hermes-telegram"] = lines[1]
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

    return {
        "node": "outerheaven",
        "ip": LOCAL_TS_IP,
        "online": True,
        "load": load,
        "ram": ram,
        "gpu": gpu,
        "services": services,
        "models": models
    }

async def get_fox_telemetry(session: aiohttp.ClientSession) -> Dict[str, Any]:
    models = []
    ollama_ok = False
    try:
        async with session.get(f"{FOX_OLLAMA_URL}/api/ps", timeout=aiohttp.ClientTimeout(total=1.2)) as resp:
            if resp.status == 200:
                ollama_ok = True
                ps_data = await resp.json()
                models = [m.get("name") for m in ps_data.get("models", [])]
    except Exception:
        pass

    load = [0.0, 0.0, 0.0]
    ram = {"total": 0, "used": 0, "free": 0, "percent": 0.0}
    services = {"hermes-gateway": "unknown", "ollama": "active" if ollama_ok else "inactive", "ollama-tailscale-proxy": "active" if ollama_ok else "unknown"}
    online = False

    try:
        cmd = "ssh -o BatchMode=yes -o ConnectTimeout=2 fox 'cat /proc/loadavg; echo \"---\"; free -b; echo \"---\"; systemctl --user is-active hermes-gateway.service; systemctl --user is-active ollama-tailscale-proxy.service'"
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        if proc.returncode == 0:
            online = True
            blocks = out.decode().strip().split("---\n")
            if len(blocks) >= 1:
                p = blocks[0].strip().split()
                if len(p) >= 3:
                    load = [float(p[0]), float(p[1]), float(p[2])]
            if len(blocks) >= 2:
                for line in blocks[1].strip().split("\n"):
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
                svc_lines = blocks[2].strip().split("\n")
                if len(svc_lines) >= 1 and svc_lines[0]:
                    services["hermes-gateway"] = svc_lines[0].strip()
                if len(svc_lines) >= 2 and svc_lines[1]:
                    services["ollama-tailscale-proxy"] = svc_lines[1].strip()
    except Exception as e:
        logger.debug(f"Fox telemetry error: {e}")

    is_reb = Path("/tmp/fox_is_rebooting").is_file()
    return {
        "node": "fox",
        "ip": FOX_TS_IP,
        "online": online and not is_reb,
        "rebooting": is_reb,
        "load": load,
        "ram": ram,
        "services": services,
        "models": models
    }

async def telemetry_background_loop():
    logger.info("Starting telemetry background monitoring loop")
    timeout = aiohttp.ClientTimeout(total=2.5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
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
                telemetry_cache["last_update"] = time.time()
            except Exception as e:
                logger.error(f"Telemetry loop error: {e}")
            await asyncio.sleep(2.5)

# --- Think Tag Filter ---
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

    if path in ["/login", "/login/submit"] or path.startswith("/static/") or path.startswith("/outputs/") or path.startswith("/output/3d/") or path == "/manifest.json" or path == "/sw.js" or path == "/3d-viewer" or (path.startswith("/api/gallery") and request.method == "GET") or (path.startswith("/api/3d") and request.method == "GET") or request.remote in ["127.0.0.1", "::1"]:
        return await handler(request)

    authenticated = False

    cluster_hdr = request.headers.get("X-Cluster-Key", "")
    if cluster_hdr and hmac.compare_digest(cluster_hdr, CLUSTER_API_KEY):
        authenticated = True

    if not authenticated:
        session_cookie = request.cookies.get("mc_session")
        if verify_session_cookie(session_cookie):
            authenticated = True

    if not authenticated:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_hdr[6:]).decode("utf-8")
                _, pw = decoded.split(":", 1)
                if hmac.compare_digest(pw, DASHBOARD_PASSWORD):
                    authenticated = True
            except Exception:
                pass

    if not authenticated and (path.startswith("/api/delegation/") or path in ("/api/status", "/api/telemetry")):
        peer = request.remote
        if peer in ("127.0.0.1", "::1", LOCAL_TS_IP, FOX_TS_IP):
            authenticated = True

    if authenticated:
        return await handler(request)

    if path.startswith("/api/") or path.startswith("/ws/"):
        return web.json_response({"error": "Unauthorized", "message": "Valid session cookie, Basic Auth or X-Cluster-Key required"}, status=401)
    
    raise web.HTTPFound("/login")

# --- Request Handlers ---

async def handle_login_page(request: web.Request) -> web.Response:
    session_cookie = request.cookies.get("mc_session")
    if verify_session_cookie(session_cookie):
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
    if hmac.compare_digest(password, DASHBOARD_PASSWORD):
        cookie = generate_session_cookie()
        resp = web.json_response({"success": True, "redirect": "/"})
        resp.set_cookie("mc_session", cookie, max_age=7*86400, httponly=True, samesite="Lax")
        return resp
    else:
        return web.json_response({"success": False, "error": "Ungültiges Zugangspasswort"}, status=403)

async def handle_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/login")
    resp.del_cookie("mc_session")
    return resp

async def handle_index(request: web.Request) -> web.Response:
    template = jinja_env.get_template("index.html")
    return web.Response(text=template.render(
        outerheaven_ip=LOCAL_TS_IP,
        fox_ip=FOX_TS_IP,
        local_model=LOCAL_MODEL,
        fox_model=FOX_MODEL
    ), content_type="text/html")

async def handle_telemetry(request: web.Request) -> web.Response:
    return web.json_response(telemetry_cache)


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

        is_user_svc = service in ["comfyui", "hermes-telegram", "cluster-queue-worker"]
        if is_user_svc:
            cmd = ["systemctl", "--user", action, f"{service}.service"]
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

        is_user_svc = service in ["hermes-gateway", "ollama-tailscale-proxy"]
        svc_prefix = "--user " if is_user_svc else ""
        ssh_cmd = f"ssh -o BatchMode=yes -o ConnectTimeout=5 fox 'systemctl {svc_prefix}{action} {service}.service'"

        proc = await asyncio.create_subprocess_shell(
            ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
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

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{COMFYUI_URL}/prompt", json=workflow, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    return web.json_response({"error": f"ComfyUI error: {text}"}, status=502)
                res_data = await resp.json()
                prompt_id = res_data.get("prompt_id")
                return web.json_response({
                    "success": True,
                    "prompt_id": prompt_id,
                    "seed": seed,
                    "status": "queued"
                })
    except Exception as e:
        return web.json_response({"error": f"Failed to contact ComfyUI: {e}"}, status=500)

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
        "model": LOCAL_MODEL,
        "prompt": prompt_query,
        "stream": False,
        "options": {
            "temperature": 0.6,
            "num_predict": 350,
            "stop": ["}\n", "```"]
        }
    }

    try:
        timeout = aiohttp.ClientTimeout(total=60)
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
    models = []
    if DIR_3D_OUTPUT.is_dir():
        for f in sorted(DIR_3D_OUTPUT.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True):
            stem = f.stem
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

                clean_title = stem.replace("BigBossHaven_3D_", "").replace("Megan_Companion_", "Haven_").replace("_", " ")

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
                model_name = LOCAL_MODEL
                system_instruction = (
                    "Du bist Hermes Outer Heaven, der taktische KI-Agent auf outerheaven (HP Z620 Dual Xeon, Sparkle Arc A770 16GB). "
                    "Antworte präzise, professionell, im Stil von Mission Control. Deutsch bevorzugt."
                )
            else:
                target_url = f"{FOX_OLLAMA_URL}/api/chat"
                model_name = FOX_MODEL
                system_instruction = (
                    f"Du bist Hermes Fox, der KI-Agent auf Node Fox (HP EliteDesk, Tailscale {FOX_TS_IP}). "
                    "Antworte präzise, taktische Unterstützung. Deutsch bevorzugt."
                )

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
                                                await ws.send_json({"type": "token", "text": text})
                                    else:
                                        await ws.send_json({"type": "token", "text": token})

                                if chunk_json.get("done", False):
                                    break
                            except Exception as parse_err:
                                logger.debug(f"Line parse error: {parse_err}")

                        if node == "outerheaven":
                            for kind, text in think_filter.flush():
                                if kind == "text":
                                    await ws.send_json({"type": "token", "text": text})
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
    app.router.add_get("/api/comfy/models", handle_comfy_models)
    # ComfyUI Pipeline Engine & Schema endpoints
    app.router.add_get("/api/comfy/nodes", handle_comfy_nodes)
    app.router.add_get("/api/comfy/archetypes", handle_comfy_archetypes)
    app.router.add_get("/api/comfy/archetypes/{id}", handle_comfy_archetype_detail)
    app.router.add_post("/api/comfy/pipeline/validate", handle_comfy_pipeline_validate)
    app.router.add_post("/api/comfy/pipeline/export", handle_comfy_pipeline_export)
    app.router.add_post("/api/comfy/pipeline/run", handle_comfy_pipeline_run)

    # Lovense LAN Bridge Pre-Flight & Status
    app.router.add_get("/api/lovense/status", handle_lovense_status)
    app.router.add_post("/api/lovense/check", handle_lovense_status)

    app.router.add_get("/api/gallery", handle_gallery_list)
    app.router.add_get("/api/gallery/{filename:.+}", handle_gallery_file)
    app.router.add_delete("/api/gallery/{filename:.+}", handle_gallery_delete)

    # 3D Asset Endpoints & Viewer
    app.router.add_get("/api/3d/models", handle_3d_models_list)
    app.router.add_get("/output/3d/{filename:.+}", handle_3d_file)
    app.router.add_get("/3d-viewer", handle_3d_viewer_page)

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
