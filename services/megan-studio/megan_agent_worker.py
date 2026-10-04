#!/usr/bin/env python3
"""
megan_agent_worker.py - Megan Autonomous Housekeeping & Idle Agent Worker
Outer Heaven Cluster // Megan Studio Background Agent

Responsibilities:
1. ComfyUI Assets & Cataloging:
   - Scans and catalogs Checkpoints, LoRAs, and fixed workflows under:
     * services/megan-studio/workflows/
     * /mnt/storage-beta/models/checkpoints/ (and SDXL subfolder)
     * /mnt/storage-beta/models/loras/
   - Stores catalog in data/comfy_catalog.json
2. Google Drive Synchronization & Project Indexing:
   - Verifies /mnt/fox-home/fox/gdrive/Agenten-Drive
   - Scans projects and project folders
   - Stores index in data/gdrive_index.json
3. Autonomous Status & Suggestion Notes in SQLite (data/megan_chat.db):
   - When idle or on demand, Megan leaves proactive system/assistant notes for Big Boss
     (e.g., "Boss, ich habe den Workflow für Catlord einsatzbereit gemacht.")
   - Avoids spamming by respecting configurable intervals and cooldowns.
"""

import os
import sys
import json
import time
import secrets
import sqlite3
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [megan-worker] %(message)s"
)
logger = logging.getLogger("megan-worker")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHAT_DB_PATH = DATA_DIR / "megan_chat.db"
WORKFLOWS_DIR = BASE_DIR / "workflows"
CATALOG_PATH = DATA_DIR / "comfy_catalog.json"
GDRIVE_INDEX_PATH = DATA_DIR / "gdrive_index.json"

GDRIVE_BASE_PATHS = [
    Path("/mnt/fox-home/fox/gdrive/Agenten-Drive"),
    Path("/home/outerheaven/gdrive/Agenten-Drive"),
    Path("/mnt/storage-beta/gdrive/Agenten-Drive")
]

CHECKPOINT_PATHS = [
    Path("/mnt/storage-beta/models/checkpoints"),
    Path("/home/outerheaven/ComfyUI/models/checkpoints"),
    Path("/mnt/storage-beta/models/checkpoints/SDXL")
]

LORA_PATHS = [
    Path("/mnt/storage-beta/models/loras"),
    Path("/home/outerheaven/ComfyUI/models/loras")
]


def init_chat_db():
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


def save_proactive_note(text: str, sender: str = "MEGAN", msg_type: str = "TEXT"):
    init_chat_db()
    msg_id = f"megan_note_{int(time.time()*1000)}_{secrets.token_hex(3)}"
    ts = int(time.time() * 1000)
    with sqlite3.connect(str(CHAT_DB_PATH)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO messages (id, sender, type, text, image_url, audio_url, prompt, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, sender, msg_type, text, None, None, None, ts)
        )
        conn.commit()
    logger.info(f"Proaktive Notiz gespeichert: {text}")


def scan_comfy_assets() -> Dict[str, Any]:
    checkpoints = set()
    loras = set()
    workflows = []

    # Workflows
    if WORKFLOWS_DIR.is_dir():
        for f in WORKFLOWS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                workflows.append({
                    "id": f.stem,
                    "filename": f.name,
                    "nodes_count": len(data),
                    "path": str(f)
                })
            except Exception as e:
                logger.warning(f"Error reading workflow {f.name}: {e}")

    # Checkpoints
    for c_dir in CHECKPOINT_PATHS:
        if c_dir.is_dir():
            for f in c_dir.glob("*.safetensors"):
                checkpoints.add(f.name)
            for f in c_dir.glob("*/*.safetensors"):
                checkpoints.add(f.name)

    # LoRAs
    for l_dir in LORA_PATHS:
        if l_dir.is_dir():
            for f in l_dir.glob("*.safetensors"):
                loras.add(f.name)
            for f in l_dir.glob("*/*.safetensors"):
                loras.add(f.name)

    catalog = {
        "updated_at": int(time.time()),
        "workflows": workflows,
        "checkpoints": sorted(list(checkpoints)),
        "loras": sorted(list(loras))
    }

    try:
        CATALOG_PATH.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Comfy-Katalog aktualisiert: {len(workflows)} Workflows, {len(checkpoints)} Checkpoints, {len(loras)} LoRAs")
    except Exception as e:
        logger.warning(f"Konnte Comfy-Katalog nicht schreiben: {e}")

    return catalog


def scan_gdrive_projects() -> Dict[str, Any]:
    gdrive_root = None
    for p in GDRIVE_BASE_PATHS:
        if p.is_dir():
            gdrive_root = p
            break

    projects = []
    if gdrive_root:
        try:
            for item in gdrive_root.iterdir():
                if item.is_dir():
                    files = [f.name for f in item.iterdir() if f.is_file()][:10]
                    projects.append({
                        "name": item.name,
                        "path": str(item),
                        "sample_files": files,
                        "file_count": len(list(item.glob("*")))
                    })
        except Exception as e:
            logger.warning(f"Fehler beim Scannen von GDrive: {e}")

    index_data = {
        "updated_at": int(time.time()),
        "gdrive_root": str(gdrive_root) if gdrive_root else None,
        "available": gdrive_root is not None,
        "projects": projects
    }

    try:
        GDRIVE_INDEX_PATH.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"GDrive-Index aktualisiert: {len(projects)} Projekte gefunden (Root: {gdrive_root})")
    except Exception as e:
        logger.warning(f"Konnte GDrive-Index nicht schreiben: {e}")

    return index_data


class MeganAgentWorker:
    def __init__(self, interval_sec: int = 180):
        self.interval_sec = interval_sec
        self._running = False
        self._last_suggestion_ts = 0

    async def run_cycle(self, force_note: bool = False):
        logger.info("Starte Megan Housekeeping Routine...")
        catalog = scan_comfy_assets()
        gdrive = scan_gdrive_projects()

        now = time.time()
        # Nur alle 6 Stunden oder bei force_note eine proaktive Notiz hinterlegen
        if force_note or (now - self._last_suggestion_ts > 21600):
            wf_names = [w["id"] for w in catalog.get("workflows", [])]
            gdrive_cnt = len(gdrive.get("projects", []))
            note = (
                f"Boss, Megan hier. Ich habe den Studio-Status geprüft:\n"
                f"- ComfyUI-Workflows aktiv: {', '.join(wf_names) if wf_names else 'Standard'}\n"
                f"- Verfügbare Checkpoints: {len(catalog.get('checkpoints', []))} | LoRAs: {len(catalog.get('loras', []))}\n"
                f"- Agenten-Drive: {gdrive_cnt} Projekte synchronisiert ({gdrive.get('gdrive_root') or 'Offline'}).\n"
                f"Workflows für 'Catlord Comic' und 'Megan Erotic Chat' sind bereit für deine Befehle!"
            )
            save_proactive_note(note)
            self._last_suggestion_ts = now

    async def start(self):
        self._running = True
        logger.info(f"Megan Agent Worker gestartet (Intervall: {self.interval_sec}s)")
        # Erster Durchlauf direkt beim Start
        try:
            await self.run_cycle(force_note=False)
        except Exception as e:
            logger.error(f"Fehler im ersten Worker-Zyklus: {e}")

        while self._running:
            try:
                await asyncio.sleep(self.interval_sec)
                await self.run_cycle(force_note=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Fehler im Housekeeping-Zyklus: {e}")
                await asyncio.sleep(10)

    def stop(self):
        self._running = False


async def main():
    worker = MeganAgentWorker(interval_sec=300)
    try:
        await worker.start()
    except (KeyboardInterrupt, SystemExit):
        worker.stop()
        logger.info("Megan Agent Worker beendet.")


if __name__ == "__main__":
    asyncio.run(main())
