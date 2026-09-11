#!/usr/bin/env python3
"""
application_generator.py - Autonomous Application & Cover Letter Pipeline on Outer Heaven.

Watches /mnt/storage-beta/jobangebote/ for incoming job postings (.txt, .pdf, .md),
matches against candidate master data in /mnt/storage-beta/bewerbungstresor/ (lebenslauf.md, stil_profil.md),
synthesizes a tailored, high-impact German cover letter via local Ollama (deepseek-r1:14b),
exports to /mnt/storage-beta/bewerbungsausgang/anschreiben_[Firma]_[Rolle].md,
and moves the job posting to /mnt/storage-beta/jobangebote/erledigt/.
"""

import os
import sys
import re
import json
import time
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

import requests

try:
    import pypdf
except ImportError:
    pypdf = None

# Base Paths
BASE_STORAGE = Path("/mnt/storage-beta")
TRESOR_DIR = BASE_STORAGE / "bewerbungstresor"
JOBS_INBOX_DIR = BASE_STORAGE / "jobangebote"
JOBS_DONE_DIR = JOBS_INBOX_DIR / "erledigt"
OUTPUT_DIR = BASE_STORAGE / "bewerbungsausgang"

CV_FILE = TRESOR_DIR / "lebenslauf.md"
STYLE_FILE = TRESOR_DIR / "stil_profil.md"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
PRIMARY_MODEL = os.environ.get("APP_MODEL", "deepseek-r1:14b")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "lexi-llama-3-8b-uncensored_q4_k_m:latest")

SUPPORTED_EXTS = {".txt", ".pdf", ".md"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [app_generator]: %(message)s"
)
logger = logging.getLogger("app_generator")


def ensure_environment():
    """Ensures directories exist."""
    TRESOR_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DONE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def read_text_from_file(file_path: Path) -> str:
    """Extracts text from txt, md or pdf file."""
    ext = file_path.suffix.lower()
    if ext in [".txt", ".md"]:
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception as e:
            logger.error(f"Failed to read text file {file_path.name}: {e}")
            return ""

    elif ext == ".pdf" and pypdf is not None:
        try:
            reader = pypdf.PdfReader(str(file_path))
            pages = [p.extract_text() or "" for p in reader.pages]
            return "\n".join(pages).strip()
        except Exception as e:
            logger.error(f"Failed to read PDF {file_path.name}: {e}")
            return ""

    return ""


def load_master_profile() -> Tuple[str, str]:
    """Loads master CV and style profile from bewerbungstresor."""
    cv_text = ""
    style_text = ""

    if CV_FILE.is_file():
        cv_text = CV_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    else:
        logger.warning(f"Master CV not found at {CV_FILE}")

    if STYLE_FILE.is_file():
        style_text = STYLE_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    else:
        logger.warning(f"Style profile not found at {STYLE_FILE}")

    return cv_text, style_text


def sanitize_token(text: str) -> str:
    """Cleans names for filenames."""
    clean = re.sub(r"[^\w\-]+", "_", str(text).strip(), flags=re.UNICODE)
    return clean.strip("_")[:35] or "Unbekannt"


def extract_job_meta_and_letter(job_text: str, cv_text: str, style_text: str) -> Tuple[str, str, str]:
    """
    Queries Ollama to extract company & role, and generate the tailored cover letter.
    Returns: (firma, rolle, cover_letter_markdown)
    """
    today_str = datetime.now().strftime("%d. %B %Y")

    system_prompt = (
        "Du bist ein hochkarätiger Executive Career Coach und professioneller Bewerbungsschreiber. "
        "Deine Aufgabe ist es, auf Basis der Stellenanzeige und des Kandidatenprofils ein maßgeschneidertes, "
        "kraftvolles und DIN 5008-konformes Anschreiben zu verfassen. "
        "Verwende niemals veraltete Floskeln. Hebe den konkreten Mehrwert und die technischen Kernkompetenzen hervor. "
        "Halte dich strikt an die Tatsachen aus dem Lebenslauf."
    )

    user_prompt = f"""
### 1. KANDIDATEN-LEBENSLAUF:
{cv_text}

### 2. STIL- & TONALITÄTS-RICHTLINIEN:
{style_text}

### 3. STELLENANZEIGE:
{job_text[:4000]}

---
### AUFGABE:
1. Extrahiere als erste zwei Zeilen genau in diesem Format:
FIRMA: [Name des Unternehmens]
ROLLE: [Bezeichnung der Stelle / Position]

2. Verfasse danach das vollständige Anschreiben auf Deutsch als Markdown-Dokument:
- Datum: {today_str}
- Betreff: Fettgedruckt mit Referenz / Stellentitel
- Persönliche und ansprechende Einleitung (Hook)
- 2 bis 3 substanzielle Absätze, die konkrete Erfahrungen aus dem Lebenslauf mit den Anforderungen der Stelle verknüpfen (Mehrwert, Technologien, Macher-Mentalität)
- Motivierender Abschluss mit Verweis auf persönliches Kennenlernen
- Professionelle Grußformel mit Kandidaten-Namen
"""

    models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL, "darkidol-8b:latest"]
    raw_response = ""

    for model in models_to_try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 1800}
        }

        try:
            logger.info(f"Generating tailored cover letter via Ollama model '{model}'...")
            res = requests.post(OLLAMA_URL, json=payload, timeout=120)
            if res.status_code == 200:
                data = res.json()
                raw_response = data.get("message", {}).get("content", "").strip()
                if raw_response:
                    logger.info(f"✔ Received cover letter from {model}")
                    break
        except Exception as e:
            logger.warning(f"Ollama generation failed with {model}: {e}")

    # Strip thinking tags if present
    cleaned = re.sub(r"<think>.*?</think>", "", raw_response, flags=re.DOTALL).strip()

    # Extract FIRMA and ROLLE
    firma = "Firma"
    rolle = "Position"

    f_match = re.search(r"^FIRMA:\s*(.*?)$", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    r_match = re.search(r"^ROLLE:\s*(.*?)$", cleaned, flags=re.MULTILINE | re.IGNORECASE)

    if f_match:
        firma = f_match.group(1).strip()
    if r_match:
        rolle = r_match.group(1).strip()

    # Clean out the FIRMA/ROLLE header from the letter content
    letter_body = re.sub(r"^FIRMA:.*?$", "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    letter_body = re.sub(r"^ROLLE:.*?$", "", letter_body, flags=re.MULTILINE | re.IGNORECASE).strip()

    return firma, rolle, letter_body


def process_single_job_offer(file_path: Path) -> bool:
    """Processes a single incoming job posting."""
    logger.info(f"=== Processing job posting: {file_path.name} ===")
    job_text = read_text_from_file(file_path)

    if not job_text:
        logger.warning(f"Job posting {file_path.name} is empty or unreadable. Skipping.")
        return False

    cv_text, style_text = load_master_profile()
    if not cv_text:
        logger.error("Cannot proceed without master CV in bewerbungstresor.")
        return False

    # Generate tailored application
    firma, rolle, letter_content = extract_job_meta_and_letter(job_text, cv_text, style_text)
    safe_firma = sanitize_token(firma)
    safe_rolle = sanitize_token(rolle)

    out_filename = f"anschreiben_{safe_firma}_{safe_rolle}.md"
    out_path = OUTPUT_DIR / out_filename

    # Save cover letter
    out_path.write_text(letter_content, encoding="utf-8")
    logger.info(f"✔ Cover letter saved to {out_path}")

    # Move processed job posting to erledigt/
    target_done_path = JOBS_DONE_DIR / file_path.name
    counter = 1
    while target_done_path.exists():
        target_done_path = JOBS_DONE_DIR / f"{file_path.stem}_{counter}{file_path.suffix}"
        counter += 1

    shutil.move(str(file_path), str(target_done_path))
    logger.info(f"Moved job offer to {target_done_path}")
    return True


def run_pipeline(once: bool = False, poll_interval: int = 15):
    """Runs the application generator pipeline."""
    ensure_environment()
    logger.info(f"Application pipeline active. Watching {JOBS_INBOX_DIR}...")

    while True:
        candidates = [
            f for f in JOBS_INBOX_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS and f.parent != JOBS_DONE_DIR
        ]

        if candidates:
            logger.info(f"Found {len(candidates)} new job posting(s).")
            for c in candidates:
                try:
                    process_single_job_offer(c)
                except Exception as proc_err:
                    logger.error(f"Error processing {c.name}: {proc_err}")

        if once:
            logger.info("Run once completed.")
            break

        time.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Application & Cover Letter Pipeline")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=15, help="Poll interval in seconds")
    args = parser.parse_args()

    run_pipeline(once=args.once, poll_interval=args.interval)
