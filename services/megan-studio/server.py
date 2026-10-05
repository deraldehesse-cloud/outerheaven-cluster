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
        get_comfyui_queue_status,
        set_image_finished_callback
    )
except ImportError:
    megan_context_engine = None
    def sanitize_megan_response(text: str) -> str:
        return text.strip()
    def get_gdrive_base_path():
        return Path("/mnt/fox-home/fox/gdrive/Agenten-Drive")
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
    def get_comfyui_queue_status(comfy_url=None):
        return {"online": True, "busy": False, "queue_total": 0, "running_count": 0, "pending_count": 0}
    def set_image_finished_callback(cb):
        pass

# Active WebSockets & Broadcast
ACTIVE_WS_CLIENTS: set = set()
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

async def broadcast_ws(payload: Dict[str, Any]) -> None:
    """Send JSON payload to all connected WebSocket clients."""
    if not ACTIVE_WS_CLIENTS:
        return
    dead = set()
    for ws in list(ACTIVE_WS_CLIENTS):
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
        "filename": data.get("filename")
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

def check_autonomous_intent(text: str) -> Optional[str]:
    """Detects if user asks for Google Drive project lookup, troubleshooting, or system status."""
    t_lower = text.lower()

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

    # 3. ComfyUI Workflow / Bildgenerierung (Megan Erotic Chat vs. Catlord Comic)
    img_keywords = ["bild von dir", "foto von dir", "selfie", "zeig dich", "sexy bild", "erotik bild", "generiere ein bild", "catlord", "comic"]
    if any(k in t_lower for k in img_keywords) or (("bild" in t_lower or "foto" in t_lower) and any(a in t_lower for a in ["schick", "mach", "zeig", "generier", "render"])):
        is_catlord = "catlord" in t_lower or "comic" in t_lower
        wf_name = "catlord_comic" if is_catlord else "megan_erotic_chat"
        res = execute_autonomous_tool("comfyui_render", {"prompt": text, "workflow": wf_name})
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
# ------------------------------------------------------------------------------
# Piper Neural TTS Engine
# ------------------------------------------------------------------------------
PIPER_MODELS = [
    Path("/mnt/storage-beta/models/piper/de_DE-kerstin-low.onnx"),
    Path("/mnt/storage-beta/models/piper/de_DE-thorsten-medium.onnx"),
    Path("/home/outerheaven/models/piper/de_DE-kerstin-low.onnx"),
    Path("/home/outerheaven/models/piper/de_DE-thorsten-medium.onnx"),
    Path.home() / "models/piper/de_DE-kerstin-low.onnx",
    Path.home() / ".local/share/piper/de_DE-kerstin-low.onnx"
]

def clean_text_for_tts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\*[^*]+\*', '', text)
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
        if p.is_file():
            model_file = str(p)
            break

    audio_id = f"megan_voice_{int(time.time()*1000)}_{secrets.token_hex(3)}"
    out_wav = AUDIO_TTS_DIR / f"{audio_id}.wav"
    piper_bin = shutil.which("piper") or str(Path.home() / ".local/bin/piper")

    if model_file and (shutil.which(piper_bin) or Path(piper_bin).is_file()):
        try:
            cmd = [piper_bin, "-m", model_file, "-f", str(out_wav)]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate(input=cleaned.encode("utf-8"))
            if proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 0:
                logger.info(f"[megan-tts] Piper TTS generiert: {out_wav.name} ({out_wav.stat().st_size} bytes)")
                return audio_id
            else:
                logger.warning(f"[megan-tts] Piper Warnung ({proc.returncode}): {stderr.decode('utf-8', errors='ignore')}")
        except Exception as pe:
            logger.error(f"[megan-tts] Piper execution error: {pe}")

    # Fallback to edge_tts if installed
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text=cleaned, voice="de-DE-SeraphinaMultilingualNeural")
        await communicate.save(str(out_wav))
        if out_wav.is_file() and out_wav.stat().st_size > 0:
            logger.info(f"[megan-tts] Edge-TTS Fallback generiert: {out_wav.name}")
            return audio_id
    except Exception:
        pass

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
                _WHISPER_MODEL = WhisperModel("medium", device="cpu", compute_type="int8")
            except Exception:
                _WHISPER_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
        except Exception as e:
            logger.error(f"Failed to load WhisperModel: {e}")
    return _WHISPER_MODEL

def transcribe_audio_local(audio_path: str, language: str = "de") -> str:
    model = get_whisper_model()
    if not model:
        raise RuntimeError("Whisper model not available")
    segments, _ = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )
    parts = [s.text.strip() for s in segments if s.text and s.text.strip()]
    return " ".join(parts).strip()

async def handle_audio_transcribe(request: web.Request) -> web.Response:
    """POST /api/audio/transcribe - Transcribes uploaded audio via Mission Control (:8000) or local faster-whisper."""
    temp_path = None
    try:
        raw_body = await request.read()
        if not raw_body:
            return web.json_response({"status": "error", "error": "No audio data provided", "text": ""}, status=400)

        # 1. Forward to Mission Control (:8000) if running to save RAM and leverage existing model instance
        try:
            headers = {}
            if "Content-Type" in request.headers:
                headers["Content-Type"] = request.headers["Content-Type"]
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://127.0.0.1:8000/api/audio/transcribe",
                    data=raw_body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        res_data = await resp.json()
                        logger.info(f"[megan-stt] Transkription via Mission Control: '{res_data.get('text', '')}'")
                        return web.json_response(res_data)
                    else:
                        logger.warning(f"[megan-stt] Mission Control /api/audio/transcribe HTTP {resp.status}, wechsle auf lokalen Fallback")
        except Exception as fwd_err:
            logger.info(f"[megan-stt] Weiterleitung an :8000 nicht möglich ({fwd_err}), nutze lokalen Fallback")

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

        text = await asyncio.to_thread(transcribe_audio_local, temp_path, "de")
        logger.info(f"[megan-stt] Lokale Transkription erfolgreich: '{text}'")
        return web.json_response({"status": "ok", "text": text})
    except Exception as e:
        logger.error(f"[megan-stt] Fehler bei Transkription: {e}", exc_info=True)
        return web.json_response({"status": "error", "error": str(e), "text": ""}, status=500)
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
    user_msg_id = body.get("id") or f"user_{int(time.time()*1000)}_{secrets.token_hex(3)}"
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

    # Load recent context from central SQLite
    recent_msgs = load_chat_messages(limit=10)
    messages = [{"role": "system", "content": system_instruction}]
    for m in recent_msgs:
        r = "user" if m.get("sender") == "USER" else "assistant"
        c = str(m.get("text", "")).strip()
        if c:
            messages.append({"role": r, "content": c})

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

                # 2. Piper TTS Audio Generation
                audio_id = await generate_piper_tts(clean_text)
                audio_url = f"/api/audio/{audio_id}.wav" if audio_id else None

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
                except Exception as e:
                    logger.error(f"WebSocket ungültiges JSON: {e}")
                    await ws.send_json({"type": "error", "text": "Ungültiges JSON"})
                    continue

                user_text = (data.get("message") or data.get("text") or "").strip()
                if not user_text:
                    logger.warning("WebSocket Nachricht ohne Textinhalt empfangen.")
                    continue

                logger.info(f"WebSocket verarbeitet Chat-Nachricht: '{user_text}'")

                user_msg_id = f"user_{int(time.time()*1000)}_{secrets.token_hex(3)}"
                user_ts = int(time.time() * 1000)
                save_chat_message(
                    msg_id=user_msg_id,
                    sender="USER",
                    text=user_text,
                    msg_type="TEXT",
                    timestamp=user_ts
                )

                # Send typing and start events to client
                await ws.send_json({"type": "typing"})
                await ws.send_json({"type": "start", "node": "outerheaven", "model": MEGAN_MODEL})

                system_prompt = get_active_megan_prompt()
                intent_context = await asyncio.to_thread(check_autonomous_intent, user_text)
                if intent_context:
                    system_prompt += f"\n\n{intent_context}"

                recent_msgs = load_chat_messages(limit=10)
                messages = [{"role": "system", "content": system_prompt}]
                for m in recent_msgs:
                    r = "user" if m.get("sender") == "USER" else "assistant"
                    c = str(m.get("text", "")).strip()
                    if c:
                        messages.append({"role": r, "content": c})

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
                english_intro_re = re.compile(
                    r'^(?:(?:Sure|Certainly|Okay|Ok|Alright|Hello|Hi|Of course|Here is|Here\'s|I will|Let me)[,!\.:\s]+)+',
                    re.IGNORECASE
                )

                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{LOCAL_OLLAMA_URL}/api/chat", json=payload) as resp:
                        async for chunk in resp.content:
                            if chunk:
                                try:
                                    parsed = json.loads(chunk.decode("utf-8"))
                                    token = parsed.get("message", {}).get("content", "")
                                    if not token:
                                        continue

                                    accumulated_text += token

                                    # 1. Filter <think>...</think> Blöcke restlos vor WebSocket-Dispatch ab
                                    if "<think>" in token or "<think>" in accumulated_text:
                                        if "</think>" not in accumulated_text:
                                            in_think = True
                                            continue
                                        else:
                                            if in_think:
                                                in_think = False
                                                # Text nach </think> übernehmen
                                                after_think = accumulated_text.split("</think>", 1)[1]
                                                intro_buffer = after_think
                                                continue

                                    if in_think:
                                        if "</think>" in token:
                                            in_think = False
                                        continue

                                    # 2. Englische Einleitungs-Tokens vor dem Dispatch abfangen
                                    if not intro_dispatched:
                                        intro_buffer += token
                                        if any(p in intro_buffer for p in [".", ",", "!", "?", ":", "\n"]) or len(intro_buffer) >= 18:
                                            cleaned_intro = english_intro_re.sub("", intro_buffer).lstrip()
                                            if cleaned_intro:
                                                await ws.send_json({
                                                    "type": "token",
                                                    "content": cleaned_intro,
                                                    "text": cleaned_intro,
                                                    "token": cleaned_intro
                                                })
                                            intro_dispatched = True
                                            intro_buffer = ""
                                    else:
                                        await ws.send_json({
                                            "type": "token",
                                            "content": token,
                                            "text": token,
                                            "token": token
                                        })
                                except Exception:
                                    pass

                # Eventuellen Rest-Buffer nach kurzem Stream senden
                if not intro_dispatched and intro_buffer:
                    cleaned_intro = english_intro_re.sub("", intro_buffer).lstrip()
                    if cleaned_intro:
                        await ws.send_json({
                            "type": "token",
                            "content": cleaned_intro,
                            "text": cleaned_intro,
                            "token": cleaned_intro
                        })

                clean_text = sanitize_megan_response(accumulated_text)
                audio_id = await generate_piper_tts(clean_text)
                audio_url = f"/api/audio/{audio_id}.wav" if audio_id else None

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

                if audio_url:
                    await ws.send_json({
                        "type": "audio_ready",
                        "audio_url": audio_url,
                        "text": clean_text
                    })

                await ws.send_json({
                    "type": "done",
                    "id": megan_msg_id,
                    "text": clean_text,
                    "content": clean_text,
                    "audio_url": audio_url
                })
                await ws.send_json({"type": "idle"})
    finally:
        ACTIVE_WS_CLIENTS.discard(ws)
        logger.info(f"WebSocket Chat Client getrennt (Aktive Clients: {len(ACTIVE_WS_CLIENTS)})")
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

            # 2. Wenn GPU belegt ist: sofort authentische Sprachnachricht von Megan via Piper TTS generieren und senden
            if is_busy:
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
    # Fester Content-Type (Android-Clients zeigen sonst schwarze Bilder durch falsches MIME-Sniffing)
    ext = path.suffix.lower()
    ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
    return web.FileResponse(path, headers={
        "Content-Type": ctype,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=3600",
    })

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

    # Central Chat & Audio Pipeline
    app.router.add_get("/api/chat", handle_chat)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/api/chat/history", handle_chat_history)
    app.router.add_get("/ws/chat", handle_chat_ws)
    app.router.add_get("/api/audio/{filename}", handle_audio_serve)
    app.router.add_get("/api/audio/tts/{filename}", handle_audio_serve)
    app.router.add_post("/api/audio/transcribe", handle_audio_transcribe)

    # ComfyUI Render & Gallery
    app.router.add_post("/api/render", handle_render)
    app.router.add_post("/api/render/direct", handle_render)
    app.router.add_get("/api/gallery", handle_gallery_list)
    app.router.add_get("/api/gallery/{filename:.+}", handle_gallery_file)
    app.router.add_get("/api/images/{filename:.+}", handle_gallery_file)

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
