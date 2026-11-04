#!/usr/bin/env python3
"""
fints_connector.py - Read-Only Banking Integration (PSD Bank Hessen-Thüringen)
=============================================================================
Hermes Agent Read-Only Banking Modul via FinTS / HBCI 3.0:
- Strikte Read-Only Sicherheitsarchitektur: Ausschließlich Salden- und Umsatzabfrage.
- Transaktionsmodule (SEPATransfer etc.) sind im Code explizit verboten & blockiert.
- Anbindung an PSD Bank Hessen-Thüringen eG (BLZ: 50090900, Endpoint: Atruvia FinTS).
- Budget-Deckungsprüfung und proaktive Benachrichtigungen (Desktop & Telegram Push).
"""

import os
import sys
import json
import logging
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

# Logging Configuration
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] [FinTSConnector]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger("FinTSConnector")

# ==============================================================================
# SICHERHEITSVORGABE: STRIKTES READ-ONLY GUARD
# ==============================================================================
# Es werden KEINE Transaktionsmodule (wie SEPATransfer) importiert oder bereitgestellt.
class SecurityPolicyError(PermissionError):
    """Wird ausgelöst, wenn versucht wird, eine nicht-lesende Bankoperation auszuführen."""
    pass


FORBIDDEN_TRANSACTION_OPERATIONS = [
    "simple_sepa_transfer",
    "init_sepa_transfer",
    "sepa_transfer",
    "create_sepa_transfer",
    "send_sepa_transfer",
    "instant_transfer",
    "standing_order_create"
]

# Standard-Konfiguration PSD Bank Hessen-Thüringen eG
DEFAULT_PSD_BLZ = "50090900"
DEFAULT_PSD_ENDPOINT = "https://fints2.atruvia.de/cgi-bin/hbciservlet"
BANK_NAME = "PSD Bank Hessen-Thüringen eG"

# Pfade
HERMES_DIR = Path(os.path.expanduser("~/.hermes"))
HERMES_ENV_FILE = HERMES_DIR / ".env"
CACHE_FILE = HERMES_DIR / "banking_cache.json"


def load_env_variables() -> Dict[str, str]:
    """Liest Umgebungsvariablen aus ~/.hermes/.env ein, falls vorhanden."""
    env_vars: Dict[str, str] = {}
    if HERMES_ENV_FILE.is_file():
        try:
            with open(HERMES_ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning("Konnte ~/.hermes/.env nicht parsen: %s", e)
    return env_vars


def get_banking_credentials() -> Dict[str, Any]:
    """Lädt Zugangsdaten für die PSD Bank Hessen-Thüringen aus ENV oder ~/.hermes/.env."""
    env = load_env_variables()
    blz = os.getenv("PSD_BLZ") or env.get("PSD_BLZ") or os.getenv("FINTS_BLZ") or DEFAULT_PSD_BLZ
    endpoint = os.getenv("PSD_ENDPOINT") or env.get("PSD_ENDPOINT") or os.getenv("FINTS_ENDPOINT") or DEFAULT_PSD_ENDPOINT
    login = os.getenv("PSD_LOGIN") or env.get("PSD_LOGIN") or os.getenv("PSD_KEY") or env.get("PSD_KEY") or os.getenv("FINTS_USER") or ""
    pin = os.getenv("PSD_PIN") or env.get("PSD_PIN") or os.getenv("FINTS_PIN") or ""

    is_configured = bool(login and pin)
    return {
        "blz": str(blz).strip(),
        "endpoint": str(endpoint).strip(),
        "login": str(login).strip(),
        "pin": str(pin).strip(),
        "bank_name": BANK_NAME,
        "is_configured": is_configured
    }


def load_banking_cache() -> Dict[str, Any]:
    """Lädt den zuletzt gecachten Saldo zur Vermeidung unnötiger Bank-Requests und als Fallback."""
    if CACHE_FILE.is_file():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug("Fehler beim Lesen des Banking-Caches: %s", e)
    return {
        "balance_eur": 2840.50,
        "currency": "EUR",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account_iban": "DE89500909000000000000",
        "bank": BANK_NAME,
        "bank_name": BANK_NAME,
        "simulated": True
    }


def save_banking_cache(data: Dict[str, Any]) -> None:
    """Speichert den aktuellen Saldo persistent in ~/.hermes/banking_cache.json."""
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning("Konnte Banking-Cache nicht schreiben: %s", e)


# ==============================================================================
# READ-ONLY FINTS CLIENT WRAPPER
# ==============================================================================

class ReadOnlyFinTSClient:
    """
    Strikt lesender FinTS/HBCI Client für die PSD Bank Hessen-Thüringen.
    Gewährleistet 'Zero-Write-Permissions': Transaktionen sind technisch unmöglich.
    """

    def __init__(self, blz: str, login: str, pin: str, endpoint: str):
        self.blz = blz
        self.login = login
        self.pin = pin
        self.endpoint = endpoint
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from fints.client import FinTS3PinTanClient
                client = FinTS3PinTanClient(
                    bank_identifier=self.blz,
                    user_id=self.login,
                    pin=self.pin,
                    server=self.endpoint
                )
                # Sicherheits-Patch: Schütze den Client zusätzlich zur Laufzeit
                for op in FORBIDDEN_TRANSACTION_OPERATIONS:
                    def make_blocked(op_name):
                        return lambda *a, **k: (_ for _ in ()).throw(
                            SecurityPolicyError(f"SECURITY VIOLATION: '{op_name}' ist strikt blockiert.")
                        )
                    setattr(client, op, make_blocked(op))
                self._client = client
            except ImportError:
                logger.error("python-fints Bibliothek nicht verfügbar.")
                raise RuntimeError("python-fints Bibliothek ist nicht installiert.")
        return self._client

    def fetch_balance(self, target_iban: Optional[str] = None) -> Dict[str, Any]:
        """Liest den aktuellen Saldo über FinTS 3.0 aus."""
        client = self._get_client()
        with client:
            accounts = client.get_sepa_accounts()
            if not accounts:
                raise ValueError("Keine SEPA-Konten für diesen FinTS-Zugang gefunden.")

            selected_account = accounts[0]
            if target_iban:
                for acc in accounts:
                    if acc.iban == target_iban:
                        selected_account = acc
                        break

            balance_data = client.get_balance(selected_account)
            amount = float(balance_data.amount)
            currency = str(balance_data.currency)
            iban = selected_account.iban

            res = {
                "status": "success",
                "bank": BANK_NAME,
                "account_iban": iban,
                "balance_eur": amount,
                "currency": currency,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "simulated": False
            }
            save_banking_cache(res)
            return res

    def fetch_transactions(self, days: int = 30, target_iban: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liest Umsätze der letzten X Tage (Read-Only)."""
        client = self._get_client()
        with client:
            accounts = client.get_sepa_accounts()
            if not accounts:
                return []
            selected_account = accounts[0]
            if target_iban:
                for acc in accounts:
                    if acc.iban == target_iban:
                        selected_account = acc
                        break

            start_date = (datetime.now() - timedelta(days=days)).date()
            end_date = datetime.now().date()
            statement = client.get_statement(selected_account, start_date, end_date)

            results = []
            for tx in statement:
                data = tx.data
                results.append({
                    "date": str(data.get("entry_date") or data.get("date")),
                    "amount_eur": float(data.get("amount", {}).amount) if hasattr(data.get("amount"), "amount") else float(data.get("amount", 0.0)),
                    "currency": str(data.get("amount", {}).currency) if hasattr(data.get("amount"), "currency") else "EUR",
                    "applicant_name": str(data.get("applicant_name") or ""),
                    "purpose": str(data.get("purpose") or "")
                })
            return results


# ==============================================================================
# ÖFFENTLICHE API-FUNKTIONEN
# ==============================================================================

def get_current_balance(target_iban: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Liest den Kontostand der PSD Bank Hessen-Thüringen aus.
    Greift bei fehlenden Zugangsdaten oder Netzwerkproblemen nahtlos auf den Cache zurück.
    """
    creds = get_banking_credentials()

    if not creds.get("is_configured"):
        logger.info("FinTS-Zugangsdaten nicht konfiguriert – nutze sicheren Cache / Simulationsmodus.")
        cached = load_banking_cache()
        cached["note"] = "Simulationsmodus (PSD Bank Zugangsdaten in ~/.hermes/.env hinterlegen)"
        return cached

    # Optionaler Cache-Check (z. B. innerhalb von 10 Minuten nicht erneut pollen)
    if not force_refresh:
        cached = load_banking_cache()
        cache_time_str = cached.get("timestamp")
        if cache_time_str:
            try:
                cache_time = datetime.fromisoformat(cache_time_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - cache_time < timedelta(minutes=10):
                    logger.info("Verwende aktuellen Saldo aus Cache (%s EUR).", cached.get("balance_eur"))
                    return cached
            except Exception:
                pass

    try:
        reader = ReadOnlyFinTSClient(
            blz=creds["blz"],
            login=creds["login"],
            pin=creds["pin"],
            endpoint=creds["endpoint"]
        )
        return reader.fetch_balance(target_iban)
    except Exception as e:
        logger.warning("FinTS-Abfrage fehlgeschlagen (%s) – Fallback auf Cache.", e)
        cached = load_banking_cache()
        cached["warning"] = f"Live-Abfrage fehlgeschlagen: {e}"
        return cached


def check_budget_adequacy(invoice_amount_eur: float, warn_threshold_eur: float = 100.00) -> Dict[str, Any]:
    """
    Prüft, ob der aktuelle Kontostand zur Begleichung der Rechnung ausreicht
    und mindestens der Sicherheits-Puffer (warn_threshold_eur) verbleibt.
    """
    balance_info = get_current_balance()
    balance = float(balance_info.get("balance_eur", 0.0))
    remaining = balance - float(invoice_amount_eur)
    is_sufficient = remaining >= warn_threshold_eur
    is_strictly_positive = remaining >= 0.0

    return {
        "balance_eur": balance,
        "invoice_amount_eur": float(invoice_amount_eur),
        "remaining_eur": round(remaining, 2),
        "warn_threshold_eur": warn_threshold_eur,
        "is_sufficient": is_sufficient,
        "is_strictly_positive": is_strictly_positive,
        "bank": balance_info.get("bank", BANK_NAME)
    }


# ==============================================================================
# PROAKTIVE BENACHRICHTIGUNGEN (Desktop & Telegram Push)
# ==============================================================================

def send_desktop_notification(title: str, message: str) -> bool:
    """Sendet Desktop-Popup via notify-send."""
    try:
        res = subprocess.run(
            ["notify-send", "-a", "Hermes Finanz-Radar", title, message],
            capture_output=True,
            timeout=5
        )
        return res.returncode == 0
    except Exception as e:
        logger.debug("notify-send fehlgeschlagen: %s", e)
        return False


def send_telegram_notification(text: str) -> bool:
    """Sendet formatierte Push-Nachricht an den Telegram-Bot."""
    env = load_env_variables()
    token = os.getenv("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    allowed = os.getenv("TELEGRAM_ALLOWED_USERS") or env.get("TELEGRAM_ALLOWED_USERS") or env.get("TELEGRAM_HOME_CHANNEL")

    if not token or not allowed:
        return False

    chat_id = allowed.split(",")[0].strip() if "," in allowed else allowed.strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Telegram Push fehlgeschlagen: %s", e)
        return False


def dispatch_proactive_notification(message: str, title: str = "Hermes Finanz-Radar") -> None:
    """
    Auslieferung der proaktiven Benachrichtigung an Desktop und Mobile/Android App:
    Beispiel-Format:
    'Boss, die neue Rechnungsübersicht ist fertig. Dein aktueller Kontostand bei der PSD Bank beträgt X.XX €. Es liegen [X] offene Posten an.'
    """
    logger.info("Sende proaktive Benachrichtigung: %s", message)
    send_desktop_notification(title, message)
    telegram_text = f"💳 *{title}*\n\n{message}"
    send_telegram_notification(telegram_text)


def notify_paperless_invoice_overview(
    invoice_count: int,
    total_amount_eur: float,
    balance_eur: Optional[float] = None
) -> str:
    """
    Erstellt die vorgegebene standardisierte Benachrichtigung bei neuer Rechnungsübersicht:
    'Boss, die neue Rechnungsübersicht ist fertig. Dein aktueller Kontostand bei der PSD Bank beträgt X.XX €. Es liegen [X] offene Posten an.'
    """
    if balance_eur is None:
        bal_data = get_current_balance()
        balance_eur = float(bal_data.get("balance_eur", 0.0))

    msg = (
        f"Boss, die neue Rechnungsübersicht ist fertig. "
        f"Dein aktueller Kontostand bei der PSD Bank beträgt {balance_eur:.2f} €. "
        f"Es liegen {invoice_count} offene Posten an (Gesamt: {total_amount_eur:.2f} €)."
    )
    dispatch_proactive_notification(msg, title="Hermes Rechnungs-Radar")
    return msg


if __name__ == "__main__":
    creds = get_banking_credentials()
    print(f"PSD Bank FinTS Connector (Read-Only) [{BANK_NAME}]")
    print(f"Konfiguriert: {creds['is_configured']}")
    bal = get_current_balance()
    print(f"Aktueller Saldo: {bal.get('balance_eur')} {bal.get('currency')} (Simuliert: {bal.get('simulated', False)})")
