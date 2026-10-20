#!/usr/bin/env python3
"""
budget_service.py - Budget & Rechnungs-Manager Service Backend for Fox.

Provides REST API endpoints for:
- GET  /api/budget: Incomes, fixed costs, dynamic Paperless invoices, calculated restbudget
- POST /api/budget/status: Toggle or set status (offen/bezahlt) and sync Paperless tags via REST
- POST /api/budget/update: Update incomes or fixed costs in budget.json
"""

import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import requests
from fastapi import APIRouter, HTTPException, Body

logger = logging.getLogger("budget_service")

# Paths and URLs
BASE_DIR = Path(__file__).resolve().parent
BUDGET_JSON_PATH = BASE_DIR / "budget.json"
PAPERLESS_URL = os.getenv("PAPERLESS_URL", "http://127.0.0.1:8000")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "e1ea8674359e0979eece9a5335cd4d49fa709ed")

router = APIRouter(prefix="/api/budget", tags=["budget"])


class StatusUpdateRequest(BaseModel):
    id: Any
    status: Optional[str] = None  # "offen" or "bezahlt", or None to toggle


class BudgetUpdateRequest(BaseModel):
    einnahmen: Optional[List[Dict[str, Any]]] = None
    fixkosten: Optional[List[Dict[str, Any]]] = None


def get_paperless_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Token {PAPERLESS_TOKEN}",
        "Content-Type": "application/json"
    }


def load_budget_data() -> Dict[str, Any]:
    if BUDGET_JSON_PATH.is_file():
        try:
            with open(BUDGET_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {BUDGET_JSON_PATH}: {e}")
    
    # Default fallback template
    return {
        "einnahmen": [
            {"id": "e1", "bezeichnung": "Bürgergeld / ALG II (Regelbedarf)", "betrag": 563.00, "faelligkeitstag": 1},
            {"id": "e2", "bezeichnung": "Kosten der Unterkunft (Mietzuschuss)", "betrag": 450.00, "faelligkeitstag": 1}
        ],
        "fixkosten": [
            {"id": "f1", "bezeichnung": "Miete (Dachwohnung)", "betrag": 450.00, "faelligkeitstag": 1},
            {"id": "f2", "bezeichnung": "Stromabschlag", "betrag": 65.00, "faelligkeitstag": 15},
            {"id": "f3", "bezeichnung": "Internet & Mobilfunk", "betrag": 39.99, "faelligkeitstag": 10},
            {"id": "f4", "bezeichnung": "Haftpflichtversicherung", "betrag": 7.50, "faelligkeitstag": 1}
        ],
        "rechnungs_overrides": {},
        "letzte_aktualisierung": datetime.now().isoformat()
    }


def save_budget_data(data: Dict[str, Any]):
    data["letzte_aktualisierung"] = datetime.now().isoformat()
    try:
        with open(BUDGET_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving {BUDGET_JSON_PATH}: {e}")


def get_or_create_paperless_tags() -> Dict[str, int]:
    """Ensures 'offen' and 'bezahlt' tags exist in Paperless and returns {name: id}."""
    tags_map = {}
    headers = get_paperless_headers()
    try:
        res = requests.get(f"{PAPERLESS_URL}/api/tags/", headers=headers, timeout=5)
        if res.status_code == 200:
            for item in res.json().get("results", []):
                name = item.get("name", "").strip().lower()
                tags_map[name] = item.get("id")

        for req_tag in ["offen", "bezahlt"]:
            if req_tag not in tags_map:
                create_res = requests.post(
                    f"{PAPERLESS_URL}/api/tags/",
                    headers=headers,
                    json={"name": req_tag},
                    timeout=5
                )
                if create_res.status_code in (200, 201):
                    tags_map[req_tag] = create_res.json().get("id")
    except Exception as e:
        logger.warning(f"Failed to query/create Paperless tags: {e}")
        # Default known IDs from setup
        tags_map.setdefault("offen", 1)
        tags_map.setdefault("bezahlt", 2)

    return tags_map


def extract_amount_from_text(text: str) -> float:
    """Extracts Euro amount from OCR text or notes."""
    # Look for patterns like 704,28 € or 57.90 EUR or Gesamtrückstand 704,28
    matches = re.findall(r'(\d+[\.,]\d{2})\s*(?:€|EUR|Euro)', text, re.IGNORECASE)
    if matches:
        val_str = matches[-1].replace('.', '').replace(',', '.') if ',' in matches[-1] and '.' in matches[-1] else matches[-1].replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            pass
    # Secondary check for 'Rückstand: 123,45' or 'Gesamtbetrag: 123.45'
    matches2 = re.findall(r'(?:Gesamtbetrag|Rückstand|Forderung|Betrag)[\s\w:]*?(\d+[\.,]\d{2})', text, re.IGNORECASE)
    if matches2:
        val_str = matches2[-1].replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            pass
    return 0.0


def fetch_paperless_invoices(tags_map: Dict[str, int], overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetches documents from Paperless and normalizes them into invoices."""
    invoices = []
    headers = get_paperless_headers()
    tag_offen_id = tags_map.get("offen", 1)
    tag_bezahlt_id = tags_map.get("bezahlt", 2)

    try:
        res = requests.get(f"{PAPERLESS_URL}/api/documents/", headers=headers, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results", [])
            for doc in results:
                doc_id = str(doc.get("id"))
                title = doc.get("title", f"Dokument #{doc_id}")
                doc_tags = doc.get("tags", [])
                created = doc.get("created", "")
                content = doc.get("content", "")
                notes_text = " ".join([n.get("note", "") for n in doc.get("notes", [])])

                # Determine status
                if tag_bezahlt_id in doc_tags:
                    status = "bezahlt"
                elif tag_offen_id in doc_tags:
                    status = "offen"
                else:
                    # Check override or default to offen
                    status = "offen"

                # Check if this document is an invoice/bill or has overrides
                override = overrides.get(doc_id, {})
                bezeichnung = override.get("bezeichnung", title)
                betrag = override.get("betrag")
                faelligkeit = override.get("faelligkeit", created)

                # If amount not overridden, try extracting from content/notes
                if betrag is None:
                    betrag = extract_amount_from_text(f"{notes_text}\n{content[:1500]}")

                # Filter out pure noise / invitations that are explicitly 0 EUR unless explicitly in overrides or tagged
                is_financial = (
                    doc_id in overrides or
                    tag_offen_id in doc_tags or
                    tag_bezahlt_id in doc_tags or
                    any(k in title.lower() for k in ["rechnung", "mahnung", "festsetzung", "pfändung", "rückstand", "beitrag", "bescheid"])
                )

                if is_financial:
                    invoices.append({
                        "id": int(doc_id) if doc_id.isdigit() else doc_id,
                        "paperless_id": int(doc_id) if doc_id.isdigit() else doc_id,
                        "bezeichnung": bezeichnung,
                        "title": title,
                        "betrag": round(float(betrag), 2),
                        "faelligkeit": faelligkeit,
                        "status": status,
                        "tags": doc_tags
                    })
    except Exception as e:
        logger.error(f"Error querying Paperless documents: {e}")
        # Return fallback items from overrides
        for doc_id, item in overrides.items():
            invoices.append({
                "id": int(doc_id) if doc_id.isdigit() else doc_id,
                "paperless_id": int(doc_id) if doc_id.isdigit() else doc_id,
                "bezeichnung": item.get("bezeichnung", f"Dokument #{doc_id}"),
                "title": item.get("bezeichnung", f"Dokument #{doc_id}"),
                "betrag": round(float(item.get("betrag", 0.0)), 2),
                "faelligkeit": item.get("faelligkeit", ""),
                "status": "offen",
                "tags": [tag_offen_id]
            })

    # Sort invoices: open first, then by due date or id descending
    invoices.sort(key=lambda x: (x["status"] == "bezahlt", str(x.get("faelligkeit", ""))), reverse=False)
    return invoices


def calculate_budget(budget_data: Dict[str, Any], invoices: List[Dict[str, Any]]) -> Dict[str, Any]:
    einnahmen = budget_data.get("einnahmen", [])
    fixkosten = budget_data.get("fixkosten", [])

    sum_einnahmen = round(sum(float(e.get("betrag", 0.0)) for e in einnahmen), 2)
    sum_fixkosten = round(sum(float(f.get("betrag", 0.0)) for f in fixkosten), 2)
    sum_offen = round(sum(float(i.get("betrag", 0.0)) for i in invoices if i.get("status") == "offen"), 2)
    sum_bezahlt = round(sum(float(i.get("betrag", 0.0)) for i in invoices if i.get("status") == "bezahlt"), 2)

    # Restbudget (Puffer) = Einnahmen - Fixkosten - offene Rechnungen
    restbudget = round(sum_einnahmen - sum_fixkosten - sum_offen, 2)

    return {
        "summe_einnahmen": sum_einnahmen,
        "summe_fixkosten": sum_fixkosten,
        "summe_rechnungen_offen": sum_offen,
        "summe_rechnungen_bezahlt": sum_bezahlt,
        "restbudget": restbudget
    }


@router.get("")
@router.get("/")
def get_budget():
    """Returns budget summary, income, fixed costs, and live Paperless invoices."""
    budget_data = load_budget_data()
    overrides = budget_data.get("rechnungs_overrides", {})
    tags_map = get_or_create_paperless_tags()
    invoices = fetch_paperless_invoices(tags_map, overrides)
    kalkulation = calculate_budget(budget_data, invoices)

    return {
        "status": "ok",
        "einnahmen": budget_data.get("einnahmen", []),
        "fixkosten": budget_data.get("fixkosten", []),
        "rechnungen": invoices,
        "kalkulation": kalkulation,
        "letzte_aktualisierung": datetime.now().isoformat()
    }


@router.post("/status")
def update_invoice_status(req: StatusUpdateRequest = Body(...)):
    """Toggles or sets the invoice status in Paperless tags and local budget state."""
    doc_id = str(req.id)
    target_status = req.status.lower() if req.status else None
    tags_map = get_or_create_paperless_tags()
    tag_offen_id = tags_map.get("offen", 1)
    tag_bezahlt_id = tags_map.get("bezahlt", 2)

    headers = get_paperless_headers()
    current_tags = []

    # 1. Fetch current document from Paperless
    try:
        get_res = requests.get(f"{PAPERLESS_URL}/api/documents/{doc_id}/", headers=headers, timeout=5)
        if get_res.status_code == 200:
            doc_data = get_res.json()
            current_tags = doc_data.get("tags", [])
        else:
            logger.warning(f"Document {doc_id} not found in Paperless (status {get_res.status_code})")
    except Exception as e:
        logger.error(f"Error fetching document {doc_id} from Paperless: {e}")

    # Determine current status
    is_currently_paid = tag_bezahlt_id in current_tags

    if target_status is None:
        new_status = "offen" if is_currently_paid else "bezahlt"
    else:
        new_status = "bezahlt" if target_status in ("bezahlt", "paid", "true") else "offen"

    # Update tag list
    new_tags = [t for t in current_tags if t not in (tag_offen_id, tag_bezahlt_id)]
    if new_status == "bezahlt":
        new_tags.append(tag_bezahlt_id)
    else:
        new_tags.append(tag_offen_id)

    # 2. Patch Paperless document
    paperless_success = False
    try:
        patch_res = requests.patch(
            f"{PAPERLESS_URL}/api/documents/{doc_id}/",
            headers=headers,
            json={"tags": new_tags},
            timeout=5
        )
        if patch_res.status_code == 200:
            paperless_success = True
    except Exception as e:
        logger.error(f"Error patching document {doc_id} in Paperless: {e}")

    # 3. Update budget data
    budget_data = load_budget_data()
    overrides = budget_data.setdefault("rechnungs_overrides", {})
    if doc_id in overrides:
        overrides[doc_id]["status"] = new_status
    save_budget_data(budget_data)

    # 4. Return fresh budget overview
    invoices = fetch_paperless_invoices(tags_map, overrides)
    kalkulation = calculate_budget(budget_data, invoices)

    return {
        "status": "ok",
        "id": int(doc_id) if doc_id.isdigit() else doc_id,
        "new_status": new_status,
        "paperless_synced": paperless_success,
        "kalkulation": kalkulation,
        "rechnungen": invoices
    }


@router.post("/update")
def update_budget(req: BudgetUpdateRequest = Body(...)):
    """Updates income or fixed costs."""
    budget_data = load_budget_data()
    if req.einnahmen is not None:
        budget_data["einnahmen"] = req.einnahmen
    if req.fixkosten is not None:
        budget_data["fixkosten"] = req.fixkosten
    save_budget_data(budget_data)

    tags_map = get_or_create_paperless_tags()
    overrides = budget_data.get("rechnungs_overrides", {})
    invoices = fetch_paperless_invoices(tags_map, overrides)
    kalkulation = calculate_budget(budget_data, invoices)

    return {
        "status": "ok",
        "einnahmen": budget_data["einnahmen"],
        "fixkosten": budget_data["fixkosten"],
        "kalkulation": kalkulation
    }
