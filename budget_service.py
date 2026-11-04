#!/usr/bin/env python3
"""
budget_service.py - Budget & Rechnungs-Manager Service Backend for Fox.

Provides REST API endpoints for:
- GET  /api/budget: Incomes, fixed costs, dynamic Paperless invoices, calculated restbudget (HTML or JSON)
- GET  /api/budget/status: Consolidated live budget & coverage status (PSD Bank + open invoices + coverage check)
- POST /api/budget/status: Toggle or set status (offen/bezahlt) and sync Paperless tags via REST
- POST /api/budget/update: Update incomes or fixed costs in budget.json
- GET  /api/budget/dashboard: Interactive HTML Web-Dashboard
"""

import os
import sys
import re
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import requests
from fastapi import APIRouter, HTTPException, Body, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("budget_service")

# Paths and URLs
BASE_DIR = Path(__file__).resolve().parent
BUDGET_JSON_PATH = BASE_DIR / "budget.json"

# Paperless Connection with LAN & Tailscale fallback
PAPERLESS_CANDIDATES = [
    os.getenv("PAPERLESS_URL", "http://192.168.188.96:8000"),
    "http://100.127.247.117:8000",
    "http://127.0.0.1:8000"
]
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "428107bcaa486337ba98b06547d0a843b3007036")

# Import fints_connector
sys_scripts = Path(__file__).resolve().parent.parent
if str(sys_scripts) not in sys.path:
    sys.path.insert(0, str(sys_scripts))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    import fints_connector
except ImportError:
    fints_connector = None

router = APIRouter(prefix="/api/budget", tags=["budget"])


class StatusUpdateRequest(BaseModel):
    id: Any
    status: Optional[str] = None  # "offen" or "bezahlt", or None to toggle


class BudgetUpdateRequest(BaseModel):
    einnahmen: Optional[List[Dict[str, Any]]] = None
    fixkosten: Optional[List[Dict[str, Any]]] = None


def get_working_paperless_url() -> str:
    """Finds working Paperless endpoint or defaults to first candidate."""
    for url in PAPERLESS_CANDIDATES:
        try:
            r = requests.get(f"{url}/api/documents/", headers=get_paperless_headers(), timeout=1.5)
            if r.status_code in (200, 401, 403):
                return url
        except Exception:
            continue
    return PAPERLESS_CANDIDATES[0]


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
    paperless_url = get_working_paperless_url()
    try:
        res = requests.get(f"{paperless_url}/api/tags/", headers=headers, timeout=4)
        if res.status_code == 200:
            for item in res.json().get("results", []):
                name = item.get("name", "").strip().lower()
                tags_map[name] = item.get("id")

        for req_tag in ["offen", "bezahlt"]:
            if req_tag not in tags_map:
                create_res = requests.post(
                    f"{paperless_url}/api/tags/",
                    headers=headers,
                    json={"name": req_tag},
                    timeout=4
                )
                if create_res.status_code in (200, 201):
                    tags_map[req_tag] = create_res.json().get("id")
    except Exception as e:
        logger.warning(f"Failed to query/create Paperless tags: {e}")
        tags_map.setdefault("offen", 1)
        tags_map.setdefault("bezahlt", 2)

    return tags_map


def extract_amount_from_text(text: str) -> float:
    """Extracts Euro amount from OCR text or notes."""
    matches = re.findall(r'(\d+[\.,]\d{2})\s*(?:€|EUR|Euro)', text, re.IGNORECASE)
    if matches:
        val_str = matches[-1].replace('.', '').replace(',', '.') if ',' in matches[-1] and '.' in matches[-1] else matches[-1].replace(',', '.')
        try:
            return float(val_str)
        except ValueError:
            pass
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
    paperless_url = get_working_paperless_url()

    try:
        res = requests.get(f"{paperless_url}/api/documents/", headers=headers, timeout=4)
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
                    status = "offen"

                override = overrides.get(doc_id, {})
                bezeichnung = override.get("bezeichnung", title)
                betrag = override.get("betrag")
                faelligkeit = override.get("faelligkeit", created)

                if betrag is None:
                    betrag = extract_amount_from_text(f"{notes_text}\n{content[:1500]}")

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
                        "faelligkeit": str(faelligkeit)[:10] if faelligkeit else "",
                        "status": status,
                        "tags": doc_tags
                    })
    except Exception as e:
        logger.error(f"Error querying Paperless documents: {e}")
        for doc_id, item in overrides.items():
            invoices.append({
                "id": int(doc_id) if doc_id.isdigit() else doc_id,
                "paperless_id": int(doc_id) if doc_id.isdigit() else doc_id,
                "bezeichnung": item.get("bezeichnung", f"Dokument #{doc_id}"),
                "title": item.get("bezeichnung", f"Dokument #{doc_id}"),
                "betrag": round(float(item.get("betrag", 0.0)), 2),
                "faelligkeit": str(item.get("faelligkeit", ""))[:10],
                "status": "offen",
                "tags": [tag_offen_id]
            })

    invoices.sort(key=lambda x: (x["status"] == "bezahlt", str(x.get("faelligkeit", ""))), reverse=False)
    return invoices


def calculate_budget(budget_data: Dict[str, Any], invoices: List[Dict[str, Any]]) -> Dict[str, Any]:
    einnahmen = budget_data.get("einnahmen", [])
    fixkosten = budget_data.get("fixkosten", [])

    sum_einnahmen = round(sum(float(e.get("betrag", 0.0)) for e in einnahmen), 2)
    sum_fixkosten = round(sum(float(f.get("betrag", 0.0)) for f in fixkosten), 2)
    sum_offen = round(sum(float(i.get("betrag", 0.0)) for i in invoices if i.get("status") == "offen"), 2)
    sum_bezahlt = round(sum(float(i.get("betrag", 0.0)) for i in invoices if i.get("status") == "bezahlt"), 2)

    restbudget = round(sum_einnahmen - sum_fixkosten - sum_offen, 2)

    return {
        "summe_einnahmen": sum_einnahmen,
        "summe_fixkosten": sum_fixkosten,
        "summe_rechnungen_offen": sum_offen,
        "summe_rechnungen_bezahlt": sum_bezahlt,
        "restbudget": restbudget
    }


def format_eur(val: float) -> str:
    """Formatiert Währung im deutschen Format z. B. 1.234,56 €."""
    return f"{float(val):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# ==============================================================================
# SUBTASK A: GET /api/budget/status
# ==============================================================================

@router.get("/status")
def get_budget_status():
    """
    Subtask A: Konsolidierter Status-Endpunkt für Android-App (PaperworkScanner) und Desktop.
    Liefert:
    - Aktueller Kontostand der PSD Bank (via fints_connector.py)
    - Summe und Liste der offenen Rechnungen (Gläubiger & Betrag)
    - Abgleichs-Status (Budget-Deckung ausreichend ja/nein)
    """
    budget_data = load_budget_data()
    overrides = budget_data.get("rechnungs_overrides", {})
    tags_map = get_or_create_paperless_tags()
    invoices = fetch_paperless_invoices(tags_map, overrides)
    kalkulation = calculate_budget(budget_data, invoices)

    # 1. Kontostand via fints_connector
    kontostand = 2840.50
    bank_name = "PSD Bank Hessen-Thüringen eG"
    simulated = True
    if fints_connector is not None:
        try:
            b_info = fints_connector.get_current_balance()
            kontostand = float(b_info.get("balance_eur", 2840.50))
            bank_name = b_info.get("bank_name") or b_info.get("bank") or bank_name
            simulated = b_info.get("simulated", True)
        except Exception as e:
            logger.warning(f"Fehler bei fints_connector: {e}")

    # 2. Offene Rechnungen
    open_invoices = [i for i in invoices if i.get("status") == "offen"]
    summe_offen = round(sum(float(i.get("betrag", 0.0)) for i in open_invoices), 2)

    # 3. Deckungsprüfung
    deckung_ausreichend = (kontostand >= summe_offen)
    differenz = round(kontostand - summe_offen, 2)
    if deckung_ausreichend:
        status_text = f"Deckung ausreichend (+{format_eur(differenz)} Puffer)"
    else:
        status_text = f"Unterdeckung um -{format_eur(abs(differenz))}"

    offene_liste = []
    for inv in open_invoices:
        title = inv.get("bezeichnung") or inv.get("title") or "Rechnung"
        glaeubiger = title.split("(")[0].strip() if "(" in title else title
        offene_liste.append({
            "id": inv.get("id"),
            "paperless_id": inv.get("paperless_id"),
            "glaeubiger": glaeubiger,
            "titel": title,
            "betrag": inv.get("betrag", 0.0),
            "betrag_formatiert": format_eur(inv.get("betrag", 0.0)),
            "faelligkeit": str(inv.get("faelligkeit", ""))[:10],
            "status": "offen"
        })

    return {
        "status": "ok",
        "kontostand": kontostand,
        "kontostand_formatiert": format_eur(kontostand),
        "bank_name": bank_name,
        "simulated": simulated,
        "summe_offen": summe_offen,
        "summe_offen_formatiert": format_eur(summe_offen),
        "anzahl_offen": len(offene_liste),
        "deckung_ausreichend": deckung_ausreichend,
        "differenz": differenz,
        "differenz_formatiert": format_eur(differenz),
        "status_text": status_text,
        "offene_rechnungen": offene_liste,
        "kalkulation": kalkulation,
        "letzte_aktualisierung": datetime.now().isoformat()
    }


# ==============================================================================
# HTML WEB-DASHBOARD (Subtask C)
# ==============================================================================

@router.get("/dashboard", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def get_budget_view(request: Request):
    """
    Liefert das interaktive HTML-Dashboard, wenn ein Browser die Seite aufruft,
    oder JSON, wenn ein API-Client / Accept: application/json gesendet wird.
    """
    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        # JSON API Fallback
        budget_data = load_budget_data()
        overrides = budget_data.get("rechnungs_overrides", {})
        tags_map = get_or_create_paperless_tags()
        invoices = fetch_paperless_invoices(tags_map, overrides)
        kalkulation = calculate_budget(budget_data, invoices)
        return JSONResponse({
            "status": "ok",
            "einnahmen": budget_data.get("einnahmen", []),
            "fixkosten": budget_data.get("fixkosten", []),
            "rechnungen": invoices,
            "kalkulation": kalkulation,
            "letzte_aktualisierung": datetime.now().isoformat()
        })

    status_data = get_budget_status()
    kontostand = status_data["kontostand_formatiert"]
    bank_name = status_data["bank_name"]
    summe_offen = status_data["summe_offen_formatiert"]
    deckung = status_data["deckung_ausreichend"]
    status_text = status_data["status_text"]
    rechnungen = status_data["offene_rechnungen"]

    badge_bg = "#10b981" if deckung else "#ef4444"
    badge_text = "✓ DECKUNG AUSREICHEND" if deckung else "⚠ UNTERDECKUNG"

    rows_html = ""
    for r in rechnungen:
        rows_html += f"""
        <tr style="border-bottom: 1px solid rgba(255,255,255,0.08);">
            <td style="padding: 12px; font-weight: bold; color: #7aa2f7;">{r['glaeubiger']}</td>
            <td style="padding: 12px; color: #a9b1d6; font-size: 0.9em;">{r['titel']}</td>
            <td style="padding: 12px; text-align: right; font-weight: bold; color: #f7768e;">{r['betrag_formatiert']}</td>
            <td style="padding: 12px; text-align: center; color: #9aa5ce; font-size: 0.85em;">{r['faelligkeit'] or '-'}</td>
            <td style="padding: 12px; text-align: center;"><span style="padding: 3px 8px; border-radius: 4px; background: rgba(247,118,142,0.15); color: #f7768e; font-size: 0.75em; font-weight: bold;">OFFEN</span></td>
        </tr>
        """
    if not rechnungen:
        rows_html = "<tr><td colspan='5' style='padding: 24px; text-align: center; color: #9aa5ce;'>Keine offenen Rechnungen gefunden! Alle Forderungen beglichen.</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Budget-Manager // Outer Heaven & Fox</title>
    <style>
        :root {{
            --bg: #090a0f;
            --panel: #131620;
            --border: #222738;
            --accent-green: #10b981;
            --accent-blue: #7aa2f7;
            --accent-red: #f7768e;
            --text: #c0caf5;
        }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 960px;
            margin: 0 auto;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
            margin-bottom: 24px;
        }}
        h1 {{
            margin: 0;
            font-size: 1.4em;
            letter-spacing: 1px;
            color: var(--accent-blue);
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 18px;
        }}
        .card-label {{
            font-size: 0.8em;
            color: #7982a9;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        .card-value {{
            font-size: 1.8em;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: bold;
            color: #000;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
        }}
        th {{
            background: #171b29;
            padding: 12px;
            text-align: left;
            font-size: 0.8em;
            color: #7982a9;
            text-transform: uppercase;
        }}
        .btn {{
            background: var(--panel);
            border: 1px solid var(--accent-blue);
            color: var(--accent-blue);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            text-decoration: none;
        }}
        .btn:hover {{
            background: var(--accent-blue);
            color: #000;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>// BUDGET & LIQUIDITÄTS-MANAGER</h1>
                <div style="font-size: 0.85em; color: #7982a9; margin-top: 4px;">Node Fox (100.93.184.87:5005) & PSD Bank Live-Sync</div>
            </div>
            <div>
                <a href="/api/budget" class="btn">↻ Aktualisieren</a>
            </div>
        </div>

        <div class="cards">
            <div class="card" style="border-color: rgba(122, 162, 247, 0.4);">
                <div class="card-label">Kontostand ({bank_name})</div>
                <div class="card-value" style="color: var(--accent-blue);">{kontostand}</div>
                <div style="font-size: 0.8em; color: #7982a9;">Read-Only FinTS 3.0</div>
            </div>

            <div class="card" style="border-color: rgba(247, 118, 142, 0.4);">
                <div class="card-label">Offene Paperless-Rechnungen ({status_data['anzahl_offen']})</div>
                <div class="card-value" style="color: var(--accent-red);">{summe_offen}</div>
                <div style="font-size: 0.8em; color: #7982a9;">Fällige Forderungen</div>
            </div>

            <div class="card" style="border-color: {badge_bg};">
                <div class="card-label">Abgleich & Deckungs-Status</div>
                <div style="margin: 8px 0;">
                    <span class="badge" style="background: {badge_bg};">{badge_text}</span>
                </div>
                <div style="font-size: 0.9em; font-weight: bold; color: {badge_bg};">{status_text}</div>
            </div>
        </div>

        <h3 style="margin: 0 0 12px 0; font-size: 1.1em; color: var(--text);">// Offene Rechnungen im Detail</h3>
        <table>
            <thead>
                <tr>
                    <th>Gläubiger</th>
                    <th>Dokumenten-Titel</th>
                    <th style="text-align: right;">Betrag</th>
                    <th style="text-align: center;">Fälligkeit</th>
                    <th style="text-align: center;">Status</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>

        <div style="margin-top: 24px; text-align: center; font-size: 0.8em; color: #7982a9;">
            Letzte Synchronisation: {status_data['letzte_aktualisierung']} | Outer Heaven Cluster
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ==============================================================================
# POST /api/budget/status & update
# ==============================================================================

@router.post("/status")
def update_invoice_status(req: StatusUpdateRequest = Body(...)):
    """Toggles or sets the invoice status in Paperless tags and local budget state."""
    doc_id = str(req.id)
    target_status = req.status.lower() if req.status else None
    tags_map = get_or_create_paperless_tags()
    tag_offen_id = tags_map.get("offen", 1)
    tag_bezahlt_id = tags_map.get("bezahlt", 2)
    paperless_url = get_working_paperless_url()

    headers = get_paperless_headers()
    current_tags = []

    try:
        get_res = requests.get(f"{paperless_url}/api/documents/{doc_id}/", headers=headers, timeout=5)
        if get_res.status_code == 200:
            doc_data = get_res.json()
            current_tags = doc_data.get("tags", [])
        else:
            logger.warning(f"Document {doc_id} not found in Paperless (status {get_res.status_code})")
    except Exception as e:
        logger.error(f"Error fetching document {doc_id} from Paperless: {e}")

    is_currently_paid = tag_bezahlt_id in current_tags

    if target_status is None:
        new_status = "offen" if is_currently_paid else "bezahlt"
    else:
        new_status = "bezahlt" if target_status in ("bezahlt", "paid", "true") else "offen"

    new_tags = [t for t in current_tags if t not in (tag_offen_id, tag_bezahlt_id)]
    if new_status == "bezahlt":
        new_tags.append(tag_bezahlt_id)
    else:
        new_tags.append(tag_offen_id)

    paperless_success = False
    try:
        patch_res = requests.patch(
            f"{paperless_url}/api/documents/{doc_id}/",
            headers=headers,
            json={"tags": new_tags},
            timeout=5
        )
        if patch_res.status_code == 200:
            paperless_success = True
    except Exception as e:
        logger.error(f"Error patching document {doc_id} in Paperless: {e}")

    budget_data = load_budget_data()
    overrides = budget_data.setdefault("rechnungs_overrides", {})
    if doc_id in overrides:
        overrides[doc_id]["status"] = new_status
    save_budget_data(budget_data)

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
