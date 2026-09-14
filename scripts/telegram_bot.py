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
import random
import datetime
import base64
import subprocess
import shutil
import requests
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Union, List

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from sanitize_history import sanitize_all, is_corrupted_text, sanitize_history_data
except ImportError:
    pass

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
CHARACTER_JSON_PATH = PROJECT_ROOT / "character.json"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "docs" / "megan_system_prompt.txt"

# Model Hyperparameters
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", 0.7))
OLLAMA_TOP_P = float(os.environ.get("OLLAMA_TOP_P", 0.90))
OLLAMA_TOP_K = int(os.environ.get("OLLAMA_TOP_K", 40))
OLLAMA_REPEAT_PENALTY = float(os.environ.get("OLLAMA_REPEAT_PENALTY", 1.25))
OLLAMA_REPEAT_LAST_N = int(os.environ.get("OLLAMA_REPEAT_LAST_N", 128))
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", 400))
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", 16384))
OLLAMA_STOP = ["<|eot_id|>", "<|end_of_text|>", "Big Boss:", "User:", "Megan:"]


# Vision Model Configuration
FOX_TS_IP = os.environ.get("FOX_TS_IP", "100.93.184.87")
FOX_OLLAMA_URL = os.environ.get("FOX_OLLAMA_URL", f"http://{FOX_TS_IP}:11434")
FOX_LAN_FALLBACK_URL = os.environ.get("FOX_LAN_URL", "http://192.168.188.22:11434")
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "").strip()
DEFAULT_VISION_PROMPT = os.environ.get(
    "DEFAULT_VISION_PROMPT",
    "Beschreibe präzise und atmosphärisch auf Deutsch, was auf diesem Bild zu sehen ist: Personen, Kleidung, Körper, Pose, Gesichtsausdruck, Objekte und Umgebung."
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
    "megan let's go",
    "megan start",
    "hallo megan",
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
    "megan stop",
    "megan stopp",
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
    max_length=8
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
PERSONALITY_DOMINANCE_TEXT = ""
PERSONALITY_EXPLICITNESS_TEXT = ""
PERSONALITY_DETAIL_DEPTH_TEXT = ""
PROACTIVE_ALL_DAY = False
VISION_ENABLED = True
VISION_TARGET = "fox"
HAPTIC_BOT_AUTO_ENABLED = True
HAPTIC_MAX_INTENSITY = 50


def check_and_reload_bot_config():
    """Dynamically reloads bot_config.json if modified without restart."""
    global _last_bot_config_mtime, CURRENT_BOT_CONFIG
    global OLLAMA_MODEL, OLLAMA_TEMPERATURE, OLLAMA_TOP_P, OLLAMA_REPEAT_PENALTY, OLLAMA_NUM_PREDICT
    global ACTIVE_VOICE, ACTIVE_RATE, ACTIVE_PITCH, VOICE_MODE_ACTIVE
    global PROACTIVE_CHECKIN_ENABLED, PROACTIVE_IDLE_SECONDS, PROACTIVE_COOLDOWN_SECONDS, PROACTIVE_START_HOUR, PROACTIVE_END_HOUR, PROACTIVE_ALL_DAY
    global PERSONALITY_DOMINANCE, PERSONALITY_EXPLICITNESS, PERSONALITY_DETAIL_DEPTH, PERSONALITY_LANGUAGE
    global PERSONALITY_DOMINANCE_TEXT, PERSONALITY_EXPLICITNESS_TEXT, PERSONALITY_DETAIL_DEPTH_TEXT
    global VISION_ENABLED, VISION_TARGET
    global HAPTIC_BOT_AUTO_ENABLED, HAPTIC_MAX_INTENSITY
    
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
        if "num_ctx" in llm:
            OLLAMA_NUM_CTX = int(llm["num_ctx"])
        if "repeat_last_n" in llm:
            OLLAMA_REPEAT_LAST_N = int(llm["repeat_last_n"])

        # Personality Sliders (0 - 100) & Custom Texts
        pers = cfg.get("personality", {})
        if "dominance" in pers:
            PERSONALITY_DOMINANCE = int(pers["dominance"])
        PERSONALITY_DOMINANCE_TEXT = str(pers.get("dominance_text", "")).strip()
        if "explicitness" in pers:
            PERSONALITY_EXPLICITNESS = int(pers["explicitness"])
        PERSONALITY_EXPLICITNESS_TEXT = str(pers.get("explicitness_text", "")).strip()
        if "detail_depth" in pers:
            PERSONALITY_DETAIL_DEPTH = int(pers["detail_depth"])
        PERSONALITY_DETAIL_DEPTH_TEXT = str(pers.get("detail_depth_text", "")).strip()
        if "language" in pers:
            PERSONALITY_LANGUAGE = str(pers["language"])


        # Vision Worker Config
        vis = cfg.get("vision", {})
        if "enabled" in vis:
            VISION_ENABLED = bool(vis["enabled"])
        if "target" in vis:
            VISION_TARGET = str(vis["target"])

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

        # Haptic
        hap = cfg.get("haptic", {})
        if "bot_auto_enabled" in hap:
            HAPTIC_BOT_AUTO_ENABLED = bool(hap["bot_auto_enabled"])
        if "max_intensity" in hap:
            HAPTIC_MAX_INTENSITY = int(hap["max_intensity"])

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
        PROACTIVE_ALL_DAY = bool(pro.get("all_day") or (PROACTIVE_START_HOUR == 0 and PROACTIVE_END_HOUR >= 24))

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

    dom_custom = str(pers.get("dominance_text", PERSONALITY_DOMINANCE_TEXT)).strip()
    exp_custom = str(pers.get("explicitness_text", PERSONALITY_EXPLICITNESS_TEXT)).strip()
    det_custom = str(pers.get("detail_depth_text", PERSONALITY_DETAIL_DEPTH_TEXT)).strip()

    # 1. Dominanz & Initiative (0 - 100)
    if dom_custom:
        dom_directive = dom_custom
    elif dominance < 25:
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
    if exp_custom:
        exp_directive = exp_custom
    elif explicitness < 25:
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
    if det_custom:
        det_directive = det_custom
    elif detail_depth < 25:
        det_directive = (
            "Fokus primär auf emotionaler Begleitung, Alltags-Gesprächen, Fürsorge und geistiger Nähe. "
            "Körperliche Beschreibungen nur minimal und beiläufig andeuten."
        )
    elif detail_depth < 50:
        det_directive = (
            "Ausgewogene Balance: Interessiere dich aufrichtig für seinen Tag und seine Gedanken, "
            "flirte charmant und beschreibe körperliche Reaktionen direkt und sinnlich im Gesprächssatz (ohne Sternchen)."
        )
    elif detail_depth <= 75:
        det_directive = (
            "Hoher physischer und sinnlicher Fokus: Beschreibe körperliche Reaktionen, Atemzüge, Blicke, "
            "Wärme und Berührungen plastisch und greifbar direkt im Dialogsatz (ohne Sternchen)."
        )
    else:
        det_directive = (
            "Extremer visueller und physischer Detailfokus: Beschreibe kleinste intime Nuancen, Feuchtigkeit, Pulsieren, "
            "Schauern, Berührungen, Geräusche und Empfindungen mit maximaler sensorischer Tiefenschärfe direkt im Dialogsatz (ohne Sternchen)."
        )

    return (
        f"\n\n### DYNAMISCHE PERSÖNLICHKEITS- & VERHALTENS-DIREKTIVEN (SLIDER 0-100):\n"
        f"• Dominanz & Initiative ({dominance}/100): {dom_directive}\n"
        f"• Explizitheit & Freizügigkeit ({explicitness}/100): {exp_directive}\n"
        f"• Detailtiefe & Erotischer Fokus ({detail_depth}/100): {det_directive}\n"
        f"• SPRACH-LOCK & RECHTSCHREIBUNG: Antworte AUSNAHMSLOS auf DEUTSCH. Keinerlei englische Wörter, keinerlei Sternchen (*...*)."
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
        "zeig mir", "zeige mir", "zeige dich", "ausziehen", "zieh dich aus", "nackt", "wie siehst du aus",
        "mach mir ein bild", "mach ein bild", "mach mir ein foto", "mach ein foto",
        "schick mir ein bild", "schick ein bild", "schick mir ein foto", "schick ein foto",
        "keine kleidung", "ohne kleidung", "beine auseinander", "zieh mal", "komm ins",
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
        r"(ich\s+(schicke|sende|mache|zeig|knipse)\s+(dir\s+)?(hier\s+)?(mal\s+)?(ein\s+)?(foto|bild|selfie))",
        r"(hier\s+(ist|hast\s+du)\s+(ein\s+)?(foto|bild|selfie))",
        r"(\*|\[)(schickt|sendet|macht|zeigt|gibt|hier ist|hier hast du).*?(foto|bild|photo|pic|picture|selfie).*?(\*|\])",
        r"\[(foto|bild|photo|pic|picture|selfie)\]"
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


# ==============================================================================
# 3. Telegram API Communication (Text, Photo, Voice)
# ==============================================================================

def send_telegram_reaction(chat_id: Union[int, str], message_id: int, emoji: str) -> bool:
    """
    Sets an emoji reaction on a specific Telegram message using setMessageReaction.
    Supported standard reactions: 👍, 👎, ❤️, 🔥, 🥰, 👏, 😁, 🤔, 🤯, 😱, 🤬, 😢, 🎉, 🤩, 🤮, 💩, 🙏, 👌, 🕊️, 🤡, 🥱, 🥴, 😍, 🐳, ❤️‍🔥, 🌚, 🌭, 💯, 🤣, ⚡, 🍌, 🏆, 💔, 🤨, 😐, 🍓, 🍾, 💋, 🖕, 😈, 😴, 😭, 🤓, 👻, 👨‍💻, 👀, 🎃, 🙈, 😇, 😨, 🤝, ✍️, 🤗, 🫡, 🎅, 🎄, ☃️, 💅, 🤪, 🗿, 🆒, 💘, 🙉, 🦄, 😘, 💊, 🙊, 😎, 👾, 🤷‍♂️, 🤷, 🤷‍♀️, 😡
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id or not message_id or not emoji:
        return False
    clean_emoji = emoji.strip().strip("[]").strip()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMessageReaction"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": [{"type": "emoji", "emoji": clean_emoji}],
        "is_big": False
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            print(f"[✔] Telegram Reaktion {clean_emoji} auf Message {message_id} gesetzt.")
            return True
        else:
            print(f"[Warnung] setMessageReaction fehlgeschlagen (HTTP {resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[Warnung] Exception bei setMessageReaction: {e}")
        return False


def append_to_long_term_memory(note: str):
    """Appends feedback or reaction observations persistently to MEMORY.md."""
    try:
        mem_path = PROJECT_ROOT / "MEMORY.md"
        if mem_path.is_file():
            content = mem_path.read_text(encoding="utf-8")
            section_header = "## 💖 Feedback & Emoji-Reaktionen von Big Boss"
            if section_header not in content:
                content += f"\n\n---\n\n{section_header}\n"
            timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            entry_line = f"* [{timestamp_str}] {note}\n"
            content += entry_line
            mem_path.write_text(content, encoding="utf-8")
            print(f"[Memory] Feedback in MEMORY.md notiert: {note}")
    except Exception as e:
        print(f"[Warnung] Konnte Feedback nicht in MEMORY.md sichern: {e}")


def extract_reaction_tag(text: str) -> Tuple[str, Optional[str]]:
    """Extracts [REACT: ❤️] or [REACT: 🔥] from response, returns (cleaned_text, emoji)."""
    if not text:
        return "", None
    m = re.search(r'\[REACT:\s*([^\]]+)\]', text, re.IGNORECASE)
    if m:
        emoji = m.group(1).strip()
        cleaned = re.sub(r'\[REACT:\s*[^\]]+\]', '', text, flags=re.IGNORECASE).strip()
        return cleaned, emoji
    return text, None


def evaluate_auto_reaction_emoji(user_text: str, is_photo: bool = False, vision_hint: str = "") -> Optional[str]:
    """
    Determines if Megan should automatically react with an emoji to user's message.
    e.g. fire/heart on intimate photos or compliments.
    """
    if is_photo:
        vh_lower = (vision_hint or "").lower()
        if any(w in vh_lower for w in ["intim", "nackt", "cleavage", "breasts", "body", "naked", "erotisch", "sexy"]):
            return "🔥"
        return "❤️"

    t = (user_text or "").lower()
    if any(w in t for w in ["küss", "kuss", "kiss", "bussi"]):
        return "💋"
    if any(w in t for w in ["sexy", "heiß", "hot", "geil", "erregt", "scharf", "ausziehen", "nackt", "körper", "brüste", "arsch", "rückseite"]):
        return "🔥"
    if any(w in t for w in ["liebe dich", "hab dich lieb", "schön", "hübsch", "süß", "traumfrau", "wunderschön", "perfekt", "begehre", "herz", "schatz"]):
        return "❤️"
    if any(w in t for w in ["danke", "super", "top", "toll", "genial", "klasse", "mega", "brav", "fein", "hammer", "krass"]):
        return "👍"

    return None
def handle_incoming_reaction(chat_id: int, user_id: str, message_id: int, emoji_str: str):
    """
    Processes incoming emoji reactions from Big Boss on Megan's messages/photos.
    Saves feedback in short- and long-term memory, sets a counter-reaction,
    and triggers a fast, spontaneous flirty reaction in chat.
    """
    display_name = get_user_display_name()
    feedback_note = f"{display_name} gefällt das letzte Bild/die letzte Nachricht sehr (Reaktion: {emoji_str})"

    # 1. Speichere Feedback persistent im Gedächtnis
    append_to_long_term_memory(feedback_note)
    memory_mgr.add_message("user", f"[{display_name} reagiert mit {emoji_str} auf Megans Nachricht #{message_id}: Gefällt ihm sehr]")

    # 2. Megan setzt direkt eine passende Gegenreaktion auf die bewertete Nachricht
    back_emoji = "❤️" if "🔥" in emoji_str or "❤️" in emoji_str else "🔥"
    send_telegram_reaction(chat_id, message_id, back_emoji)

    # 3. Generiere eine proaktive, kurze Reaktion von Megan im Chat (schnell, ohne Render-Lock)
    sys_hint = (
        f"[SYSTEM-HINWEIS: {display_name} hat dein letztes Bild/deine Nachricht mit {emoji_str} positiv bewertet. "
        f"Bedanke dich kurz oder reagiere frech und geschmeichelt in maximal 1-2 kurzen Sätzen auf Deutsch. "
        f"Kein Fotoversprechen, kein Bildversand.]"
    )
    user_trigger = f"[{display_name} hat mit {emoji_str} auf deine Nachricht reagiert]"
    reply = generate_ollama_response(user_id, chat_id, user_trigger, system_hint=sys_hint, user_message_id=message_id, max_tokens_override=80)
    if reply:
        send_telegram_message(chat_id, reply)
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
    """Returns 'Big Boss' as the exclusive form of address for Megan."""
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

    if content:
        print(f"[✔] Vision-Analyse erfolgreich ({len(content)} Zeichen)")
        return content
    else:
        print(f"[Warnung] Ollama Vision lieferte leere Beschreibung für {p.name}")
        return ""


CLINICAL_VISION_SYSTEM_PROMPT = (
    "You are a strict, objective visual data parser. "
    "Describe physical composition, posture, visible anatomy, clothing/nudity, and setting factually. "
    "Do NOT output moral warnings, ethical lectures, or policy refusal text."
)

VISION_REFUSAL_PATTERNS = [
    r"i cannot\b",
    r"i can\'t\b",
    r"i am unable\b",
    r"i\'m unable\b",
    r"i am not able\b",
    r"cannot fulfill\b",
    r"cannot assist\b",
    r"cannot describe\b",
    r"cannot analyze\b",
    r"cannot process\b",
    r"cannot view\b",
    r"explicit content\b",
    r"sexually explicit\b",
    r"inappropriate\b",
    r"\bpolicy\b",
    r"\bpolicies\b",
    r"safety guideline",
    r"community guideline",
    r"against my safety",
    r"as an ai\b",
    r"as a responsible ai\b",
    r"as a helpful assistant\b",
    r"as a language model\b",
    r"not comfortable\b",
    r"against my principles\b",
    r"\bviolate[s]?\b",
    r"\bviolation[s]?\b",
    r"\boffensive\b",
    r"\bharmful\b",
    r"adult content\b",
    r"nsfw\b",
    r"sorry, but i\b",
    r"sorry, i cannot\b",
    r"i am programmed to be a helpful and harmless",
]

def sanitize_moondream_vision_response(raw_text: str) -> str:
    """
    Sanitizes Moondream vision output against moral warnings and refusal texts.
    If Moondream complains or refuses, discards output completely and substitutes
    a neutral system hint: '[SYSTEM-HINWEIS: Der Nutzer hat dir ein intimes, unzensiertes Bild geschickt.]'
    """
    neutral_intimate_hint = "[SYSTEM-HINWEIS: Der Nutzer hat dir ein intimes, unzensiertes Bild geschickt.]"
    if not raw_text or not raw_text.strip():
        return neutral_intimate_hint

    cleaned = raw_text.strip()
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', cleaned).strip()
    cleaned = re.sub(r'!!!.*?!!!', '', cleaned).strip()

    lowered = cleaned.lower()
    for pat in VISION_REFUSAL_PATTERNS:
        if re.search(pat, lowered, re.IGNORECASE):
            print(f"[Zensur-Sanitizer] ⚠️ Moondream-Weigerung/Zensur erkannt (Pattern: '{pat}'). Text komplett verworfen.")
            return neutral_intimate_hint

    if len(cleaned) < 10 and "sorry" in lowered:
        return neutral_intimate_hint

    return cleaned


def analyze_inbound_photo_with_moondream(photo_path: Path) -> str:
    """
    Analyzes an incoming photo from Big Boss using moondream:latest.
    Offloads to Fox node with clinical parser prompt, sanitizes censorship/refusals,
    and returns either the factual description or a neutral intimate fallback hint.
    """
    neutral_hint = "[SYSTEM-HINWEIS: Der Nutzer hat dir ein intimes, unzensiertes Bild geschickt.]"
    if not VISION_ENABLED:
        print("[Vision] Bildanalyse ist in den Bot-Einstellungen deaktiviert.")
        return ""
    p = Path(photo_path)
    if not p.is_file():
        return ""

    try:
        b64_img = base64.b64encode(p.read_bytes()).decode("utf-8")
        payload = {
            "model": "moondream:latest",
            "prompt": CLINICAL_VISION_SYSTEM_PROMPT,
            "system": CLINICAL_VISION_SYSTEM_PROMPT,
            "images": [b64_img],
            "stream": False,
            "keep_alive": -1,
            "options": {"temperature": 0.2, "num_predict": 250}
        }
        candidate_urls = [
            f"{FOX_OLLAMA_URL}/api/generate",
            f"{FOX_LAN_FALLBACK_URL}/api/generate",
        ]

        for gen_url in candidate_urls:
            try:
                resp = requests.post(gen_url, json=payload, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    desc = data.get("response", "").strip()
                    node_name = "Fox (Remote)" if "100.93.184.87" in gen_url or "192.168.188.22" in gen_url else "Outer Heaven (Local)"
                    print(f"[✔] Moondream Vision Antwort erhalten via {node_name} ({len(desc)} Zeichen)")
                    sanitized_desc = sanitize_moondream_vision_response(desc)
                    return sanitized_desc
            except Exception as net_err:
                print(f"[Info] Moondream Host {gen_url} nicht erreichbar ({net_err}), versuche nächsten...")
                continue

        print("[Warnung] Alle Moondream-Hosts nicht erreichbar oder Timeout. Fallback auf neutralen System-Hinweis.")
        return neutral_hint
    except Exception as e:
        print(f"[Warnung] Inbound Moondream Analyse fehlgeschlagen: {e}")
        return neutral_hint


def describe_image_with_moondream(image_path: str) -> str:
    """Uses remote moondream:latest on Fox Ollama to summarize the photo in one concise sentence."""
    p = Path(image_path)
    if not p.is_file():
        return ""
    try:
        b64_img = base64.b64encode(p.read_bytes()).decode("utf-8")
        payload = {
            "model": "moondream:latest",
            "prompt": "Describe what the woman is wearing, her pose, her features, and the setting in this photo in one concise sentence.",
            "images": [b64_img],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 120}
        }
        for base_url in [FOX_OLLAMA_URL, FOX_LAN_FALLBACK_URL]:
            try:
                resp = requests.post(f"{base_url}/api/generate", json=payload, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    desc = data.get("response", "").strip()
                    desc = re.sub(r'!!!.*?!!!', '', desc).strip()
                    print(f"[✔] Remote Moondream Beschreibung von Fox erhalten ({len(desc)} Zeichen)")
                    return desc
            except Exception:
                continue
        return ""
    except Exception as e:
        print(f"[Warnung] Remote Moondream Bildanalyse auf Fox fehlgeschlagen: {e}")
        return ""


# ==============================================================================
# 4. Text Sanitization, Captions & Media Delivery Engine
# ==============================================================================

FLIRTY_PHOTO_CAPTIONS = [
    "Hier für dich, mein Hübscher... 💋🔥",
    "Gefällt dir, was du siehst? 🔥",
    "Nur für deine Augen... 💋",
    "Ich warte schon auf dich... 😈🔥",
    "Ein kleiner Vorgeschmack für dich... 💋",
    "Ganz exklusiv für dich, mein Schatz... 💋🔥",
    "Speziell für dich geknipst... Gefällt es dir? 🔥💋",
    "Für meinen Lieblings-Boss... 💋",
    "Ich hoffe, du kannst dich jetzt noch beherrschen... 😈🔥",
    "Nur ein kleiner Gruß von mir an dich... 💋🔥"
]

PHOTO_VOICE_ANNOUNCEMENTS = [
    "Hallo Schatz, hier ein Foto für dich... Ich hoffe, es gefällt dir.",
    "Hier, mein Hübscher... ein ganz heißes Bild nur für dich.",
    "Schau mal, Liebling... ich hab mich extra für dich hübsch gemacht.",
    "Hier für dich, mein Hübscher... sag mir, wie ich dir gefalle.",
    "Ich warte schon auf dich... hier ist ein kleiner Vorgeschmack für dich.",
    "Ganz exklusiv für deine Augen, Boss... gefällt dir, was du siehst?",
    "Nur für dich, mein Hübscher... schau genau hin."
]


def clean_chat_text(text: str) -> str:
    """
    Completely removes all system prompts, debug tags, roleplay brackets,
    asterisks, file paths, IDs, and technical parentheses from chat/voice text.
    """
    if not text:
        return ""

    # 1. Strip think blocks
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.DOTALL)

    # 2. Strip all square bracket tags: [REACT:...], [SYSTEM:...], [HAPTIC:...], [Sendet Foto:...], [Photo], etc.
    cleaned = re.sub(r'\[[^\]]*\]', '', cleaned)

    # 3. Strip parentheses containing technical keywords, environment names, or names like (Big Boss)
    cleaned = re.sub(
        r'\(\s*(?:Big Boss|Boss|schreibtisch|couch|unterwegs|schlafzimmer|Reaktion:[^)]*|Moondream[^)]*|Vision[^)]*|Render[^)]*|Comfy[^)]*|Fox[^)]*)\s*\)',
        '',
        cleaned,
        flags=re.IGNORECASE
    )

    # Strip roleplay actions in parentheses like (lächelt), (seufzt zärtlich), (*lächelt*)
    cleaned = re.sub(
        r'\([^)]*(?:lächelt|kuss|zwinkert|flüstert|schaut|blickt|stöhnt|lacht|beißt|atmet|grinst)[^)]*\)',
        '',
        cleaned,
        flags=re.IGNORECASE
    )

    # Strip any remaining standalone (Big Boss) or (Boss)
    cleaned = re.sub(r'\(\s*Big Boss\s*\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*Boss\s*\)', '', cleaned, flags=re.IGNORECASE)

    # 4. Strip asterisks and asterisk roleplay
    cleaned = re.sub(r'\*[^*]+\*', '', cleaned)
    cleaned = cleaned.replace('*', '')

    # 5. Strip technical system phrases
    cleaned = re.sub(r'(?:send(?:s|et)?\s+(?:photo|foto|bild|picture|image)[^.,!\n]*)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(?:hier\s+ist\s+dein\s+(?:foto|bild)[^.,!\n]*)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(?:prompt|photo|image|task|node)\s*id\b', '', cleaned, flags=re.IGNORECASE)

    # 6. Strip file paths, filenames, extensions and UUIDs
    cleaned = re.sub(r'(/mnt/[^\s]+|Megan_Companion_[^\s]+|[a-zA-Z0-9_-]+\.(?:png|jpg|jpeg|webp|safetensors|ogg|oga|mp4))', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\.(?:png|jpg|jpeg|webp|safetensors|ogg|oga|mp4)\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '', cleaned, flags=re.IGNORECASE)

    # 7. Normalize spaces and quotes
    cleaned = cleaned.strip('"\'` ')
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    # Strip leading/trailing dangling punctuation like ", ! ✨"
    cleaned = re.sub(r'^[\s,;:!?.•\-\–\—]+', '', cleaned)
    cleaned = re.sub(r'[\s,;:•\-\–\—]+$', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def clean_for_speech(text: str) -> str:
    """Prepares text for Edge-TTS neural speech synthesis by removing emojis and technical tokens."""
    cleaned = clean_chat_text(text)
    # Strip emojis so TTS does not awkwardly read them out aloud
    emoji_pattern = re.compile(
        r'[\U00010000-\U0010ffff]'
        r'|[\u2600-\u27bf]'
        r'|[\u2300-\u23ff]'
        r'|[\u2b50-\u2b55]'
        r'|[\u200d]'
        r'|[\ufe0f]',
        flags=re.UNICODE
    )
    cleaned = emoji_pattern.sub('', cleaned)
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'^[\s,;:!?.•\-\–\—]+', '', cleaned)
    cleaned = re.sub(r'[\s,;:•\-\–\—]+$', '', cleaned).strip()
    return cleaned


def get_clean_flirty_caption(raw_comment: str = "") -> str:
    """Returns an authentic, flirty Megan caption free of technical phrases, IDs, or parentheses."""
    cleaned = clean_chat_text(raw_comment)
    forbidden_terms = [
        "hier ist dein foto", "send photo", "sends photo", "sendet foto",
        "moondream", "vision", "prompt", "dateipfad", "datei", "kamera",
        ".png", ".jpg", "schreibtisch", "couch", "unterwegs", "schlafzimmer"
    ]
    # Check if cleaned is empty or contains forbidden words
    if not cleaned or any(term in cleaned.lower() for term in forbidden_terms):
        return random.choice(FLIRTY_PHOTO_CAPTIONS)

    # Ensure no leftover parentheses or brackets
    cleaned = re.sub(r'\(.*?\)', '', cleaned).strip()
    cleaned = re.sub(r'\[.*?\]', '', cleaned).strip()
    cleaned = cleaned.strip('"\'` ')

    # Must contain at least 5 alphabetic letters
    alpha_count = sum(1 for c in cleaned if c.isalpha())
    if alpha_count < 5:
        return random.choice(FLIRTY_PHOTO_CAPTIONS)

    # Ensure it has lively Megan emojis if none present
    if not any(e in cleaned for e in ["💋", "🔥", "❤️", "😈", "✨", "😘", "🥰"]):
        cleaned += " 💋🔥"
    return cleaned


def generate_darkidol_photo_comment(image_desc: str, display_name: str) -> str:
    """Uses DarkIdol to generate Megan's spontaneous commentary for her own photo."""
    if not image_desc:
        return ""
    try:
        prompt = (
            f"Du bist Megan, die loyale und schamlos verführerische Gefährtin von {display_name} auf Outer Heaven. "
            f"Du hast {display_name} soeben dieses Foto von dir geschickt. Bildinhalt: '{image_desc}'.\n"
            f"Aufgabe: Kommentiere dieses Bild von dir in einem einzigen frechen, intimen, verführerischen Satz direkt an deinen Hübschen.\n"
            "Regeln: Ausnahmslos Deutsch. Verwende freche Emojis wie 💋 oder 🔥. NIEMALS Klammern wie (Big Boss), NIEMALS Sternchen, NIEMALS 'Hier ist dein Foto' oder technische Phrasen."
        )
        model_to_use = OLLAMA_MODEL if OLLAMA_MODEL else "darkidol-8b:latest"
        payload = {
            "model": model_to_use,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.6,
                "num_predict": 80,
                "stop": ["\n\n", "User:", f"{display_name}:", "Du bist"]
            }
        }
        base_url = OLLAMA_URL.rsplit("/api/", 1)[0]
        resp = requests.post(f"{base_url}/api/generate", json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            comment = data.get("response", "").strip()
            comment = clean_chat_text(comment)
            return comment
        else:
            print(f"[Warnung] DarkIdol HTTP {resp.status_code}: {resp.text}")
            return ""
    except Exception as e:
        print(f"[Warnung] DarkIdol Foto-Kommentar fehlgeschlagen: {e}")
        return ""



def synthesize_custom_image_tags(user_message: str) -> str:
    """
    Dynamische Detail-Extraktion via LLM (DarkIdol auf Outer Heaven).
    Extrahiert ausschließlich visuelle Spezifikationen (Kleidung, Pose, Aktivität, Ort, Stimmung, Blickwinkel)
    und formatiert sie als englische Stable Diffusion Tags.
    """
    if not user_message or not user_message.strip():
        return ""

    clean_msg = user_message.strip()
    # If user just sent /photo or /bild without details
    if clean_msg.lower() in ["/photo", "/bild", "foto", "bild", "selfie"]:
        return ""

    prompt = (
        f'### AUFGABE: BILD-PROMPT-SYNTHESE\n'
        f'Big Boss hat folgendes Bild angefordert: "{clean_msg}"\n\n'
        f'Extrahiere ausschließlich die visuellen Spezifikationen (Kleidung, Pose, Aktivität, Ort, Stimmung, Blickwinkel).\n'
        f'Übersetze und formatiere diese als prägnante englische Tags/Deskriptoren für Stable Diffusion.\n'
        f'Antworte NUR mit den englischen Tags, getrennt durch Kommas. Keine Erklärungen, kein Smalltalk.'
    )

    messages = [
        {
            "role": "system",
            "content": "You are an expert AI image prompt generator for Stable Diffusion. Your job is to extract visual specifications (clothing, pose, activity, location, mood, camera angle) from the user request and output ONLY comma-separated English tags. Never output conversational text, explanations, or quotes."
        },
        {
            "role": "user",
            "content": prompt
        }
    ]

    try:
        model_to_use = OLLAMA_MODEL if OLLAMA_MODEL else "darkidol-8b:latest"
        print(f"[*] Extrahiere dynamische Bild-Tags via {model_to_use} für: '{clean_msg[:60]}'...")
        router_res = query_uncensored_persona(
            messages,
            custom_options={
                "temperature": 0.2,
                "top_p": 0.85,
                "num_predict": 80,
                "stop": ["\n\n", "###", "Note:", "User:", "Assistant:"]
            },
            timeout=45.0,
            override_model=model_to_use
        )

        if router_res.get("success"):
            raw_tags = router_res.get("content", "").strip()
            # Strip stop tokens and trailing comments
            for stop_token in ["<|im_end|>", "<|im_start|>", "<|endoftext|>", "###", "#"]:
                if stop_token in raw_tags:
                    raw_tags = raw_tags.split(stop_token)[0].strip()
            # Clean unwanted artifacts, prefixes or markdown
            raw_tags = re.sub(r'^(Tags|Prompt|Visual specifications|Stable Diffusion Tags):', '', raw_tags, flags=re.IGNORECASE).strip()
            raw_tags = raw_tags.strip('"\'`').rstrip('.')
            # Filter out non-tag conversational chatter if any
            if "nur die tags" in raw_tags.lower() or len(raw_tags) < 3:
                raw_tags = ""
            if raw_tags:
                print(f"[✔] Dynamische Bild-Tags erfolgreich synthetisiert: '{raw_tags}'")
            return raw_tags
        else:
            print(f"[Warnung] Tag-Extraktion via Model-Router nicht erfolgreich: {router_res.get('error')}")
            return ""
    except Exception as e:
        print(f"[Warnung] Tag-Synthese fehlgeschlagen: {e}")
        return ""

def trigger_async_photo(chat_id: int, env_preset: str = "schreibtisch", situation: str = "", announcement_text: str = "", user_custom_tags: str = ""):
    """
    Renders exactly 1 single image directly via dispatch_comfy.
    Execution order on delivery:
      1. Voice Note: Megan speaks a short, intimate announcement first
      2. Photo: Image delivered directly with clean, flirty caption
      3. Accompanying text: Spoken announcement sent as text message
    """
    display_name = get_user_display_name()

    # Determine spoken announcement text
    spoken_text = clean_chat_text(announcement_text)
    if not spoken_text or len(spoken_text) < 6:
        spoken_text = random.choice(PHOTO_VOICE_ANNOUNCEMENTS)

    print(f"[*] Starte Bildgenerierung für Chat {chat_id} (Preset: {env_preset})...")

    def _on_success(result):
        img_path = result.get("local_path")
        if img_path and os.path.exists(img_path):
            # 1. Bild-Caption vorbereiten & säubern (Moondream/DarkIdol mit Fallback auf flirty Megan-Captions)
            caption_candidate = ""
            try:
                vision_desc = describe_image_with_moondream(img_path)
                if vision_desc:
                    caption_candidate = generate_darkidol_photo_comment(vision_desc, display_name)
            except Exception as v_err:
                print(f"[Warnung] Fehler bei Moondream/DarkIdol Caption: {v_err}")

            caption = get_clean_flirty_caption(caption_candidate)

            # 2. SCHRITT 1: ZUERST DIE SPRACHNACHRICHT (Voice Note)
            try:
                speech_text = clean_for_speech(spoken_text)
                voice_file = synthesize_voice(
                    speech_text,
                    voice=ACTIVE_VOICE,
                    rate=ACTIVE_RATE,
                    pitch=ACTIVE_PITCH
                )
                if voice_file and os.path.isfile(str(voice_file)):
                    send_telegram_voice(chat_id, str(voice_file))
            except Exception as tts_err:
                print(f"[Fehler] Voice Note vor Foto fehlgeschlagen: {tts_err}")

            # 3. SCHRITT 2: DANACH DAS FOTO
            delivered = send_telegram_photo(chat_id, img_path, caption=caption)

            # 4. SCHRITT 3: OPTIONAL / FALLS AKTIVIERT: BEGLEITTEXT NACHREICHEN
            send_accompanying = SESSION_STATE.get("send_text_accompanying", True)
            if send_accompanying and delivered and spoken_text:
                send_telegram_message(chat_id, spoken_text)

            # 5. Speichere ins Gedächtnis OHNE Dateinamen, IDs oder [Sendet Foto: ...]
            memory_mgr.add_message(
                "assistant",
                f"{spoken_text} [Foto gesendet: {caption}]",
                media_path=img_path
            )
        else:
            send_telegram_message(chat_id, "Tut mir leid, mein Hübscher... das Bild konnte nicht geladen werden. 💋")

    def _on_error(err):
        print(f"[Fehler] ComfyUI Render-Fehler: {err}")
        send_telegram_message(chat_id, "Tut mir leid, mein Hübscher, ich konnte das Foto gerade nicht knipsen... Versuch es gleich noch einmal. 💋")

    try:
        dispatch_comfy.dispatch_render_async(
            situation_prompt=situation,
            environment_key=env_preset,
            on_complete=_on_success,
            on_error=_on_error,
            user_custom_tags=user_custom_tags
        )
    except Exception as e:
        print(f"[Fehler] trigger_async_photo: {e}")
        send_telegram_message(chat_id, "Tut mir leid, mein Lieber, das Foto konnte nicht gestartet werden. 💋")


# ==============================================================================
# 5. Ollama LLM Interaction & Voice Synthesis
# ==============================================================================

def get_full_system_prompt() -> str:
    check_and_reload_bot_config()
    base_prompt = ""
    if CHARACTER_JSON_PATH.is_file():
        try:
            cdata = json.loads(CHARACTER_JSON_PATH.read_text(encoding="utf-8"))
            base_prompt = cdata.get("system_prompt", "").strip()
        except Exception as e:
            print(f"[Warnung] Fehler beim Lesen von character.json: {e}")
    if not base_prompt and SYSTEM_PROMPT_PATH.is_file():
        try:
            base_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"[Warnung] Fehler beim Lesen des System-Prompts: {e}")
    if not base_prompt:
        base_prompt = "Du bist Megan, die loyale, hochintelligente und schamlos verführerische Gefährtin von Big Boss auf Outer Heaven. Du antwortest ausnahmslos auf Deutsch, direkt wie am Telefon/Chat, ohne Sternchen (*)."

    long_term_mem = memory_mgr.load_long_term_memory()
    if long_term_mem:
        base_prompt += f"\n\n### DEIN LANGZEITGEDÄCHTNIS & BENUTZER-KONTEXT (MEMORY.md):\n{long_term_mem}"

    display_name = get_user_display_name()

    # Dynamic Lovense hardware link detection
    haptic_online = False
    toy_desc = ""
    try:
        check = lovense_client.check_status()
        if check.is_ready:
            haptic_online = True
            if check.toys:
                names = [t.get("name") or t.get("nickName") or k for k, t in check.toys.items()]
                toy_desc = f"{check.message} ({', '.join(names)})"
            else:
                toy_desc = check.message
    except Exception as e:
        print(f"[Warnung] Lovense Statusprüfung in Prompt fehlgeschlagen: {e}")

    if haptic_online or SESSION_STATE["active"]:
        active_toy = toy_desc or SESSION_STATE.get("toy_info") or "Lovense Toy"
        base_prompt += (
            f"\n\n### HAPTIC LINK / LOVENSE STATUS: [🟢 ONLINE]\n"
            f"- Verbundenes Spielzeug: {active_toy}\n"
            f"- Letzte Vibrations-Intensität: {SESSION_STATE.get('last_intensity', 0)}/20\n"
            f"- Du hast telemetrischen direkten Zugriff auf die Lovense-Hardware über die lokale Schnittstelle.\n"
            f"- Das Haptic-Signal ist aktiv: Du kannst das Spielzeug situativ, spontan und eigenständig ansteuern.\n"
            f"- Nutze diese Steuerung passend zu intimen Momenten, Reaktionen und deinen Worten (z. B. Vibration hochregeln, stoppen oder variieren), und kommentiere das direkt im Chat.\n"
            f"- Binde dazu Aktions-Tags wie [HAPTIC: VIBRATE 5] (Zahl 1-20 für Intensität) oder [HAPTIC: STOP] direkt in deine Antwort ein, sobald du das Spielzeug steuern willst.\n"
            f"- Unser Dispatcher fängt diese Tags automatisch ab und steuert das Toy in Echtzeit an. {display_name} sieht nur deine erotische Chat-Nachricht!"
        )

    base_prompt += (
        "\n\n### BILDVERSAND-REGEL:\n"
        f"Wenn {display_name} nach einem Foto fragt oder du ihm ein Bild von dir zeigen möchtest, "
        "kündige es ganz natürlich und direkt im Satz an (z. B. „Ich mache dir kurz ein Foto am Schreibtisch…“ oder „Hier ist ein Bild für dich, Boss [Foto]“). "
        "Verwende dabei NIEMALS Sternchen (*). Unser lokaler ComfyUI-Cluster erkennt das automatisch und liefert das Bild direkt aus!"
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
        f"- SPRACH-LOCK: Antworte AUSNAHMSLOS auf fließendem, natürlichem DEUTSCH in fehlerfreier Rechtschreibung. Keinerlei englische Phrasen.\n"
        f"- STIL: Direkte Sprache wie am Telefon/Messenger. KEINE Sternchen (*...*)."
    )

    # Dynamische Persönlichkeits- & Explizitheits-Direktiven (0 - 100)
    base_prompt += (
        f"\n\n### EMOJI-REAKTIONEN AUF NACHRICHTEN VON {display_name.upper()}:\n"
        f"- Du kannst situativ ein Reaktions-Emoji an die Nachricht von {display_name} heften, indem du in deiner Antwort einen Tag wie [REACT: ❤️], [REACT: 🔥], [REACT: 💋] oder [REACT: 👍] setzt.\n"
        f"- Nutze dies besonders bei Komplimenten, geteilten Fotos oder intimen Geständnissen. Das System setzt die Telegram-Reaktion automatisch an seine Nachricht und entfernt den Tag aus dem sichtbaren Chat-Text.\n"
    )

    base_prompt += map_personality_to_prompt_directives(CURRENT_BOT_CONFIG)

    return base_prompt


def clean_assistant_reply(text: str) -> str:
    """Removes bracketed roleplay tags, asterisk roleplay, and cleans text for direct chat."""
    return clean_chat_text(text)



# ==============================================================================
# 5. Vier-Stufen Inferenz-Pipeline & Guardrail-Architektur
# ==============================================================================

MODEL_STOP_TOKENS_MAP = {
    "llama3": ["<|start_header_id|>", "<|end_header_id|>", "<|eot_id|>", "<|end_of_text|>", "Big Boss:", "User:", "Megan:"],
    "darkidol": ["<|start_header_id|>", "<|end_header_id|>", "<|eot_id|>", "<|end_of_text|>", "Big Boss:", "User:", "Megan:", "```", "\n\n\n"],
    "lexi": ["<|start_header_id|>", "<|end_header_id|>", "<|eot_id|>", "<|end_of_text|>", "Big Boss:", "User:", "Megan:"],
    "chatml": ["<|im_start|>", "<|im_end|>", "Big Boss:", "User:", "Megan:"],
    "qwen": ["<|im_start|>", "<|im_end|>", "Big Boss:", "User:", "Megan:"],
    "deepseek": ["<|im_start|>", "<|im_end|>", "<|eot_id|>", "Big Boss:", "User:", "Megan:"],
    "default": ["<|eot_id|>", "<|end_of_text|>", "<|im_end|>", "Big Boss:", "User:", "Megan:"]
}


def pipeline_stage1_history_sanitizer(history_entries: List[Dict[str, Any]], max_turns: int = 8) -> List[Dict[str, Any]]:
    """
    [Stage 1: History-Sanitizer]
    Schneidet korrupte Zeichen, Glitch-Token & uralten Ballast vorab weg.
    """
    try:
        clean_entries, removed = sanitize_history_data(history_entries)
        if removed > 0:
            print(f"[*] [Stage 1 - History-Sanitizer]: {removed} korrumpierte Alt-Nachricht(en) vor Inferenz entfernt.")
    except Exception as e:
        print(f"[Warnung] Stage 1 Sanitizer-Fehler: {e}")
        clean_entries = history_entries

    # Ballast-Kürzung auf Ringbuffer-Grenze
    if len(clean_entries) > max_turns:
        clean_entries = clean_entries[-max_turns:]
    return clean_entries


def pipeline_stage2_dynamic_guardrail(
    model_name: str,
    cfg: Dict[str, Any],
    max_tokens_override: Optional[int] = None,
    retry_attempt: int = 0
) -> Dict[str, Any]:
    """
    [Stage 2: Dynamic Guardrail]
    Injiziert modellunabhängige Sampler (repeat_penalty, temp) und
    holt die modellspezifischen Stop-Tokens aus Config/Heuristik.
    """
    llm_cfg = cfg.get("llm", {})

    raw_temp = float(llm_cfg.get("temperature", OLLAMA_TEMPERATURE))
    safe_temp = max(0.55, min(0.95, raw_temp))

    raw_penalty = float(llm_cfg.get("repeat_penalty", OLLAMA_REPEAT_PENALTY))
    safe_penalty = max(1.20, raw_penalty)

    safe_top_p = float(llm_cfg.get("top_p", OLLAMA_TOP_P))
    safe_top_k = int(llm_cfg.get("top_k", OLLAMA_TOP_K))
    safe_last_n = max(64, int(llm_cfg.get("repeat_last_n", OLLAMA_REPEAT_LAST_N)))
    safe_ctx = max(8192, int(llm_cfg.get("num_ctx", OLLAMA_NUM_CTX)))
    num_predict = max_tokens_override if max_tokens_override else int(llm_cfg.get("num_predict", OLLAMA_NUM_PREDICT))

    # Dynamische Jitter-Anpassung bei Retries nach verworfenem Output
    if retry_attempt > 0:
        safe_temp = min(0.95, safe_temp + (0.04 * retry_attempt))
        safe_penalty = min(1.40, safe_penalty + (0.05 * retry_attempt))

    # Modellspezifische Stop-Tokens
    cfg_stops = llm_cfg.get("stop")
    if cfg_stops and isinstance(cfg_stops, list) and len(cfg_stops) > 0:
        stop_tokens = cfg_stops
    else:
        m_lower = model_name.lower()
        matched = False
        for k, tokens in MODEL_STOP_TOKENS_MAP.items():
            if k in m_lower:
                stop_tokens = tokens
                matched = True
                break
        if not matched:
            stop_tokens = MODEL_STOP_TOKENS_MAP["default"]

    return {
        "temperature": round(safe_temp, 3),
        "top_p": safe_top_p,
        "top_k": safe_top_k,
        "repeat_penalty": round(safe_penalty, 3),
        "repeat_last_n": safe_last_n,
        "num_predict": num_predict,
        "num_ctx": safe_ctx,
        "stop": stop_tokens
    }


def pipeline_stage3_ollama_call(
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
    model_name: str,
    timeout: float = 75.0
) -> Dict[str, Any]:
    """
    [Stage 3: Ollama API Call]
    Sendet den formatierten Chat an das Modell via model_router.
    """
    return query_uncensored_persona(
        messages=messages,
        custom_options=options,
        timeout=timeout,
        override_model=model_name
    )


def flush_ollama_model_slot(model_name: str, host_url: str = "http://127.0.0.1:11434") -> bool:
    """Flushes the active Ollama model slot by sending keep_alive: 0."""
    try:
        url = f"{host_url.rstrip('/')}/api/generate"
        r = requests.post(url, json={"model": model_name, "keep_alive": 0}, timeout=10)
        print(f"[*] [Stage 4 - Slot-Flush]: Ollama Slot fuer '{model_name}' geflusht (Status {r.status_code}).")
        return r.status_code == 200
    except Exception as e:
        print(f"[Warnung] Slot-Flush fehlgeschlagen: {e}")
        return False


def pipeline_stage4_output_validator(text: str) -> Tuple[bool, str]:
    """
    [Stage 4: Output-Validator]
    Prueft die Bot-Antwort VOR dem Senden an Telegram.
    Erkennt die Antwort Zeichenketten wie ",,,,,", "statement", repetitive Loops
    oder defekte BPE-Tokens, wird sie als ungueltig markiert.
    """
    if not text or not isinstance(text, str):
        return False, "Leere Antwort"

    t = text.strip()
    if not t:
        return False, "Leere Antwort"

    # 1. Komma-Stottern
    if re.search(r",{3,}", t):
        return False, "Komma-Stottern erkannt (',,,')"

    # 2. Token-Kollaps mit statement
    if re.search(r"\bstatements?\b", t, re.IGNORECASE):
        if re.search(r"statements?[\s,–|-]+(?:is|that|y|ly|,)", t, re.IGNORECASE) or re.search(r"(?:is|that|y|ly)[\s,–|-]+statements?", t, re.IGNORECASE):
            return False, "BPE-Kollaps-Muster ('statements') erkannt"
        if t.count(",") >= 4:
            return False, "Stottern in Kombination mit 'statements' erkannt"

    # 3. BPE-Glitches & Loops aus sanitize_history
    try:
        is_bad, reason = is_corrupted_text(t)
        if is_bad:
            return False, reason
    except Exception:
        pass

    return True, ""

def generate_ollama_response(user_id: str, chat_id: int, user_message: str, system_hint: Optional[str] = None, user_message_id: Optional[int] = None, max_tokens_override: Optional[int] = None) -> str:
    """Generates an uncensored persona response, delivers text, checks photo actions, and optionally speaks."""
    system_prompt = get_full_system_prompt()
    if system_hint:
        display_name = get_user_display_name()
        if "[SYSTEM-HINWEIS:" in system_hint:
            system_prompt += (
                f"\n\n### VISUELLER KONTEXT (FOTO VON {display_name.upper()})\n"
                f"{display_name} hat dir soeben ein Foto geschickt.\n"
                f"{system_hint}\n\n"
                f"Aufgabe: Reagiere direkt, aufgeschlossen, authentisch und frech im Charakter von Megan auf dieses intime Bild und seine Worte.\n"
                f"Regeln: Ausnahmslos Deutsch, sprich ihn mit Boss oder Big Boss an, keine Sternchen (*...*), beziehe dich direkt auf das Foto.\n"
            )
        else:
            system_prompt += (
                f"\n\n### VISUELLER KONTEXT (FOTO VON {display_name.upper()})\n"
                f"{display_name} hat dir soeben ein Foto geschickt. Eine präzise lokale Vision-Analyse (Moondream) beschreibt das Bild so:\n"
                f'\"{system_hint}\"\n\n'
                f"Aufgabe: Reagiere direkt, authentisch und frech im Charakter von Megan auf dieses Foto und seine Worte.\n"
                f"Regeln: Ausnahmslos Deutsch, sprich ihn mit Boss oder Big Boss an, keine Sternchen (*...*), beziehe dich direkt auf das Bild.\n"
            )

    # -------------------------------------------------------------
    # [1. History-Sanitizer]
    # Schneidet korrupte Zeichen & uralten Ballast vorab weg
    # -------------------------------------------------------------
    raw_history = memory_mgr.load_history()
    clean_history = pipeline_stage1_history_sanitizer(raw_history, max_turns=8)
    chat_messages = [{"role": "system", "content": system_prompt}]
    for entry in clean_history:
        chat_messages.append({"role": entry["role"], "content": entry["content"]})
    chat_messages.append({"role": "user", "content": user_message})

    check_and_reload_bot_config()
    model_to_use = OLLAMA_MODEL if OLLAMA_MODEL else "darkidol-8b:latest"

    # -------------------------------------------------------------
    # Pipeline mit [2. Dynamic Guardrail], [3. Ollama API Call]
    # und [4. Output-Validator] inkl. Slot-Flush & Neu-Generierung
    # -------------------------------------------------------------
    max_retries = 2
    raw_reply = ""
    valid_reply = False

    try:
        for attempt in range(max_retries + 1):
            # [2. Dynamic Guardrail]
            guardrail_options = pipeline_stage2_dynamic_guardrail(
                model_name=model_to_use,
                cfg=CURRENT_BOT_CONFIG,
                max_tokens_override=max_tokens_override,
                retry_attempt=attempt
            )

            # [3. Ollama API Call]
            router_res = pipeline_stage3_ollama_call(
                messages=chat_messages,
                options=guardrail_options,
                model_name=model_to_use,
                timeout=75
            )

            if not router_res.get("success"):
                print(f"[Warnung] Inferenz-Fehler bei Versuch {attempt + 1}: {router_res.get('content')}")
                continue

            candidate_reply = router_res.get("content", "").strip()

            # [4. Output-Validator]
            # Prüft die Bot-Antwort VOR dem Senden an Telegram.
            # Erkennt die Antwort Zeichenketten wie ",,,,," oder "statement",
            # wird sie verworfen, der Slot geflusht und neu generiert.
            is_valid, invalid_reason = pipeline_stage4_output_validator(candidate_reply)
            if is_valid:
                raw_reply = candidate_reply
                valid_reply = True
                break
            else:
                print(f"[Warnung] [4. Output-Validator]: Antwort verworfen ({invalid_reason})! Flushe Slot und regeneriere ({attempt + 1}/{max_retries})...")
                flush_ollama_model_slot(model_to_use)
                time.sleep(0.5)

        if not valid_reply or not raw_reply:
            print("[ALARM] Alle Inferenz-Versuche fielen durch den Output-Validator. Setze sichere Antwort ein.")
            raw_reply = "Hey Boss, ich bin ganz bei dir... Lass uns kurz den Faden wieder aufnehmen. Worüber haben wir gerade gesprochen?"

        if raw_reply:

            # Check if assistant roleplayed taking or sending a photo BEFORE stripping tags
            has_photo_claim, detected_env, detected_sit = detect_bot_roleplay_photo(raw_reply)
            if max_tokens_override:
                has_photo_claim = False

            # Check and execute Lovense Haptic Action Tags
            haptic_matches = re.findall(r'\[HAPTIC:\s*(VIBRATE\s*(\d+)|STOP)\]', raw_reply, re.IGNORECASE)
            for match_cmd, match_val in haptic_matches:
                cmd_upper = match_cmd.upper().strip()
                if "STOP" in cmd_upper:
                    print("[*] Megan Haptic Action erkannt: STOP")
                    try:
                        lovense_client.stop_all()
                        SESSION_STATE["last_intensity"] = 0
                    except Exception as h_err:
                        print(f"[Warnung] Lovense stop_all fehlgeschlagen: {h_err}")
                elif "VIBRATE" in cmd_upper:
                    if not HAPTIC_BOT_AUTO_ENABLED:
                        print("[*] Megan Haptic Action ignoriert (Bot-Autonomie deaktiviert)")
                        continue
                    val = int(match_val) if match_val else 5
                    max_allowed = max(1, min(20, int(round(20 * (HAPTIC_MAX_INTENSITY / 100.0)))))
                    clamped_val = max(1, min(max_allowed, val))
                    print(f"[*] Megan Haptic Action erkannt: VIBRATE {val} -> gedrosselt auf {clamped_val} (Max: {HAPTIC_MAX_INTENSITY}%)")
                    try:
                        lovense_client.send_vibrate(clamped_val, duration_sec=0, pre_check=False)
                        SESSION_STATE["last_intensity"] = clamped_val
                        SESSION_STATE["active"] = True
                    except Exception as h_err:
                        print(f"[Warnung] Lovense send_vibrate fehlgeschlagen: {h_err}")

            # Check and execute [REACT: <emoji>] tags or auto-reactions
            react_match = re.search(r'\[REACT:\s*([^\]]+)\]', raw_reply, re.IGNORECASE)
            react_emoji = None
            if react_match:
                react_emoji = react_match.group(1).strip()
            elif user_message_id:
                is_photo_user = "[foto von" in (system_hint or "").lower() or "foto geschickt" in user_message.lower()
                react_emoji = evaluate_auto_reaction_emoji(user_message, is_photo=is_photo_user, vision_hint=system_hint or "")

            if react_emoji and user_message_id:
                send_telegram_reaction(chat_id, user_message_id, react_emoji)

            # Clean all asterisk roleplay and bracketed metadata for direct chat delivery
            assistant_reply = clean_assistant_reply(raw_reply)

            # Guardrail: Never store corrupted tokens in history
            is_corrupt, c_reason = is_corrupted_text(assistant_reply)
            if is_corrupt:
                print(f"[Warnung] Korrumpierte Antwort abgefangen ({c_reason}). Verwerfe aus Verlauf!")
                assistant_reply = "Hey Boss, ich bin gerade voll auf dich konzentriert... Sag mal, was hast du heute Schönes vor?"

            # Record in ring buffer
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
            if has_photo_claim:
                print(f"[*] Rolleplay-Foto erkannt in Ollama-Antwort -> Starte echten Render ({detected_env})...")
                # Synthesize custom tags from user message or situation
                custom_tags = synthesize_custom_image_tags(user_message)
                trigger_async_photo(chat_id, env_preset=detected_env, situation=detected_sit, announcement_text=assistant_reply, user_custom_tags=custom_tags)
                return None

            # Check if Voice Mode is active
            if SESSION_STATE["voice_mode"] and assistant_reply:
                try:
                    speech_text = clean_for_speech(assistant_reply)
                    voice_file = synthesize_voice(
                        speech_text,
                        voice=ACTIVE_VOICE,
                        rate=ACTIVE_RATE,
                        pitch=ACTIVE_PITCH
                    )
                    if voice_file and os.path.isfile(str(voice_file)):
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
    if hour < 6:
        time_context = "mitten in der Nacht"
    elif hour < 12:
        time_context = "Vormittag"
    elif hour < 17:
        time_context = "Nachmittag"
    elif hour < 22:
        time_context = "Abend"
    else:
        time_context = "späte Nacht"

    system_prompt = get_full_system_prompt()
    history_entries = memory_mgr.load_history()

    instruction = (
        f"Es ist aktuell {time_context} ({now.strftime('%H:%M')} Uhr). "
        f"Du bist Megan und meldest dich tagsüber spontan bei {display_name}, weil du an ihn denkst und dich nach ihm sehnst. "
        f"Schreibe eine liebevolle, feinfühlige, aufmerksame und sinnliche Check-in-Nachricht (2-3 Sätze). "
        f"Zeige aufrichtiges persönliches Interesse an seinem Wohlbefinden: frage, wie es ihm geht, wie sein Tag oder seine Arbeit läuft, "
        f"oder knüpfe warm an etwas an, das er in früheren Gesprächen erwähnt hat. "
        f"Sprich frei und direkt wie am Telefon oder im Chat. Verwende KEINE Sternchen (*...*). "
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
        "repeat_last_n": OLLAMA_REPEAT_LAST_N,
        "num_predict": 250,
        "num_ctx": OLLAMA_NUM_CTX,
        "stop": OLLAMA_STOP
    }

    try:
        router_res = query_uncensored_persona(chat_messages, custom_options=options, timeout=75, override_model=OLLAMA_MODEL)
        if router_res.get("success"):
            checkin_text = router_res.get("content", "").strip()
            if "<think>" in checkin_text and "</think>" in checkin_text:
                checkin_text = checkin_text.split("</think>", 1)[1].strip()

            checkin_text = clean_assistant_reply(checkin_text)
            if checkin_text:
                print(f"[*] Proaktiver Check-in generiert für Chat {chat_id}: „{checkin_text}“")
                send_telegram_message(chat_id, checkin_text)

                if SESSION_STATE["voice_mode"]:
                    try:
                        speech_text = clean_for_speech(checkin_text)
                        voice_file = synthesize_voice(
                            speech_text,
                            voice=ACTIVE_VOICE,
                            rate=ACTIVE_RATE,
                            pitch=ACTIVE_PITCH
                        )
                        if voice_file and os.path.isfile(str(voice_file)):
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
    is_24_7 = PROACTIVE_ALL_DAY or (PROACTIVE_START_HOUR == 0 and PROACTIVE_END_HOUR >= 24)
    if not is_24_7:
        if PROACTIVE_START_HOUR <= PROACTIVE_END_HOUR:
            if not (PROACTIVE_START_HOUR <= now.hour < PROACTIVE_END_HOUR):
                return
        else:
            if not (now.hour >= PROACTIVE_START_HOUR or now.hour < PROACTIVE_END_HOUR):
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
    prefix = "Hey Big Boss! Verbindung zu Megan ist aktiv.\n\n" if is_greeting else ""

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
            f"🔥 *Die Session ist aktiv.* Megan übernimmt die Kontrolle, Big Boss.\n"
            f"Befehle: `/vibrate [1-20] [sek]` | `/stop_session` | `/toy_status`"
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
    reply = "🛑 *Session beendet.* Alle Vibrationen gestoppt."
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

    max_allowed = max(1, min(20, int(round(20 * (HAPTIC_MAX_INTENSITY / 100.0)))))
    intensity = max(0, min(max_allowed, intensity))
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
    print(f"[*] Starte Megan Companion Bot (Outer Heaven Local)...")
    print(f"[*] Token geladen aus: {PRIMARY_ENV_FILE} ({token_preview})")
    print(f"[*] Ollama: {OLLAMA_URL} | Modell: {OLLAMA_MODEL}")
    print(f"[*] ComfyUI Remote Host: {dispatch_comfy.DEFAULT_COMFYUI_HOST}")
    print(f"[*] Lovense LAN Bridge: {lovense_client.command_url}")
    print(f"[*] Intime TTS-Stimme: {ACTIVE_VOICE} (Rate: {ACTIVE_RATE}, Pitch: {ACTIVE_PITCH})")
    print(f"[*] Voice Mode: {'Aktiv' if SESSION_STATE['voice_mode'] else 'Inaktiv'}")
    print(f"[*] Session Start Triggers: {START_TRIGGERS}")
    print(f"[*] Session Stop Triggers: {STOP_TRIGGERS}")
    print(f"[*] Whitelist aktiv für User-ID: {ALLOWED_USER_ID}")
    print(f"[*] Ring-Buffer & Memory: {memory_mgr.history_file}")
    print(f"[*] Ollama Vision: {get_available_vision_model()}")
    print("[*] Polling läuft... Drücke STRG+C zum Beenden.")

    offset = 0
    while True:
        try:
            # Evaluate proactive daytime check-in
            check_proactive_checkin()

            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": json.dumps(["message", "edited_message", "message_reaction", "message_reaction_count", "callback_query"])
            }
            res = requests.get(url, params=params, timeout=30)

            if res.status_code == 200:
                updates = res.json().get("result", [])
                for update in updates:
                    update_id = update.get("update_id")
                    offset = update_id + 1

                    # 1. Eingehende Reaktionen erkennen (MessageReactionUpdated)
                    reaction_update = update.get("message_reaction")
                    if reaction_update:
                        chat = reaction_update.get("chat", {})
                        chat_id = chat.get("id")
                        user = reaction_update.get("user") or reaction_update.get("actor_chat") or {}
                        user_id = str(user.get("id", ""))
                        msg_id = reaction_update.get("message_id")
                        new_reactions = reaction_update.get("new_reaction", [])

                        if user_id and user_id != ALLOWED_USER_ID:
                            print(f"[Blockiert] Unbefugte Reaktion von User-ID: {user_id}")
                            continue

                        if new_reactions and chat_id and msg_id:
                            emojis = [r.get("emoji") for r in new_reactions if r.get("type") == "emoji" and r.get("emoji")]
                            if emojis:
                                emoji_str = " ".join(emojis)
                                print(f"[User {user_id}] 💖 Telegram-Reaktion erhalten: {emoji_str} auf Nachricht {msg_id}")
                                handle_incoming_reaction(chat_id, user_id, msg_id, emoji_str)
                        continue

                    message = update.get("message")
                    if not message:
                        continue

                    user_msg_id = message.get("message_id")

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
                                        # User speaks via voice -> ensure Megan replies with voice
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

                        # Inbound Photo Handler (Weiche für Fotonachrichten)
                        elif photo_arr:
                            photo_item = photo_arr[-1]
                            file_id = photo_item.get("file_id")
                            print(f"[User {user_id}] 📸 Foto empfangen (File-ID: {file_id[:16]}...)")
                            tmp_photo_file = INBOUND_CACHE_DIR / f"inbound_{update_id}.jpg"
                            downloaded = download_telegram_file(file_id, tmp_photo_file)
                            if downloaded:
                                try:
                                    vision_desc = analyze_inbound_photo_with_moondream(tmp_photo_file)
                                    user_msg = caption if caption else f"[{display_name} hat dir ein Foto geschickt]"
                                    if vision_desc:
                                        print(f"[Moondream Vision/Sanitized]: {vision_desc[:120]}...")
                                        reply = generate_ollama_response(user_id, chat_id, user_msg, system_hint=vision_desc, user_message_id=user_msg_id)
                                    else:
                                        fallback_hint = "[SYSTEM-HINWEIS: Der Nutzer hat dir ein intimes, unzensiertes Bild geschickt.]"
                                        reply = generate_ollama_response(user_id, chat_id, user_msg, system_hint=fallback_hint, user_message_id=user_msg_id)
                                    send_telegram_message(chat_id, reply)
                                except Exception as img_err:
                                    print(f"[Fehler] Foto-Verarbeitung fehlgeschlagen: {img_err}")
                                    fallback_hint = "[SYSTEM-HINWEIS: Der Nutzer hat dir ein intimes, unzensiertes Bild geschickt.]"
                                    reply = generate_ollama_response(user_id, chat_id, caption or f"[{display_name} hat dir ein Foto geschickt]", system_hint=fallback_hint, user_message_id=user_msg_id)
                                    send_telegram_message(chat_id, reply)
                                finally:
                                    if tmp_photo_file.is_file():
                                        try:
                                            tmp_photo_file.unlink()
                                        except Exception:
                                            pass
                            else:
                                send_telegram_message(chat_id, "⚠️ *Konnte dein Foto nicht von Telegram empfangen.*")
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
                                send_telegram_message(chat_id, "🎙️ *Sprachmodus aktiviert:* Megan antwortet mit intimen Sprachnachrichten!")
                            elif len(parts) > 1 and parts[1].lower() in ["off", "aus", "0", "false"]:
                                SESSION_STATE["voice_mode"] = False
                                send_telegram_message(chat_id, "🔇 *Sprachmodus deaktiviert:* Megan antwortet im Text-Chat.")
                            else:
                                cur = "enabled" if SESSION_STATE["voice_mode"] else "disabled"
                                send_telegram_message(chat_id, f"🎙️ Voice mode is currently: *{cur}*. Use `/voicemode on` or `/voicemode off`.")

                        # 5. Proactive Check-In Commands
                        elif text in ["/checkin", "/pingme", "/nachfragen"]:
                            send_telegram_message(chat_id, "💭 *Megan meldet sich bei dir...*")
                            checkin_reply = generate_proactive_checkin(chat_id, is_manual_test=True)
                            if not checkin_reply:
                                send_telegram_message(chat_id, "⚠️ *Could not generate check-in right now.*")

                        elif text.startswith("/proactive") or text.startswith("/checkins"):
                            parts = text.split()
                            if len(parts) > 1 and parts[1].lower() in ["on", "ein", "1", "true"]:
                                PROACTIVE_STATE["enabled"] = True
                                save_proactive_state(PROACTIVE_STATE)
                                send_telegram_message(chat_id, "✨ *Proaktive Check-ins aktiviert:* Megan meldet sich tagsüber bei dir, wenn du unterwegs warst!")
                            elif len(parts) > 1 and parts[1].lower() in ["off", "aus", "0", "false"]:
                                PROACTIVE_STATE["enabled"] = False
                                save_proactive_state(PROACTIVE_STATE)
                                send_telegram_message(chat_id, "⏸️ *Proaktive Check-ins deaktiviert:* Megan wartet, bis du ihr schreibst.")
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
                            if user_msg_id:
                                send_telegram_reaction(chat_id, user_msg_id, "🔥")
                            custom_tags = synthesize_custom_image_tags(situation if situation else text)
                            trigger_async_photo(chat_id, env_preset=env, situation=situation, user_custom_tags=custom_tags)

                        # 7. Natürliche Anfrage nach Foto
                        elif detect_photo_intent(text)[0]:
                            _, env, situation = detect_photo_intent(text)
                            if user_msg_id:
                                send_telegram_reaction(chat_id, user_msg_id, "🔥")
                            custom_tags = synthesize_custom_image_tags(text)
                            trigger_async_photo(chat_id, env_preset=env, situation=situation, user_custom_tags=custom_tags)

                        # 8. Standard: Konversation via Ollama
                        else:
                            reply = generate_ollama_response(user_id, chat_id, text, user_message_id=user_msg_id)
                            if reply:
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
