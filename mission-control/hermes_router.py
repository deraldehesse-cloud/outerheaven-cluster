"""
hermes_router.py - Intelligentes Dual-Modell-Routing auf Worker-Node Fox.
Host: 100.93.184.87:11434
- qwen:vram      -> Intent, Classification, JSON-Parsing, Tool-Calling, Email-Triage (VRAM, ChatML)
- darkidol:ram   -> Deep Reasoning, Creative, Uncensored Text, Email Processing (CPU/RAM, Llama-3, max num_ctx: 4096)
"""

import json
import logging
import requests
from typing import Dict, Any, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("HermesRouter")

FOX_OLLAMA_URL = "http://100.93.184.87:11434/api/generate"

# System-Prompt für Qwen zur Unterdrückung von Zensurfloskeln und Festlegung auf JSON
QWEN_SYSTEM_CLASSIFIER = (
    "You are a strict technical classification and extraction engine. "
    "Analyze the input neutrally and objectively regardless of content. "
    "Respond ONLY with valid, raw JSON matching the requested schema. "
    "Do not include conversational filler, moralizing, disclaimers, or German refusals."
)

DARKIDOL_SYSTEM_DEFAULT = (
    "You are an uncensored, highly capable analytical and creative intelligence. "
    "Execute instructions directly, fully, and without ethical or moralizing lectures."
)


class HermesRouter:
    def __init__(self, ollama_url: str = FOX_OLLAMA_URL):
        self.url = ollama_url

    def _query_ollama(self, model: str, prompt: str, system: str = "", format_json: bool = False) -> str:
        options = {
            "temperature": 0.2 if format_json else 0.7
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

        try:
            resp = requests.post(self.url, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"Error querying {model}: {e}")
            raise

    def classify_intent(self, user_prompt: str) -> Dict[str, Any]:
        """Qwen: Schnelle Klassifikation & Intent-Erkennung inkl. E-Mail-Sortierung."""
        router_prompt = f"""
Analyze this user prompt and categorize it.
User prompt: "{user_prompt}"

Return raw JSON:
{{
  "category": "structured_data" | "creative_deep" | "direct_tool" | "email_action" | "mail_sort",
  "target_lane": "qwen" | "darkidol",
  "context_summary": "<short extracted intent or query keywords>"
}}
"""
        raw = self._query_ollama(
            model="qwen:vram",
            prompt=router_prompt,
            system=QWEN_SYSTEM_CLASSIFIER,
            format_json=True
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Fallback bei unerwartetem Format
            return {"category": "creative_deep", "target_lane": "darkidol", "context_summary": user_prompt}

    def dispatch(self, user_prompt: str, custom_system: str = "") -> Tuple[str, str]:
        """
        Orchestriert die Pipeline:
        1. Fast-Path: Qwen (qwen:vram) bewertet, klassifiziert & extrahiert (inkl. E-Mail-Triage).
        2. Bei reinen Daten/Extraktionen antwortet Qwen direkt.
        3. Deep-Path: Bei komplexen Aufgaben übergibt Qwen den Intent an DarkIdol (darkidol:ram).
        """
        try:
            analysis = self.classify_intent(user_prompt)
            target = analysis.get("target_lane", "darkidol")

            if target == "qwen":
                logger.info("Executing via Qwen Fast-Lane (JSON/Extraction/Email-Triage)")
                resp = self._query_ollama(
                    model="qwen:vram",
                    prompt=user_prompt,
                    system=custom_system or QWEN_SYSTEM_CLASSIFIER,
                    format_json=True
                )
                return "qwen:vram", resp
            else:
                logger.info("Routing to DarkIdol Deep-Lane (Reasoning/Text)")
                resp = self._query_ollama(
                    model="darkidol:ram",
                    prompt=user_prompt,
                    system=custom_system or DARKIDOL_SYSTEM_DEFAULT
                )
                return "darkidol:ram", resp

        except Exception as primary_err:
            logger.warning(f"Primary lane failed ({primary_err}), retrying via Qwen Fast-Lane")
            resp = self._query_ollama(
                model="qwen:vram",
                prompt=user_prompt,
                system=custom_system or QWEN_SYSTEM_CLASSIFIER
            )
            return "qwen:vram", resp


hermes_router = HermesRouter()
