#!/usr/bin/env python3
"""
model_router.py - Intelligent Cluster Model Router & Uncensored Persona Pipeline.

Prioritizes uncensored models on Outer Heaven (Arc A770 Compute Node),
with seamless fallback to Fox, completely bypassing standard guardrail refusals.

Cluster Priority Chain:
  1. outerheaven:11434 -> lexi-llama-3-8b-uncensored_q4_k_m:latest (Intimate Persona)
  2. outerheaven:11434 -> darkidol-8b:latest (Uncensored Roleplay)
  3. fox:11434         -> darkidol-8b:latest (Local SFF Fallback)
  4. fox:11434         -> hermes:latest (Lightweight Local Fallback)
"""

import os
import sys
import time
import logging
import requests
from typing import List, Dict, Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [model_router]: %(message)s")
logger = logging.getLogger("model_router")

# Cluster Nodes
LOCAL_OUTERHEAVEN_HOSTS = [
    os.environ.get("OLLAMA_LOCAL_URL", "http://127.0.0.1:11434"),
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    os.environ.get("OLLAMA_OUTERHEAVEN_URL", "http://outerheaven:11434"),
    "http://192.168.188.21:11434",
    "http://100.116.38.29:11434"
]
FOX_WORKER_HOSTS = [
    os.environ.get("OLLAMA_FOX_FALLBACK_URL", "http://192.168.188.35:11434"),
    "http://fox:11434",
    "http://100.106.14.81:11434"
]

ROUTING_TARGETS = [
    {"hosts": LOCAL_OUTERHEAVEN_HOSTS, "model": "lexi-llama-3-8b-uncensored_q4_k_m:latest", "desc": "Outer Heaven Local - Lexi Llama 3 8B Uncensored"},
    {"hosts": LOCAL_OUTERHEAVEN_HOSTS, "model": "darkidol-8b:latest", "desc": "Outer Heaven Local - DarkIdol 8B Uncensored"},
    {"hosts": FOX_WORKER_HOSTS, "model": "darkidol-8b:latest", "desc": "Fox Worker Fallback - DarkIdol 8B"},
    {"hosts": FOX_WORKER_HOSTS, "model": "hermes:latest", "desc": "Fox Worker Fallback - Hermes 3.6B"}
]

DEFAULT_OPTIONS = {
    "temperature": 0.95,
    "top_p": 0.90,
    "top_k": 40,
    "repeat_penalty": 1.25,
    "num_predict": 350,
    "num_ctx": 4096,
    "stop": ["<|im_end|>", "<|endoftext|>", "User:", "Assistant:", "\nUser:", "\nAssistant:"]
}


import re

# DeepSeek Reasoning / Math / Logic routing model
REASONING_MODEL = os.environ.get("REASONING_MODEL", "deepseek-r1:14b")

REASONING_KEYWORDS = [
    # Math & Calculations
    r"\b(?:rechne|berechne|kalkuliere|wieviel\s+ist|wie\s+viel\s+ist|summe|multipliziere|dividiere|prozent|gleichung|integral|ableitung)\b",
    r"\b\d+[\s]*[\+\-\*\/\^%][\s]*\d+\b",
    # Logic & Puzzles
    r"\b(?:r[äa]tsel|logik|deduktion|schlussfolgerung|wahrheitstabelle|beweise|beweis)\b",
    # Coding & Algorithms
    r"\b(?:schreibe\s+(?:ein|einen)?\s*(?:python|bash|javascript|c\+\+|sql|regex|skript|programm|code|funktion))\b",
    r"\b(?:algorit(?:hmus|hms)|datenstruktur|komplexit[äa]t|o\(n\)|refactor(?:e|ing)?|debug(?:ge|gen)?)\b",
    r"```(?:python|bash|sh|sql|json|javascript|js|cpp|c|go|rust|html|css)",
    # Structured Tables & Fact Sheets
    r"\b(?:erstelle\s+(?:eine)?\s*(?:tabelle|matrix|vergleichstabelle|csv|markdown-tabelle))\b",
    r"\b(?:analysiere\s+(?:die)?\s*(?:daten|zahlen|statistik|bilanz|tabelle))\b"
]
REASONING_REGEX = re.compile("|".join(REASONING_KEYWORDS), re.IGNORECASE)


def is_reasoning_or_math_prompt(messages: List[Dict[str, str]]) -> bool:
    """Classifies whether the user prompt contains math, code, logic puzzles or structured table tasks."""
    if not messages:
        return False
    # Check the last user message
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if REASONING_REGEX.search(content):
                return True
            break
    return False


def flush_model_slot(model_name: str, host_url: str = "http://127.0.0.1:11434") -> bool:
    """Flushes a model from VRAM by setting keep_alive: 0."""
    try:
        url = f"{host_url.rstrip('/')}/api/generate"
        r = requests.post(url, json={"model": model_name, "keep_alive": 0}, timeout=5)
        logger.info(f"Slot flushed for '{model_name}' on {host_url} (HTTP {r.status_code})")
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"Failed to flush slot for {model_name} on {host_url}: {e}")
        return False


def query_uncensored_persona(
    messages: List[Dict[str, str]],
    custom_options: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
    override_model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Dispatches a chat completion request through the cluster priority chain.
    Features Dual-Engine Intent Routing:
    - Standard/Roleplay/Megan Dialog -> Dispatched to configured model (e.g. DarkIdol, Rogue-Creative)
    - Math, Logic, Code, Tables -> Dispatched to DeepSeek-R1:14b with immediate keep_alive: 0 slot flush.
    """
    options = {**DEFAULT_OPTIONS, **(custom_options or {})}
    start_time = time.time()
    last_error = None

    # Dual-Engine Intent Detection
    is_reasoning = is_reasoning_or_math_prompt(messages)
    chosen_model = None

    if is_reasoning:
        logger.info(f"🧠 [Dual-Engine Intent Router]: Logik/Mathe/Code erkannt! Route isoliert zu {REASONING_MODEL}")
        chosen_model = REASONING_MODEL
        # For reasoning model, avoid overriding with 16k context if it would OOM
        reasoning_options = dict(options)
        reasoning_options["num_ctx"] = min(options.get("num_ctx", 8192), 8192)
        targets = [{
            "hosts": LOCAL_OUTERHEAVEN_HOSTS,
            "model": REASONING_MODEL,
            "desc": f"Dual-Engine Reasoning - {REASONING_MODEL}"
        }]
    else:
        targets = list(ROUTING_TARGETS)
        if override_model and override_model.strip():
            m = override_model.strip()
            chosen_model = m
            targets.insert(0, {
                "hosts": LOCAL_OUTERHEAVEN_HOSTS,
                "model": m,
                "desc": f"Configured Model - {m}"
            })

    for target in targets:
        model_name = target["model"]
        desc = target["desc"]

        for base_url in target["hosts"]:
            api_endpoint = f"{base_url.rstrip('/')}/api/chat"
            effective_options = reasoning_options if is_reasoning else options
            payload = {
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": effective_options
            }

            try:
                logger.debug(f"Attempting inference via {desc} ({api_endpoint})...")
                resp = requests.post(api_endpoint, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("message", {}).get("content", "").strip()
                    if content:
                        elapsed = round(time.time() - start_time, 2)
                        logger.info(f"✔ Antwort erhalten von {desc} in {elapsed}s")
                        # If we used DeepSeek for reasoning, immediately flush slot to reclaim VRAM for Arc A770
                        if is_reasoning:
                            logger.info(f"🧹 [VRAM Guard]: Gebe DeepSeek Slot auf {base_url} sofort frei (keep_alive: 0)...")
                            flush_model_slot(REASONING_MODEL, base_url)
                        return {
                            "success": True,
                            "content": content,
                            "model_used": model_name,
                            "endpoint_used": api_endpoint,
                            "elapsed_seconds": elapsed
                        }
                elif resp.status_code == 404:
                    # Model not present on this host, try next target
                    logger.debug(f"Modell {model_name} auf {base_url} nicht gefunden (404).")
                    break
                else:
                    logger.warning(f"Ollama auf {base_url} meldet HTTP {resp.status_code}: {resp.text[:100]}")
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as conn_err:
                last_error = conn_err
                logger.debug(f"Verbindungsfehler zu {base_url}: {conn_err}")
                continue
            except Exception as ex:
                last_error = ex
                logger.warning(f"Unerwarteter Fehler bei {base_url}: {ex}")
                continue

    elapsed = round(time.time() - start_time, 2)
    logger.error(f"Alle Routing-Ziele fehlgeschlagen nach {elapsed}s: {last_error}")
    return {
        "success": False,
        "content": "Fehler: Die unzensierte Companion-Engine konnte im Cluster nicht erreicht werden.",
        "model_used": None,
        "endpoint_used": None,
        "elapsed_seconds": elapsed
    }


if __name__ == "__main__":
    test_msgs = [
        {"role": "system", "content": "Du bist Megan, eine loyale und verspielte Gefährtin."},
        {"role": "user", "content": "Hallo Megan! Wie geht es dir heute?"}
    ]
    res = query_uncensored_persona(test_msgs, timeout=15)
    print("Router Test Result:", res)
