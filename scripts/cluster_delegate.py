#!/usr/bin/env python3
"""
cluster_delegate.py - Multi-Node Workload Delegation Tool
Designed for Outer Heaven Cluster (outerheaven -> fox).

Logic:
1. Checks if Node 'fox' is currently rebooting (/tmp/fox_is_rebooting exists).
   If so, strictly prevents delegation to fox and executes locally on outerheaven.
2. Checks if ComfyUI on outerheaven (127.0.0.1:8188) is currently rendering
   (queue_running > 0 or queue_pending > 0) or VRAM is heavily occupied.
3. If busy and fox is online: Delegates text and auxiliary tasks to Node 'fox'.
4. If idle: Executes locally on outerheaven via Ollama (deepseek-r1:14b).
"""

import os
import sys
import json
import urllib.request
import urllib.error
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple

COMFYUI_URL = "http://127.0.0.1:8188"
LOCAL_OLLAMA_URL = "http://127.0.0.1:11434"
FOX_HOST = "fox"
FOX_MODEL = "hermes-3-3b-q4:latest"
LOCAL_MODEL = "deepseek-r1:14b"
FOX_REBOOT_FLAG = Path("/tmp/fox_is_rebooting")


def is_fox_rebooting() -> bool:
    """Checks if Fox has signaled an imminent shutdown or reboot."""
    return FOX_REBOOT_FLAG.exists()


def check_comfyui_status() -> Tuple[bool, str]:
    """
    Checks if ComfyUI is rendering or VRAM is exhausted.
    Returns (is_busy: bool, reason: str).
    """
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/queue", headers={"User-Agent": "OuterHeaven-Delegator"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            running = len(data.get("queue_running", []))
            pending = len(data.get("queue_pending", []))
            if running > 0 or pending > 0:
                return True, f"ComfyUI rendert aktiv ({running} aktiv, {pending} in Warteschlange)"
    except Exception:
        pass

    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats", headers={"User-Agent": "OuterHeaven-Delegator"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            devices = stats.get("devices", [])
            for dev in devices:
                total = dev.get("vram_total", 0)
                free = dev.get("vram_free", 0)
                if total > 0 and (free / total) < 0.10:
                    return True, f"VRAM fast vollständig belegt ({free // (1024*1024)} MB frei von {total // (1024*1024)} MB)"
    except Exception:
        pass

    return False, "ComfyUI ist im Leerlauf / VRAM ist frei"


def query_ollama_local(prompt: str, system_prompt: str = "") -> str:
    """Executes prompt on local outerheaven Ollama instance."""
    url = f"{LOCAL_OLLAMA_URL}/api/generate"
    payload = {
        "model": LOCAL_MODEL,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "num_ctx": 8192
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("response", "").strip()


def query_ollama_fox(prompt: str, system_prompt: str = "") -> str:
    """Delegates prompt to Node fox via SSH and Ollama API."""
    payload = json.dumps({
        "model": FOX_MODEL,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "num_ctx": 4096
        }
    })
    cmd = [
        "ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
        FOX_HOST,
        f"curl -s http://127.0.0.1:11434/api/generate -d '{payload}'"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"SSH Delegation an fox fehlgeschlagen: {proc.stderr}")
    res = json.loads(proc.stdout)
    return res.get("response", "").strip()


def delegate_task(prompt: str, system_prompt: str = "") -> Dict[str, Any]:
    """
    Intelligently routes task either to local outerheaven or remote fox.
    Strictly avoids routing to fox when /tmp/fox_is_rebooting is active.
    """
    fox_rebooting = is_fox_rebooting()
    is_busy, reason = check_comfyui_status()

    if fox_rebooting:
        target_node = "outerheaven"
        reason = "Node Fox startet derzeit neu (Handover-Flag aktiv). Ausführung lokal auf Outer Heaven erzwungen."
        response = query_ollama_local(prompt, system_prompt)
    elif is_busy:
        target_node = "fox"
        try:
            response = query_ollama_fox(prompt, system_prompt)
        except Exception as e:
            target_node = "outerheaven"
            reason += f" (Fallback lokal, da Fox nicht antwortete: {e})"
            response = query_ollama_local(prompt, system_prompt)
    else:
        target_node = "outerheaven"
        response = query_ollama_local(prompt, system_prompt)

    return {
        "target_node": target_node,
        "reason": reason,
        "response": response,
        "fox_rebooting": fox_rebooting
    }


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Verwendung: cluster_delegate.py [status | run <prompt>]")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "status":
        busy, reason = check_comfyui_status()
        fox_reb = is_fox_rebooting()
        preferred = "outerheaven" if fox_reb else ("fox" if busy else "outerheaven")
        status = {
            "comfyui_busy": busy,
            "fox_rebooting": fox_reb,
            "reason": reason if not fox_reb else "Node Fox meldet Reboot/Shutdown Handover!",
            "preferred_node": preferred,
            "local_model": LOCAL_MODEL,
            "fox_model": FOX_MODEL
        }
        print(json.dumps(status, indent=2, ensure_ascii=False))

    elif cmd == "run":
        prompt = " ".join(sys.argv[2:]).strip()
        if not prompt:
            print("Fehler: Bitte einen Prompt angeben.")
            sys.exit(1)
        res = delegate_task(prompt)
        print(f"[{res['target_node'].upper()}] ({res['reason']})\n{res['response']}")


if __name__ == "__main__":
    main()
