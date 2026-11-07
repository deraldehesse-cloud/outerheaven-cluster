#!/usr/bin/env python3
"""
test_hermes_email_ingest.py - Unit & Integration Tests for Email Ingest & Review
================================================================================
Testet:
1. E-Mail MIME-Extraktion (Text & Anhänge)
2. KI-Klassifizierung & Triage (SPAM, PAPERLESS, REVIEW)
3. SQLite Review CRUD & Auflösung (Löschen & Paperless-Dispatch)
4. Paperless Ingest Dispatcher
5. FastAPI REST-Endpunkte (/api/email_review, /action, /poll)
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import patch, MagicMock

import sqlite3
import hermes_email_ingest
import hermes_web_ui


class TestHermesEmailIngest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_ingest.db"
        self.att_dir = Path(self.tmp_dir.name) / "test_attachments"
        self.att_dir.mkdir(parents=True, exist_ok=True)

        hermes_email_ingest.init_ingest_db(self.db_path)
        self.orig_get_db = hermes_email_ingest.get_db_path
        self.orig_get_att = hermes_email_ingest.get_attachments_dir

        hermes_email_ingest.get_db_path = lambda: self.db_path
        hermes_email_ingest.get_attachments_dir = lambda: self.att_dir

    def tearDown(self):
        hermes_email_ingest.get_db_path = self.orig_get_db
        hermes_email_ingest.get_attachments_dir = self.orig_get_att
        self.tmp_dir.cleanup()

    def test_extract_email_data_with_attachment(self):
        """Prüft das Dekodieren von Betreff, Absender und Binäranhang."""
        msg = EmailMessage()
        msg["Subject"] = "Rechnung Telekom November 2026"
        msg["From"] = "kundenservice@telekom.de"
        msg["Date"] = "Wed, 4 Nov 2026 01:00:00 +0100"
        msg.set_content("Sehr geehrter Kunde, anbei Ihre monatliche Rechnung.")
        msg.add_attachment(b"%PDF-1.4 mock pdf content", maintype="application", subtype="pdf", filename="Rechnung_Nov26.pdf")

        raw_bytes = msg.as_bytes()
        extracted = hermes_email_ingest.extract_email_data(raw_bytes)

        self.assertEqual(extracted["subject"], "Rechnung Telekom November 2026")
        self.assertIn("kundenservice@telekom.de", extracted["sender"])
        self.assertIn("monatliche Rechnung", extracted["body"])
        self.assertEqual(len(extracted["attachments"]), 1)
        self.assertEqual(extracted["attachments"][0]["filename"], "Rechnung_Nov26.pdf")
        self.assertEqual(extracted["attachments"][0]["data"], b"%PDF-1.4 mock pdf content")

    def test_review_item_crud_and_delete(self):
        """Testet Anlegen eines Review-Elements und anschließendes Löschen."""
        item_id = hermes_email_ingest.add_review_item(
            sender="unbekannt@absender.de",
            subject="Wichtige Mitteilung zu Ihrem Konto",
            received_at="2026-11-04T01:00:00Z",
            summary="Unklarer Hinweis auf Kontoänderung",
            category="REVIEW",
            confidence=0.5,
            attachment_name="info.pdf",
            attachment_data=b"mock-pdf-bytes",
            db_path=self.db_path
        )
        self.assertIsInstance(item_id, int)

        # 1. Prüfen, dass es in der Liste der offenen Reviews auftaucht
        pending = hermes_email_ingest.list_pending_reviews(db_path=self.db_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], item_id)
        self.assertEqual(pending[0]["subject"], "Wichtige Mitteilung zu Ihrem Konto")
        self.assertEqual(pending[0]["status"], "pending")

        # 2. Lösch-Aktion ausführen
        ok, msg = hermes_email_ingest.resolve_review_item(
            item_id=item_id,
            action="DELETE",
            db_path=self.db_path
        )
        self.assertTrue(ok)

        # 3. Keine offenen Reviews mehr
        remaining = hermes_email_ingest.list_pending_reviews(db_path=self.db_path)
        self.assertEqual(len(remaining), 0)

    def test_review_item_send_to_paperless(self):
        """Testet die Weiterleitung eines Review-Elements an Paperless."""
        item_id = hermes_email_ingest.add_review_item(
            sender="arbeitsagentur@arbeitsagentur.de",
            subject="Bescheid über Leistungen",
            received_at="2026-11-04T01:00:00Z",
            summary="Leistungsbescheid der Arbeitsagentur",
            category="REVIEW",
            confidence=0.8,
            attachment_name="Bescheid_2026.pdf",
            attachment_data=b"%PDF-1.4 Testbescheid",
            db_path=self.db_path
        )

        with patch("hermes_email_ingest.send_to_paperless", return_value=(True, "Dokument 999 erzeugt")) as mock_paperless:
            ok, msg = hermes_email_ingest.resolve_review_item(
                item_id=item_id,
                action="SEND_TO_PAPERLESS",
                custom_title="2026-11-04 Arbeitsagentur Bescheid",
                db_path=self.db_path
            )
            self.assertTrue(ok)
            mock_paperless.assert_called_once()
            self.assertEqual(mock_paperless.call_args[0][1], "Bescheid_2026.pdf")
            self.assertEqual(mock_paperless.call_args[1]["title"], "2026-11-04 Arbeitsagentur Bescheid")

        # Prüfen, dass das Element nicht mehr pending ist
        remaining = hermes_email_ingest.list_pending_reviews(db_path=self.db_path)
        self.assertEqual(len(remaining), 0)

    def test_classify_email_heuristics_fallback(self):
        """Testet Heuristik für Spam, Paperless und Review bei Offline-LLM."""
        # SPAM Heuristik
        spam_res = hermes_email_ingest.classify_email_with_llm(
            sender="newsletter@shopping-deals.de",
            subject="30% Rabatt Gutschein nur heute!",
            body="Klicke hier um deinen Gutschein einzulösen oder abmelden.",
            attachment_names=[],
            ollama_url="http://127.0.0.1:9999"  # Dummy Port -> Fallback
        )
        self.assertEqual(spam_res["category"], "SPAM")

        # PAPERLESS Heuristik
        paper_res = hermes_email_ingest.classify_email_with_llm(
            sender="rechnung@vodafone.de",
            subject="Ihre Rechnung für November",
            body="Sehr geehrter Kunde, im Anhang finden Sie die Rechnung.",
            attachment_names=["Rechnung_112026.pdf"],
            ollama_url="http://127.0.0.1:9999"
        )
        self.assertEqual(paper_res["category"], "PAPERLESS")

        # REVIEW Heuristik
        rev_res = hermes_email_ingest.classify_email_with_llm(
            sender="max.mustermann@web.de",
            subject="Kurze Rückfrage zu unserem Treffen",
            body="Hallo, wie sieht es morgen Nachmittag bei dir aus?",
            attachment_names=[],
            ollama_url="http://127.0.0.1:9999"
        )
        self.assertEqual(rev_res["category"], "REVIEW")

    def test_fastapi_endpoints(self):
        """Testet die REST-Endpunkte /api/email_review und /api/email_review/{id}/action."""
        # Element in die DB einfügen
        item_id = hermes_email_ingest.add_review_item(
            sender="finanzamt@hessen.de",
            subject="Steuerbescheid 2025",
            received_at="2026-11-04T01:00:00Z",
            summary="Steuerfestsetzung 2025",
            category="REVIEW",
            confidence=0.85,
            attachment_name="Bescheid_2025.pdf",
            attachment_data=b"%PDF-1.4 Steuerbescheid",
            db_path=self.db_path
        )

        # GET /api/email_review
        resp = hermes_web_ui.get_email_reviews()
        self.assertIn("items", resp)
        self.assertEqual(resp["count"], 1)
        self.assertEqual(resp["items"][0]["id"], item_id)

        # POST /api/email_review/{id}/action (SEND_TO_PAPERLESS)
        req = hermes_web_ui.EmailReviewActionRequest(action="SEND_TO_PAPERLESS", title="Finanzamt Steuerbescheid")
        with patch("hermes_email_ingest.send_to_paperless", return_value=(True, "Dokument OK")):
            action_resp = hermes_web_ui.take_email_review_action(item_id, req)
            self.assertEqual(action_resp["status"], "success")
            self.assertEqual(action_resp["id"], item_id)


    def test_pending_reviews_view(self):
        """Testet die SQLite-View pending_reviews für direkte CLI- und SQL-Abfragen."""
        hermes_email_ingest.add_review_item(
            sender="service@vodafone.de",
            subject="Ihre Rechnung",
            received_at="2026-11-07T12:00:00Z",
            summary="Monatsrechnung",
            category="REVIEW",
            confidence=0.9,
            db_path=self.db_path
        )
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.cursor()
            count = cursor.execute("SELECT count(*) FROM pending_reviews;").fetchone()[0]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
