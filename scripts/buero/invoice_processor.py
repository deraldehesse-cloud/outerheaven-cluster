#!/usr/bin/env python3
"""
invoice_processor.py - Autonomous Invoice Processing Pipeline on Outer Heaven.

Watches /mnt/storage-beta/rechnungseingang/ for incoming invoices (.pdf, .png, .jpg),
extracts text via pypdf / pytesseract, queries local Ollama (deepseek-r1:14b) for structured
JSON data, logs to rechnungen_index.csv, and archives files to /mnt/storage-beta/rechnungsarchiv/YYYY/.
"""

import os
import sys
import re
import csv
import json
import time
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

import requests
from PIL import Image

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

# Base Paths
BASE_STORAGE = Path("/mnt/storage-beta")
INBOX_DIR = BASE_STORAGE / "rechnungseingang"
ARCHIVE_DIR = BASE_STORAGE / "rechnungsarchiv"
INDEX_CSV_PATH = ARCHIVE_DIR / "rechnungen_index.csv"
ERROR_DIR = INBOX_DIR / "fehlerhaft"

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
PRIMARY_MODEL = os.environ.get("INVOICE_MODEL", "deepseek-r1:14b")
FALLBACK_MODEL = os.environ.get("FALLBACK_MODEL", "lexi-llama-3-8b-uncensored_q4_k_m:latest")

SUPPORTED_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [invoice_processor]: %(message)s"
)
logger = logging.getLogger("invoice_processor")


def ensure_environment():
    """Ensures all operational directories and index files exist."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_DIR.mkdir(parents=True, exist_ok=True)

    if not INDEX_CSV_PATH.is_file():
        with open(INDEX_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "erfasst_am",
                "absender",
                "rechnungsnummer",
                "rechnungsdatum",
                "faelligkeitsdatum",
                "gesamtbetrag",
                "iban",
                "verwendungszweck",
                "archiv_pfad",
                "original_name"
            ])
        logger.info(f"Created new invoice index at {INDEX_CSV_PATH}")


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extracts text from PDF using pypdf, falling back to OCR if empty."""
    text_content = []
    if pypdf is not None:
        try:
            reader = pypdf.PdfReader(str(pdf_path))
            for page_idx, page in enumerate(reader.pages):
                txt = page.extract_text() or ""
                if txt.strip():
                    text_content.append(txt.strip())
        except Exception as e:
            logger.warning(f"pypdf text extraction failed for {pdf_path.name}: {e}")

    combined = "\n".join(text_content).strip()
    if len(combined) >= 30:
        return combined

    # If text is too short or empty, attempt OCR if pytesseract is available
    logger.info(f"PDF {pdf_path.name} contains little or no text ({len(combined)} chars). Trying OCR...")
    if pytesseract is not None:
        try:
            import subprocess
            res = subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "2", str(pdf_path), str(pdf_path.with_suffix(""))],
                capture_output=True, timeout=30
            )
            ocr_texts = []
            for rendered in sorted(pdf_path.parent.glob(f"{pdf_path.stem}-*.png")):
                try:
                    img = Image.open(rendered)
                    ocr_txt = pytesseract.image_to_string(img, lang="eng")
                    ocr_texts.append(ocr_txt)
                finally:
                    if rendered.is_file():
                        rendered.unlink()
            if ocr_texts:
                return "\n".join(ocr_texts).strip()
        except Exception as ocr_err:
            logger.warning(f"PDF OCR fallback failed: {ocr_err}")

    return combined


def extract_text_from_image(img_path: Path) -> str:
    """Extracts text from an image scan using pytesseract."""
    if pytesseract is None:
        logger.error("pytesseract is not installed; cannot OCR image.")
        return ""
    try:
        img = Image.open(img_path)
        text = pytesseract.image_to_string(img, lang="eng")
        return text.strip()
    except Exception as e:
        logger.error(f"OCR failed for image {img_path.name}: {e}")
        return ""


def clean_llm_json_response(raw_text: str) -> Dict[str, Any]:
    """Strips think tags, markdown code blocks, and extracts valid JSON."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass

    logger.warning(f"Could not parse raw LLM JSON: {raw_text[:200]}...")
    return {}


def query_ollama_for_invoice_data(raw_text: str) -> Dict[str, Any]:
    """Queries local Ollama to extract structured invoice JSON."""
    prompt = f"""Du bist ein automatisiertes Rechnungs-Erfassungssystem.
Analysiere den folgenden Rechnungs-Text und extrahiere die Rechnungsdaten exakt.
Antworte STRIKT und AUSSCHLIESSLICH im folgenden JSON-Format ohne weiteren Text oder Markdown:

{{
  "absender": "Firmenname",
  "rechnungsnummer": "...",
  "rechnungsdatum": "YYYY-MM-DD",
  "faelligkeitsdatum": "YYYY-MM-DD",
  "gesamtbetrag": 0.00,
  "iban": "...",
  "verwendungszweck": "..."
}}

Regeln:
- Datumsangaben immer im Format YYYY-MM-DD formatieren. Falls unbekannt, nimm das aktuelle Datum.
- gesamtbetrag muss eine Float-Zahl mit Punkt als Dezimaltrenner sein (z.B. 49.99).
- iban als String ohne Leerzeichen.
- Falls ein Wert im Text absolut nicht zu finden ist, setze einen leeren String bzw. 0.00.

Rechnungstext:
\"\"\"{raw_text[:3500]}\"\"\"
"""

    models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL, "darkidol-8b:latest"]
    last_err = None

    for model in models_to_try:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise JSON document extractor. Output valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 600}
        }

        try:
            logger.info(f"Querying Ollama model '{model}' for invoice extraction...")
            res = requests.post(OLLAMA_URL, json=payload, timeout=90)
            if res.status_code == 200:
                data = res.json()
                raw_out = data.get("message", {}).get("content", "")
                parsed = clean_llm_json_response(raw_out)
                if parsed and parsed.get("absender"):
                    logger.info(f"✔ Successfully extracted invoice data via {model}")
                    return parsed
            elif res.status_code == 404:
                logger.debug(f"Model {model} not found in Ollama, trying next...")
                continue
        except Exception as e:
            last_err = e
            logger.warning(f"Ollama query error with {model}: {e}")

    logger.error(f"All LLM attempts failed: {last_err}")
    return {}


def sanitize_filename_part(text: str) -> str:
    """Converts arbitrary string into safe filename token."""
    clean = re.sub(r"[^\w\-]+", "_", str(text).strip(), flags=re.UNICODE)
    return clean.strip("_")[:40] or "Unbekannt"


def process_single_invoice(file_path: Path) -> bool:
    """Processes a single incoming invoice file."""
    logger.info(f"=== Processing invoice: {file_path.name} ===")
    ext = file_path.suffix.lower()

    # 1. Extract text
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path)
    else:
        text = extract_text_from_image(file_path)

    if not text.strip():
        logger.warning(f"No text could be extracted from {file_path.name}. Moving to fehlerhaft.")
        shutil.move(str(file_path), str(ERROR_DIR / file_path.name))
        return False

    logger.info(f"Extracted {len(text)} characters of text from {file_path.name}.")

    # 2. Extract structured data via Ollama
    data = query_ollama_for_invoice_data(text)
    if not data or not data.get("absender"):
        logger.warning(f"Could not extract structured data for {file_path.name}. Fallback heuristic...")
        absender = sanitize_filename_part(file_path.stem)
        rechnungsdatum = datetime.now().strftime("%Y-%m-%d")
        data = {
            "absender": absender,
            "rechnungsnummer": "OH-MANUAL-" + datetime.now().strftime("%Y%m%d%H%M"),
            "rechnungsdatum": rechnungsdatum,
            "faelligkeitsdatum": rechnungsdatum,
            "gesamtbetrag": 0.00,
            "iban": "",
            "verwendungszweck": file_path.stem
        }

    # 3. Format Date & Target Paths
    raw_date = str(data.get("rechnungsdatum", "")).strip()
    try:
        parsed_date = datetime.strptime(raw_date[:10], "%Y-%m-%d")
    except Exception:
        parsed_date = datetime.now()

    year_str = parsed_date.strftime("%Y")
    month_str = parsed_date.strftime("%m")
    safe_absender = sanitize_filename_part(str(data.get("absender", "Absender")))
    safe_orig_name = sanitize_filename_part(file_path.stem)

    target_year_dir = ARCHIVE_DIR / year_str
    target_year_dir.mkdir(parents=True, exist_ok=True)

    target_filename = f"{month_str}_{safe_absender}_{safe_orig_name}{ext}"
    target_path = target_year_dir / target_filename

    counter = 1
    while target_path.exists():
        target_filename = f"{month_str}_{safe_absender}_{safe_orig_name}_{counter}{ext}"
        target_path = target_year_dir / target_filename
        counter += 1

    # 4. Move file to archive
    shutil.move(str(file_path), str(target_path))
    logger.info(f"Archived: {file_path.name} -> {target_path}")

    # 5. Append to CSV Index
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rel_archive_path = f"{year_str}/{target_filename}"

    with open(INDEX_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            now_str,
            str(data.get("absender", "")).strip(),
            str(data.get("rechnungsnummer", "")).strip(),
            parsed_date.strftime("%Y-%m-%d"),
            str(data.get("faelligkeitsdatum", "")).strip(),
            f"{float(data.get('gesamtbetrag', 0.0)):.2f}",
            str(data.get("iban", "")).strip(),
            str(data.get("verwendungszweck", "")).strip(),
            rel_archive_path,
            file_path.name
        ])

    logger.info(f"✔ Invoice successfully indexed: {data.get('absender')} - {data.get('gesamtbetrag')} EUR")
    return True


def run_pipeline(once: bool = False, poll_interval: int = 10):
    """Runs the invoice processing pipeline."""
    ensure_environment()
    logger.info(f"Invoice pipeline active. Watching {INBOX_DIR}...")

    while True:
        candidates = [
            f for f in INBOX_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS and f.parent != ERROR_DIR
        ]

        if candidates:
            logger.info(f"Found {len(candidates)} incoming invoice(s).")
            for c in candidates:
                try:
                    process_single_invoice(c)
                except Exception as proc_err:
                    logger.error(f"Error processing {c.name}: {proc_err}")

        if once:
            logger.info("Run once completed.")
            break

        time.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Invoice Processing Pipeline")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")
    args = parser.parse_args()

    run_pipeline(once=args.once, poll_interval=args.interval)
