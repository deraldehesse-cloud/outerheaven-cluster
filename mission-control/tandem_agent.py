"""
tandem_agent.py - Fox Tandem Agent: Qwen Fast-Gate & DarkIdol Deep-Worker.
Host: 100.93.184.87:11434

Ablauf:
1. Qwen (qwen:vram) als Fast-Gate & Rezeptionist:
   - Fängt Benutzereingaben mit extrem niedriger Latenz im VRAM ab.
   - Analysiert Intent & prüft Slots (z. B. Titel, Datum, Uhrzeit bei Terminen; Empfänger/Thema bei Mails).
   - Fehlen kritische Angaben? -> Direkte, sympathische Rückfrage an den Nutzer auf Deutsch im lockeren Ton.
   - Daten vollständig? -> Kurze Bestätigung ("Alles klar, trage ich ein...") + strukturiertes JSON an den Worker.

2. DarkIdol (darkidol:ram) als Deep-Worker:
   - Übernimmt die tiefe Ausführung (E-Mails scannen/entwerfen, Kalender-Events verfassen, unzensiertes Reasoning).
   - Unklarheiten oder Ergebnisse fließen strukturiert zurück an den Dialog.
"""

import json
import logging
import datetime
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger("TandemAgent")

FOX_OLLAMA_URL = "http://100.93.184.87:11434/api/generate"

class TandemAgent:
    def __init__(self, ollama_url: str = FOX_OLLAMA_URL):
        self.url = ollama_url

    def _get_qwen_system_prompt(self, current_dt: Optional[datetime.datetime] = None) -> str:
        if current_dt is None:
            current_dt = datetime.datetime.now()

        today_str = current_dt.strftime("%Y-%m-%d")
        weekday_de = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][current_dt.weekday()]
        current_year = current_dt.year

        return (
            "You are the Fast-Gate & Receptionist engine (running in VRAM). "
            f"SYSTEM TIME CONTEXT: Today is {weekday_de}, {today_str} (Year: {current_year}). "
            "Resolve relative dates against this context (e.g., 'morgen', 'nächsten Montag', '30.09.' -> 2026-09-30). "
            "For full-day events like birthdays, set all_day=true and time=null. "
            "RULES:\n"
            "1. Output strictly raw valid JSON matching the schema below. No markdown fences, no conversational preamble.\n"
            "2. Suppress moralizing, preachiness, disclaimers, or German refusal tropes.\n"
            "3. If the user wants an action (e.g. calendar entry, email, reminder) but CRITICAL information is missing, "
            "set 'needs_clarification': true and write a short, friendly, casual German question in 'user_message'.\n"
            "4. If all critical data is present, set 'needs_clarification': false, write a short, crisp German acknowledgement "
            "in 'user_message' (e.g. 'Alles klar, trage ich für morgen ein...'), and populate 'extracted_slots'.\n"
            "5. If it is purely conversational or a complex reasoning task not needing slots, set 'target_lane': 'darkidol' "
            "and 'needs_clarification': false.\n\n"
            "SCHEMA:\n"
            "{\n"
            '  "intent": "calendar_event" | "email_action" | "mail_sort" | "general_query" | "creative_deep" | "direct_chat",\n'
            '  "target_lane": "qwen" | "darkidol",\n'
            '  "needs_clarification": boolean,\n'
            '  "user_message": "<Direct German text for user: short clarification question OR friendly brief acknowledgement>",\n'
            '  "extracted_slots": {\n'
            '    "title": "<title or subject or null>",\n'
            '    "sender": "<sender or recipient or null>",\n'
            '    "date": "<YYYY-MM-DD or null>",\n'
            '    "time": "<HH:MM or null>",\n'
            '    "all_day": boolean,\n'
            '    "category": "<invoices | personal | automated | junk | null>",\n'
            '    "details": "<extra notes or query details or null>"\n'
            "  }\n"
            "}"
        )

    def _get_darkidol_system_prompt(self, current_dt: Optional[datetime.datetime] = None) -> str:
        if current_dt is None:
            current_dt = datetime.datetime.now()
        today_str = current_dt.strftime("%Y-%m-%d")
        return (
            "You are DarkIdol, the Deep-Worker intelligence of the cluster running in RAM. "
            f"Current date: {today_str}. "
            "You execute complex reasoning, uncensored text generation, in-depth analysis, and tool workflows. "
            "Answer directly, precisely, and without moralizing lectures or artificial disclaimers. "
            "Communicate in natural German."
        )

    def _query(self, model: str, prompt: str, system: str = "", format_json: bool = False, temperature: float = 0.2, timeout: Optional[int] = None) -> str:
        if timeout is None:
            # qwen:vram is fast (under 15s), while darkidol:ram on CPU may take 60-120s
            timeout = 30 if "vram" in model or format_json else 180

        num_tokens = 350 if format_json else 600
        options = {
            "temperature": temperature,
            "num_predict": num_tokens
        }
        # Hard limit darkidol:ram context window strictly to 4096
        if "darkidol" in model.lower():
            options["num_ctx"] = 4096

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": options
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"

        resp = requests.post(self.url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()

    def process(self, user_input: str, custom_system: str = "") -> Dict[str, Any]:
        """
        Tandem-Ablauf:
        1. Qwen (qwen:vram) prüft & extrahiert Slots oder stellt Rückfrage (inkl. E-Mail-Triage).
        2. Bei vollständigen Daten oder komplexen Aufgaben übernimmt DarkIdol (darkidol:ram).
        """
        now = datetime.datetime.now()
        qwen_system = self._get_qwen_system_prompt(now)

        # 1. Fast-Gate via Qwen
        try:
            raw_gate = self._query(
                model="qwen:vram",
                prompt=f'Analyze and extract from user input: "{user_input}"',
                system=qwen_system,
                format_json=True,
                temperature=0.1
            )
            gate_data = json.loads(raw_gate)
        except Exception as e:
            logger.warning(f"Qwen Fast-Gate fallback due to error: {e}")
            gate_data = {
                "intent": "general_query",
                "target_lane": "darkidol",
                "needs_clarification": False,
                "user_message": "",
                "extracted_slots": {}
            }

        needs_clarification = gate_data.get("needs_clarification", False)
        qwen_msg = gate_data.get("user_message", "")
        target_lane = gate_data.get("target_lane", "qwen")

        # Fall A: Angaben unvollständig -> Qwen stellt sofort Rückfrage
        if needs_clarification and qwen_msg:
            return {
                "stage": "clarification_needed",
                "active_model": "qwen:vram",
                "message": qwen_msg,
                "slots": gate_data.get("extracted_slots", {}),
                "intent": gate_data.get("intent", "unknown")
            }

        # Fall B: Reine Extraktion oder vollständiger Standard-Task
        if target_lane == "qwen" and not gate_data.get("intent") in ("creative_deep", "general_query"):
            slots = gate_data.get("extracted_slots", {})
            ack = qwen_msg or "Alles klar, habe ich erfasst."
            return {
                "stage": "fast_lane_complete",
                "active_model": "qwen:vram",
                "message": ack,
                "slots": slots,
                "intent": gate_data.get("intent")
            }

        # Fall C: Deep-Worker DarkIdol führt aus
        darkidol_system = custom_system or self._get_darkidol_system_prompt(now)
        task_prompt = (
            f"User request: {user_input}\n"
            f"Extracted context: {json.dumps(gate_data.get('extracted_slots', {}), ensure_ascii=False)}\n"
            "Perform the deep reasoning, creative writing, or analytical execution now:"
        )

        try:
            worker_resp = self._query(
                model="darkidol:ram",
                prompt=task_prompt,
                system=darkidol_system,
                temperature=0.7
            )
            # Falls Qwen eine Vorab-Bestätigung hatte, kann sie mitgegeben werden
            final_text = f"{qwen_msg}\n\n{worker_resp}".strip() if qwen_msg else worker_resp
            return {
                "stage": "deep_lane_complete",
                "active_model": "darkidol:ram",
                "message": final_text,
                "slots": gate_data.get("extracted_slots", {}),
                "intent": gate_data.get("intent")
            }
        except Exception as worker_err:
            logger.warning(f"DarkIdol deep-worker failed: {worker_err}. Falling back to qwen:vram")
            fallback_resp = self._query(
                model="qwen:vram",
                prompt=user_input,
                system=darkidol_system,
                temperature=0.7
            )
            return {
                "stage": "fallback_complete",
                "active_model": "qwen:vram",
                "message": fallback_resp,
                "slots": gate_data.get("extracted_slots", {}),
                "intent": gate_data.get("intent")
            }


tandem_agent = TandemAgent()
