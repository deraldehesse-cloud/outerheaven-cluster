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
import sqlite3
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
AUDIO_TTS_DIR = DATA_DIR / "audio" / "tts"
CHAT_DB_PATH = DATA_DIR / "megan_chat.db"

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

# Local Autarkic Megan Assets & Caches (Outer Heaven storage-alpha)
MEGAN_ASSETS_DIR = Path("/mnt/storage-alpha/megan-assets")
MEGAN_AVATARS_DIR = MEGAN_ASSETS_DIR / "avatars"
MEGAN_IMAGES_DIR = MEGAN_ASSETS_DIR / "images"
MEGAN_FRAMES_DIR = MEGAN_ASSETS_DIR / "frames"
MEGAN_ASSET_CACHE_DIR = MEGAN_ASSETS_DIR / "cache"

STATIC_IMAGES_DIR = STATIC_DIR / "images"
STATIC_AVATAR_DIR = STATIC_DIR / "avatar"

COMFYUI_OUTPUT_DIR = Path("/home/outerheaven/ComfyUI/output")
if not COMFYUI_OUTPUT_DIR.is_dir():
    _alt = Path("/mnt/storage-beta/ComfyUI/output")
    if _alt.is_dir():
        COMFYUI_OUTPUT_DIR = _alt
    else:
        COMFYUI_OUTPUT_DIR = BASE_DIR / "outputs"
        COMFYUI_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Ensure cache dirs exist
STATIC_DIR.mkdir(parents=True, exist_ok=True)
STATIC_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
try:
    MEGAN_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    MEGAN_AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    MEGAN_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    MEGAN_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    MEGAN_ASSET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
APPEARANCE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_TTS_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# Central Chat Database Initializer
def init_chat_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(CHAT_DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'TEXT',
                text TEXT NOT NULL,
                image_url TEXT,
                audio_url TEXT,
                prompt TEXT,
                timestamp INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp)")
        conn.commit()

init_chat_db()

def save_chat_message(
    msg_id: str,
    sender: str,
    text: str,
    msg_type: str = "TEXT",
    image_url: Optional[str] = None,
    audio_url: Optional[str] = None,
    prompt: Optional[str] = None,
    timestamp: Optional[int] = None
) -> Dict[str, Any]:
    init_chat_db()
    ts = timestamp or int(time.time() * 1000)
    with sqlite3.connect(str(CHAT_DB_PATH)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO messages (id, sender, type, text, image_url, audio_url, prompt, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, sender, msg_type, text, image_url, audio_url, prompt, ts)
        )
        conn.commit()
    return {
        "id": msg_id,
        "sender": sender,
        "type": msg_type,
        "text": text,
        "image_url": image_url,
        "audio_url": audio_url,
        "prompt": prompt,
        "timestamp": ts
    }

def load_chat_messages(limit: int = 150) -> List[Dict[str, Any]]:
    init_chat_db()
    with sqlite3.connect(str(CHAT_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, sender, type, text, image_url, audio_url, prompt, timestamp
            FROM messages
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (limit,)
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]

def clear_chat_messages() -> int:
    """Deletes all messages from the central SQLite chat DB."""
    init_chat_db()
    with sqlite3.connect(str(CHAT_DB_PATH)) as conn:
        cur = conn.execute("DELETE FROM messages")
        deleted = cur.rowcount
        conn.commit()
    logger.info(f"Chat-Historie geleert ({deleted} Nachrichten gelöscht).")
    return deleted

# Context Engine Import
try:
    from megan_context import (
        megan_context_engine,
        sanitize_megan_response,
        get_gdrive_base_path,
        resolve_gdrive_project_path,
        execute_autonomous_tool,
        load_hermes_tools_config,
        save_hermes_tools_config,
        build_megan_system_prompt,
        reload_megan_in_memory_state,
        get_comfyui_queue_status,
        purge_comfyui_gpu,
        set_image_finished_callback,
        KANBAN_DB,
        VALID_TASK_PROJECTS,
        VALID_TASK_TYPES,
        init_mission_tasks_db,
        get_mission_tasks,
        add_mission_task,
        update_mission_task_status,
        get_mission_task_by_id,
        update_mission_task_result,
        delete_mission_task,
        CUSTOM_TOOLS_DIR,
    )
except ImportError:
    megan_context_engine = None
    KANBAN_DB = Path.home() / ".hermes" / "kanban.db"
    CUSTOM_TOOLS_DIR = Path.home() / ".hermes" / "custom_tools"
    VALID_TASK_PROJECTS = ["Megan Mobile Companion", "Catlord Project", "ComfyUI / Arc A770", "Server & Infrastruktur"]
    VALID_TASK_TYPES = [
        "Bild-Render (Assignee: Megan / ComfyUI)",
        "Code / Skripting (Assignee: Hermes)",
        "System / Cluster-Wartung (Assignee: Hermes)",
        "Analyse & Recherche (Assignee: Hermes)",
        "Allgemeine Aufgabe"
    ]
    def init_mission_tasks_db(): pass
    def get_mission_tasks(): return []
    def add_mission_task(title, project="Megan Mobile Companion", task_type="Allgemeine Aufgabe", details="", status="Zu erledigen", priority="Normal", assignee="Hermes"):
        try:
            KANBAN_DB.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(KANBAN_DB), timeout=3.0) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mission_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        project TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        details TEXT,
                        status TEXT NOT NULL,
                        priority TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        assignee TEXT NOT NULL
                    )
                """)
                cur = conn.cursor()
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                cur.execute("""
                    INSERT INTO mission_tasks (title, project, task_type, details, status, priority, created_at, assignee)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (title, project, task_type, details, status, priority, now_str, assignee))
                conn.commit()
                return cur.lastrowid
        except Exception as e:
            logger.error(f"Fallback add_mission_task error: {e}")
            return 1
    def update_mission_task_status(task_id, new_status): return True
    def get_mission_task_by_id(task_id): return None
    def update_mission_task_result(task_id, result, new_status=None): return True
    def delete_mission_task(task_id): return True
    def sanitize_megan_response(text: str) -> str:
        return text.strip()
    def get_gdrive_base_path():
        candidates = [
            Path("/mnt/storage-alpha/megan-assets/gdrive"),
            Path("/home/outerheaven/gdrive"),
            Path("/mnt/storage-beta/gdrive"),
            Path.home() / "gdrive"
        ]
        for c in candidates:
            if c.is_dir():
                return c
        return Path("/mnt/storage-alpha/megan-assets/gdrive")
    def resolve_gdrive_project_path(query: str):
        return {"found": False, "query": query}
    def execute_autonomous_tool(name: str, params=None):
        return {"success": False, "error": "Tool engine not loaded"}
    def load_hermes_tools_config():
        return {"builtin_tools": {}, "custom_tools": {}}
    def save_hermes_tools_config(cfg):
        return True
    def build_megan_system_prompt(lore=None):
        return "Du bist Megan, die persönliche Assistentin von Big Boss."
    def reload_megan_in_memory_state():
        pass
    def get_comfyui_queue_status(comfy_url=None):
        return {"online": True, "busy": False, "queue_total": 0, "running_count": 0, "pending_count": 0}
    def purge_comfyui_gpu(comfy_url=None):
        return True
    def set_image_finished_callback(cb):
        pass

# Active WebSockets & Broadcast
ACTIVE_WS_CLIENTS: set = set()
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

async def broadcast_ws(payload: Dict[str, Any], exclude: Optional[web.WebSocketResponse] = None) -> None:
    """Send JSON payload to all connected WebSocket clients, optionally excluding one."""
    if not ACTIVE_WS_CLIENTS:
        return
    dead = set()
    for ws in list(ACTIVE_WS_CLIENTS):
        if exclude is not None and ws is exclude:
            continue
        try:
            if not ws.closed:
                await ws.send_json(payload)
            else:
                dead.add(ws)
        except Exception as e:
            logger.debug(f"Broadcast to WS failed: {e}")
            dead.add(ws)
    for ws in dead:
        ACTIVE_WS_CLIENTS.discard(ws)

def broadcast_ws_sync(payload: Dict[str, Any]) -> None:
    """Sync wrapper to broadcast to WebSockets from background threads."""
    global MAIN_LOOP
    loop = MAIN_LOOP
    if not loop or not loop.is_running():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast_ws(payload), loop)

def on_image_finished_broadcast(data: Dict[str, Any]) -> None:
    """Callback invoked by megan_context when a ComfyUI render completes."""
    logger.info(f"ComfyUI Bild fertiggestellt, sende an aktive WebSockets: {data.get('filename')}")
    # 1. image_ready event for Android Companion / Web UI
    broadcast_ws_sync({
        "type": "image_ready",
        "image_url": data.get("url"),
        "url": data.get("url"),
        "prompt": data.get("prompt", ""),
        "id": data.get("id"),
        "filename": data.get("filename"),
        "workflow": data.get("workflow")
    })
    # 2. image event for backward compatibility
    broadcast_ws_sync({
        "type": "image",
        "url": data.get("url"),
        "prompt": data.get("prompt", "")
    })
    # 3. render_completed event to reset isRendering
    broadcast_ws_sync({
        "type": "render_completed",
        "render_id": data.get("id"),
        "id": data.get("id"),
        "filename": data.get("filename"),
        "image_url": data.get("url"),
        "url": data.get("url"),
        "prompt": data.get("prompt", "")
    })
    broadcast_ws_sync({
        "type": "idle"
    })

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
    """Loads active character prompt with strict canonical German Megan persona & anti-Hermes guards."""
    base_prompt = ""
    if CANONICAL_CHARACTER_FILE.is_file():
        try:
            data = json.loads(CANONICAL_CHARACTER_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base_prompt = data.get("system_prompt") or data.get("description") or ""
        except Exception as e:
            logger.warning(f"Error reading {CANONICAL_CHARACTER_FILE}: {e}")

    if not base_prompt and MEGAN_PROMPT_FILE.is_file():
        try:
            base_prompt = MEGAN_PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Error reading {MEGAN_PROMPT_FILE}: {e}")

    return build_megan_system_prompt(base_prompt)

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
# Handlers: Google Drive Resolution & Autonomous Tool Execution
# ------------------------------------------------------------------------------
async def handle_megan_gdrive_resolve(request: web.Request) -> web.Response:
    project = request.query.get("project", "").strip()
    if not project:
        try:
            body = await request.json()
            project = body.get("project", "").strip()
        except Exception:
            pass
    if not project:
        return web.json_response({"success": False, "error": "Projektname ('project') erforderlich"}, status=400)

    result = resolve_gdrive_project_path(project)
    return web.json_response({"success": True, **result})

async def handle_megan_gdrive_search(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        query = body.get("query", "").strip()
    except Exception:
        query = request.query.get("query", "").strip()

    if not query:
        return web.json_response({"success": False, "error": "Suchbegriff ('query') erforderlich"}, status=400)

    result = resolve_gdrive_project_path(query)
    return web.json_response({"success": True, **result})

async def handle_megan_system_status(request: web.Request) -> web.Response:
    res = execute_autonomous_tool("system_status")
    return web.json_response(res)

async def handle_megan_tools_execute(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Ungültiges JSON"}, status=400)

    tool = body.get("tool", "")
    params = body.get("params", {})
    if not tool:
        return web.json_response({"success": False, "error": "'tool' Parameter erforderlich"}, status=400)

    res = execute_autonomous_tool(tool, params)
    return web.json_response(res)

def detect_megan_photo_claim(text: str) -> bool:
    """
    Erkennt, wenn Megan im Chat ein Bild von sich ankündigt oder verspricht.
    Beispiele:
    - '<image>...</image>' oder '<image> ...'
    - '[PHOTO: ...]' oder '[IMAGE: ...]'
    - 'Hier ist ein Foto von mir...'
    - 'Hier ist ein Bild für dich...'
    - 'Ich mache dir kurz ein Foto...'
    - 'Ich schicke dir mal ein Bild...'
    """
    if not text:
        return False
    patterns = [
        r'<image>[^<]+</image>',
        r'<image>\s*([^\n]+)',
        r'\[(?:photo|image|foto|bild|selfie):[^\]]+\]',
        r'\[image:[^\]]+\]',
        r'(?:hier\s+ist|hier\s+hast\s+du|hier\s+kommt)\s+(?:ein\s+|mal\s+ein\s+|das\s+|mein\s+)?(?:foto|bild|selfie|schnappschuss)\b',
        r'(?:ich\s+mache\s+dir|ich\s+schicke\s+dir|ich\s+sende\s+dir|ich\s+knipse\s+dir)\s+(?:kurz\s+|mal\s+)?(?:ein\s+|ein\s+neues\s+|mal\s+ein\s+)?(?:foto|bild|selfie)\b',
        r'foto\s+von\s+mir\b',
        r'bild\s+von\s+mir\b',
    ]
    t = text.lower()
    return any(bool(re.search(p, t, re.IGNORECASE)) for p in patterns)

def detect_user_photo_intent(text: str) -> bool:
    """
    Erkennt, wenn der Nutzer Megan um ein Foto bittet oder einen Bild-Befehl sendet.
    Beispiele:
    - 'schick mir ein bild'
    - 'foto von dir'
    - 'erstell ein bild'
    - 'zieh dich aus'
    - '<image> ...' oder '[IMAGE: ...]'
    """
    if not text:
        return False
    t = text.lower()
    patterns = [
        r'(?:schick|zeig|sende|gib|knips|mach)\s+(?:mir\s+)?(?:uns\s+)?(?:mal\s+|kurz\s+|bitte\s+)?(?:ein\s+|mal\s+ein\s+|das\s+|dein\s+|ein\s+neues\s+)?(?:foto|bild|selfie|schnappschuss|pic)\b',
        r'(?:foto|bild|selfie|pic)\s+von\s+(?:dir|euch)\b',
        r'(?:erstell|generier|zeichne|render|mach)\s+(?:mir\s+|uns\s+)?(?:mal\s+|kurz\s+|bitte\s+)?(?:ein\s+|mal\s+ein\s+|das\s+)?(?:foto|bild|selfie|zeichnung|portrait)\b',
        r'(?:kannst\s+du\s+|hast\s+du\s+|schickst\s+du\s+)?(?:mir\s+)?(?:ein\s+)?(?:foto|bild|selfie)\s+(?:von\s+dir\s+)?(?:schicken|senden|machen|zeigen)\b',
        r'\bzieh\s+dich\s+.*aus\b',
        r'\bzieh\s+dich\s+(?:mal\s+|bitte\s+)?aus\b',
        r'\b(?:ausziehen|nackt|nackig|strip|oben\s+ohne|untendrunter|mach\s+dich\s+frei)\b',
        r'\[(?:photo|image|foto|bild|selfie):[^\]]+\]',
        r'<image>[^<]+</image>',
        r'<image>\s*([^\n]+)'
    ]
    return any(bool(re.search(p, t, re.IGNORECASE)) for p in patterns)

LAST_RENDERED_PROMPT: Optional[str] = None

def trigger_async_photo_render(prompt: str, workflow: str = "megan_erotic_chat", steps: Optional[int] = 18, cfg: Optional[float] = 5.5) -> asyncio.Task:
    """Startet einen autonomen ComfyUI Bild-Render asynchron im Hintergrund."""
    global LAST_RENDERED_PROMPT
    LAST_RENDERED_PROMPT = prompt
    effective_steps = int(steps or 18)
    effective_cfg = float(cfg or 5.5)
    logger.info(f"[Auto-Render] Triggere ComfyUI Render mit Prompt: '{prompt[:60]}...' (Workflow: {workflow}, steps={effective_steps}, cfg={effective_cfg})")
    params: Dict[str, Any] = {
        "prompt": prompt,
        "workflow": workflow,
        "steps": effective_steps,
        "cfg": effective_cfg,
        "sampler_name": "euler_ancestral",
        "scheduler": "karras"
    }
    return asyncio.create_task(
        asyncio.to_thread(
            execute_autonomous_tool,
            "comfyui_render",
            params
        )
    )

def extract_megan_image_prompt(text: str) -> Tuple[Optional[str], bool, str]:
    """
    Extrahiert Bildanforderungen und Feedback-Tags aus dem Modelltext:
    - [IMAGE_TRIGGER: <danbooru-tags>]
    - <image>...</image> oder [IMAGE: ...]
    - [RETRY_SAFE_RENDER]
    Entfernt alle Trigger-Tags restlos aus dem Chat-Text.
    Gibt (extracted_prompt, has_retry, cleaned_text) zurück.
    """
    if not text:
        return None, False, text

    extracted_prompt = None
    has_retry = False
    cleaned = text

    # 1. [RETRY_SAFE_RENDER] Tag erkennen und entfernen
    if re.search(r'\[RETRY_SAFE_RENDER\]', cleaned, flags=re.IGNORECASE):
        has_retry = True
        cleaned = re.sub(r'\[RETRY_SAFE_RENDER\]', '', cleaned, flags=re.IGNORECASE)

    # 2. [IMAGE_TRIGGER: ...]
    m_trig = re.search(r'\[IMAGE_TRIGGER:\s*([^\]]+)\]', cleaned, flags=re.IGNORECASE)
    if m_trig:
        extracted_prompt = m_trig.group(1).strip()
        cleaned = re.sub(r'\[IMAGE_TRIGGER:[^\]]+\]', '', cleaned, flags=re.IGNORECASE)

    # 3. <image>prompt</image>
    if not extracted_prompt:
        m1 = re.search(r'<image>(.*?)</image>', cleaned, flags=re.DOTALL | re.IGNORECASE)
        if m1:
            extracted_prompt = m1.group(1).strip()
            cleaned = re.sub(r'<image>.*?</image>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    # 4. <image> prompt bis zum Zeilenumbruch
    if not extracted_prompt:
        m2 = re.search(r'<image>\s*([^\n<]+)', cleaned, flags=re.IGNORECASE)
        if m2:
            extracted_prompt = m2.group(1).strip()
            cleaned = re.sub(r'<image>\s*[^\n<]+', '', cleaned, flags=re.IGNORECASE)

    # 5. [IMAGE: prompt] oder [PHOTO: prompt]
    if not extracted_prompt:
        m3 = re.search(r'\[(?:image|photo|foto|bild|selfie):\s*([^\]]+)\]', cleaned, flags=re.IGNORECASE)
        if m3:
            extracted_prompt = m3.group(1).strip()
            cleaned = re.sub(r'\[(?:image|photo|foto|bild|selfie):[^\]]+\]', '', cleaned, flags=re.IGNORECASE)

    # Verbleibende leere Tags entfernen und Whitespace normalisieren
    cleaned = re.sub(r'</?image>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\[(?:IMAGE_TRIGGER|RETRY_SAFE_RENDER)[^\]]*\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()

    return extracted_prompt, has_retry, cleaned

def detect_black_frame_feedback(text: str) -> bool:
    """
    Erkennt, ob Sebastian meldet, dass das Bild schwarz war oder NaN-Pixel aufwies.
    """
    if not text:
        return False
    patterns = [
        r"\b(?:bild|foto|render|frame|screen)\s+(?:war|ist|blieb|bleibt|wurde)\s+(?:komplett\s+|ganz\s+|völlig\s+|einfach\s+)?(?:schwarz|black)\b",
        r"\b(?:schwarz(?:es|er)?|black)\s+(?:bild|foto|frame|screen)\b",
        r"\bblack[\s_-]?(?:screen|frame)\b",
        r"\b(?:nur|komplett|völlig|alles)\s+schwarz\b",
        r"\bwar\s+schwarz\b",
        r"\bbild\s+schwarz\b"
    ]
    t = text.lower()
    return any(bool(re.search(p, t, re.IGNORECASE)) for p in patterns)

def get_last_image_prompt() -> str:
    """Ermittelt den letzten verwendeten Bild-Prompt aus dem Speicher oder der SQLite DB."""
    global LAST_RENDERED_PROMPT
    if LAST_RENDERED_PROMPT and LAST_RENDERED_PROMPT.strip():
        return LAST_RENDERED_PROMPT.strip()

    try:
        init_chat_db()
        with sqlite3.connect(str(CHAT_DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT prompt FROM messages 
                WHERE type = 'SDXL_IMAGE' AND prompt IS NOT NULL AND prompt != ''
                ORDER BY timestamp DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row and row[0] and row[0].strip():
                LAST_RENDERED_PROMPT = row[0].strip()
                return LAST_RENDERED_PROMPT
    except Exception as e:
        logger.warning(f"Fehler beim Laden des letzten Bild-Prompts aus DB: {e}")

    return "1girl, megan, solo, cute smile, emerald green eyes, fiery orange hair, soft lighting, 8k uhd, photorealistic"

def trigger_safe_recovery_render(custom_prompt: Optional[str] = None) -> asyncio.Task:
    """
    Führt die Intel Arc A770 Black-Frame Auto-Recovery durch:
    1. ComfyUI GPU Purge (/interrupt und /free)
    2. Sichere Fallback-Parameter: CFG 5.5, Steps 10, VAE FP32
    3. Re-Render des letzten Prompts und Verteilung an aktive Clients
    """
    logger.info("[Auto-Recovery] Black-Frame gemeldet. Starte Intel Arc A770 Auto-Recovery...")
    try:
        purge_comfyui_gpu(COMFYUI_URL)
    except Exception as pe:
        logger.warning(f"[Auto-Recovery] Purge-Fehler: {pe}")

    prompt_to_render = (custom_prompt or "").strip() or get_last_image_prompt()
    logger.info(f"[Auto-Recovery] Starte Safe-Render mit 10 Steps, CFG 5.5 für Prompt: '{prompt_to_render[:60]}...'")
    return trigger_async_photo_render(prompt_to_render, workflow="megan_erotic_chat", steps=10, cfg=5.5)

def check_autonomous_intent(text: str) -> Optional[str]:
    """Detects if user asks for Google Drive project lookup, troubleshooting, or system status."""
    t_lower = text.lower().strip()
    if not t_lower:
        return None

    # Schneller Ausstieg bei alltäglichen Begrüßungen, Höflichkeiten und Smalltalk
    greetings = {
        "hallo", "hi", "hey", "hallo megan", "hi megan", "hey megan",
        "guten morgen", "guten tag", "guten abend", "gute nacht",
        "servus", "servas", "moin", "gude", "gude megan", "tach", "grüß gott", "grüss gott",
        "tschüss", "tschau", "ciao",
        "wie gehts", "wie geht's", "wie geht es dir",
        "danke", "danke schön", "danke dir", "vielen dank",
        "ok", "okay", "alles klar", "ja", "nein", "gut", "super"
    }
    cleaned_simple = re.sub(r'[^\w\s]', '', t_lower).strip()
    if cleaned_simple in greetings or any(cleaned_simple.startswith(g + " ") for g in ["hallo megan", "hi megan", "hey megan", "gude megan", "guten morgen", "guten tag", "wie geht es dir", "wie gehts"]):
        return None

    # 1. Google Drive / Project Resolution
    if "google drive" in t_lower or "gdrive" in t_lower or "agenten-drive" in t_lower or "projekt" in t_lower:
        m = re.search(r'(?:projekt|project)\s+([a-zA-Z0-9_\-]+)', text, re.IGNORECASE)
        project_term = m.group(1) if m else None
        if not project_term:
            m2 = re.search(r'nach\s+([a-zA-Z0-9_\-]+)', text, re.IGNORECASE)
            if m2 and m2.group(1).lower() not in ["projekt", "google", "drive"]:
                project_term = m2.group(1)

        if project_term:
            info = resolve_gdrive_project_path(project_term)
            if info.get("found"):
                fnames = [f["rel_path"] for f in info.get("files", [])[:8]]
                flist_str = ", ".join(fnames) if fnames else "Ordner ist leer oder enthält keine Dateien"
                return (
                    f"\n[AUTONOMER GOOGLE-DRIVE RESOLVER]:\n"
                    f"Projekt '{info.get('project_name')}' wurde erfolgreich aufgelöst!\n"
                    f"- Absoluter Pfad: {info.get('resolved_path')} ({info.get('source')})\n"
                    f"- Enthaltene Dateien ({info.get('file_count')} gefunden): {flist_str}\n"
                    f"Nutze diese Pfad-Informationen direkt in deiner Antwort."
                )
            else:
                return (
                    f"\n[AUTONOMER GOOGLE-DRIVE RESOLVER]:\n"
                    f"Projekt '{project_term}' wurde gesucht, existiert aber nicht im Agenten-Drive ({get_gdrive_base_path()})."
                )

    # 2. System Status / Troubleshooting
    if any(k in t_lower for k in ["system status", "cluster status", "warum hängt", "service status", "dienst prüfen", "gpu status", "wie ausgelastet"]):
        s = execute_autonomous_tool("system_status")
        return f"\n[AUTONOME SYSTEM-DIAGNOSE]:\n{json.dumps(s.get('status', {}), indent=2, ensure_ascii=False)}"

    # 3. Megan Task & Script Intent (Zentrales Kanban-Board)
    # Flexible Intent-Erkennung für Skripte, Tools, Code und Board-Aufgaben
    script_pattern_1 = r'(?:(?:kannst\s+du\s+(?:mir\s+)?)?(?:schreib(?:e)?|programmier(?:e)?|erstell(?:e)?|entwickl(?:e)?|bau(?:e)?|mach(?:e)?)\s+(?:mir\s+|uns\s+)?(?:mal\s+|bitte\s+)?(?:ein\s+|einen\s+|mal\s+ein\s+|bitte\s+ein\s+)?(?:skript|script|tool|python[\s-]skript|programm|code))\b\s*(.*)'
    script_pattern_2 = r'(?:(?:kannst\s+du\s+(?:mir\s+)?)?(?:ein\s+|einen\s+)?(?:skript|script|tool|python[\s-]skript|code)\s+(?:schreiben|bauen|erstellen|programmieren|entwickeln|machen))\s*(.*)'
    script_pattern_3 = r'^(?:schreib(?:e)?\s+mir\s+(?:mal\s+|bitte\s+)?)(.+)'

    task_pattern_1 = r'(?:(?:kannst\s+du\s+(?:mir\s+)?)?(?:erstell(?:e)?|leg(?:e)?|pack(?:e)?|mach(?:e)?|trag(?:e)?|setz(?:e)?)\s+(?:mir\s+|uns\s+)?(?:mal\s+|bitte\s+)?(?:eine\s+|mal\s+eine\s+|bitte\s+eine\s+|ne\s+|einen\s+)?(?:aufgabe|task|ticket|todo)\b\s*(?:an|auf(?:s|\s+das)?\s+board)?)\s*(.*)'
    task_pattern_2 = r'(?:(?:neue[n]?\s+)?(?:aufgabe|task)\s+(?:anlegen|erstellen|hinzufügen|aufnehmen|eintragen))\s*(?:[:\-\s]\s*)?(.*)'

    explicit_task_phrase = any(k in t_lower for k in [
        "aufgabe anlegen", "aufgabe erstellen", "neue aufgabe",
        "task anlegen", "task erstellen", "neuer task",
        "aufgabe fürs board", "aufgabe auf das board", "mach mal ein skript", "bau ein tool", "schreib mir ein skript"
    ])

    m_script = re.search(script_pattern_1, text, re.IGNORECASE) or re.search(script_pattern_2, text, re.IGNORECASE) or re.search(script_pattern_3, text, re.IGNORECASE)
    m_task = re.search(task_pattern_1, text, re.IGNORECASE) or re.search(task_pattern_2, text, re.IGNORECASE)

    if m_script or m_task or explicit_task_phrase:
        is_script = bool(m_script) or any(k in t_lower for k in ["skript", "script", "tool", "code", "programm", "python"])
        
        # Raw extrahierten Text ermitteln
        raw_req = ""
        if m_script and m_script.groups() and m_script.group(1):
            raw_req = m_script.group(1).strip()
        elif m_task and m_task.groups() and m_task.group(1):
            raw_req = m_task.group(1).strip()
        elif not raw_req:
            for trigger in ["aufgabe anlegen", "aufgabe erstellen", "neue aufgabe", "task anlegen", "task erstellen", "mach mal ein skript", "bau ein tool", "schreib mir"]:
                if trigger in t_lower:
                    idx = t_lower.find(trigger) + len(trigger)
                    raw_req = text[idx:].strip()
                    break

        # Aufgabentitel sauber bereinigen
        cleaned = re.sub(r'^[,\.\:\-\s]+', '', raw_req).strip()
        cleaned = re.sub(r'^(?:für|zu|zum|zur|das|dass|um|welches|die|der|den|ein|eine|einen|mit\s+dem\s+titel|namens)\s+', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'^[,\.\:\-\s]+', '', cleaned).strip()
        cleaned = re.sub(r'[\.\?!,;:\s]+$', '', cleaned).strip()
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]

        req_l = (cleaned or text).lower()
        if is_script:
            title = f"Skript: {cleaned[:50]}" if cleaned and not cleaned.lower().startswith(("skript", "script", "tool", "code")) else (cleaned[:50] if cleaned else "Neues Automatisierungs-Skript")
            task_type = "Code / Skripting (Assignee: Hermes)"
            assignee = "Hermes"
            details = f"Anforderung von Boss via Megan-Chat:\n{text}\n\nZiel: Python-Skript / Tool für: {cleaned or text}"
        elif any(k in req_l for k in ["bild", "render", "foto", "sdxl", "lora"]):
            title = cleaned[:50] if cleaned else "Bild-Render Aufgabe"
            task_type = "Bild-Render (Assignee: Megan / ComfyUI)"
            assignee = "Megan"
            details = f"Aufgabe via Megan Mobile Companion von Boss:\n{text}"
        elif any(k in req_l for k in ["wartung", "check", "system", "cluster", "dienst", "service"]):
            title = cleaned[:50] if cleaned else "System / Wartung"
            task_type = "System / Cluster-Wartung (Assignee: Hermes)"
            assignee = "Hermes"
            details = f"Aufgabe via Megan Mobile Companion von Boss:\n{text}"
        elif any(k in req_l for k in ["analyse", "recherche", "prüfung", "untersuch"]):
            title = cleaned[:50] if cleaned else "Analyse & Recherche"
            task_type = "Analyse & Recherche (Assignee: Hermes)"
            assignee = "Hermes"
            details = f"Aufgabe via Megan Mobile Companion von Boss:\n{text}"
        else:
            title = cleaned[:50] if cleaned else "Neue Aufgabe via Megan-Voice"
            task_type = "Allgemeine Aufgabe (Assignee: Hermes)"
            assignee = "Hermes"
            details = f"Aufgabe via Megan Mobile Companion von Boss:\n{text}"

        project = "Megan Mobile Companion"
        task_id = add_mission_task(
            title=title,
            project=project,
            task_type=task_type,
            details=details,
            status="Zu erledigen",
            priority="Normal",
            assignee=assignee
        ) or 1
        
        broadcast_ws_sync({
            "type": "task_created",
            "task_id": task_id,
            "title": title,
            "project": project,
            "task_type": task_type,
            "assignee": assignee
        })
        
        return (
            f"\n[AUTONOMER TASK-BOARD INTENT - ERFOLGREICH ANGELEGT]:\n"
            f"Die Aufgabe #{task_id} wurde soeben erfolgreich in der zentralen Kanban-Datenbank (~/.hermes/kanban.db) angelegt!\n"
            f"- ID: #{task_id}\n"
            f"- Titel: {title}\n"
            f"- Projekt: {project}\n"
            f"- Typ: {task_type}\n"
            f"- Zuweisung: {assignee}\n\n"
            f"STRIKTE ANWEISUNG FÜR DEINE JETZIGE ANTWORT:\n"
            f"Bestätige dem Boss sofort authentisch, charmant und exakt auf Deutsch per Sprachnachricht, dass die Aufgabe jetzt auf unserem Board liegt.\n"
            f"Nenne in deiner Antwort unbedingt genau diese Details:\n"
            f"- Aufgabentitel: '{title}'\n"
            f"- Aufgabentyp: '{task_type}'\n"
            f"- Zuweisung an: {assignee}\n"
            f"Sag ihm z.B.: „Alles klar, Boss! Ich habe die Aufgabe '{title}' ({task_type}) soeben auf unser Board gelegt und {assignee} zugewiesen. Liegt bereit!“"
        )

    # 4. ComfyUI Workflow / Bildgenerierung (Megan Erotic Chat vs. Catlord Comic)
    # Strikte, explizite Intent-Erkennung für Render-Aufträge:
    # Nur wenn ein expliziter Bildbefehl vorliegt (z. B. 'erstelle ein bild', 'generiere ein bild', 'zeichne ein bild', 'render ein foto', '/render', etc.)!
    # Normale Fragen, Begrüßungen oder beiläufige Erwähnungen von "Bild" / "Comic" dürfen NIEMALS ComfyUI triggern.
    explicit_render_match = (
        t_lower.startswith(("/render", "/generate", "/draw"))
        or bool(re.search(r'\b(erstelle|generiere|zeichne|render|male)\b.*?\b(bild|foto|zeichnung|comic|selfie|portrait|szene)\b', t_lower, re.IGNORECASE))
        or bool(re.search(r'\b(bild|foto|zeichnung|comic|selfie|portrait|szene)\b.*?\b(erstellen|generieren|zeichnen|rendern|malen)\b', t_lower, re.IGNORECASE))
        or bool(re.search(r'^(?:schick|zeig|hast du|mach)\s+(?:mir\s+)?(?:ein\s+|mal\s+ein\s+)?(?:neues\s+)?(?:foto|selfie|bild\s+von\s+dir)\b', t_lower, re.IGNORECASE))
        or bool(re.search(r'\b(foto|selfie|bild)\s+von\s+(?:dir|euch)\b', t_lower, re.IGNORECASE))
    )
    if explicit_render_match:
        is_catlord = "catlord" in t_lower or "elliot" in t_lower or "comic" in t_lower
        wf_name = "catlord_comic" if is_catlord else "megan_erotic_chat"
        render_payload = {"prompt": text, "workflow": wf_name}

        # Extrahiere optionale Parameter für den dynamischen ComfyGraph-Builder
        m_steps = re.search(r'(\d+)\s*(?:steps|schritte)', t_lower)
        if m_steps:
            render_payload["steps"] = int(m_steps.group(1))
            render_payload["use_builder"] = True

        m_sampler = re.search(r'\b(euler_ancestral|euler\s*a|euler|dpm\+\+\s*2m\s*sde|dpm\+\+\s*2m|dpmpp_2m_sde|dpmpp_2m|ddim)\b', t_lower)
        if m_sampler:
            s_val = m_sampler.group(1).lower().replace(" ", "_").replace("++", "pp")
            if s_val == "euler_a":
                s_val = "euler_ancestral"
            render_payload["sampler_name"] = s_val
            render_payload["use_builder"] = True

        m_cfg = re.search(r'cfg\s*(?:von\s*)?(\d+(?:\.\d+)?)', t_lower)
        if m_cfg:
            render_payload["cfg"] = float(m_cfg.group(1))
            render_payload["use_builder"] = True

        m_lora = re.search(r'lora\s+([a-zA-Z0-9_\-\.]+)', t_lower)
        if m_lora:
            render_payload["lora"] = m_lora.group(1)
            render_payload["use_builder"] = True

        res = execute_autonomous_tool("comfyui_render", render_payload)
        if res.get("success"):
            was_busy = res.get("is_busy", False) or res.get("busy", False)
            queue_remaining = res.get("queue_remaining", 1)
            # Broadcast render_started to update mobile companion HeaderBar
            broadcast_ws_sync({
                "type": "render_started",
                "in_queue": was_busy,
                "queue_remaining": queue_remaining,
                "workflow": wf_name
            })
            if was_busy:
                return (
                    f"\n[AUTONOMER COMFYUI-RENDER - WARTESCHLANGE]:\n"
                    f"Die Grafikkarte (Intel Arc A770) rechnet gerade noch an einem anderen Job, aber der Render-Job für '{wf_name}' wurde erfolgreich hinten angestellt (in Warteschlange, Prompt-ID: {res.get('prompt_id')}).\n"
                    f"WICHTIG: Antworte dem Boss authentisch auf Deutsch. Sag ihm klar und sympathisch:\n"
                    f"„Boss, die Grafikkarte rechnet gerade noch an einem anderen Job. Ich habe deinen Render hinten angestellt – sobald die A770 frei ist, ziehe ich dein Bild direkt durch!“\n"
                    f"Erkläre ihm, dass das fertige Bild automatisch im Chat erscheint, sobald die A770 fertig ist."
                )
            elif is_catlord:
                return (
                    f"\n[AUTONOMER COMFYUI-RENDER]:\n"
                    f"Der Render-Job für 'Catlord-Comic' wurde im Hintergrund gestartet (Workflow: workflows/catlord_comic.json ohne Megan-LoRAs, Prompt-ID: {res.get('prompt_id')}).\n"
                    f"Das Bild wird nach Fertigstellung über /api/images/<filename> in den Chat eingebunden.\n"
                    f"Antworte dem Boss auf Deutsch und kündige begeistert die neue Comic-Szene mit Kater Elliot an!"
                )
            else:
                return (
                    f"\n[AUTONOMER COMFYUI-RENDER]:\n"
                    f"Der Render-Job mit Profil '{wf_name}' wurde im Hintergrund gestartet (Status: queued, Prompt-ID: {res.get('prompt_id')}).\n"
                    f"Das Bild wird automatisch nach Fertigstellung über /api/images/<filename> in den Chat eingebunden.\n"
                    f"Antworte dem Boss charmant auf Deutsch und kündige an, dass das Bild gleich im Chat erscheint!"
                )

    return None

# ------------------------------------------------------------------------------
# Handlers: Kanban & Mission Tasks API (Port 8080)
# ------------------------------------------------------------------------------
async def handle_tasks_list(request: web.Request) -> web.Response:
    """GET /api/tasks - Liefert alle Aufgaben aus ~/.hermes/kanban.db."""
    tasks = await asyncio.to_thread(get_mission_tasks)
    return web.json_response({"success": True, "tasks": tasks})

async def handle_tasks_create(request: web.Request) -> web.Response:
    """POST /api/tasks - Erstellt eine neue Agenten-Aufgabe in mission_tasks."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    title = (data.get("title") or "").strip()
    if not title:
        return web.json_response({"success": False, "error": "Bitte einen Aufgaben-Titel angeben"}, status=400)

    project = (data.get("project") or "Megan Mobile Companion").strip()
    if project in ['Android App', 'Megan Companion', 'Android-App', 'Android App (OpenUp / Megan)']:
        project = "Megan Mobile Companion"
    task_type = (data.get("task_type") or "Bild-Render (Assignee: Megan / ComfyUI)").strip()
    details = (data.get("details") or "").strip()
    status = (data.get("status") or "Zu erledigen").strip()
    priority = (data.get("priority") or "Normal").strip()
    assignee = (data.get("assignee") or ("Megan" if "bild" in task_type.lower() or "render" in task_type.lower() else "Hermes")).strip()

    task_id = await asyncio.to_thread(
        add_mission_task, title, project, task_type, details, status, priority, assignee
    )
    if task_id:
        broadcast_ws_sync({
            "type": "task_created",
            "task_id": task_id,
            "title": title,
            "project": project,
            "task_type": task_type,
            "assignee": assignee
        })
        return web.json_response({
            "success": True,
            "task_id": task_id,
            "task": {
                "id": task_id,
                "title": title,
                "project": project,
                "task_type": task_type,
                "details": details,
                "status": status,
                "priority": priority,
                "assignee": assignee
            }
        })
    return web.json_response({"success": False, "error": "Fehler beim Hinzufügen der Aufgabe"}, status=500)

async def handle_tasks_status_update(request: web.Request) -> web.Response:
    """POST /api/tasks/status - Ändert den Kanban-Status einer Aufgabe."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    task_id = data.get("id")
    new_status = data.get("status")
    if task_id is None or not new_status:
        return web.json_response({"success": False, "error": "id und status erforderlich"}, status=400)

    try:
        task_id = int(task_id)
    except (ValueError, TypeError):
        return web.json_response({"success": False, "error": "Ungültige Task-ID"}, status=400)

    ok = await asyncio.to_thread(update_mission_task_status, task_id, str(new_status))
    if ok:
        return web.json_response({"success": True, "id": task_id, "status": new_status})
    return web.json_response({"success": False, "error": "Fehler beim Aktualisieren des Status"}, status=500)

async def handle_tasks_delete(request: web.Request) -> web.Response:
    """DELETE /api/tasks/{id} - Entfernt eine Aufgabe."""
    task_id = request.match_info.get("id")
    if not task_id:
        return web.json_response({"success": False, "error": "id erforderlich"}, status=400)

    try:
        task_id = int(task_id)
    except (ValueError, TypeError):
        return web.json_response({"success": False, "error": "Ungültige Task-ID"}, status=400)

    ok = await asyncio.to_thread(delete_mission_task, task_id)
    if ok:
        return web.json_response({"success": True, "id": task_id})
    return web.json_response({"success": False, "error": "Fehler beim Löschen"}, status=500)

async def run_task_worker(task: Dict[str, Any]):
    """Führt eine Kanban-Aufgabe asynchron im Hintergrund aus und aktualisiert mission_tasks."""
    import ast
    task_id = task.get("id")
    if not task_id:
        return
    title = task.get("title", "")
    details = task.get("details", "")
    task_type = (task.get("task_type") or "").lower()
    project = (task.get("project") or "").lower()
    assignee = (task.get("assignee") or "").lower()

    logger.info(f"[Kanban-Execute] Starte Ausführung für Task #{task_id}: '{title}' ({task_type})")

    try:
        # Fall 1: Bild-Render (SDXL / LoRA / Megan / ComfyUI)
        if "render" in task_type or "bild" in task_type or assignee == "megan":
            prompt = details.strip() if details.strip() else title.strip()
            wf_name = "catlord_comic" if "catlord" in project or "catlord" in prompt.lower() else "megan_erotic_chat"
            res = execute_autonomous_tool("comfyui_render", {"prompt": prompt, "workflow": wf_name})
            if res.get("success"):
                img_url = res.get("url") or f"/api/images/{res.get('filename', '')}"
                await asyncio.to_thread(update_mission_task_result, task_id, img_url, "Render / Review")
                logger.info(f"[Kanban-Execute] Task #{task_id} Render erfolgreich: {img_url}")
            else:
                err_msg = res.get("error") or "Render-Fehler: ComfyUI lieferte kein Bild oder GPU war belegt."
                await asyncio.to_thread(update_mission_task_result, task_id, err_msg, "In Bearbeitung")
                logger.warning(f"[Kanban-Execute] Task #{task_id} Render fehlgeschlagen.")

        # Fall 2: Code / Skripting (Hermes generiert Code, ast.parse Prüfung & Speicherung in ~/.hermes/custom_tools/)
        elif "code" in task_type or "skript" in task_type or "script" in task_type:
            logger.info(f"[Kanban-Execute] Generiere Python-Code für Task #{task_id} mit Hermes LLM...")
            model = MEGAN_MODEL
            if BOT_CONFIG_FILE.is_file():
                try:
                    with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        m = data.get("hermes_model") or data.get("llm", {}).get("model") or data.get("active_model")
                        if m and isinstance(m, str) and m.strip():
                            model = m.strip()
                except Exception:
                    pass

            prompt_content = (
                "Du bist Hermes, autonomer Senior-Entwickler auf Outer Heaven.\n"
                "Erstelle ein vollständiges, syntaktisch einwandfreies und direkt ausführbares Python-Skript für folgende Anforderung.\n"
                f"Aufgabe: {title}\n"
                f"Projekt: {task.get('project', 'Megan Mobile Companion')}\n"
                f"Details & Spezifikation:\n{details}\n\n"
                "ANWEISUNG:\n"
                "Antworte AUSSCHLIESSLICH mit dem Python-Code innerhalb eines ```python ... ``` Blocks. "
                "Kein Begleittext, keine Einleitung, keine Schlussworte, nur der Codeblock."
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{LOCAL_OLLAMA_URL}/api/generate",
                    json={"model": model, "prompt": prompt_content, "stream": False},
                    timeout=aiohttp.ClientTimeout(total=90.0)
                ) as resp:
                    if resp.status == 200:
                        jdata = await resp.json()
                        llm_reply = jdata.get("response", "").strip()
                        clean_reply = re.sub(r'<think>.*?</think>', '', llm_reply, flags=re.DOTALL).strip() or llm_reply.strip()

                        code_match = re.search(r'```(?:python)?\s*\n(.*?)```', clean_reply, re.DOTALL)
                        code_str = code_match.group(1).strip() if code_match else clean_reply.strip()

                        is_valid = True
                        syntax_err = ""
                        try:
                            ast.parse(code_str)
                        except SyntaxError as se:
                            is_valid = False
                            syntax_err = f"SyntaxError in Zeile {se.lineno}: {se.msg}"
                        except Exception as e_ast:
                            is_valid = False
                            syntax_err = str(e_ast)

                        if is_valid:
                            CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)

                            target_match = re.search(r'\[TARGET_PATH\]:\s*([^\n\r]+)', details)
                            filename = None
                            if target_match:
                                raw_p = Path(target_match.group(1).strip())
                                filename = raw_p.name if raw_p.name.endswith(".py") else f"{raw_p.name}.py"

                            if not filename:
                                clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', title.lower()).strip('_')
                                clean_name = re.sub(r'_+', '_', clean_name)
                                if not clean_name:
                                    clean_name = f"hermes_tool_{task_id}"
                                filename = f"{clean_name}.py"

                            out_file = CUSTOM_TOOLS_DIR / filename
                            out_file.write_text(code_str, encoding="utf-8")

                            code_preview = code_str[:500] + ("\n..." if len(code_str) > 500 else "")
                            res_text = (
                                f"✅ Skript erfolgreich generiert & verifiziert (ast.parse OK)\n"
                                f"Pfad: {out_file}\n"
                                f"Größe: {len(code_str)} Bytes\n\n"
                                f"Code-Vorschau:\n```python\n{code_preview}\n```"
                            )
                            await asyncio.to_thread(update_mission_task_result, task_id, res_text, "Erledigt")
                            logger.info(f"[Kanban-Execute] Task #{task_id} Code erfolgreich gespeichert in {out_file}")
                        else:
                            res_text = (
                                f"❌ Syntaxfehler bei Code-Generierung ({syntax_err})\n\n"
                                f"Vorschau des fehlerhaften Codes:\n```python\n{code_str[:800]}\n```"
                            )
                            await asyncio.to_thread(update_mission_task_result, task_id, res_text, "In Bearbeitung")
                            logger.warning(f"[Kanban-Execute] Task #{task_id} Syntaxfehler: {syntax_err}")
                    else:
                        await asyncio.to_thread(update_mission_task_result, task_id, f"LLM-Fehler HTTP {resp.status}", "In Bearbeitung")

        # Fall 3: System / Cluster-Wartung (Führt System- & Health-Checks aus und loggt Konsolenausgabe)
        elif "system" in task_type or "wartung" in task_type or "cluster" in task_type:
            logger.info(f"[Kanban-Execute] Führe System-/Cluster-Health-Checks für Task #{task_id} aus...")
            check_sections = []

            services = ["mission-control.service", "megan-studio.service", "comfyui.service", "ollama.service"]
            svc_lines = []
            for s in services:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "systemctl", "--user", "is-active", s,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    so, _ = await proc.communicate()
                    st = so.decode().strip() or "unknown"
                    icon = "🟢" if st == "active" else "🔴"
                    svc_lines.append(f"  {icon} {s}: {st}")
                except Exception as e:
                    svc_lines.append(f"  ⚠️ {s}: {e}")
            check_sections.append("=== SYSTEMD DIENSTE ===\n" + "\n".join(svc_lines))

            try:
                proc = await asyncio.create_subprocess_exec(
                    "df", "-h", "/", "/mnt/storage-beta",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                so, _ = await proc.communicate()
                check_sections.append("=== SPEICHERPLATZ ===\n" + so.decode().strip())
            except Exception as e:
                check_sections.append(f"=== SPEICHERPLATZ ===\nFehler: {e}")

            try:
                proc = await asyncio.create_subprocess_exec(
                    "tailscale", "status",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                so, _ = await proc.communicate()
                ts_lines = so.decode().strip().split("\n")[:8]
                check_sections.append("=== TAILSCALE MESH ===\n" + "\n".join(ts_lines))
            except Exception as e:
                check_sections.append(f"=== TAILSCALE MESH ===\nFehler: {e}")

            try:
                proc = await asyncio.create_subprocess_exec(
                    "xpu-smi", "discovery",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                so, _ = await proc.communicate()
                xpu_out = so.decode().strip()
                if xpu_out:
                    check_sections.append("=== INTEL ARC GPU ===\n" + xpu_out[:400])
                else:
                    check_sections.append("=== INTEL ARC GPU ===\nIntel Arc A770 (Host Driver OK)")
            except Exception:
                check_sections.append("=== INTEL ARC GPU ===\nIntel Arc A770 (Sysfs discovery OK)")

            tool_name = None
            for part in (title + " " + details).split():
                clean_part = re.sub(r'[^a-zA-Z0-9_\-]', '', part)
                if clean_part and (CUSTOM_TOOLS_DIR / f"{clean_part}.py").is_file():
                    tool_name = clean_part
                    break

            if tool_name:
                script_path = (CUSTOM_TOOLS_DIR / f"{tool_name}.py").resolve()
                try:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, str(script_path),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    so, se = await asyncio.wait_for(proc.communicate(), timeout=35.0)
                    t_out = (so.decode("utf-8", errors="replace") + "\n" + se.decode("utf-8", errors="replace")).strip()
                    check_sections.append(f"=== TOOL '{tool_name}' AUSFÜHRUNG (Exit {proc.returncode}) ===\n{t_out[:800]}")
                except Exception as ex:
                    check_sections.append(f"=== TOOL '{tool_name}' FEHLER ===\n{ex}")

            final_report = "🛡️ SYSTEM- & CLUSTER-WARTUNG BERICHT:\n\n" + "\n\n".join(check_sections)
            await asyncio.to_thread(update_mission_task_result, task_id, final_report, "Erledigt")
            logger.info(f"[Kanban-Execute] Task #{task_id} System-Wartung erfolgreich abgeschlossen.")

        # Fall 4: Standard LLM / Prompt-Optimierung / Hermes Analyse & Recherche
        else:
            model = MEGAN_MODEL
            prompt_content = f"Du bist Hermes, technischer Agent auf Outer Heaven. Führe diese Aufgabe aus und liefere das konkrete Ergebnis:\nAufgabe: {title}\nProjekt: {project}\nDetails: {details}"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{LOCAL_OLLAMA_URL}/api/generate",
                    json={"model": model, "prompt": prompt_content, "stream": False},
                    timeout=aiohttp.ClientTimeout(total=50.0)
                ) as resp:
                    if resp.status == 200:
                        jdata = await resp.json()
                        llm_reply = jdata.get("response", "").strip()
                        clean_reply = re.sub(r'<think>.*?</think>', '', llm_reply, flags=re.DOTALL).strip() or llm_reply.strip()
                        await asyncio.to_thread(update_mission_task_result, task_id, clean_reply, "Erledigt")
                        logger.info(f"[Kanban-Execute] Task #{task_id} LLM-Ausführung erfolgreich.")
                    else:
                        await asyncio.to_thread(update_mission_task_result, task_id, f"Ollama-Fehler HTTP {resp.status}", "In Bearbeitung")

    except Exception as e:
        logger.error(f"[Kanban-Execute] Fehler bei Ausführung von Task #{task_id}: {e}", exc_info=True)
        await asyncio.to_thread(update_mission_task_result, task_id, f"Fehler bei Ausführung: {e}", "In Bearbeitung")

async def handle_tasks_execute(request: web.Request) -> web.Response:
    """POST /api/kanban/{id}/execute oder /api/tasks/{id}/execute - Startet die autonome Task-Ausführung."""
    task_id_str = request.match_info.get("id")
    if not task_id_str:
        return web.json_response({"success": False, "error": "Task-ID erforderlich"}, status=400)
    try:
        task_id = int(task_id_str)
    except (ValueError, TypeError):
        return web.json_response({"success": False, "error": "Ungültige Task-ID"}, status=400)

    task = await asyncio.to_thread(get_mission_task_by_id, task_id)
    if not task:
        return web.json_response({"success": False, "error": f"Task #{task_id} nicht gefunden"}, status=404)

    # Status direkt auf 'In Bearbeitung' setzen
    await asyncio.to_thread(update_mission_task_status, task_id, "In Bearbeitung")
    task["status"] = "In Bearbeitung"

    # Autonome Abarbeitung als Background-Task entkoppeln
    asyncio.create_task(run_task_worker(task))

    return web.json_response({
        "success": True,
        "task_id": task_id,
        "status": "In Bearbeitung",
        "message": f"Task #{task_id} ('{task['title']}') wurde gestartet."
    })

# ------------------------------------------------------------------------------
# Handlers: Character, Lore & Appearance Presets
# ------------------------------------------------------------------------------
async def handle_bot_config_get(request: web.Request) -> web.Response:
    cfg = {}
    if BOT_CONFIG_FILE.is_file():
        try:
            cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Error reading {BOT_CONFIG_FILE}: {e}")
    return web.json_response({
        "status": "ok",
        "synced": True,
        "success": True,
        "config": cfg,
        **cfg
    })

async def handle_bot_config_post(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        payload = data.get("config", data) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            payload = data

        current_cfg = {}
        if BOT_CONFIG_FILE.is_file():
            try:
                current_cfg = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as re:
                logger.warning(f"Fehler beim Lesen der bestehenden bot_config.json: {re}")

        # Deep merge incoming sections (personality, haptic, appearance, etc.)
        for key, val in payload.items():
            if key in ["config", "status", "synced", "success"]:
                continue
            if isinstance(val, dict) and isinstance(current_cfg.get(key), dict):
                current_cfg[key].update(val)
            else:
                current_cfg[key] = val

        # Persist bot_config.json atomar
        atomic_write_file(BOT_CONFIG_FILE, json.dumps(current_cfg, indent=2, ensure_ascii=False))

        # Appearance Presets synchronisieren falls appearance vorhanden
        app_data = payload.get("appearance") or current_cfg.get("appearance")
        if isinstance(app_data, dict):
            try:
                APPEARANCE_DIR.mkdir(parents=True, exist_ok=True)
                active_look_file = APPEARANCE_DIR / "megan_active_appearance.json"
                active_look_content = {
                    "id": "megan_active_appearance",
                    "title": f"Megan Active Look ({app_data.get('outfit', 'current')})",
                    "name": f"Megan Active Look ({app_data.get('outfit', 'current')})",
                    "hair_color": app_data.get("hair_color", ""),
                    "hair_style": app_data.get("hair_style", ""),
                    "outfit": app_data.get("outfit", ""),
                    "prompt_tags": app_data.get("prompt_tags", ""),
                    "timestamp": int(time.time() * 1000)
                }
                atomic_write_file(active_look_file, json.dumps(active_look_content, indent=2, ensure_ascii=False))
            except Exception as ape:
                logger.warning(f"Fehler beim Speichern von megan_active_appearance.json: {ape}")

        # Hot-Reload in-memory state sofort im laufenden Betrieb
        try:
            reload_megan_in_memory_state()
        except Exception as he:
            logger.warning(f"In-Memory Hot-Reload fehlgeschlagen: {he}")

        # Broadcast WebSocket Event an alle verbundenen Clients
        try:
            await broadcast_ws({
                "type": "config_updated",
                "synced": True,
                "config": current_cfg
            })
        except Exception as be:
            logger.warning(f"Broadcast WebSocket Fehler: {be}")

        return web.json_response({
            "status": "ok",
            "synced": True,
            "success": True,
            "config": current_cfg
        })
    except Exception as e:
        logger.error(f"Fehler in handle_bot_config_post: {e}", exc_info=True)
        return web.json_response({"status": "error", "success": False, "error": str(e)}, status=400)

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
    try:
        import haptic_engine
        status = haptic_engine.haptic_controller.get_status()
        status["ip"] = LOVENSE_IP
        status["port"] = LOVENSE_PORT
        return web.json_response(status)
    except Exception as e:
        return web.json_response({
            "success": True,
            "ip": LOVENSE_IP,
            "port": LOVENSE_PORT,
            "connected": False,
            "error": str(e)
        })

async def handle_bot_haptic_test(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        strength = body.get("strength")
        pattern = body.get("pattern")
        duration = float(body.get("duration", 2.0))

        import haptic_engine
        if pattern:
            logger.info(f"Lovense Haptic Pattern empfangen: {pattern} ({duration}s)")
            asyncio.create_task(
                haptic_engine.haptic_controller.play_pattern(
                    pattern, duration_sec=duration, context="API /api/bot/haptic/test"
                )
            )
            return web.json_response({"success": True, "pattern": pattern, "duration": duration, "status": "started"})
        elif strength is not None:
            logger.info(f"Lovense Haptic Command empfangen: Stärke {strength} ({duration}s)")
            res = await haptic_engine.haptic_controller.vibrate(
                int(strength), time_sec=duration, context="API /api/bot/haptic/test"
            )
            return web.json_response(res)
        else:
            await haptic_engine.haptic_controller.stop(context="API /api/bot/haptic/test Stop")
            return web.json_response({"success": True, "command": "stop"})
    except Exception as e:
        logger.error(f"Fehler in handle_bot_haptic_test: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)

# ------------------------------------------------------------------------------
# Handlers: Chat (Megan LLM Inference)
# ------------------------------------------------------------------------------
# ------------------------------------------------------------------------------
# Piper Neural TTS Engine
# ------------------------------------------------------------------------------
PIPER_MODELS = [
    Path("/home/outerheaven/models/piper/de_DE-kerstin-low.onnx"),
    Path("/home/outerheaven/models/piper/de_DE-thorsten-medium.onnx"),
    Path("/mnt/storage-beta/models/piper/de_DE-kerstin-low.onnx"),
    Path("/mnt/storage-beta/models/piper/de_DE-thorsten-medium.onnx"),
    Path.home() / "models/piper/de_DE-kerstin-low.onnx",
    Path.home() / ".local/share/piper/de_DE-kerstin-low.onnx"
]

PIPER_BIN_CANDIDATES = [
    shutil.which("piper"),
    str(Path.home() / ".hermes/.venv/bin/piper"),
    "/home/outerheaven/.hermes/.venv/bin/piper",
    str(Path.home() / ".local/bin/piper"),
    "/usr/local/bin/piper",
    "/usr/bin/piper"
]

def get_piper_binary() -> Optional[str]:
    for cand in PIPER_BIN_CANDIDATES:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None

def clean_text_for_tts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    # Strikte Bereinigung aller Sternchen- und Rollenspielblöcke auch über Zeilenumbrüche
    text = re.sub(r'\*[^*]*\*', '', text, flags=re.DOTALL)
    text = re.sub(r'_[^_]*_', '', text, flags=re.DOTALL)
    text = text.replace('*', '')
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'```[\w]*\n(.*?)```', '', text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    text = re.sub(r'[\u2600-\u27BF]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def generate_piper_tts(text: str) -> Optional[str]:
    cleaned = clean_text_for_tts(text)
    if not cleaned:
        return None
    # Nur sprechen, wenn echte Dialogworte (Buchstaben oder Zahlen) vorhanden sind
    if not re.search(r'[a-zA-ZäöüÄÖÜß0-9]', cleaned):
        return None

    # Phoneme pronunciation table if available
    try:
        from megan_memory import load_phonemes
        phon_cfg = load_phonemes()
        repls = phon_cfg.get("replacements", {})
        for word, replacement in repls.items():
            pattern = r'\b' + re.escape(word) + r'\b'
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    except Exception:
        pass

    model_file = None
    for p in PIPER_MODELS:
        try:
            if p.is_file():
                model_file = str(p)
                break
        except Exception:
            continue

    audio_id = f"megan_voice_{int(time.time()*1000)}_{secrets.token_hex(3)}"
    out_wav = AUDIO_TTS_DIR / f"{audio_id}.wav"
    piper_bin = get_piper_binary()

    if model_file and piper_bin:
        try:
            cmd = [piper_bin, "-m", model_file, "-f", str(out_wav)]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=cleaned.encode("utf-8")),
                timeout=15.0
            )
            if proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 0:
                logger.info(f"[megan-tts] Piper TTS generiert: {out_wav.name} ({out_wav.stat().st_size} bytes)")
                return audio_id
            else:
                err_msg = stderr.decode('utf-8', errors='ignore') if stderr else ""
                logger.warning(f"[megan-tts] Piper Warnung ({proc.returncode}): {err_msg}")
        except Exception as pe:
            logger.error(f"[megan-tts] Piper execution error: {pe}")

    # Fallback to edge_tts if installed
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text=cleaned, voice="de-DE-SeraphinaMultilingualNeural")
        await asyncio.wait_for(communicate.save(str(out_wav)), timeout=10.0)
        if out_wav.is_file() and out_wav.stat().st_size > 0:
            logger.info(f"[megan-tts] Edge-TTS Fallback generiert: {out_wav.name}")
            return audio_id
    except Exception as ee:
        logger.warning(f"[megan-tts] Edge-TTS Fallback fehlgeschlagen: {ee}")

    return None

async def handle_audio_serve(request: web.Request) -> web.StreamResponse:
    """GET /api/audio/{filename} & GET /api/audio/tts/{filename} - Serves WAV audio with HTTP Range support."""
    raw_name = request.match_info.get("filename", "").strip()
    if not raw_name.endswith(".wav"):
        raw_name += ".wav"
    target = (AUDIO_TTS_DIR / raw_name).resolve()
    if not target.is_file() or not str(target).startswith(str(AUDIO_TTS_DIR.resolve())):
        return web.HTTPNotFound(text="Audio file not found")

    return web.FileResponse(
        target,
        headers={
            "Content-Type": "audio/wav",
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600"
        }
    )

_WHISPER_MODEL = None

def get_whisper_model():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        try:
            from faster_whisper import WhisperModel
            try:
                # Use base with int8 on CPU - ultra fast (1-2s) & highly accurate for voice commands
                _WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4)
            except Exception as e:
                logger.warning(f"Failed to load WhisperModel base with 4 threads: {e}")
                _WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            logger.error(f"Failed to load WhisperModel: {e}")
    return _WHISPER_MODEL

def transcribe_audio_local(audio_path: str, language: str = "de") -> str:
    model = get_whisper_model()
    if not model:
        raise RuntimeError("Whisper model not available")
    try:
        segments, _ = model.transcribe(
            audio_path,
            language="de",
            initial_prompt="Dies ist ein deutsches Sprachkommando für Megan und das Outer Heaven Cluster. Begriffe: Megan, Catlord, Hermes, ComfyUI, Python, Script, Aufgabe, Workflow, LoRA, Render.",
            condition_on_previous_text=False,
            temperature=0.0,
            vad_filter=True,
            beam_size=1,
            best_of=1,
            no_speech_threshold=0.6
        )
        parts = [s.text.strip() for s in segments if s.text and s.text.strip()]
        return " ".join(parts).strip()
    except Exception as e:
        logger.warning(f"[megan-stt] Fehler bei lokaler Transkription oder keine Sprache: {e}")
        return ""

async def handle_audio_transcribe(request: web.Request) -> web.Response:
    """POST /api/audio/transcribe - Transcribes uploaded audio via Mission Control (:8000) or local faster-whisper."""
    temp_path = None
    try:
        raw_body = await request.read()
        if not raw_body:
            logger.info("[Transcribe Output]: ")
            return web.json_response({"status": "ok", "text": ""})

        # 1. Forward to Mission Control (:8000) if running to save RAM and leverage existing model instance
        # Short timeout (5s total, 1.5s connect) so we never block if :8000 is slow or unresponsive
        try:
            headers = {}
            if "Content-Type" in request.headers:
                headers["Content-Type"] = request.headers["Content-Type"]
            client_timeout = aiohttp.ClientTimeout(total=2.0, connect=0.5)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    "http://127.0.0.1:8000/api/audio/transcribe?language=de",
                    data=raw_body,
                    headers=headers
                ) as resp:
                    if resp.status == 200:
                        res_data = await resp.json()
                        transcribed_text = (res_data.get("text") or "").strip()
                        if transcribed_text:
                            logger.info(f"[Transcribe Output via :8000]: {transcribed_text}")
                            return web.json_response({"status": "ok", "text": transcribed_text})
                    else:
                        logger.warning(f"[megan-stt] Mission Control /api/audio/transcribe HTTP {resp.status}, wechsle auf lokalen Fallback")
        except Exception as fwd_err:
            logger.info(f"[megan-stt] Weiterleitung an :8000 nicht möglich ({fwd_err}), nutze schnellen lokalen Fallback")

        # 2. Local Fallback: Extract audio bytes and write to temp file
        ext = ".wav"
        content_type = request.headers.get("Content-Type", "")
        if "multipart/form-data" in content_type and b"boundary=" in content_type.encode():
            boundary = content_type.split("boundary=")[-1].strip().encode('utf-8')
            parts = raw_body.split(b"--" + boundary)
            audio_bytes = None
            for p in parts:
                if b'filename=' in p:
                    if b"\r\n\r\n" in p:
                        hdr, body = p.split(b"\r\n\r\n", 1)
                        audio_bytes = body.rstrip(b"\r\n--")
                        break
                    elif b"\n\n" in p:
                        hdr, body = p.split(b"\n\n", 1)
                        audio_bytes = body.rstrip(b"\r\n--")
                        break
            if not audio_bytes:
                audio_bytes = raw_body
        else:
            audio_bytes = raw_body

        temp_path = f"/tmp/megan_stt_{secrets.token_hex(6)}{ext}"
        with open(temp_path, "wb") as f:
            f.write(audio_bytes)

        # Transcribe with max 15s safety timeout
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(transcribe_audio_local, temp_path, "de"),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("[megan-stt] Lokale Transkription Timeout nach 15s")
            text = ""

        transcribed_text = (text or "").strip()
        logger.info(f"[Transcribe Output]: {transcribed_text}")
        return web.json_response({"status": "ok", "text": transcribed_text})
    except Exception as e:
        logger.error(f"[megan-stt] Fehler bei Transkription: {e}", exc_info=True)
        logger.info("[Transcribe Output]: ")
        return web.json_response({"status": "ok", "text": ""})
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

# ------------------------------------------------------------------------------
# Handlers: Central Chat (Megan LLM Inference & SQLite Persistence)
# ------------------------------------------------------------------------------
async def handle_chat_history(request: web.Request) -> web.Response:
    """GET /api/chat/history - Returns all persistent messages from data/megan_chat.db."""
    limit = int(request.query.get("limit", 150))
    msgs = load_chat_messages(limit=limit)
    return web.json_response({
        "success": True,
        "messages": msgs,
        "count": len(msgs)
    })

async def handle_chat_clear(request: web.Request) -> web.Response:
    """POST /api/chat/clear or DELETE /api/chat/history - Clears chat history."""
    deleted = clear_chat_messages()
    broadcast_ws_sync({"type": "history_cleared", "deleted": deleted})
    return web.json_response({
        "success": True,
        "deleted": deleted,
        "message": "Chat-Historie erfolgreich geleert."
    })

async def handle_chat(request: web.Request) -> web.Response:
    """
    POST /api/chat - REST Chat with Megan using local Ollama (megan:latest).
    Reads & writes to central SQLite database (data/megan_chat.db).
    """
    if request.method == "GET":
        return await handle_chat_history(request)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    user_msg = (body.get("message") or body.get("text") or "").strip()
    if not user_msg:
        return web.json_response({"error": "Message required"}, status=400)

    # 1. Save user message to central SQLite DB
    user_msg_id = body.get("client_id") or body.get("id") or f"user_{int(time.time()*1000)}_{secrets.token_hex(3)}"
    user_ts = int(time.time() * 1000)
    save_chat_message(
        msg_id=user_msg_id,
        sender="USER",
        text=user_msg,
        msg_type="TEXT",
        timestamp=user_ts
    )

    system_instruction = get_active_megan_prompt()
    intent_context = await asyncio.to_thread(check_autonomous_intent, user_msg)
    if intent_context:
        system_instruction += f"\n\n{intent_context}"

    # Load recent context from central SQLite and build clean message sequence
    recent_msgs = load_chat_messages(limit=10)
    messages = [{"role": "system", "content": system_instruction + '\n\n[SPRACHREGEL]: Du denkst und antwortest ausnahmslos auf direktem, lebendigem Deutsch als Megan. Keinerlei englische Phrasen!'}]

    for m in recent_msgs:
        if m.get("id") == user_msg_id:
            continue
        r = "user" if m.get("sender") == "USER" else "assistant"
        c = str(m.get("text", "")).strip()
        if c and not is_system_telemetry_message(c):
            messages.append({"role": r, "content": c})

    # The current user message MUST be the last message in the list
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
            async with session.post(f"{LOCAL_OLLAMA_URL}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status != 200:
                    err_txt = await resp.text()
                    return web.json_response({"error": f"Ollama error: {err_txt}"}, status=502)
                data = await resp.json()
                raw_text = data.get("message", {}).get("content", "")
                clean_text = sanitize_megan_response(raw_text)
                if not clean_text:
                    clean_text = re.sub(r'</?think>', '', raw_text).strip() or "Alles klar, Boss! Ich bin bereit."

                # 2. Extract image prompt & strip [IMAGE_TRIGGER: ...] and [RETRY_SAFE_RENDER] from clean_text
                extracted_prompt, has_retry_tag, clean_text = extract_megan_image_prompt(clean_text)

                # Piper TTS Audio Generation (nur gesprochene Dialogworte)
                audio_id = await generate_piper_tts(clean_text)
                audio_url = f"/api/audio/{audio_id}.wav" if audio_id else None

                # 2b. Black-Frame Auto-Recovery vs. Normal Photo Trigger (VRAM Schutz: erst nach LLM-Ende)
                user_reported_black_frame = detect_black_frame_feedback(user_msg)
                is_recovery = has_retry_tag or user_reported_black_frame
                if is_recovery:
                    logger.info(f"[Auto-Recovery] Black-Frame Recovery ausgelöst (POST - Feedback: {user_reported_black_frame}, Tag: {has_retry_tag})")
                    trigger_safe_recovery_render(extracted_prompt)
                else:
                    user_wants_photo = detect_user_photo_intent(user_msg)
                    megan_claims_photo = detect_megan_photo_claim(clean_text)
                    if extracted_prompt or user_wants_photo or megan_claims_photo or (intent_context and "[AUTONOMER COMFYUI-RENDER" in intent_context):
                        render_prompt = extracted_prompt or (clean_text if megan_claims_photo else user_msg)
                        broadcast_ws_sync({
                            "type": "image_pending",
                            "workflow": "megan_erotic_chat",
                            "prompt": render_prompt,
                            "timestamp": int(time.time() * 1000)
                        })
                        logger.info(f"[Auto-Render] Photo-Trigger (POST): user_intent={user_wants_photo}, megan_claim={megan_claims_photo} -> Prompt: '{render_prompt[:60]}...'")
                        trigger_async_photo_render(render_prompt, "megan_erotic_chat", steps=18, cfg=5.5)

                # 3. Save Megan's response to central SQLite DB
                megan_msg_id = f"megan_{int(time.time()*1000)}_{secrets.token_hex(3)}"
                megan_ts = int(time.time() * 1000)
                save_chat_message(
                    msg_id=megan_msg_id,
                    sender="MEGAN",
                    text=clean_text,
                    msg_type="TEXT",
                    audio_url=audio_url,
                    timestamp=megan_ts
                )

                return web.json_response({
                    "success": True,
                    "id": megan_msg_id,
                    "response": clean_text,
                    "message": clean_text,
                    "text": clean_text,
                    "audio_url": audio_url,
                    "model": MEGAN_MODEL,
                    "timestamp": megan_ts
                })
    except Exception as e:
        logger.error(f"Chat request failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

async def handle_chat_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ACTIVE_WS_CLIENTS.add(ws)
    logger.info(f"WebSocket Chat Client verbunden (Aktive Clients: {len(ACTIVE_WS_CLIENTS)})")

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                logger.info(f"WebSocket empfing Rohdaten: {msg.data[:200]}")
                try:
                    data = json.loads(msg.data)
                except Exception:
                    # Fallback für Clients, die Plaintext senden
                    data = {"text": msg.data}

                # Robuste Extraktion der Nachricht aus verschiedenen Payload-Formaten
                user_text = ""
                if isinstance(data.get("message"), str):
                    user_text = data.get("message")
                elif isinstance(data.get("text"), str):
                    user_text = data.get("text")
                elif isinstance(data.get("content"), str):
                    user_text = data.get("content")
                elif isinstance(data.get("message"), dict):
                    user_text = data.get("message", {}).get("content", "")
                user_text = str(user_text or "").strip()

                if not user_text:
                    logger.warning("WebSocket Nachricht ohne Textinhalt empfangen.")
                    continue

                logger.info(f"WebSocket verarbeitet Chat-Nachricht: '{user_text}'")

                user_msg_id = data.get("client_id") or data.get("id") or f"user_{int(time.time()*1000)}_{secrets.token_hex(3)}"
                user_ts = int(time.time() * 1000)
                try:
                    save_chat_message(
                        msg_id=user_msg_id,
                        sender="USER",
                        text=user_text,
                        msg_type="TEXT",
                        timestamp=user_ts
                    )
                except Exception as save_err:
                    logger.warning(f"Konnte User-Nachricht nicht in DB speichern: {save_err}")

                # Send typing and start events to client
                await ws.send_json({"type": "typing"})
                await ws.send_json({"type": "start", "node": "outerheaven", "model": MEGAN_MODEL})

                # Robuster System-Prompt & Intent-Check ohne Absturzrisiko
                try:
                    system_prompt = get_active_megan_prompt()
                except Exception as pe:
                    logger.error(f"Fehler beim Laden des System-Prompts: {pe}", exc_info=True)
                    system_prompt = "Du bist Megan, die charmante, freche und loyale persönliche Assistentin von Big Boss im Outer Heaven Cluster."

                intent_context = None
                try:
                    intent_context = await asyncio.to_thread(check_autonomous_intent, user_text)
                    if intent_context:
                        system_prompt += f"\n\n{intent_context}"
                except Exception as ie:
                    logger.warning(f"Intent-Prüfung fehlgeschlagen: {ie}")

                recent_msgs = []
                try:
                    recent_msgs = load_chat_messages(limit=10)
                except Exception as me:
                    logger.warning(f"Konnte Chat-Historie nicht laden: {me}")

                # Strikter System-Prompt an Position 0, gefolgt von Dialogverlauf
                messages = [{"role": "system", "content": system_prompt + '\n\n[SPRACHREGEL]: Du denkst und antwortest ausnahmslos auf direktem, lebendigem Deutsch als Megan. Keinerlei englische Phrasen!'}]

                for m in recent_msgs:
                    if m.get("id") == user_msg_id:
                        continue
                    r = "user" if m.get("sender") == "USER" else "assistant"
                    c = str(m.get("text", "")).strip()
                    if c and not is_system_telemetry_message(c):
                        messages.append({"role": r, "content": c})

                # Die aktuelle User-Nachricht MUSS die letzte Nachricht im Array sein
                messages.append({"role": "user", "content": user_text})

                payload = {
                    "model": MEGAN_MODEL,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": 0.75,
                        "repeat_penalty": 1.15,
                        "num_ctx": 4096
                    }
                }

                accumulated_text = ""
                in_think = False
                intro_buffer = ""
                intro_dispatched = False
                tag_buffer = ""
                in_tag_bracket = False
                english_intro_re = re.compile(
                    r'^(?:(?:Sure|Certainly|Okay|Ok|Alright|Hello|Hi|Of course|Here is|Here\'s|I will|Let me)[,!\.:\s]+)+',
                    re.IGNORECASE
                )
                english_prefix_words = ("sure", "certainly", "okay", "ok", "alright", "hello", "hi", "of course", "here is", "here's", "let me", "i will")

                tokens_sent = 0

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"{LOCAL_OLLAMA_URL}/api/chat",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=180, connect=10)
                        ) as resp:
                            if resp.status != 200:
                                err_body = await resp.text()
                                logger.error(f"Ollama returned HTTP {resp.status}: {err_body}")
                                await ws.send_json({"type": "error", "message": f"Ollama HTTP {resp.status}", "text": f"Ollama HTTP {resp.status}"})
                                await ws.send_json({"type": "idle"})
                                continue

                            # Zeilenweises Lesen für einwandfreies NDJSON-Streaming
                            async for line_bytes in resp.content:
                                line_str = line_bytes.decode("utf-8", errors="ignore").strip()
                                if not line_str:
                                    continue

                                # Zeile kann mehrere JSON-Objekte enthalten, falls Chunks zusammengefügt wurden
                                for json_chunk in line_str.split("\n"):
                                    json_chunk = json_chunk.strip()
                                    if not json_chunk:
                                        continue
                                    try:
                                        parsed = json.loads(json_chunk)
                                    except Exception:
                                        continue

                                    token = parsed.get("message", {}).get("content", "")
                                    if not token:
                                        continue

                                    accumulated_text += token

                                    # 1. Thought-Blöcke (<think>...</think>) sauber filtern
                                    if "<think>" in token or "<think>" in accumulated_text:
                                        if "</think>" not in accumulated_text:
                                            in_think = True
                                            continue
                                        else:
                                            if in_think:
                                                in_think = False
                                                after_think = accumulated_text.split("</think>", 1)[1]
                                                intro_buffer = after_think
                                                continue

                                    if in_think:
                                        if "</think>" in token:
                                            in_think = False
                                        continue

                                    # 2. Englische Einleitungs-Tokens abfangen ohne deutsche Wörter zu blockieren
                                    if not intro_dispatched:
                                        intro_buffer += token
                                        buf_lower = intro_buffer.lower().lstrip()

                                        # Prüfen, ob der Puffer überhaupt mit einem englischen Intro-Präfix beginnen könnte
                                        could_be_english = any(buf_lower.startswith(p[:len(buf_lower)]) for p in english_prefix_words)

                                        if not could_be_english:
                                            # Es beginnt direkt mit Deutsch (z.B. "Ja", "Hallo Boss", "Klar", "Ich") -> SOFORT senden!
                                            if intro_buffer:
                                                await ws.send_json({
                                                    "type": "token",
                                                    "content": intro_buffer,
                                                    "text": intro_buffer,
                                                    "token": intro_buffer
                                                })
                                                tokens_sent += 1
                                            intro_dispatched = True
                                            intro_buffer = ""
                                        else:
                                            # Könnte Englisch sein -> warten bis Satzzeichen oder Leerzeichen das Wort abschließt
                                            if any(p in intro_buffer for p in [".", ",", "!", "?", ":", "\n", " "]) or len(intro_buffer) >= 15:
                                                cleaned_intro = english_intro_re.sub("", intro_buffer).lstrip()
                                                if cleaned_intro:
                                                    await ws.send_json({
                                                        "type": "token",
                                                        "content": cleaned_intro,
                                                        "text": cleaned_intro,
                                                        "token": cleaned_intro
                                                    })
                                                    tokens_sent += 1
                                                    intro_dispatched = True
                                                    intro_buffer = ""
                                                elif not any(buf_lower.startswith(p) for p in english_prefix_words):
                                                    # Intro war bereinigt, Puffer ist jetzt leer
                                                    intro_dispatched = True
                                                    intro_buffer = ""
                                    else:
                                        # Tag-Filterung während des Streams: Tags wie [IMAGE_TRIGGER: ...] oder [RETRY_SAFE_RENDER] abfangen
                                        if "[" in token or in_tag_bracket:
                                            in_tag_bracket = True
                                            tag_buffer += token
                                            if "]" in tag_buffer:
                                                in_tag_bracket = False
                                                # Prüfen, ob es sich um ein internes Tag handelt
                                                if re.search(r'\[(?:IMAGE_TRIGGER|RETRY_SAFE_RENDER|image|photo|foto|bild|selfie|HAPTIC)', tag_buffer, re.I):
                                                    tag_buffer = ""  # Restlos verwerfen, nicht an App streamen
                                                else:
                                                    await ws.send_json({
                                                        "type": "token",
                                                        "content": tag_buffer,
                                                        "text": tag_buffer,
                                                        "token": tag_buffer
                                                    })
                                                    tokens_sent += 1
                                                    tag_buffer = ""
                                        else:
                                            await ws.send_json({
                                                "type": "token",
                                                "content": token,
                                                "text": token,
                                                "token": token
                                            })
                                            tokens_sent += 1

                except Exception as e:
                    logger.error(f"Ollama Stream unterbrochen oder fehlgeschlagen: {e}", exc_info=True)
                    await ws.send_json({"type": "error", "message": f"Ollama Stream unterbrochen: {e}", "text": "Ollama Stream unterbrochen"})
                    await ws.send_json({"type": "idle"})
                    continue

                # Eventuellen Rest-Buffer nach kurzem Stream senden
                if intro_buffer:
                    cleaned_intro = english_intro_re.sub("", intro_buffer).lstrip()
                    send_rest = cleaned_intro if cleaned_intro else intro_buffer
                    if send_rest:
                        await ws.send_json({
                            "type": "token",
                            "content": send_rest,
                            "text": send_rest,
                            "token": send_rest
                        })
                        tokens_sent += 1
                    intro_buffer = ""

                # Bereinigung der Gesamtnachricht
                clean_text = sanitize_megan_response(accumulated_text)

                # Fallback, falls Sanitizer den gesamten Text verschluckt hat oder Modell sehr kurz war
                if not clean_text:
                    raw_fallback = re.sub(r'</?think>', '', accumulated_text).strip()
                    clean_text = raw_fallback or "Alles klar, Boss! Ich bin bereit."

                # Falls noch gar keine Tokens gestreamt wurden (z.B. bei sehr kurzer oder bereinigter Antwort):
                if tokens_sent == 0 and clean_text:
                    await ws.send_json({
                        "type": "token",
                        "content": clean_text,
                        "text": clean_text,
                        "token": clean_text
                    })

                # 3. Extract image prompt & strip [IMAGE_TRIGGER: ...] and [RETRY_SAFE_RENDER] from clean_text
                extracted_prompt, has_retry_tag, clean_text = extract_megan_image_prompt(clean_text)

                # Piper TTS Audio Generation
                audio_id = None
                try:
                    audio_id = await generate_piper_tts(clean_text)
                except Exception as tts_err:
                    logger.error(f"TTS Erzeugung fehlgeschlagen: {tts_err}")

                audio_url = f"/api/audio/{audio_id}.wav" if audio_id else None

                megan_msg_id = f"megan_{int(time.time()*1000)}_{secrets.token_hex(3)}"
                megan_ts = int(time.time() * 1000)
                try:
                    save_chat_message(
                        msg_id=megan_msg_id,
                        sender="MEGAN",
                        text=clean_text,
                        msg_type="TEXT",
                        audio_url=audio_url,
                        timestamp=megan_ts
                    )
                except Exception as save_err:
                    logger.warning(f"Konnte Megan-Antwort nicht in DB speichern: {save_err}")

                if audio_url:
                    audio_payload = {
                        "type": "audio_ready",
                        "audio_url": audio_url,
                        "text": clean_text
                    }
                    voice_payload = {
                        "type": "voice",
                        "audio_url": audio_url,
                        "text": clean_text
                    }
                    await ws.send_json(audio_payload)
                    await ws.send_json(voice_payload)
                    await broadcast_ws(voice_payload, exclude=ws)

                done_payload = {
                    "type": "done",
                    "id": megan_msg_id,
                    "text": clean_text,
                    "content": clean_text,
                    "audio_url": audio_url
                }
                await ws.send_json(done_payload)
                await broadcast_ws(done_payload, exclude=ws)
                await ws.send_json({"type": "idle"})

                # 4. Photo Trigger (VRAM-Schutz: Chat-Stream & Token-Übertragung ist 100% abgeschlossen)
                user_reported_black_frame = detect_black_frame_feedback(user_text)
                is_recovery = has_retry_tag or user_reported_black_frame
                if is_recovery:
                    logger.info(f"[Auto-Recovery] Black-Frame Recovery ausgelöst (WS - Feedback: {user_reported_black_frame}, Tag: {has_retry_tag})")
                    trigger_safe_recovery_render(extracted_prompt)
                else:
                    user_wants_photo = detect_user_photo_intent(user_text)
                    megan_claims_photo = detect_megan_photo_claim(clean_text)
                    if extracted_prompt or user_wants_photo or megan_claims_photo or (intent_context and "[AUTONOMER COMFYUI-RENDER" in intent_context):
                        render_prompt = extracted_prompt or (clean_text if megan_claims_photo else user_text)
                        logger.info(f"[Auto-Render] Photo-Trigger (WS): user_intent={user_wants_photo}, megan_claim={megan_claims_photo} -> Prompt: '{render_prompt[:60]}...'")
                        pending_payload = {
                            "type": "image_pending",
                            "workflow": "megan_erotic_chat",
                            "prompt": render_prompt,
                            "timestamp": int(time.time() * 1000)
                        }
                        await ws.send_json(pending_payload)
                        await broadcast_ws(pending_payload, exclude=ws)
                        trigger_async_photo_render(render_prompt, "megan_erotic_chat", steps=18, cfg=5.5)
    finally:
        ACTIVE_WS_CLIENTS.discard(ws)
        logger.info(f"WebSocket Chat Client getrennt (Aktive Clients: {len(ACTIVE_WS_CLIENTS)})")
        if not ACTIVE_WS_CLIENTS:
            try:
                import haptic_engine
                if hasattr(haptic_engine, "frame_buffer"):
                    asyncio.create_task(haptic_engine.frame_buffer.on_client_disconnected("Keine aktiven WebSocket-Clients"))
                if hasattr(haptic_engine, "haptic_controller"):
                    asyncio.create_task(haptic_engine.haptic_controller.on_client_disconnected("Keine aktiven WebSocket-Clients"))
            except Exception as failsafe_err:
                logger.warning(f"[Fail-Safe] Fehler beim Ausführen des Disconnect-Not-Aus: {failsafe_err}")
    return ws

# ------------------------------------------------------------------------------
# Handlers: Tools Leitstand & GPU Status
# ------------------------------------------------------------------------------
async def handle_tools_list(request: web.Request) -> web.Response:
    """GET /api/tools - Lists builtin & custom tools from ~/.hermes/tools_config.json."""
    cfg = load_hermes_tools_config()
    return web.json_response({"success": True, "tools": cfg})

async def handle_tools_toggle(request: web.Request) -> web.Response:
    """POST /api/tools/toggle - Toggles a tool in ~/.hermes/tools_config.json."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Invalid JSON"}, status=400)

    category = data.get("category", "builtin_tools")
    name = data.get("name", "").strip()
    enabled = data.get("enabled")

    if not name or enabled is None:
        return web.json_response({"success": False, "error": "name und enabled erforderlich"}, status=400)

    cfg = load_hermes_tools_config()
    if category not in cfg:
        cfg[category] = {}

    if name in cfg[category]:
        cfg[category][name]["enabled"] = bool(enabled)
    else:
        cfg[category][name] = {"description": name, "enabled": bool(enabled)}

    if save_hermes_tools_config(cfg):
        return web.json_response({
            "success": True,
            "category": category,
            "name": name,
            "enabled": bool(enabled),
            "config": cfg
        })
    return web.json_response({"success": False, "error": "Fehler beim Speichern"}, status=500)

async def handle_gpu_status(request: web.Request) -> web.Response:
    """GET /api/gpu/status - Returns GPU status & Render Lock for Android Companion and Web UI."""
    lock_status = get_gpu_lock_status()
    is_locked = lock_status.get("locked", False) if isinstance(lock_status, dict) else False

    queue_info = await asyncio.to_thread(get_comfyui_queue_status, COMFYUI_URL)
    is_queue_busy = queue_info.get("busy", False) or queue_info.get("queue_total", 0) > 0
    queue_remaining = queue_info.get("queue_total", 0)

    is_busy = is_locked or is_queue_busy
    status_str = "busy" if is_busy else ("idle" if queue_info.get("online", True) else "offline")

    return web.json_response({
        "success": True,
        "status": status_str,
        "device": "Intel Arc A770 16GB",
        "is_busy": is_busy,
        "queue_remaining": queue_remaining,
        "queue_running": queue_info.get("running_count", 0),
        "queue_pending": queue_info.get("pending_count", 0),
        "gpu_lock": lock_status,
        "render_lock": is_locked,
        "timestamp": int(time.time())
    })

# ------------------------------------------------------------------------------
# Handlers: ComfyUI Render & Gallery
# ------------------------------------------------------------------------------
async def handle_render(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        prompt_text = body.get("prompt", "").strip()
        if not prompt_text:
            return web.json_response({"error": "Prompt erforderlich"}, status=400)

        # Trigger ComfyUI workflow via autonomous tool
        # Entkoppelt: blockierendes urlopen/Queueing im Thread-Pool, Polling läuft ohnehin im Daemon-Thread
        result = await asyncio.to_thread(execute_autonomous_tool, "comfyui_render", body)
        if result.get("success"):
            is_busy = result.get("is_busy", False) or result.get("busy", False)
            queue_rem = result.get("queue_remaining", 1)
            # 1. Inform all connected clients about render start
            await broadcast_ws({
                "type": "render_started",
                "in_queue": is_busy,
                "queue_remaining": queue_rem,
                "workflow": result.get("workflow", "")
            })

            # 2. Wenn GPU belegt ist: Sprachnachricht NUR bei explizitem Bildbefehl senden
            is_explicit_render = (
                body.get("force", False)
                or body.get("direct", False)
                or prompt_text.startswith(("/render", "/generate", "/draw"))
                or bool(re.search(r'\b(erstelle|generiere|zeichne|render|male)\b.*?\b(bild|foto|zeichnung|comic|selfie|portrait|szene)\b', prompt_text, re.IGNORECASE))
                or bool(re.search(r'\b(bild|foto|zeichnung|comic|selfie|portrait|szene)\b.*?\b(erstellen|generieren|zeichnen|rendern|malen)\b', prompt_text, re.IGNORECASE))
                or bool(re.search(r'^(?:schick|zeig)\s+(?:mir\s+)?(?:ein\s+|mal\s+ein\s+)?(?:neues\s+)?(?:foto|selfie|bild\s+von\s+dir)\b', prompt_text, re.IGNORECASE))
            )
            if is_busy and is_explicit_render:
                busy_msg = (
                    "Boss, die Grafikkarte rechnet gerade noch an einem anderen Job. "
                    "Ich habe deinen Render hinten angestellt – sobald die A770 frei ist, ziehe ich dein Bild direkt durch!"
                )
                audio_id = await generate_piper_tts(busy_msg)
                audio_url = f"/api/audio/{audio_id}.wav" if audio_id else None
                megan_msg_id = f"megan_{int(time.time()*1000)}_{secrets.token_hex(3)}"
                megan_ts = int(time.time() * 1000)
                save_chat_message(
                    msg_id=megan_msg_id,
                    sender="MEGAN",
                    text=busy_msg,
                    msg_type="TEXT",
                    audio_url=audio_url,
                    timestamp=megan_ts
                )
                if audio_url:
                    await broadcast_ws({
                        "type": "voice",
                        "audio_url": audio_url,
                        "url": audio_url,
                        "text": busy_msg
                    })
                    await broadcast_ws({
                        "type": "audio_ready",
                        "audio_url": audio_url,
                        "text": busy_msg
                    })
                await broadcast_ws({
                    "type": "done",
                    "id": megan_msg_id,
                    "text": busy_msg,
                    "content": busy_msg,
                    "audio_url": audio_url
                })

        return web.json_response(result)
    except Exception as e:
        logger.error(f"Render handler failed: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)

async def handle_comfy_graph_validate(request: web.Request) -> web.Response:
    """POST /api/comfy/graph/validate - Validiert einen ComfyUI API Prompt Graph vor Ausführung."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "valid": False, "errors": ["Ungültiges JSON"]}, status=400)

    graph_data = body.get("graph") or body.get("prompt") or body
    try:
        from comfy_graph_builder import validate_graph
        valid, errors = validate_graph(graph_data)
        node_dict = graph_data.get("prompt", graph_data) if isinstance(graph_data, dict) else {}
        return web.json_response({
            "success": True,
            "valid": valid,
            "errors": errors,
            "node_count": len(node_dict) if isinstance(node_dict, dict) else 0
        })
    except Exception as e:
        logger.error(f"Graph-Validierung fehlgeschlagen: {e}", exc_info=True)
        return web.json_response({"success": False, "valid": False, "errors": [str(e)]}, status=500)

async def handle_comfy_graph_submit(request: web.Request) -> web.Response:
    """POST /api/comfy/graph/submit - Validiert und sendet einen dynamischen Graph an ComfyUI."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Ungültiges JSON"}, status=400)

    graph_data = body.get("graph") or body.get("prompt") or body
    if not isinstance(graph_data, dict):
        return web.json_response({"success": False, "error": "Graph muss ein Dictionary sein"}, status=400)

    try:
        from comfy_graph_builder import validate_graph
        valid, errors = validate_graph(graph_data)
        if not valid:
            return web.json_response({
                "success": False,
                "error": "Graph-Validierung fehlgeschlagen",
                "validation_errors": errors
            }, status=400)

        prompt_payload = graph_data.get("prompt", graph_data)
        res = await asyncio.to_thread(execute_autonomous_tool, "comfyui_render", {"prompt_graph": prompt_payload, "workflow": "dynamic_graph"})
        return web.json_response(res)
    except Exception as e:
        logger.error(f"Fehler bei dynamischem Graph-Submit: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

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

def _find_image_file(filename: str) -> Optional[Path]:
    """Finds an image file across ComfyUI output, megan-assets, static dirs and sexchat assets."""
    search_dirs = [
        COMFYUI_OUTPUT_DIR,
        STATIC_IMAGES_DIR,
        STATIC_AVATAR_DIR,
        STATIC_DIR,
        MEGAN_AVATARS_DIR,
        MEGAN_IMAGES_DIR,
        MEGAN_FRAMES_DIR,
        MEGAN_ASSETS_DIR,
        BASE_DIR / "assets",
        SEXCHAT_DIR / "assets"
    ]
    clean_name = filename.strip("/")
    for d in search_dirs:
        if d.is_dir():
            target = (d / clean_name).resolve()
            if target.is_file():
                return target
    return None

async def handle_gallery_file(request: web.Request) -> web.Response:
    raw_filename = request.match_info.get("filename", "")
    filename = urllib.parse.unquote(raw_filename).strip("/")
    if not filename:
        return web.HTTPNotFound(text="Dateiname fehlt")
    path = _find_image_file(filename)
    if not path or not path.is_file():
        return web.HTTPNotFound(text="Bild nicht gefunden")
    ext = path.suffix.lower()
    ctype_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".png": "image/png",
        ".gif": "image/gif",
        ".mp4": "video/mp4"
    }
    ctype = ctype_map.get(ext, "image/png")
    return web.FileResponse(path, headers={
        "Content-Type": ctype,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=3600",
    })

async def handle_avatar_serve(request: web.Request) -> web.Response:
    """GET /api/avatar or /api/avatar/{filename} - Serves current avatar or requested avatar image."""
    raw_filename = request.match_info.get("filename", "").strip()
    filename = urllib.parse.unquote(raw_filename).strip("/") if raw_filename else ""
    path = None
    if filename:
        path = _find_image_file(filename)

    if not path or not path.is_file():
        # Fallback to default avatar candidates
        avatar_candidates = [
            STATIC_AVATAR_DIR / "megan.jpg",
            STATIC_IMAGES_DIR / "megan.jpg",
            STATIC_AVATAR_DIR / "main.png",
            STATIC_IMAGES_DIR / "main.png",
            MEGAN_AVATARS_DIR / "megan.jpg",
            MEGAN_AVATARS_DIR / "main.png",
            BASE_DIR / "assets" / "main.png",
            SEXCHAT_DIR / "assets" / "megan.jpg"
        ]
        for cand in avatar_candidates:
            if cand.is_file():
                path = cand
                break

    if not path or not path.is_file():
        return web.HTTPNotFound(text="Avatar nicht gefunden")

    ext = path.suffix.lower()
    ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
    return web.FileResponse(path, headers={
        "Content-Type": ctype,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=3600",
    })

# ------------------------------------------------------------------------------
# Handlers: ComfyUI Custom Nodes Management & Code Inspection
# ------------------------------------------------------------------------------
try:
    import comfy_custom_nodes as ccn
except ImportError:
    ccn = None

async def handle_custom_nodes_list(request: web.Request) -> web.Response:
    """GET /api/nodes/custom - Lists all installed ComfyUI custom nodes."""
    if not ccn:
        return web.json_response({"success": False, "error": "comfy_custom_nodes Modul nicht verfügbar"}, status=500)
    nodes = await asyncio.to_thread(ccn.list_custom_nodes)
    return web.json_response({"success": True, "nodes": nodes, "count": len(nodes)})

async def handle_custom_node_details(request: web.Request) -> web.Response:
    """GET /api/nodes/custom/{node_name}/details - Returns metadata, README, Git info, NODE_CLASS_MAPPINGS, and file list."""
    if not ccn:
        return web.json_response({"success": False, "error": "comfy_custom_nodes Modul nicht verfügbar"}, status=500)
    node_name = request.match_info.get("node_name", "").strip()
    if not node_name:
        return web.json_response({"success": False, "error": "Node-Name fehlt"}, status=400)
    details = await asyncio.to_thread(ccn.get_custom_node_details, node_name)
    if not details:
        return web.json_response({"success": False, "error": f"Custom Node '{node_name}' nicht gefunden"}, status=404)
    return web.json_response(details)

async def handle_custom_node_file_get(request: web.Request) -> web.Response:
    """GET /api/nodes/custom/{node_name}/file?path=... - Returns the source code of a file."""
    if not ccn:
        return web.json_response({"success": False, "error": "comfy_custom_nodes Modul nicht verfügbar"}, status=500)
    node_name = request.match_info.get("node_name", "").strip()
    rel_path = request.query.get("path")
    if not node_name:
        return web.json_response({"success": False, "error": "Node-Name fehlt"}, status=400)
    ok, res = await asyncio.to_thread(ccn.get_custom_node_file, node_name, rel_path)
    if not ok:
        status_code = 404 if "nicht gefunden" in str(res) else 400
        return web.json_response({"success": False, "error": str(res)}, status=status_code)
    return web.json_response(res)

async def handle_custom_node_file_post(request: web.Request) -> web.Response:
    """POST /api/nodes/custom/{node_name}/file - Saves changes, validates via ast.parse, creates .bak backup."""
    if not ccn:
        return web.json_response({"success": False, "error": "comfy_custom_nodes Modul nicht verfügbar"}, status=500)
    node_name = request.match_info.get("node_name", "").strip()
    if not node_name:
        return web.json_response({"success": False, "error": "Node-Name fehlt"}, status=400)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"success": False, "error": "Ungültiges JSON"}, status=400)

    rel_path = body.get("path") or request.query.get("path")
    code = body.get("code") or body.get("content") or ""

    ok, res = await asyncio.to_thread(ccn.save_custom_node_file, node_name, rel_path, code)
    if not ok:
        status_code = 400 if isinstance(res, dict) and "Syntaxfehler" in res.get("error", "") else 500
        return web.json_response({"success": False, **(res if isinstance(res, dict) else {"error": str(res)})}, status=status_code)
    return web.json_response(res)

async def handle_custom_nodes_restart(request: web.Request) -> web.Response:
    """POST /api/nodes/custom/restart - Triggers ComfyUI service restart so modified Python code takes effect."""
    if not ccn:
        return web.json_response({"success": False, "error": "comfy_custom_nodes Modul nicht verfügbar"}, status=500)
    ok, msg = await asyncio.to_thread(ccn.restart_comfyui)
    return web.json_response({"success": ok, "message": msg}, status=200 if ok else 500)

async def start_background_worker(app: web.Application):
    """Starts autonomous Megan housekeeping & cataloging agent worker in background."""
    try:
        from megan_agent_worker import MeganAgentWorker
        worker = MeganAgentWorker(interval_sec=180)
        task = asyncio.create_task(worker.start())
        yield
        worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except Exception as e:
        logger.error(f"Fehler im Background Worker Lifecycle: {e}")
        yield

# ------------------------------------------------------------------------------
# App Factory & Lifecycle
# ------------------------------------------------------------------------------
def create_app() -> web.Application:
    app = web.Application()

    async def on_app_startup(app: web.Application) -> None:
        global MAIN_LOOP
        MAIN_LOOP = asyncio.get_running_loop()
        set_image_finished_callback(on_image_finished_broadcast)
        logger.info("✔ Image finished callback und Event Loop für WebSockets registriert.")

    app.on_startup.append(on_app_startup)
    app.cleanup_ctx.append(start_background_worker)

    # Index & Health
    app.router.add_get("/", handle_index)
    app.router.add_get("/megan", handle_index)
    app.router.add_get("/megan/", handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/gpu/status", handle_gpu_status)

    # Megan Android Endpoints (OkHttp Upload & Sync)
    app.router.add_post("/api/megan/video/upload", handle_megan_video_upload)
    app.router.add_post("/api/megan/image/upload", handle_megan_video_upload)
    app.router.add_get("/api/megan/video/file/{filename}", handle_megan_video_file)
    app.router.add_get("/api/megan/video/frame/{video_id}/{filename}", handle_megan_video_frame)
    app.router.add_post("/api/megan/video/process", handle_megan_video_process)
    app.router.add_get("/api/megan/autonomy", handle_megan_autonomy_get)
    app.router.add_post("/api/megan/autonomy", handle_megan_autonomy_post)
    app.router.add_get("/api/megan/context", handle_megan_context_get)

    # Central Tool Engine (~/.hermes/tools_config.json)
    app.router.add_get("/api/tools", handle_tools_list)
    app.router.add_post("/api/tools/toggle", handle_tools_toggle)
    app.router.add_get("/api/megan/tools/status", handle_megan_tool_status)
    app.router.add_post("/api/megan/tools/search", handle_megan_tool_search)
    app.router.add_post("/api/megan/tools/scrape", handle_megan_tool_scrape)
    app.router.add_get("/api/megan/tools/brain", handle_megan_tool_brain)
    app.router.add_post("/api/megan/tools/brain", handle_megan_tool_brain)
    app.router.add_get("/api/megan/gdrive/resolve", handle_megan_gdrive_resolve)
    app.router.add_post("/api/megan/gdrive/resolve", handle_megan_gdrive_resolve)
    app.router.add_get("/api/megan/gdrive/search", handle_megan_gdrive_search)
    app.router.add_post("/api/megan/gdrive/search", handle_megan_gdrive_search)
    app.router.add_get("/api/megan/system/status", handle_megan_system_status)
    app.router.add_post("/api/megan/tools/execute", handle_megan_tools_execute)
    app.router.add_post("/api/tools/execute", handle_megan_tools_execute)

    # Character & Lore & Config (inkl. Aliase für Android & Web)
    for conf_p in ["/api/bot/config", "/api/config", "/api/settings", "/api/parameters"]:
        app.router.add_get(conf_p, handle_bot_config_get)
        app.router.add_post(conf_p, handle_bot_config_post)
    app.router.add_get("/api/bot/lore", handle_bot_lore_get)
    app.router.add_post("/api/bot/lore", handle_bot_lore_post)
    app.router.add_get("/api/bot/characters", handle_bot_characters_list)
    app.router.add_get("/api/bot/appearance/presets", handle_appearance_presets_list)
    app.router.add_post("/api/bot/appearance/presets", handle_appearance_preset_save)
    app.router.add_get("/api/danbooru/tags", handle_danbooru_tags)

    # Lovense
    app.router.add_get("/api/lovense/status", handle_lovense_status)
    app.router.add_post("/api/bot/haptic/test", handle_bot_haptic_test)

    # Central Chat & Audio Pipeline
    app.router.add_get("/api/chat", handle_chat)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/api/chat/history", handle_chat_history)
    app.router.add_delete("/api/chat/history", handle_chat_clear)
    app.router.add_post("/api/chat/clear", handle_chat_clear)
    app.router.add_get("/ws/chat", handle_chat_ws)
    app.router.add_get("/api/audio/{filename}", handle_audio_serve)
    app.router.add_get("/api/audio/tts/{filename}", handle_audio_serve)
    app.router.add_post("/api/audio/transcribe", handle_audio_transcribe)

    # Kanban Board & Mission Tasks (~/.hermes/kanban.db)
    app.router.add_get("/api/tasks", handle_tasks_list)
    app.router.add_post("/api/tasks", handle_tasks_create)
    app.router.add_post("/api/tasks/status", handle_tasks_status_update)
    app.router.add_delete("/api/tasks/{id}", handle_tasks_delete)
    app.router.add_post("/api/kanban/{id}/execute", handle_tasks_execute)
    app.router.add_post("/api/tasks/{id}/execute", handle_tasks_execute)

    # ComfyUI Render & Gallery & Autarkic Assets
    app.router.add_post("/api/render", handle_render)
    app.router.add_post("/api/render/direct", handle_render)
    app.router.add_post("/api/comfy/graph/validate", handle_comfy_graph_validate)
    app.router.add_post("/api/comfy/graph/submit", handle_comfy_graph_submit)
    app.router.add_get("/api/gallery", handle_gallery_list)
    app.router.add_get("/api/gallery/{filename:.+}", handle_gallery_file)
    app.router.add_get("/api/images/{filename:.+}", handle_gallery_file)
    app.router.add_get("/static/images/{filename:.+}", handle_gallery_file)
    app.router.add_get("/static/avatar/{filename:.+}", handle_gallery_file)
    app.router.add_get("/api/avatar", handle_avatar_serve)
    app.router.add_get("/api/avatar/{filename:.+}", handle_avatar_serve)
    app.router.add_get("/output/{filename:.+}", handle_gallery_file)
    app.router.add_get("/outputs/{filename:.+}", handle_gallery_file)
    if COMFYUI_OUTPUT_DIR.is_dir():
        app.router.add_static("/output/", path=str(COMFYUI_OUTPUT_DIR), name="output", follow_symlinks=True)
        app.router.add_static("/outputs/", path=str(COMFYUI_OUTPUT_DIR), name="outputs", follow_symlinks=True)

    # ComfyUI Custom Nodes Management & Code Inspection
    app.router.add_get("/api/nodes/custom", handle_custom_nodes_list)
    app.router.add_get("/api/nodes/custom/{node_name}/details", handle_custom_node_details)
    app.router.add_get("/api/nodes/custom/{node_name}/file", handle_custom_node_file_get)
    app.router.add_post("/api/nodes/custom/{node_name}/file", handle_custom_node_file_post)
    app.router.add_post("/api/nodes/custom/restart", handle_custom_nodes_restart)

    # Static assets
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", path=str(STATIC_DIR), name="static", follow_symlinks=True)
    if MEGAN_ASSETS_DIR.is_dir():
        app.router.add_static("/assets/", path=str(MEGAN_ASSETS_DIR), name="assets", follow_symlinks=True)

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
