#!/usr/bin/env python3
import os
import sys
import json
import re
import yaml
import requests
from datetime import datetime, timezone

PAPERLESS_URL = os.getenv("PAPERLESS_URL", "http://192.168.188.96:8000")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "e1ea8674359e0979eece9a5335cd4d49fa709ed")
DEFAULT_OLLAMA = "http://100.93.184.87:11434/api/generate" if os.path.exists("/.dockerenv") else "http://127.0.0.1:11434/api/generate"
OLLAMA_URL = os.getenv("OLLAMA_URL", DEFAULT_OLLAMA)

def load_profile(path="advisor_profile.yaml"):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    script_relative = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if os.path.exists(script_relative):
        with open(script_relative, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

def run_hermes_advisor(document_id):
    headers = {"Authorization": f"Token {PAPERLESS_TOKEN}"}
    
    # 1. Dokument-Details & OCR-Text aus Paperless abrufen
    res = requests.get(f"{PAPERLESS_URL}/api/documents/{document_id}/", headers=headers)
    if res.status_code != 200:
        print(f"Fehler beim Abrufen von Dokument {document_id}: HTTP {res.status_code}")
        return

    doc = res.json()
    ocr_content = doc.get("content", "")
    profile_data = load_profile()

    # 2. System-Prompt für Triage & Finanzprüfung
    prompt = f"""
Du bist Hermes, ein loyaler Finanz- und Lebensberater. Analysiere das folgende Dokument.
Benutzer-Kontext:
{json.dumps(profile_data, ensure_ascii=False)}

Dokumenten-Volltext:
\"\"\"{ocr_content[:4000]}\"\"\"

Aufgaben:
1. Ermittle den Absender (Korrespondent) und das Belegdatum (YYYY-MM-DD).
2. Verfasse einen sauberen Titel nach Schema: YYYY-MM-DD [Absender] - [Dokumenttyp] - [Betreff].
3. Bestimme die Dringlichkeit (GRUEN: Info/Ablage, GELB: Frist > 7 Tage, ROT: Frist < 3 Tage / Mahnung).
4. Prüfe Plausibilität (z.B. Rundfunkbeitrag-Regel, Doppelzahlungen, Preiserhöhungen).
5. Erstelle eine beruhigende, klare Notiz: Was will der Absender? Was ist zu tun?

Antworte AUSSCHLIESSLICH im folgenden JSON-Format:
{{
  "title": "...",
  "correspondent": "...",
  "created_date": "YYYY-MM-DD",
  "document_type": "Rechnung|Vertrag|Bescheid|Werbung|Info",
  "urgency": "GRUEN|GELB|ROT",
  "amount_eur": 0.0,
  "due_date": null,
  "is_duplicate_or_invalid": false,
  "advisor_note": "..."
}}
"""

    # 3. Call an lokales LLM
    response = requests.post(
        OLLAMA_URL,
        json={"model": "hermes-agent", "prompt": prompt, "format": "json", "stream": False},
        timeout=120
    )
    raw_resp = response.json().get("response", "{}")
    cleaned_resp = raw_resp.strip()
    if cleaned_resp.startswith("```"):
        cleaned_resp = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned_resp)
        cleaned_resp = re.sub(r"\n?```$", "", cleaned_resp).strip()
    try:
        analysis = json.loads(cleaned_resp)
    except json.JSONDecodeError:
        json_match = re.search(r"\{.*\}", cleaned_resp, re.DOTALL)
        if json_match:
            analysis = json.loads(json_match.group(0))
        else:
            analysis = {}

    # 4. Metadaten-Update per REST-API
    patch_data = {
        "title": analysis.get("title", doc.get("title")),
    }
    if analysis.get("created_date"):
        patch_data["created"] = analysis["created_date"]

    requests.patch(f"{PAPERLESS_URL}/api/documents/{document_id}/", headers=headers, json=patch_data)

    # 5. E-Mail Dispatch & Draft Workflow (Paperworker)
    email_record = None
    email_note_text = ""
    try:
        import hermes_email_handler

        doc_type = analysis.get("document_type", "Dokument")
        urgency = analysis.get("urgency", "GRUEN")
        correspondent = analysis.get("correspondent", doc.get("correspondent", "Unbekannt"))
        amount = analysis.get("amount_eur")
        due_date = analysis.get("due_date")

        # Aktenzeichen aus Volltext ermitteln
        az_match = re.search(r"(?:Aktenzeichen|AZ|Vorgangs-?Nr|Kassenzeichen|Referenz|Rechnungs-?Nr)[:\s]+([A-Za-z0-9\-_./]+)", ocr_content, re.IGNORECASE)
        aktenzeichen = az_match.group(1).replace("/", "-") if az_match else ""

        # Mögliche Empfänger-E-Mail aus dem OCR-Text extrahieren
        email_match = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", ocr_content)
        detected_email = email_match.group(0) if email_match else ""
        recipient = detected_email or f"kontakt@{correspondent.lower().replace(' ', '')}.de"

        # Nur für relevante Dokumente E-Mail generieren
        if doc_type in ["Rechnung", "Mahnung", "Bescheid"] or urgency in ["GELB", "ROT"]:
            action_mode = os.getenv("HERMES_EMAIL_ACTION", "draft").lower()  # "draft" oder "auto_send"
            is_auto = (action_mode == "auto_send") and bool(detected_email)

            generated = hermes_email_handler.generate_autonomous_email_text(
                correspondent=correspondent,
                aktenzeichen=aktenzeichen,
                betrag=amount,
                due_date=due_date,
                doc_summary=analysis.get("advisor_note", ""),
                doc_type=doc_type,
                user_name=profile_data.get("profile", {}).get("user_name", "Sebastian"),
                model=os.getenv("HERMES_EMAIL_MODEL", "megan:hermes")
            )

            email_record = hermes_email_handler.create_email_record(
                recipient=recipient,
                subject=generated["subject"],
                body=generated["body"],
                is_draft=not is_auto,
                document_id=str(document_id),
                metadata={
                    "correspondent": correspondent,
                    "aktenzeichen": aktenzeichen,
                    "amount_eur": amount,
                    "urgency": urgency,
                    "document_type": doc_type,
                    "auto_detected_email": bool(detected_email)
                },
                auto_send=is_auto
            )

            if is_auto:
                email_note_text = f"\n[✉️ E-Mail Versendet] Automatisch versendet an: {recipient}"
            else:
                email_note_text = f"\n[✉️ E-Mail Entwurf] In Hermes Pending Outbox abgelegt (Empfänger: {recipient})."
    except Exception as e:
        print(f"Hinweis: E-Mail Workflow für Dokument {document_id} übersprungen/fehlgeschlagen: {e}")

    # 6. PSD Bank FinTS Saldo & Budget-Abgleich
    banking_note_text = ""
    try:
        import fints_connector
        if amount and amount > 0:
            threshold = profile_data.get("profile", {}).get("budget_context", {}).get("warn_threshold_eur", 100.0)
            budget_res = fints_connector.check_budget_adequacy(invoice_amount_eur=amount, warn_threshold_eur=threshold)
            status_symbol = "✓" if budget_res["is_sufficient"] else "⚠️"
            banking_note_text = (
                f"\n[💳 PSD Bank Check] Saldo: {budget_res['balance_eur']:.2f} € "
                f"| Nach Abzug: {budget_res['remaining_eur']:.2f} € "
                f"({status_symbol} Budget {'ausreichend' if budget_res['is_sufficient'] else 'KNAPP'})"
            )
            # Proaktive Benachrichtigung an Desktop & Mobile/Android
            fints_connector.notify_paperless_invoice_overview(
                invoice_count=1,
                total_amount_eur=amount,
                balance_eur=budget_res["balance_eur"]
            )
    except Exception as e:
        print(f"Hinweis: Banking-Check für Dokument {document_id} übersprungen: {e}")

    # 7. Notiz anhängen (Entschärfungs-Text + E-Mail Status + Banking Check)
    note_payload = {
        "note": f"[{analysis.get('urgency', 'INFO')}] Hermes-Check:\n{analysis.get('advisor_note')}{email_note_text}{banking_note_text}"
    }
    requests.post(f"{PAPERLESS_URL}/api/documents/{document_id}/notes/", headers=headers, json=note_payload)
    print(f"Dokument {document_id} erfolgreich via Hermes aktualisiert.")
    return {"analysis": analysis, "email": email_record, "banking": banking_note_text}

if __name__ == "__main__":
    doc_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DOCUMENT_ID")
    if doc_id:
        run_hermes_advisor(doc_id)

