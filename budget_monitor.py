#!/usr/bin/env python3
"""
budget_monitor.py - Finanz-Radar & Spar-Coach für Hermes / Fox
==============================================================
Verwaltet:
- Budget-Konfiguration (~/.hermes/budget.env)
- Paperless-Dokumentenabfrage des aktuellen Monats (deraldehesse API)
- Berechnung des realen Restgelds:
  MONTHLY_INCOME - Summe(Fixkosten) - Summe(Monatsrechnungen)
- KI-Spar-Coach (llama3.2:3b auf fox:11434) mit beruhigender, deeskalierender Sprache
"""

import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import requests

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [BudgetMonitor]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("BudgetMonitor")

HERMES_DIR = Path(os.path.expanduser("~/.hermes"))
BUDGET_ENV_FILE = HERMES_DIR / "budget.env"

# Standard-Fallbacks falls budget.env noch leer oder unvollständig
DEFAULT_BUDGET_CONFIG = {
    "MONTHLY_INCOME": 1013.00,
    "FIX_RENT": 450.00,
    "FIX_UTILITIES": 65.00,
    "FIX_PHONE": 39.99,
    "FIX_GROCERIES": 250.00
}

PAPERLESS_CANDIDATES = [
    os.getenv("PAPERLESS_URL", "http://100.127.247.117:8000"),
    "http://192.168.188.96:8000",
    "http://127.0.0.1:8000"
]
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "428107bcaa486337ba98b06547d0a843b3007036")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


def get_budget_env_path() -> Path:
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        return BUDGET_ENV_FILE
    except Exception:
        return Path(__file__).resolve().parent / "budget.env"


def load_budget_config() -> Dict[str, float]:
    """Liest die Budget-Werte aus budget.env."""
    config = dict(DEFAULT_BUDGET_CONFIG)
    env_path = get_budget_env_path()
    if env_path.is_file():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        try:
                            config[k] = float(v)
                        except ValueError:
                            pass
        except Exception as e:
            logger.warning("Fehler beim Lesen von %s: %s", env_path, e)
    return config


def save_budget_config(updates: Dict[str, Any]) -> Dict[str, float]:
    """Aktualisiert budget.env mit neuen Werten."""
    config = load_budget_config()
    for k, v in updates.items():
        if k in DEFAULT_BUDGET_CONFIG:
            try:
                config[k] = round(float(v), 2)
            except (ValueError, TypeError):
                pass

    env_path = get_budget_env_path()
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("# Hermes Finanz-Radar Budget Konfiguration\n")
            f.write(f"# Automatisch aktualisiert am: {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write(f"MONTHLY_INCOME={config['MONTHLY_INCOME']:.2f}\n")
            f.write(f"FIX_RENT={config['FIX_RENT']:.2f}\n")
            f.write(f"FIX_UTILITIES={config['FIX_UTILITIES']:.2f}\n")
            f.write(f"FIX_PHONE={config['FIX_PHONE']:.2f}\n")
            f.write(f"FIX_GROCERIES={config['FIX_GROCERIES']:.2f}\n")
        logger.info("Budget-Konfiguration gespeichert in %s", env_path)
    except Exception as e:
        logger.error("Konnte budget.env nicht schreiben: %s", e)
        raise e
    return config


def get_working_paperless_url() -> str:
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}
    for candidate in PAPERLESS_CANDIDATES:
        try:
            r = requests.get(f"{candidate.rstrip('/')}/api/documents/?page_size=1", headers=headers, timeout=2.0)
            if r.status_code in (200, 401, 403):
                return candidate.rstrip('/')
        except Exception:
            continue
    return PAPERLESS_CANDIDATES[0].rstrip('/')


def extract_amount_from_document(doc: Dict[str, Any]) -> float:
    """Extrahiert einen Rechnungsbetrag aus Titel, Notizen oder OCR-Inhalt."""
    text_corpus = f"{doc.get('title', '')}\n{' '.join([n.get('note', '') for n in doc.get('notes', [])])}\n{doc.get('content', '')[:2500]}"
    
    # 1. Betragsangaben wie 123,45 € oder 123.45 EUR
    matches = re.findall(r'(\d+[\.,]\d{2})\s*(?:€|EUR|Euro)', text_corpus, re.IGNORECASE)
    if matches:
        raw = matches[-1].replace('.', '').replace(',', '.') if (',' in matches[-1] and '.' in matches[-1]) else matches[-1].replace(',', '.')
        try:
            val = float(raw)
            if val > 0.0:
                return round(val, 2)
        except ValueError:
            pass

    # 2. Schlüsselwörter mit Betrag
    matches2 = re.findall(r'(?:Gesamtbetrag|Rückstand|Forderung|Rechnungsbetrag|Endbetrag|Betrag)[\s\w:]*?(\d+[\.,]\d{2})', text_corpus, re.IGNORECASE)
    if matches2:
        raw = matches2[-1].replace(',', '.')
        try:
            val = float(raw)
            if val > 0.0:
                return round(val, 2)
        except ValueError:
            pass

    return 0.0


def fetch_current_month_invoices() -> Tuple[List[Dict[str, Any]], float]:
    """Zieht Rechnungen und finanzielle Dokumente des aktuellen Monats aus Paperless."""
    invoices: List[Dict[str, Any]] = []
    base_url = get_working_paperless_url()
    headers = {
        "Authorization": f"Token {PAPERLESS_TOKEN}",
        "Content-Type": "application/json"
    }

    now = datetime.now()
    current_month_prefix = now.strftime("%Y-%m")

    try:
        resp = requests.get(f"{base_url}/api/documents/?page_size=100", headers=headers, timeout=6.0)
        if resp.status_code == 200:
            docs = resp.json().get("results", [])
            for doc in docs:
                created = str(doc.get("created", "") or "")
                added = str(doc.get("added", "") or "")
                title = str(doc.get("title", f"Dokument #{doc.get('id')}"))

                is_current_month = created.startswith(current_month_prefix) or added.startswith(current_month_prefix)
                amount = extract_amount_from_document(doc)

                is_financial = amount > 0.0 or any(k in title.lower() for k in [
                    "rechnung", "mahnung", "forderung", "beitrag", "gebühr", "abrechnung"
                ])

                if is_current_month and is_financial:
                    invoices.append({
                        "id": doc.get("id"),
                        "title": title,
                        "amount": amount,
                        "date": (created[:10] if created else (added[:10] if added else now.strftime("%Y-%m-%d"))),
                        "tags": doc.get("tags", [])
                    })

            # Falls im laufenden Kalendermonat noch keine Rechnungen vorliegen,
            # binden wir jüngste vorhandene Finanzdokumente ein
            if not invoices:
                for doc in docs[:5]:
                    amt = extract_amount_from_document(doc)
                    if amt > 0.0 or any(k in str(doc.get("title", "")).lower() for k in ["rechnung", "mahnung", "forderung"]):
                        invoices.append({
                            "id": doc.get("id"),
                            "title": doc.get("title", f"Dokument #{doc.get('id')}"),
                            "amount": amt,
                            "date": str(doc.get("created", now.strftime("%Y-%m-%d")))[:10],
                            "tags": doc.get("tags", [])
                        })
    except Exception as e:
        logger.error("Fehler beim Abrufen der Paperless-Dokumente: %s", e)

    total_invoices = round(sum(item["amount"] for item in invoices), 2)
    return invoices, total_invoices


def generate_spar_coach_advice(
    income: float,
    fixed_costs: Dict[str, float],
    invoices: List[Dict[str, Any]],
    invoices_total: float,
    remaining_budget: float
) -> str:
    """Ruft llama3.2:3b als beruhigenden KI-Spar-Coach auf."""
    fixed_total = sum(fixed_costs.values())
    invoices_summary = "\n".join([f"- {inv['title']}: {inv['amount']:.2f} €" for inv in invoices]) if invoices else "Keine gesonderten Monatsrechnungen erfasst."

    system_prompt = (
        "Du bist Carola / Hermes, ein sanfter, verständnisvoller und deeskalierender Finanz-Coach für Sebastian.\n"
        "Deine oberste Mission ist es, jegliche Briefkasten- und Finanzphobie abzubauen. Unangenehme Zahlen werden nie vorwurfsvoll oder panisch dargestellt, sondern ruhig, klar und handlungsorientiert.\n"
        "Sprich Sebastian direkt, herzlich und motivierend an. Erkläre kurz und entspannt die aktuelle Finanzlage und nenne 2 konkrete, leicht umsetzbare Spar- oder Organisationstipps (z. B. Handy- oder Stromverträge prüfen, Pauschalen clever einteilen).\n"
        "Halte deine Antwort auf Deutsch in maximal 3 kurzen Absätzen oder klaren Stichpunkten."
    )

    user_prompt = f"""
Aktuelle Monatsübersicht:
- Monatliches Einkommen: {income:.2f} €
- Fixkosten Gesamt: {fixed_total:.2f} € (Miete: {fixed_costs.get('rent', 0):.2f} €, Nebenkosten: {fixed_costs.get('utilities', 0):.2f} €, Handy & Internet: {fixed_costs.get('phone', 0):.2f} €, Lebensmittel: {fixed_costs.get('groceries', 0):.2f} €)
- Zusätzliche Rechnungen im Monat ({invoices_total:.2f} €):
{invoices_summary}
- Reales freies Restgeld: {remaining_budget:.2f} €

Bitte gib Sebastian deine ruhige, unterstützende Einschätzung und konkrete Einsparpotenziale.
"""

    models_to_try = [OLLAMA_MODEL, "llama3.2:3b", "qwen2.5:3b", "hermes3:8b"]
    for model_name in models_to_try:
        try:
            payload = {
                "model": model_name,
                "prompt": user_prompt,
                "system": system_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.4
                }
            }
            resp = requests.post(f"{OLLAMA_URL.rstrip('/')}/api/generate", json=payload, timeout=25.0)
            if resp.status_code == 200:
                answer = resp.json().get("response", "").strip()
                if answer:
                    return answer
        except Exception as e:
            logger.debug("Ollama-Generierung mit %s fehlgeschlagen: %s", model_name, e)

    # Deeskalierender Fallback falls Ollama offline
    if remaining_budget >= 0:
        return (
            f"Alles im grünen Bereich: Nach Abzug aller Fixkosten ({fixed_total:.2f} €) und laufenden Monatsrechnungen "
            f"verbleiben dir real {remaining_budget:.2f} € zur freien Verfügung. "
            f"Tipp: Überprüfe regelmäßig laufende Telekommunikations- und Stromtarife, um noch entspannter Rücklagen zu bilden."
        )
    else:
        deficit = abs(remaining_budget)
        return (
            f"Ruhig bleiben: In diesem Monat liegt eine temporäre Überdeckung von {deficit:.2f} € vor. "
            f"Das ist absolut handhabbar – durch die 1-Tap-Fristverlängerung im Phobie-Schild gewinnen wir die nötige Zeit, "
            f"ohne dass Mahngebühren eskalieren. Wir schauen uns jetzt gemeinsam die variablen Kosten an."
        )


def get_budget_summary() -> Dict[str, Any]:
    """Erstellt die vollständige monatliche Finanzübersicht samt Spar-Coach."""
    config = load_budget_config()
    income = config.get("MONTHLY_INCOME", 1013.00)
    fixed_costs = {
        "rent": config.get("FIX_RENT", 450.00),
        "utilities": config.get("FIX_UTILITIES", 65.00),
        "phone": config.get("FIX_PHONE", 39.99),
        "groceries": config.get("FIX_GROCERIES", 250.00),
    }
    fixed_total = round(sum(fixed_costs.values()), 2)

    invoices, invoices_total = fetch_current_month_invoices()
    remaining_budget = round(income - fixed_total - invoices_total, 2)

    coach_advice = generate_spar_coach_advice(
        income=income,
        fixed_costs=fixed_costs,
        invoices=invoices,
        invoices_total=invoices_total,
        remaining_budget=remaining_budget
    )

    now = datetime.now()
    return {
        "status": "ok",
        "month": now.strftime("%Y-%m"),
        "month_name": now.strftime("%B %Y"),
        "monthly_income": income,
        "fixed_costs": {
            **fixed_costs,
            "total": fixed_total
        },
        "invoices": invoices,
        "invoices_total": invoices_total,
        "remaining_budget": remaining_budget,
        "coach_advice": coach_advice,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }


if __name__ == "__main__":
    summary = get_budget_summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
