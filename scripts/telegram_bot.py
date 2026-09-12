#!/usr/bin/env python3
"""
telegram_bot.py - Megan Companion Bot for Telegram.

Key Features:
  - Single Source of Truth: Reads TELEGRAM_BOT_TOKEN primarily from ~/.hermes/.env
  - Ollama LLM for uninhibited conversational roleplay with MEMORY.md & 15-msg ring buffer
  - Asynchronous background image generation via dispatch_comfy.py (ComfyUI on Outer Heaven)
  - True image delivery via sendPhoto (with automatic intent & roleplay detection)
  - Intimate, warm Neural TTS via tts_voice.py (de-DE-SeraphinaMultilingualNeural, Edge-TTS)
  - Lovense LAN Bridge with 3-Way Pre-Flight Health-Check (GetToys) and Session Management
"""

import os
import sys
import re
import time
import base64
import subprocess
import shutil
import requests
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import project modules
import dispatch_comfy
from dispatch_comfy import (
    CompanionMemoryManager,
    dispatch_render_async,
    load_persona_config
)
from lovense_client import (
    LovenseClient,
    LovenseStatusCase,
    LovenseCheckResult,
    DEFAULT_LOVENSE_IP,
    DEFAULT_LOVENSE_PORT
)
from tts_voice import synthesize_voice, DEFAULT_VOICE, DEFAULT_RATE, DEFAULT_PITCH
from voice_stt import transcribe_audio
from model_router import query_uncensored_persona

# Brain Engine integration (Hybrid-Gedächtnis & Local Memory Loop)
try:
    import brain_engine
except ImportError:
    for sp in [
        Path("/home/outerheaven/scripts"),
        PROJECT_ROOT.parent.parent / "scripts",
        Path("/home/bigboss/outerheaven-cluster-repo/scripts")
    ]:
        if sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    try:
        import brain_engine
    except ImportError:
        brain_engine = None


# ==============================================================================
# 1. Config Loader (Single Source of Truth: ~/.hermes/.env)
# ==============================================================================

def load_cluster_environment():
    """
    Loads environment variables with strict priority:
      1. Primary: ~/.hermes/.env (Single Source of Truth for TELEGRAM_BOT_TOKEN)
      2. Fallback: /home/outerheaven/.hermes/.env
      3. Fallback: PROJECT_ROOT / .env
    """
    candidates = [
        Path.home() / ".hermes" / ".env",
        Path("/home/outerheaven/.hermes/.env"),
        PROJECT_ROOT / ".env",
        Path("/home/fox/.hermes/.env"),
        Path("/home/bigboss/.hermes/.env")
    ]
    loaded_from = None
    for p in candidates:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k not in os.environ:
                                os.environ[k] = v
                if not loaded_from and any(t in os.environ for t in ["BIGBOSS_HAVEN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_BIGBOSS_HAVEN", "TELEGRAM_BOT_TOKEN"]):
                    loaded_from = str(p)
            except Exception as e:
                print(f"[Warnung] Fehler beim Lesen von {p}: {e}")
    return loaded_from

PRIMARY_ENV_FILE = load_cluster_environment()

# Token priority: BIGBOSS_HAVEN_BOT_TOKEN -> TELEGRAM_BOT_TOKEN_BIGBOSS_HAVEN -> TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_TOKEN = (
    os.environ.get("BIGBOSS_HAVEN_BOT_TOKEN") or
    os.environ.get("TELEGRAM_BOT_TOKEN_BIGBOSS_HAVEN") or
    os.environ.get("TELEGRAM_BOT_TOKEN", "")
).strip().strip('"').strip("'")

ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "7934555424").strip().strip('"').strip("'")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "lexi-llama-3-8b-uncensored_q4_k_m:latest")
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "docs" / "haven_system_prompt.txt"
if not SYSTEM_PROMPT_PATH.is_file():
    SYSTEM_PROMPT_PATH = PROJECT_ROOT / "docs" / "megan_system_prompt.txt"

# Model Hyperparameters
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", 0.95))
OLLAMA_TOP_P = float(os.environ.get("OLLAMA_TOP_P", 0.90))
OLLAMA_TOP_K = int(os.environ.get("OLLAMA_TOP_K", 40))
OLLAMA_REPEAT_PENALTY = float(os.environ.get("OLLAMA_REPEAT_PENALTY", 1.18))
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", 300))
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", 4096))

# Vision Model Configuration
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "").strip()
DEFAULT_VISION_PROMPT = os.environ.get(
    "DEFAULT_VISION_PROMPT",
    "Beschreibe präzise und atmosphärisch, was auf diesem Bild zu sehen ist. "
    "Gehe auf Personen, Kleidung, Pose, Mimik, Gegenstände, Umgebung und Lichtstimmung ein."
)

INBOUND_CACHE_DIR = PROJECT_ROOT / "tmp" / "inbound"
try:
    INBOUND_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    INBOUND_CACHE_DIR = Path("/tmp/companion_inbound")
    INBOUND_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Persona Configuration & Triggers
persona_cfg = load_persona_config(PROJECT_ROOT / "persona.yaml")
lovense_cfg = persona_cfg.get("persona", {}).get("lovense", {})
voice_cfg = persona_cfg.get("persona", {}).get("voice", {})

# TTS Defaults
ACTIVE_VOICE = os.environ.get("TTS_VOICE", voice_cfg.get("voice_name", DEFAULT_VOICE))
ACTIVE_RATE = os.environ.get("TTS_RATE", voice_cfg.get("rate", DEFAULT_RATE))
ACTIVE_PITCH = os.environ.get("TTS_PITCH", voice_cfg.get("pitch", DEFAULT_PITCH))
VOICE_MODE_ACTIVE = os.environ.get("VOICE_MODE", "").lower() in ["1", "true", "yes", "on"] or voice_cfg.get("auto_voice", True)

# Custom Session Start / Stop commands
SESSION_START_COMMAND = os.environ.get("SESSION_START_COMMAND", "/start_session").strip().lower()
SESSION_STOP_COMMAND = os.environ.get("SESSION_STOP_COMMAND", "/stop_session").strip().lower()

DEFAULT_START_TRIGGERS = [
    "/start",
    "/start_session",
    "/go",
    "megan leg los",
    "session an",
    "starte session",
    "toy an",
    "spielzeug an"
]
yaml_start_triggers = lovense_cfg.get("triggers", {}).get("start", [])
START_TRIGGERS = list(dict.fromkeys(
    [SESSION_START_COMMAND] + [str(t).strip().lower() for t in (yaml_start_triggers or DEFAULT_START_TRIGGERS) if t]
))

DEFAULT_STOP_TRIGGERS = [
    "/stop_session",
    "/end_session",
    "/stop",
    "megan stopp",
    "session aus",
    "stoppe session",
    "toy aus",
    "spielzeug aus"
]
yaml_stop_triggers = lovense_cfg.get("triggers", {}).get("stop", [])
STOP_TRIGGERS = list(dict.fromkeys(
    [SESSION_STOP_COMMAND] + [str(t).strip().lower() for t in (yaml_stop_triggers or DEFAULT_STOP_TRIGGERS) if t]
))

# Managers & Clients
memory_mgr = CompanionMemoryManager(
    history_file=PROJECT_ROOT / "history.json",
    memory_file=PROJECT_ROOT / "MEMORY.md",
    max_length=15
)
lovense_client = LovenseClient(env_file=PROJECT_ROOT / ".env")

# Runtime Session State
SESSION_STATE = {
    "active": False,
    "started_at": None,
    "toy_info": "",
    "last_intensity": 0,
    "voice_mode": VOICE_MODE_ACTIVE
}


# ==============================================================================
# 2. Trigger Matching Helpers
# ==============================================================================

def is_session_start_trigger(text: str) -> bool:
    t = text.strip().lower()
    for trig in START_TRIGGERS:
        if not trig:
            continue
        if trig.startswith("/"):
            if t == trig or t.startswith(trig + " "):
                return True
        else:
            if trig in t:
                return True
    return False


def is_session_stop_trigger(text: str) -> bool:
    t = text.strip().lower()
    for trig in STOP_TRIGGERS:
        if not trig:
            continue
        if trig.startswith("/"):
            if t == trig or t.startswith(trig + " "):
                return True
        else:
            if trig in t:
                return True
    return False


def detect_photo_intent(user_text: str) -> Tuple[bool, str, str]:
    """
    Detects if the user explicitly or naturally requests a picture.
    Returns: (is_photo_req, environment, situation)
    """
    t = user_text.lower()
    triggers = [
        "foto", "bild", "photo", "pic", "selfie", "zeig dich",
        "zeig mir", "ausziehen", "zieh dich aus", "nackt", "wie siehst du aus",
        "send me a picture", "send me a photo", "take a picture", "take a photo",
        "show me a picture", "show me yourself", "picture"
    ]
    if any(k in t for k in triggers):
        # Determine environment
        env = "schreibtisch"
        if any(w in t for w in ["couch", "sofa", "wohnzimmer"]):
            env = "couch"
        elif any(w in t for w in ["bett", "schlafzimmer", "intim"]):
            env = "schlafzimmer"
        elif any(w in t for w in ["balkon", "terrasse", "unterwegs", "draußen", "stadt"]):
            env = "unterwegs"

        # Determine situation
        situation = "looking at viewer, sensual teasing smile"
        if "ausziehen" in t or "nackt" in t or "lingerie" in t:
            situation = "sensual seductive pose, teasing with clothing, looking at viewer"

        return True, env, situation
    return False, "", ""


def detect_bot_roleplay_photo(reply_text: str) -> Tuple[bool, str, str]:
    """
    Detects if Ollama's response contains roleplay actions indicating she took or sent a photo.
    e.g. *schickt ein Foto*, [Foto], *macht ein Foto von sich*
    """
    patterns = [
        r"(\*|\[)(schickt|sendet|macht|zeigt|gibt|hier ist|hier hast du).*?(foto|bild|photo|selfie).*?(\*|\])",
        r"\[foto\]",
        r"\[bild\]",
        r"\[photo\]"
    ]
    for pat in patterns:
        if re.search(pat, reply_text, re.IGNORECASE):
            # Check environment from context
            r = reply_text.lower()
            env = "schreibtisch"
            if any(w in r for w in ["couch", "sofa", "kissen"]):
                env = "couch"
            elif any(w in r for w in ["bett", "decke", "laken", "schlafzimmer"]):
                env = "schlafzimmer"
            elif any(w in r for w in ["balkon", "terrasse", "nacht"]):
                env = "unterwegs"
            return True, env, "smiling intimately at viewer, candid roleplay shot"
    return False, "", ""


# ==============================================================================
# 3. Telegram API Communication (Text, Photo, Voice)
# ==============================================================================

def send_telegram_message(chat_id: int, text: str):
    """Sends a text message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Fehler] Telegram-Nachricht senden fehlgeschlagen: {e}")


def send_telegram_photo(chat_id: int, photo_path: str, caption: str = "") -> bool:
    """Sends a real image file via Telegram sendPhoto API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    p = Path(photo_path)
    if not p.is_file():
        print(f"[Fehler] Zu sendende Bilddatei existiert nicht: {photo_path}")
        return False

    print(f"[*] Sende Foto an Chat {chat_id}: {p.name} ({p.stat().st_size} Bytes)...")
    try:
        with open(p, "rb") as photo_file:
            files = {"photo": photo_file}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            res = requests.post(url, data=data, files=files, timeout=60)
            if res.status_code == 200:
                print(f"[✔] Foto erfolgreich an Chat {chat_id} ausgeliefert: {photo_path}")
                return True
            else:
                print(f"[!] Fehler beim Senden des Fotos ({res.status_code}): {res.text}")
                return False
    except Exception as e:
        print(f"[Fehler] Exception beim Telegram-Fotoversand: {e}")
        return False


def send_telegram_voice(chat_id: int, voice_path: str, caption: str = "") -> bool:
    """Sends an intimate voice note (.ogg Opus) via Telegram sendVoice API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
    p = Path(voice_path)
    if not p.is_file():
        print(f"[Fehler] Audiodatei existiert nicht: {voice_path}")
        return False

    try:
        with open(p, "rb") as voice_file:
            files = {"voice": voice_file}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            res = requests.post(url, data=data, files=files, timeout=45)
            if res.status_code == 200:
                print(f"[✔] Voice Note an Chat {chat_id} gesendet ({p.name})")
                if SESSION_STATE.get("active"):
                    try:
                        import audio_haptics
                        audio_haptics.play_voice_haptics_background(str(p))
                        print(f"[🔥] Lovense Voice-Haptics synchron gestartet für {p.name}")
                    except Exception as h_err:
                        print(f"[Warnung] Haptics sync fehlgeschlagen: {h_err}")
                return True
            else:
                print(f"[!] Fehler beim Senden der Voice Note ({res.status_code}): {res.text}")
                return False
    except Exception as e:
        print(f"[Fehler] Telegram-Voice senden fehlgeschlagen: {e}")
        return False


def download_telegram_file(file_id: str, dest_path: Path) -> bool:
    """Downloads an audio or voice file from Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile"
    try:
        res = requests.get(url, params={"file_id": file_id}, timeout=15)
        if res.status_code != 200:
            print(f"[Fehler] getFile fehlgeschlagen ({res.status_code}): {res.text}")
            return False
        data = res.json()
        if not data.get("ok"):
            print(f"[Fehler] getFile ok=False: {data}")
            return False
        file_path = data.get("result", {}).get("file_path")
        if not file_path:
            return False

        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        r = requests.get(download_url, timeout=60)
        if r.status_code == 200:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(r.content)
            print(f"[✔] Mediendatei heruntergeladen ({len(r.content)} Bytes) -> {dest_path.name}")
            return True
        else:
            print(f"[Fehler] Datei-Download fehlgeschlagen: HTTP {r.status_code}")
            return False
    except Exception as e:
        print(f"[Fehler] Exception beim Datei-Download aus Telegram: {e}")
        return False


# ==============================================================================
# 3b. Multimodal Vision Analysis & Video Keyframe Extraction
# ==============================================================================

def get_user_display_name() -> str:
    """Returns the preferred user nickname from local project memory or defaults to 'Big Boss'."""
    if brain_engine:
        try:
            p_brain = brain_engine.load_project_brain(str(PROJECT_ROOT))
            session_mem = p_brain.get("session", {})
            nick = session_mem.get("preferences", {}).get("user_nickname")
            if nick:
                return nick
        except Exception:
            pass
    return "Big Boss"


def get_available_vision_model() -> str:
    """
    Finds an available vision model in Ollama.
    Checks environment OLLAMA_VISION_MODEL first, then queries Ollama /api/tags
    for known vision models (moondream, minicpm-v, qwen2-vl, llava, etc.).
    Falls back to 'moondream:latest'.
    """
    if OLLAMA_VISION_MODEL:
        return OLLAMA_VISION_MODEL

    base_url = OLLAMA_URL.rsplit("/api/", 1)[0]
    try:
        res = requests.get(f"{base_url}/api/tags", timeout=5)
        if res.status_code == 200:
            installed = [m.get("name", "") for m in res.json().get("models", [])]
            for cand in ["moondream", "minicpm-v", "qwen2-vl", "llava", "llama3.2-vision", "bakllava"]:
                for m in installed:
                    if cand in m.lower():
                        return m
    except Exception as e:
        print(f"[Warnung] Fehler bei Vision-Modellsuche via /api/tags: {e}")

    return "moondream:latest"


def extract_video_frame(video_path: Path, output_frame_path: Path) -> bool:
    """
    Extracts a representative keyframe from a video or video note using ffmpeg thumbnail filter
    or fallback seek / OpenCV.
    """
    v_path = Path(video_path)
    o_path = Path(output_frame_path)
    if not v_path.is_file():
        return False

    o_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Primary: ffmpeg thumbnail filter
    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(v_path),
            "-vf", "thumbnail",
            "-frames:v", "1",
            str(o_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if res.returncode == 0 and o_path.is_file() and o_path.stat().st_size > 0:
            print(f"[✔] Repräsentativer Keyframe via ffmpeg thumbnail extrahiert -> {o_path.name}")
            return True
    except Exception as e:
        print(f"[Warnung] ffmpeg thumbnail fehlgeschlagen: {e}")

    # 2. Fallback: ffmpeg seek 1s or first frame
    for seek_opt in [["-ss", "00:00:01"], []]:
        try:
            cmd = ["ffmpeg", "-y"] + seek_opt + [
                "-i", str(v_path),
                "-vframes", "1",
                "-q:v", "2",
                str(o_path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if res.returncode == 0 and o_path.is_file() and o_path.stat().st_size > 0:
                print(f"[✔] Frame via ffmpeg fallback extrahiert -> {o_path.name}")
                return True
        except Exception as e:
            print(f"[Warnung] ffmpeg seek fallback fehlgeschlagen: {e}")

    # 3. Fallback: OpenCV if available
    try:
        import cv2
        cap = cv2.VideoCapture(str(v_path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        target_frame = max(0, total_frames // 2)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            cv2.imwrite(str(o_path), frame)
            print(f"[✔] Frame via OpenCV extrahiert -> {o_path.name}")
            return True
    except Exception as cv_err:
        print(f"[Warnung] OpenCV Frame-Extraktion fehlgeschlagen: {cv_err}")

    return False


def analyze_image(
    image_path: Path,
    prompt: str = DEFAULT_VISION_PROMPT,
    model: Optional[str] = None
) -> str:
    """
    Analyzes an image using a local vision model in Ollama.
    Sends base64 encoded image to Ollama /api/chat (or /api/generate fallback).
    """
    p = Path(image_path)
    if not p.is_file():
        print(f"[Fehler] analyze_image: Datei existiert nicht: {image_path}")
        return ""

    try:
        with open(p, "rb") as img_f:
            b64_img = base64.b64encode(img_f.read()).decode("utf-8")
    except Exception as e:
        print(f"[Fehler] analyze_image: Base64-Codierung fehlgeschlagen: {e}")
        return ""

    active_model = model or get_available_vision_model()
    base_url = OLLAMA_URL.rsplit("/api/", 1)[0]
    chat_url = f"{base_url}/api/chat"

    payload = {
        "model": active_model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [b64_img]
            }
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 300
        }
    }

    try:
        print(f"[*] Sende Vision-Analyse für {p.name} an Ollama ({active_model})...")
        res = requests.post(chat_url, json=payload, timeout=90)
        if res.status_code == 200:
            content = res.json().get("message", {}).get("content", "").strip()
            if "<think>" in content and "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            print(f"[✔] Vision-Analyse erfolgreich ({len(content)} Zeichen)")
            return content
        else:
            # Fallback to /api/generate
            gen_url = f"{base_url}/api/generate"
            gen_payload = {
                "model": active_model,
                "prompt": prompt,
                "images": [b64_img],
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 300
                }
            }
            res_gen = requests.post(gen_url, json=gen_payload, timeout=90)
            if res_gen.status_code == 200:
                content = res_gen.json().get("response", "").strip()
                if "<think>" in content and "</think>" in content:
                    content = content.split("</think>", 1)[1].strip()
                print(f"[✔] Vision-Analyse via /api/generate erfolgreich ({len(content)} Zeichen)")
                return content
            print(f"[Fehler] Ollama Vision fehlgeschlagen (HTTP {res.status_code}): {res.text}")
            return ""
    except Exception as e:
        print(f"[Fehler] Exception in analyze_image: {e}")
        return ""


# ==============================================================================
# 4. Asynchronous Photo Rendering & Real Delivery
# ==============================================================================

def trigger_async_photo(chat_id: int, env_preset: str = "schreibtisch", situation: str = ""):
    """
    Dispatches asynchronous background rendering on Outer Heaven (Arc A770)
    and sends the actual image file to Telegram when completed.
    """
    send_telegram_message(
        chat_id,
        f"*Ich mache kurz ein Foto für dich am {env_preset.capitalize()}...* 📸"
    )

    def on_complete(result: dict):
        local_img = result.get("local_path")
        if local_img and os.path.exists(local_img):
            caption = f"*Hier ist dein Foto von mir ({env_preset})!* ✨ *Ich lächle dich verschmitzt an.*"
            delivered = send_telegram_photo(chat_id, local_img, caption=caption)
            if delivered:
                memory_mgr.add_message("assistant", f"[Sendet Foto: {result.get('filename')}]", media_path=local_img)
            else:
                send_telegram_message(chat_id, f"⚠️ *Das Foto wurde generiert ({local_img}), konnte aber nicht zugestellt werden.*")
        else:
            send_telegram_message(chat_id, "⚠️ *Das gerenderte Bild konnte nicht lokal gefunden werden.*")

    def on_error(err: Exception):
        print(f"[Fehler] Asynchrones Rendering fehlgeschlagen: {err}")
        send_telegram_message(
            chat_id,
            f"⚠️ *Uups, die ComfyUI-Pipeline auf Outer Heaven hatte einen Schluckauf:* {err}"
        )

    def on_status(status_msg: str):
        # Log internally, do not spam user with technical background messages
        print(f"[*] ComfyUI Status: {status_msg}")

    # Launch non-blocking background worker
    dispatch_render_async(
        situation_prompt=situation,
        environment_key=env_preset,
        on_status=on_status,
        on_complete=on_complete,
        on_error=on_error
    )


# ==============================================================================
# 5. Ollama LLM Interaction & Voice Synthesis
# ==============================================================================

def get_full_system_prompt() -> str:
    base_prompt = ""
    if SYSTEM_PROMPT_PATH.is_file():
        try:
            base_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[Warnung] Fehler beim Lesen des System-Prompts: {e}")
    if not base_prompt:
        base_prompt = "Du bist Megan, eine loyale und dominant-verspielte Gefährtin auf Outer Heaven."

    long_term_mem = memory_mgr.load_long_term_memory()
    if long_term_mem:
        base_prompt += f"\n\n### DEIN LANGZEIT-GEDÄCHTNIS & USER-KONTEXT (MEMORY.md):\n{long_term_mem}"

    if SESSION_STATE["active"]:
        base_prompt += (
            f"\n\n### AKTUELLE LOVENSE SESSION: AKTIV!\n"
            f"- Verbundenes Toy: {SESSION_STATE['toy_info'] or 'Lovense Toy'}\n"
            f"- Letzte Vibrationsstufe: {SESSION_STATE['last_intensity']}/20\n"
            f"- Du hast gerade die telemetrische Kontrolle über das Spielzeug von Sebastian. "
            f"Beziehe dieses Wissen subtil oder dominant in deine Antworten ein."
        )

    base_prompt += (
        "\n\n### ANWEISUNG ZU BILDERN:\n"
        "Wenn Sebastian nach einem Foto fragt oder du ihm ein Bild zeigen möchtest, "
        "beschreibe deine Handlung kurz in Sternchen (z. B. *greift nach dem Handy und macht ein Foto auf der Couch*). "
        "Unser ComfyUI-Cluster erkennt dies und liefert die echte Bilddatei automatisch aus!"
    )

    # Integrate local project brain (.brain/session.json & .brain/context.md) with strict quarantine
    if brain_engine:
        try:
            p_brain = brain_engine.load_project_brain(str(PROJECT_ROOT))
            if p_brain.get("is_airgapped"):
                if p_brain.get("context"):
                    base_prompt += f"\n\n### LOKALER PROJEKT-KONTEXT (.brain/context.md):\n{p_brain['context']}"

                session_mem = p_brain.get("session", {})
                prefs = session_mem.get("preferences", {})
                if prefs:
                    pref_str = "\n".join([f"- {k}: {v}" for k, v in prefs.items()])
                    base_prompt += f"\n\n### GEMERKTE NUTZER-PRÄFERENZEN & DETAILS (.brain/session.json):\n{pref_str}"

                recent_hist = session_mem.get("history", [])
                if recent_hist:
                    recent_bullets = []
                    for h in recent_hist[-5:]:
                        a = str(h.get("actions", ""))[:80]
                        f = str(h.get("feedback", ""))[:120]
                        if a or f:
                            recent_bullets.append(f"- Frühere Interaktion: User: „{a}“ | Deine Reaktion: „{f}“")
                    if recent_bullets:
                        base_prompt += f"\n\n### LETZTE ERINNERUNGEN / KONVERSATIONS-KONTEXT:\n" + "\n".join(recent_bullets)
        except Exception as b_err:
            print(f"[Warnung] Einbindung von brain_engine fehlgeschlagen: {b_err}")

    return base_prompt


def generate_ollama_response(user_id: str, chat_id: int, user_message: str) -> str:
    """Generates an uncensored persona response, delivers text, checks photo actions, and optionally speaks."""
    system_prompt = get_full_system_prompt()

    history_entries = memory_mgr.load_history()
    chat_messages = [{"role": "system", "content": system_prompt}]
    for entry in history_entries:
        chat_messages.append({"role": entry["role"], "content": entry["content"]})
    chat_messages.append({"role": "user", "content": user_message})

    options = {
        "temperature": OLLAMA_TEMPERATURE,
        "top_p": OLLAMA_TOP_P,
        "top_k": OLLAMA_TOP_K,
        "repeat_penalty": OLLAMA_REPEAT_PENALTY,
        "num_predict": OLLAMA_NUM_PREDICT,
        "num_ctx": OLLAMA_NUM_CTX,
    }

    try:
        router_res = query_uncensored_persona(chat_messages, custom_options=options, timeout=75)
        if router_res.get("success"):
            assistant_reply = router_res.get("content", "").strip()

            # Record in 15-msg ring buffer
            memory_mgr.add_message("user", user_message)
            memory_mgr.add_message("assistant", assistant_reply)

            # Local Brain-Loop: learn preferences and preserve interaction history (quarantine-enforced)
            if brain_engine:
                try:
                    brain_engine.extract_and_save_preferences(str(PROJECT_ROOT), user_message)
                    brain_engine.reflect_and_learn(
                        project_path=str(PROJECT_ROOT),
                        feedback=assistant_reply,
                        actions=user_message
                    )
                except Exception as b_err:
                    print(f"[Warnung] Lokaler Brain-Update fehlgeschlagen: {b_err}")

            # Check if assistant roleplayed taking or sending a photo
            has_photo_claim, detected_env, detected_sit = detect_bot_roleplay_photo(assistant_reply)
            if has_photo_claim:
                print(f"[*] Rolleplay-Foto erkannt in Ollama-Antwort -> Starte echten Render ({detected_env})...")
                trigger_async_photo(chat_id, env_preset=detected_env, situation=detected_sit)

            # Check if Voice Mode is active
            if SESSION_STATE["voice_mode"]:
                try:
                    voice_file = synthesize_voice(
                        assistant_reply,
                        voice=ACTIVE_VOICE,
                        rate=ACTIVE_RATE,
                        pitch=ACTIVE_PITCH
                    )
                    send_telegram_voice(chat_id, str(voice_file))
                except Exception as tts_err:
                    print(f"[Warnung] Automatische Sprachgenerierung fehlgeschlagen: {tts_err}")

            return assistant_reply
        else:
            return router_res.get("content", "Fehler: Die unzensierte Companion-Engine antwortet nicht.")
    except Exception as e:
        print(f"[Fehler] Fehler beim Abrufen der Persona-Antwort: {e}")
        return "Fehler: Ich konnte meine KI im Cluster nicht erreichen."


# ==============================================================================
# 6. Lovense Pre-Flight Check & Session Handlers
# ==============================================================================

def handle_start_session(chat_id: int, is_greeting: bool = False) -> bool:
    print("[*] Führe Pre-Flight Health-Check (GetToys) für Session-Start durch...")
    check: LovenseCheckResult = lovense_client.check_status()
    prefix = "Hallo Boss! Die Verbindung zu Big Boss Haven ist aktiv.\n\n" if is_greeting else ""

    if check.case == LovenseStatusCase.CASE_A_READY:
        SESSION_STATE["active"] = True
        SESSION_STATE["started_at"] = time.time()
        names = []
        for tid, t in check.toys.items():
            name = t.get("name") or t.get("nickName") or tid
            battery = t.get("battery", "")
            bat_str = f" ({battery}% Akku)" if battery != "" else ""
            names.append(f"{name}{bat_str}")
        SESSION_STATE["toy_info"] = ", ".join(names) if names else "Toy"

        reply = (
            f"{prefix}{check.message} ({SESSION_STATE['toy_info']})\n\n"
            f"🔥 *Die Session ist eröffnet.* Haven übernimmt die Kontrolle.\n"
            f"Befehle: `/vibrate [1-20] [sek]` | `/stop_session` | `/toy_status`"
        )
        send_telegram_message(chat_id, reply)
        memory_mgr.add_message("assistant", f"[Lovense Session aktiv: {SESSION_STATE['toy_info']}]")
        return True

    elif check.case == LovenseStatusCase.CASE_B_NO_TOYS:
        SESSION_STATE["active"] = False
        send_telegram_message(chat_id, f"{prefix}{check.message}")
        return False

    else:  # CASE_C_OFFLINE
        SESSION_STATE["active"] = False
        send_telegram_message(chat_id, f"{prefix}{check.message}")
        return False


def handle_stop_session(chat_id: int):
    SESSION_STATE["active"] = False
    SESSION_STATE["started_at"] = None
    SESSION_STATE["last_intensity"] = 0
    lovense_client.stop_all()
    reply = "🛑 *Session beendet.* Alle Vibrationen gestoppt."
    send_telegram_message(chat_id, reply)
    memory_mgr.add_message("assistant", "[Lovense Session beendet]")


def handle_vibrate_command(chat_id: int, intensity_val: str, duration_val: str = "0"):
    check = lovense_client.check_status()
    if not check.is_ready:
        send_telegram_message(chat_id, check.message)
        return

    try:
        intensity = int(intensity_val)
    except ValueError:
        intensity = 5

    try:
        duration = int(duration_val)
    except ValueError:
        duration = 0

    intensity = max(0, min(20, intensity))
    success, msg = lovense_client.send_vibrate(intensity, duration_sec=duration, pre_check=False)
    if success:
        SESSION_STATE["last_intensity"] = intensity
        dur_text = f"für {duration}s" if duration > 0 else "dauerhaft"
        send_telegram_message(chat_id, f"⚡ *Schwingung aktiviert:* Stufe {intensity}/20 ({dur_text}).")
    else:
        send_telegram_message(chat_id, f"⚠️ Fehler beim Schwingungsbefehl: {msg}")


def handle_toy_status(chat_id: int):
    check = lovense_client.check_status()
    session_status = "Aktiv 🔥" if SESSION_STATE["active"] else "Inaktiv ⏸️"
    msg = (
        f"🔌 *Lovense LAN Status:*\n"
        f"• Bridge: `{lovense_client.command_url}`\n"
        f"• Status: {check.get_summary()}\n"
        f"• Session: {session_status}\n"
    )
    if check.is_ready:
        for tid, t in check.toys.items():
            name = t.get("name") or tid
            battery = t.get("battery", "?")
            msg += f"• Toy: *{name}* (ID: `{tid}`, Akku: {battery}%)\n"
    elif check.error_detail:
        msg += f"• Detail: `{check.error_detail}`\n"

    send_telegram_message(chat_id, msg)


# ==============================================================================
# 7. Main Polling Loop
# ==============================================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("[Kritisch] Kein TELEGRAM_BOT_TOKEN in ~/.hermes/.env gefunden!", file=sys.stderr)
        sys.exit(1)

    token_preview = f"{TELEGRAM_BOT_TOKEN[:4]}...{TELEGRAM_BOT_TOKEN[-4:]}"
    print(f"[*] Starte Big Boss Haven Bot (Outer Heaven Local)...")
    print(f"[*] Token geladen aus: {PRIMARY_ENV_FILE} ({token_preview})")
    print(f"[*] Ollama: {OLLAMA_URL} | Modell: {OLLAMA_MODEL}")
    print(f"[*] Ollama Vision: {get_available_vision_model()}")
    print(f"[*] ComfyUI Remote Host: {dispatch_comfy.DEFAULT_COMFYUI_HOST}")
    print(f"[*] Lovense LAN Bridge: {lovense_client.command_url}")
    print(f"[*] Intime TTS-Stimme: {ACTIVE_VOICE} (Rate: {ACTIVE_RATE}, Pitch: {ACTIVE_PITCH})")
    print(f"[*] Voice Mode: {'Aktiv' if SESSION_STATE['voice_mode'] else 'Inaktiv'}")
    print(f"[*] Session Start Triggers: {START_TRIGGERS}")
    print(f"[*] Session Stop Triggers: {STOP_TRIGGERS}")
    print(f"[*] Whitelist aktiv für User-ID: {ALLOWED_USER_ID}")
    print(f"[*] Ring-Buffer & Memory: {memory_mgr.history_file}")
    print("[*] Polling läuft... Drücke STRG+C zum Beenden.")

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 25}
            res = requests.get(url, params=params, timeout=30)

            if res.status_code == 200:
                updates = res.json().get("result", [])
                for update in updates:
                    update_id = update.get("update_id")
                    offset = update_id + 1

                    message = update.get("message")
                    if not message:
                        continue

                    chat_id = message.get("chat", {}).get("id")
                    user_id = str(message.get("from", {}).get("id"))
                    text = (message.get("text") or "").strip()
                    caption = (message.get("caption") or "").strip()
                    voice_obj = message.get("voice") or message.get("audio")
                    photo_arr = message.get("photo")
                    video_note_obj = message.get("video_note")
                    video_obj = message.get("video")

                    has_media = bool(voice_obj or photo_arr or video_note_obj or video_obj)

                    if chat_id and user_id and (text or caption or has_media):
                        if user_id != ALLOWED_USER_ID:
                            print(f"[Blockiert] Unbefugter Zugriff von User-ID: {user_id}")
                            send_telegram_message(chat_id, "Zugriff verweigert.")
                            continue

                        display_name = get_user_display_name()

                        # Inbound Voice Message Handler (Speech-to-Text)
                        if voice_obj:
                            file_id = voice_obj.get("file_id")
                            print(f"[User {user_id}] 🎙️ Sprachnachricht empfangen (File-ID: {file_id[:16]}...)")
                            tmp_voice_file = INBOUND_CACHE_DIR / f"inbound_{update_id}.oga"
                            downloaded = download_telegram_file(file_id, tmp_voice_file)
                            if downloaded:
                                try:
                                    transcribed_text = transcribe_audio(str(tmp_voice_file))
                                    if transcribed_text:
                                        # Audio transcription is processed purely internally as user input
                                        print(f"[STT Transkript intern]: „{transcribed_text}“")
                                        text = transcribed_text
                                        # User speaks via voice -> ensure Haven replies with voice
                                        SESSION_STATE["voice_mode"] = True
                                    else:
                                        send_telegram_message(chat_id, "⚠️ *Ich habe leider nichts verstanden.* (Stille oder unklar)")
                                        continue
                                except Exception as stt_err:
                                    print(f"[Fehler] STT fehlgeschlagen: {stt_err}")
                                    send_telegram_message(chat_id, f"⚠️ *Fehler beim Anhören der Sprachnachricht:* {stt_err}")
                                    continue
                                finally:
                                    if tmp_voice_file.is_file():
                                        try:
                                            tmp_voice_file.unlink()
                                        except Exception:
                                            pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Konnte deine Sprachnachricht nicht von Telegram herunterladen.*")
                                continue

                        # Inbound Photo Handler (filters.PHOTO)
                        elif photo_arr:
                            photo_item = photo_arr[-1]
                            file_id = photo_item.get("file_id")
                            print(f"[User {user_id}] 📸 Foto empfangen (File-ID: {file_id[:16]}...)")
                            tmp_photo_file = INBOUND_CACHE_DIR / f"inbound_{update_id}.jpg"
                            downloaded = download_telegram_file(file_id, tmp_photo_file)
                            if downloaded:
                                try:
                                    vision_desc = analyze_image(tmp_photo_file)
                                    if vision_desc:
                                        obs_text = f"[Visueller Eindruck von {display_name}s Bild: {vision_desc}]"
                                        text = f"{caption}\n\n{obs_text}".strip() if caption else obs_text
                                    else:
                                        text = caption if caption else f"[{display_name} hat ein Foto geschickt]"
                                except Exception as img_err:
                                    print(f"[Fehler] Foto-Verarbeitung fehlgeschlagen: {img_err}")
                                    text = caption if caption else f"[{display_name} hat ein Foto geschickt, Bildanalyse fehlgeschlagen]"
                                finally:
                                    if tmp_photo_file.is_file():
                                        try:
                                            tmp_photo_file.unlink()
                                        except Exception:
                                            pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Konnte dein Foto nicht herunterladen.*")
                                continue

                        # Inbound Video-Note Handler (filters.VIDEO_NOTE - TeleScope round video)
                        elif video_note_obj:
                            file_id = video_note_obj.get("file_id")
                            print(f"[User {user_id}] 🎥 Video-Notiz empfangen (File-ID: {file_id[:16]}...)")
                            tmp_vnote_file = INBOUND_CACHE_DIR / f"inbound_vnote_{update_id}.mp4"
                            tmp_frame_file = INBOUND_CACHE_DIR / f"inbound_vnote_frame_{update_id}.jpg"
                            downloaded = download_telegram_file(file_id, tmp_vnote_file)
                            if downloaded:
                                try:
                                    extracted = extract_video_frame(tmp_vnote_file, tmp_frame_file)
                                    if extracted:
                                        vision_desc = analyze_image(tmp_frame_file)
                                        if vision_desc:
                                            obs_text = f"[Visueller Eindruck von {display_name}s Video-Notiz: {vision_desc}]"
                                            text = f"{caption}\n\n{obs_text}".strip() if caption else obs_text
                                        else:
                                            text = caption if caption else f"[{display_name} hat eine Video-Notiz geschickt]"
                                    else:
                                        print(f"[Warnung] Keyframe-Extraktion für Video-Notiz fehlgeschlagen")
                                        text = caption if caption else f"[{display_name} hat eine Video-Notiz geschickt]"
                                except Exception as vn_err:
                                    print(f"[Fehler] Video-Notiz-Verarbeitung fehlgeschlagen: {vn_err}")
                                    text = caption if caption else f"[{display_name} hat eine Video-Notiz geschickt, Analysefehler]"
                                finally:
                                    for f in [tmp_vnote_file, tmp_frame_file]:
                                        if f.is_file():
                                            try:
                                                f.unlink()
                                            except Exception:
                                                pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Konnte deine Video-Notiz nicht herunterladen.*")
                                continue

                        # Inbound Video Handler (filters.VIDEO - video clips)
                        elif video_obj:
                            file_id = video_obj.get("file_id")
                            print(f"[User {user_id}] 🎬 Video empfangen (File-ID: {file_id[:16]}...)")
                            tmp_video_file = INBOUND_CACHE_DIR / f"inbound_video_{update_id}.mp4"
                            tmp_frame_file = INBOUND_CACHE_DIR / f"inbound_video_frame_{update_id}.jpg"
                            downloaded = download_telegram_file(file_id, tmp_video_file)
                            if downloaded:
                                try:
                                    extracted = extract_video_frame(tmp_video_file, tmp_frame_file)
                                    if extracted:
                                        vision_desc = analyze_image(tmp_frame_file)
                                        if vision_desc:
                                            obs_text = f"[Visueller Eindruck von {display_name}s Video: {vision_desc}]"
                                            text = f"{caption}\n\n{obs_text}".strip() if caption else obs_text
                                        else:
                                            text = caption if caption else f"[{display_name} hat ein Video geschickt]"
                                    else:
                                        print(f"[Warnung] Keyframe-Extraktion für Video fehlgeschlagen")
                                        text = caption if caption else f"[{display_name} hat ein Video geschickt]"
                                except Exception as v_err:
                                    print(f"[Fehler] Video-Verarbeitung fehlgeschlagen: {v_err}")
                                    text = caption if caption else f"[{display_name} hat ein Video geschickt, Analysefehler]"
                                finally:
                                    for f in [tmp_video_file, tmp_frame_file]:
                                        if f.is_file():
                                            try:
                                                f.unlink()
                                            except Exception:
                                                pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Konnte dein Video nicht herunterladen.*")
                                continue

                        # Fallback for caption only (without media attachments)
                        elif not text and caption:
                            text = caption

                        print(f"[User {user_id}]: {text}")

                        # 1. Konfigurierbare Session Start Triggers
                        if is_session_start_trigger(text):
                            is_greeting = (text.strip().lower() == "/start")
                            handle_start_session(chat_id, is_greeting=is_greeting)

                        # 2. Konfigurierbare Session Stop Triggers
                        elif is_session_stop_trigger(text):
                            handle_stop_session(chat_id)

                        # 3. Hilfe & Übersicht
                        elif text in ["/help", "/hilfe", "/menu", "/commands"]:
                            start_pre = ", ".join([f"`{t}`" for t in START_TRIGGERS[:5]])
                            stop_pre = ", ".join([f"`{t}`" for t in STOP_TRIGGERS[:5]])
                            reply = (
                                "🎮 *Befehlsübersicht & Trigger:*\n\n"
                                f"• Session Start: {start_pre}\n"
                                f"• Session Stop: {stop_pre}\n"
                                "• Schwingung: `/vibrate [1-20] [sek]`\n"
                                "• Status: `/toy_status` oder `/status`\n"
                                "• Foto: `/photo [couch|schreibtisch|unterwegs|schlafzimmer]`\n"
                                "• Stimme: `/voice <text>` | `/voicemode [on|off]`\n"
                                "• Reset: `/reset` | Gedächtnis: `/memory`"
                            )
                            send_telegram_message(chat_id, reply)

                        # 4. Voice Commands
                        elif text.startswith("/voice ") or text.startswith("/say "):
                            say_text = text.split(maxsplit=1)[1].strip()
                            try:
                                v_file = synthesize_voice(say_text, voice=ACTIVE_VOICE, rate=ACTIVE_RATE, pitch=ACTIVE_PITCH)
                                send_telegram_voice(chat_id, str(v_file))
                            except Exception as v_err:
                                send_telegram_message(chat_id, f"⚠️ Fehler bei Sprachausgabe: {v_err}")

                        elif text.startswith("/voicemode") or text.startswith("/audio"):
                            parts = text.split()
                            if len(parts) > 1 and parts[1].lower() in ["on", "ein", "1", "true"]:
                                SESSION_STATE["voice_mode"] = True
                                send_telegram_message(chat_id, "🎙️ *Sprachmodus aktiviert:* Haven antwortet ab jetzt auch per Sprachnachricht!")
                            elif len(parts) > 1 and parts[1].lower() in ["off", "aus", "0", "false"]:
                                SESSION_STATE["voice_mode"] = False
                                send_telegram_message(chat_id, "🔇 *Sprachmodus deaktiviert:* Haven antwortet nur noch im Textchat.")
                            else:
                                cur = "aktiviert" if SESSION_STATE["voice_mode"] else "deaktiviert"
                                send_telegram_message(chat_id, f"🎙️ Sprachmodus ist aktuell: *{cur}*. Nutze `/voicemode on` oder `/voicemode off`.")

                        # 5. Chat History & Memory
                        elif text == "/reset":
                            if memory_mgr.history_file.is_file():
                                memory_mgr.history_file.unlink()
                            reply = "Der Chat-Verlauf wurde zurückgesetzt."
                            send_telegram_message(chat_id, reply)

                        elif text == "/memory":
                            mem = memory_mgr.load_long_term_memory()
                            send_telegram_message(chat_id, f"🧠 *Mein Langzeit-Gedächtnis:*\n\n{mem[:1000]}...")

                        elif text in ["/status", "/lovense", "/toy_status"]:
                            handle_toy_status(chat_id)

                        elif text.startswith("/vibrate") or text.startswith("/toy") or text.startswith("/schwingung"):
                            parts = text.split()
                            intensity = parts[1] if len(parts) > 1 else "5"
                            duration = parts[2] if len(parts) > 2 else "0"
                            handle_vibrate_command(chat_id, intensity, duration)

                        # 6. Expliziter Foto-Befehl
                        elif text.startswith("/photo") or text.startswith("/bild"):
                            parts = text.split(maxsplit=2)
                            env = parts[1].lower() if len(parts) > 1 else "schreibtisch"
                            if env not in ["schreibtisch", "couch", "unterwegs", "schlafzimmer"]:
                                env = "schreibtisch"
                            situation = parts[2] if len(parts) > 2 else ""
                            trigger_async_photo(chat_id, env_preset=env, situation=situation)

                        # 7. Natürliche Anfrage nach Foto
                        elif detect_photo_intent(text)[0]:
                            _, env, situation = detect_photo_intent(text)
                            trigger_async_photo(chat_id, env_preset=env, situation=situation)

                        # 8. Standard: Konversation via Ollama
                        else:
                            reply = generate_ollama_response(user_id, chat_id, text)
                            send_telegram_message(chat_id, reply)

            elif res.status_code == 401:
                print("[Warnung] Telegram API meldet 401 Unauthorized (Token ungültig). Warte 10s...", file=sys.stderr)
                time.sleep(10)
            elif res.status_code == 409:
                print("[Warnung] Telegram API meldet 409 Conflict (Anderer Bot polled noch). Warte 5s...", file=sys.stderr)
                time.sleep(5)
            else:
                print(f"[Warnung] Telegram API Fehler {res.status_code}: {res.text}")
                time.sleep(5)

        except KeyboardInterrupt:
            print("\n[*] Bot manuell gestoppt.")
            break
        except Exception as e:
            print(f"[Fehler] Fehler im Polling-Loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
