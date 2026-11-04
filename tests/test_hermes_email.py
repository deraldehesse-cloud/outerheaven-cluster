#!/usr/bin/env python3
"""
test_hermes_email.py - Comprehensive Unit & Integration Tests
============================================================
Tests for:
1. hermes_email_handler.py (Database, Outbox CRUD, SMTP Simulation, megan:hermes Prompt-Logik)
2. hermes_web_ui.py (Backend handler functions for /api/email/outbox, /send, /generate, skill preset)
3. hermes_paperless_advisor.py (Autonomous email drafting from Paperless data)
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import hermes_email_handler
import hermes_web_ui


class TestHermesEmailHandler(unittest.TestCase):
    def setUp(self):
        # Temporäre Datenbank für isolierte Tests
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_outbox.db"
        hermes_email_handler.init_outbox_db(self.db_path)
        self.orig_get_db_path = hermes_email_handler.get_db_path
        hermes_email_handler.get_db_path = lambda: self.db_path

    def tearDown(self):
        hermes_email_handler.get_db_path = self.orig_get_db_path
        self.tmp_dir.cleanup()

    def test_outbox_crud_and_status_flow(self):
        """Testet den gesamten Lebenszyklus eines Entwurfs: Erstellen -> Bearbeiten -> Freigeben -> Senden."""
        # 1. Entwurf erstellen
        draft = hermes_email_handler.create_email_record(
            recipient="inkasso@pairfinance.de",
            subject="Rückmeldung zum Aktenzeichen AZ-78901",
            body="Sehr geehrte Damen und Herren,\nIhre Nachricht liegt vor...",
            is_draft=True,
            document_id="42",
            metadata={"aktenzeichen": "AZ-78901", "amount_eur": 129.50, "correspondent": "PAIR Finance"},
            auto_send=False
        )

        self.assertIsNotNone(draft)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["recipient"], "inkasso@pairfinance.de")
        self.assertEqual(draft["document_id"], "42")
        self.assertEqual(draft["metadata"]["aktenzeichen"], "AZ-78901")

        draft_id = draft["id"]

        # 2. Entwurf auflisten
        outbox = hermes_email_handler.list_outbox(status_filter="draft")
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["id"], draft_id)

        # 3. Entwurf bearbeiten
        updated = hermes_email_handler.update_existing_draft(
            draft_id=draft_id,
            subject="Prüfung & Fristverlängerung zum Aktenzeichen AZ-78901"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["subject"], "Prüfung & Fristverlängerung zum Aktenzeichen AZ-78901")

        # 4. Entwurf freigeben und absenden (Simulation)
        with patch.object(hermes_email_handler, 'send_desktop_notification') as mock_notif:
            success, msg, sent_item = hermes_email_handler.send_existing_draft(draft_id)
            self.assertTrue(success)
            self.assertEqual(sent_item["status"], "sent")
            self.assertIsNotNone(sent_item["sent_at"])
            mock_notif.assert_called_once()

        # 5. Prüfen, dass keine offenen Entwürfe mehr da sind
        remaining_drafts = hermes_email_handler.list_outbox(status_filter="draft")
        self.assertEqual(len(remaining_drafts), 0)

        # 6. Prüfen, dass gesendete Mail im Protokoll ist
        sent_list = hermes_email_handler.list_outbox(status_filter="sent")
        self.assertEqual(len(sent_list), 1)

    def test_auto_send_workflow(self):
        """Testet den vollautonomen Direktversand (Auto-Send)."""
        with patch.object(hermes_email_handler, 'send_desktop_notification') as mock_notif:
            record = hermes_email_handler.create_email_record(
                recipient="service@telekom.de",
                subject="Rechnungsprüfung Telekom",
                body="Sehr geehrte Damen und Herren...",
                is_draft=False,
                document_id="101",
                auto_send=True
            )
            self.assertEqual(record["status"], "sent")
            self.assertIsNotNone(record["sent_at"])
            mock_notif.assert_called_once()

    def test_discard_draft(self):
        """Testet das Verwerfen eines Entwurfs."""
        draft = hermes_email_handler.create_email_record(
            recipient="test@example.com",
            subject="Test Entwurf",
            body="Inhalt...",
            is_draft=True
        )
        draft_id = draft["id"]
        deleted = hermes_email_handler.cancel_existing_draft(draft_id, delete_permanent=True)
        self.assertTrue(deleted)
        self.assertIsNone(hermes_email_handler.get_outbox_item(draft_id))

    def test_autonomous_email_generation(self):
        """Testet die deeskalierende Prompt-Logik (Fallback und Formatierung)."""
        res = hermes_email_handler.generate_autonomous_email_text(
            correspondent="BKK Krankenkasse",
            aktenzeichen="KV-991204",
            betrag="84,50",
            due_date="15.11.2026",
            doc_type="Rechnung",
            user_name="Sebastian"
        )
        self.assertIn("subject", res)
        self.assertIn("body", res)
        self.assertIn("KV-991204", res["subject"] + res["body"])
        self.assertIn("Sebastian", res["body"])


class TestWebUIHandlers(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_web_outbox.db"
        hermes_email_handler.init_outbox_db(self.db_path)
        self.orig_get_db_path = hermes_email_handler.get_db_path
        hermes_email_handler.get_db_path = lambda: self.db_path

        self.orig_skills_file = hermes_web_ui.SKILLS_FILE
        self.orig_hermes_skills_file = hermes_web_ui.HERMES_SKILLS_FILE
        self.test_skills_file = Path(self.tmp_dir.name) / "test_skills.json"
        with open(self.test_skills_file, "w", encoding="utf-8") as f:
            json.dump([], f)
        hermes_web_ui.SKILLS_FILE = str(self.test_skills_file)
        hermes_web_ui.HERMES_SKILLS_FILE = str(self.test_skills_file)

    def tearDown(self):
        hermes_email_handler.get_db_path = self.orig_get_db_path
        hermes_web_ui.SKILLS_FILE = self.orig_skills_file
        hermes_web_ui.HERMES_SKILLS_FILE = self.orig_hermes_skills_file
        self.tmp_dir.cleanup()

    def test_api_outbox_handlers(self):
        """Testet die Handler get_email_outbox, send_email_draft, update_email_draft, generate_email direkt."""
        # 1. Leere Outbox
        outbox = hermes_web_ui.get_email_outbox()
        self.assertEqual(outbox["emails"], [])

        # 2. Generieren
        gen_req = hermes_web_ui.EmailGenerateRequest(
            correspondent="EOS Deutscher Inkassodienst",
            aktenzeichen="EOS-2026-X9",
            betrag=240.0,
            due_date="20.11.2026",
            doc_type="Mahnung",
            action_mode="draft"
        )
        gen_res = hermes_web_ui.generate_email(gen_req)
        self.assertEqual(gen_res["status"], "success")
        self.assertEqual(gen_res["action"], "draft")
        draft_id = gen_res["email"]["id"]

        # 3. Item abrufen
        item = hermes_web_ui.get_email_draft(draft_id)
        self.assertEqual(item["metadata"]["aktenzeichen"], "EOS-2026-X9")

        # 4. Item bearbeiten
        update_req = hermes_web_ui.EmailDraftUpdateRequest(
            subject="Aktualisierter Betreff AZ EOS-2026-X9"
        )
        update_res = hermes_web_ui.update_email_draft(draft_id, update_req)
        self.assertEqual(update_res["item"]["subject"], "Aktualisierter Betreff AZ EOS-2026-X9")

        # 5. Item absenden
        with patch.object(hermes_email_handler, 'send_desktop_notification'):
            send_res = hermes_web_ui.send_email_draft(draft_id)
            self.assertEqual(send_res["status"], "success")
            self.assertEqual(send_res["item"]["status"], "sent")

    def test_skill_create_with_action_mode(self):
        """Testet das Erstellen des E-Mail Skills mit action_mode."""
        req = hermes_web_ui.SkillCreateRequest(
            name="test_email_workflow_custom",
            description="Erstelle automatische E-Mail-Entwürfe",
            parameters="absender, aktenzeichen",
            handler_type="E-Mail Dispatch & Draft",
            action_mode="draft"
        )
        res = hermes_web_ui.create_skill(req)
        self.assertEqual(res["status"], "success")
        skill = res["skill"]
        self.assertEqual(skill["handler"]["action_mode"], "draft")
        self.assertEqual(skill["handler"]["type"], "E-Mail Dispatch & Draft")


class TestPaperlessAdvisorWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_paperless_outbox.db"
        hermes_email_handler.init_outbox_db(self.db_path)
        self.orig_get_db_path = hermes_email_handler.get_db_path
        hermes_email_handler.get_db_path = lambda: self.db_path

    def tearDown(self):
        hermes_email_handler.get_db_path = self.orig_get_db_path
        self.tmp_dir.cleanup()

    @patch("requests.post")
    @patch("requests.patch")
    @patch("requests.get")
    def test_paperless_email_draft_creation(self, mock_get, mock_patch, mock_post):
        """Testet automatische E-Mail-Entwurf-Erstellung bei eingehendem Paperless-Dokument."""
        import hermes_paperless_advisor

        # 1. Mock Paperless Dokument GET
        mock_doc_resp = MagicMock()
        mock_doc_resp.status_code = 200
        mock_doc_resp.json.return_value = {
            "id": 99,
            "title": "BKK Rechnung",
            "correspondent": "BKK Krankenkasse",
            "content": "Rechnung der BKK Krankenkasse\nAktenzeichen: KV-2026-9912\nForderung: 149,99 EUR\nKontakt: rechnung@bkk-direkt.de"
        }
        mock_get.return_value = mock_doc_resp

        # 2. Mock Ollama LLM Response
        mock_ollama_resp = MagicMock()
        mock_ollama_resp.status_code = 200
        mock_ollama_resp.json.return_value = {
            "response": json.dumps({
                "title": "2026-11-02 BKK Krankenkasse - Rechnung - Beitragsnachforderung",
                "correspondent": "BKK Krankenkasse",
                "created_date": "2026-11-02",
                "document_type": "Rechnung",
                "urgency": "GELB",
                "amount_eur": 149.99,
                "due_date": "2026-11-20",
                "is_duplicate_or_invalid": False,
                "advisor_note": "Forderung über 149,99 EUR zur Prüfung vorgemerkt."
            })
        }
        # Ollama Call für Analysis und Paperless Notes Call
        mock_post.side_effect = [
            mock_ollama_resp,  # Ollama Triage
            MagicMock(status_code=200)  # Paperless Note POST
        ]

        result = hermes_paperless_advisor.run_hermes_advisor(document_id="99")

        self.assertIsNotNone(result)
        self.assertIn("email", result)
        email_rec = result["email"]
        self.assertIsNotNone(email_rec)
        self.assertEqual(email_rec["status"], "draft")
        self.assertEqual(email_rec["recipient"], "rechnung@bkk-direkt.de")
        self.assertEqual(email_rec["metadata"]["aktenzeichen"], "KV-2026-9912")

        # Prüfe, dass Notiz an Paperless den Status enthält
        note_call_args = mock_post.call_args_list[-1]
        note_json = note_call_args.kwargs.get("json", {})
        self.assertIn("[✉️ E-Mail Entwurf]", note_json.get("note", ""))


if __name__ == "__main__":
    unittest.main()

