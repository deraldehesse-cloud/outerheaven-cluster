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
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import aiohttp
from aiohttp import web
import jinja2

# --- Configuration & Paths ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
COMFYUI_OUTPUT_DIR = Path("/home/outerheaven/ComfyUI/output")
SECRETS_DIR = Path("/mnt/storage-alpha/secrets")

LOCAL_TS_IP = "<OUTERHEAVEN_IP>"
FOX_TS_IP = "<FOX_IP>"
PORT = 8080

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
    pw_file = SECRETS_DIR / "dashboard_password.txt"
    key_file = SECRETS_DIR / "cluster_api.key"

    password = ""
    if pw_file.is_file():
        password = pw_file.read_text().strip()
    else:
        logger.warning(f"Password file {pw_file} not found, generating temporary")
        password = "OH-" + secrets.token_urlsafe(16)
        pw_file.write_text(password + "\n")
        os.chmod(pw_file, 0o600)

    cluster_key = ""
    if key_file.is_file():
        cluster_key = key_file.read_text().strip()
    else:
        logger.warning(f"Cluster API key {key_file} not found, generating temporary")
        cluster_key = secrets.token_hex(32)
        key_file.write_text(cluster_key + "\n")
        os.chmod(key_file, 0o600)

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
    "last_update": 0
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

    if path in ["/login", "/login/submit"] or path.startswith("/static/") or path == "/manifest.json" or path == "/sw.js":
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

    valid_oh_services = ["comfyui", "hermes-telegram", "ollama", "ollama-model-watcher"]
    valid_fox_services = ["hermes-gateway", "ollama", "ollama-tailscale-proxy"]

    if node == "outerheaven":
        if service not in valid_oh_services:
            return web.json_response({"error": f"Invalid service for outerheaven. Allowed: {valid_oh_services}"}, status=400)

        is_user_svc = service in ["comfyui", "hermes-telegram"]
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

# --- ComfyUI Render Bridge & Gallery ---

async def handle_comfy_render(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    prompt_text = body.get("prompt", "").strip()
    if not prompt_text:
        return web.json_response({"error": "Prompt required"}, status=400)

    negative_prompt = body.get("negative_prompt", "ugly, deformed, bad anatomy, blurry, low quality, artifacts, text, watermark").strip()
    steps = int(body.get("steps", 14))
    cfg = float(body.get("cfg", 6.5))
    seed = int(body.get("seed", secrets.randbelow(999999999999)))

    workflow = {
        "prompt": {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "DreamShaper8_LCM.safetensors"}
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
                "inputs": {"filename_prefix": "OuterHeaven_MissionControl", "images": ["6", 0]}
            }
        }
    }

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
                    if "7" in outputs and "images" in outputs["7"]:
                        imgs = outputs["7"]["images"]
                        if imgs:
                            filename = imgs[0].get("filename")
                            return web.json_response({
                                "completed": True,
                                "filename": filename,
                                "url": f"/api/gallery/{filename}"
                            })
                return web.json_response({"completed": False, "status": "processing"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_gallery_list(request: web.Request) -> web.Response:
    images = []
    if COMFYUI_OUTPUT_DIR.is_dir():
        for f in COMFYUI_OUTPUT_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                try:
                    stat = f.stat()
                    images.append({
                        "filename": f.name,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "url": f"/api/gallery/{f.name}"
                    })
                except Exception:
                    pass
    images.sort(key=lambda x: x["mtime"], reverse=True)
    return web.json_response({"images": images[:50]})

async def handle_gallery_file(request: web.Request) -> web.Response:
    filename = request.match_info.get("filename", "")
    if not filename or os.path.basename(filename) != filename:
        return web.HTTPBadRequest(text="Invalid filename")

    file_path = COMFYUI_OUTPUT_DIR / filename
    if not file_path.is_file():
        return web.HTTPNotFound(text="Image not found")

    return web.FileResponse(file_path)

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
                    "Du bist Hermes Fox, der KI-Agent auf Node Fox (HP EliteDesk, Tailscale <FOX_IP>). "
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
    app.router.add_post("/api/service", handle_service_control)
    app.router.add_post("/api/handover", handle_handover)
    app.router.add_post("/api/render", handle_comfy_render)
    app.router.add_get("/api/render/status/{prompt_id}", handle_comfy_render_status)
    app.router.add_get("/api/gallery", handle_gallery_list)
    app.router.add_get("/api/gallery/{filename}", handle_gallery_file)

    # WebSocket
    app.router.add_get("/ws/chat", handle_chat_ws)

    # Static files & PWA
    app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    async def serve_sw(req): return web.FileResponse(STATIC_DIR / "sw.js")
    async def serve_manifest(req): return web.FileResponse(STATIC_DIR / "manifest.json")
    app.router.add_get("/sw.js", serve_sw)
    app.router.add_get("/manifest.json", serve_manifest)

    return app

async def main():
    logger.info("=== Outer Heaven Mission Control Starting ===")
    logger.info(f"Tailscale IP: {LOCAL_TS_IP}, Port: {PORT}")
    logger.info(f"Fox Peer IP: {FOX_TS_IP}")

    app = create_app()
    asyncio.create_task(telemetry_background_loop())

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
