#!/usr/bin/env python3
"""
hermes_telegram_service.py - Outer Heaven Telegram Bot Service (Bigbossheaven_bot)
Integrated with Hermes Agent (deepseek-r1:14b), ComfyUI-Bridge, and Node Fox Delegation.

Features:
1. Whitelist Verification: Restricts bot access strictly to TELEGRAM_ALLOWED_USERS.
2. ComfyUI-Bridge:
   - Accepts prompts (/render, /image, /generate or natural requests).
   - Generates images via ComfyUI (127.0.0.1:8188) on Intel Sparkle Arc A770 16GB.
   - Sends generated images directly back into the Telegram chat.
3. Multi-Node Workload Delegation:
   - When ComfyUI is rendering or VRAM is occupied, text tasks are transparently
     delegated to Node Fox (AVX2 worker).
   - When idle, tasks are processed locally via Ollama (deepseek-r1:14b).
4. System & Cluster Tools:
   - /status: Shows VRAM, ComfyUI, Ollama, and Fox status.
   - /calendar: Lists upcoming Google Calendar events via Service Account.
   - /drive: Lists files in Google Drive via Service Account.
"""

import os
import sys
import time
import json
import logging
import random
import asyncio
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Set up logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("hermes-telegram")

# Cluster Paths & Constants
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_OUTPUT_DIR = Path("/home/outerheaven/ComfyUI/output")
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL = "deepseek-r1:14b"
DEFAULT_FOX_MODEL = "hermes-3-3b-q4:latest"
TOKEN_FILE = Path("/mnt/storage-alpha/secrets/telegram_outerheaven.token")
ENV_FILE = Path("/home/outerheaven/.hermes/.env")
SOUL_FILE = Path("/home/outerheaven/.hermes/SOUL.md")
GOOGLE_CALENDAR_SCRIPT = Path("/home/outerheaven/.hermes/skills/productivity/google-calendar/scripts/google_calendar.py")
GOOGLE_DRIVE_SCRIPT = Path("/home/outerheaven/.hermes/skills/productivity/google-drive/scripts/google_drive.py")
PYTHON_BIN = Path("/home/outerheaven/.hermes/.venv/bin/python")

# Add scripts directory to path for cluster_delegate
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))


def load_token() -> str:
    """Loads bot token from secrets file or environment."""
    if TOKEN_FILE.is_file():
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if token:
                logger.info("Bot-Token erfolgreich aus %s geladen.", TOKEN_FILE)
                return token
        except Exception as e:
            logger.warning("Konnte Token nicht aus %s lesen: %s", TOKEN_FILE, e)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not token:
        raise ValueError("Kein Telegram-Bot-Token gefunden! Bitte /mnt/storage-alpha/secrets/telegram_outerheaven.token prüfen.")
    return token


def load_allowed_users() -> set:
    """Loads allowed user IDs from environment or .env."""
    raw = os.getenv("TELEGRAM_ALLOWED_USERS", "")
    if not raw and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TELEGRAM_ALLOWED_USERS="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not raw:
        raw = "<YOUR_TELEGRAM_USER_ID>"

    allowed = set()
    for item in raw.split(","):
        item = item.strip()
        if item:
            try:
                allowed.add(int(item))
            except ValueError:
                allowed.add(item)
    logger.info("Autorisierte Telegram-User-IDs: %s", allowed)
    return allowed


ALLOWED_USERS = load_allowed_users()


def load_system_prompt() -> str:
    """Loads system instructions from SOUL.md."""
    if SOUL_FILE.is_file():
        try:
            return SOUL_FILE.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Konnte SOUL.md nicht lesen: %s", e)
    return (
        "Du bist Hermes, KI-Assistent für das OuterHeaven-Projekt im Dienst von Big Boss. "
        "Du antwortest immer auf Deutsch. Host: outerheaven (HP Z620, Sparkle Arc A770 16GB, AVX1 CPU). "
        "Strikt kein CPU-Offloading bei AI-Tasks. Zugriff auf /mnt/storage-beta."
    )


def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_USERS or str(user_id) in ALLOWED_USERS


async def check_auth(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_authorized(user.id):
        uid = user.id if user else "Unbekannt"
        uname = user.username if user else "Unbekannt"
        logger.warning("⛔ Unautorisierter Zugriff abgewehrt! User ID: %s (@%s)", uid, uname)
        if update.effective_message:
            await update.effective_message.reply_text(
                f"⛔ *Zugriff verweigert*\n\nDeine User-ID (`{uid}`) ist nicht im Outer Heaven Cluster autorisiert.",
                parse_mode=ParseMode.MARKDOWN
            )
        return False
    return True


async def render_image_comfyui(prompt_text: str, negative_prompt: str = "") -> str:
    if not negative_prompt:
        negative_prompt = (
            "ugly, deformed, bad anatomy, bad paws, extra limbs, missing limbs, "
            "fused bodies, poorly drawn face, blurry, low quality, artifacts, text, watermark, signature"
        )

    seed = random.randint(1, 9999999999999)

    workflow = {
        "prompt": {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": "DreamShaper8_LCM.safetensors"
                }
            },
            "2": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["1", 1],
                    "text": prompt_text
                }
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["1", 1],
                    "text": negative_prompt
                }
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["2", 0],
                    "seed": seed,
                    "steps": 14,
                    "cfg": 6.5,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0
                }
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["5", 0],
                    "vae": ["1", 2]
                }
            },
            "7": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "OuterHeaven_Bot",
                    "images": ["6", 0]
                }
            }
        }
    }

    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=json.dumps(workflow).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            prompt_id = data.get("prompt_id")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI hat keine prompt_id geliefert: {data}")
    except Exception as e:
        raise RuntimeError(f"Verbindung zu ComfyUI ({COMFYUI_URL}) fehlgeschlagen: {e}")

    logger.info("ComfyUI Render gestartet: prompt_id=%s, seed=%d", prompt_id, seed)

    max_wait = 90
    start_time = time.time()

    while time.time() - start_time < max_wait:
        await asyncio.sleep(2.0)
        try:
            h_req = urllib.request.Request(f"{COMFYUI_URL}/history/{prompt_id}")
            with urllib.request.urlopen(h_req, timeout=5) as h_resp:
                history = json.loads(h_resp.read().decode("utf-8"))
                if prompt_id in history:
                    outputs = history[prompt_id].get("outputs", {})
                    if "7" in outputs and "images" in outputs["7"]:
                        images = outputs["7"]["images"]
                        if images:
                            fname = images[0].get("filename")
                            subfolder = images[0].get("subfolder", "")
                            img_path = COMFYUI_OUTPUT_DIR / subfolder / fname if subfolder else COMFYUI_OUTPUT_DIR / fname
                            if img_path.is_file():
                                return str(img_path)
        except Exception as e:
            logger.debug("ComfyUI history poll Fehler: %s", e)

    raise TimeoutError(f"ComfyUI-Rendering hat nach {max_wait} Sekunden noch kein Ergebnis geliefert.")


async def generate_response(prompt: str, user_name: str = "") -> Tuple[str, str]:
    from cluster_delegate import check_comfyui_status, query_ollama_local, query_ollama_fox, is_fox_rebooting

    system_prompt = load_system_prompt()
    if user_name:
        system_prompt += f"\nDu sprichst gerade mit Big Boss ({user_name})."

    is_busy, reason = check_comfyui_status()
    loop = asyncio.get_running_loop()

    if is_busy:
        logger.info("ComfyUI aktiv (%s) -> Delegiere Textaufgabe an Node Fox.", reason)
        try:
            resp = await loop.run_in_executor(None, query_ollama_fox, prompt, system_prompt)
            return resp, "fox"
        except Exception as e:
            logger.error("Fox Delegation fehlgeschlagen: %s. Versuche lokalen Fallback.", e)
            resp = await loop.run_in_executor(None, query_ollama_local, prompt, system_prompt)
            return resp, "outerheaven (Fallback)"
    else:
        try:
            resp = await loop.run_in_executor(None, query_ollama_local, prompt, system_prompt)
            return resp, "outerheaven"
        except Exception as e:
            logger.error("Lokale Inferenz auf outerheaven fehlgeschlagen: %s. Delegiere an Fox.", e)
            resp = await loop.run_in_executor(None, query_ollama_fox, prompt, system_prompt)
            return resp, "fox (Fallback)"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    user = update.effective_user.first_name
    msg = (
        f"🌌 *Outer Heaven Command Center*\n\n"
        f"Willkommen zurück, **{user}** (Big Boss)!\n\n"
        f"Der Cluster-Bot **Bigbossheaven_bot** ist online und einsatzbereit.\n\n"
        f"**Verfügbare Befehle & Funktionen:**\n"
        f"• `/render <prompt>` — Generiert ein Bild via ComfyUI auf der Arc A770\n"
        f"• `/status` — Zeigt VRAM, Host-Auslastung, ComfyUI- und Cluster-Status\n"
        f"• `/calendar` — Zeigt anstehende Termine aus Google Calendar\n"
        f"• `/drive` — Listet Dateien in Google Drive auf\n"
        f"• **Freier Chat:** Sende mir beliebige Fragen oder Anweisungen. Bei ComfyUI-Renderings lagere ich automatisch an Node Fox aus."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await cmd_start(update, context)


async def cmd_render(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return

    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text(
            "ℹ️ Bitte gib einen Bild-Prompt an!\n\nBeispiel:\n`/render two cute cats on a cozy wooden windowsill, morning light, photorealistic`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    status_msg = await update.message.reply_text(
        f"🎨 *ComfyUI Rendervorgang gestartet...*\n"
        f"**Hardware:** Intel Sparkle Arc A770 (16 GB VRAM)\n"
        f"**Modell:** `DreamShaper8_LCM.safetensors`\n"
        f"**Prompt:** _{prompt}_\n\n"
        f"⏳ Bitte einen Moment Geduld...",
        parse_mode=ParseMode.MARKDOWN
    )

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        t0 = time.time()
        img_path = await render_image_comfyui(prompt)
        elapsed = time.time() - t0

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)

        with open(img_path, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=f"✨ *ComfyUI Render abgeschlossen* ({elapsed:.1f}s)\n\nPrompt: {prompt}",
                parse_mode=ParseMode.MARKDOWN
            )
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error("Renderfehler: %s", e)
        await status_msg.edit_text(f"❌ *Fehler beim Rendern in ComfyUI:*\n`{e}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    comfy_status = "🔴 OFFLINE"
    vram_info = "Unbekannt"
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats")
        with urllib.request.urlopen(req, timeout=2) as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            ver = stats.get('system', {}).get('comfyui_version', 'akt')
            comfy_status = f"🟢 BEREIT (v{ver})"
            devs = stats.get("devices", [])
            if devs:
                dev = devs[0]
                total = dev.get("vram_total", 0) // (1024*1024)
                free = dev.get("vram_free", 0) // (1024*1024)
                vname = dev.get('name', 'Arc A770')
                vram_info = f"{total - free} MB / {total} MB belegt ({vname})"
    except Exception:
        pass

    ollama_oh = "🔴 OFFLINE"
    try:
        req = urllib.request.Request(f"{LOCAL_OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in tags.get("models", [])]
            ollama_oh = f"🟢 AKTIV ({', '.join(models)})"
    except Exception:
        pass

    fox_status = "🔴 OFFLINE"
    try:
        cmd = ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", "fox", "uptime -p"]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            fox_status = f"🟢 ONLINE ({stdout.decode().strip()})"
    except Exception:
        pass

    text = (
        f"📊 *Outer Heaven Cluster Status*\n\n"
        f"🌌 **Host outerheaven (Heavy Compute):**\n"
        f"• **ComfyUI Server (:8188):** {comfy_status}\n"
        f"• **Sparkle Arc A770 VRAM:** `{vram_info}`\n"
        f"• **Lokale Ollama Engine:** {ollama_oh}\n"
        f"• **CPU-Architektur:** Dual Xeon (AVX1 - strikt kein CPU-Offload)\n"
        f"• **Speicherpfade:** `/mnt/storage-alpha` & `/mnt/storage-beta` bereit\n\n"
        f"🦊 **Node fox (Worker & Gateway):**\n"
        f"• **Status:** {fox_status}\n"
        f"• **Routing-Regel:** Bei ComfyUI-Renderlast wird Inferenz automatisch an Fox delegiert."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        proc = await asyncio.create_subprocess_exec(
            str(PYTHON_BIN), str(GOOGLE_CALENDAR_SCRIPT), "list", "--max-results", "5", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            await update.message.reply_text(f"❌ Fehler bei Google Calendar:\n`{stderr.decode()}`", parse_mode=ParseMode.MARKDOWN)
            return

        events = json.loads(stdout.decode())
        if not events:
            await update.message.reply_text("📅 Keine anstehenden Termine im Kalender gefunden.")
            return

        lines = ["📅 *Nächste anstehende Termine:*\n"]
        for ev in events:
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
            summary = ev.get("summary", "Ohne Titel")
            lines.append(f"• **{start}**: {summary}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"❌ Fehler beim Abrufen des Kalenders: `{e}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        proc = await asyncio.create_subprocess_exec(
            str(PYTHON_BIN), str(GOOGLE_DRIVE_SCRIPT), "list", "--max-results", "10", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            await update.message.reply_text(f"❌ Fehler bei Google Drive:\n`{stderr.decode()}`", parse_mode=ParseMode.MARKDOWN)
            return

        files = json.loads(stdout.decode())
        if not files:
            await update.message.reply_text("📁 Keine Dateien in Google Drive gefunden.")
            return

        lines = ["📁 *Dateien in Google Drive (Auswahl):*\n"]
        for f in files:
            name = f.get("name", "Unbenannt")
            fid = f.get("id", "")
            lines.append(f"• **{name}** (`{fid}`)")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"❌ Fehler beim Abrufen von Google Drive: `{e}`", parse_mode=ParseMode.MARKDOWN)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return

    text = update.effective_message.text or ""
    if not text:
        return

    lower_text = text.lower().strip()
    is_render_intent = False
    render_prompt = ""

    trigger_prefixes = [
        "rendere ein bild von ", "rendere ein bild:", "rendere ",
        "generiere ein bild von ", "generiere ein bild:", "generiere ",
        "erstelle ein bild von ", "zeichne ", "mal mir ", "image:", "prompt:"
    ]
    for prefix in trigger_prefixes:
        if lower_text.startswith(prefix):
            is_render_intent = True
            render_prompt = text[len(prefix):].strip()
            break

    if is_render_intent and render_prompt:
        context.args = render_prompt.split()
        await cmd_render(update, context)
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    user_name = update.effective_user.first_name or "Big Boss"

    try:
        reply, node = await generate_response(text, user_name)
        footer = f"\n\n_(Inferenz: {node})_"
        if "<think>" in reply and "</think>" in reply:
            reply = reply.split("</think>", 1)[1].strip()

        full_text = reply + footer
        if len(full_text) <= 4000:
            await update.message.reply_text(full_text)
        else:
            chunks = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
            for ch in chunks:
                await update.message.reply_text(ch)

    except Exception as e:
        logger.error("Fehler bei Inferenz: %s", e)
        await update.message.reply_text(f"⚠️ Entschuldigung, bei der Verarbeitung ist ein Fehler aufgetreten:\n`{e}`", parse_mode=ParseMode.MARKDOWN)


def main():
    logger.info("🚀 Starte Hermes Telegram Bot Service (Bigbossheaven_bot) auf outerheaven...")

    token = load_token()
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler(["render", "image", "generate", "comfy"], cmd_render))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler(["calendar", "termine", "events"], cmd_calendar))
    app.add_handler(CommandHandler(["drive", "gdrive", "files"], cmd_drive))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Hermes Telegram Bot erfolgreich initialisiert. Polling startet...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
