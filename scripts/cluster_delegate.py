#!/usr/bin/env python3
"""
cluster_delegate.py - Bidirektionales Workload-Delegations- & Queue-Tool für den Outer Heaven Cluster.

Unterstützt:
1. Fox -> Outer Heaven (delegate_to_outerheaven):
   Fox prüft vor rechenintensiven Aufgaben (große Dateiabgleiche, schweres Reasoning),
   ob Outer Heaven (100.116.38.29) erreichbar ist und ComfyUI inaktiv ist (VRAM < 4 GB).
   - Frei: Auslagerung an Outer Heavens Ollama (deepseek-r1:14b), Empfang des Ergebnisses,
           nur lokale Dateioperationen auf Fox.
   - Belegt / Offline: Autonomes Weiterrechnen auf Fox mit lokalem Modell (hermes-3-3b-q4).
2. Outer Heaven -> Fox (delegate_to_fox):
   Outer Heaven lagert bei aktiven Renderings / VRAM-Mangel Textaufgaben an Fox aus,
   sofern Fox nicht gerade neu startet (/tmp/fox_is_rebooting).
3. Task-Handover-Queue (/mnt/storage-beta/cluster_tasks.json):
   Wenn Fox einen Task nicht stemmen kann und Outer Heaven belegt ist, reiht Fox
   den Job mit Status 'QUEUED_FOR_OH' in die persistente Queue ein.
   Outer Heaven arbeitet die Queue via Daemon/Worker ab, sobald ComfyUI frei ist.
"""

import os
import sys
import time
import json
import socket
import fcntl
import secrets
import urllib.request
import urllib.error
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

# --- Netzwerk- und Node-Konfiguration ---
OUTERHEAVEN_TS_IP = "100.116.38.29"
FOX_TS_IP = "100.93.184.87"

COMFYUI_PORT = 8188
OLLAMA_PORT = 11434
MISSION_CONTROL_PORT = 8080

OH_MODEL = "deepseek-r1:14b"
FOX_MODEL = "hermes-3-3b-q4:latest"

FOX_REBOOT_FLAG = Path("/tmp/fox_is_rebooting")

# Shared Cluster Task Queue
QUEUE_FILE = Path("/mnt/storage-beta/cluster_tasks.json")
LOCK_FILE = Path("/mnt/storage-beta/.cluster_tasks.lock")

# Mögliche Pfade für den Cluster API Key
KEY_PATHS = [
    Path("/home/fox/.hermes/cluster_api.key"),
    Path("/mnt/storage-alpha/secrets/cluster_api.key"),
    Path("/home/outerheaven/.hermes/cluster_api.key"),
    Path(os.path.expanduser("~/.hermes/cluster_api.key"))
]


def detect_current_node() -> str:
    """Ermittelt den aktuellen Hostnamen / Knoten (fox, outerheaven oder bigboss)."""
    hostname = socket.gethostname().lower()
    if "fox" in hostname:
        return "fox"
    elif "outerheaven" in hostname or "z620" in hostname:
        return "outerheaven"
    return "bigboss"


def get_cluster_api_key() -> str:
    """Liest den Cluster API Key aus Umgebungsvariablen oder Key-Dateien."""
    env_key = os.environ.get("CLUSTER_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in KEY_PATHS:
        try:
            if path.is_file():
                key = path.read_text().strip()
                if key:
                    return key
        except Exception:
            pass
    return ""


def report_mission_control_status(
    task: str,
    state: str,
    delegated_to: str,
    origin_node: str,
    target_node: str,
    model: str,
    reason: str = ""
) -> bool:
    """
    Sendet ein Status-Update an das Mission Control Dashboard auf Outer Heaven (:8080).
    Zustände: 'running', 'completed', 'failed', 'queued', 'idle'
    """
    url = f"http://{OUTERHEAVEN_TS_IP}:{MISSION_CONTROL_PORT}/api/delegation/update"
    payload = {
        "task": task,
        "state": state,
        "delegated_to": delegated_to,
        "origin_node": origin_node,
        "target_node": target_node,
        "model": model,
        "reason": reason,
        "timestamp": time.time()
    }
    headers = {"Content-Type": "application/json"}
    api_key = get_cluster_api_key()
    if api_key:
        headers["X-Cluster-Key"] = api_key

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def is_outerheaven_reachable(timeout: float = 1.5) -> bool:
    """Prüft, ob Outer Heaven im Netzwerk erreichbar ist (Port 8080, 8188, 11434 oder 22)."""
    for port in (COMFYUI_PORT, OLLAMA_PORT, MISSION_CONTROL_PORT, 22):
        try:
            with socket.create_connection((OUTERHEAVEN_TS_IP, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    return False


def is_fox_rebooting() -> bool:
    """Prüft, ob Fox einen anstehenden Reboot/Shutdown signalisiert hat."""
    return FOX_REBOOT_FLAG.exists()


def check_outerheaven_comfyui_and_vram() -> Tuple[bool, float, str]:
    """
    Prüft den ComfyUI- und VRAM-Status auf Outer Heaven.
    Kriterien für 'inaktiv / frei':
    - ComfyUI-Warteschlange ist leer (queue_running == 0 und queue_pending == 0)
    - Belegter VRAM ist kleiner als 4.0 GB (4096 MB)
    - Wenn ComfyUI-Dienst gestoppt ist: gilt als inaktiv mit 0 GB VRAM.

    Rückgabe:
      (is_busy: bool, vram_used_gb: float, reason: str)
    """
    if not is_outerheaven_reachable(timeout=1.5):
        return True, 0.0, "Outer Heaven ist offline oder schläft"

    # 1. ComfyUI Queue prüfen
    queue_url = f"http://{OUTERHEAVEN_TS_IP}:{COMFYUI_PORT}/queue"
    queue_data = None
    try:
        req = urllib.request.Request(queue_url, headers={"User-Agent": "OuterHeaven-Cluster-Delegator"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            queue_data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    if queue_data:
        running = len(queue_data.get("queue_running", []))
        pending = len(queue_data.get("queue_pending", []))
        if running > 0 or pending > 0:
            return True, 0.0, f"ComfyUI rendert aktiv ({running} aktiv, {pending} in Warteschlange)"

    # 2. VRAM über ComfyUI system_stats prüfen
    stats_url = f"http://{OUTERHEAVEN_TS_IP}:{COMFYUI_PORT}/system_stats"
    try:
        req = urllib.request.Request(stats_url, headers={"User-Agent": "OuterHeaven-Cluster-Delegator"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            stats = json.loads(resp.read().decode("utf-8"))
            devices = stats.get("devices", [])
            for dev in devices:
                total_bytes = dev.get("vram_total", 0)
                free_bytes = dev.get("vram_free", 0)
                if total_bytes > 0:
                    used_bytes = max(0, total_bytes - free_bytes)
                    used_gb = round(used_bytes / (1024 ** 3), 2)
                    if used_gb >= 4.0:
                        return True, used_gb, f"ComfyUI belegt {used_gb} GB VRAM (Schwellenwert >= 4.0 GB)"
                    return False, used_gb, f"ComfyUI inaktiv, VRAM frei ({used_gb} GB belegt < 4.0 GB)"
    except Exception:
        pass

    # 3. Falls ComfyUI nicht antwortet, prüfe Ollama auf Outer Heaven
    try:
        ps_url = f"http://{OUTERHEAVEN_TS_IP}:{OLLAMA_PORT}/api/ps"
        req = urllib.request.Request(ps_url, headers={"User-Agent": "OuterHeaven-Cluster-Delegator"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            ps_data = json.loads(resp.read().decode("utf-8"))
            models = ps_data.get("models", [])
            vram_used = sum(m.get("size_vram", 0) for m in models)
            used_gb = round(vram_used / (1024 ** 3), 2)
            if used_gb >= 4.0:
                return True, used_gb, f"Outer Heaven VRAM durch Ollama belegt ({used_gb} GB >= 4.0 GB)"
            return False, used_gb, f"Outer Heaven frei (ComfyUI aus, Ollama {used_gb} GB VRAM)"
    except Exception:
        pass

    return False, 0.0, "ComfyUI ist nicht aktiv (0 GB VRAM genutzt)"


def query_ollama(
    host_ip: str,
    model: str,
    prompt: str,
    system_prompt: str = "",
    num_ctx: int = 8192,
    timeout: int = 180
) -> str:
    """Sendet einen Prompt an eine Ollama-Instanz und liefert die generierte Antwort."""
    url = f"http://{host_ip}:{OLLAMA_PORT}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "temperature": 0.6
        }
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("response", "").strip()


# ==============================================================================
# Task-Handover-Queue (/mnt/storage-beta/cluster_tasks.json)
# ==============================================================================

def load_cluster_tasks() -> List[Dict[str, Any]]:
    """Liest alle Tasks aus /mnt/storage-beta/cluster_tasks.json threadsicher ein."""
    if not QUEUE_FILE.is_file():
        return []
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("tasks", [])
            elif isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def save_cluster_tasks(tasks: List[Dict[str, Any]]) -> bool:
    """Schreibt Tasks atomar und flock-gesichert nach /mnt/storage-beta/cluster_tasks.json."""
    tmp_file = Path(f"/mnt/storage-beta/cluster_tasks.json.{os.getpid()}_{secrets.token_hex(2)}.tmp")
    data = {
        "tasks": tasks,
        "last_updated": time.time(),
        "updated_by": detect_current_node()
    }
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCK_FILE, "w") as lock_f:
            try:
                fcntl.flock(lock_f, fcntl.LOCK_EX)
            except (BlockingIOError, OSError):
                time.sleep(0.1)
                fcntl.flock(lock_f, fcntl.LOCK_EX)

            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_file, QUEUE_FILE)
            fcntl.flock(lock_f, fcntl.LOCK_UN)
        return True
    except Exception as e:
        if tmp_file.is_file():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        return False


def enqueue_task(
    name: str,
    prompt: str,
    task_type: str = "reasoning",
    command: str = ""
) -> Dict[str, Any]:
    """Reiht einen neuen Task mit Status 'QUEUED_FOR_OH' in die Handover-Queue ein."""
    task_id = f"task_{int(time.time())}_{secrets.token_hex(4)}"
    current_node = detect_current_node()
    new_task = {
        "id": task_id,
        "name": name,
        "type": task_type,
        "prompt": prompt,
        "command": command or "",
        "status": "QUEUED_FOR_OH",
        "origin": current_node,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "model": OH_MODEL,
        "result": None,
        "error": None
    }
    tasks = load_cluster_tasks()
    tasks.append(new_task)
    save_cluster_tasks(tasks)

    report_mission_control_status(
        task=f"QUEUED TASK: {name}",
        state="queued",
        delegated_to="OH",
        origin_node=current_node,
        target_node="outerheaven",
        model=OH_MODEL,
        reason=f"Task eingereiht in /mnt/storage-beta/cluster_tasks.json ({task_id})"
    )
    return new_task


def process_queue_once() -> Optional[Dict[str, Any]]:
    """
    Prüft auf Outer Heaven, ob Aufgaben mit Status 'QUEUED_FOR_OH' anstehen.
    Wird nur ausgeführt, wenn ComfyUI inaktiv ist (VRAM < 4.0 GB und queue == 0).
    """
    tasks = load_cluster_tasks()
    queued = [t for t in tasks if t.get("status") == "QUEUED_FOR_OH"]
    if not queued:
        return None

    # ComfyUI & VRAM prüfen
    is_busy, vram_used, reason = check_outerheaven_comfyui_and_vram()
    if is_busy or vram_used >= 4.0:
        return None  # Outer Heaven belegt -> warten

    target_task = queued[0]
    target_id = target_task.get("id")
    task_name = target_task.get("name", "Task")

    # Status auf WORKING_ON_TASK setzen
    for t in tasks:
        if t.get("id") == target_id:
            t["status"] = "WORKING_ON_TASK"
            t["started_at"] = time.time()
            break
    save_cluster_tasks(tasks)

    report_mission_control_status(
        task=f"WORKING ON FOX TASK: {task_name}",
        state="running",
        delegated_to="OH",
        origin_node="fox",
        target_node="outerheaven",
        model=OH_MODEL,
        reason=f"Outer Heaven arbeitet Task ab (ComfyUI VRAM {vram_used} GB < 4 GB)"
    )

    result_text = None
    error_text = None
    try:
        cmd = target_task.get("command", "").strip()
        if cmd:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
            if proc.returncode == 0:
                result_text = proc.stdout.strip()
            else:
                error_text = proc.stderr.strip() or f"Prozess mit Code {proc.returncode} beendet"
        else:
            prompt = target_task.get("prompt", "")
            result_text = query_ollama("127.0.0.1", OH_MODEL, prompt, num_ctx=8192, timeout=300)
    except Exception as e:
        error_text = str(e)

    # Status auf FINISHED / FAILED aktualisieren
    for t in tasks:
        if t.get("id") == target_id:
            t["status"] = "FINISHED" if not error_text else "FAILED"
            t["finished_at"] = time.time()
            t["result"] = result_text
            t["error"] = error_text
            break
    save_cluster_tasks(tasks)

    report_mission_control_status(
        task=f"FINISHED: {task_name}",
        state="completed" if not error_text else "failed",
        delegated_to="OH",
        origin_node="fox",
        target_node="outerheaven",
        model=OH_MODEL,
        reason="Abarbeitung erfolgreich abgeschlossen" if not error_text else f"Fehler: {error_text}"
    )
    return target_task


def run_queue_worker(interval: float = 3.0):
    """Worker-Daemon für Outer Heaven zur permanenten Abarbeitung der Queue."""
    print(f"[{detect_current_node().upper()}] Starte Cluster Queue Worker...")
    print(f"Überwache /mnt/storage-beta/cluster_tasks.json (Intervall: {interval}s)")
    while True:
        try:
            task = process_queue_once()
            if task:
                status = task.get("status")
                print(f"[WORKER] {status}: {task.get('name')} ({task.get('id')})")
        except Exception as e:
            print(f"[WORKER ERROR] {e}", file=sys.stderr)
        time.sleep(interval)


# ==============================================================================
# Live-Delegation (Synchron)
# ==============================================================================

def delegate_to_outerheaven(
    task: str,
    system_prompt: str = "",
    task_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Kernfunktion für Node Fox:
    Prüft vor rechenintensiven Aufgaben (große Dateiabgleiche, schweres Reasoning),
    ob Outer Heaven (100.116.38.29) erreichbar ist und ComfyUI inaktiv ist (VRAM < 4 GB).

    - Frei: Rechenaufgabe an Outer Heavens Ollama weiterleiten, Ergebnis empfangen.
            Lokale Dateioperationen werden auf Fox ausgeführt.
    - Belegt / Offline: Fox rechnet autonom mit seinem lokalen Modell (Hermes-3) weiter.
    """
    short_task = task_name or (task[:60] + "..." if len(task) > 60 else task)
    current_node = detect_current_node()

    is_busy, vram_used, reason = check_outerheaven_comfyui_and_vram()

    if not is_busy:
        target_node = "outerheaven"
        delegated_to = "OH"
        active_model = OH_MODEL
        report_reason = f"Outer Heaven frei ({reason})"

        report_mission_control_status(
            task=short_task,
            state="running",
            delegated_to="OH",
            origin_node=current_node,
            target_node="outerheaven",
            model=active_model,
            reason=report_reason
        )

        try:
            response = query_ollama(
                host_ip=OUTERHEAVEN_TS_IP,
                model=active_model,
                prompt=task,
                system_prompt=system_prompt,
                num_ctx=8192,
                timeout=180
            )
            success = True
        except Exception as e:
            report_reason = f"Fallback lokal auf Fox (Outer Heaven Inferenz-Fehler: {e})"
            target_node = "fox"
            delegated_to = "FOX"
            active_model = FOX_MODEL

            report_mission_control_status(
                task=short_task,
                state="running",
                delegated_to="FOX",
                origin_node=current_node,
                target_node="fox",
                model=active_model,
                reason=report_reason
            )

            try:
                response = query_ollama(
                    host_ip="127.0.0.1",
                    model=active_model,
                    prompt=task,
                    system_prompt=system_prompt,
                    num_ctx=4096,
                    timeout=120
                )
                success = True
            except Exception as local_err:
                response = f"Fehler bei Inferenz auf Fox: {local_err}"
                success = False
    else:
        target_node = "fox"
        delegated_to = "FOX"
        active_model = FOX_MODEL
        report_reason = f"Autonome Ausführung auf Fox ({reason})"

        report_mission_control_status(
            task=short_task,
            state="running",
            delegated_to="FOX",
            origin_node=current_node,
            target_node="fox",
            model=active_model,
            reason=report_reason
        )

        try:
            response = query_ollama(
                host_ip="127.0.0.1",
                model=active_model,
                prompt=task,
                system_prompt=system_prompt,
                num_ctx=4096,
                timeout=120
            )
            success = True
        except Exception as local_err:
            response = f"Fehler bei lokaler Inferenz auf Fox: {local_err}"
            success = False

    report_mission_control_status(
        task=short_task,
        state="completed" if success else "failed",
        delegated_to=delegated_to,
        origin_node=current_node,
        target_node=target_node,
        model=active_model,
        reason=report_reason
    )

    return {
        "success": success,
        "delegated_to": delegated_to,
        "node": target_node,
        "model": active_model,
        "reason": report_reason,
        "response": response,
        "task": short_task
    }


def delegate_to_fox(
    task: str,
    system_prompt: str = "",
    task_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Richtung Outer Heaven -> Fox:
    Wird auf Outer Heaven genutzt, um Tasks bei ComfyUI-Volllast an Fox auszulagern.
    """
    short_task = task_name or (task[:60] + "..." if len(task) > 60 else task)
    current_node = detect_current_node()

    if is_fox_rebooting():
        target_node = "outerheaven"
        delegated_to = "OH"
        active_model = OH_MODEL
        reason = "Fox startet neu (/tmp/fox_is_rebooting aktiv). Lokale Berechnung auf Outer Heaven."
        try:
            response = query_ollama("127.0.0.1", active_model, task, system_prompt, num_ctx=8192)
            success = True
        except Exception as e:
            response = f"Fehler bei Inferenz: {e}"
            success = False
    else:
        target_node = "fox"
        delegated_to = "FOX"
        active_model = FOX_MODEL
        reason = "Auslagerung an Node Fox (ComfyUI aktiv auf Outer Heaven)"

        report_mission_control_status(
            task=short_task,
            state="running",
            delegated_to="FOX",
            origin_node=current_node,
            target_node="fox",
            model=active_model,
            reason=reason
        )

        try:
            response = query_ollama(FOX_TS_IP, active_model, task, system_prompt, num_ctx=4096, timeout=120)
            success = True
        except Exception as e:
            target_node = "outerheaven"
            delegated_to = "OH"
            active_model = OH_MODEL
            reason += f" (Fallback lokal auf Outer Heaven: {e})"
            try:
                response = query_ollama("127.0.0.1", active_model, task, system_prompt, num_ctx=8192)
                success = True
            except Exception as e2:
                response = f"Fehler bei Inferenz: {e2}"
                success = False

    report_mission_control_status(
        task=short_task,
        state="completed" if success else "failed",
        delegated_to=delegated_to,
        origin_node=current_node,
        target_node=target_node,
        model=active_model,
        reason=reason
    )

    return {
        "success": success,
        "delegated_to": delegated_to,
        "node": target_node,
        "model": active_model,
        "reason": reason,
        "response": response,
        "task": short_task
    }


def delegate_task(task: str, system_prompt: str = "") -> Dict[str, Any]:
    """Automatische bidirektionale Delegations-Erkennung."""
    node = detect_current_node()
    if node == "fox":
        return delegate_to_outerheaven(task, system_prompt=system_prompt)
    elif node == "outerheaven":
        is_busy, _, _ = check_outerheaven_comfyui_and_vram()
        if is_busy:
            return delegate_to_fox(task, system_prompt=system_prompt)
        else:
            return {
                "success": True,
                "delegated_to": "OH",
                "node": "outerheaven",
                "model": OH_MODEL,
                "reason": "Lokal auf Outer Heaven ausgeführt (ComfyUI frei)",
                "response": query_ollama("127.0.0.1", OH_MODEL, task, system_prompt, num_ctx=8192),
                "task": task[:60]
            }
    else:
        is_busy, _, _ = check_outerheaven_comfyui_and_vram()
        if not is_busy and is_outerheaven_reachable():
            return delegate_to_outerheaven(task, system_prompt=system_prompt)
        else:
            return delegate_to_fox(task, system_prompt=system_prompt)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Verwendung: cluster_delegate.py <Befehl> [Optionen]")
        print("Befehle:")
        print("  status                Zeigt Cluster- & Delegations-Status im JSON-Format")
        print("  delegate-oh <prompt>  Lagert Task an Outer Heaven aus (Fallback: Fox)")
        print("  delegate-fox <prompt> Lagert Task an Fox aus (Fallback: Outer Heaven)")
        print("  run <prompt>          Automatische bidirektionale Lastverteilung")
        print("  queue <name> <prompt> Reiht Job in /mnt/storage-beta/cluster_tasks.json ein")
        print("  queue-list            Listet alle Jobs der Handover-Queue auf")
        print("  queue-worker          Startet den Queue-Worker-Daemon auf Outer Heaven")
        print("  queue-process         Führt genau einen anstehenden Queue-Task aus")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        current_node = detect_current_node()
        oh_online = is_outerheaven_reachable(timeout=1.5)
        busy, vram_used, reason = check_outerheaven_comfyui_and_vram()
        fox_reb = is_fox_rebooting()
        tasks = load_cluster_tasks()
        queued_count = sum(1 for t in tasks if t.get("status") == "QUEUED_FOR_OH")
        active_count = sum(1 for t in tasks if t.get("status") == "WORKING_ON_TASK")

        if current_node == "fox":
            recommended = "outerheaven (DeepSeek-R1)" if (not busy and oh_online) else "fox (Hermes-3B)"
        else:
            recommended = "fox (Hermes-3B)" if (busy and not fox_reb) else "outerheaven (DeepSeek-R1)"

        status = {
            "current_node": current_node,
            "outerheaven": {
                "ip": OUTERHEAVEN_TS_IP,
                "online": oh_online,
                "comfyui_busy": busy,
                "vram_used_gb": vram_used,
                "reason": reason,
                "model": OH_MODEL
            },
            "fox": {
                "ip": FOX_TS_IP,
                "rebooting": fox_reb,
                "model": FOX_MODEL
            },
            "queue": {
                "queued_count": queued_count,
                "active_count": active_count,
                "total_tasks": len(tasks)
            },
            "recommended_compute_node": recommended
        }
        print(json.dumps(status, indent=2, ensure_ascii=False))

    elif cmd in ("delegate-oh", "delegate_to_outerheaven"):
        prompt = " ".join(sys.argv[2:]).strip()
        if not prompt:
            print("Fehler: Bitte einen Prompt / Task angeben.", file=sys.stderr)
            sys.exit(1)
        res = delegate_to_outerheaven(prompt)
        prefix = f"[{res['delegated_to']}] ({res['node']} // {res['model']} - {res['reason']})"
        print(f"{prefix}\n\n{res['response']}")

    elif cmd in ("delegate-fox", "delegate_to_fox"):
        prompt = " ".join(sys.argv[2:]).strip()
        if not prompt:
            print("Fehler: Bitte einen Prompt / Task angeben.", file=sys.stderr)
            sys.exit(1)
        res = delegate_to_fox(prompt)
        prefix = f"[{res['delegated_to']}] ({res['node']} // {res['model']} - {res['reason']})"
        print(f"{prefix}\n\n{res['response']}")

    elif cmd == "run":
        prompt = " ".join(sys.argv[2:]).strip()
        if not prompt:
            print("Fehler: Bitte einen Prompt / Task angeben.", file=sys.stderr)
            sys.exit(1)
        res = delegate_task(prompt)
        prefix = f"[{res['delegated_to']}] ({res['node']} // {res['model']} - {res['reason']})"
        print(f"{prefix}\n\n{res['response']}")

    elif cmd == "queue":
        if len(sys.argv) < 4:
            print("Verwendung: cluster_delegate.py queue <Task-Name> <Prompt/Anweisung>", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[2].strip()
        prompt = " ".join(sys.argv[3:]).strip()
        task = enqueue_task(name, prompt)
        print(f"[QUEUED] Job '{name}' mit ID {task['id']} eingereiht.")

    elif cmd == "queue-list":
        tasks = load_cluster_tasks()
        print(json.dumps(tasks, indent=2, ensure_ascii=False))

    elif cmd == "queue-worker":
        run_queue_worker()

    elif cmd == "queue-process":
        task = process_queue_once()
        if task:
            print(f"[PROCESSED] {task.get('name')} -> {task.get('status')}")
        else:
            print("[IDLE] Keine Aufgaben anstehend oder Outer Heaven belegt.")

    else:
        print(f"Unbekannter Befehl: {cmd}. Nutze --help für Hilfe.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
