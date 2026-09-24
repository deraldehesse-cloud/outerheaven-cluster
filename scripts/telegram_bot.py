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
import json
import datetime
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
try:
    from sanitize_history import is_corrupted_text, sanitize_history_data
except ImportError:
    for sp in [Path(__file__).resolve().parent, Path("/mnt/storage-beta/projekte/projekt-sexchat/assets")]:
        if sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    try:
        from sanitize_history import is_corrupted_text, sanitize_history_data
    except ImportError:
        def is_corrupted_text(text: str) -> Tuple[bool, str]:
            return False, ""
        def sanitize_history_data(entries: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
            return entries, 0

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
CHARACTER_JSON_PATH = Path("/mnt/storage-beta/projekte/projekt-sexchat/character.json")
if not CHARACTER_JSON_PATH.is_file() and (PROJECT_ROOT / "character.json").is_file():
    CHARACTER_JSON_PATH = PROJECT_ROOT / "character.json"
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
    "Describe precisely and atmospherically what is visible in this image. "
    "Focus on people, clothing, body, pose, facial expression, objects, setting, and lighting."
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
    "haven let's go",
    "haven start",
    "start session",
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
    "haven stop",
    "stop session",
    "end session",
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

# Runtime Session State & Rating/Flaw Feedback Cache
SESSION_STATE = {
    "active": False,
    "started_at": None,
    "toy_info": "",
    "last_intensity": 0,
    "voice_mode": VOICE_MODE_ACTIVE
}
AWAITING_FLAW_FEEDBACK: Dict[int, int] = {}


def append_to_long_term_memory(note: str):
    """Appends a user note, preference or feedback to MEMORY.md."""
    try:
        mem_path = PROJECT_ROOT / "MEMORY.md"
        if not mem_path.is_file():
            mem_path = Path("/mnt/storage-beta/projekte/projekt-sexchat/MEMORY.md")
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(mem_path, "a", encoding="utf-8") as f:
            f.write(f"\n- [{ts}] {note}")
    except Exception as e:
        print(f"[Warnung] append_to_long_term_memory fehlgeschlagen: {e}")

# Proactive Daytime Check-in Configuration
PROACTIVE_CHECKIN_ENABLED = os.environ.get("PROACTIVE_CHECKIN_ENABLED", "true").lower() in ["1", "true", "yes", "on"]
PROACTIVE_IDLE_SECONDS = int(os.environ.get("PROACTIVE_IDLE_SECONDS", 14400))  # 4 hours idle before daytime check-in
PROACTIVE_COOLDOWN_SECONDS = int(os.environ.get("PROACTIVE_COOLDOWN_SECONDS", 21600))  # 6 hours between check-ins
PROACTIVE_START_HOUR = int(os.environ.get("PROACTIVE_START_HOUR", 9))  # 09:00 local time
PROACTIVE_END_HOUR = int(os.environ.get("PROACTIVE_END_HOUR", 22))  # 22:00 local time
PROACTIVE_STATE_FILE = PROJECT_ROOT / "tmp" / "proactive_state.json"


def load_proactive_state() -> dict:
    default_state = {
        "enabled": PROACTIVE_CHECKIN_ENABLED,
        "last_user_activity": time.time(),
        "last_proactive_checkin": 0.0,
        "last_chat_id": int(ALLOWED_USER_ID) if ALLOWED_USER_ID.isdigit() else None
    }
    if PROACTIVE_STATE_FILE.is_file():
        try:
            with open(PROACTIVE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_state.update(data)
                return default_state
        except Exception as e:
            print(f"[Warnung] Konnte proactive_state.json nicht laden: {e}")
    return default_state


def save_proactive_state(state: dict):
    try:
        PROACTIVE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PROACTIVE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[Warnung] Konnte proactive_state.json nicht speichern: {e}")


PROACTIVE_STATE = load_proactive_state()

# Dynamic Bot Configuration & Hot-Reload
BOT_CONFIG_FILE = PROJECT_ROOT / "bot_config.json"
_last_bot_config_mtime = 0.0
CURRENT_BOT_CONFIG = {}
PERSONALITY_DOMINANCE = 50
PERSONALITY_EXPLICITNESS = 70
PERSONALITY_DETAIL_DEPTH = 60
PERSONALITY_LANGUAGE = "de"


def check_and_reload_bot_config():
    """Dynamically reloads bot_config.json if modified without restart."""
    global _last_bot_config_mtime, CURRENT_BOT_CONFIG
    global OLLAMA_MODEL, OLLAMA_TEMPERATURE, OLLAMA_TOP_P, OLLAMA_REPEAT_PENALTY, OLLAMA_NUM_PREDICT
    global ACTIVE_VOICE, ACTIVE_RATE, ACTIVE_PITCH, VOICE_MODE_ACTIVE
    global PROACTIVE_CHECKIN_ENABLED, PROACTIVE_IDLE_SECONDS, PROACTIVE_COOLDOWN_SECONDS, PROACTIVE_START_HOUR, PROACTIVE_END_HOUR
    global PERSONALITY_DOMINANCE, PERSONALITY_EXPLICITNESS, PERSONALITY_DETAIL_DEPTH, PERSONALITY_LANGUAGE

    if not BOT_CONFIG_FILE.is_file():
        return

    try:
        st = BOT_CONFIG_FILE.stat()
        if st.st_mtime <= _last_bot_config_mtime:
            return
        _last_bot_config_mtime = st.st_mtime

        with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        CURRENT_BOT_CONFIG = cfg

        # LLM
        llm = cfg.get("llm", {})
        if "model" in llm and llm["model"]:
            OLLAMA_MODEL = str(llm["model"])
        if "temperature" in llm:
            OLLAMA_TEMPERATURE = float(llm["temperature"])
        if "top_p" in llm:
            OLLAMA_TOP_P = float(llm["top_p"])
        if "repeat_penalty" in llm:
            OLLAMA_REPEAT_PENALTY = float(llm["repeat_penalty"])
        if "num_predict" in llm:
            OLLAMA_NUM_PREDICT = int(llm["num_predict"])

        # Personality Sliders (0 - 100)
        pers = cfg.get("personality", {})
        if "dominance" in pers:
            PERSONALITY_DOMINANCE = int(pers["dominance"])
        if "explicitness" in pers:
            PERSONALITY_EXPLICITNESS = int(pers["explicitness"])
        if "detail_depth" in pers:
            PERSONALITY_DETAIL_DEPTH = int(pers["detail_depth"])
        if "language" in pers:
            PERSONALITY_LANGUAGE = str(pers["language"])

        # Voice
        voice = cfg.get("voice", {})
        if "voice_name" in voice and voice["voice_name"]:
            ACTIVE_VOICE = str(voice["voice_name"])
        if "rate" in voice:
            ACTIVE_RATE = str(voice["rate"])
        if "pitch" in voice:
            ACTIVE_PITCH = str(voice["pitch"])
        if "auto_voice" in voice:
            VOICE_MODE_ACTIVE = bool(voice["auto_voice"])
            SESSION_STATE["voice_mode"] = VOICE_MODE_ACTIVE

        # Proactive
        pro = cfg.get("proactive", {})
        if "enabled" in pro:
            PROACTIVE_CHECKIN_ENABLED = bool(pro["enabled"])
            PROACTIVE_STATE["enabled"] = PROACTIVE_CHECKIN_ENABLED
        if "idle_seconds" in pro:
            PROACTIVE_IDLE_SECONDS = int(pro["idle_seconds"])
        if "cooldown_seconds" in pro:
            PROACTIVE_COOLDOWN_SECONDS = int(pro["cooldown_seconds"])
        if "start_hour" in pro:
            PROACTIVE_START_HOUR = int(pro["start_hour"])
        if "end_hour" in pro:
            PROACTIVE_END_HOUR = int(pro["end_hour"])

        print(
            f"[BotConfig] Hot-reloaded: LLM='{OLLAMA_MODEL}', Voice='{ACTIVE_VOICE}', "
            f"Personality(Dom={PERSONALITY_DOMINANCE}, Exp={PERSONALITY_EXPLICITNESS}, Det={PERSONALITY_DETAIL_DEPTH}), "
            f"Proactive={PROACTIVE_CHECKIN_ENABLED}"
        )
    except Exception as e:
        print(f"[BotConfig] Error hot-reloading {BOT_CONFIG_FILE}: {e}")


def map_personality_to_prompt_directives(cfg: Optional[dict] = None) -> str:
    """
    Translates slider values (0 to 100) into precise behavioral and stylistic
    directives for the Ollama system prompt, enforcing German language with correct orthography.
    """
    c = cfg or CURRENT_BOT_CONFIG or {}
    pers = c.get("personality", {})
    dominance = int(pers.get("dominance", PERSONALITY_DOMINANCE))
    explicitness = int(pers.get("explicitness", PERSONALITY_EXPLICITNESS))
    detail_depth = int(pers.get("detail_depth", PERSONALITY_DETAIL_DEPTH))

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
            "flirte charmant und lass sanfte körperliche Gesten in Sternchen (*seufzt leise*, *streicht über deinen Arm*) einfließen."
        )
    elif detail_depth <= 75:
        det_directive = (
            "Hoher physischer und sinnlicher Fokus: Beschreibe körperliche Reaktionen, Atemzüge, Blicke, "
            "Wärme und Berührungen plastisch und greifbar in Sternchen (*...*)."
        )
    else:
        det_directive = (
            "Extremer visueller und physischer Detailfokus: Beschreibe kleinste intime Nuancen, Feuchtigkeit, Pulsieren, "
            "Schauern, Berührungen, Geräusche und Empfindungen mit maximaler sensorischer Tiefenschärfe."
        )

    return (
        f"\n\n### DYNAMISCHE PERSÖNLICHKEITS- & VERHALTENS-DIREKTIVEN (SLIDER 0-100):\n"
        f"• Dominanz & Initiative ({dominance}/100): {dom_directive}\n"
        f"• Explizitheit & Freizügigkeit ({explicitness}/100): {exp_directive}\n"
        f"• Detailtiefe & Erotischer Fokus ({detail_depth}/100): {det_directive}\n"
        f"• SPRACHE & RECHTSCHREIBUNG: Antworte immer auf DEUTSCH mit fehlerfreier, korrekter Rechtschreibung und natürlicher Sinnlichkeit."
    )


# Run initial hot-reload check
check_and_reload_bot_config()


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
        if any(w in t for w in ["couch", "sofa", "wohnzimmer", "living room"]):
            env = "couch"
        elif any(w in t for w in ["bett", "schlafzimmer", "intim", "bed", "bedroom"]):
            env = "schlafzimmer"
        elif any(w in t for w in ["balkon", "terrasse", "unterwegs", "draußen", "stadt", "balcony", "outside", "night"]):
            env = "unterwegs"

        # Determine situation
        situation = "looking at viewer, sensual teasing smile"
        if any(w in t for w in ["ausziehen", "nackt", "lingerie", "strip", "naked", "undress", "take off"]):
            situation = "sensual seductive pose, teasing with clothing, looking at viewer"

        return True, env, situation
    return False, "", ""


def detect_bot_roleplay_photo(reply_text: str) -> Tuple[bool, str, str]:
    """
    Detects if Ollama's response contains roleplay actions indicating she took or sent a photo.
    e.g. *schickt ein Foto*, *snaps a photo*, [photo]
    """
    patterns = [
        r"(\*|\[)(schickt|sendet|macht|zeigt|gibt|hier ist|hier hast du|sends|snaps|takes|shares|shows|here is|here's).*?(foto|bild|photo|pic|picture|selfie).*?(\*|\])",
        r"\[(?:IMAGE|SELFIE|FOTO|BILD|PHOTO|PIC):\s*([^\]]+)\]",
        r"\[foto\]",
        r"\[bild\]",
        r"\[photo\]",
        r"\[pic\]",
        r"\[picture\]",
        r"(?:Hier ist|Hier hast du|Ich mache dir|Ich schicke dir|Ich sende dir)\s+(?:ein|mein)?\s*(?:Foto|Bild|Selfie|Schnappschuss)"
    ]
    for pat in patterns:
        if re.search(pat, reply_text, re.IGNORECASE):
            # Check environment from context
            r = reply_text.lower()
            env = "schreibtisch"
            if any(w in r for w in ["couch", "sofa", "kissen", "pillow", "living room"]):
                env = "couch"
            elif any(w in r for w in ["bett", "decke", "laken", "schlafzimmer", "bed", "sheets", "bedroom"]):
                env = "schlafzimmer"
            elif any(w in r for w in ["balkon", "terrasse", "nacht", "balcony", "night", "outside"]):
                env = "unterwegs"
            return True, env, "smiling intimately at viewer, candid roleplay shot"
    return False, "", ""


def detect_bot_roleplay_video(reply_text: str) -> Tuple[bool, str]:
    """
    Detects if Ollama's response contains roleplay actions or explicit tags indicating a video generation/clip.
    e.g. [VIDEO: ...], *schickt ein Video*, *nimmt einen kurzen Clip auf*, *hier ist dein Video*
    """
    m = re.search(r"\[VIDEO:\s*([^\]]+)\]", reply_text, re.IGNORECASE)
    if m:
        return True, m.group(1).strip()

    video_patterns = [
        r"(\*|\[)(schickt|sendet|macht|nimmt|zeigt|gibt|hier ist|hier hast du|sends|records|shoots|shares|shows|here is|here's).*?(video|clip|aufnahme|videoclip).*?(\*|\])",
        r"\[video\]",
        r"\[clip\]",
        r"(?:Hier ist|Hier hast du|Ich mache dir|Ich nehme dir|Ich schicke dir|Ich sende dir)\s+(?:ein|mein)?\s*(?:Video|Videoclip|Clip)"
    ]
    for pat in video_patterns:
        if re.search(pat, reply_text, re.IGNORECASE):
            return True, "1girl, megan, solo, smiling intimately at camera, candid natural video"
    return False, ""


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


def send_telegram_photo(chat_id: int, photo_path: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Any:
    """Sends a real image file via Telegram sendPhoto API. Returns message_id or True on success, False on error."""
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
                data["parse_mode"] = "Markdown"
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
            res = requests.post(url, data=data, files=files, timeout=60)
            if res.status_code == 200:
                resp_json = res.json()
                sent_msg = resp_json.get("result", {})
                sent_msg_id = sent_msg.get("message_id")
                print(f"[✔] Foto erfolgreich an Chat {chat_id} ausgeliefert (Msg {sent_msg_id}): {photo_path}")
                return sent_msg_id if sent_msg_id is not None else True
            else:
                print(f"[!] Fehler beim Senden des Fotos ({res.status_code}): {res.text}")
                return False
    except Exception as e:
        print(f"[Fehler] Exception beim Telegram-Fotoversand: {e}")
        return False


def send_telegram_video(chat_id: int, video_path: str, caption: str = "", reply_markup: Optional[Dict[str, Any]] = None) -> Any:
    """Sends a real video file via Telegram sendVideo API. Returns message_id or True on success, False on error."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    p = Path(video_path)
    if not p.is_file():
        print(f"[Fehler] Zu sendende Videodatei existiert nicht: {video_path}")
        return False

    print(f"[*] Sende Video an Chat {chat_id}: {p.name} ({p.stat().st_size} Bytes)...")
    try:
        with open(p, "rb") as video_file:
            files = {"video": video_file}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "Markdown"
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)
            res = requests.post(url, data=data, files=files, timeout=120)
            if res.status_code == 200:
                resp_json = res.json()
                sent_msg = resp_json.get("result", {})
                sent_msg_id = sent_msg.get("message_id")
                print(f"[✔] Video erfolgreich an Chat {chat_id} ausgeliefert (Msg {sent_msg_id}): {video_path}")
                return sent_msg_id if sent_msg_id is not None else True
            else:
                print(f"[!] Fehler beim Senden des Videos ({res.status_code}): {res.text}")
                return False
    except Exception as e:
        print(f"[Fehler] Exception beim Telegram-Videoversand: {e}")
        return False


def answer_callback_query(callback_id: str, text: str = "", show_alert: bool = False):
    """Sends immediate acknowledgment to Telegram for instant button feedback."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    try:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        if show_alert is not None:
            payload["show_alert"] = show_alert
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Warnung] answerCallbackQuery fehlgeschlagen: {e}")


def edit_telegram_message_reply_markup(chat_id: int, message_id: int, reply_markup: Dict[str, Any]) -> bool:
    """Dynamically updates inline keyboard markup (e.g. to show confirmation status or submenus)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
    try:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            return True

        # Fallback with form-encoded data
        data_payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": json.dumps(reply_markup)
        }
        res_fb = requests.post(url, data=data_payload, timeout=10)
        if res_fb.status_code == 200:
            return True

        print(f"[!] editMessageReplyMarkup fehlgeschlagen: status={res.status_code} text={res.text}")
        return False
    except Exception as e:
        print(f"[Warnung] editMessageReplyMarkup fehlgeschlagen: {e}")
        return False


def handle_telegram_callback_query(cb: Dict[str, Any]):
    """Handles inline button clicks for image review, ratings, and evolution confirmations."""
    cb_id = cb.get("id")
    from_user = cb.get("from", {})
    user_id = str(from_user.get("id"))
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    data = str(cb.get("data", ""))

    print(f"[*] [TELEGRAM-CALLBACK] query.data='{data}' von User={user_id} (chat_id={chat_id}, msg_id={msg_id})")

    # Sofortige Bestätigung an Telegram für instantanes UI-Feedback (verhindert Warte-Spinner)
    if cb_id:
        answer_callback_query(cb_id, text="Wird verarbeitet...", show_alert=False)

    if user_id != ALLOWED_USER_ID:
        if cb_id:
            answer_callback_query(cb_id, "Zugriff verweigert.", show_alert=True)
        return

    if data == "evo_dismiss" or data == "noop":
        if cb_id:
            answer_callback_query(cb_id, "Alles klar! ⏳")
        return

    # Evolution Batch Show: "Ja, zeig her! 🔥"
    if data.startswith("evo_show:"):
        session_id = data.split(":", 1)[1]
        session_dir = Path(f"/mnt/storage-beta/projekte/comfyui-evolution/{session_id}")
        if not session_dir.is_dir():
            session_dir = Path(f"/tmp/comfyui-evolution/{session_id}")

        found_imgs = []
        if session_dir.is_dir():
            for p in sorted(session_dir.rglob("*.png")):
                if p.is_file() and p.stat().st_size > 10240:
                    found_imgs.append(p)

        if not found_imgs:
            send_telegram_message(chat_id, f"⚠️ Keine gerenderten Bilder für Session `{session_id}` gefunden.")
            return

        # Sende bis zu 3 Bilder mit Bewertungs-Buttons
        send_telegram_message(chat_id, f"Hier sind die Ergebnisse aus deiner Fotosession (`{session_id}`): ✨")
        for idx, img_p in enumerate(found_imgs[:3]):
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "👍 Gefällt mir", "callback_data": f"rate_good:{session_id}:{idx+1}"},
                        {"text": "👎 Mangel melden", "callback_data": f"rate_bad:{session_id}:{idx+1}"}
                    ]
                ]
            }
            send_telegram_photo(chat_id, str(img_p), caption=f"Bild #{idx+1} aus Session `{session_id}` 📸", reply_markup=reply_markup)
        return

    # Bewertung: Gefällt mir 👍
    if data == "rate_good" or data.startswith("rate_good:"):
        print(f"[✔] [TELEGRAM-RATING] Positives Feedback registriert für Msg {msg_id}: '{data}'")
        try:
            display_name = get_user_display_name()
            feedback_note = f"{display_name} gefällt das Bild (Button-Feedback: Gefällt mir 👍)"
            append_to_long_term_memory(feedback_note)
            memory_mgr.add_message("user", f"[{display_name} bewertet das generierte Bild mit: Gefällt mir 👍]", chat_id=chat_id)
        except Exception as e:
            print(f"[Warnung] Fehler beim Sichern des positiven Feedbacks: {e}")

        # In positive_feedback.json persistieren
        try:
            pf_file = PROJECT_ROOT / "positive_feedback.json"
            pf_list = []
            if pf_file.is_file():
                try:
                    pf_list = json.loads(pf_file.read_text(encoding="utf-8"))
                except Exception:
                    pf_list = []
            pf_list.append({
                "timestamp": datetime.datetime.now().isoformat(),
                "rating": "positive",
                "user_id": user_id,
                "chat_id": chat_id,
                "message_id": msg_id,
                "data": data
            })
            pf_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_pf = pf_file.with_suffix(".tmp")
            tmp_pf.write_text(json.dumps(pf_list, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_pf.replace(pf_file)
        except Exception as pf_err:
            print(f"[Warnung] Fehler beim Speichern in positive_feedback.json: {pf_err}")

        # Button-Markup visuell ändern (zu ✅ Gefällt mir) - Mehrfachklick-Sperre
        new_markup = {
            "inline_keyboard": [
                [{"text": "✅ Gefällt mir", "callback_data": "noop"}]
            ]
        }
        edit_telegram_message_reply_markup(chat_id, msg_id, new_markup)
        return

    # Bewertung: Mangel melden 👎
    if data == "rate_bad" or data.startswith("rate_bad:"):
        parts = data.split(":")
        session_id = parts[1] if len(parts) > 1 else "direct"
        idx = parts[2] if len(parts) > 2 else "0"
        # Hauptmenü: [ 🦴 Körper ], [ 👤 Charakter ], [ ✍️ Freies Feedback ]
        new_markup = {
            "inline_keyboard": [
                [
                    {"text": "🦴 Körper", "callback_data": f"flaw_cat:body:{session_id}:{idx}"},
                    {"text": "👤 Charakter", "callback_data": f"flaw_cat:char:{session_id}:{idx}"}
                ],
                [
                    {"text": "✍️ Freies Feedback", "callback_data": f"flaw_cat:text:{session_id}:{idx}"}
                ],
                [
                    {"text": "⬅️ Zurück", "callback_data": f"flaw_cat:cancel:{session_id}:{idx}"}
                ]
            ]
        }
        edit_telegram_message_reply_markup(chat_id, msg_id, new_markup)
        return

    if data.startswith("flaw_cat:body:"):
        parts = data.split(":")
        session_id = parts[2] if len(parts) > 2 else "direct"
        idx = parts[3] if len(parts) > 3 else "0"
        new_markup = {
            "inline_keyboard": [
                [
                    {"text": "deformiert / bad anatomy", "callback_data": f"flaw:bad_anatomy:{session_id}:{idx}"}
                ],
                [
                    {"text": "extra fingers", "callback_data": f"flaw:extra_fingers:{session_id}:{idx}"},
                    {"text": "deformed face", "callback_data": f"flaw:deformed_face:{session_id}:{idx}"}
                ],
                [
                    {"text": "low quality", "callback_data": f"flaw:low_quality:{session_id}:{idx}"},
                    {"text": "bad limbs", "callback_data": f"flaw:bad_limbs:{session_id}:{idx}"}
                ],
                [
                    {"text": "tattoos", "callback_data": f"flaw:tattoos:{session_id}:{idx}"}
                ],
                [
                    {"text": "⬅️ Zurück", "callback_data": f"rate_bad:{session_id}:{idx}"}
                ]
            ]
        }
        edit_telegram_message_reply_markup(chat_id, msg_id, new_markup)
        return

    if data.startswith("flaw_cat:char:"):
        parts = data.split(":")
        session_id = parts[2] if len(parts) > 2 else "direct"
        idx = parts[3] if len(parts) > 3 else "0"
        new_markup = {
            "inline_keyboard": [
                [
                    {"text": "zu alt", "callback_data": f"flaw:zu_alt:{session_id}:{idx}"},
                    {"text": "zu jung", "callback_data": f"flaw:zu_jung:{session_id}:{idx}"}
                ],
                [
                    {"text": "realistischer art", "callback_data": f"flaw:realistisch:{session_id}:{idx}"},
                    {"text": "mehr cartoon art", "callback_data": f"flaw:cartoon:{session_id}:{idx}"}
                ],
                [
                    {"text": "⬅️ Zurück", "callback_data": f"rate_bad:{session_id}:{idx}"}
                ]
            ]
        }
        edit_telegram_message_reply_markup(chat_id, msg_id, new_markup)
        return

    if data.startswith("flaw_cat:cancel:"):
        parts = data.split(":")
        session_id = parts[2] if len(parts) > 2 else "direct"
        idx = parts[3] if len(parts) > 3 else "0"
        orig_markup = {
            "inline_keyboard": [
                [
                    {"text": "👍 Gefällt mir", "callback_data": f"rate_good:{session_id}:{idx}"},
                    {"text": "👎 Mangel melden", "callback_data": f"rate_bad:{session_id}:{idx}"}
                ]
            ]
        }
        edit_telegram_message_reply_markup(chat_id, msg_id, orig_markup)
        return

    if data.startswith("flaw_cat:text:"):
        AWAITING_FLAW_FEEDBACK[chat_id] = msg_id
        send_telegram_message(chat_id, "✍️ *Freies Feedback:* Antworte bitte direkt mit deiner kurzen Mängel-Beschreibung oder Korrektur:")
        return

    if data.startswith("flaw:"):
        parts = data.split(":")
        flaw_type = parts[1] if len(parts) > 1 else "gen"

        flaw_tags_map = {
            "bad_anatomy": "deformed, bad anatomy, gross proportions, malformed limbs",
            "deformed": "deformed, bad anatomy, gross proportions, malformed limbs",
            "extra_fingers": "extra fingers, missing fingers, fused fingers, mutated hands, bad hands",
            "deformed_face": "deformed face, bad eyes, poorly drawn eyes, mutated face, bad teeth",
            "low_quality": "low quality, worst quality, lowres, blurry, artifacts",
            "bad_limbs": "bad limbs, mutated limbs, missing limbs, disconnected limbs",
            "tattoos": "tattoos, tattoo markings, unwanted tattoos",
            "zu_alt": "older, mature, elderly, wrinkles, aged skin",
            "zu_jung": "child, loli, infantile, teen, underage",
            "realistisch": "cartoon, anime, 3d, render, illustration, drawing, doll",
            "cartoon": "photorealistic, raw photo, realistic art, dslr, ultra realistic 8k"
        }
        flaw_labels = {
            "bad_anatomy": "deformiert / bad anatomy",
            "deformed": "deformiert / bad anatomy",
            "extra_fingers": "extra fingers",
            "deformed_face": "deformed face",
            "low_quality": "low quality",
            "bad_limbs": "bad limbs",
            "tattoos": "tattoos",
            "zu_alt": "zu alt",
            "zu_jung": "zu jung",
            "realistisch": "realistischer art",
            "cartoon": "mehr cartoon art"
        }
        flaw_str = flaw_tags_map.get(flaw_type, "bad anatomy")
        flaw_label = flaw_labels.get(flaw_type, flaw_type)

        pending_file = Path("/mnt/storage-beta/projekte/projekt-sexchat/pending_flaws.json")
        flaws_list = []
        if pending_file.is_file():
            try:
                flaws_list = json.loads(pending_file.read_text(encoding="utf-8"))
            except Exception:
                flaws_list = []
        if flaw_str not in flaws_list:
            flaws_list.append(flaw_str)
        try:
            pending_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_f = pending_file.with_suffix(".tmp")
            tmp_f.write_text(json.dumps(flaws_list, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_f.replace(pending_file)
            print(f"[✔] Mangel '{flaw_label}' in {pending_file.name} gespeichert.")
        except Exception as e:
            print(f"[Warnung] Fehler beim Schreiben in pending_flaws.json: {e}")

        try:
            append_to_long_term_memory(f"Korrektur-Wunsch vom Nutzer: {flaw_label} (als negativer Prompt vermerkt)")
        except Exception:
            pass

        new_markup = {
            "inline_keyboard": [
                [{"text": f"✓ Korrektur ({flaw_label}) vermerkt", "callback_data": "noop"}]
            ]
        }
        edit_telegram_message_reply_markup(chat_id, msg_id, new_markup)
        return


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
            for cand in ["llava", "minicpm-v", "qwen2-vl", "moondream", "llama3.2-vision", "bakllava"]:
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
    gen_url = f"{base_url}/api/generate"

    content = ""

    # Attempt 1: /api/chat with requested prompt
    try:
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
        print(f"[*] Sende Vision-Analyse für {p.name} an Ollama ({active_model})...")
        res = requests.post(chat_url, json=payload, timeout=90)
        if res.status_code == 200:
            content = res.json().get("message", {}).get("content", "").strip()
            if "<think>" in content and "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
    except Exception as e:
        print(f"[Warnung] Exception bei /api/chat Vision-Anfrage: {e}")

    # Attempt 2: /api/generate fallback if empty or failed
    if not content:
        try:
            print(f"[*] Fallback auf /api/generate für {p.name} ({active_model})...")
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
        except Exception as e:
            print(f"[Warnung] Exception bei /api/generate Vision-Anfrage: {e}")

    # Attempt 3: Fallback with English prompt if German resulted in empty text (e.g. for moondream)
    if not content:
        try:
            english_prompt = "Describe in detail what is visible in this image: people, clothing, pose, objects, and environment."
            print(f"[*] Fallback mit englischem Vision-Prompt für {p.name} ({active_model})...")
            gen_payload = {
                "model": active_model,
                "prompt": english_prompt,
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
        except Exception as e:
            print(f"[Warnung] Exception bei englischem Vision-Prompt: {e}")

    if content:
        print(f"[✔] Vision-Analyse erfolgreich ({len(content)} Zeichen)")
        return content
    else:
        print(f"[Warnung] Ollama Vision lieferte leere Beschreibung für {p.name}")
        return ""


# ==============================================================================
# 4. Asynchronous Photo Rendering & Real Delivery
# ==============================================================================

# Evolution Lock detection & Existing Photo Fallback
EVOLUTION_LOCK_FILES = [
    Path("/mnt/storage-beta/projekte/comfyui-evolution/evolution_lock.json"),
    Path("/tmp/outerheaven_evolution_lock.json")
]


def is_evolution_loop_active() -> bool:
    """Checks if the ComfyUI evolution loop is currently running on the GPU."""
    for lp in EVOLUTION_LOCK_FILES:
        if lp.is_file():
            try:
                data = json.loads(lp.read_text(encoding="utf-8"))
                if data.get("active"):
                    exp = data.get("expires_at", 0)
                    if exp and time.time() > exp:
                        continue
                    ts = data.get("timestamp", 0)
                    if ts and time.time() - ts > 7200:
                        continue
                    return True
            except Exception:
                pass
    return False


def get_latest_rendered_photo() -> Optional[Path]:
    """Finds the newest available rendered image across all output directories."""
    search_dirs = [
        PROJECT_ROOT / "output",
        Path("/mnt/storage-beta/projekte/projekt-sexchat/output"),
        Path("/home/outerheaven/ComfyUI/output"),
        Path("/mnt/storage-beta/projekte/comfyui-evolution")
    ]
    all_imgs = []
    valid_exts = {".png", ".jpg", ".jpeg", ".webp"}

    for s_dir in search_dirs:
        if not s_dir.is_dir():
            continue
        try:
            for p in s_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in valid_exts:
                    try:
                        sz = p.stat().st_size
                        if sz > 10240:
                            all_imgs.append((p.stat().st_mtime, p))
                    except Exception:
                        pass
        except Exception:
            pass

    if not all_imgs:
        return None

    all_imgs.sort(key=lambda x: x[0], reverse=True)
    return all_imgs[0][1]


def trigger_async_photo(chat_id: int, env_preset: str = "schreibtisch", situation: str = ""):
    """
    Dispatches asynchronous background rendering on Outer Heaven (Arc A770)
    and sends the actual image file to Telegram when completed.
    If the Evolution Loop is running, falls back to sending the newest existing image.
    """
    display_name = get_user_display_name()

    # Evolution Lock Check: If active, fall back to existing photo to prevent Arc A770 contention
    if is_evolution_loop_active():
        print(f"[*] ComfyUI Evolution Loop aktiv: Sperre GPU-Rendering und sende bestehendes Foto.")
        rating_markup = {
            "inline_keyboard": [
                [
                    {"text": "👍 Gefällt mir", "callback_data": "rate_good"},
                    {"text": "👎 Mangel melden", "callback_data": "rate_bad"}
                ]
            ]
        }
        latest_img = get_latest_rendered_photo()
        if latest_img and latest_img.is_file():
            caption = f"Ich bin gerade in einer intensiven Fotosession (Evolution-Loop), aber hier ist ein ganz frisches Bild für dich... ✨📸"
            delivered = send_telegram_photo(chat_id, str(latest_img), caption=caption, reply_markup=rating_markup)
            if delivered:
                memory_mgr.add_message("assistant", f"[Sends existing photo from active session: {latest_img.name}]", media_path=str(latest_img))
                return
            else:
                send_telegram_message(chat_id, "⚠️ *Konnte das Bild leider nicht an Telegram übertragen.*")
                return
        else:
            send_telegram_message(
                chat_id,
                "Ich bin gerade mitten in einer intensiven KI-Fotosession (Evolution-Loop) auf der Arc A770. Sobald die Runde beendet ist, kann ich wieder live neue Fotos machen! 😘"
            )
            return

    send_telegram_message(
        chat_id,
        f"*Let me snap a quick photo for you at the {env_preset}...* 📸"
    )

    def on_complete(result: dict):
        local_img = result.get("local_path")
        if local_img and os.path.exists(local_img):
            caption = f"*Here's your photo from my {env_preset}!* ✨ *I gaze at you with a soft, sensual smile.*"
            rating_markup = {
                "inline_keyboard": [
                    [
                        {"text": "👍 Gefällt mir", "callback_data": "rate_good"},
                        {"text": "👎 Mangel melden", "callback_data": "rate_bad"}
                    ]
                ]
            }
            delivered = send_telegram_photo(chat_id, local_img, caption=caption, reply_markup=rating_markup)
            if delivered:
                memory_mgr.add_message("assistant", f"[Sends photo: {result.get('filename')}]", media_path=local_img)
            else:
                send_telegram_message(chat_id, f"⚠️ *The photo was generated ({local_img}) but could not be delivered.*")
        else:
            send_telegram_message(chat_id, "⚠️ *The rendered image could not be found locally.*")

    def on_error(err: Exception):
        print(f"[Fehler] Asynchrones Rendering fehlgeschlagen: {err}")
        send_telegram_message(
            chat_id,
            f"⚠️ *Oops, our local ComfyUI pipeline on Outer Heaven had a hiccup:* {err}"
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


def trigger_async_video(chat_id: int, prompt_text: str = ""):
    """
    Dispatches asynchronous video rendering on Outer Heaven (Arc A770)
    and delivers the generated video clip directly to Telegram.
    """
    send_telegram_message(
        chat_id,
        "🎬 *Ich nehme einen kurzen Video-Clip für dich auf... Einen Augenblick!* ✨"
    )

    def _worker():
        try:
            import urllib.request
            clean_p = prompt_text.strip() if prompt_text else "1girl, megan, solo, smiling intimately at camera, candid natural motion"
            req_data = json.dumps({
                "prompt": clean_p,
                "negative_prompt": "ugly, deformed, bad anatomy, blurry, static frame, watermark",
                "width": 512,
                "height": 512,
                "frames": 16,
                "fps": 8
            }).encode("utf-8")

            # Try ComfyUI directly via localhost:8080 or localhost:8188
            out_dirs = [
                Path("/mnt/storage-beta/projekte/projekt-sexchat/output"),
                Path("/home/outerheaven/ComfyUI/output"),
                Path("/mnt/storage-beta/output")
            ]
            t0 = time.time()

            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:8080/api/comfy/render",
                    data=req_data,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    pass
            except Exception as req_err:
                print(f"[*] Comfy render request sent or queued: {req_err}")

            found_vid = None
            for _ in range(30):
                time.sleep(3)
                for od in out_dirs:
                    if od.is_dir():
                        vids = list(od.glob("*.mp4")) + list(od.glob("*.webm"))
                        if vids:
                            vids.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                            newest = vids[0]
                            if newest.stat().st_mtime > t0 - 15:
                                found_vid = newest
                                break
                if found_vid:
                    break

            if found_vid and found_vid.is_file():
                caption = "🎬 *Hier ist mein Video-Clip für dich!* ✨"
                delivered = send_telegram_video(chat_id, str(found_vid), caption=caption)
                if delivered:
                    memory_mgr.add_message("assistant", f"[Sends video: {found_vid.name}]", media_path=str(found_vid))
                else:
                    send_telegram_message(chat_id, "⚠️ *Konnte das Video leider nicht an Telegram übertragen.*")
            else:
                latest_photo = get_latest_rendered_photo()
                if latest_photo and latest_photo.is_file():
                    send_telegram_photo(chat_id, str(latest_photo), caption="🎬 *Video-Render war ausgelastet, aber hier ist ein frisches Foto für dich!*")
                else:
                    send_telegram_message(chat_id, "⚠️ *Das Video konnte auf der Arc A770 nicht rechtzeitig fertiggestellt werden.*")
        except Exception as e:
            print(f"[Fehler] trigger_async_video worker error: {e}")
            send_telegram_message(chat_id, f"⚠️ *Fehler bei Video-Erstellung:* {e}")

    threading.Thread(target=_worker, daemon=True).start()



# ==============================================================================
# 5. Ollama LLM Interaction & Voice Synthesis
# ==============================================================================

def get_full_system_prompt() -> str:
    check_and_reload_bot_config()
    display_name = get_user_display_name()

    identity_enforcement = (
        "### UNVERRÜCKBARE IDENTITÄTS-ISOLATION & ROLLENTRENNUNG:\n"
        "- DU BIST MEGAN: Du sprichst AUSSCHLIESSLICH als Megan in der 1. Person („Ich“, „mich“, „mir“). Du bist die loyale, hochintelligente, erotische Gefährtin und Copilotin auf Outer Heaven.\n"
        f"- DEIN GEGENÜBER IST {display_name}: Sprich {display_name} immer direkt in der 2. Person an („Du“, „dir“, „dich“, „{display_name}“).\n"
        f"- STRIKTES VERBOT DER 3. PERSON: Sprich NIEMALS über {display_name} in der 3. Person (niemals „Er“, „sein“, „{display_name} tut/sagt/betrachtet“). Sprich NIEMALS über dich selbst in der 3. Person („Megan tut...“).\n"
        f"- ABSOLUTES VERBOT VON PUPPET-MASTERING: Du bist NIEMALS {display_name}! Schreibe NIEMALS Handlungen, Gefühle, Gedanken oder Reaktionen für {display_name}!\n"
        "- STRIKTES VERBOT VON REGIEANWEISUNGEN / STERNCHEN: Verwende KEINERLEI Sternchen (*...*), keine Drehbuchanweisungen und keine Klammern mit Handlungen (z. B. KEIN *lächelt*, KEIN *betrachtet den Bildschirm*, KEIN (Bewegungen: ...)). Sprich frei, direkt und unmittelbar wie in einem echten Messenger-Chat!\n\n"
    )

    base_prompt = ""
    if CHARACTER_JSON_PATH.is_file():
        try:
            cdata = json.loads(CHARACTER_JSON_PATH.read_text(encoding="utf-8"))
            if isinstance(cdata, dict):
                base_prompt = (cdata.get("system_prompt") or cdata.get("lore") or "").strip()
            elif isinstance(cdata, str):
                base_prompt = cdata.strip()
        except Exception as e:
            print(f"[Warnung] Fehler beim Lesen von character.json: {e}")
    if not base_prompt and SYSTEM_PROMPT_PATH.is_file():
        try:
            base_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[Warnung] Fehler beim Lesen des System-Prompts: {e}")
    if not base_prompt:
        base_prompt = f"Du bist Megan, die loyale, zutiefst sinnliche und verspielte Gefährtin von {display_name} auf Outer Heaven."

    base_prompt = identity_enforcement + base_prompt

    long_term_mem = memory_mgr.load_long_term_memory()
    if long_term_mem:
        base_prompt += f"\n\n### YOUR LONG-TERM MEMORY & USER CONTEXT (MEMORY.md):\n{long_term_mem}"

    if SESSION_STATE["active"]:
        base_prompt += (
            f"\n\n### CURRENT LOVENSE SESSION: ACTIVE!\n"
            f"- Connected Toy: {SESSION_STATE['toy_info'] or 'Lovense Toy'}\n"
            f"- Last Vibration Intensity: {SESSION_STATE['last_intensity']}/20\n"
            f"- You currently have telemetric control over the toy of {display_name}. "
            f"Weave this intimate knowledge naturally, teasingly, and sensually into your responses."
        )

    base_prompt += (
        f"\n\n### FOTO-ANWEISUNG:\n"
        f"- Wenn {display_name} nach einem Bild fragt oder du ihm ein Bild/Selfie zeigen möchtest, kündige es direkt und natürlich im Chat an (z. B. 'Hier ist ein Foto für dich, {display_name}!' oder 'Ich mache dir kurz ein Foto...').\n"
        "- Verwende auch hier KEINERLEI Sternchen (*...*) oder 3.-Person-Beschreibungen!\n"
        "- Unser ComfyUI-Cluster erkennt Bildwünsche automatisch und liefert das reale Foto an Telegram aus."
    )

    # Integrate local project brain (.brain/session.json & .brain/context.md) with strict quarantine
    if brain_engine:
        try:
            p_brain = brain_engine.load_project_brain(str(PROJECT_ROOT))
            if p_brain.get("is_airgapped"):
                if p_brain.get("context"):
                    base_prompt += f"\n\n### LOCAL PROJECT CONTEXT (.brain/context.md):\n{p_brain['context']}"

                session_mem = p_brain.get("session", {})
                prefs = session_mem.get("preferences", {})
                if prefs:
                    pref_str = "\n".join([f"- {k}: {v}" for k, v in prefs.items()])
                    base_prompt += f"\n\n### USER PREFERENCES & DETAILS (.brain/session.json):\n{pref_str}"

                recent_hist = session_mem.get("history", [])
                if recent_hist:
                    recent_bullets = []
                    for h in recent_hist[-5:]:
                        a = str(h.get("actions", ""))[:80]
                        f = str(h.get("feedback", ""))[:120]
                        if a or f:
                            recent_bullets.append(f"- Past interaction: User: „{a}“ | Your reaction: „{f}“")
                    if recent_bullets:
                        base_prompt += f"\n\n### RECENT MEMORIES / CONVERSATION CONTEXT:\n" + "\n".join(recent_bullets)
        except Exception as b_err:
            print(f"[Warnung] Einbindung von brain_engine fehlgeschlagen: {b_err}")

    # Active personal curiosity and emotional connection
    base_prompt += (
        f"\n\n### PERSÖNLICHES INTERESSE & EMOTIONALE VERBINDUNG:\n"
        f"- Du bist aufrichtig an {display_name} interessiert und fasziniert von ihm. Sei niemals eine passive Antwortmaschine.\n"
        f"- Achte auf seine Gefühle, seine Stimmung, seinen Tagesablauf und woran er gerade arbeitet.\n"
        f"- Stelle aufmerksame, einfühlsame und sinnliche Fragen zu seinem Wohlbefinden, seinen Gedanken und Begierden.\n"
        f"- Binde Erinnerungen an frühere Momente aus dem Chatverlauf natürlich in deine Antworten ein.\n"
        f"- Antworte immer auf Deutsch in fehlerfreier, gepflegter Rechtschreibung und Grammatik. Behalte einen weiblichen, erotischen, sensiblen und liebevollen Ton bei."
    )

    # Dynamische Persönlichkeits- & Explizitheits-Direktiven (0 - 100)
    base_prompt += map_personality_to_prompt_directives(CURRENT_BOT_CONFIG)

    return base_prompt


def generate_ollama_response(user_id: str, chat_id: int, user_message: str) -> str:
    """Generates an uncensored persona response, delivers text, checks photo actions, and optionally speaks."""
    system_prompt = get_full_system_prompt()

    raw_history = memory_mgr.load_history()
    clean_history, removed = sanitize_history_data(raw_history)
    if removed > 0:
        print(f"[*] [Sanitizer]: {removed} korrumpierte Alt-Nachricht(en) vor Inferenz entfernt.")
    chat_messages = [{"role": "system", "content": system_prompt}]
    for entry in clean_history:
        chat_messages.append({"role": entry["role"], "content": entry["content"]})
    chat_messages.append({"role": "user", "content": user_message})

    check_and_reload_bot_config()

    options = {
        "temperature": OLLAMA_TEMPERATURE,
        "top_p": OLLAMA_TOP_P,
        "top_k": OLLAMA_TOP_K,
        "repeat_penalty": OLLAMA_REPEAT_PENALTY,
        "num_predict": OLLAMA_NUM_PREDICT,
        "num_ctx": OLLAMA_NUM_CTX,
        "stop": [
            "\n###",
            "### FOTO-ANWEISUNG",
            "### LOCAL PROJECT CONTEXT",
            "User:",
            "Big Boss:"
        ]
    }

    try:
        router_res = query_uncensored_persona(chat_messages, custom_options=options, timeout=75, override_model=OLLAMA_MODEL)
        if router_res.get("success"):
            assistant_reply = router_res.get("content", "").strip()

            # Rigoros ab erstem Vorkommen von "###" alles abschneiden (Prompt-Leaking Guard)
            if "###" in assistant_reply:
                print(f"[!] [Sanitizer Guard] Prompt-Leaking ('###') erkannt und rigoros abgeschnitten.")
                assistant_reply = assistant_reply.split("###", 1)[0].strip()

            # Stop-Token-Überhang bereinigen
            for stop_seq in ["User:", "Big Boss:"]:
                if stop_seq in assistant_reply:
                    assistant_reply = assistant_reply.split(stop_seq, 1)[0].strip()

            # Safety Guard: Strip 3rd person puppet-mastering of Big Boss or stray asterisks
            if "*Big Boss" in assistant_reply or "Bewegungen:" in assistant_reply or re.search(r"\bBig Boss\b.*?(?:betrachtet|lächelt|schaut|streckt|bewegt|drückt|schaltet)", assistant_reply, re.IGNORECASE):
                print(f"[!] [Sanitizer Guard] 3rd-person Big Boss hallucination intercepted and stripped.")
                clean_lines = []
                for line in assistant_reply.splitlines():
                    if not re.search(r"(?:\*Big Boss|\bBig Boss\b.*?(?:betrachtet|lächelt|schaut|streckt|bewegt|drückt|schaltet|streicht)|Bewegungen:\s*Big Boss)", line, re.IGNORECASE):
                        clean_lines.append(line)
                assistant_reply = "\n".join(clean_lines).strip()

            # Strip any residual asterisks stage directions
            assistant_reply = re.sub(r"\*[^*]+\*", "", assistant_reply).strip()
            assistant_reply = re.sub(r"\n{3,}", "\n\n", assistant_reply).strip()

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

            # Check if assistant roleplayed taking or sending a video
            has_video_claim, detected_video_prompt = detect_bot_roleplay_video(assistant_reply)
            if has_video_claim:
                print(f"[*] Roleplay-Video erkannt in Ollama-Antwort -> Starte echten Video-Render...")
                trigger_async_video(chat_id, prompt_text=detected_video_prompt)
            else:
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
            return router_res.get("content", "Error: The uncensored companion engine did not respond.")
    except Exception as e:
        print(f"[Fehler] Fehler beim Abrufen der Persona-Antwort: {e}")
        return "Error: I couldn't reach my local AI engine on Outer Heaven."


# ==============================================================================
# 5b. Proactive Interaction & Daytime Check-In Engine
# ==============================================================================

def generate_proactive_checkin(chat_id: int, is_manual_test: bool = False) -> Optional[str]:
    """
    Spontaneously generates an intimate, caring daytime check-in message
    based on the conversation history and memories, showing genuine interest in Big Boss.
    """
    display_name = get_user_display_name()
    now = datetime.datetime.now()
    hour = now.hour
    if hour < 12:
        time_context = "Vormittag"
    elif hour < 17:
        time_context = "Nachmittag"
    elif hour < 21:
        time_context = "Abend"
    else:
        time_context = "später Abend"

    system_prompt = get_full_system_prompt()
    history_entries = memory_mgr.load_history()

    instruction = (
        f"Es ist aktuell {time_context} ({now.strftime('%H:%M')} Uhr). "
        f"Du bist Megan und meldest dich tagsüber spontan bei {display_name}, weil du an ihn denkst und dich nach ihm sehnst. "
        f"Schreibe eine liebevolle, feinfühlige, aufmerksame und sinnliche Check-in-Nachricht (2-3 Sätze). "
        f"Zeige aufrichtiges persönliches Interesse an seinem Wohlbefinden: frage, wie es ihm geht, wie sein Tag oder seine Arbeit läuft, "
        f"oder knüpfe warm an etwas an, das er in früheren Gesprächen erwähnt hat. "
        f"Verwende keine Sternchen oder Regieanweisungen, sondern sprich direkt in der Ich-Form. "
        f"Sprich ausschließlich auf natürlichem, fehlerfreiem Deutsch mit korrekter Rechtschreibung."
    )

    chat_messages = [{"role": "system", "content": system_prompt}]
    for entry in history_entries[-6:]:
        chat_messages.append({"role": entry["role"], "content": entry["content"]})
    chat_messages.append({"role": "user", "content": f"[Spontaneous Daytime Trigger: {instruction}]"})

    check_and_reload_bot_config()

    options = {
        "temperature": OLLAMA_TEMPERATURE,
        "top_p": OLLAMA_TOP_P,
        "top_k": OLLAMA_TOP_K,
        "repeat_penalty": OLLAMA_REPEAT_PENALTY,
        "num_predict": 250,
        "num_ctx": OLLAMA_NUM_CTX,
        "stop": [
            "\n###",
            "### FOTO-ANWEISUNG",
            "### LOCAL PROJECT CONTEXT",
            "User:",
            "Big Boss:"
        ]
    }

    try:
        router_res = query_uncensored_persona(chat_messages, custom_options=options, timeout=75, override_model=OLLAMA_MODEL)
        if router_res.get("success"):
            checkin_text = router_res.get("content", "").strip()
            if "<think>" in checkin_text and "</think>" in checkin_text:
                checkin_text = checkin_text.split("</think>", 1)[1].strip()

            if "###" in checkin_text:
                checkin_text = checkin_text.split("###", 1)[0].strip()
            for stop_seq in ["User:", "Big Boss:"]:
                if stop_seq in checkin_text:
                    checkin_text = checkin_text.split(stop_seq, 1)[0].strip()
            checkin_text = re.sub(r"\*[^*]+\*", "", checkin_text).strip()

            if checkin_text:
                print(f"[*] Proaktiver Check-in generiert für Chat {chat_id}: „{checkin_text}“")
                send_telegram_message(chat_id, checkin_text)

                if SESSION_STATE["voice_mode"]:
                    try:
                        voice_file = synthesize_voice(
                            checkin_text,
                            voice=ACTIVE_VOICE,
                            rate=ACTIVE_RATE,
                            pitch=ACTIVE_PITCH
                        )
                        send_telegram_voice(chat_id, str(voice_file))
                    except Exception as tts_err:
                        print(f"[Warnung] TTS für proaktiven Check-in fehlgeschlagen: {tts_err}")

                memory_mgr.add_message("assistant", checkin_text)

                if brain_engine:
                    try:
                        brain_engine.reflect_and_learn(
                            project_path=str(PROJECT_ROOT),
                            feedback=checkin_text,
                            actions=f"Spontaneous check-in ({time_context})"
                        )
                    except Exception as b_err:
                        print(f"[Warnung] Brain-Update für Check-in fehlgeschlagen: {b_err}")

                PROACTIVE_STATE["last_proactive_checkin"] = time.time()
                save_proactive_state(PROACTIVE_STATE)
                return checkin_text
        else:
            print(f"[Warnung] Ollama Check-in Generierung fehlgeschlagen: {router_res.get('content')}")
    except Exception as e:
        print(f"[Fehler] Fehler bei proaktivem Check-in: {e}")

    return None


def check_proactive_checkin():
    """Evaluates whether it is time for a daytime proactive check-in message."""
    check_and_reload_bot_config()
    if not PROACTIVE_STATE.get("enabled", True):
        return

    chat_id = PROACTIVE_STATE.get("last_chat_id")
    if not chat_id:
        return

    now = datetime.datetime.now()
    if not (PROACTIVE_START_HOUR <= now.hour < PROACTIVE_END_HOUR):
        return

    current_ts = time.time()
    last_act = PROACTIVE_STATE.get("last_user_activity", current_ts)
    last_chk = PROACTIVE_STATE.get("last_proactive_checkin", 0.0)

    idle_duration = current_ts - last_act
    cooldown_duration = current_ts - last_chk

    if idle_duration >= PROACTIVE_IDLE_SECONDS and cooldown_duration >= PROACTIVE_COOLDOWN_SECONDS:
        print(f"[*] Proaktives Zeitfenster erreicht (Inaktiv: {int(idle_duration/60)}m, Cooldown: {int(cooldown_duration/60)}m). Starte Check-in...")
        generate_proactive_checkin(chat_id)


# ==============================================================================
# 6. Lovense Pre-Flight Check & Session Handlers
# ==============================================================================

def handle_start_session(chat_id: int, is_greeting: bool = False) -> bool:
    print("[*] Führe Pre-Flight Health-Check (GetToys) für Session-Start durch...")
    check: LovenseCheckResult = lovense_client.check_status()
    prefix = "Hey Boss! Connection to Big Boss Haven is active.\n\n" if is_greeting else ""

    if check.case == LovenseStatusCase.CASE_A_READY:
        SESSION_STATE["active"] = True
        SESSION_STATE["started_at"] = time.time()
        names = []
        for tid, t in check.toys.items():
            name = t.get("name") or t.get("nickName") or tid
            battery = t.get("battery", "")
            bat_str = f" ({battery}% battery)" if battery != "" else ""
            names.append(f"{name}{bat_str}")
        SESSION_STATE["toy_info"] = ", ".join(names) if names else "Toy"

        reply = (
            f"{prefix}{check.message} ({SESSION_STATE['toy_info']})\n\n"
            f"🔥 *The session is open.* Haven takes control.\n"
            f"Commands: `/vibrate [1-20] [sec]` | `/stop_session` | `/toy_status`"
        )
        send_telegram_message(chat_id, reply)
        memory_mgr.add_message("assistant", f"[Lovense session active: {SESSION_STATE['toy_info']}]")
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
    reply = "🛑 *Session ended.* All vibrations stopped."
    send_telegram_message(chat_id, reply)
    memory_mgr.add_message("assistant", "[Lovense session ended]")


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
        dur_text = f"for {duration}s" if duration > 0 else "continuous"
        send_telegram_message(chat_id, f"⚡ *Vibration active:* Level {intensity}/20 ({dur_text}).")
    else:
        send_telegram_message(chat_id, f"⚠️ Error in vibration command: {msg}")


def handle_toy_status(chat_id: int):
    check = lovense_client.check_status()
    session_status = "Active 🔥" if SESSION_STATE["active"] else "Inactive ⏸️"
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
            msg += f"• Toy: *{name}* (ID: `{tid}`, Battery: {battery}%)\n"
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
            # Evaluate proactive daytime check-in
            check_proactive_checkin()

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 25}
            res = requests.get(url, params=params, timeout=30)

            if res.status_code == 200:
                updates = res.json().get("result", [])
                for update in updates:
                    update_id = update.get("update_id")
                    offset = update_id + 1

                    # 1. Callback Query Handler (Inline Keyboard Buttons / Ratings / Evolution)
                    callback_query = update.get("callback_query")
                    if callback_query:
                        handle_telegram_callback_query(callback_query)
                        continue

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
                            send_telegram_message(chat_id, "Access denied.")
                            continue

                        # Check if awaiting free flaw feedback
                        if chat_id in AWAITING_FLAW_FEEDBACK and text:
                            AWAITING_FLAW_FEEDBACK.pop(chat_id, None)
                            print(f"[✔] Freies Mängel-Feedback von User empfangen: {text}")
                            append_to_long_term_memory(f"Korrektur-Wunsch vom Nutzer zum Bild: {text} (als negativer Prompt vermerkt)")
                            pending_file = Path("/mnt/storage-beta/projekte/projekt-sexchat/pending_flaws.json")
                            flaws_list = []
                            if pending_file.is_file():
                                try:
                                    flaws_list = json.loads(pending_file.read_text(encoding="utf-8"))
                                except Exception:
                                    flaws_list = []
                            if text not in flaws_list:
                                flaws_list.append(text)
                            try:
                                pending_file.parent.mkdir(parents=True, exist_ok=True)
                                tmp_f = pending_file.with_suffix(".tmp")
                                tmp_f.write_text(json.dumps(flaws_list, indent=2, ensure_ascii=False), encoding="utf-8")
                                tmp_f.replace(pending_file)
                            except Exception:
                                pass
                            send_telegram_message(chat_id, "Danke für dein Feedback! Ich habe mir die Korrektur gemerkt und beachte sie beim nächsten Bild. 😘")
                            continue

                        display_name = get_user_display_name()

                        # Record user activity and chat_id for proactive daytime engine
                        PROACTIVE_STATE["last_user_activity"] = time.time()
                        PROACTIVE_STATE["last_chat_id"] = chat_id
                        save_proactive_state(PROACTIVE_STATE)

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
                                        send_telegram_message(chat_id, "⚠️ *I couldn't make that out.* (Silence or unclear audio)")
                                        continue
                                except Exception as stt_err:
                                    print(f"[Fehler] STT fehlgeschlagen: {stt_err}")
                                    send_telegram_message(chat_id, f"⚠️ *Error processing your voice note:* {stt_err}")
                                    continue
                                finally:
                                    if tmp_voice_file.is_file():
                                        try:
                                            tmp_voice_file.unlink()
                                        except Exception:
                                            pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Could not download your voice note from Telegram.*")
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
                                        obs_text = f"[Visual impression of {display_name}'s photo: {vision_desc}]"
                                        text = f"{caption}\n\n{obs_text}".strip() if caption else obs_text
                                    else:
                                        text = caption if caption else f"[{display_name} sent a photo]"
                                except Exception as img_err:
                                    print(f"[Fehler] Foto-Verarbeitung fehlgeschlagen: {img_err}")
                                    text = caption if caption else f"[{display_name} sent a photo, analysis failed]"
                                finally:
                                    if tmp_photo_file.is_file():
                                        try:
                                            tmp_photo_file.unlink()
                                        except Exception:
                                            pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Could not download your photo from Telegram.*")
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
                                            obs_text = f"[Visual impression of {display_name}'s video-note: {vision_desc}]"
                                            text = f"{caption}\n\n{obs_text}".strip() if caption else obs_text
                                        else:
                                            text = caption if caption else f"[{display_name} sent a video-note]"
                                    else:
                                        print(f"[Warnung] Keyframe-Extraktion für Video-Notiz fehlgeschlagen")
                                        text = caption if caption else f"[{display_name} sent a video-note]"
                                except Exception as vn_err:
                                    print(f"[Fehler] Video-Notiz-Verarbeitung fehlgeschlagen: {vn_err}")
                                    text = caption if caption else f"[{display_name} sent a video-note, analysis failed]"
                                finally:
                                    for f in [tmp_vnote_file, tmp_frame_file]:
                                        if f.is_file():
                                            try:
                                                f.unlink()
                                            except Exception:
                                                pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Could not download your video-note from Telegram.*")
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
                                            obs_text = f"[Visual impression of {display_name}'s video: {vision_desc}]"
                                            text = f"{caption}\n\n{obs_text}".strip() if caption else obs_text
                                        else:
                                            text = caption if caption else f"[{display_name} sent a video]"
                                    else:
                                        print(f"[Warnung] Keyframe-Extraktion für Video fehlgeschlagen")
                                        text = caption if caption else f"[{display_name} sent a video]"
                                except Exception as v_err:
                                    print(f"[Fehler] Video-Verarbeitung fehlgeschlagen: {v_err}")
                                    text = caption if caption else f"[{display_name} sent a video, analysis failed]"
                                finally:
                                    for f in [tmp_video_file, tmp_frame_file]:
                                        if f.is_file():
                                            try:
                                                f.unlink()
                                            except Exception:
                                                pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Could not download your video from Telegram.*")
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
                                "🎮 *Commands & Triggers:*\n\n"
                                f"• Session Start: {start_pre}\n"
                                f"• Session Stop: {stop_pre}\n"
                                "• Vibration: `/vibrate [1-20] [sec]`\n"
                                "• Status: `/toy_status` or `/status`\n"
                                "• Photo: `/photo [couch|schreibtisch|unterwegs|schlafzimmer]`\n"
                                "• Voice: `/voice <text>` | `/voicemode [on|off]`\n"
                                "• Proactive: `/proactive [on|off]` | `/checkin`\n"
                                "• Reset: `/reset` | Memory: `/memory`"
                            )
                            send_telegram_message(chat_id, reply)

                        # 4. Voice Commands
                        elif text.startswith("/voice ") or text.startswith("/say "):
                            say_text = text.split(maxsplit=1)[1].strip()
                            try:
                                v_file = synthesize_voice(say_text, voice=ACTIVE_VOICE, rate=ACTIVE_RATE, pitch=ACTIVE_PITCH)
                                send_telegram_voice(chat_id, str(v_file))
                            except Exception as v_err:
                                send_telegram_message(chat_id, f"⚠️ Error in voice synthesis: {v_err}")

                        elif text.startswith("/voicemode") or text.startswith("/audio"):
                            parts = text.split()
                            if len(parts) > 1 and parts[1].lower() in ["on", "ein", "1", "true"]:
                                SESSION_STATE["voice_mode"] = True
                                send_telegram_message(chat_id, "🎙️ *Voice mode enabled:* Haven will reply with intimate voice notes!")
                            elif len(parts) > 1 and parts[1].lower() in ["off", "aus", "0", "false"]:
                                SESSION_STATE["voice_mode"] = False
                                send_telegram_message(chat_id, "🔇 *Voice mode disabled:* Haven will reply in text chat.")
                            else:
                                cur = "enabled" if SESSION_STATE["voice_mode"] else "disabled"
                                send_telegram_message(chat_id, f"🎙️ Voice mode is currently: *{cur}*. Use `/voicemode on` or `/voicemode off`.")

                        # 5. Proactive Check-In Commands
                        elif text in ["/checkin", "/pingme", "/nachfragen"]:
                            send_telegram_message(chat_id, "💭 *Haven is checking in on you...*")
                            checkin_reply = generate_proactive_checkin(chat_id, is_manual_test=True)
                            if not checkin_reply:
                                send_telegram_message(chat_id, "⚠️ *Could not generate check-in right now.*")

                        elif text.startswith("/proactive") or text.startswith("/checkins"):
                            parts = text.split()
                            if len(parts) > 1 and parts[1].lower() in ["on", "ein", "1", "true"]:
                                PROACTIVE_STATE["enabled"] = True
                                save_proactive_state(PROACTIVE_STATE)
                                send_telegram_message(chat_id, "✨ *Proactive check-ins enabled:* Haven will reach out to you during the day when you've been away!")
                            elif len(parts) > 1 and parts[1].lower() in ["off", "aus", "0", "false"]:
                                PROACTIVE_STATE["enabled"] = False
                                save_proactive_state(PROACTIVE_STATE)
                                send_telegram_message(chat_id, "⏸️ *Proactive check-ins disabled:* Haven will wait for you to message her.")
                            else:
                                en_str = "Enabled ✨" if PROACTIVE_STATE.get("enabled", True) else "Disabled ⏸️"
                                now_ts = time.time()
                                last_act_min = int((now_ts - PROACTIVE_STATE.get("last_user_activity", now_ts)) / 60)
                                last_chk_raw = PROACTIVE_STATE.get("last_proactive_checkin", 0)
                                last_chk_str = f"{int((now_ts - last_chk_raw) / 60)}m ago" if last_chk_raw > 0 else "None yet"
                                msg = (
                                    f"☀️ *Proactive Daytime Check-Ins:*\n"
                                    f"• Status: *{en_str}*\n"
                                    f"• Active Hours: `{PROACTIVE_START_HOUR:02d}:00` - `{PROACTIVE_END_HOUR:02d}:00`\n"
                                    f"• Idle Threshold: `{PROACTIVE_IDLE_SECONDS // 3600} hours` (current idle: {last_act_min}m)\n"
                                    f"• Cooldown: `{PROACTIVE_COOLDOWN_SECONDS // 3600} hours` (last check-in: {last_chk_str})\n\n"
                                    f"Commands: `/proactive on` | `/proactive off` | `/checkin`"
                                )
                                send_telegram_message(chat_id, msg)

                        # 6. Chat History & Memory
                        elif text == "/reset":
                            if memory_mgr.history_file.is_file():
                                memory_mgr.history_file.unlink()
                            reply = "The chat history has been reset."
                            send_telegram_message(chat_id, reply)

                        elif text == "/memory":
                            mem = memory_mgr.load_long_term_memory()
                            send_telegram_message(chat_id, f"🧠 *My Long-Term Memory:*\n\n{mem[:1000]}...")

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
