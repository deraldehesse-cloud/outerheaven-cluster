#!/usr/bin/env python3
"""
audit_cluster_bot.py - Umfassendes Diagnose- und Audit-Werkzeug für den Companion-Bot & Cluster.
Prüft:
  1. Python-Syntax & Modul-Kompilierung aller Projektdateien
  2. Funktions-Signaturen & Schnittstellen-Konsistenz (ComfyUI Dispatch & Bot)
  3. Integrität von history.json, MEMORY.md, persona.yaml & bot_config.json
  4. Status der Outer-Heaven-Dienste (Bot, ComfyUI, Ollama, VRAM)
  5. Status des Fox-Knotens (Netzwerk, Ollama, Hermes-Gateway, Zombie-Dienste)
"""

import os
import sys
import json
import socket
import py_compile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Tuple, Dict, Any

# ANSI Colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR if (BASE_DIR / "persona.yaml").is_file() else (BASE_DIR.parent if (BASE_DIR.parent / "persona.yaml").is_file() else Path("/mnt/storage-beta/projekte/projekt-sexchat"))

# Import sanitizer if available
sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(BASE_DIR))
try:
    from sanitize_history import is_corrupted_text
except ImportError:
    def is_corrupted_text(text: str) -> Tuple[bool, str]:
        return False, ""


def log_header(title: str):
    print(f"\n{BOLD}{CYAN}{'='*70}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*70}{RESET}")


def log_pass(msg: str):
    print(f"  [{GREEN}PASS{RESET}] {msg}")


def log_warn(msg: str):
    print(f"  [{YELLOW}WARN{RESET}] {msg}")


def log_fail(msg: str):
    print(f"  [{RED}FAIL{RESET}] {msg}")


def is_local_outerheaven() -> bool:
    hn = socket.gethostname().lower()
    return "outerheaven" in hn or hn == "oh"


def get_oh_host() -> str:
    if is_local_outerheaven():
        return "127.0.0.1"
    return "192.168.188.21"


def audit_python_syntax(project_path: Path) -> Tuple[int, int]:
    log_header("1. PYTHON-SYNTAX & KOMPILIERUNGS-AUDIT")
    passed = 0
    failed = 0
    py_files = sorted(list(project_path.glob("*.py")) + list((project_path / "assets").glob("*.py")))
    
    seen = set()
    for pf in py_files:
        if pf.name.startswith(".") or pf.name in seen:
            continue
        seen.add(pf.name)
        try:
            py_compile.compile(str(pf), doraise=True)
            log_pass(f"{pf.name} -> Syntax einwandfrei")
            passed += 1
        except py_compile.PyCompileError as e:
            log_fail(f"{pf.name} -> Syntaxfehler: {e.msg}")
            failed += 1
        except Exception as e:
            log_fail(f"{pf.name} -> Fehler beim Prüfen: {e}")
            failed += 1
    return passed, failed


def audit_signatures(project_path: Path) -> bool:
    log_header("2. SCHNITTSTELLEN- & SIGNATUR-AUDIT")
    ok = True
    
    # Check dispatch_comfy.py signature
    dispatch_file = project_path / "dispatch_comfy.py"
    if not dispatch_file.is_file():
        dispatch_file = project_path / "assets" / "dispatch_comfy.py"
        
    if dispatch_file.is_file():
        content = dispatch_file.read_text(encoding="utf-8")
        if "user_custom_tags: Optional[str] = None" in content:
            log_pass("dispatch_comfy.py: 'user_custom_tags' in dispatch_render_sync/async deklariert")
        else:
            log_fail("dispatch_comfy.py: 'user_custom_tags' fehlt in den Funktionsparametern!")
            ok = False

        if "def load_visual_preferences_from_memory" in content:
            log_pass("dispatch_comfy.py: 'load_visual_preferences_from_memory' implementiert")
        else:
            log_warn("dispatch_comfy.py: 'load_visual_preferences_from_memory' nicht gefunden")
            ok = False
    else:
        log_fail("dispatch_comfy.py wurde nicht gefunden!")
        ok = False

    # Check telegram_bot.py calls
    bot_file = project_path / "assets" / "telegram_bot.py"
    if not bot_file.is_file():
        bot_file = project_path / "telegram_bot.py"
        
    if bot_file.is_file():
        content = bot_file.read_text(encoding="utf-8")
        if "dispatch_render_async" in content and "user_custom_tags=user_custom_tags" in content:
            log_pass("telegram_bot.py: Übergibt 'user_custom_tags' sauber an dispatch_render_async")
        else:
            log_warn("telegram_bot.py: Aufruf von dispatch_render_async weicht ab")

        if "update_visual_preferences_in_memory" in content:
            log_pass("telegram_bot.py: 'update_visual_preferences_in_memory' für Kink-Erkennung vorhanden")
        else:
            log_warn("telegram_bot.py: Kink-Erkennungsroutine 'update_visual_preferences_in_memory' fehlt")
            ok = False
    else:
        log_fail("telegram_bot.py wurde nicht gefunden!")
        ok = False

    return ok


def audit_data_files(project_path: Path) -> bool:
    log_header("3. INTEGRITÄT DER DATENDATEIEN & GEDÄCHTNIS")
    all_ok = True

    # 1. history.json
    hist_file = project_path / "history.json"
    if hist_file.is_file():
        try:
            raw = json.loads(hist_file.read_text(encoding="utf-8"))
            messages = raw.get("messages", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            if isinstance(raw, (dict, list)):
                count = len(messages)
                log_pass(f"history.json: Valides JSON-Format ({count} Nachrichten im Verlauf)")
                corrupt_entries = []
                for idx, entry in enumerate(messages):
                    content = entry.get("content", "")
                    is_bad, reason = is_corrupted_text(content)
                    if is_bad:
                        corrupt_entries.append(f"#{idx}: {reason}")
                if corrupt_entries:
                    log_warn(f"history.json enthält {len(corrupt_entries)} potenziell korrupte Einträge: {', '.join(corrupt_entries[:3])}")
                else:
                    log_pass("history.json: Keine isolierten Klammern, BPE-Glitches oder Wortsalat entdeckt")
            else:
                log_fail("history.json: Weder Array noch Nachrichten-Objekt!")
                all_ok = False
        except Exception as e:
            log_fail(f"history.json: JSON-Syntax ungültig: {e}")
            all_ok = False
    else:
        log_warn("history.json existiert nicht (wird beim ersten Chat automatisch erstellt)")

    # 2. MEMORY.md
    mem_file = project_path / "MEMORY.md"
    if mem_file.is_file():
        content = mem_file.read_text(encoding="utf-8")
        headers = [
            ("## 👤 Benutzerprofil", "Benutzerprofil von Big Boss"),
            ("## 💡 Vorlieben", "Hobbys & Themen"),
            ("## 📸 Visuelle Vorlieben", "Visuelle Vorlieben & Kinks"),
            ("## 💖 Feedback", "Letzte Reaktionen von Big Boss")
        ]
        for tag, desc in headers:
            if tag in content:
                log_pass(f"MEMORY.md: Sektion '{desc}' vorhanden")
            else:
                log_warn(f"MEMORY.md: Sektion '{desc}' fehlt!")
    else:
        log_fail("MEMORY.md nicht gefunden!")
        all_ok = False

    # 3. persona.yaml
    persona_file = project_path / "persona.yaml"
    if persona_file.is_file():
        log_pass("persona.yaml: Datei vorhanden")
    else:
        log_fail("persona.yaml fehlt!")
        all_ok = False

    # 4. bot_config.json
    cfg_file = project_path / "bot_config.json"
    if cfg_file.is_file():
        try:
            cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            model = cfg.get("active_model", "unbekannt")
            ctx = cfg.get("context_length", 0)
            log_pass(f"bot_config.json: Valide Konfiguration (Modell: {model}, Kontext: {ctx})")
        except Exception as e:
            log_fail(f"bot_config.json: JSON-Fehler: {e}")
            all_ok = False
    else:
        log_warn("bot_config.json fehlt")

    return all_ok


def audit_local_services() -> None:
    log_header("4. HARDWARE- & DIENSTE-DIAGNOSE (OUTER HEAVEN)")
    oh_host = get_oh_host()

    # 1. bigboss-haven-bot.service
    if is_local_outerheaven():
        res = subprocess.run(["systemctl", "--user", "is-active", "bigboss-haven-bot.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        res = subprocess.run(["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", "outerheaven", "systemctl --user is-active bigboss-haven-bot.service"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    status = res.stdout.strip()
    if status == "active":
        log_pass("Systemd-Dienst 'bigboss-haven-bot.service': [AKTIV / RUNNING]")
    else:
        log_warn(f"Systemd-Dienst 'bigboss-haven-bot.service': [{status.upper() or 'INAKTIV'}]")

    # 2. ComfyUI Endpoint
    try:
        req = urllib.request.Request(f"http://{oh_host}:8188/queue", headers={"User-Agent": "ClusterAudit/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                q = json.loads(resp.read().decode("utf-8"))
                running = len(q.get("queue_running", []))
                pending = len(q.get("queue_pending", []))
                log_pass(f"ComfyUI auf Outer Heaven ({oh_host}:8188): [ONLINE] (Queue: {running} aktiv, {pending} wartend)")
    except Exception as e:
        log_fail(f"ComfyUI auf Outer Heaven ({oh_host}:8188) nicht erreichbar: {e}")

    # 3. Ollama Endpoint & VRAM Status
    try:
        with urllib.request.urlopen(f"http://{oh_host}:11434/api/tags", timeout=3) as resp:
            if resp.status == 200:
                models = json.loads(resp.read().decode("utf-8")).get("models", [])
                log_pass(f"Ollama auf Outer Heaven ({oh_host}:11434): [ONLINE] ({len(models)} Modelle registriert)")
        with urllib.request.urlopen(f"http://{oh_host}:11434/api/ps", timeout=3) as resp:
            if resp.status == 200:
                ps = json.loads(resp.read().decode("utf-8")).get("models", [])
                if ps:
                    for m in ps:
                        vram_mb = m.get("size_vram", 0) / (1024 * 1024)
                        log_pass(f"Ollama VRAM-Belegung: '{m.get('name')}' nutzt {vram_mb:.0f} MB")
                else:
                    log_pass("Ollama VRAM-Belegung: Aktuell kein Modell geladen (Headroom frei für ComfyUI)")
    except Exception as e:
        log_fail(f"Ollama auf Outer Heaven ({oh_host}:11434) nicht erreichbar: {e}")


def audit_fox_node() -> None:
    log_header("5. KNOTEN-DIAGNOSE (FOX NODE - 192.168.188.91)")

    # 1. Ping / SSH Check
    ssh_test = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", "fox", "echo ok"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if ssh_test.returncode == 0:
        log_pass("SSH-Verbindung zu Fox: [ONLINE]")

        # Ollama Check on Fox
        ollama_test = subprocess.run(
            ["ssh", "fox", "curl -s -m 3 http://127.0.0.1:11434/api/tags"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if "models" in ollama_test.stdout:
            log_pass("Ollama auf Fox (:11434): [ONLINE]")
        else:
            log_warn("Ollama auf Fox (:11434): Nicht erreichbar oder Antwort unerwartet")

        # Check failing comfyui service on Fox
        zombie_check = subprocess.run(
            ["ssh", "fox", "systemctl is-active comfyui.service 2>/dev/null || true"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        z_status = zombie_check.stdout.strip()
        if z_status in ["failed", "activating"]:
            log_fail(f"Fox Zombie-Dienst erkannt: 'comfyui.service' ist [{z_status.upper()}] (Crash-Loop alle 10s!)")
            print(f"      {YELLOW}→ Behebung durch Big Boss: ssh fox 'sudo systemctl disable --now comfyui.service'{RESET}")
        else:
            log_pass(f"Fox Systemd-Status 'comfyui.service': [{z_status or 'nicht aktiv'}]")

        # Hermes Gateway Check on Fox
        gw_check = subprocess.run(
            ["ssh", "fox", "pgrep -f hermes || true"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if gw_check.stdout.strip():
            log_pass("Hermes Gateway / Agent auf Fox: [AKTIV]")
        else:
            log_warn("Hermes Gateway auf Fox scheint nicht zu laufen")
    else:
        log_warn("Knoten 'fox' per SSH im BatchMode nicht direkt erreichbar")


def main():
    print(f"\n{BOLD}{GREEN}======================================================================{RESET}")
    print(f"{BOLD}{GREEN}   OUTER HEAVEN & FOX CLUSTER COMPREHENSIVE BOT AUDIT               {RESET}")
    print(f"{BOLD}{GREEN}======================================================================{RESET}")
    print(f"Projekt-Verzeichnis: {PROJECT_DIR}")

    passed_syntax, failed_syntax = audit_python_syntax(PROJECT_DIR)
    sig_ok = audit_signatures(PROJECT_DIR)
    data_ok = audit_data_files(PROJECT_DIR)
    audit_local_services()
    audit_fox_node()

    log_header("AUDIT ZUSAMMENFASSUNG")
    if failed_syntax == 0 and sig_ok and data_ok:
        print(f"\n{BOLD}{GREEN}✔ GESAMT-STATUS: ALLE AUDIT-CHECKS ERFOLGREICH BESTANDEN!{RESET}\n")
    else:
        print(f"\n{BOLD}{YELLOW}⚠ GESAMT-STATUS: EINIGE PUNKTE BEDÜRFEN DER BEACHTUNG (SIEHE LOG OBEN){RESET}\n")


if __name__ == "__main__":
    main()
