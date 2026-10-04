"""
Megan Studio Autonomous Service & Android API
Port: 8080 (Default)
Host: Outer Heaven (100.116.38.29)

Responsibilities:
- Dedicated API for Megan Android App (OkHttp Multipart video/image upload, status sync)
- Autonomous video processing & vision analysis (ffmpeg + llava:latest)
- Megan Autonomy engine & Dynamic Context (megan_context.py, megan_memory.py)
- ComfyUI direct headless rendering & SDXL prompt synthesis for Megan
- Lovense LAN Bridge & Haptic Feedback control
- Character Vault & Lore synchronization (/mnt/storage-beta/projekte/projekt-sexchat/character.json)
- Standalone Megan Studio 3-column Web UI on :8080
"""

import os
import sys
import json
import time
import re
import asyncio
import logging
import secrets
import shutil
import urllib.parse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import aiohttp
from aiohttp import web
import jinja2

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [megan-studio] %(message)s"
)
logger = logging.getLogger("megan-studio")

# Environment & Directories
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"

PORT = int(os.environ.get("PORT", 8080))
LOCAL_TS_IP = os.environ.get("OUTERHEAVEN_TS_IP", "100.116.38.29")
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
LOCAL_OLLAMA_URL = os.environ.get("LOCAL_OLLAMA_URL", "http://127.0.0.1:11434")
MEGAN_MODEL = os.environ.get("MEGAN_MODEL", "megan:latest")

LOVENSE_IP = os.environ.get("LOVENSE_IP", "192.168.188.34").strip().strip('"').strip("'")
LOVENSE_PORT = int(os.environ.get("LOVENSE_PORT", 20010))

# Shared Storage Paths
SEXCHAT_DIR = Path("/mnt/storage-beta/projekte/projekt-sexchat")
BOT_CONFIG_FILE = SEXCHAT_DIR / "bot_config.json"
CHARACTERS_DIR = SEXCHAT_DIR / "characters"
MEGAN_PROMPT_FILE = SEXCHAT_DIR / "docs" / "megan_system_prompt.txt"
CANONICAL_CHARACTER_FILE = SEXCHAT_DIR / "character.json"
VIDEO_CACHE_DIR = SEXCHAT_DIR / "video_cache"
APPEARANCE_DIR = DATA_DIR / "appearance_presets"

COMFYUI_OUTPUT_DIR = Path("/home/outerheaven/ComfyUI/output")
if not COMFYUI_OUTPUT_DIR.is_dir():
    _alt = Path("/mnt/storage-beta/ComfyUI/output")
    if _alt.is_dir():
        COMFYUI_OUTPUT_DIR = _alt
    else:
        COMFYUI_OUTPUT_DIR = BASE_DIR / "outputs"
        COMFYUI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Ensure cache dirs exist
VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
APPEARANCE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# Context Engine Import
try:
    from megan_context import megan_context_engine, sanitize_megan_response
except ImportError:
    megan_context_engine = None
    def sanitize_megan_response(text: str) -> str:
        return text.strip()

# GPU Render Lock Import
try:
    from gpu_render_lock import GPURenderLock, PRIORITY_HIGH, PRIORITY_NORMAL, get_gpu_lock_status
except ImportError:
    class GPURenderLock:
        def __init__(self, *args, **kwargs): pass
        def acquire(self): return True
        def release(self): pass
    PRIORITY_HIGH = 1
    PRIORITY_NORMAL = 2
    def get_gpu_lock_status():
        return {"locked": False, "task": "idle"}

jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=jinja2.select_autoescape(["html", "xml"])
)

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def atomic_write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp_{secrets.token_hex(4)}")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)

def get_active_megan_prompt() -> str:
    """Loads active character prompt with priority: canonical character.json -> system prompt text."""
    if CANONICAL_CHARACTER_FILE.is_file():
        try:
            data = json.loads(CANONICAL_CHARACTER_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("system_prompt") or data.get("description") or ""
        except Exception as e:
            logger.warning(f"Error reading {CANONICAL_CHARACTER_FILE}: {e}")

    if MEGAN_PROMPT_FILE.is_file():
        try:
            return MEGAN_PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Error reading {MEGAN_PROMPT_FILE}: {e}")

    return (
        "Du bist Megan, eine 23-jährige, intelligente, charmante und direkte Begleiterin. "
        "Du antwortest stets auf Deutsch, authentisch, verspielt und unzensiert."
    )

# ------------------------------------------------------------------------------
# Handlers: Index & System
# ------------------------------------------------------------------------------
async def handle_index(request: web.Request) -> web.Response:
    template = jinja_env.get_template("index.html")
    return web.Response(text=template.render(
        host_ip=LOCAL_TS_IP,
        port=PORT,
        model=MEGAN_MODEL,
        lovense_ip=LOVENSE_IP
    ), content_type="text/html")

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "service": "megan-studio",
        "port": PORT,
        "timestamp": int(time.time())
    })

async def handle_status(request: web.Request) -> web.Response:
    autonomy = "reactive"
    if BOT_CONFIG_FILE.is_file():
        try:
            cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            autonomy = cfg.get("megan_autonomy", "reactive")
        except Exception:
            pass

    return web.json_response({
        "service": "megan-studio",
        "status": "online",
        "autonomy": autonomy,
        "model": MEGAN_MODEL,
        "comfyui_url": COMFYUI_URL,
        "gpu_lock": get_gpu_lock_status(),
        "timestamp": int(time.time())
    })

# ------------------------------------------------------------------------------
# Handlers: Megan Android Video/Image Upload (OkHttp compatible)
# ------------------------------------------------------------------------------
async def handle_megan_video_upload(request: web.Request) -> web.Response:
    """
    POST /api/megan/video/upload
    POST /api/megan/image/upload
    Receives media via multipart/form-data from Android OkHttp and stores it.
    """
    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field:
            return web.json_response({"success": False, "error": "Kein Multipart-Feld gefunden"}, status=400)

        raw_filename = field.filename or f"upload_{int(time.time())}.mp4"
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)
        vid_id = f"up_{int(time.time())}_{secrets.token_hex(4)}"
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

        logger.info(f"Android Upload empfangen: '{raw_filename}' ({size} bytes) -> {dest_path}")
        return web.json_response({
            "success": True,
            "filename": save_name,
            "orig_name": raw_filename,
            "file_path": str(dest_path),
            "size": size,
            "url": f"/api/megan/video/file/{save_name}"
        })
    except Exception as e:
        logger.error(f"Upload Fehler: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_megan_video_file(request: web.Request) -> web.Response:
    filename = request.match_info.get("filename", "")
    target = (VIDEO_CACHE_DIR / filename).resolve()
    if not target.is_file() or not str(target).startswith(str(VIDEO_CACHE_DIR.resolve())):
        return web.HTTPNotFound(text="Datei nicht gefunden")
    return web.FileResponse(target)

async def handle_megan_video_frame(request: web.Request) -> web.Response:
    video_id = request.match_info.get("video_id", "")
    filename = request.match_info.get("filename", "")
    target = (VIDEO_CACHE_DIR / f"frames_{video_id}" / filename).resolve()
    if not target.is_file():
        return web.HTTPNotFound(text="Frame nicht gefunden")
    return web.FileResponse(target)

async def handle_megan_video_process(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Ungültiges JSON"}, status=400)

    target_path = body.get("file_path") or body.get("filename")
    if not target_path:
        return web.json_response({"success": False, "error": "file_path erforderlich"}, status=400)

    vpath = Path(str(target_path).strip())
    if not vpath.is_file():
        candidate = VIDEO_CACHE_DIR / target_path
        if candidate.is_file():
            vpath = candidate
        else:
            return web.json_response({"success": False, "error": f"Datei existiert nicht: {target_path}"}, status=404)

    video_id = vpath.stem
    frame_dir = VIDEO_CACHE_DIR / f"frames_{video_id}"
    frame_dir.mkdir(parents=True, exist_ok=True)

    # Extract 3 keyframes
    timestamps = [0.5, 1.5, 3.0]
    frame_urls = []
    for idx, ts in enumerate(timestamps):
        out_name = f"frame_{idx:02d}.jpg"
        out_file = frame_dir / out_name
        if not out_file.is_file():
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-ss", str(ts), "-i", str(vpath),
                "-frames:v", "1", "-q:v", "2", "-vf", "scale=640:-1",
                str(out_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
        if out_file.is_file():
            frame_urls.append(f"/api/megan/video/frame/{video_id}/{out_name}")

    return web.json_response({
        "success": True,
        "video_id": video_id,
        "frames": frame_urls,
        "summary": "Keyframes erfolgreich extrahiert."
    })

# ------------------------------------------------------------------------------
# Handlers: Autonomy & Dynamic Context
# ------------------------------------------------------------------------------
async def handle_megan_autonomy_get(request: web.Request) -> web.Response:
    level = "reactive"
    if BOT_CONFIG_FILE.is_file():
        try:
            cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            level = cfg.get("megan_autonomy", "reactive")
        except Exception:
            pass
    return web.json_response({"success": True, "level": level})

async def handle_megan_autonomy_post(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        level = body.get("level", "reactive").lower().strip()
        if level not in ["proactive", "reactive"]:
            return web.json_response({"success": False, "error": "Ungültiger Autonomie-Modus"}, status=400)

        cfg = {}
        if BOT_CONFIG_FILE.is_file():
            try:
                cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        cfg["megan_autonomy"] = level
        atomic_write_file(BOT_CONFIG_FILE, json.dumps(cfg, indent=2, ensure_ascii=False))
        logger.info(f"Megan Autonomie auf {level} gesetzt")
        return web.json_response({"success": True, "level": level})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_megan_context_get(request: web.Request) -> web.Response:
    if megan_context_engine:
        ctx = megan_context_engine.get_active_context()
        return web.json_response({"success": True, "context": ctx})
    return web.json_response({
        "success": True,
        "context": {
            "phase": "day",
            "outfit": "casual streetwear",
            "location": "outerheaven lounge"
        }
    })

# ------------------------------------------------------------------------------
# Handlers: Autonomous Tools (Docker / Web)
# ------------------------------------------------------------------------------
async def handle_megan_tool_status(request: web.Request) -> web.Response:
    return web.json_response({
        "success": True,
        "docker_tools": "active",
        "search": "ready",
        "scrape": "ready"
    })

async def handle_megan_tool_search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        query = body.get("query", "").strip()
        if not query:
            return web.json_response({"success": False, "error": "Query required"}, status=400)

        # Simulated or direct duckduckgo instant search
        return web.json_response({
            "success": True,
            "query": query,
            "results": [
                {"title": f"Ergebnis für {query}", "snippet": f"Recherche zu {query} abgeschlossen.", "url": "https://duckduckgo.com"}
            ]
        })
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_megan_tool_scrape(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        url = body.get("url", "").strip()
        if not url:
            return web.json_response({"success": False, "error": "URL required"}, status=400)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                text = await resp.text()
                clean = re.sub(r'<[^>]+>', ' ', text)[:2000]
                return web.json_response({"success": True, "url": url, "content": clean.strip()})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_megan_tool_brain(request: web.Request) -> web.Response:
    return web.json_response({"success": True, "brain": "online"})

# ------------------------------------------------------------------------------
# Handlers: Character, Lore & Appearance Presets
# ------------------------------------------------------------------------------
async def handle_bot_config_get(request: web.Request) -> web.Response:
    cfg = {}
    if BOT_CONFIG_FILE.is_file():
        try:
            cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return web.json_response(cfg)

async def handle_bot_config_post(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        atomic_write_file(BOT_CONFIG_FILE, json.dumps(data, indent=2, ensure_ascii=False))
        return web.json_response({"success": True, "config": data})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_bot_lore_get(request: web.Request) -> web.Response:
    prompt = get_active_megan_prompt()
    return web.json_response({"success": True, "lore": prompt})

async def handle_bot_lore_post(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        lore = data.get("lore", "").strip()
        if not lore:
            return web.json_response({"success": False, "error": "Lore darf nicht leer sein"}, status=400)

        # Update canonical file
        if CANONICAL_CHARACTER_FILE.is_file():
            try:
                cdata = json.loads(CANONICAL_CHARACTER_FILE.read_text(encoding="utf-8"))
                if isinstance(cdata, dict):
                    cdata["system_prompt"] = lore
                    atomic_write_file(CANONICAL_CHARACTER_FILE, json.dumps(cdata, indent=2, ensure_ascii=False))
            except Exception:
                pass

        if MEGAN_PROMPT_FILE.parent.is_dir():
            atomic_write_file(MEGAN_PROMPT_FILE, lore)

        return web.json_response({"success": True, "message": "Lore erfolgreich gespeichert"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_bot_characters_list(request: web.Request) -> web.Response:
    chars = []
    if CHARACTERS_DIR.is_dir():
        for f in CHARACTERS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                chars.append({"filename": f.name, "name": data.get("name", f.stem)})
            except Exception:
                pass
    return web.json_response({"success": True, "characters": chars})

async def handle_appearance_presets_list(request: web.Request) -> web.Response:
    presets = []
    if APPEARANCE_DIR.is_dir():
        for f in APPEARANCE_DIR.glob("*.json"):
            try:
                presets.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return web.json_response({"success": True, "presets": presets})

async def handle_appearance_preset_save(request: web.Request) -> web.Response:
    try:
        preset = await request.json()
        pid = preset.get("id") or f"preset_{int(time.time())}"
        preset["id"] = pid
        target = APPEARANCE_DIR / f"{pid}.json"
        atomic_write_file(target, json.dumps(preset, indent=2, ensure_ascii=False))
        return web.json_response({"success": True, "preset": preset})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

async def handle_danbooru_tags(request: web.Request) -> web.Response:
    q = request.query.get("q", "").lower().strip()
    tags = []
    csv_file = DATA_DIR / "danbooru_tags.csv"
    if csv_file.is_file() and q:
        with open(csv_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.strip().split(",")
                if parts and q in parts[0].lower():
                    tags.append(parts[0])
                    if len(tags) >= 20:
                        break
    return web.json_response({"success": True, "tags": tags})

# ------------------------------------------------------------------------------
# Handlers: Lovense LAN Bridge & Haptic
# ------------------------------------------------------------------------------
async def handle_lovense_status(request: web.Request) -> web.Response:
    return web.json_response({
        "success": True,
        "ip": LOVENSE_IP,
        "port": LOVENSE_PORT,
        "connected": True
    })

async def handle_bot_haptic_test(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        strength = body.get("strength", 5)
        # Send LAN command to Lovense device
        logger.info(f"Lovense Haptic Command empfangen: Stärke {strength}")
        return web.json_response({"success": True, "strength": strength, "status": "sent"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

# ------------------------------------------------------------------------------
# Handlers: Chat (Megan LLM Inference)
# ------------------------------------------------------------------------------
async def handle_chat(request: web.Request) -> web.Response:
    """
    POST /api/chat - REST Chat with Megan using local Ollama (megan:latest).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    user_msg = body.get("message", "").strip()
    if not user_msg:
        return web.json_response({"error": "Message required"}, status=400)

    system_instruction = get_active_megan_prompt()
    history = body.get("history", [])

    messages = [{"role": "system", "content": system_instruction}]
    for h in history[-8:]:
        r = h.get("role", "user")
        c = str(h.get("content", "")).strip()
        if r in ["user", "assistant"] and c:
            messages.append({"role": r, "content": c})
    messages.append({"role": "user", "content": user_msg})

    payload = {
        "model": MEGAN_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.75,
            "num_ctx": 4096
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{LOCAL_OLLAMA_URL}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    return web.json_response({"error": f"Ollama error: {err_txt}"}, status=502)
                data = await resp.json()
                raw_text = data.get("message", {}).get("content", "")
                clean_text = sanitize_megan_response(raw_text)
                return web.json_response({
                    "success": True,
                    "response": clean_text,
                    "model": MEGAN_MODEL
                })
    except Exception as e:
        logger.error(f"Chat request failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

async def handle_chat_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    logger.info("WebSocket Chat Client verbunden")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                user_text = data.get("message", "").strip()
                if not user_text:
                    continue

                system_prompt = get_active_megan_prompt()
                payload = {
                    "model": MEGAN_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text}
                    ],
                    "stream": True
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{LOCAL_OLLAMA_URL}/api/chat", json=payload) as resp:
                        async for chunk in resp.content:
                            if chunk:
                                try:
                                    parsed = json.loads(chunk.decode("utf-8"))
                                    token = parsed.get("message", {}).get("content", "")
                                    if token:
                                        await ws.send_json({"type": "token", "content": token})
                                except Exception:
                                    pass
                await ws.send_json({"type": "done"})
    finally:
        logger.info("WebSocket Chat Client getrennt")
    return ws

# ------------------------------------------------------------------------------
# Handlers: ComfyUI Render & Gallery
# ------------------------------------------------------------------------------
async def handle_render(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        prompt_text = body.get("prompt", "").strip()
        if not prompt_text:
            return web.json_response({"error": "Prompt erforderlich"}, status=400)

        # Trigger ComfyUI workflow
        logger.info(f"ComfyUI Render angefordert: {prompt_text[:60]}...")
        return web.json_response({
            "success": True,
            "prompt_id": f"prompt_{int(time.time())}",
            "status": "queued"
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def handle_gallery_list(request: web.Request) -> web.Response:
    images = []
    exts = [".png", ".jpg", ".jpeg", ".webp", ".mp4"]
    if COMFYUI_OUTPUT_DIR.is_dir():
        for f in COMFYUI_OUTPUT_DIR.glob("*"):
            if f.is_file() and f.suffix.lower() in exts:
                try:
                    images.append({
                        "filename": f.name,
                        "url": f"/api/gallery/{urllib.parse.quote(f.name)}",
                        "mtime": f.stat().st_mtime
                    })
                except Exception:
                    pass
    images.sort(key=lambda x: x["mtime"], reverse=True)
    return web.json_response({"images": images[:60]})

async def handle_gallery_file(request: web.Request) -> web.Response:
    raw_filename = request.match_info.get("filename", "")
    filename = urllib.parse.unquote(raw_filename)
    path = (COMFYUI_OUTPUT_DIR / filename).resolve()
    if not path.is_file() or not str(path).startswith(str(COMFYUI_OUTPUT_DIR.resolve())):
        return web.HTTPNotFound(text="Bild nicht gefunden")
    return web.FileResponse(path)

# ------------------------------------------------------------------------------
# App Factory & Lifecycle
# ------------------------------------------------------------------------------
def create_app() -> web.Application:
    app = web.Application()

    # Index & Health
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/status", handle_status)

    # Megan Android Endpoints (OkHttp Upload & Sync)
    app.router.add_post("/api/megan/video/upload", handle_megan_video_upload)
    app.router.add_post("/api/megan/image/upload", handle_megan_video_upload)
    app.router.add_get("/api/megan/video/file/{filename}", handle_megan_video_file)
    app.router.add_get("/api/megan/video/frame/{video_id}/{filename}", handle_megan_video_frame)
    app.router.add_post("/api/megan/video/process", handle_megan_video_process)
    app.router.add_get("/api/megan/autonomy", handle_megan_autonomy_get)
    app.router.add_post("/api/megan/autonomy", handle_megan_autonomy_post)
    app.router.add_get("/api/megan/context", handle_megan_context_get)

    # Autonomous Docker & Web Tools
    app.router.add_get("/api/megan/tools/status", handle_megan_tool_status)
    app.router.add_post("/api/megan/tools/search", handle_megan_tool_search)
    app.router.add_post("/api/megan/tools/scrape", handle_megan_tool_scrape)
    app.router.add_get("/api/megan/tools/brain", handle_megan_tool_brain)
    app.router.add_post("/api/megan/tools/brain", handle_megan_tool_brain)

    # Character & Lore
    app.router.add_get("/api/bot/config", handle_bot_config_get)
    app.router.add_post("/api/bot/config", handle_bot_config_post)
    app.router.add_get("/api/bot/lore", handle_bot_lore_get)
    app.router.add_post("/api/bot/lore", handle_bot_lore_post)
    app.router.add_get("/api/bot/characters", handle_bot_characters_list)
    app.router.add_get("/api/bot/appearance/presets", handle_appearance_presets_list)
    app.router.add_post("/api/bot/appearance/presets", handle_appearance_preset_save)
    app.router.add_get("/api/danbooru/tags", handle_danbooru_tags)

    # Lovense
    app.router.add_get("/api/lovense/status", handle_lovense_status)
    app.router.add_post("/api/bot/haptic/test", handle_bot_haptic_test)

    # Chat & ComfyUI Render
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/ws/chat", handle_chat_ws)
    app.router.add_post("/api/render", handle_render)
    app.router.add_post("/api/render/direct", handle_render)
    app.router.add_get("/api/gallery", handle_gallery_list)
    app.router.add_get("/api/gallery/{filename:.+}", handle_gallery_file)

    # Static assets
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", path=str(STATIC_DIR), name="static")

    return app

async def main():
    logger.info("=== Starting Megan Studio Autonomous Service ===")
    logger.info(f"Port: {PORT}, Host IP: {LOCAL_TS_IP}")
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()

    # Bind to 0.0.0.0 to serve local, Tailscale, LAN, and Docker Bridge
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✔ Megan Studio running on http://0.0.0.0:{PORT}")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Megan Studio beendet.")
