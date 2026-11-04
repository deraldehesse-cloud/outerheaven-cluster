#!/usr/bin/env python3
"""
test_hermes_reminder.py - Testsuite für Hermes Agent Lebensplan & Smart Reminder
Testet:
1. Subtask A: NLP-Extraktion, Persistenz in lebensplan.json, Background-Scheduler und Push-Dispatch
2. Subtask B: Smart Speaker API mit Feature-Toggle (SPEAKER_ENABLED=false/true)
3. Subtask C: Skill-Konfiguration in hermes_skills.json & skills_registry.json sowie Web-UI APIs
"""

import os
import sys
import json
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Projektverzeichnis zum Pfad hinzufügen
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import hermes_reminder
from hermes_reminder import (
    parse_german_datetime,
    extract_reminder_intent,
    add_reminder,
    get_reminders,
    cancel_reminder,
    delete_reminder,
    load_lebensplan,
    save_lebensplan,
    dispatch_reminder,
    dispatch_to_smart_speaker,
    ReminderScheduler,
    is_speaker_enabled
)


class TestHermesReminderExtraction(unittest.TestCase):
    """Testet die Extraktion von Zeitpunkten und Vorhaben aus natürlicher Sprache."""

    def setUp(self):
        # Feste Referenzzeit: Montag, 02.11.2026 um 12:00:00 Uhr
        self.ref_time = datetime(2026, 11, 2, 12, 0, 0)

    def test_extract_morgen_vorhaben(self):
        prompt = "Ich habe morgen vor zum Bürgeramt zu gehen, erinnere mich um 15 Uhr"
        res = extract_reminder_intent(prompt, reference_time=self.ref_time)
        self.assertIsNotNone(res)
        expected_dt = datetime(2026, 11, 3, 15, 0, 0)
        self.assertEqual(res["due_time"], expected_dt)
        self.assertIn("Bürgeramt", res["task_description"])
        self.assertEqual(res["formatted_datetime"], "morgen um 15:00 Uhr")

    def test_extract_in_minuten(self):
        prompt = "Erinnere mich in 25 Minuten an Pizza aus dem Ofen holen"
        res = extract_reminder_intent(prompt, reference_time=self.ref_time)
        self.assertIsNotNone(res)
        expected_dt = self.ref_time + timedelta(minutes=25)
        self.assertEqual(res["due_time"], expected_dt)
        self.assertIn("Pizza", res["task_description"])

    def test_extract_heute_uhrzeit(self):
        prompt = "Erinnere mich heute um 18:30 Uhr an das Telefonat mit Snake"
        res = extract_reminder_intent(prompt, reference_time=self.ref_time)
        self.assertIsNotNone(res)
        expected_dt = datetime(2026, 11, 2, 18, 30, 0)
        self.assertEqual(res["due_time"], expected_dt)
        self.assertIn("Telefonat", res["task_description"])

    def test_extract_konkretes_datum(self):
        prompt = "Am 15.11. um 14:00 Uhr Steuerberater anrufen"
        res = extract_reminder_intent(prompt, reference_time=self.ref_time)
        self.assertIsNotNone(res)
        expected_dt = datetime(2026, 11, 15, 14, 0, 0)
        self.assertEqual(res["due_time"], expected_dt)
        self.assertIn("Steuerberater", res["task_description"])

    def test_non_reminder_text(self):
        prompt = "Wie ist das Wetter heute im Cluster?"
        res = extract_reminder_intent(prompt, reference_time=self.ref_time)
        self.assertIsNone(res)


class TestHermesReminderPersistenceAndScheduler(unittest.TestCase):
    """Testet Persistenz, Scheduling und Push-Dispatch."""

    def setUp(self):
        self.test_file = BASE_DIR / "test_lebensplan.json"
        if self.test_file.exists():
            self.test_file.unlink()

    def tearDown(self):
        if self.test_file.exists():
            self.test_file.unlink()

    def test_add_and_get_reminder(self):
        due = datetime.now() + timedelta(hours=2)
        item = add_reminder(
            task_description="Backup prüfen",
            due_time=due,
            push_notify=True,
            speaker_broadcast=False,
            filepath=self.test_file
        )
        self.assertTrue(item["id"].startswith("rem_"))
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["task_description"], "Backup prüfen")

        items = get_reminders(filepath=self.test_file)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], item["id"])

    def test_cancel_and_delete_reminder(self):
        due = datetime.now() + timedelta(hours=1)
        item = add_reminder("Meeting", due, filepath=self.test_file)
        rem_id = item["id"]

        # Cancel
        success = cancel_reminder(rem_id, filepath=self.test_file)
        self.assertTrue(success)
        items = get_reminders(status_filter="cancelled", filepath=self.test_file)
        self.assertEqual(len(items), 1)

        # Delete
        del_success = delete_reminder(rem_id, filepath=self.test_file)
        self.assertTrue(del_success)
        all_items = get_reminders(filepath=self.test_file)
        self.assertEqual(len(all_items), 0)

    @patch("hermes_reminder.send_telegram_notification")
    @patch("hermes_reminder.send_desktop_notification")
    def test_scheduler_check_and_dispatch(self, mock_desktop, mock_tg):
        mock_tg.return_value = True
        mock_desktop.return_value = True

        # Fälliger Eintrag (1 Sekunde in der Vergangenheit)
        past_time = datetime.now() - timedelta(seconds=1)
        item = add_reminder("Sofortiger Alarm", past_time, push_notify=True, filepath=self.test_file)

        scheduler = ReminderScheduler(filepath=self.test_file)
        dispatched_count = scheduler.check_and_dispatch()

        self.assertEqual(dispatched_count, 1)
        mock_tg.assert_called_once()
        mock_desktop.assert_called_once()

        # Status muss nun "completed" sein
        items = get_reminders(filepath=self.test_file)
        self.assertEqual(items[0]["status"], "completed")


class TestSmartSpeakerApi(unittest.TestCase):
    """Testet Subtask B: Xiaomi Smart Speaker Vorbereitung & Feature-Toggle."""

    def test_speaker_disabled_by_default(self):
        with patch.dict(os.environ, {"SPEAKER_ENABLED": "false"}):
            res = dispatch_to_smart_speaker("Hallo Big Boss")
            self.assertEqual(res["status"], "disabled")
            self.assertFalse(res["enabled"])
            self.assertIn("SPEAKER_ENABLED=false", res["message"])

    @patch("urllib.request.urlopen")
    def test_speaker_enabled_webhook_dispatch(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with patch.dict(os.environ, {
            "SPEAKER_ENABLED": "true",
            "SPEAKER_WEBHOOK_URL": "http://127.0.0.1:8123/api/webhook/xiaomi_speaker_tts"
        }):
            self.assertTrue(is_speaker_enabled())
            res = dispatch_to_smart_speaker("Test Sprachausgabe")
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["enabled"])
            self.assertEqual(res["status_code"], 200)
            mock_urlopen.assert_called_once()


class TestSkillCenterConfiguration(unittest.TestCase):
    """Testet Subtask C: Skill-Preset in hermes_skills.json und skills_registry.json."""

    def test_skills_files_contain_lebensplan_preset(self):
        for fname in ["hermes_skills.json", "skills_registry.json"]:
            fpath = BASE_DIR / fname
            self.assertTrue(fpath.is_file(), f"{fname} existiert nicht")
            with open(fpath, "r", encoding="utf-8") as f:
                skills = json.load(f)

            lebensplan_skill = next((s for s in skills if s.get("id") == "lebensplan_reminder"), None)
            self.assertIsNotNone(lebensplan_skill, f"lebensplan_reminder fehlt in {fname}")

            # Überprüfung der vorgegebenen Parameter und Konfiguration
            self.assertIn("datum_uhrzeit", lebensplan_skill.get("parameters", []))
            self.assertIn("vorhaben_aufgabe", lebensplan_skill.get("parameters", []))
            self.assertIn("Pläne äußert", lebensplan_skill.get("description", ""))

            handler = lebensplan_skill.get("handler", {})
            self.assertTrue(handler.get("push_notify"))
            self.assertFalse(handler.get("speaker_broadcast"))


class TestWebUiIntegration(unittest.TestCase):
    """Testet die Integration in die Web-UI Endpunkte und Chat-Erkennung."""

    def test_web_ui_lebensplan_endpoints(self):
        import hermes_web_ui
        # 1. Add item
        req = hermes_web_ui.ReminderCreateRequest(
            task="Serverwartung Cluster",
            due_time="morgen um 11:00 Uhr",
            push_notify=True,
            speaker_broadcast=False
        )
        res = hermes_web_ui.add_lebensplan_item(req)
        self.assertEqual(res["status"], "success")
        item_id = res["item"]["id"]

        # 2. List items
        items_res = hermes_web_ui.get_lebensplan_items()
        self.assertIn("items", items_res)
        found = any(i["id"] == item_id for i in items_res["items"])
        self.assertTrue(found)

        # 3. Delete item
        del_res = hermes_web_ui.delete_lebensplan_item(item_id)
        self.assertEqual(del_res["status"], "success")

    @patch("requests.post")
    def test_chat_endpoint_reminder_detection(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Verstanden, Big Boss."}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        import hermes_web_ui
        chat_req = hermes_web_ui.ChatRequest(
            model="hermes-3-3b-q4:latest",
            prompt="Ich habe morgen vor X, erinnere mich um 15 Uhr",
            node="outerheaven"
        )
        res = hermes_web_ui.chat(chat_req)
        self.assertTrue(res.get("reminder_detected"))
        self.assertIn("[Lebensplan]", res.get("response", ""))
        self.assertIn("15:00 Uhr", res.get("response", ""))

    def test_smart_speaker_endpoint(self):
        import hermes_web_ui
        with patch.dict(os.environ, {"SPEAKER_ENABLED": "false"}):
            res = hermes_web_ui.test_smart_speaker("Hallo Outer Heaven")
            self.assertEqual(res["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
