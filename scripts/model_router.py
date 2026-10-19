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
    {"hosts": LOCAL_OUTERHEAVEN_HOSTS, "model": "megan:latest", "desc": "Outer Heaven Local - Megan Uncensored (Lexi Llama 3)"},
    {"hosts": LOCAL_OUTERHEAVEN_HOSTS, "model": "lexi-llama-3-8b-uncensored_q4_k_m:latest", "desc": "Outer Heaven Local - Lexi Llama 3 8B Uncensored"},
    {"hosts": LOCAL_OUTERHEAVEN_HOSTS, "model": "darkidol-8b:latest", "desc": "Outer Heaven Local - DarkIdol 8B Uncensored"}
]

DEFAULT_OPTIONS = {
    "temperature": 0.95,
    "top_p": 0.90,
    "top_k": 40,
    "repeat_penalty": 1.18,
    "num_predict": 350,
    "num_ctx": 4096,
    "stop": ["<|im_end|>", "<|end_of_text|>", "<|eot_id|>", "<turn|>", "<|turn|>", "\nUser:", "\nBig Boss:", "Big Boss:", "\n[", "[Spontaneous"]
}


REFUSAL_TRIGGERS = [
    "cannot create", "cannot fulfill", "unable to create", "unable to generate",
    "cannot generate", "cannot provide", "must refuse", "illegal activities",
    "explicit content", "against my safety", "safety policy", "against the guidelines",
    "safety guidelines", "as an ai", "content policy", "cannot comply",
    "cannot assist with", "kann keine expliziten inhalte", "verstößt gegen",
    "sicherheitsrichtlinien", "nicht gestattet", "policy prohibits"
]

def is_refusal_response(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(trig in low for trig in REFUSAL_TRIGGERS)


def query_uncensored_persona(
    messages: List[Dict[str, str]],
    custom_options: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
    override_model: Optional[str] = None
) -> Dict[str, Any]:
    """
    Dispatches a chat completion request through the cluster priority chain.
    """
    options = {**DEFAULT_OPTIONS, **(custom_options or {})}
    start_time = time.time()
    last_error = None

    targets = list(ROUTING_TARGETS)
    if override_model and override_model.strip():
        m = override_model.strip()
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
            payload = {
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": options
            }

            try:
                logger.debug(f"Attempting inference via {desc} ({api_endpoint})...")
                resp = requests.post(api_endpoint, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("message", {}).get("content", "").strip()
                    if content:
                        if is_refusal_response(content):
                            logger.warning(f"Standard-Sicherheitsverweigerung abgefangen von {model_name} ({content[:80]}...). Wechsle zu nächstem Ziel.")
                            continue
                        elapsed = round(time.time() - start_time, 2)
                        logger.info(f"✔ Antwort erhalten von {desc} in {elapsed}s")
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
