#!/usr/bin/env python3
"""
application_assistant.py - Hermes Bewerbungs-Assistent
=====================================================
Zieht Bewerbungsunterlagen (Zeugnisse, Zertifikate, Lebensläufe) aus Paperless (deraldehesse),
bündelt diese mit eingesprochenen Sprachnotizen (Voice-to-Application) bzw. Stellenanzeigen
und generiert über das Modell catlord-hermes:latest (metal-dgear) ein maßgeschneidertes Anschreiben
sowie einen passenden Lebenslauf-Entwurf (mit Fallback auf fox).
"""

import os
import re
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests

logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [ApplicationAssistant]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("ApplicationAssistant")

# Paperless API
PAPERLESS_CANDIDATES = [
    os.getenv("PAPERLESS_URL", "http://100.127.247.117:8000"),
    "http://192.168.188.96:8000",
    "http://127.0.0.1:8000"
]
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "428107bcaa486337ba98b06547d0a843b3007036")
TAG_BEWERBUNG = "Bewerbungsunterlagen"

# LLM-Endpunkte (metal-dgear primär, fox lokal Fallback)
METAL_DGEAR_OLLAMA = os.getenv("METAL_DGEAR_OLLAMA", "http://100.96.88.84:11434")
METAL_DGEAR_MODEL = os.getenv("METAL_DGEAR_MODEL", "catlord-hermes:latest")
FOX_OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
FOX_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")


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


def ensure_paperless_tag(tag_name: str = TAG_BEWERBUNG) -> Optional[int]:
    """Prüft, ob der Tag in Paperless existiert, oder legt ihn via REST API an."""
    base_url = get_working_paperless_url()
    headers = {
        "Authorization": f"Token {PAPERLESS_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(f"{base_url}/api/tags/?page_size=100", headers=headers, timeout=4.0)
        if r.status_code == 200:
            results = r.json().get("results", [])
            for t in results:
                if t.get("name", "").lower() == tag_name.lower():
                    return t.get("id")
            # Tag noch nicht da -> Neu anlegen
            create_r = requests.post(f"{base_url}/api/tags/", headers=headers, json={"name": tag_name}, timeout=4.0)
            if create_r.status_code in (200, 201):
                new_id = create_r.json().get("id")
                logger.info("Paperless-Tag '%s' neu erstellt (ID %s)", tag_name, new_id)
                return new_id
    except Exception as e:
        logger.warning("Konnte Paperless-Tag '%s' nicht abrufen/erstellen: %s", tag_name, e)
    return None


def fetch_bewerbungsunterlagen() -> List[Dict[str, Any]]:
    """Zieht alle Dokumente mit Tag 'Bewerbungsunterlagen' aus Paperless."""
    tag_id = ensure_paperless_tag(TAG_BEWERBUNG)
    base_url = get_working_paperless_url()
    headers = {
        "Authorization": f"Token {PAPERLESS_TOKEN}",
        "Content-Type": "application/json"
    }
    documents: List[Dict[str, Any]] = []

    try:
        url = f"{base_url}/api/documents/?page_size=50"
        if tag_id:
            url += f"&tags__id__in={tag_id}"

        resp = requests.get(url, headers=headers, timeout=8.0)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            for doc in results:
                doc_tags = doc.get("tags", [])
                title = doc.get("title", f"Dokument #{doc.get('id')}")
                content = doc.get("content", "")
                
                # Wenn nach tag gefiltert oder Titel auf Bewerbung hinweist
                if (tag_id and tag_id in doc_tags) or any(k in title.lower() for k in ["zeugnis", "lebenslauf", "zertifikat", "bewerbung"]):
                    documents.append({
                        "id": doc.get("id"),
                        "title": title,
                        "created": doc.get("created"),
                        "content": content[:4000]  # Kontextfenster schonen
                    })

            # Falls noch keine Dokumente mit dem Tag vorhanden sind, nutzen wir alle vorhandenen Dokumente als Kontextbasis
            if not documents:
                for doc in results[:3]:
                    content = doc.get("content", "")
                    if len(content.strip()) > 50:
                        documents.append({
                            "id": doc.get("id"),
                            "title": doc.get("title"),
                            "created": doc.get("created"),
                            "content": content[:2500]
                        })
    except Exception as e:
        logger.error("Fehler beim Abrufen der Bewerbungsunterlagen aus Paperless: %s", e)

def _format_text_field(val: Any) -> str:
    if isinstance(val, str):
        return val.strip()
    elif isinstance(val, dict):
        lines = []
        for k, v in val.items():
            if isinstance(v, list):
                lines.append(f"{k}:\n" + "\n".join(f"  - {item}" for item in v))
            elif isinstance(v, dict):
                lines.append(f"{k}:\n" + "\n".join(f"  - {subk}: {subv}" for subk, subv in v.items()))
            else:
                lines.append(f"{k}: {v}")
        return "\n\n".join(lines)
    elif isinstance(val, list):
        return "\n".join(f"- {x}" if not str(x).startswith("-") else str(x) for x in val)
    return str(val).strip() if val is not None else ""


def generate_application_draft(
    voice_transcript: Optional[str] = None,
    job_posting_text: Optional[str] = None,
    documents: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Bündelt alle Eingaben und ruft catlord-hermes auf metal-dgear auf (Fallback fox).
    """
    docs = documents if documents is not None else fetch_bewerbungsunterlagen()
    
    docs_context_parts = []
    for d in docs:
        docs_context_parts.append(f"--- DOKUMENT: {d['title']} ---\n{d['content']}")
    docs_context = "\n\n".join(docs_context_parts) if docs_context_parts else "Keine archivierten Dokumente im System gefunden."

    user_voice = (voice_transcript or "").strip()
    job_text = (job_posting_text or "").strip()

    system_prompt = (
        "Du bist der professionelle, empathische und hochkompetente Bewerbungs-Assistent von Hermes für Sebastian.\n"
        "Deine Aufgabe ist es, aus den im Cluster archivierten Unterlagen (Zeugnisse, Zertifikate, bisherige Profile) "
        "und den aktuellen Nutzereingaben (eingesprochene Stichpunkte / Stellenanzeige) ein sofort verwendbares, "
        "wirkungsvolles Bewerbungsschreiben (Anschreiben) und einen passenden Lebenslauf-Auszug zu erstellen.\n"
        "Regeln:\n"
        "1. Sprache: Deutsch, professionell, authentisch, formvollendet (orientiert an DIN 5008).\n"
        "2. Keine erfundenen Fakten, sondern geschickte Verknüpfung der tatsächlichen Stärken und Praxiserfahrungen.\n"
        "3. Liefere als Antwort ein sauberes JSON-Objekt mit zwei Schlüsseln:\n"
        "   - 'cover_letter': Das vollständige Anschreiben (mit Absender-Platzhaltern, Datum, Betreff, Anrede, Textkörper, Grußformel).\n"
        "   - 'cv_draft': Ein strukturierter Lebenslauf-Entwurf passend zur Zielposition.\n"
        "   - 'advice': 1-2 praxisnahe Tipps für das Vorstellungsgespräch."
    )

    prompt = f"""
STEUERUNGSDATEN DER BEWERBUNG:
- Eingesprochene Sprachnotizen des Nutzers (Voice-to-Application):
{user_voice if user_voice else "Keine spezifische Sprachnotiz vorhanden."}

- Stellenanzeige / Zielprofil:
{job_text if job_text else "Initiativbewerbung bzw. Orientierung an den archivierten Kompetenzen."}

- Vorhandene Dokumente & Zeugnisse aus Paperless ({len(docs)} Dokumente):
{docs_context[:8000]}

Erstelle nun das maßgeschneiderte Anschreiben und den Lebenslauf-Entwurf. Antworte AUSSCHLIESSLICH als JSON:
{{
  "cover_letter": "...",
  "cv_draft": "...",
  "advice": "..."
}}
"""

    # Inferenz-Endpunkte in Prioritätsreihenfolge:
    # 1. metal-dgear (catlord-hermes:latest)
    # 2. fox (llama3.2:3b / hermes3:8b)
    endpoints = [
        {"url": METAL_DGEAR_OLLAMA, "model": METAL_DGEAR_MODEL, "node": "metal-dgear"},
        {"url": FOX_OLLAMA, "model": FOX_MODEL, "node": "fox"}
    ]

    for ep in endpoints:
        ollama_url = ep["url"].rstrip("/")
        model_name = ep["model"]
        node_name = ep["node"]

        try:
            logger.info("Starte Bewerbungs-Inferenz auf %s mit Modell '%s'...", node_name, model_name)
            req_body = {
                "model": model_name,
                "prompt": prompt,
                "system": system_prompt,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_ctx": 2048
                }
            }
            resp = requests.post(f"{ollama_url}/api/generate", json=req_body, timeout=120.0)
            if resp.status_code == 200:
                raw_response = resp.json().get("response", "").strip()
                # Markdown-Codeblocks abfangen falls vorhanden
                if raw_response.startswith("```"):
                    raw_response = re.sub(r"^```[a-zA-Z]*\n?", "", raw_response)
                    raw_response = re.sub(r"\n?```$", "", raw_response).strip()

                parsed = json.loads(raw_response)
                logger.info("Bewerbungs-Inferenz erfolgreich generiert auf %s!", node_name)
                return {
                    "status": "success",
                    "cover_letter": _format_text_field(parsed.get("cover_letter")),
                    "cv_draft": _format_text_field(parsed.get("cv_draft")),
                    "advice": _format_text_field(parsed.get("advice")),
                    "generator_node": node_name,
                    "generator_model": model_name,
                    "docs_used_count": len(docs),
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
        except Exception as e:
            logger.warning("Inferenz auf %s (%s) fehlgeschlagen: %s", node_name, model_name, e)

    # Heuristischer Notfall-Fallback
    fallback_letter = (
        "Sebastian\n[Straße und Hausnummer]\n[PLZ Ort]\n\n"
        f"Datum: {datetime.now().strftime('%d.%m.%Y')}\n\n"
        "Bewerbung um die Position als Fachkraft\n\n"
        "Sehr geehrte Damen und Herren,\n\n"
        "mit großem Interesse habe ich Ihre Stellenausschreibung zur Kenntnis genommen. "
        "Aufgrund meiner langjährigen technischen Erfahrung und lösungsorientierten Arbeitsweise "
        "kann ich Ihr Team vom ersten Tag an tatkräftig unterstützen.\n\n"
        "Über eine Einladung zu einem persönlichen Vorstellungsgespräch freue ich mich sehr.\n\n"
        "Mit freundlichen Grüßen\n\nSebastian"
    )

    fallback_cv = (
        "LEBENSLAUF\n\n"
        "Persönliche Daten:\n"
        "Name: Sebastian\n\n"
        "Beruflicher Werdegang & Kernkompetenzen:\n"
        "- Systemadministration, Automatisierung & Netzwerkinfrastruktur\n"
        "- Eigenverantwortliche Projekt- und Dokumentenorganisation\n\n"
        "Kenntnisse:\n"
        "- Linux, Docker, Mobil- & Web-Anwendungen, IT-Support"
    )

    return {
        "status": "success",
        "cover_letter": fallback_letter,
        "cv_draft": fallback_cv,
        "advice": "Prüfe vor dem Absenden die konkreten Ansprechpartner und passe die Adresszeile an.",
        "generator_node": "fallback_template",
        "generator_model": "heuristic_template",
        "docs_used_count": len(docs),
        "created_at": datetime.now(timezone.utc).isoformat()
    }


if __name__ == "__main__":
    res = generate_application_draft(voice_transcript="Bewerbung als IT-Techniker und Systembetreuer")
    print(json.dumps(res, indent=2, ensure_ascii=False))
