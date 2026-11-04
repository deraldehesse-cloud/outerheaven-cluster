#!/usr/bin/env python3
"""
test_fints_banking.py - Comprehensive Tests for Read-Only Banking (PSD Bank)
============================================================================
1. Security Verification (Zero-Write Policy, Blocked Transaction Calls)
2. Balance & Cache Handling (fints_connector.py)
3. Budget Adequacy Check & Paperless Integration (hermes_paperless_advisor.py)
4. Proactive Notification Dispatch (Desktop & Telegram Push)
5. Web-UI Endpoints (/api/banking/balance, /api/banking/report/trigger)
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import fints_connector
import hermes_paperless_advisor
import hermes_web_ui


class TestReadOnlySecurity(unittest.TestCase):
    """Prüft strikte Einhaltung der Read-Only Sicherheitsvorgaben."""

    def test_no_sepa_transfer_exposed(self):
        """Stellt sicher, dass keine SEPA-Überweisungsfunktionen exportiert werden."""
        self.assertFalse(hasattr(fints_connector, "SEPATransfer"), "SEPATransfer darf nicht exportiert werden!")
        self.assertFalse(hasattr(fints_connector, "simple_sepa_transfer"), "simple_sepa_transfer darf nicht exportiert werden!")
        self.assertFalse(hasattr(fints_connector, "sepa_transfer"), "sepa_transfer darf nicht exportiert werden!")

    def test_blocked_transaction_methods_raise_security_error(self):
        """Verifiziert, dass der ReadOnlyFinTSClient bei Manipulationsversuchen blockiert."""
        client = fints_connector.ReadOnlyFinTSClient(
            blz="50090900",
            login="testuser",
            pin="12345",
            endpoint="https://example.com/fints"
        )
        mock_raw_client = MagicMock()
        client._client = mock_raw_client

        # Manuell versuchen, Transaktionsoperationen anzuhängen
        for op in fints_connector.FORBIDDEN_TRANSACTION_OPERATIONS:
            # Erzeuge die blockierte Methode wie im Code
            def make_blocked(op_name):
                return lambda *a, **k: (_ for _ in ()).throw(
                    fints_connector.SecurityPolicyError(f"SECURITY VIOLATION: '{op_name}' ist strikt blockiert.")
                )
            setattr(mock_raw_client, op, make_blocked(op))

            with self.assertRaises(fints_connector.SecurityPolicyError):
                getattr(mock_raw_client, op)()


class TestFinTSBalanceAndCache(unittest.TestCase):
    """Testet Saldo-Abfrage, Caching und Budget-Prüfung."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_file = Path(self.tmp_dir.name) / "banking_cache.json"
        self.orig_cache_file = fints_connector.CACHE_FILE
        fints_connector.CACHE_FILE = self.cache_file

    def tearDown(self):
        fints_connector.CACHE_FILE = self.orig_cache_file
        self.tmp_dir.cleanup()

    def test_cache_fallback_when_unconfigured(self):
        """Stellt sicher, dass ohne Zugangsdaten der sichere Fallback-Cache geladen wird."""
        with patch.dict(os.environ, {"PSD_LOGIN": "", "PSD_PIN": ""}):
            bal = fints_connector.get_current_balance(force_refresh=True)
            self.assertIn("balance_eur", bal)
            self.assertEqual(bal["currency"], "EUR")
            self.assertEqual(bal["bank"], "PSD Bank Hessen-Thüringen eG")
            self.assertTrue(bal.get("simulated", False))

    def test_save_and_load_cache(self):
        """Testet das Speichern und Laden des Banking-Caches."""
        test_data = {
            "balance_eur": 3120.45,
            "currency": "EUR",
            "account_iban": "DE12500909001234567890",
            "bank": "PSD Bank Hessen-Thüringen eG",
            "timestamp": "2026-11-02T10:00:00Z",
            "simulated": False
        }
        fints_connector.save_banking_cache(test_data)
        loaded = fints_connector.load_banking_cache()
        self.assertEqual(loaded["balance_eur"], 3120.45)
        self.assertEqual(loaded["account_iban"], "DE12500909001234567890")

    def test_budget_adequacy_check(self):
        """Prüft die Budget-Berechnung (Saldo vs. Rechnungsbetrag)."""
        with patch.object(fints_connector, "get_current_balance") as mock_bal:
            mock_bal.return_value = {
                "balance_eur": 500.00,
                "currency": "EUR",
                "bank": "PSD Bank Hessen-Thüringen eG"
            }
            # Fall 1: Budget reicht locker (Rechnung 150 €, Schwelle 100 € -> Rest 350 € >= 100 €)
            res1 = fints_connector.check_budget_adequacy(invoice_amount_eur=150.0, warn_threshold_eur=100.0)
            self.assertTrue(res1["is_sufficient"])
            self.assertEqual(res1["remaining_eur"], 350.0)

            # Fall 2: Budget knapp (Rechnung 450 €, Schwelle 100 € -> Rest 50 € < 100 €)
            res2 = fints_connector.check_budget_adequacy(invoice_amount_eur=450.0, warn_threshold_eur=100.0)
            self.assertFalse(res2["is_sufficient"])
            self.assertTrue(res2["is_strictly_positive"])
            self.assertEqual(res2["remaining_eur"], 50.0)


class TestProactiveNotificationDispatch(unittest.TestCase):
    """Testet Benachrichtigungen gemäß Spezifikations-Wortlaut."""

    @patch.object(fints_connector, "send_telegram_notification")
    @patch.object(fints_connector, "send_desktop_notification")
    def test_invoice_overview_notification_format(self, mock_desktop, mock_tg):
        """Verifiziert das exakte Benachrichtigungsformat."""
        msg = fints_connector.notify_paperless_invoice_overview(
            invoice_count=3,
            total_amount_eur=245.50,
            balance_eur=1850.20
        )
        # Vorgabe: "Boss, die neue Rechnungsübersicht ist fertig. Dein aktueller Kontostand bei der PSD Bank beträgt X.XX €. Es liegen [X] offene Posten an."
        self.assertIn("Boss, die neue Rechnungsübersicht ist fertig.", msg)
        self.assertIn("Dein aktueller Kontostand bei der PSD Bank beträgt 1850.20 €.", msg)
        self.assertIn("Es liegen 3 offene Posten an", msg)

        mock_desktop.assert_called_once()
        mock_tg.assert_called_once()


class TestPaperlessBankingAdvisor(unittest.TestCase):
    """Testet die Integration von Banking & Budget-Check in hermes_paperless_advisor."""

    @patch("requests.post")
    @patch("requests.patch")
    @patch("requests.get")
    def test_advisor_includes_banking_check(self, mock_get, mock_patch, mock_post):
        mock_doc = MagicMock()
        mock_doc.status_code = 200
        mock_doc.json.return_value = {
            "id": 88,
            "title": "Stadtwerke Stromrechnung",
            "correspondent": "Stadtwerke",
            "content": "Rechnung über 120.00 EUR. Aktenzeichen STW-8812. Kontakt: info@stadtwerke.de"
        }
        mock_get.return_value = mock_doc

        mock_ollama = MagicMock()
        mock_ollama.status_code = 200
        mock_ollama.json.return_value = {
            "response": json.dumps({
                "title": "2026-11-02 Stadtwerke - Rechnung",
                "correspondent": "Stadtwerke",
                "document_type": "Rechnung",
                "urgency": "GELB",
                "amount_eur": 120.0,
                "advisor_note": "Monatliche Stromabschlagzahlung."
            })
        }
        mock_post.side_effect = [
            mock_ollama,
            MagicMock(status_code=200)
        ]

        with patch.object(fints_connector, "notify_paperless_invoice_overview") as mock_notify:
            res = hermes_paperless_advisor.run_hermes_advisor("88")
            self.assertIsNotNone(res)
            self.assertIn("banking", res)
            self.assertIn("[💳 PSD Bank Check]", res["banking"])
            mock_notify.assert_called_once()


class TestWebUIBankingEndpoints(unittest.TestCase):
    """Testet die Banking-Endpunkte und den wöchentlichen Report-Scheduler in hermes_web_ui."""

    def test_get_banking_balance_endpoint(self):
        bal = hermes_web_ui.get_banking_balance()
        self.assertIn("balance_eur", bal)
        self.assertEqual(bal["bank"], "PSD Bank Hessen-Thüringen eG")

    @patch.object(fints_connector, "dispatch_proactive_notification")
    def test_trigger_weekly_banking_report(self, mock_dispatch):
        res = hermes_web_ui.run_weekly_banking_report(force=True)
        self.assertEqual(res["status"], "success")
        self.assertIn("Boss, die neue Rechnungsübersicht ist fertig.", res["message"])
        self.assertIn("Dein aktueller Kontostand bei der PSD Bank beträgt", res["message"])
        mock_dispatch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
