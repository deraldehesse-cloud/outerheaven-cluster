#!/usr/bin/env python3
"""
buero_hermes.py - Büro-Hermes Backend & Autogen Integration
============================================================
Automatisierte Büro-Organisation für den Hermes-Agenten auf Node Fox:
1. Lokales Dateisystem-Handling im gemounteten Agenten-Drive:
   /home/fox/gdrive/Agenten-Drive/Büro/Büro Hermes/
   - "schriftlich" (PDF-Scans)
   - "E-Mail" (Rechnungen & Anhänge)
   - "Fixkosten" (Verträge & Abos)
   - "Bewerbungsunterlagen" (CV, Zeugnisse)
   - "Archiv/YYYY/" (Revisionssicheres 10-Jahre-Langzeitarchiv nach GoBD)
   - "Werbung/" (Newsletter & Werbung)
   - "Isoliert/" (Quarantäne für Phishing-Verdacht & unzulässige Anhänge)

2. Revisionssichere Archivierung (10 Jahre Aufbewahrungspflicht):
   - Keine Rechnungen/Belege löschen!
   - Vorgänge > 90 Tage werden in Archiv/YYYY/E-Mail/ bzw. Archiv/YYYY/schriftlich/ verschoben.
   - Für den aktiven LLM-Kontext (qwen:vram) werden standardmäßig nur die letzten 90 Tage
     geladen (Schutz des 8192-Token-Limits).

3. Mail-Klassifizierung & Trennung:
   - Spam: Dauerhaft und unwiderruflich löschen (IMAP +FLAGS \\Deleted + EXPUNGE).
   - Werbung/Newsletter: Gmail-Ordner 'Werbung' + lokaler Drive-Ordner 'Werbung/'.
   - Rechnungen/Belege: Lokaler Zielpfad 'E-Mail/'.

4. Strikte IT-Sicherheit & Phishing-Guard:
   - NIEMALS Links in E-Mails öffnen, folgen oder scrapen.
   - Strikte Whitelist für Anhänge: Ausschließlich .pdf, .png, .jpg, .jpeg (mit Magic-Byte-Prüfung).
   - Verbot & sofortige Abwehr von .exe, .scr, .bat, .vbs, .js, .zip, etc.
   - Phishing-Verdacht (Domain-Spoofing, Konto-Sperr-Druck, Tracking) sofort isolieren.

5. Modell-Routing:
   - Extraktion, Indexierung, Umbenennung: qwen:vram (Kontext 8192, Low Latency).
   - Textentwürfe (Widersprüche, Anfragen, Bewerbungsanpassungen): darkidol:ram.
"""

import os
import sys
import re
import json
import time
import shutil
import imaplib
import logging
import argparse
import tempfile
import subprocess
import urllib.request
import urllib.error
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple, Set

# Logging Configuration
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [BueroHermes]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("BueroHermes")

# --- Konfigurationskonstanten ---
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = int(os.environ.get("OLLAMA_PORT", 11434))
FAST_MODEL = os.environ.get("BUERO_FAST_MODEL", "qwen:vram")
DEEP_MODEL = os.environ.get("BUERO_DEEP_MODEL", "darkidol:ram")
NUM_CTX = 8192
ACTIVE_WINDOW_DAYS = 90  # 90 Tage Arbeitsbestand für LLM-Kontext

DEFAULT_BASE_PATH = "/home/fox/gdrive/Agenten-Drive/Büro/Büro Hermes"
FALLBACK_UNICODE_SLASH_PATH = "/home/fox/gdrive/Agenten-Drive/Büro/Büro／Hermes"

GMAIL_CREDS_FILE = Path(os.path.expanduser("~/.config/google/gmail_creds.json"))
GOOGLE_TOKEN_FILE = Path(os.path.expanduser("~/.hermes/google_token.json"))

# --- IT-Sicherheit & Phishing-Guard Filter ---
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
FORBIDDEN_DANGEROUS_EXTENSIONS = {
    ".exe", ".scr", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".ps1", ".ps1xml", ".ps2", ".msh", ".jar",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".img", ".dmg",
    ".msi", ".msp", ".hta", ".cpl", ".inf", ".reg", ".docm", ".xlsm", ".pptm"
}

RECHNUNG_KEYWORDS = [
    "rechnung", "beleg", "invoice", "abrechnung", "mahnung",
    "gebühr", "quittung", "zahlungsbestätigung", "forderung"
]

WERBUNG_KEYWORDS = [
    "newsletter", "rabatt", "angebot", "gutschein", "sale",
    "prozent", "werbung", "deal", "sparen", "abonnieren",
    "unsubscribe", "abmelden", "marketing"
]

SPAM_KEYWORDS = [
    "viagra", "casino", "lottery", "gewinnspiel", "erbe", "inheritance",
    "bitcoin profit", "crypto doubling", "investieren sie jetzt",
    "millionen euro gewonnen", "sexy girls", "horny", "pills"
]

PHISHING_ALERTS = [
    "konto gesperrt", "kontosperrung", "zugangsdaten aktualisieren",
    "verifizieren sie ihr konto", "sicherheitsabteilung", "kreditkarte abgelaufen",
    "dringende handlung erforderlich", "innerhalb von 24 stunden",
    "zugriff eingeschränkt", "passwort zurücksetzen", "login-versuch",
    "ihre identität bestätigen"
]


class BueroPaths:
    """Verwaltet und normalisiert lokale Pfade im gemounteten Google Drive."""

    def __init__(self, custom_base: Optional[str] = None):
        self.base_dir = self._resolve_base_dir(custom_base)
        self.schriftlich = self._resolve_subfolder(["schriftlich", "Schriftlich"])
        self.email = self._resolve_subfolder(["E-Mail", "email", "Email", "e-mail"])
        self.fixkosten = self._resolve_subfolder(["Fixkosten", "fixkosten"])
        self.werbung = self._resolve_subfolder(["Werbung", "werbung"])
        self.isoliert = self._resolve_subfolder(["Isoliert", "isoliert", "Quarantäne"])
        self.archiv = self._resolve_subfolder(["Archiv", "archiv"])
        self.bewerbungsunterlagen = self._resolve_bewerbung_folder()
        self.schriftsaetze = self._resolve_schriftsaetze_folder()

    def _resolve_base_dir(self, custom_base: Optional[str] = None) -> Path:
        if custom_base:
            p = Path(custom_base).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p

        candidates = [
            Path(DEFAULT_BASE_PATH),
            Path(FALLBACK_UNICODE_SLASH_PATH),
            Path.home() / "gdrive" / "Agenten-Drive" / "Büro" / "Büro Hermes",
            Path.home() / "gdrive" / "Agenten-Drive" / "Büro" / "Büro／Hermes",
            Path("/tmp/buero_hermes_mock")
        ]

        for c in candidates:
            if c.exists() and c.is_dir():
                return c

        parent = Path("/home/fox/gdrive/Agenten-Drive/Büro")
        if parent.exists() and parent.is_dir():
            target = parent / "Büro Hermes"
            try:
                target.mkdir(parents=True, exist_ok=True)
                return target
            except Exception:
                pass

        fallback = candidates[0]
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except Exception:
            mock_path = Path("/tmp/buero_hermes_mock")
            mock_path.mkdir(parents=True, exist_ok=True)
            return mock_path

    def _resolve_subfolder(self, names: List[str]) -> Path:
        for name in names:
            p = self.base_dir / name
            if p.exists() and p.is_dir():
                return p
        target = self.base_dir / names[0]
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Konnte Verzeichnis %s nicht erstellen: %s", target, e)
        return target

    def _resolve_bewerbung_folder(self) -> Path:
        candidates = [
            self.base_dir / "Bewerbungsunterlagen",
            self.base_dir.parent / "Bewerbungsunterlagen",
            self.base_dir / "bewerbungsunterlagen"
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        target = self.base_dir / "Bewerbungsunterlagen"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return target

    def get_archive_dir(self, year: int, category: str) -> Path:
        """Liefert bzw. erstellt das revisionssichere Archiv: Archiv/YYYY/<category>/."""
        target = self.archiv / str(year) / category
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _resolve_schriftsaetze_folder(self) -> Path:
        candidates = [
            Path("/home/fox/gdrive/Agenten-Drive/Büro/Büro／Hermes/Schriftsätze"),
            Path("/home/fox/gdrive/Agenten-Drive/Büro/Büro Hermes/Schriftsätze"),
            self.base_dir / "Schriftsätze",
            self.base_dir / "schriftsaetze"
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        target = self.base_dir / "Schriftsätze"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return target

    def status(self) -> Dict[str, str]:
        return {
            "base_dir": str(self.base_dir),
            "schriftlich": str(self.schriftlich),
            "email": str(self.email),
            "fixkosten": str(self.fixkosten),
            "bewerbungsunterlagen": str(self.bewerbungsunterlagen),
            "werbung": str(self.werbung),
            "isoliert": str(self.isoliert),
            "archiv": str(self.archiv),
            "schriftsaetze": str(self.schriftsaetze),
            "exists": str(self.base_dir.exists())
        }


# ==============================================================================
# Strikte IT-Sicherheit & Phishing-Guard
# ==============================================================================

class SecurityGuard:
    """
    Sicherheitsprüfung für Dokumente und E-Mails:
    - Verhindert Ausführen / Öffnen von bösartigen Payloads
    - Strikte Whitelist für Dokumentenformate (.pdf, .png, .jpg)
    - Validierung der Magic Bytes (Dateisignatur)
    - Phishing-Erkennung & Quarantäne-Isolation
    - Absolutes Verbot von HTTP/Link-Aufrufen aus E-Mail-Inhalten
    """

    @staticmethod
    def is_extension_allowed(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in ALLOWED_ATTACHMENT_EXTENSIONS

    @staticmethod
    def is_extension_dangerous(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in FORBIDDEN_DANGEROUS_EXTENSIONS

    @staticmethod
    def validate_magic_bytes(content: bytes, filename: str) -> bool:
        """Validiert die Dateisignatur (Magic Bytes), um Maskierungen zu verhindern."""
        if not content:
            return False
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            # PDF muss mit %PDF- beginnen (üblicherweise erste 1024 Bytes)
            return b"%PDF-" in content[:1024]
        elif ext == ".png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        elif ext in [".jpg", ".jpeg"]:
            return content.startswith(b"\xff\xd8\xff")
        return False

    @classmethod
    def inspect_attachment(cls, filename: str, content: bytes) -> Tuple[bool, str]:
        """
        Prüft einen Anhang auf IT-Sicherheitskonformität.
        Gibt (ist_sicher, begruendung) zurück.
        """
        if cls.is_extension_dangerous(filename):
            return False, f"GEFÄHRLICHER DATEITYP BLOCKIERT: Endung {Path(filename).suffix} ist unzulässig!"

        if not cls.is_extension_allowed(filename):
            return False, f"NICHT-WHILISTED FORMAT: {filename} entspricht nicht PDF/PNG/JPG."

        if not cls.validate_magic_bytes(content, filename):
            return False, f"DATEISIGNATUR UNGÜLTIG: {filename} ist keine echte PDF/Bilddatei (Manipulationsverdacht)!"

        return True, "Sicher"

    @classmethod
    def check_phishing(cls, sender: str, subject: str, body_text: str) -> Tuple[bool, str]:
        """
        Analysiert E-Mail auf typische Phishing-Indikatoren.
        Gibt (phishing_verdacht, grund) zurück.
        """
        combined = f"{subject} {body_text}".lower()

        # 1. Bekannte Alarmphrasen
        for alert in PHISHING_ALERTS:
            if alert in combined:
                # Prüfe ob der Absender eine vertrauenswürdige Domain ist
                _, addr = parseaddr(sender)
                domain = addr.split("@")[-1].lower() if "@" in addr else ""
                return True, f"Phishing-Alarmphrase erkannt: '{alert}' von Absender-Domain '{domain}'"

        # 2. Absender-Name vs. Absender-Adresse (Spoofing)
        name, addr = parseaddr(sender)
        brand_spoof = ["paypal", "sparkasse", "volksbank", "amazon", "dhl", "deutsche bank", "postbank"]
        for brand in brand_spoof:
            if brand in name.lower() and brand not in addr.lower():
                return True, f"Absender-Spoofing erkannt: Name '{name}', aber echte Adresse '{addr}'"

        # 3. Aggressiver Link-Spam (z. B. mehr als 15 Links in einer kurzen Nachricht)
        links = re.findall(r"https?://[^\s<>\"']+", body_text)
        if len(links) > 20 and len(body_text) < 1500:
            return True, f"Auffälliger Link-Spam erkannt ({len(links)} Links im Text)"

        return False, "Unauffällig"


# ==============================================================================
# Schutz- & Vorlagen-Modul: Mahnungs- & Inkasso-Guard
# ==============================================================================

MAHNUNG_INKASSO_KEYWORDS = [
    "mahnung", "inkasso", "aktenzeichen", "hauptforderung",
    "verzugszinsen", "verzugsschaden", "frist", "forderung",
    "inkassokosten", "inkassogebühren", "vorgerichtliche mahnkosten",
    "zahlungserinnerung", "letzte mahnung", "mahnbescheid",
    "vollstreckungsbescheid", "titulierungsankündigung", "titulierung",
    "pair finance", "creditreform", "eos", "ksp", "universum",
    "lowell", "riverty", "coeo", "infoscore", "atradius",
    "forderungsaufstellung", "schadensersatz", "zahlungsaufforderung"
]

KNOWN_INKASSO_ENTITIES = [
    "PAIR Finance", "EOS", "Creditreform", "KSP", "Universum Inkasso",
    "Lowell", "Riverty", "coeo Inkasso", "Infoscore", "Atradius",
    "B2Impact", "Intrum", "Paigo", "Advanzia", "Klarna"
]


class MahnungInkassoGuard:
    """
    Schutz- & Vorlagen-Modul für Mahnungen und Inkassofälle (z. B. PAIR Finance / Gläubiger):
    - Früherkennung von Mahnungen, Inkassofällen, Fristen, Hauptforderungen und Aktenzeichen.
    - Präzise Einstufung der Mahnstufe (Zahlungserinnerung bis gerichtlicher Mahnbescheid).
    - Rechtssichere deutsche Musterschreiben:
      * Widerspruch gegen unberechtigte Forderungen & Inkassogebühren (§ 4 Abs. 5 RDGEG / § 174 BGB)
      * Angebot realistischer Ratenzahlung AUSDRÜCKLICH OHNE SCHULDANERKENNTNIS (§ 781 BGB)
    """

    @classmethod
    def is_mahnung_or_inkasso(cls, text: str, filename: str = "") -> bool:
        combined = f"{filename} {text}".lower()
        if "inkasso" in combined or any(entity.lower() in combined for entity in KNOWN_INKASSO_ENTITIES):
            return True
        if "mahnung" in combined or "mahnbescheid" in combined or "zahlungserinnerung" in combined:
            return True
        hits = sum(1 for kw in MAHNUNG_INKASSO_KEYWORDS if kw in combined)
        return hits >= 2

    @classmethod
    def detect_mahnstufe(cls, text: str, filename: str = "") -> str:
        combined = f"{filename} {text}".lower()
        first_lines = "\n".join(text.splitlines()[:8]).lower()

        # 1. Echter gerichtlicher Mahn- oder Vollstreckungsbescheid
        is_court = any(court in combined for court in ["amtsgericht", "zentrales mahngericht", "mahnantrag", "vollstreckungsgericht"])
        if any(term in combined for term in ["vollstreckungsbescheid", "gerichtlicher mahnbescheid"]):
            return "Mahnbescheid / Gerichtliche Mahnung"
        if ("mahnbescheid" in first_lines or "mahnbescheid" in filename.lower()) and (is_court or "antrag auf" in combined):
            return "Mahnbescheid / Gerichtliche Mahnung"

        # 2. Inkasso (Büro, Inkassoverfahren, Abtretung / Titulierungsankündigung)
        if any(entity.lower() in combined for entity in KNOWN_INKASSO_ENTITIES):
            return "Inkassoschreiben"
        if any(term in combined for term in ["inkassoverfahren", "inkassoschreiben", "inkassoauftrag", "titulierungsankündigung", "titulierung"]):
            return "Inkassoschreiben"
        if "inkasso" in combined and "vor inkasso" not in combined and "an ein inkasso" not in combined:
            return "Inkassoschreiben"

        # 3. Spezifische Mahnstufen vor Inkasso (1., 2., 3. / Letzte Mahnung)
        if any(term in combined for term in ["letzte mahnung", "3. mahnung", "dritte mahnung"]):
            return "Letzte Mahnung"
        if any(term in combined for term in ["2. mahnung", "zweite mahnung"]):
            return "2. Mahnung"
        if any(term in combined for term in ["1. mahnung", "erste mahnung"]):
            return "1. Mahnung"

        # 4. Zahlungserinnerung / allgemeine Mahnung
        if "zahlungserinnerung" in combined:
            return "Zahlungserinnerung"
        if "mahnung" in combined:
            return "Mahnung"
        if "inkasso" in combined:
            return "Inkassoschreiben"
        return "Zahlungsaufforderung"

    @classmethod
    def extract_deadline(cls, text: str) -> Tuple[Optional[str], Optional[int], str]:
        """Extrahiert Frist oder Zahlungsziel und berechnet Dringlichkeitsstufe."""
        today = datetime.now().date()
        date_pattern = r"(?:zahlung(?:sziel)?\s*(?:bis|zum)?|frist(?:\s*bis)?|bis\s*zum|bis\s*spätestens|spätestens\s*bis)\s*[:\s]*(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})"
        m = re.search(date_pattern, text, re.IGNORECASE)

        if m:
            d, month, y = m.groups()
            y = f"20{y}" if len(y) == 2 else y
            try:
                deadline = datetime(int(y), int(month), int(d)).date()
                deadline_str = deadline.strftime("%Y-%m-%d")
                days_left = (deadline - today).days
                if days_left < 0:
                    status = "🚨 ÜBERFÄLLIG"
                elif days_left <= 5:
                    status = "⚠️ DRINGEND"
                else:
                    status = "ℹ️ OFFEN"
                return deadline_str, days_left, status
            except ValueError:
                pass

        rel_pattern = r"(?:innerhalb\s*von\s*)(\d+)\s*(?:tagen|werktagen)"
        rm = re.search(rel_pattern, text, re.IGNORECASE)
        if rm:
            days = int(rm.group(1))
            deadline = today + timedelta(days=days)
            deadline_str = deadline.strftime("%Y-%m-%d")
            status = "⚠️ DRINGEND" if days <= 5 else "ℹ️ OFFEN"
            return deadline_str, days, status

        return None, None, "❓ UNBEKANNT"

    @classmethod
    def analyze_document(cls, text: str, filename: str = "") -> Dict[str, Any]:
        """Analysiert Text auf Falldaten: Gläubiger, AZ, Hauptforderung, Gesamtforderung, Frist."""
        is_relevant = cls.is_mahnung_or_inkasso(text, filename)
        mahnstufe = cls.detect_mahnstufe(text, filename)
        frist_datum, frist_tage, urgency = cls.extract_deadline(text)

        # Gläubiger & Inkassodienstleister ermitteln
        inkassobuero = None
        for entity in KNOWN_INKASSO_ENTITIES:
            if entity.lower() in f"{filename} {text}".lower():
                inkassobuero = entity
                break

        glaeubiger = inkassobuero or "Unbekannt"
        g_match = re.search(
            r"(?:Gläubiger(?:in)?|Auftraggeber(?:in)?|Forderung der|im Namen von|namens und im Auftrag der)[:\s]+([A-Za-z0-9\s&äöüÄÖÜß\.\-]+?)(?:[\n,\.;])",
            text,
            re.IGNORECASE
        )
        if g_match:
            cand = g_match.group(1).strip()
            if 2 < len(cand) < 40:
                glaeubiger = cand

        fn_match = re.match(r"^\d{4}-\d{2}-\d{2}_(.+?)_(\d+[\.,]\d{2}EUR)_(.+?)\.pdf$", filename)
        if fn_match:
            fn_glaeubiger = fn_match.group(1).replace("_", " ")
            if glaeubiger == "Unbekannt" or (inkassobuero and glaeubiger == inkassobuero):
                glaeubiger = fn_glaeubiger

        # Aktenzeichen ermitteln
        aktenzeichen = "OhneAZ"
        az_match = re.search(
            r"(?:Aktenzeichen|AZ|Vorgangs-?Nr|Geschäfts-?Nr|Kassenzeichen|Referenz-?Nr|Unser Zeichen|Ihr Zeichen)[:\s]+([A-Za-z0-9\-_./]+)",
            text,
            re.IGNORECASE
        )
        if az_match:
            aktenzeichen = az_match.group(1).replace("/", "-")
        elif fn_match:
            aktenzeichen = fn_match.group(3).replace("_", "-")

        # Hauptforderung & Gesamtforderung
        hauptforderung = None
        hf_match = re.search(r"(?:Hauptforderung|Hauptbetrag|Ursprungsforderung)[:\s]*(\d+[\.,]\d{2})\s*(?:€|EUR)?", text, re.IGNORECASE)
        if hf_match:
            hauptforderung = hf_match.group(1).replace(".", ",") + " €"

        gesamtforderung = None
        gf_match = re.search(
            r"(?:Gesamtforderung|Gesamtbetrag|zu\s+zahlen(?:der)?\s+Betrag|Zahlbetrag|Überweisungsbetrag)[:\s]*(\d+[\.,]\d{2})\s*(?:€|EUR)?",
            text,
            re.IGNORECASE
        )
        if gf_match:
            gesamtforderung = gf_match.group(1).replace(".", ",") + " €"
        elif fn_match:
            gesamtforderung = fn_match.group(2).replace("EUR", " €")

        if not gesamtforderung and hauptforderung:
            gesamtforderung = hauptforderung
        elif not gesamtforderung:
            b_match = re.search(r"(\d+[\.,]\d{2})\s*(?:€|EUR|Euro)", text, re.IGNORECASE)
            gesamtforderung = (b_match.group(1).replace(".", ",") + " €") if b_match else "0,00 €"

        if not hauptforderung:
            hauptforderung = gesamtforderung

        # Verzugszinsen
        verzugszinsen = "0,00 €"
        vz_match = re.search(r"(?:Verzugszinsen|Zinsen)[:\s]*(\d+[\.,]\d{2})\s*(?:€|EUR)?", text, re.IGNORECASE)
        if vz_match:
            verzugszinsen = vz_match.group(1).replace(".", ",") + " €"

        return {
            "is_mahnung_inkasso": is_relevant,
            "mahnstufe": mahnstufe,
            "glaeubiger": glaeubiger,
            "inkassobuero": inkassobuero,
            "aktenzeichen": aktenzeichen,
            "hauptforderung": hauptforderung,
            "gesamtforderung": gesamtforderung,
            "verzugszinsen": verzugszinsen,
            "frist_datum": frist_datum,
            "frist_tage": frist_tage,
            "urgency": urgency,
            "filename": filename
        }

    @classmethod
    def generate_template(cls, draft_type: str, details: Dict[str, Any]) -> str:
        """
        Erzeugt ein rechtssicheres deutsches Musterschreiben:
        - widerspruch: Vollmachtsrüge § 174 BGB, Bestreiten unberechtigter Inkassogebühren § 4 Abs. 5 RDGEG / § 254 BGB, DSGVO-Sperre
        - ratenzahlung: Angebot realistischer Ratenzahlung AUSDRÜCKLICH OHNE SCHULDANERKENNTNIS (§ 781 BGB)
        """
        heute = datetime.now().strftime("%d.%m.%Y")
        antwortfrist = (datetime.now() + timedelta(days=14)).strftime("%d.%m.%Y")
        glaeubiger = details.get("inkassobuero") or details.get("glaeubiger") or "Gläubiger / Inkassodienstleister"
        aktenzeichen = details.get("aktenzeichen") or "Ohne Zeichen"
        hauptforderung = details.get("hauptforderung") or details.get("betrag") or "0,00 €"
        gesamtforderung = details.get("gesamtforderung") or details.get("betrag") or "0,00 €"
        belegdatum = details.get("datum") or details.get("belegdatum") or heute
        monatsrate = details.get("monatsrate") or "30,00"
        erster_monat = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=1).strftime("%d.%m.%Y")

        if draft_type.lower() == "ratenzahlung":
            return f"""# Angebot einer Ratenzahlungsvereinbarung (Ohne Anerkenntnis einer Rechtspflicht)

**Absender:**
[Vorname Nachname]
[Straße Hausnummer]
[PLZ Ort]
[E-Mail-Adresse]

**Empfänger:**
{glaeubiger}
[Abteilung Forderungsmanagement / Inkasso]
[Straße / Postfach]
[PLZ Ort]

**Datum:** {heute}

---

### **Angebot einer Ratenzahlungsvereinbarung – Ausdrücklich ohne Anerkenntnis einer Rechtspflicht**
**Ihr Aktenzeichen:** {aktenzeichen}
**Ihre Zahlungsaufforderung bezüglich Gesamtbetrag:** {gesamtforderung}

Sehr geehrte Damen und Herren,

in vorbezeichneter Angelegenheit beziehe ich mich auf Ihre Zahlungsaufforderung bezüglich des Aktenzeichens **{aktenzeichen}**.

> [!IMPORTANT]
> **RECHTSVERBINDLICHER VORBEHALT:**
> Dieses Schreiben und das nachfolgende Zahlungsangebot erfolgen **AUSDRÜCKLICH OHNE ANERKENNTNIS EINER RECHTSPFLICHT** sowie ohne Präjudiz für die Sach- und Rechtslage. Es stellt **KEIN Schuldanerkenntnis** im Sinne der §§ 780, 781 BGB dar.

Zur Vermeidung weiterer streitiger Auseinandersetzungen und unzweckmäßiger Kosten unterbreite ich Ihnen hiermit folgenden tragfähigen Vergleichsvorschlag:

1. **Ratenzahlungsangebot:**
   Ich biete an, die offene Forderung in monatlichen Teilbeträgen in Höhe von:
   **{monatsrate} €**
   jeweils zum **1. eines jeden Monats**, beginnend mit dem **{erster_monat}**, auf das von Ihnen zu benennende Konto zu überweisen.

2. **Bedingungen des Vergleichsvorschlags:**
   - **Kosten- und Zinsstopp:** Für die Dauer der pünktlichen Ratenzahlung werden keine weiteren Zinsen, Verzugspauschalen oder Kontoführungsgebühren berechnet.
   - **Ruhen von Maßnahmen:** Sämtliche Mahn-, Betreibungs- und Vollstreckungsmaßnahmen (einschließlich gerichtlicher Mahnverfahren) werden für die Dauer der Ratenzahlung ausgesetzt.
   - **Keine Auskunftei-Einmeldung:** Von einer Einmeldung an Wirtschaftsauskunfteien (SCHUFA Holding AG, Creditreform, CRIF etc.) wird abgesehen.
   - **Kostenbegrenzung:** Auf überhöhte Inkassogebühren und Einigungsgebühren wird im Rahmen dieser einvernehmlichen Regelung verzichtet.

3. **Zahlungsmodalitäten:**
   Bitte bestätigen Sie mir die Annahme dieses Ratenplans schriftlich und teilen Sie mir die maßgebliche Bankverbindung (IBAN, BIC) sowie den zwingend anzugebenden Verwendungszweck mit.

Ich erwarte Ihre schriftliche Bestätigung bis zum:

**Frist zur Rückmeldung: {antwortfrist}**

Mit freundlichen Grüßen,

*(Unterschrift)*
[Vorname Nachname]
"""

        # Standard: Widerspruch gegen Inkassoforderung & unberechtigte Gebühren
        return f"""# Widerspruch gegen Inkassoforderung / Bestreiten unberechtigter Gebühren

**Absender:**
[Vorname Nachname]
[Straße Hausnummer]
[PLZ Ort]
[E-Mail-Adresse]

**Empfänger:**
{glaeubiger}
[Abteilung Forderungsmanagement / Inkasso]
[Straße / Postfach]
[PLZ Ort]

**Datum:** {heute}

---

### **Widerspruch gegen Zahlungsaufforderung und Zurückweisung unberechtigter Gebühren**
**Ihr Zeichen / Aktenzeichen:** {aktenzeichen}
**Hauptforderung:** {hauptforderung}
**Gesamtforderung:** {gesamtforderung}
**Datum Ihres Schreibens:** {belegdatum}

Sehr geehrte Damen und Herren,

in vorbezeichneter Angelegenheit nehme ich Bezug auf Ihr Schreiben vom {belegdatum}, in welchem Sie bezüglich des Aktenzeichens **{aktenzeichen}** einen Betrag in Höhe von **{gesamtforderung}** geltend machen.

Ich weise die von Ihnen geltend gemachte Forderung vollumfänglich dem Grunde und der Höhe nach zurück und lege hiermit form- und fristgerecht

# **W I D E R S P R U C H**

ein.

Zur Begründung führe ich Folgendes aus:

1. **Substantiiertes Bestreiten der Hauptforderung (§ 11a / § 13a RDG):**
   Die von Ihnen behauptete Hauptforderung in Höhe von {hauptforderung} ist für mich dem Grunde und der Höhe nach nicht nachvollziehbar bzw. unberechtigt. Gemäß den gesetzlichen Darlegungspflichten nach § 11a / § 13a RDG fordere ich Sie hiermit auf, eine detaillierte, aufgeschlüsselte und nachvollziehbare Forderungsaufstellung vorzulegen. Aus dieser müssen sich der konkrete Vertragsgegenstand, das Zustandekommen des Vertrags, das Datum des Vertragsschlusses sowie die Fälligkeit zweifelsfrei ergeben.

2. **Zurückweisung unberechtigter Inkassogebühren (§ 4 Abs. 5 RDGEG / § 254 BGB):**
   Die von Ihnen beanspruchten Inkassovergütungen, Kontoführungs- und Geschäftsgebühren übersteigen das gesetzlich zulässige Maß erheblich und verstoßen gegen die gesetzliche Schadensminderungspflicht des Gläubigers (§ 254 BGB). Gemäß § 4 Abs. 5 RDGEG darf die erstattungsfähige Inkassovergütung die einem Rechtsanwalt nach dem Rechtsanwaltsvergütungsgesetz (RVG) zustehende Vergütung für dieselbe Tätigkeit keinesfalls überschreiten. Pauschalierte Fantasiegebühren für Standard-Mahnanschreiben weise ich vollumfänglich zurück.

3. **Rüge mangelnder Vollmachtsvorlage im Original (§ 174 BGB):**
   Sofern Sie im Namen eines Gläubigers auftreten, weise ich Ihre Zahlungsaufforderung mangels Vorlage einer schriftlichen Original-Vollmachtsurkunde ausdrücklich gemäß § 174 BGB zurück. Ich fordere Sie auf, eine ordnungsgemäße, vom Gläubiger eigenhändig unterzeichnete Vollmachtsurkunde unverzüglich im Original nachzureichen.

4. **Untersagung der Datenübermittlung an Auskunfteien (Art. 21 DSGVO / § 31 BDSG):**
   Da die Forderung hiermit ausdrücklich und substantiiert bestritten wird, ist eine Datenübermittlung an Wirtschaftsauskunfteien (insbesondere SCHUFA Holding AG, Creditreform, CRIF etc.) nach § 31 Abs. 2 Satz 1 Nr. 4 BDSG unzulässig. Ich widerspreche hiermit gem. Art. 21 DSGVO vorsorglich jeder Weitergabe meiner personenbezogenen Daten. Bei einer rechtswidrigen Einmeldung werde ich unverzüglich Schadensersatzansprüche geltend machen und Beschwerde bei der zuständigen Landesdatenschutzbehörde einlegen.

Ich fordere Sie auf, die Einleitung weiterer Maßnahmen bis zur Klärung der Sachlage einzustellen und mir den Eingang dieses Widerspruchs schriftlich zu bestätigen.

**Frist zur schriftlichen Stellungnahme: {antwortfrist}**

Mit freundlichen Grüßen,

*(Unterschrift)*
[Vorname Nachname]
"""


# ==============================================================================
# Modell-Routing & Ollama API
# ==============================================================================

def query_ollama(
    model: str,
    prompt: str,
    system_prompt: str = "",
    temperature: float = 0.2,
    num_ctx: int = NUM_CTX,
    timeout: int = 120
) -> str:
    """Sendet einen Prompt an die lokale Ollama-Instanz auf Fox."""
    url = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "temperature": temperature
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "").strip()
    except Exception as e:
        logger.error("Fehler bei Ollama-Anfrage an Modell %s: %s", model, e)
        raise RuntimeError(f"Ollama-Inferenz fehlgeschlagen ({model}): {e}")


def extract_metadata_qwen(text_sample: str) -> Dict[str, Any]:
    """
    Modell-Routing 1 (qwen:vram):
    Extrahiert Gläubiger/Absender, Betrag, Aktenzeichen und Belegdatum.
    """
    system_prompt = (
        "Du bist ein hochpräziser Dokumenten-Indexer für Büro- und Rechtsdokumente. "
        "Deine Aufgabe: Analysiere den Textauszug eines Dokuments (z.B. Rechnung, Mahnung, Inkasso-Schreiben) "
        "und extrahiere die Kerndaten im JSON-Format.\n"
        "Regeln:\n"
        "- datum: Beleg-/Schreibendatum im Format YYYY-MM-DD (falls unklar, heutiges Datum nutzen).\n"
        "- glaeubiger: Name der Firma, Behörde oder des Gläubigers (z. B. 'PAIR Finance', 'Telekom', 'Finanzamt'). "
        "Bereinige Rechtsformen wie GmbH, AG auf das Wesentliche.\n"
        "- betrag: Der finale Gesamt- oder Forderungsbetrag als Zahl mit Komma (z. B. '372,41' oder '49,99'). Keine Währungszeichen.\n"
        "- aktenzeichen: Das Aktenzeichen, die Rechnungsnummer, Kunden- oder Vorgangsnummer (z. B. 'AZ-98124', 'RE-2026-01').\n"
        "- Gib AUSSCHLIESSLICH ein valides JSON-Objekt ohne Markdown-Codeblöcke oder Erklärungen zurück."
    )

    prompt = (
        f"Dokument-Textauszug:\n"
        f"\"\"\"\n{text_sample[:4000]}\n\"\"\"\n\n"
        f"Antworte nur mit JSON:\n"
        f"{{\n"
        f'  "datum": "YYYY-MM-DD",\n'
        f'  "glaeubiger": "Name",\n'
        f'  "betrag": "0,00",\n'
        f'  "aktenzeichen": "Nummer"\n'
        f"}}"
    )

    raw_response = query_ollama(
        model=FAST_MODEL,
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=0.1,
        num_ctx=NUM_CTX,
        timeout=60
    )

    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()

    data = {}
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
            except Exception:
                pass

    if not data.get("datum"):
        d_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", text_sample)
        if d_match:
            d, m, y = d_match.groups()
            y = f"20{y}" if len(y) == 2 else y
            data["datum"] = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        else:
            data["datum"] = datetime.now().strftime("%Y-%m-%d")

    if not data.get("betrag"):
        b_match = re.search(r"(\d+[\.,]\d{2})\s*(?:€|EUR|Euro)", text_sample, re.IGNORECASE)
        data["betrag"] = b_match.group(1).replace(".", ",") if b_match else "0,00"

    if not data.get("glaeubiger"):
        data["glaeubiger"] = "Unbekannt"

    if not data.get("aktenzeichen"):
        az_match = re.search(r"(?:Aktenzeichen|AZ|Rechnungs-?Nr|Rechnung|Vorgang)[:\s]+([A-Za-z0-9\-_/]+)", text_sample, re.IGNORECASE)
        data["aktenzeichen"] = az_match.group(1).replace("/", "-") if az_match else "OhneAZ"

    return data


def draft_document_darkidol(
    draft_type: str,
    context: str,
    details: Optional[Dict[str, Any]] = None
) -> str:
    """
    Modell-Routing 2 (darkidol:ram):
    Generiert anspruchsvolle juristische Widersprüche, Ratenzahlungsangebote (ohne Schuldanerkenntnis),
    Anfragen oder Bewerbungsanschreiben mit automatischem Musterschreiben-Fallback.
    """
    details = details or {}
    type_prompts = {
        "widerspruch": (
            "Du bist ein versierter rechtlicher Assistent im deutschen Zivil- und Inkassorecht. "
            "Formuliere einen förmlichen, fundierten und bestimmten Widerspruch gegen eine unberechtigte Forderung, "
            "eine Mahnung oder ein Inkassoschreiben (z.B. PAIR Finance, EOS, Creditreform). "
            "Bestreite die Hauptforderung sowie überhöhte Inkassokosten nach § 4 Abs. 5 RDGEG / § 254 BGB. "
            "Fordere eine detaillierte Forderungsaufstellung (§ 11a/13a RDG) und Vollmachtsurkunde nach § 174 BGB an. "
            "Untersage Datenweitergabe an Auskunfteien (SCHUFA etc.) gem. Art. 21 DSGVO / § 31 BDSG."
        ),
        "ratenzahlung": (
            "Du bist ein versierter rechtlicher Assistent im deutschen Zivil- und Zwangsvollstreckungsrecht. "
            "Formuliere ein verbindliches, tragfähiges Angebot zur Ratenzahlung an Gläubiger oder Inkasso. "
            "WICHTIGSTE VORSCHRIFT: Das Angebot muss AUSDRÜCKLICH OHNE ANERKENNTNIS EINER RECHTSPFLICHT und "
            "OHNE SCHULDANERKENNTNIS nach §§ 780, 781 BGB abgegeben werden. "
            "Verlange Zins- und Kostenstopp, Ruhendstellung aller Vollstreckungsmaßnahmen und Absehen von SCHUFA-Meldungen."
        ),
        "anfrage": (
            "Du bist ein professioneller Büro-Assistent. Formuliere eine höfliche, aber rechtlich präzise schriftliche "
            "Anfrage zu einem Vorgang (z. B. Bitte um Rechnungsaufschlüsselung, Fristverlängerung oder Belegkopie)."
        ),
        "bewerbung": (
            "Du bist ein profilierter Karriereberater und Ghostwriter für Bewerbungsunterlagen. "
            "Formuliere ein aussagekräftiges, modernes und überzeugendes Anschreiben (Cover Letter) auf Deutsch, "
            "abgestimmt auf die vorgegebenen Fähigkeiten, Qualifikationen und die Stellenbeschreibung."
        )
    }

    std_template = ""
    if draft_type.lower() in ["widerspruch", "ratenzahlung"]:
        std_template = MahnungInkassoGuard.generate_template(draft_type, details)

    system_prompt = type_prompts.get(
        draft_type.lower(),
        "Du bist ein sachkundiger Assistent für geschäftliche und amtliche Korrespondenz auf Deutsch."
    )

    prompt = (
        f"Erstelle einen vollständigen, sofort versandfertigen Schriftsatz für die Kategorie '{draft_type}'.\n\n"
        f"Gegebener Sachverhalt & Dokumentenkontext:\n"
        f"\"\"\"\n{context}\n\"\"\"\n\n"
        f"Zusätzliche Details: {json.dumps(details, ensure_ascii=False)}\n\n"
    )
    if std_template:
        prompt += (
            f"Rechtssicheres Musterschreiben als verbindliche juristische Leitlinie:\n"
            f"\"\"\"\n{std_template}\n\"\"\"\n\n"
        )
    prompt += (
        f"Anforderungen:\n"
        f"- Korrektes deutsches Briefformat (Absender, Empfänger, Datum, Betreff mit Aktenzeichen, Anrede, Textkörper, Grußformel).\n"
        f"- Sachlich, souverän, durchdacht und unmissverständlich.\n"
        f"- Zwingende Einhaltung der Schutzrechte (§ 4 Abs. 5 RDGEG, § 174 BGB, kein Schuldanerkenntnis gem. § 781 BGB).\n"
        f"- Gebe den fertigen Brief direkt in sauberem Markdown aus."
    )

    try:
        logger.info("Sende Entwurfsanfrage (%s) an Deep-Modell '%s'...", draft_type, DEEP_MODEL)
        return query_ollama(
            model=DEEP_MODEL,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            num_ctx=NUM_CTX,
            timeout=180
        )
    except Exception as e:
        logger.warning(
            "Ollama-Inferenz für '%s' via '%s' nicht verfügbar (%s). "
            "Verwende das rechtssichere, standardisierte deutsche Musterschreiben.",
            draft_type, DEEP_MODEL, e
        )
        if std_template:
            return std_template
        raise RuntimeError(f"Weder LLM-Inferenz noch Vorlage verfügbar: {e}")


# ==============================================================================
# Revisionssichere Archivierung (10 Jahre Aufbewahrungspflicht)
# ==============================================================================

def parse_date_from_filename_or_mtime(file_path: Path) -> datetime:
    """Ermittelt das Belegdatum aus dem Dateinamen oder der Datei-mtime."""
    name = file_path.name
    # 1. Format: YYYY-MM-DD_...
    match_ymd = re.match(r"^(\d{4})-(\d{2})-(\d{2})_", name)
    if match_ymd:
        try:
            return datetime(int(match_ymd.group(1)), int(match_ymd.group(2)), int(match_ymd.group(3)))
        except ValueError:
            pass

    # 2. Format: YYYYMMDD_...
    match_compact = re.match(r"^(\d{4})(\d{2})(\d{2})_", name)
    if match_compact:
        try:
            return datetime(int(match_compact.group(1)), int(match_compact.group(2)), int(match_compact.group(3)))
        except ValueError:
            pass

    # 3. Fallback: Datei-Änderungsdatum
    stat = file_path.stat()
    return datetime.fromtimestamp(stat.st_mtime)


def archive_aged_documents(
    paths: Optional[BueroPaths] = None,
    days_threshold: int = ACTIVE_WINDOW_DAYS,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Revisionssichere Archivierung:
    Verschiebt Vorgänge, die älter als `days_threshold` (Standard 90 Tage) sind,
    geordnet nach Kalenderjahr in das Langzeitarchiv:
      - Archiv/YYYY/E-Mail/
      - Archiv/YYYY/schriftlich/
    Keine Belege werden gelöscht!
    """
    paths = paths or BueroPaths()
    cutoff_date = datetime.now() - timedelta(days=days_threshold)
    archived_records = {"E-Mail": [], "schriftlich": []}

    categories = [
        ("E-Mail", paths.email),
        ("schriftlich", paths.schriftlich)
    ]

    for category, folder in categories:
        if not folder.exists():
            continue

        for f in folder.glob("*.pdf"):
            if not f.is_file():
                continue

            doc_date = parse_date_from_filename_or_mtime(f)
            if doc_date < cutoff_date:
                year = doc_date.year
                target_archive_dir = paths.get_archive_dir(year, category)
                target_file = target_archive_dir / f.name

                logger.info(
                    "Archiviere Dokument (>%d Tage alt, Belegdatum %s): %s -> %s",
                    days_threshold, doc_date.strftime("%Y-%m-%d"), f.name, target_archive_dir
                )

                if not dry_run:
                    try:
                        shutil.move(str(f), str(target_file))
                        archived_records[category].append({
                            "original": str(f),
                            "archived": str(target_file),
                            "year": year,
                            "date": doc_date.strftime("%Y-%m-%d")
                        })
                    except Exception as e:
                        logger.error("Fehler beim Verschieben ins Archiv für %s: %s", f.name, e)
                else:
                    archived_records[category].append({
                        "original": str(f),
                        "archived": str(target_file),
                        "year": year,
                        "date": doc_date.strftime("%Y-%m-%d"),
                        "dry_run": True
                    })

    total = len(archived_records["E-Mail"]) + len(archived_records["schriftlich"])
    logger.info("Revisionssichere Archivierung abgeschlossen: %d Dokument(e) archiviert.", total)
    return archived_records


def get_active_workspace_files(
    paths: Optional[BueroPaths] = None,
    max_days: int = ACTIVE_WINDOW_DAYS
) -> List[Path]:
    """
    Liefert nur die Dokumente des aktiven Arbeitsbestands (letzte 90 Tage),
    um den LLM-Kontext für qwen:vram sauber und kompakt zu halten.
    """
    paths = paths or BueroPaths()
    cutoff_date = datetime.now() - timedelta(days=max_days)
    active_files = []

    for folder in [paths.schriftlich, paths.email]:
        if not folder.exists():
            continue
        for f in folder.glob("*.pdf"):
            if f.is_file():
                doc_date = parse_date_from_filename_or_mtime(f)
                if doc_date >= cutoff_date:
                    active_files.append(f)

    return sorted(active_files, key=lambda x: x.name)


# ==============================================================================
# Routine 1: Gmail -> Klassifizierung, IT-Sicherheit & Ingest
# ==============================================================================

def sanitize_filename(name: str) -> str:
    """Entfernt problematische Sonderzeichen für Dateinamen."""
    cleaned = re.sub(r'[\s/\\:*?"<>|]+', '_', name)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or "Anhang"


def classify_email(subject: str, sender: str, body_text: str) -> str:
    """
    Klassifiziert eine E-Mail in:
    - 'PHISHING' (Sofort isolieren)
    - 'SPAM' (Unwiderruflich löschen)
    - 'WERBUNG' (In Werbung-Ordner einsortieren)
    - 'RECHNUNG' (Rechnung/Beleg für E-Mail Ingest)
    - 'SONSTIGES' (Reguläre Mail)
    """
    # 1. Phishing-Prüfung
    is_phish, _ = SecurityGuard.check_phishing(sender, subject, body_text)
    if is_phish:
        return "PHISHING"

    combined = f"{subject} {sender} {body_text}".lower()

    # 2. Spam-Prüfung
    if any(kw in combined for kw in SPAM_KEYWORDS):
        return "SPAM"

    # 3. Rechnungs-Prüfung
    if any(kw in subject.lower() for kw in RECHNUNG_KEYWORDS):
        return "RECHNUNG"

    # 4. Werbung/Newsletter-Prüfung
    if any(kw in combined for kw in WERBUNG_KEYWORDS):
        return "WERBUNG"

    if any(kw in combined for kw in RECHNUNG_KEYWORDS[:5]):
        return "RECHNUNG"

    return "SONSTIGES"


def ensure_gmail_label(mail: imaplib.IMAP4_SSL, label_name: str) -> bool:
    """Stellt sicher, dass ein Zielordner/Label in Gmail existiert."""
    try:
        status, folder_list = mail.list()
        if status == "OK":
            for f_info in folder_list:
                if f'"{label_name}"' in f_info.decode("utf-8", errors="replace"):
                    return True
        mail.create(label_name)
        return True
    except Exception:
        return False


def fetch_gmail_invoices(
    paths: Optional[BueroPaths] = None,
    creds_file: Path = GMAIL_CREDS_FILE,
    max_mails: int = 25,
    mark_as_read: bool = True
) -> Dict[str, Any]:
    """
    Routine 1 mit Mail-Filterung & Phishing-Schutz:
    - Spam: Dauerhaft gelöscht (+FLAGS \\Deleted & EXPUNGE).
    - Werbung: In Google Drive 'Werbung/' und Gmail-Ordner 'Werbung'.
    - Phishing: Sofort isoliert in 'Isoliert/'.
    - Rechnungen: Sichere PDFs nach 'E-Mail/' herunterladen.
    """
    paths = paths or BueroPaths()
    results = {
        "invoices_saved": [],
        "werbung_processed": [],
        "spam_deleted": 0,
        "phishing_isolated": 0
    }

    if not creds_file.is_file():
        logger.warning("Keine Gmail-Anmeldedaten unter %s gefunden. Überspringe IMAP-Sync.", creds_file)
        return results

    try:
        with open(creds_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        email_user = cfg.get("email")
        app_password = cfg.get("app_password", "").replace(" ", "")
    except Exception as e:
        logger.error("Konnte %s nicht lesen: %s", creds_file, e)
        return results

    if not email_user or not app_password:
        logger.error("Ungültige Anmeldedaten in %s", creds_file)
        return results

    logger.info("Verbinde mit Gmail IMAP (%s)...", email_user)
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(email_user, app_password)
        mail.select("inbox")

        # Prüfe Ordner 'Werbung'
        ensure_gmail_label(mail, "Werbung")

        # Gezielte Suche nach ungelesenen Mails
        matched_ids: Set[bytes] = set()
        for kw in ["Rechnung", "Beleg", "Invoice", "Mahnung", "Abrechnung", "Newsletter", "Angebot"]:
            search_query = f'(UNSEEN SUBJECT "{kw}")'
            st, d = mail.search(None, search_query)
            if st == "OK" and d[0]:
                for mid in d[0].split():
                    matched_ids.add(mid)

        # Fallback auf allgemeine UNSEEN Mails
        if not matched_ids:
            st, d = mail.search(None, "UNSEEN")
            if st == "OK" and d[0]:
                for mid in d[0].split():
                    matched_ids.add(mid)

        if not matched_ids:
            logger.info("Keine relevanten ungelesenen Nachrichten im Posteingang vorhanden.")
            mail.logout()
            return results

        sorted_ids = sorted(list(matched_ids), key=lambda x: int(x), reverse=True)[:max_mails]
        logger.info("%d ungesehene Mail(s) im Fokus. Führe Klassifizierung & Sicherheitsprüfung durch...", len(sorted_ids))

        parser = BytesParser(policy=policy.default)
        expunge_needed = False

        for msg_id in sorted_ids:
            res, data = mail.fetch(msg_id, "(RFC822)")
            if res != "OK":
                continue

            raw_email = data[0][1]
            msg = parser.parsebytes(raw_email)

            subject = str(msg["Subject"] or "Kein Betreff")
            sender = str(msg["From"] or "Unbekannt")
            date_str = str(msg["Date"] or "")

            # Textinhalt für Klassifizierung extrahieren (KEIN Link-Scraping / Browser-Aufruf!)
            body_parts = []
            for part in msg.walk():
                if part.get_content_type() in ["text/plain", "text/html"]:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body_parts.append(payload.decode("utf-8", errors="replace"))
            body_text = "\n".join(body_parts)

            category = classify_email(subject, sender, body_text)
            timestamp_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Fall A: PHISHING -> Sofort isolieren
            if category == "PHISHING":
                is_phish, reason = SecurityGuard.check_phishing(sender, subject, body_text)
                logger.warning("🚨 PHISHING ERKANNT: '%s' von '%s' -> %s", subject, sender, reason)
                quarantine_info = {
                    "timestamp": timestamp_prefix,
                    "sender": sender,
                    "subject": subject,
                    "reason": reason,
                    "date": date_str
                }
                meta_file = paths.isoliert / f"PHISHING_{timestamp_prefix}_{sanitize_filename(subject)[:40]}.json"
                meta_file.write_text(json.dumps(quarantine_info, indent=2, ensure_ascii=False), encoding="utf-8")
                results["phishing_isolated"] += 1
                if mark_as_read:
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                continue

            # Fall B: SPAM -> Unwiderruflich löschen
            elif category == "SPAM":
                logger.info("🗑️ SPAM gelöscht: '%s' von '%s'", subject, sender)
                mail.store(msg_id, "+FLAGS", "\\Deleted")
                expunge_needed = True
                results["spam_deleted"] += 1
                continue

            # Fall C: WERBUNG -> Nach 'Werbung/' und Gmail-Ordner 'Werbung'
            elif category == "WERBUNG":
                logger.info("📢 Werbung / Newsletter erkannt: '%s' von '%s'", subject, sender)
                werbung_info = {
                    "timestamp": timestamp_prefix,
                    "sender": sender,
                    "subject": subject,
                    "date": date_str
                }
                werbung_file = paths.werbung / f"Werbung_{timestamp_prefix}_{sanitize_filename(subject)[:40]}.json"
                werbung_file.write_text(json.dumps(werbung_info, indent=2, ensure_ascii=False), encoding="utf-8")

                # In Gmail nach Ordner 'Werbung' verschieben
                try:
                    mail.copy(msg_id, "Werbung")
                    mail.store(msg_id, "+FLAGS", "\\Deleted")
                    expunge_needed = True
                except Exception as e:
                    logger.debug("Konnte Mail nicht in Ordner Werbung kopieren: %s", e)
                    if mark_as_read:
                        mail.store(msg_id, "+FLAGS", "\\Seen")

                results["werbung_processed"].append(subject)
                continue

            # Fall D: RECHNUNG / DOKUMENT
            attachments = []
            for part in msg.walk():
                filename = part.get_filename()
                if filename:
                    payload = part.get_payload(decode=True)
                    if payload:
                        # IT-Sicherheits- & Phishing-Guard Prüfung
                        is_safe, sec_reason = SecurityGuard.inspect_attachment(filename, payload)
                        if not is_safe:
                            logger.error("🛑 SICHERHEITSVERSTOSS bei Anhang '%s': %s", filename, sec_reason)
                            # Verdächtigen Anhang in Quarantäne isolieren
                            sec_file = paths.isoliert / f"BLOCKED_{timestamp_prefix}_{sanitize_filename(filename)}"
                            sec_file.write_bytes(payload)
                            continue
                        attachments.append((filename, payload))

            if attachments:
                logger.info("✅ Sichere Rechnung erkannt: '%s' (%d PDF/Bild-Anhänge)", subject, len(attachments))
                for fn, content in attachments:
                    clean_fn = sanitize_filename(fn)
                    target_file = paths.email / f"{timestamp_prefix}_{clean_fn}"
                    target_file.write_bytes(content)
                    logger.info("Dokument sicher abgelegt: %s", target_file)

                    # Früherkennung Mahnungs- & Inkasso-Guard
                    mail_meta = MahnungInkassoGuard.analyze_document(f"{subject}\n{body_text}", fn)
                    if mail_meta.get("is_mahnung_inkasso"):
                        logger.warning(
                            "🚨 [INKASSO-GUARD] E-Mail Mahnung/Inkasso eingegangen: '%s' von '%s' | Gläubiger='%s', AZ='%s', Frist='%s'",
                            subject, sender,
                            mail_meta.get("glaeubiger"),
                            mail_meta.get("aktenzeichen"),
                            mail_meta.get("frist_datum") or "Keine"
                        )
                    results["invoices_saved"].append({
                        "file": str(target_file),
                        "subject": subject,
                        "sender": sender,
                        "date": date_str
                    })

                if mark_as_read:
                    mail.store(msg_id, "+FLAGS", "\\Seen")

        if expunge_needed:
            mail.expunge()

        mail.logout()
        logger.info(
            "Routine 1 abgeschlossen: %d Rechnungen gesichert, %d Werbung sortiert, %d Spam gelöscht, %d Phishing isoliert.",
            len(results["invoices_saved"]), len(results["werbung_processed"]),
            results["spam_deleted"], results["phishing_isolated"]
        )
    except Exception as e:
        logger.error("Fehler beim Gmail-Abruf: %s", e)

    return results


# ==============================================================================
# Routine 2: Scan-Indexer (PDF/OCR & Umbenennung mit qwen:vram)
# ==============================================================================

def extract_pdf_text_fast(pdf_path: Path) -> str:
    """Extrahiert Text aus digitalen PDFs blitzschnell via /usr/bin/pdftotext."""
    if not shutil.which("pdftotext"):
        return ""
    try:
        proc = subprocess.run(
            ["pdftotext", "-l", "3", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception as e:
        logger.debug("pdftotext fehlgeschlagen für %s: %s", pdf_path.name, e)
    return ""


def extract_pdf_text_ocr(pdf_path: Path) -> str:
    """OCR-Fallback für reine Bildscans via pdftoppm + tesseract."""
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        logger.warning("pdftoppm oder tesseract nicht verfügbar für OCR.")
        return ""

    with tempfile.TemporaryDirectory(prefix="buero_ocr_") as tmpdir:
        tmp_prefix = Path(tmpdir) / "page"
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "2", str(pdf_path), str(tmp_prefix)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=25
            )

            ocr_texts = []
            for img_file in sorted(Path(tmpdir).glob("page-*.png")):
                out_txt = Path(tmpdir) / f"{img_file.stem}_ocr"
                subprocess.run(
                    ["tesseract", str(img_file), str(out_txt)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30
                )
                txt_file = out_txt.with_suffix(".txt")
                if txt_file.is_file():
                    ocr_texts.append(txt_file.read_text(encoding="utf-8", errors="replace"))

            return "\n".join(ocr_texts).strip()
        except Exception as e:
            logger.error("OCR-Verarbeitung fehlgeschlagen für %s: %s", pdf_path.name, e)
            return ""


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Kombiniert pdftotext und OCR-Fallback."""
    text = extract_pdf_text_fast(pdf_path)
    if len(text.strip()) > 60:
        return text
    logger.info("Wenig/kein Text in %s. Starte Tesseract-OCR Fallback...", pdf_path.name)
    return extract_pdf_text_ocr(pdf_path)


def is_already_indexed(filename: str) -> bool:
    """Prüft, ob die Datei bereits dem Schema YYYY-MM-DD_Absender_BetragEUR_Aktenzeichen.pdf entspricht."""
    pattern = r"^\d{4}-\d{2}-\d{2}_.+_\d+([.,]\d{1,2})?EUR_.*\.pdf$"
    return bool(re.match(pattern, filename))


def format_currency_eur(val: str) -> str:
    """Formatiert Beträge sauber zu z. B. '372,41EUR'."""
    cleaned = re.sub(r"[^\d,\.]", "", val).strip()
    if not cleaned:
        return "0,00EUR"
    if "." in cleaned and "," not in cleaned:
        cleaned = cleaned.replace(".", ",")
    return f"{cleaned}EUR"


def index_scans(
    paths: Optional[BueroPaths] = None,
    dry_run: bool = False
) -> List[Dict[str, Any]]:
    """
    Routine 2:
    Überwacht 'schriftlich/', liest neue PDFs ein, extrahiert per Text/OCR Gläubiger,
    Betrag und Aktenzeichen (z. B. PAIR Finance 372,41 €) und benennt die Datei lokal
    um in 'YYYY-MM-DD_Absender_BetragEUR_Aktenzeichen.pdf'.
    """
    paths = paths or BueroPaths()
    scan_dir = paths.schriftlich
    results = []

    if not scan_dir.exists():
        logger.warning("Verzeichnis 'schriftlich' nicht gefunden: %s", scan_dir)
        return results

    pdf_files = [f for f in scan_dir.glob("*.pdf") if f.is_file()]
    logger.info("Überprüfe %d PDF(s) in %s...", len(pdf_files), scan_dir)

    for pdf in sorted(pdf_files):
        if is_already_indexed(pdf.name):
            logger.debug("Bereits indiziert: %s", pdf.name)
            continue

        logger.info("Indiziere Scan: %s", pdf.name)
        extracted_text = extract_text_from_pdf(pdf)

        if not extracted_text:
            logger.warning("Kein Text aus %s extrahierbar. Überspringe.", pdf.name)
            continue

        # Extraktion via qwen:vram
        meta = extract_metadata_qwen(extracted_text)
        datum = sanitize_filename(meta.get("datum", datetime.now().strftime("%Y-%m-%d")))
        glaeubiger = sanitize_filename(meta.get("glaeubiger", "Unbekannt"))
        betrag_str = format_currency_eur(meta.get("betrag", "0,00"))
        aktenzeichen = sanitize_filename(meta.get("aktenzeichen", "OhneAZ"))

        new_filename = f"{datum}_{glaeubiger}_{betrag_str}_{aktenzeichen}.pdf"
        target_path = scan_dir / new_filename

        # Kollisionsvermeidung
        counter = 1
        while target_path.exists() and target_path != pdf:
            new_filename = f"{datum}_{glaeubiger}_{betrag_str}_{aktenzeichen}_{counter}.pdf"
            target_path = scan_dir / new_filename
            counter += 1

        logger.info("Neuer Name für %s -> %s", pdf.name, new_filename)

        # Früherkennung Mahnungs- & Inkasso-Guard
        guard_meta = MahnungInkassoGuard.analyze_document(extracted_text, new_filename)
        if guard_meta.get("is_mahnung_inkasso"):
            logger.warning(
                "🚨 [INKASSO-GUARD] Mahnung/Inkasso erkannt: Gläubiger='%s', AZ='%s', Betrag='%s', Mahnstufe='%s', Frist='%s'",
                guard_meta.get("glaeubiger"),
                guard_meta.get("aktenzeichen"),
                guard_meta.get("gesamtforderung"),
                guard_meta.get("mahnstufe"),
                guard_meta.get("frist_datum") or "Keine"
            )

        if not dry_run:
            try:
                shutil.move(str(pdf), str(target_path))
                results.append({
                    "old_path": str(pdf),
                    "new_path": str(target_path),
                    "metadata": meta
                })
            except Exception as e:
                logger.error("Konnte Datei %s nicht umbenennen: %s", pdf.name, e)
        else:
            results.append({
                "old_path": str(pdf),
                "new_path": str(target_path),
                "metadata": meta,
                "dry_run": True
            })

    logger.info("Routine 2 abgeschlossen: %d Datei(en) indiziert und umbenannt.", len(results))
    return results



def find_case_document(paths: BueroPaths, case_query: str) -> Tuple[Optional[Path], Dict[str, Any]]:
    """
    Findet ein zu einem Fall (Name, Gläubiger oder Aktenzeichen) passendes Dokument
    in schriftlich/, E-Mail/ oder Archiv/.
    """
    q = case_query.strip().lower()
    search_dirs = [paths.schriftlich, paths.email, paths.archiv]

    # Stufe 1: Dateinamen prüfen
    for folder in search_dirs:
        if not folder.exists():
            continue
        for doc in sorted(folder.glob("**/*"), reverse=True):
            if doc.is_file() and doc.suffix.lower() in [".pdf", ".txt", ".md"]:
                if q in doc.name.lower():
                    text = extract_text_from_pdf(doc) if doc.suffix.lower() == ".pdf" else doc.read_text(encoding="utf-8", errors="replace")
                    meta = MahnungInkassoGuard.analyze_document(text, doc.name)
                    return doc, meta

    # Stufe 2: Textinhalt der aktiven Arbeitsdateien durchsuchen
    for folder in [paths.schriftlich, paths.email]:
        if not folder.exists():
            continue
        for doc in sorted(folder.glob("*"), reverse=True):
            if doc.is_file() and doc.suffix.lower() in [".pdf", ".txt"]:
                text = extract_text_from_pdf(doc) if doc.suffix.lower() == ".pdf" else doc.read_text(encoding="utf-8", errors="replace")
                if q in text.lower():
                    meta = MahnungInkassoGuard.analyze_document(text, doc.name)
                    return doc, meta

    return None, {
        "glaeubiger": case_query,
        "aktenzeichen": case_query,
        "hauptforderung": "0,00 €",
        "gesamtforderung": "0,00 €",
        "frist_datum": None
    }


def generate_mahnungen_summary(paths: Optional[BueroPaths] = None, as_json: bool = False) -> List[Dict[str, Any]]:
    """
    Erzeugt eine tabellarische Statusübersicht aller offenen Mahnungen,
    Inkassoschreiben und Zahlungsfristen aus dem aktiven Arbeitsbestand.
    """
    paths = paths or BueroPaths()
    cases: List[Dict[str, Any]] = []
    search_dirs = [paths.schriftlich, paths.email]
    processed_files = set()

    for folder in search_dirs:
        if not folder.exists():
            continue
        for item in sorted(folder.glob("*")):
            if not item.is_file() or item.name in processed_files:
                continue
            if item.suffix.lower() not in [".pdf", ".txt", ".json", ".md"]:
                continue

            processed_files.add(item.name)
            content = ""
            if item.suffix.lower() == ".pdf":
                content = extract_text_from_pdf(item)
            elif item.suffix.lower() in [".txt", ".json", ".md"]:
                content = item.read_text(encoding="utf-8", errors="replace")

            analysis = MahnungInkassoGuard.analyze_document(content, item.name)
            if analysis["is_mahnung_inkasso"] or analysis.get("frist_datum"):
                analysis["file_path"] = str(item)
                cases.append(analysis)

    def sort_key(c):
        urg = c.get("urgency", "")
        if "ÜBERFÄLLIG" in urg:
            prio = 0
        elif "DRINGEND" in urg:
            prio = 1
        elif "OFFEN" in urg:
            prio = 2
        else:
            prio = 3
        days = c.get("frist_tage")
        day_val = days if days is not None else 9999
        return (prio, day_val)

    cases.sort(key=sort_key)

    if as_json:
        print(json.dumps(cases, indent=2, ensure_ascii=False, default=str))
        return cases

    print("\n" + "=" * 94)
    print("🚨 BÜRO-HERMES: MAHNUNGS- & INKASSO-STATUSREPORT")
    print("=" * 94)

    if not cases:
        print("✅ Keine offenen Mahnungen, Inkassofälle oder kritischen Fristen im Arbeitsbestand.")
        print("   Verzeichnisse 'schriftlich/' und 'E-Mail/' sind sauber.")
        print("=" * 94 + "\n")
        return cases

    print(f"Erfasste Vorgänge ({len(cases)} aktiv):")
    print("-" * 94)
    print(f"{'Dringlichkeit':<14} | {'Mahnstufe / Typ':<18} | {'Gläubiger / Inkasso':<20} | {'Aktenzeichen':<14} | {'Forderung':<10} | {'Frist':<10}")
    print("-" * 94)

    total_amount = 0.0
    for c in cases:
        urg = c.get("urgency", "ℹ️ OFFEN")
        stufe = c.get("mahnstufe", "Mahnung")[:18]
        glaeubiger = (c.get("inkassobuero") or c.get("glaeubiger") or "Unbekannt")[:20]
        az = c.get("aktenzeichen", "OhneAZ")[:14]
        betrag = c.get("gesamtforderung", "0,00 €")[:10]
        frist = c.get("frist_datum") or "Keine"

        val_cleaned = re.sub(r"[^\d,\.]", "", betrag).replace(".", "").replace(",", ".")
        try:
            total_amount += float(val_cleaned)
        except Exception:
            pass

        print(f"{urg:<14} | {stufe:<18} | {glaeubiger:<20} | {az:<14} | {betrag:<10} | {frist:<10}")

    print("-" * 94)
    fmt_total = f"{total_amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    print(f"📊 Gesamtanzahl Fälle: {len(cases)} | Offene Gesamtforderung: ca. {fmt_total}")
    print("\n💡 Empfohlene Aktionen zur Fallbearbeitung:")
    for c in cases[:3]:
        g = c.get("inkassobuero") or c.get("glaeubiger") or "Fall"
        print(f"   • python3 scripts/buero_hermes.py draft --type widerspruch --case \"{g}\"")
        print(f"   • python3 scripts/buero_hermes.py draft --type ratenzahlung --case \"{g}\" --rate 30,00")
    print("=" * 94 + "\n")

    return cases

# ==============================================================================
# AutoGen Tool Registry Schnittstelle
# ==============================================================================

def get_autogen_tools() -> Dict[str, Any]:
    """
    Gibt die Funktionsdefinitionen für AutoGen-Agenten zurück.
    Erlaubt direkte Integration in AutoGen AssistantAgent und UserProxyAgent.
    """
    return {
        "sync_gmail_invoices": {
            "function": fetch_gmail_invoices,
            "description": "Liest ungelesene Rechnungen via Gmail ab, filtert Spam/Werbung/Phishing und speichert sichere PDFs lokal in 'E-Mail'."
        },
        "index_scanned_documents": {
            "function": index_scans,
            "description": "Indiziert neue gescannte PDFs im Ordner 'schriftlich' mit qwen:vram und benennt sie nach YYYY-MM-DD_Absender_BetragEUR_Aktenzeichen.pdf um."
        },
        "archive_aged_documents": {
            "function": lambda days=90: archive_aged_documents(days_threshold=days),
            "description": "Verschiebt Dokumente älter als 90 Tage revisionssicher in das Langzeitarchiv (Archiv/YYYY/E-Mail und Archiv/YYYY/schriftlich) nach GoBD."
        },
        "get_active_workspace": {
            "function": lambda: [str(f) for f in get_active_workspace_files()],
            "description": "Liefert den aktiven Arbeitsbestand (letzte 90 Tage) für den LLM-Kontext von qwen:vram."
        },
        "draft_legal_response": {
            "function": lambda text, category="widerspruch": draft_document_darkidol(category, text),
            "description": "Erstellt mit dem Deep-Modell darkidol:ram einen fundierten juristischen Widerspruch oder eine formelle Anfrage."
        },
        "get_office_status": {
            "function": lambda: BueroPaths().status(),
            "description": "Liefert den Status und die Pfade der Büro-Hermes Verzeichnisse im lokalen Drive."
        },
        "get_mahnungen_summary": {
            "function": lambda: generate_mahnungen_summary(),
            "description": "Erzeugt eine Statusübersicht über alle offenen Mahnungen, Inkassofälle und Zahlungsfristen."
        },
        "draft_inkasso_response": {
            "function": lambda case_name, draft_type="widerspruch": draft_document_darkidol(draft_type, "", {"case_query": case_name}),
            "description": "Erstellt einen rechtssicheren Widerspruch oder ein Ratenzahlungsangebot zu einem Mahnungs- oder Inkassofall."
        }
    }


# ==============================================================================
# CLI Entrypoint & Daemon Mode
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Büro-Hermes: Revisionssichere Archivierung, Mail-Filter & Phishing-Schutz")
    subparsers = parser.add_subparsers(dest="command", help="Verfügbare Befehle")

    # Status
    subparsers.add_parser("status", help="Zeigt Verzeichnis- und Systemstatus an")

    # Routine 1: Mail Sync
    mail_parser = subparsers.add_parser("sync-mail", help="Führt Routine 1 (Gmail -> E-Mail mit Filterung & Phishing-Schutz) aus")
    mail_parser.add_argument("--max", type=int, default=25, help="Maximale Anzahl zu prüfender Mails")
    mail_parser.add_argument("--no-mark-read", action="store_true", help="Gelesene Mails nicht als \\Seen markieren")

    # Routine 2: Scan Indexer
    scan_parser = subparsers.add_parser("index-scans", help="Führt Routine 2 (Scan-Indexer & Umbenennung) aus")
    scan_parser.add_argument("--dry-run", action="store_true", help="Nur simulieren, keine Dateien umbenennen")

    # Revisionssichere Archivierung (10 Jahre)
    archive_parser = subparsers.add_parser("archive", help="Revisionssichere Archivierung von Dokumenten > 90 Tage")
    archive_parser.add_argument("--days", type=int, default=90, help="Altersschwelle in Tagen (Standard: 90)")
    archive_parser.add_argument("--dry-run", action="store_true", help="Archivierung nur simulieren")

    # Aktiver Arbeitsbestand für LLM
    subparsers.add_parser("active-files", help="Listet den aktiven 90-Tage Arbeitsbestand für qwen:vram auf")

    # Routine All
    all_parser = subparsers.add_parser("run-all", help="Führt Mail-Sync, Scan-Indexierung und Archivierung nacheinander aus")
    all_parser.add_argument("--dry-run", action="store_true", help="Änderungen nur simulieren")

    # Status-Report für Mahnungen & Inkassofälle
    summary_parser = subparsers.add_parser("summary", help="Erzeugt Statusübersicht über Fristen, Mahnungen und Inkassofälle")
    summary_parser.add_argument("--json", action="store_true", help="Ausgabe im JSON-Format")

    # Textentwurf via darkidol:ram / Schutz- & Vorlagen-Modul
    draft_parser = subparsers.add_parser("draft", help="Erstellt Textentwurf (Widerspruch/Ratenzahlung/etc.) mit Schutz- & Vorlagen-Modul")
    draft_parser.add_argument("--type", choices=["widerspruch", "ratenzahlung", "anfrage", "bewerbung"], default="widerspruch", help="Art des Entwurfs")
    draft_parser.add_argument("--case", help="Fallname, Gläubiger oder Aktenzeichen (z. B. 'PAIR Finance' oder 'AZ-98124')")
    draft_parser.add_argument("--rate", default="30,00", help="Monatliche Ratenhöhe bei Typ 'ratenzahlung' (Standard: 30,00 €)")
    draft_parser.add_argument("--file", help="PDF- oder Textdatei als Kontext")
    draft_parser.add_argument("--context", help="Freitext als Kontext")
    draft_parser.add_argument("--output", help="Optionaler Ausgabepfad (Standard: Schriftsätze/YYYY-MM-DD_Typ_Fall.md)")

    # Watcher / Daemon
    watch_parser = subparsers.add_parser("watch", help="Startet die Überwachungsschleife")
    watch_parser.add_argument("--interval", type=int, default=120, help="Prüfintervall in Sekunden")

    args = parser.parse_args()
    paths = BueroPaths()

    if args.command == "status":
        print(json.dumps(paths.status(), indent=2, ensure_ascii=False))

    elif args.command == "sync-mail":
        print("Starte Routine 1: Gmail Ingest (mit Spam-, Werbe- und Phishing-Filter)...")
        saved = fetch_gmail_invoices(
            paths=paths,
            max_mails=args.max,
            mark_as_read=not args.no_mark_read
        )
        print(json.dumps(saved, indent=2, ensure_ascii=False, default=str))

    elif args.command == "index-scans":
        print("Starte Routine 2: Scan-Indexer (qwen:vram)...")
        results = index_scans(paths=paths, dry_run=args.dry_run)
        print(f"Erledigt: {len(results)} Datei(en) bearbeitet.")

    elif args.command == "archive":
        print(f"Starte revisionssichere Archivierung (Schwelle: {args.days} Tage)...")
        rec = archive_aged_documents(paths=paths, days_threshold=args.days, dry_run=args.dry_run)
        print(json.dumps(rec, indent=2, ensure_ascii=False))

    elif args.command == "active-files":
        files = get_active_workspace_files(paths=paths)
        print(f"Aktiver 90-Tage Arbeitsbestand ({len(files)} Dateien):")
        for f in files:
            print(f" - {f.name} ({f.parent.name})")

    elif args.command == "summary":
        generate_mahnungen_summary(paths=paths, as_json=args.json)

    elif args.command == "run-all":
        print("=== Starte Büro-Hermes Gesamtzyklus ===")
        saved = fetch_gmail_invoices(paths=paths)
        indexed = index_scans(paths=paths, dry_run=args.dry_run)
        archived = archive_aged_documents(paths=paths, dry_run=args.dry_run)
        print(f"Abgeschlossen: {len(saved.get('invoices_saved', []))} Mails gesichert, {len(indexed)} Scans indiziert.")
        generate_mahnungen_summary(paths=paths)

    elif args.command == "draft":
        context = ""
        case_meta = {}
        target_doc = None

        if args.case:
            target_doc, case_meta = find_case_document(paths, args.case)
            if target_doc:
                print(f"📄 Zugeordnetes Dokument gefunden: {target_doc.name}")
                if target_doc.suffix.lower() == ".pdf":
                    context = extract_text_from_pdf(target_doc)
                else:
                    context = target_doc.read_text(encoding="utf-8", errors="replace")
            else:
                context = args.context or f"Fallbezug: {args.case}"
                case_meta = {
                    "glaeubiger": args.case,
                    "aktenzeichen": args.case,
                    "gesamtforderung": "Offen",
                    "hauptforderung": "Offen"
                }

        if args.file:
            p = Path(args.file)
            if not p.exists():
                print(f"Fehler: Datei {args.file} existiert nicht.", file=sys.stderr)
                sys.exit(1)
            target_doc = p
            if p.suffix.lower() == ".pdf":
                file_text = extract_text_from_pdf(p)
            else:
                file_text = p.read_text(encoding="utf-8", errors="replace")
            context = file_text + ("\n" + context if context else "")
            analyzed = MahnungInkassoGuard.analyze_document(file_text, p.name)
            case_meta.update({k: v for k, v in analyzed.items() if v})

        if args.context and not args.case:
            context = args.context
            analyzed = MahnungInkassoGuard.analyze_document(context, "manueller_kontext")
            case_meta.update({k: v for k, v in analyzed.items() if v})

        if not context and not args.case:
            print("Fehler: Bitte --case [NAME], --file [PFAD] oder --context [TEXT] angeben.", file=sys.stderr)
            sys.exit(1)

        if args.type == "ratenzahlung":
            case_meta["monatsrate"] = args.rate

        print(f"✍️ Generiere Schriftsatz für Kategorie '{args.type}' (Fall: {case_meta.get('glaeubiger', args.case or 'Unbekannt')})...")
        draft = draft_document_darkidol(draft_type=args.type, context=context, details=case_meta)

        if args.output:
            out_file = Path(args.output).expanduser().resolve()
        else:
            today_str = datetime.now().strftime("%Y-%m-%d")
            clean_case = sanitize_filename(case_meta.get("glaeubiger") or args.case or "Fall")
            clean_az = sanitize_filename(case_meta.get("aktenzeichen") or "OhneAZ")
            type_label = "Widerspruch" if args.type == "widerspruch" else "Ratenzahlung" if args.type == "ratenzahlung" else args.type.capitalize()
            filename = f"{today_str}_{type_label}_{clean_case}_{clean_az}.md"
            out_file = paths.schriftsaetze / filename

        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(draft, encoding="utf-8")
        print("\n" + "=" * 70)
        print("✅ SCHRIFTSATZ GESPEICHERT:")
        print(f"   Pfad: {out_file}")
        print("=" * 70)
        preview_len = min(600, len(draft))
        print(draft[:preview_len] + ("\n... [vollständiger Schriftsatz in Datei gespeichert]" if len(draft) > preview_len else ""))
        print("=" * 70)

    elif args.command == "watch":
        print(f"Starte Büro-Hermes Watcher Daemon (Intervall: {args.interval}s)...")
        print("Beenden mit Strg+C")
        try:
            while True:
                logger.info("Watcher Durchlauf: Prüfe Mails, Scans & Archiv...")
                fetch_gmail_invoices(paths=paths)
                index_scans(paths=paths)
                archive_aged_documents(paths=paths)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nWatcher sauber beendet.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
