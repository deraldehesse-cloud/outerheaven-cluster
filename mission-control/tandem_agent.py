"""
tandem_agent.py - Fox Hermes Agent: Consolidated Single-Model Engine.
Host: 100.93.184.87:11434

Architecture:
Consolidated to hermes3:8b for fast intent extraction, reasoning, tool workflows, and dialog.
Optional fallback: hermes-3-3b-q8:latest.
"""

import json
import logging
import datetime
import requests
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("TandemAgent")

FOX_OLLAMA_URL = "http://100.93.184.87:11434/api/generate"
DEFAULT_MODEL = "hermes3:8b"
FALLBACK_MODEL = "hermes-3-3b-q8:latest"
FOX_MODEL_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/fox_model.json")


def get_active_model() -> str:
    if FOX_MODEL_FILE.is_file():
        try:
            data = json.loads(FOX_MODEL_FILE.read_text(encoding="utf-8"))
            return data.get("model", DEFAULT_MODEL)
        except Exception:
            pass
    return DEFAULT_MODEL


class TandemAgent:
    def __init__(self, ollama_url: str = FOX_OLLAMA_URL):
        self.url = ollama_url

    def _get_gate_system_prompt(self, current_dt: Optional[datetime.datetime] = None) -> str:
        if current_dt is None:
            current_dt = datetime.datetime.now()

        today_str = current_dt.strftime("%Y-%m-%d")
        weekday_de = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][current_dt.weekday()]
        current_year = current_dt.year

        return (
            "You are Hermes, the primary assistant and dispatcher on Node Fox. "
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
            "5. If it is purely conversational or a complex reasoning task not needing slots, set 'target_lane': 'worker' "
            "and 'needs_clarification': false.\n\n"
            "SCHEMA:\n"
            "{\n"
            '  "intent": "calendar_event" | "email_action" | "mail_sort" | "general_query" | "creative_deep" | "direct_chat",\n'
            '  "target_lane": "gate" | "worker",\n'
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

    def _get_worker_system_prompt(self, current_dt: Optional[datetime.datetime] = None) -> str:
        if current_dt is None:
            current_dt = datetime.datetime.now()
        today_str = current_dt.strftime("%Y-%m-%d")
        return (
            "You are Hermes-3, the consolidated intelligence of Node Fox. "
            f"Current date: {today_str}. "
            "You execute complex reasoning, uncensored text generation, in-depth analysis, and cluster workflows. "
            "Answer directly, precisely, and without moralizing lectures or artificial disclaimers. "
            "Communicate in natural German."
        )

    def _query(self, model: str, prompt: str, system: str = "", format_json: bool = False, temperature: float = 0.2, timeout: Optional[int] = None) -> str:
        if timeout is None:
            timeout = 45 if format_json else 120

        num_tokens = 350 if format_json else 1000
        options = {
            "temperature": temperature,
            "num_predict": num_tokens
        }

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

        try:
            resp = requests.post(self.url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as e:
            if model != FALLBACK_MODEL:
                logger.warning(f"Error querying {model}, attempting fallback to {FALLBACK_MODEL}: {e}")
                payload["model"] = FALLBACK_MODEL
                resp = requests.post(self.url, json=payload, timeout=timeout)
                resp.raise_for_status()
                return resp.json().get("response", "").strip()
            raise

    def process(self, user_input: str, custom_system: str = "") -> Dict[str, Any]:
        """
        Hermes execution pipeline on Node Fox:
        1. Fast intent extraction & slot check.
        2. Clarification if missing critical slots, otherwise direct completion or deep reasoning.
        """
        active_model = get_active_model()
        now = datetime.datetime.now()
        gate_system = self._get_gate_system_prompt(now)

        # 1. Intent extraction
        try:
            raw_gate = self._query(
                model=active_model,
                prompt=f'Analyze and extract from user input: "{user_input}"',
                system=gate_system,
                format_json=True,
                temperature=0.1
            )
            gate_data = json.loads(raw_gate)
        except Exception as e:
            logger.warning(f"Hermes intent extraction fallback due to error: {e}")
            gate_data = {
                "intent": "general_query",
                "target_lane": "worker",
                "needs_clarification": False,
                "user_message": "",
                "extracted_slots": {}
            }

        needs_clarification = gate_data.get("needs_clarification", False)
        gate_msg = gate_data.get("user_message", "")
        target_lane = gate_data.get("target_lane", "gate")

        # Fall A: Clarification needed
        if needs_clarification and gate_msg:
            return {
                "stage": "clarification_needed",
                "active_model": active_model,
                "message": gate_msg,
                "slots": gate_data.get("extracted_slots", {}),
                "intent": gate_data.get("intent", "unknown")
            }

        # Fall B: Pure slot extraction or immediate response
        if target_lane == "gate" and not gate_data.get("intent") in ("creative_deep", "general_query"):
            slots = gate_data.get("extracted_slots", {})
            ack = gate_msg or "Alles klar, habe ich erfasst."
            return {
                "stage": "fast_lane_complete",
                "active_model": active_model,
                "message": ack,
                "slots": slots,
                "intent": gate_data.get("intent")
            }

        # Fall C: Deep reasoning / workflow execution
        worker_system = custom_system or self._get_worker_system_prompt(now)
        task_prompt = (
            f"User request: {user_input}\n"
            f"Extracted context: {json.dumps(gate_data.get('extracted_slots', {}), ensure_ascii=False)}\n"
            "Perform the deep reasoning, creative writing, or analytical execution now:"
        )

        try:
            worker_resp = self._query(
                model=active_model,
                prompt=task_prompt,
                system=worker_system,
                temperature=0.7
            )
            final_text = f"{gate_msg}\n\n{worker_resp}".strip() if gate_msg else worker_resp
            return {
                "stage": "deep_lane_complete",
                "active_model": active_model,
                "message": final_text,
                "slots": gate_data.get("extracted_slots", {}),
                "intent": gate_data.get("intent")
            }
        except Exception as worker_err:
            logger.warning(f"Hermes execution error: {worker_err}")
            return {
                "stage": "error",
                "active_model": active_model,
                "message": f"Fehler bei der Ausführung auf Node Fox: {worker_err}",
                "slots": gate_data.get("extracted_slots", {}),
                "intent": gate_data.get("intent")
            }


tandem_agent = TandemAgent()
