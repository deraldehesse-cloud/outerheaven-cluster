import os
import sys
import json
import re
import requests
import subprocess
import concurrent.futures
import time
from datetime import datetime, timezone
from typing import Optional, List, Any, Union, Dict
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
import threading

try:
    import hermes_email_handler
except ImportError:
    hermes_email_handler = None

try:
    import fints_connector
except ImportError:
    fints_connector = None

try:
    import hermes_reminder
except ImportError:
    hermes_reminder = None

try:
    import hermes_email_ingest
except ImportError:
    hermes_email_ingest = None

app = FastAPI(title="Hermes Agent Kontrollzentrum")
PORT = int(os.getenv("PORT", "8081"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")

# Pfade zur persistenten Konfiguration
SKILLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_registry.json")
HERMES_SKILLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hermes_skills.json")
NODES_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cluster_nodes.json")

# --- Cluster Node Konfiguration ---
DEFAULT_CLUSTER_NODES = [
    {
        "id": "outerheaven",
        "name": "Outer Heaven (Compute)",
        "host": "100.116.38.29",
        "tailscale_ip": "100.116.38.29",
        "ollama_urls": [
            "http://100.116.38.29:11434",
            "http://outerheaven:11434"
        ],
        "agent_urls": [
            "http://100.116.38.29:8000/"
        ],
        "description": "Heavy Compute & Lokale Ollama-Instanz"
    },
    {
        "id": "fox",
        "name": "Fox (Worker)",
        "host": "127.0.0.1",
        "tailscale_ip": "100.93.184.87",
        "ollama_urls": [
            "http://127.0.0.1:11434",
            "http://100.93.184.87:11434",
            "http://fox:11434"
        ],
        "agent_urls": [
            "http://127.0.0.1:8081/",
            "http://100.93.184.87:8081/",
            "http://127.0.0.1:5005/health"
        ],
        "description": "Always-On Standby-Worker & Hermes Agent Backend"
    }
]

def load_cluster_nodes() -> list:
    """Lädt Knoten aus cluster_nodes.json oder initialisiert mit DEFAULT_CLUSTER_NODES."""
    if os.path.exists(NODES_CONFIG_FILE):
        try:
            with open(NODES_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
        except Exception as e:
            print(f"Warnung: Konnte {NODES_CONFIG_FILE} nicht laden: {e}")
    save_cluster_nodes(DEFAULT_CLUSTER_NODES)
    return list(DEFAULT_CLUSTER_NODES)

def save_cluster_nodes(nodes: list):
    """Speichert Knoten dauerhaft in cluster_nodes.json."""
    try:
        with open(NODES_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(nodes, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Fehler beim Speichern von {NODES_CONFIG_FILE}: {e}")

def get_node_by_id(node_id: str) -> dict:
    nodes = load_cluster_nodes()
    for n in nodes:
        if n.get("id") == node_id:
            return n
    raise HTTPException(status_code=404, detail=f"Knoten '{node_id}' in Cluster-Registry nicht gefunden.")

def get_working_ollama_url(node: dict, timeout: float = 1.8) -> str:
    urls = node.get("ollama_urls") or [f"http://{node.get('host', '127.0.0.1')}:11434"]
    for u in urls:
        clean_u = u.rstrip("/")
        try:
            r = requests.get(f"{clean_u}/api/tags", timeout=timeout)
            if r.status_code == 200:
                return clean_u
        except Exception:
            continue
    return urls[0].rstrip("/")

def check_node_status(node: dict) -> dict:
    node_id = node.get("id")
    ollama_urls = node.get("ollama_urls") or [f"http://{node.get('host', '127.0.0.1')}:11434"]
    
    # 1. Ollama Online & /api/tags
    ollama_online = False
    models = []
    active_ollama_url = None
    
    for u in ollama_urls:
        clean_u = u.rstrip("/")
        try:
            r = requests.get(f"{clean_u}/api/tags", timeout=1.8)
            if r.status_code == 200:
                ollama_online = True
                active_ollama_url = clean_u
                models = r.json().get("models", [])
                break
        except Exception:
            continue
            
    if not active_ollama_url:
        active_ollama_url = ollama_urls[0].rstrip("/")

    # 2. VRAM Status (/api/ps)
    vram_status = {
        "active_model": None,
        "size_vram": 0,
        "size_vram_formatted": "0 GB VRAM",
        "loaded_models": []
    }
    if ollama_online and active_ollama_url:
        try:
            r_ps = requests.get(f"{active_ollama_url}/api/ps", timeout=1.5)
            if r_ps.status_code == 200:
                ps_models = r_ps.json().get("models", [])
                if ps_models:
                    first_m = ps_models[0]
                    vram_bytes = first_m.get("size_vram", 0)
                    vram_gb = round(vram_bytes / (1024 ** 3), 1)
                    vram_status = {
                        "active_model": first_m.get("name"),
                        "size_vram": vram_bytes,
                        "size_vram_formatted": f"{vram_gb} GB VRAM" if vram_bytes > 0 else "RAM",
                        "loaded_models": [m.get("name") for m in ps_models if m.get("name")]
                    }
        except Exception as e:
            print(f"Warnung bei /api/ps für {node_id}: {e}")

    # 3. Echter Agent-Status (Korrektur 1: Echter Dienst-Check statt Web-UI Self-Ping auf 8081)
    agent_online = False
    agent_info = "Offline"
    agent_urls = node.get("agent_urls", [])
    
    for au in agent_urls:
        try:
            r_ag = requests.get(au, timeout=1.5)
            if r_ag.status_code in (200, 204):
                agent_online = True
                if node_id == "fox":
                    agent_info = "Fox Research Agent (:5005)"
                else:
                    agent_info = "Mission Control / Cluster Service (:8000)"
                break
        except Exception:
            continue

    # Ergänzender lokaler Prozess-/Systemd-Check falls auf outerheaven
    if not agent_online and node_id == "outerheaven":
        try:
            res = subprocess.run(["systemctl", "--user", "is-active", "cluster-queue-worker.service"],
                                 capture_output=True, text=True, timeout=1.2)
            if res.stdout.strip() == "active":
                agent_online = True
                agent_info = "Cluster Queue Worker (Active)"
        except Exception:
            pass

    return {
        "id": node_id,
        "name": node.get("name", node_id),
        "host": node.get("host", ""),
        "tailscale_ip": node.get("tailscale_ip", ""),
        "description": node.get("description", ""),
        "ollama_online": ollama_online,
        "agent_online": agent_online,
        "agent_info": agent_info,
        "active_ollama_url": active_ollama_url,
        "vram_status": vram_status,
        "models": models
    }

# --- Datenmodelle ---
class ChatRequest(BaseModel):
    model: str
    prompt: str
    node: Optional[str] = "outerheaven"

class BuildRequest(BaseModel):
    name: str
    modelfile: str
    node: Optional[str] = "outerheaven"

class NodeCreateRequest(BaseModel):
    id: str
    name: str
    host: str
    tailscale_ip: Optional[str] = ""
    ollama_urls: Optional[List[str]] = None
    agent_urls: Optional[List[str]] = None
    description: Optional[str] = ""

class ModelTransferRequest(BaseModel):
    source_node: str
    target_node: str
    model_name: str

class SkillToggleRequest(BaseModel):
    skill_id: str
    enabled: bool

class SkillCreateRequest(BaseModel):
    name: str
    description: str
    parameters: Optional[str] = ""
    handler_type: Optional[str] = "Desktop-Notification"
    handler_target: Optional[str] = ""
    action_mode: Optional[str] = "draft"
    push_notify: Optional[bool] = True
    speaker_broadcast: Optional[bool] = False

class ReminderCreateRequest(BaseModel):
    task: str
    due_time: str
    push_notify: Optional[bool] = True
    speaker_broadcast: Optional[bool] = False

class EmailDraftUpdateRequest(BaseModel):
    recipient: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None

class EmailGenerateRequest(BaseModel):
    correspondent: str
    aktenzeichen: Optional[str] = ""
    betrag: Optional[Any] = None
    due_date: Optional[str] = None
    doc_summary: Optional[str] = ""
    doc_type: Optional[str] = "Rechnung"
    recipient_email: Optional[str] = None
    document_id: Optional[str] = None
    action_mode: Optional[str] = "draft"

class EmailReviewActionRequest(BaseModel):
    action: str  # "DELETE" or "SEND_TO_PAPERLESS"
    title: Optional[str] = None

# --- Standard / Initial-Skills ---
DEFAULT_SKILLS = [
    {
        "id": "buero_hermes",
        "name": "Büro Hermes / Paperless Advisor",
        "description": "Automatische Klassifikation, Verschlagwortung und Dokumentenanalyse via Paperless-ngx.",
        "category": "Office & Workflow",
        "enabled": True,
        "source": "local:OuterHeaven/buero_hermes.py",
        "parameters": ["dokument_typ", "tags", "korrespondenz_partner"]
    },
    {
        "id": "telegram_service",
        "name": "Telegram Dispatcher & Alarmierung",
        "description": "Bidirektionale Kommunikation via Telegram Bot, Status-Pushes und Benachrichtigungen.",
        "category": "Communication",
        "enabled": True,
        "source": "local:OuterHeaven/hermes_telegram_service.py",
        "parameters": ["chat_id", "nachricht", "dringlichkeit"]
    },
    {
        "id": "system_terminal",
        "name": "Cluster-Terminal Executor",
        "description": "Sichere Ausführung von Sandbox-Shellbefehlen, Diagnostik und Log-Analyse.",
        "category": "System Administration",
        "enabled": False,
        "source": "builtin:terminal_executor",
        "parameters": ["command", "working_dir"]
    },
    {
        "id": "lora_dataset_curation",
        "name": "LoRA Dataset Kuration",
        "description": "Filterung, Bereinigung und Konvertierung von Chat-Historien in JSONL-Datensätze für Fine-Tuning.",
        "category": "Machine Learning",
        "enabled": False,
        "source": "github:outerheaven/lora-curator",
        "parameters": ["dataset_path", "split_ratio"]
    },
    {
        "id": "web_search_research",
        "name": "Web Research & Fact Checking",
        "description": "Automatisierte Webrecherche über SearXNG mit Quellen-Synthese.",
        "category": "Research",
        "enabled": False,
        "source": "remote:skills/web_research.json",
        "parameters": ["search_query", "max_results"]
    }
]

def load_skills_registry() -> list:
    """Lädt Skills aus hermes_skills.json oder skills_registry.json oder initialisiert mit DEFAULT_SKILLS."""
    for path in [HERMES_SKILLS_FILE, SKILLS_FILE]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        return data
            except Exception as e:
                print(f"Warnung: Konnte {path} nicht laden: {e}")
    save_skills_registry(DEFAULT_SKILLS)
    return list(DEFAULT_SKILLS)

def save_skills_registry(skills: list):
    """Speichert Skills dauerhaft in skills_registry.json und hermes_skills.json."""
    for path in [SKILLS_FILE, HERMES_SKILLS_FILE]:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(skills, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Fehler beim Speichern von {path}: {e}")

def build_function_schema(name: str, description: str, raw_params: str) -> dict:
    """Wandelt Angaben in das Standard Function-Calling Schema (name, description, parameters) um."""
    clean_func_name = re.sub(r'[^a-zA-Z0-9_]', '_', name.strip().lower())
    param_list = [p.strip() for p in (raw_params or "").split(",") if p.strip()]
    
    properties = {}
    for p in param_list:
        clean_p = re.sub(r'[^a-zA-Z0-9_]', '_', p.lower())
        if clean_p:
            properties[clean_p] = {
                "type": "string",
                "description": f"Extrahierter Parameter '{clean_p}' für {name}."
            }
    
    return {
        "type": "function",
        "function": {
            "name": clean_func_name,
            "description": description.strip(),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties.keys())
            }
        }
    }

# --- HTML / Frontend (TUI Design) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>// HERMES AGENT [Outer Heaven Cluster]</title>
    <style>
        :root {
            --bg-dark: #090a0f;
            --bg-panel: #11141d;
            --text-main: #a9b1d6;
            --accent-blue: #7aa2f7;
            --accent-green: #9ece6a;
            --accent-red: #f7768e;
            --border: #292e42;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Fira Code', 'Consolas', monospace;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }
        /* Sidebar */
        #sidebar {
            width: 320px;
            background-color: var(--bg-panel);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            padding: 15px;
            overflow-y: auto;
        }
        .sidebar-section { margin-bottom: 22px; }
        .sidebar-section h3 {
            color: var(--accent-blue);
            font-size: 0.88em;
            text-transform: uppercase;
            margin-bottom: 10px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 5px;
            letter-spacing: 0.5px;
        }
        .btn {
            background: transparent;
            color: var(--text-main);
            border: 1px solid var(--border);
            padding: 8px 12px;
            width: 100%;
            text-align: left;
            cursor: pointer;
            font-family: inherit;
            margin-bottom: 5px;
            transition: all 0.2s;
            border-radius: 3px;
        }
        .btn:hover { border-color: var(--accent-blue); color: var(--accent-blue); }
        .btn.active { border-color: var(--accent-green); color: var(--accent-green); }
        
        /* Main Content */
        #main {
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            position: relative;
        }
        #header {
            padding: 15px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-panel);
        }
        #header h1 { font-size: 1.2em; color: var(--accent-blue); letter-spacing: 1px; }
        
        /* Modelfile View Panel (standardmäßig eingeklappt) */
        #modfile-view-panel {
            background: #0a0d14;
            border-bottom: 1px solid var(--border);
            padding: 10px 20px;
            display: none;
            flex-direction: column;
            max-height: 220px;
            transition: all 0.2s ease;
        }
        #modfile-view-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 6px;
        }
        #modfile-view-pre {
            margin: 0;
            padding: 10px;
            background: #050608;
            border: 1px solid var(--border);
            border-radius: 3px;
            color: var(--accent-green);
            font-size: 0.82em;
            overflow-y: auto;
            flex-grow: 1;
            white-space: pre-wrap;
            font-family: inherit;
            line-height: 1.35;
        }

        /* Chat Area */
        #chat-container {
            flex-grow: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        .msg { max-width: 80%; padding: 10px 15px; border-radius: 4px; line-height: 1.4; }
        .msg.user { align-self: flex-end; background-color: #1a1e2d; border: 1px solid var(--border); }
        .msg.bot { align-self: flex-start; border-left: 3px solid var(--accent-green); background-color: var(--bg-panel); }
        
        /* Input Area */
        #input-area {
            padding: 20px;
            border-top: 1px solid var(--border);
            background-color: var(--bg-panel);
            display: flex;
            gap: 10px;
        }
        #chat-input {
            flex-grow: 1;
            background: var(--bg-dark);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 10px;
            font-family: inherit;
            resize: none;
            height: 50px;
        }
        #chat-input:focus { outline: none; border-color: var(--accent-blue); }
        #send-btn { width: 100px; background: var(--border); text-align: center; }
        #send-btn:hover { background: var(--accent-blue); color: #000; }
        
        /* Modfile Editor Modal */
        #modfile-editor {
            display: none;
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: var(--bg-dark);
            z-index: 10;
            padding: 25px;
            flex-direction: column;
        }
        #modfile-textarea {
            flex-grow: 1;
            width: 100%;
            background: var(--bg-panel);
            color: var(--accent-green);
            border: 1px solid var(--border);
            font-family: inherit;
            padding: 15px;
            margin: 12px 0;
            resize: none;
            font-size: 0.9em;
            line-height: 1.4;
        }
        .editor-actions { display: flex; gap: 10px; }

        /* Skills Modal */
        #skills-modal {
            display: none;
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(9, 10, 15, 0.93);
            backdrop-filter: blur(5px);
            z-index: 20;
            padding: 30px;
            flex-direction: column;
            justify-content: center;
            align-items: center;
        }
        #skills-modal-container {
            width: 100%;
            max-width: 850px;
            background: var(--bg-panel);
            border: 1px solid var(--accent-blue);
            border-radius: 4px;
            padding: 25px;
            display: flex;
            flex-direction: column;
            max-height: 85vh;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
        }
        #skills-modal-list {
            flex-grow: 1;
            overflow-y: auto;
            margin: 15px 0;
            padding-right: 5px;
        }
        .skill-item {
            background: var(--bg-dark);
            border: 1px solid var(--border);
            padding: 12px 15px;
            margin-bottom: 10px;
            border-radius: 4px;
            display: flex;
            gap: 12px;
            align-items: flex-start;
            transition: border-color 0.2s;
        }
        .skill-item:hover { border-color: var(--accent-blue); }
        .skill-badge {
            font-size: 0.75em;
            padding: 2px 7px;
            border-radius: 3px;
            border: 1px solid var(--border);
            text-transform: uppercase;
        }
        
        pre { background: #000; padding: 10px; overflow-x: auto; margin-top: 10px; border: 1px solid #333; }
        code { color: var(--accent-green); }

        /* Modal Overlays & Popups (Standardmäßig eingeklappt, Overlay auf Knopfdruck) */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(9, 10, 15, 0.88);
            backdrop-filter: blur(6px);
            z-index: 100;
            padding: 20px;
            justify-content: center;
            align-items: center;
        }
        .modal-overlay.active {
            display: flex;
        }
        .modal-content {
            background: var(--bg-panel);
            border: 1px solid var(--accent-blue);
            border-radius: 5px;
            padding: 24px;
            box-shadow: 0 10px 35px rgba(0,0,0,0.85);
            max-height: 90vh;
            overflow-y: auto;
            position: relative;
            width: 100%;
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 12px;
            margin-bottom: 16px;
        }
        .modal-header h3 {
            color: var(--accent-blue);
            font-size: 1.1em;
            letter-spacing: 0.5px;
        }
        .btn-close {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--accent-red);
            font-size: 1em;
            padding: 2px 8px;
            cursor: pointer;
            border-radius: 3px;
            transition: all 0.2s;
        }
        .btn-close:hover {
            border-color: var(--accent-red);
            background: rgba(247, 118, 142, 0.15);
        }

        /* Dropdown Menü (Kopfzeile) */
        .dropdown {
            position: relative;
            display: inline-block;
        }
        .dropdown-menu {
            display: none;
            position: absolute;
            top: calc(100% + 8px);
            right: 0;
            background: var(--bg-panel);
            border: 1px solid var(--accent-blue);
            border-radius: 4px;
            min-width: 290px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.8);
            z-index: 60;
            padding: 6px 0;
        }
        .dropdown-menu.show {
            display: block;
        }
        .dropdown-item {
            width: 100%;
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 16px;
            background: transparent;
            border: none;
            color: var(--text-main);
            text-align: left;
            font-family: inherit;
            font-size: 0.85em;
            cursor: pointer;
            transition: background 0.15s, color 0.15s;
        }
        .dropdown-item:hover {
            background: var(--bg-dark);
            color: var(--accent-blue);
        }
        .dropdown-divider {
            height: 1px;
            background: var(--border);
            margin: 5px 0;
        }

        /* Mobile Hamburger & Backdrop */
        .mobile-hamburger-btn {
            display: none;
        }
        #sidebar-backdrop {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(4px);
            z-index: 85;
            transition: opacity 0.2s ease;
        }
        #sidebar-backdrop.active {
            display: block !important;
        }
        .sidebar-mobile-header {
            display: none;
        }
        .desktop-only {
            display: inline;
        }

        /* Mobile Responsive Layout (@media (max-width: 768px)) */
        @media (max-width: 768px) {
            .desktop-only {
                display: none !important;
            }
            html, body {
                font-size: 16px;
                width: 100%;
                height: 100%;
                overflow: hidden;
                font-size: 16px;
            }
            input, textarea, select {
                font-size: 16px !important;
            }
            .mobile-hamburger-btn {
                display: inline-flex !important;
                align-items: center;
                justify-content: center;
                width: 44px;
                height: 44px;
                min-width: 44px;
                min-height: 44px;
                background: var(--bg-panel);
                border: 1px solid var(--accent-blue);
                color: var(--accent-blue);
                border-radius: 4px;
                font-size: 24px;
                line-height: 1;
                cursor: pointer;
                padding: 0;
                flex-shrink: 0;
            }
            .mobile-hamburger-btn:active {
                background: rgba(122, 162, 247, 0.25);
            }
            .sidebar-mobile-header {
                display: flex !important;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 18px;
                padding-bottom: 10px;
                border-bottom: 1px solid var(--border);
            }

            /* Slide-Over Drawer */
            #sidebar {
                position: fixed;
                top: 0;
                left: 0;
                width: 85vw;
                max-width: 330px;
                height: 100vh;
                height: 100dvh;
                background: #0d1117;
                z-index: 95;
                transform: translateX(-100%);
                transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 6px 0 25px rgba(0,0,0,0.85);
                border-right: 1px solid var(--border);
                padding: 16px;
            }
            #sidebar.open {
                transform: translateX(0) !important;
                display: flex !important;
            }

            /* Main Content & Full Chat Width */
            #main {
                width: 100%;
                min-width: 0;
                height: 100vh;
                height: 100dvh;
            }
            #header {
                padding: 10px 14px;
                gap: 10px;
            }
            #header h1 {
                font-size: 1.05rem;
            }
            #header-info-badge {
                display: none !important;
            }
            #cluster-menu-btn {
                padding: 6px 10px !important;
                font-size: 0.75rem !important;
                white-space: nowrap !important;
            }
            
            /* Chat Area */
            #chat-container {
                padding: 14px;
                gap: 12px;
                flex-grow: 1;
            }
            .msg {
                max-width: 90%;
                font-size: 16px;
                line-height: 1.45;
                padding: 12px 14px;
            }

            /* Input Area */
            #input-area {
                padding: 12px 14px;
                gap: 10px;
                align-items: center;
            }
            #chat-input {
                font-size: 16px; /* Prevents auto zoom in mobile WebViews */
                padding: 12px 16px;
                height: 52px;
                min-height: 52px;
                border-radius: 4px;
                line-height: 1.35;
            }
            #send-btn {
                width: 88px;
                min-width: 88px;
                height: 52px;
                font-size: 15px;
                font-weight: bold;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 0;
            }

            /* Modals & Overlays on Mobile */
            .modal-content, #skills-modal-container {
                width: 95vw !important;
                max-width: 95vw !important;
                max-height: 92vh !important;
                padding: 16px !important;
            }
            #modfile-editor {
                padding: 14px !important;
            }
            #modfile-editor div[style*="grid-template-columns"] {
                grid-template-columns: 1fr !important;
            }
        }
    </style>
</head>
<body>

    <div id="sidebar-backdrop" onclick="toggleSidebar(false)"></div>

    <!-- SIDEBAR: Exakte Struktur von oben nach unten -->
    <div id="sidebar">
        <div class="sidebar-mobile-header">
            <div style="color: var(--accent-blue); font-weight: bold; font-size: 1rem;">// HERMES NAVIGATION</div>
            <button class="btn" style="width: auto; padding: 6px 12px; font-size: 0.85em; border-color: var(--accent-red); color: var(--accent-red);" onclick="toggleSidebar(false)">✕ Schließen</button>
        </div>
        <!-- 1. CLUSTER NODES -->
        <div class="sidebar-section">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h3 style="margin-bottom: 0;">Cluster Nodes</h3>
                <button class="btn" style="width: auto; padding: 2px 8px; font-size: 0.75em; border-color: var(--accent-green); color: var(--accent-green);" onclick="openNodeCreatorModal()">[+ Node]</button>
            </div>
            <select id="node-select" class="btn" style="background: var(--bg-dark);" onchange="onNodeSelectChange(this.value)">
                <option value="outerheaven">Lade Knoten...</option>
            </select>
            <div id="node-desc" style="font-size: 0.72em; color: #777; margin-top: 4px; padding-left: 2px;"></div>
        </div>

        <!-- 2. SYSTEM STATUS (für den ausgewählten Node) -->
        <div class="sidebar-section">
            <h3>System Status</h3>
            <div id="ollama-status-indicator" style="color: var(--accent-green); margin-bottom: 6px; font-size: 0.88em;">[✓] Ollama Online</div>
            <div id="agent-status-indicator" style="color: var(--accent-green); font-size: 0.88em;">[✓] Hermes Agent Online</div>
        </div>

        <!-- 3. AKTIVES MODELL (Reine Statusanzeige / Badge ohne Dropdown mit /api/ps VRAM-Status) -->
        <div class="sidebar-section">
            <h3>Aktives Modell</h3>
            <div id="active-model-box" style="background: var(--bg-dark); border: 1px solid var(--border); padding: 8px 10px; border-radius: 3px;">
                <div style="font-size: 0.72em; color: #888; text-transform: uppercase; margin-bottom: 3px;">Im VRAM / Speicher (/api/ps):</div>
                <div id="active-model-name" style="color: var(--accent-green); font-weight: bold; word-break: break-all; font-size: 0.9em;">Lade...</div>
                <div id="active-model-vram" style="font-size: 0.75em; color: var(--accent-blue); margin-top: 2px;">-</div>
            </div>
        </div>

        <!-- 4. MODELL-EINSTELLUNGEN & KONTROLLE -->
        <div class="sidebar-section">
            <h3>Modell-Einstellungen & Kontrolle</h3>
            <label style="font-size: 0.78em; color: #888; display: block; margin-bottom: 4px;">Verfügbare Modelle:</label>
            <select id="model-select" class="btn" style="background: var(--bg-dark);" onchange="onModelSelectChange(this.value)">
                <option>Lade Modelle...</option>
            </select>
            <button class="btn" onclick="openModfileEditor()">[⚙] Modfile bearbeiten</button>
            <button class="btn" style="border-color: var(--accent-blue); color: var(--accent-blue);" onclick="openModelTransferModal()">[⇄ Transfer] Modell übertragen</button>
            <button class="btn" id="toggle-view-btn" onclick="toggleModfileView()">[📄] Modelfile-Ansicht: AUS</button>
        </div>

        <!-- 5. SKILL-BIBLIOTHEK -->
        <div class="sidebar-section">
            <h3>Skill-Bibliothek</h3>
            <button class="btn" style="border-color: var(--accent-blue); color: var(--accent-blue);" onclick="openSkillsModal()">[+] Skills durchsuchen/laden</button>
            <button class="btn" style="border-color: var(--accent-green); color: var(--accent-green);" onclick="openSkillCreatorModal()">[+ Neuen Skill erstellen]</button>
            <div id="active-skills-sidebar" style="margin-top: 10px; font-size: 0.82em;">
                <span style="color: #666; font-style: italic;">Lade Skills...</span>
            </div>
        </div>

        <!-- 5b. PENDING OUTBOX & E-MAILS -->
        <div class="sidebar-section">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h3 style="margin-bottom: 0;">E-Mail Outbox</h3>
                <span id="sidebar-outbox-badge" style="background: #f7768e; color: #0d1117; font-weight: bold; border-radius: 10px; padding: 2px 7px; font-size: 0.72em;">0</span>
            </div>
            <button class="btn" style="border-color: #f7768e; color: #f7768e;" onclick="openOutboxModal()">[📬 Pending Outbox / Entwürfe]</button>
        </div>

        <!-- 5c. PSD BANK READ-ONLY SALDO -->
        <div class="sidebar-section">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <h3 style="margin-bottom: 0;">PSD Bank Saldo</h3>
                <span id="banking-status-indicator" style="color: var(--accent-green); font-size: 0.72em; font-weight: bold;">[Read-Only]</span>
            </div>
            <div style="background: var(--bg-dark); border: 1px solid var(--border); padding: 8px 10px; border-radius: 3px;">
                <div style="font-size: 0.72em; color: #888; text-transform: uppercase; margin-bottom: 3px;">PSD Bank Hessen-Thüringen:</div>
                <div id="psd-balance-display" style="color: var(--accent-green); font-weight: bold; font-size: 1.02em;">Lade Saldo...</div>
                <div id="psd-balance-timestamp" style="font-size: 0.72em; color: #777; margin-top: 2px;">-</div>
            </div>
            <button class="btn" style="border-color: var(--accent-green); color: var(--accent-green); margin-top: 8px; font-size: 0.8em; padding: 6px 10px;" onclick="triggerBankingReport()">[📊 Finanzbericht jetzt generieren]</button>
        </div>

        <!-- 6. SITZUNGEN -->
        <div class="sidebar-section">
            <h3>Sitzungen</h3>
            <button class="btn active">[+] Neuer Task</button>
            <button class="btn" style="color: #555;">[-] Historie laden...</button>
        </div>
    </div>

    <!-- MAIN AREA -->
    <div id="main">
        <div id="header">
            <div style="display: flex; align-items: center; gap: 12px; min-width: 0;">
                <button id="menu-toggle-btn" class="mobile-hamburger-btn" onclick="toggleSidebar()" aria-label="Menü öffnen">☰</button>
                <h1 style="margin: 0; white-space: nowrap;">// HERMES<span class="desktop-only"> AGENT _</span></h1>
                <span id="header-info-badge" style="font-size: 0.85em; color: var(--text-main); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    Knoten: <strong id="header-node-display" style="color: var(--accent-green);">Outer Heaven</strong> |
                    Auswahl: <strong id="current-model-display" style="color: var(--accent-blue);">Lade...</strong>
                </span>
            </div>
            <div style="display: flex; align-items: center; gap: 10px;">
                <div id="header-banking-badge" style="background: var(--bg-panel); border: 1px solid var(--accent-green); border-radius: 4px; padding: 5px 10px; font-size: 0.82em; display: flex; align-items: center; gap: 6px; cursor: pointer;" onclick="triggerBankingReport()" title="PSD Bank Hessen-Thüringen (Klicken für Sofort-Report)">
                    <span style="color: #888;">PSD:</span>
                    <strong id="header-balance-val" style="color: var(--accent-green);">Lade...</strong>
                </div>
                <button id="header-outbox-btn" class="btn" style="width: auto; padding: 6px 12px; font-size: 0.82em; border-color: #f7768e; color: #f7768e; display: flex; align-items: center; gap: 6px;" onclick="openOutboxModal()">
                    <span>📬 <span class="desktop-only">E-Mail </span>Outbox</span>
                    <span id="header-outbox-badge" style="background: var(--accent-red); color: white; border-radius: 10px; padding: 1px 6px; font-size: 0.75em; display: none;">0</span>
                </button>
                <div class="dropdown">
                    <button id="cluster-menu-btn" class="btn" style="width: auto; padding: 6px 14px; font-size: 0.82em; border-color: var(--accent-blue); color: var(--accent-blue); display: flex; align-items: center; gap: 6px;" onclick="toggleClusterMenu(event)">
                        <span>⚙ <span class="desktop-only">Cluster-</span>Tools ▾</span>
                    </button>
                    <div id="cluster-dropdown-menu" class="dropdown-menu">
                        <button class="dropdown-item" onclick="openNodeCreatorModal(); closeClusterMenu();">
                            <span style="color: var(--accent-green);">[+]</span> Cluster Node hinzufügen
                        </button>
                        <button class="dropdown-item" onclick="openModelTransferModal(); closeClusterMenu();">
                            <span style="color: var(--accent-blue);">[⇄]</span> Modell-Transfer (P2P Sync)
                        </button>
                        <div class="dropdown-divider"></div>
                        <button class="dropdown-item" onclick="toggleModfileView(); closeClusterMenu();">
                            <span style="color: #888;">[📄]</span> Modelfile-Ansicht umschalten
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- DYNAMISCHES MODELFILE ANSICHTS-PANEL -->
        <div id="modfile-view-panel">
            <div id="modfile-view-header">
                <div style="font-size: 0.85em; color: var(--accent-blue); font-weight: bold;">
                    // MODELFILE KLARTEXT-ANSICHT: <span id="view-panel-model-name" style="color: var(--accent-green);">Lade...</span>
                </div>
                <div style="display: flex; gap: 8px;">
                    <button class="btn" style="width: auto; padding: 2px 8px; font-size: 0.75em;" onclick="openModfileEditor()">[⚙ In Editor bearbeiten]</button>
                    <button class="btn" style="width: auto; padding: 2px 8px; font-size: 0.75em; border-color: #444;" onclick="toggleModfileView()">[Minimieren]</button>
                </div>
            </div>
            <pre id="modfile-view-pre">Lade Modelfile...</pre>
        </div>

        <div id="chat-container">
            <div class="msg bot">System bereit. Warte auf Parameter oder Instruktionen.</div>
        </div>

        <div id="input-area">
            <textarea id="chat-input" placeholder="Befehl oder Anfrage eingeben... (Shift+Enter für neue Zeile)"></textarea>
            <button id="send-btn" class="btn" onclick="sendMessage()">SENDEN</button>
        </div>

        <!-- MODFILE EDITOR MODAL -->
        <div id="modfile-editor">
            <h2 style="color: var(--accent-blue); margin-bottom: 5px;">// MODELFILE KONFIGURATION & BUILDER</h2>
            <p style="font-size: 0.85em; color: var(--text-main); margin-bottom: 15px;">
                Passe den System-Prompt, Parameter oder das Basismodell direkt an oder wähle eine Vorlage.
            </p>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 10px;">
                <div>
                    <label style="font-size: 0.8em; color: var(--accent-blue); display: block; margin-bottom: 5px;">Ziel-Modellname:</label>
                    <input type="text" id="target-model-name" class="btn" style="background: var(--bg-panel); width: 100%; cursor: text; padding: 8px 12px;" placeholder="z. B. hermes-custom:v1">
                </div>
                <div>
                    <label style="font-size: 0.8em; color: var(--accent-blue); display: block; margin-bottom: 5px;">Vorlagen (Templates):</label>
                    <select id="template-select" class="btn" style="background: var(--bg-panel); width: 100%;" onchange="applyTemplate(this.value)">
                        <option value="">-- Vorlage wählen --</option>
                        <option value="base">Base / Standard Hermes</option>
                        <option value="coder">Coder / Dev Spezialist</option>
                        <option value="roleplay">Rollenspiel / Kreativ</option>
                        <option value="outerheaven">Outer Heaven Cluster Ops</option>
                    </select>
                </div>
            </div>

            <textarea id="modfile-textarea" placeholder="# Modelfile-Inhalt hier definieren..."></textarea>
            
            <div class="editor-actions">
                <button class="btn" style="width: auto; border-color: var(--accent-red);" onclick="closeModfileEditor()">Abbrechen</button>
                <button class="btn" style="width: auto; border-color: var(--accent-green);" onclick="buildModel()">[⚒] Modell neu kompilieren</button>
            </div>
            <div id="build-status" style="margin-top: 10px; font-size: 0.9em; font-weight: bold;"></div>
        </div>

        <!-- SKILL-BIBLIOTHEK MODAL -->
        <div id="skills-modal">
            <div id="skills-modal-container">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
                    <div>
                        <h2 style="color: var(--accent-blue); font-size: 1.1em;">// HERMES SKILL-BIBLIOTHEK</h2>
                        <span style="font-size: 0.8em; color: #888;">Wähle Agenten-Skills zur dynamischen Aktivierung aus.</span>
                    </div>
                    <button class="btn" style="width: auto; padding: 4px 10px; border-color: var(--accent-red);" onclick="closeSkillsModal()">[✕ Schließen]</button>
                </div>

                <div id="skills-modal-list">
                    <!-- Wird dynamisch aus /api/skills geladen -->
                </div>

                <div style="border-top: 1px solid var(--border); padding-top: 12px; display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.8em; color: var(--accent-green);">[✓] Änderungen werden sofort übernommen</span>
                    <button class="btn" style="width: auto; border-color: var(--accent-green); color: var(--accent-green);" onclick="closeSkillsModal()">Fertig</button>
                </div>
            </div>
        </div>

        <!-- SKILL-CREATOR MODAL (Vereinfachter, intuitiver Skill-Builder) -->
        <div id="skill-creator-modal" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(9, 10, 15, 0.94); backdrop-filter: blur(5px); z-index: 110; padding: 14px; justify-content: center; align-items: center;">
            <div class="modal-content" style="width: 100%; max-width: 640px; background: var(--bg-panel); border: 1px solid var(--accent-green); border-radius: 6px; padding: 20px; display: flex; flex-direction: column; max-height: 92vh; box-shadow: 0 10px 30px rgba(0,0,0,0.85); overflow-y: auto;">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 14px; gap: 10px;">
                    <div>
                        <h2 style="color: var(--accent-green); font-size: 1.1em; margin: 0;">// SKILL-BUILDER</h2>
                        <span style="font-size: 0.82em; color: #9aa5ce;">Erstelle in 3 einfachen Schritten eine neue Fähigkeit für Hermes.</span>
                    </div>
                    <button class="btn" style="width: auto; padding: 6px 12px; margin: 0; border-color: var(--accent-red); color: var(--accent-red); flex-shrink: 0;" onclick="closeSkillCreatorModal()">✕</button>
                </div>

                <!-- Schnellstart-Vorlagen (1-Click Presets) -->
                <div style="margin-bottom: 14px;">
                    <span style="font-size: 0.78em; color: #888; display: block; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px;">Schnellstart-Vorlage wählen (Optional):</span>
                    <div style="display: flex; flex-wrap: wrap; gap: 8px;">
                        <button type="button" class="btn" style="width: auto; margin: 0; padding: 6px 10px; font-size: 0.82em; border-color: #e0af68; color: #e0af68;" onclick="applySkillPreset('lebensplan')">📅 Lebensplan & Reminder</button>
                        <button type="button" class="btn" style="width: auto; margin: 0; padding: 6px 10px; font-size: 0.82em; border-color: #f7768e; color: #f7768e;" onclick="applySkillPreset('email_autoreply')">✉️ Auto-Reply & E-Mail Entwurf</button>
                        <button type="button" class="btn" style="width: auto; margin: 0; padding: 6px 10px; font-size: 0.82em; border-color: var(--accent-blue); color: var(--accent-blue);" onclick="applySkillPreset('mahnung')">📄 Dokument-/Mahnungs-Alarm</button>
                        <button type="button" class="btn" style="width: auto; margin: 0; padding: 6px 10px; font-size: 0.82em; border-color: var(--accent-green); color: var(--accent-green);" onclick="applySkillPreset('telegram')">📲 Telegram-Benachrichtigung</button>
                        <button type="button" class="btn" style="width: auto; margin: 0; padding: 6px 10px; font-size: 0.82em; border-color: #bb9af7; color: #bb9af7;" onclick="applySkillPreset('system')">🖥 System-/Server-Check</button>
                    </div>
                </div>

                <div style="display: flex; flex-direction: column; gap: 14px;">
                    <div>
                        <label style="font-size: 0.88em; color: var(--accent-blue); display: block; margin-bottom: 5px; font-weight: bold;">1. Wie soll der Skill heißen?</label>
                        <input type="text" id="creator-skill-name" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 11px 12px; margin: 0; font-size: 16px;" placeholder="z. B. Mahnungs-Alarm oder Server-Check">
                    </div>

                    <div>
                        <label style="font-size: 0.88em; color: var(--accent-blue); display: block; margin-bottom: 5px; font-weight: bold;">2. Wann soll Hermes aktiv werden?</label>
                        <textarea id="creator-skill-desc" class="btn" style="background: var(--bg-dark); width: 100%; height: 84px; cursor: text; padding: 11px 12px; margin: 0; resize: vertical; line-height: 1.45; font-size: 16px;" placeholder="Beschreibe in normalen Worten: z. B. Wenn eine Rechnung, Mahnung oder dringende Frist erkannt wird."></textarea>
                    </div>

                    <div>
                        <label style="font-size: 0.88em; color: var(--accent-blue); display: block; margin-bottom: 5px; font-weight: bold;">3. Was soll ausgeführt werden?</label>
                        <select id="creator-skill-handler" class="btn" style="background: var(--bg-dark); width: 100%; padding: 11px 12px; margin: 0; font-size: 16px;" onchange="onCreatorHandlerChange(this.value)">
                            <option value="Lebensplan & Smart Reminder">📅 Lebensplan & Smart Reminder</option>
                            <option value="E-Mail Dispatch & Draft">✉️ E-Mail Dispatch & Entwurf (Paperworker)</option>
                            <option value="Desktop-Notification">🔔 Hinweis / Push-Benachrichtigung senden</option>
                            <option value="Webhook / Telegram">📲 Nachricht an Telegram / Webhook senden</option>
                            <option value="Kommandozeilen-Befehl">⚡ Terminal-Befehl / Skript ausführen</option>
                        </select>
                    </div>

                    <div id="creator-reminder-action-container" style="display: none; background: rgba(224, 175, 104, 0.08); border: 1px solid rgba(224, 175, 104, 0.4); border-radius: 4px; padding: 10px 12px; margin-top: -4px;">
                        <label style="font-size: 0.84em; color: #e0af68; display: block; margin-bottom: 6px; font-weight: bold;">Reminder Aktionen:</label>
                        <div style="display: flex; flex-direction: column; gap: 8px;">
                            <label style="display: flex; align-items: center; gap: 8px; font-size: 0.85em; cursor: pointer; color: var(--text-main);">
                                <input type="checkbox" id="creator-reminder-push" checked style="accent-color: var(--accent-green); width: 16px; height: 16px;">
                                <span><strong>Push-Nachricht ans Handy senden</strong> (Smartphone / Telegram)</span>
                            </label>
                            <label style="display: flex; align-items: center; gap: 8px; font-size: 0.85em; cursor: pointer; color: var(--text-main);">
                                <input type="checkbox" id="creator-reminder-speaker" style="accent-color: #e0af68; width: 16px; height: 16px;">
                                <span><strong>Sprachausgabe über Heimnetz-Speaker (Beta)</strong> (Xiaomi TTS)</span>
                            </label>
                        </div>
                    </div>

                    <div id="creator-email-action-container" style="background: rgba(122, 162, 247, 0.08); border: 1px solid var(--border); border-radius: 4px; padding: 10px 12px; margin-top: -4px;">
                        <label style="font-size: 0.84em; color: var(--accent-blue); display: block; margin-bottom: 6px; font-weight: bold;">E-Mail Workflow-Option:</label>
                        <div style="display: flex; flex-direction: column; gap: 8px;">
                            <label style="display: flex; align-items: center; gap: 8px; font-size: 0.85em; cursor: pointer; color: var(--text-main);">
                                <input type="radio" name="creator-email-action" value="draft" id="creator-email-action-draft" checked>
                                <span><strong>Entwurf generieren & zur Freigabe in App anzeigen</strong> (Draft/Review)</span>
                            </label>
                            <label style="display: flex; align-items: center; gap: 8px; font-size: 0.85em; cursor: pointer; color: var(--text-main);">
                                <input type="radio" name="creator-email-action" value="auto_send" id="creator-email-action-auto">
                                <span><strong>Direkt absenden & mich nur benachrichtigen</strong> (Auto-Send)</span>
                            </label>
                        </div>
                    </div>

                    <details style="background: rgba(0, 0, 0, 0.28); border: 1px solid var(--border); border-radius: 4px; padding: 10px 12px;">
                        <summary style="cursor: pointer; font-size: 0.84em; color: #888; font-weight: bold; user-select: none;">⚙ Erweiterte Optionen (Datenfelder & Ziel-Adresse)</summary>
                        <div style="display: flex; flex-direction: column; gap: 12px; margin-top: 12px;">
                            <div>
                                <label style="font-size: 0.8em; color: var(--accent-blue); display: block; margin-bottom: 4px;">Automatisch auszulesende Infos (kommagetrennt):</label>
                                <input type="text" id="creator-skill-params" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 9px 12px; margin: 0; font-size: 16px;" placeholder="z. B. betrag, frist, absender (Standard: kontext, zusammenfassung)">
                            </div>
                            <div>
                                <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 4px;">Ziel-Adresse / Skript-Pfad (Optional):</label>
                                <input type="text" id="creator-skill-target" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 9px 12px; margin: 0; font-size: 16px;" placeholder="z. B. Telegram Chat-ID, URL oder Shell-Befehl">
                            </div>
                        </div>
                    </details>
                </div>

                <div id="creator-status" style="margin-top: 12px; font-size: 0.88em; font-weight: bold;"></div>

                <div style="border-top: 1px solid var(--border); margin-top: 14px; padding-top: 14px; display: flex; justify-content: flex-end; gap: 10px;">
                    <button class="btn" style="width: auto; margin: 0; padding: 10px 16px; border-color: var(--accent-red);" onclick="closeSkillCreatorModal()">Abbrechen</button>
                    <button class="btn" style="width: auto; margin: 0; padding: 10px 18px; border-color: var(--accent-green); color: var(--accent-green); font-weight: bold;" onclick="submitSkillCreator()">✓ Skill aktivieren</button>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL: NODE CREATOR -->
    <div id="node-creator-modal" class="modal-overlay">
        <div class="modal-content" style="max-width: 600px;">
            <div class="modal-header">
                <h3>// CLUSTER NODE HINZUFÜGEN _</h3>
                <button class="btn-close" onclick="closeNodeCreatorModal()">✕</button>
            </div>
            <div class="modal-body">
                <div style="display: flex; flex-direction: column; gap: 12px;">
                    <div>
                        <label style="font-size: 0.8em; color: var(--accent-green); display: block; margin-bottom: 5px; font-weight: bold;">Node-ID (eindeutiger Identifier, Kleinbuchstaben):</label>
                        <input type="text" id="creator-node-id" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="z. B. rex, mg-ray, worker-02">
                    </div>
                    <div>
                        <label style="font-size: 0.8em; color: var(--accent-blue); display: block; margin-bottom: 5px; font-weight: bold;">Anzeigename:</label>
                        <input type="text" id="creator-node-name" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="z. B. Metal Gear Rex (Inference Node)">
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div>
                            <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 5px;">Host / LAN-IP / Hostname:</label>
                            <input type="text" id="creator-node-host" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="z. B. 192.168.178.50 oder rex">
                        </div>
                        <div>
                            <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 5px;">Tailscale-IP (Optional):</label>
                            <input type="text" id="creator-node-tailscale" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="z. B. 100.x.y.z">
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                        <div>
                            <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 5px;">Ollama Port / Endpunkte:</label>
                            <input type="text" id="creator-node-ollama" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="Standard: 11434 (kommagetrennt für URLs)">
                        </div>
                        <div>
                            <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 5px;">Agent Port / Endpunkte:</label>
                            <input type="text" id="creator-node-agent" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="Standard: 5005 oder 8000">
                        </div>
                    </div>
                    <div>
                        <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 5px;">Beschreibung & Rolle im Cluster:</label>
                        <input type="text" id="creator-node-desc" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 8px 12px;" placeholder="z. B. Dedizierter Vision- & Embeddings-Worker">
                    </div>
                </div>

                <div id="node-creator-status" style="margin-top: 15px; font-size: 0.85em; font-weight: bold;"></div>

                <div style="border-top: 1px solid var(--border); margin-top: 15px; padding-top: 15px; display: flex; justify-content: flex-end; gap: 10px;">
                    <button class="btn" style="width: auto; border-color: var(--accent-red);" onclick="closeNodeCreatorModal()">Abbrechen</button>
                    <button class="btn" style="width: auto; border-color: var(--accent-green); color: var(--accent-green);" onclick="submitNodeCreator()">[💾 Node speichern & aktivieren]</button>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL: MODEL TRANSFER -->
    <div id="model-transfer-modal" class="modal-overlay">
        <div class="modal-content" style="max-width: 650px;">
            <div class="modal-header">
                <h3>// CLUSTER MODELL-TRANSFER (P2P SYNC) _</h3>
                <button class="btn-close" onclick="closeModelTransferModal()">✕</button>
            </div>
            <div class="modal-body">
                <p style="font-size: 0.82em; color: #aaa; margin-bottom: 15px;">
                    Kopiere ein vorhandenes Ollama-Modell inklusive Manifest und Modellgewichten (Blobs) verlustfrei zwischen Cluster-Knoten.
                </p>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                    <div>
                        <label style="font-size: 0.8em; color: var(--accent-blue); display: block; margin-bottom: 5px; font-weight: bold;">Quell-Knoten (Source):</label>
                        <select id="transfer-source-node" class="btn" style="background: var(--bg-dark);" onchange="updateTransferSourceModels()">
                        </select>
                    </div>
                    <div>
                        <label style="font-size: 0.8em; color: var(--accent-green); display: block; margin-bottom: 5px; font-weight: bold;">Ziel-Knoten (Target):</label>
                        <select id="transfer-target-node" class="btn" style="background: var(--bg-dark);">
                        </select>
                    </div>
                </div>

                <div style="margin-top: 15px;">
                    <label style="font-size: 0.8em; color: var(--text-main); display: block; margin-bottom: 5px; font-weight: bold;">Zu übertragendes Modell:</label>
                    <select id="transfer-model-select" class="btn" style="background: var(--bg-dark); width: 100%;">
                    </select>
                </div>

                <div id="transfer-status" style="margin-top: 15px; font-size: 0.85em; font-weight: bold;"></div>

                <div style="border-top: 1px solid var(--border); margin-top: 15px; padding-top: 15px; display: flex; justify-content: flex-end; gap: 10px;">
                    <button class="btn" style="width: auto; border-color: var(--accent-red);" onclick="closeModelTransferModal()">Schließen</button>
                    <button id="transfer-submit-btn" class="btn" style="width: auto; border-color: var(--accent-blue); color: var(--accent-blue);" onclick="submitModelTransfer()">[⇄ Transfer starten]</button>
                </div>
            </div>
        </div>
    </div>

    <!-- MODAL: PENDING OUTBOX & E-MAIL DRAFTS (Paperworker Review-Ansicht) -->
    <div id="outbox-modal" class="modal-overlay" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(9, 10, 15, 0.94); backdrop-filter: blur(5px); z-index: 115; padding: 14px; justify-content: center; align-items: center;">
        <div class="modal-content" style="width: 100%; max-width: 820px; background: var(--bg-panel); border: 1px solid #f7768e; border-radius: 6px; padding: 20px; display: flex; flex-direction: column; max-height: 92vh; box-shadow: 0 10px 30px rgba(0,0,0,0.85); overflow-y: auto;">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 14px; gap: 10px;">
                <div>
                    <h2 style="color: #f7768e; font-size: 1.15em; margin: 0;">// PENDING OUTBOX & E-MAIL PROTOKOLL</h2>
                    <span style="font-size: 0.82em; color: #9aa5ce;">Autonome E-Mail-Entwürfe prüfen, mit 1-Klick absenden oder Historie einsehen.</span>
                </div>
                <button class="btn" style="width: auto; padding: 6px 12px; margin: 0; border-color: var(--accent-red); color: var(--accent-red); flex-shrink: 0;" onclick="closeOutboxModal()">✕</button>
            </div>

            <!-- Tab Steuerung -->
            <div style="display: flex; gap: 8px; margin-bottom: 15px; border-bottom: 1px solid var(--border); padding-bottom: 10px;">
                <button id="tab-btn-drafts" class="btn active" style="width: auto; margin: 0; padding: 8px 14px; font-size: 0.88em; border-color: var(--accent-blue);" onclick="switchOutboxTab('drafts')">
                    📝 Entwürfe zur Freigabe (<span id="drafts-count-badge">0</span>)
                </button>
                <button id="tab-btn-sent" class="btn" style="width: auto; margin: 0; padding: 8px 14px; font-size: 0.88em; border-color: var(--border); color: #888;" onclick="switchOutboxTab('sent')">
                    ✓ Gesendet & Protokoll (<span id="sent-count-badge">0</span>)
                </button>
                <button class="btn" style="width: auto; margin: 0 0 0 auto; padding: 6px 12px; font-size: 0.8em; border-color: var(--accent-green); color: var(--accent-green);" onclick="loadOutboxData()">
                    🔄 Aktualisieren
                </button>
            </div>

            <div id="outbox-status-msg" style="margin-bottom: 10px; font-size: 0.88em; display: none;"></div>

            <!-- Tab 1: Entwürfe zur Freigabe -->
            <div id="outbox-tab-drafts">
                <div id="outbox-drafts-container" style="display: flex; flex-direction: column; gap: 12px;">
                    <div style="color: #777; font-style: italic; padding: 20px; text-align: center;">Lade Entwürfe...</div>
                </div>
            </div>

            <!-- Tab 2: Gesendete Protokolle -->
            <div id="outbox-tab-sent" style="display: none;">
                <div id="outbox-sent-container" style="display: flex; flex-direction: column; gap: 12px;">
                    <div style="color: #777; font-style: italic; padding: 20px; text-align: center;">Lade Protokolle...</div>
                </div>
            </div>

            <div style="border-top: 1px solid var(--border); margin-top: 18px; padding-top: 12px; display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 0.78em; color: #888;">Gekoppelt mit Paperless-ngx & SMTP</span>
                <button class="btn" style="width: auto; border-color: #555; color: #aaa;" onclick="closeOutboxModal()">Schließen</button>
            </div>
        </div>
    </div>

    <!-- MODAL: DRAFT BEARBEITEN -->
    <div id="draft-edit-modal" class="modal-overlay" style="display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(9, 10, 15, 0.95); backdrop-filter: blur(5px); z-index: 120; padding: 14px; justify-content: center; align-items: center;">
        <div class="modal-content" style="width: 100%; max-width: 680px; background: var(--bg-panel); border: 1px solid var(--accent-blue); border-radius: 6px; padding: 20px; display: flex; flex-direction: column; max-height: 92vh; box-shadow: 0 10px 30px rgba(0,0,0,0.85); overflow-y: auto;">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 14px;">
                <h3 style="color: var(--accent-blue); font-size: 1.05em; margin: 0;">// E-MAIL ENTWURF BEARBEITEN</h3>
                <button class="btn" style="width: auto; padding: 4px 10px; margin: 0; border-color: var(--accent-red); color: var(--accent-red);" onclick="closeDraftEditModal()">✕</button>
            </div>

            <input type="hidden" id="edit-draft-id">

            <div style="display: flex; flex-direction: column; gap: 12px;">
                <div>
                    <label style="font-size: 0.82em; color: var(--accent-blue); display: block; margin-bottom: 4px; font-weight: bold;">Empfänger-Adresse:</label>
                    <input type="email" id="edit-draft-recipient" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 10px 12px; margin: 0; font-size: 15px;">
                </div>
                <div>
                    <label style="font-size: 0.82em; color: var(--accent-blue); display: block; margin-bottom: 4px; font-weight: bold;">Betreff:</label>
                    <input type="text" id="edit-draft-subject" class="btn" style="background: var(--bg-dark); width: 100%; cursor: text; padding: 10px 12px; margin: 0; font-size: 15px;">
                </div>
                <div>
                    <label style="font-size: 0.82em; color: var(--accent-blue); display: block; margin-bottom: 4px; font-weight: bold;">E-Mail Text:</label>
                    <textarea id="edit-draft-body" class="btn" style="background: var(--bg-dark); width: 100%; height: 180px; cursor: text; padding: 10px 12px; margin: 0; font-size: 14px; font-family: monospace; resize: vertical; line-height: 1.45;"></textarea>
                </div>
            </div>

            <div id="edit-draft-status" style="margin-top: 10px; font-size: 0.85em; font-weight: bold;"></div>

            <div style="border-top: 1px solid var(--border); margin-top: 15px; padding-top: 12px; display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap;">
                <button class="btn" style="width: auto; border-color: var(--accent-red);" onclick="closeDraftEditModal()">Abbrechen</button>
                <button class="btn" style="width: auto; border-color: var(--accent-blue); color: var(--accent-blue);" onclick="submitDraftEdit(false)">💾 Speichern</button>
                <button class="btn" style="width: auto; border-color: var(--accent-green); color: var(--accent-green); font-weight: bold;" onclick="submitDraftEdit(true)">🚀 Speichern & Sofort senden</button>
            </div>
        </div>
    </div>

    <script>
        // Mobile Drawer Steuerung
        function toggleSidebar(forceState) {
            const sidebar = document.getElementById('sidebar');
            const backdrop = document.getElementById('sidebar-backdrop');
            if (!sidebar || !backdrop) return;
            const shouldOpen = (forceState !== undefined) ? forceState : !sidebar.classList.contains('open');
            console.log("[Hermes Mobile] toggleSidebar called! shouldOpen =", shouldOpen);
            if (shouldOpen) {
                sidebar.classList.add('open');
                backdrop.classList.add('active');
            } else {
                sidebar.classList.remove('open');
                backdrop.classList.remove('active');
            }
        }

        // Globaler Zustand für Cluster & Modelle
        let clusterNodesData = [];
        let selectedNodeId = 'outerheaven';
        let selectedModelName = null;

        // --- Vorlagen-Definitionen (Templates) ---
        const TEMPLATES = {
            base: {
                nameSuffix: "-custom:v1",
                content: (baseModel) => `FROM ${baseModel || 'hermes'}

# --- Parameter ---
PARAMETER temperature 0.7
PARAMETER top_p 0.9
PARAMETER stop "<|im_end|>"
PARAMETER stop "<|endoftext|>"

# --- System Prompt ---
SYSTEM \"\"\"Du bist Hermes, ein hochentwickelter KI-Assistent im Outer Heaven Cluster. Antworte präzise, faktenbasiert und professionell auf Deutsch.\"\"\"
`
            },
            coder: {
                nameSuffix: "-coder:v1",
                content: (baseModel) => `FROM ${baseModel || 'hermes'}

# --- Parameter ---
PARAMETER temperature 0.2
PARAMETER top_p 0.95
PARAMETER stop "<|im_end|>"

# --- System Prompt ---
SYSTEM \"\"\"Du bist Hermes Coder/Dev, ein spezialisierter Senior Software Engineer und System-Architekt. Du schreibst sauberen, modularen und performanten Code mit präziser Dokumentation und Best Practices.\"\"\"
`
            },
            roleplay: {
                nameSuffix: "-creative:v1",
                content: (baseModel) => `FROM ${baseModel || 'hermes'}

# --- Parameter ---
PARAMETER temperature 0.85
PARAMETER top_k 40
PARAMETER top_p 0.9
PARAMETER stop "<|im_end|>"

# --- System Prompt ---
SYSTEM \"\"\"Du bist Hermes Creative, ein immersiver Rollenspiel- und Storytelling-Agent. Verwende eine atmosphärische, lebendige Sprache im Cyberpunk- und Outer-Heaven-Setting.\"\"\"
`
            },
            outerheaven: {
                nameSuffix: "-ops:v1",
                content: (baseModel) => `FROM ${baseModel || 'hermes'}

# --- Parameter ---
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER stop "<|im_end|>"

# --- System Prompt ---
SYSTEM \"\"\"Du bist Hermes Ops im Outer Heaven Cluster. Du hast Zugriff auf System-Terminal, Dateiverwaltung und Paperless-Integration. Handle methodisch, führe Pläne schrittweise aus und dokumentiere jeden Statusbericht.\"\"\"
`
            }
        };

        // --- Cluster Nodes & Model Loader ---
        async function loadClusterNodes(autoSelectNode = null, autoSelectModel = null) {
            try {
                const res = await fetch('/api/nodes');
                const data = await res.json();
                clusterNodesData = data.nodes || [];

                const nodeSelect = document.getElementById('node-select');
                nodeSelect.innerHTML = '';

                if (clusterNodesData.length > 0) {
                    clusterNodesData.forEach(node => {
                        const opt = document.createElement('option');
                        opt.value = node.id;
                        opt.textContent = `${node.name} (${node.host})`;
                        nodeSelect.appendChild(opt);
                    });

                    // Bestimme aktiven Node
                    let targetNode = autoSelectNode || selectedNodeId || data.default_node || clusterNodesData[0].id;
                    const exists = clusterNodesData.some(n => n.id === targetNode);
                    if (!exists) targetNode = clusterNodesData[0].id;
                    nodeSelect.value = targetNode;
                    
                    applyNodeSelection(targetNode, autoSelectModel);
                } else {
                    nodeSelect.innerHTML = '<option value="">Keine Knoten konfiguriert</option>';
                    document.getElementById('ollama-status-indicator').innerHTML = '<span style="color: var(--accent-red);">[X] Keine Knoten</span>';
                    document.getElementById('agent-status-indicator').innerHTML = '<span style="color: var(--accent-red);">[X] Keine Knoten</span>';
                }
            } catch (e) {
                console.error("Fehler beim Abrufen der Cluster-Nodes:", e);
                document.getElementById('ollama-status-indicator').innerHTML = '<span style="color: var(--accent-red);">[X] Verbindungsfehler</span>';
                document.getElementById('agent-status-indicator').innerHTML = '<span style="color: var(--accent-red);">[X] Verbindungsfehler</span>';
            }
        }

        async function onNodeSelectChange(nodeId) {
            applyNodeSelection(nodeId);
        }

        async function applyNodeSelection(nodeId, autoSelectModel = null) {
            selectedNodeId = nodeId;
            const node = clusterNodesData.find(n => n.id === nodeId);
            if (!node) return;

            // Header aktualisieren
            document.getElementById('header-node-display').innerText = node.name;
            document.getElementById('node-desc').innerText = node.description || node.tailscale_ip || '';

            // 1. Statusanzeigen (Ollama + Hermes Agent)
            const ollamaInd = document.getElementById('ollama-status-indicator');
            if (node.ollama_online) {
                ollamaInd.innerHTML = '<span style="color: var(--accent-green);">[✓] Ollama Online</span>';
            } else {
                ollamaInd.innerHTML = '<span style="color: var(--accent-red);">[X] Ollama Offline</span>';
            }

            const agentInd = document.getElementById('agent-status-indicator');
            if (node.agent_online) {
                agentInd.innerHTML = `<span style="color: var(--accent-green);" title="${node.agent_info}">[✓] Hermes Agent Online</span>`;
            } else {
                agentInd.innerHTML = '<span style="color: var(--accent-red);">[X] Hermes Agent Offline</span>';
            }

            // 2. AKTIVES MODELL (Reine Statusanzeige aus /api/ps VRAM-Status)
            const activeBox = document.getElementById('active-model-box');
            const activeName = document.getElementById('active-model-name');
            const activeVram = document.getElementById('active-model-vram');

            if (node.vram_status && node.vram_status.active_model) {
                activeName.innerText = node.vram_status.active_model;
                activeName.style.color = "var(--accent-green)";
                activeVram.innerText = node.vram_status.size_vram_formatted || "Geladen";
                activeBox.style.borderColor = "var(--accent-green)";
            } else {
                activeName.innerText = "Standby (Kein Modell im VRAM)";
                activeName.style.color = "#777";
                activeVram.innerText = "0 GB VRAM belegt";
                activeBox.style.borderColor = "var(--border)";
            }

            // 3. Modell-Dropdown für den ausgewählten Knoten befüllen
            const modelSelect = document.getElementById('model-select');
            modelSelect.innerHTML = '';

            const models = node.models || [];
            if (models.length > 0) {
                models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.name;
                    opt.textContent = m.name;
                    modelSelect.appendChild(opt);
                });

                // Vorauswahl treffen:
                let targetModel = autoSelectModel;
                if (!targetModel && node.vram_status && node.vram_status.active_model) {
                    // Falls Modell im Speicher liegt, dieses bevorzugen
                    const loadedName = node.vram_status.active_model;
                    if (models.some(m => m.name === loadedName)) {
                        targetModel = loadedName;
                    }
                }
                if (!targetModel || !models.some(m => m.name === targetModel)) {
                    targetModel = models[0].name;
                }

                modelSelect.value = targetModel;
                selectedModelName = targetModel;
                onModelSelectChange(targetModel);
            } else {
                modelSelect.innerHTML = '<option value="">Keine Modelle installiert</option>';
                selectedModelName = null;
                document.getElementById('current-model-display').innerText = 'Kein Modell';
                document.getElementById('view-panel-model-name').innerText = 'Kein Modell';
                document.getElementById('modfile-view-pre').innerText = `# Keine Modelle auf Knoten '${node.name}' installiert.`;
            }
        }

        async function onModelSelectChange(modelName) {
            if (!modelName) return;
            selectedModelName = modelName;
            
            // Header und Ansichts-Panel aktualisieren
            document.getElementById('current-model-display').innerText = modelName;
            document.getElementById('view-panel-model-name').innerText = modelName;
            
            // Zielname im Editor voreinstellen
            document.getElementById('target-model-name').value = modelName;
            
            // Modelfile von der spezifischen Node-Instanz laden
            await loadAndDisplayModfile(selectedNodeId, modelName);
        }

        async function loadAndDisplayModfile(nodeId, modelName) {
            const viewPre = document.getElementById('modfile-view-pre');
            const textarea = document.getElementById('modfile-textarea');
            viewPre.innerText = `# Lade Modelfile für ${modelName} von Node '${nodeId}'...`;
            
            try {
                const res = await fetch(`/api/modfile/${encodeURIComponent(nodeId)}/${encodeURIComponent(modelName)}`);
                const data = await res.json();
                const content = data.modelfile || `# Kein Modelfile auf ${nodeId} für ${modelName} hinterlegt.`;
                
                viewPre.innerText = content;
                textarea.value = content;
            } catch (e) {
                viewPre.innerText = `# Fehler beim Abrufen des Modelfiles für ${modelName} von ${nodeId}.`;
            }
        }

        function toggleModfileView() {
            const panel = document.getElementById('modfile-view-panel');
            const btn = document.getElementById('toggle-view-btn');
            const isHidden = panel.style.display === 'none' || getComputedStyle(panel).display === 'none';
            if (isHidden) {
                panel.style.display = 'flex';
                if (btn) btn.innerText = '[📄] Modelfile-Ansicht: AN';
            } else {
                panel.style.display = 'none';
                if (btn) btn.innerText = '[📄] Modelfile-Ansicht: AUS';
            }
        }

        // --- Modelfile Editor & Vorlagen ---
        function openModfileEditor() {
            toggleSidebar(false);
            const currentModel = document.getElementById('model-select').value;
            if (currentModel && !document.getElementById('target-model-name').value) {
                document.getElementById('target-model-name').value = currentModel;
            }
            document.getElementById('build-status').innerText = "";
            document.getElementById('modfile-editor').style.display = 'flex';
        }

        function closeModfileEditor() {
            document.getElementById('modfile-editor').style.display = 'none';
            document.getElementById('build-status').innerText = "";
        }

        function applyTemplate(tplKey) {
            if (!tplKey || !TEMPLATES[tplKey]) return;
            const currentModel = document.getElementById('model-select').value || 'hermes';
            const tpl = TEMPLATES[tplKey];
            
            document.getElementById('modfile-textarea').value = tpl.content(currentModel);
            
            const basePrefix = currentModel.split(':')[0] || 'hermes';
            document.getElementById('target-model-name').value = `${basePrefix}${tpl.nameSuffix}`;
            document.getElementById('template-select').value = "";
        }

        async function buildModel() {
            const targetName = document.getElementById('target-model-name').value.trim();
            const modfile = document.getElementById('modfile-textarea').value.trim();
            const statusDiv = document.getElementById('build-status');
            
            if (!targetName) {
                statusDiv.innerHTML = "<span style='color: var(--accent-red);'>[!] Bitte gib einen Modellnamen an.</span>";
                return;
            }
            if (!modfile) {
                statusDiv.innerHTML = "<span style='color: var(--accent-red);'>[!] Modelfile darf nicht leer sein.</span>";
                return;
            }
            
            statusDiv.innerHTML = `<span style='color: var(--accent-blue);'>[⚒] Kompiliere Modell '${targetName}' auf Node '${selectedNodeId}'... Bitte warten.</span>`;
            try {
                const res = await fetch('/api/build', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: targetName, modelfile: modfile, node: selectedNodeId })
                });
                if (res.ok) {
                    statusDiv.innerHTML = `<span style='color: var(--accent-green);'>[✓] Modell '${targetName}' erfolgreich auf Node '${selectedNodeId}' kompiliert!</span>`;
                    
                    // Automatisch Knoten- und Modelliste neu laden und Zielmodell aktivieren
                    await loadClusterNodes(selectedNodeId, targetName);
                    
                    setTimeout(() => {
                        closeModfileEditor();
                    }, 1500);
                } else {
                    const err = await res.json().catch(() => ({}));
                    statusDiv.innerHTML = `<span style='color: var(--accent-red);'>[X] Fehler beim Build: ${err.detail || res.statusText}</span>`;
                }
            } catch (e) {
                statusDiv.innerHTML = "<span style='color: var(--accent-red);'>[X] Netzwerk- oder Verbindungsfehler.</span>";
            }
        }

        // --- Skill-Bibliothek ---
        async function loadSkills() {
            try {
                const res = await fetch('/api/skills');
                const data = await res.json();
                const skills = data.skills || [];
                renderSkillsModal(skills);
                renderSkillsSidebar(skills);
            } catch (e) {
                console.error("Fehler beim Laden der Skills:", e);
                document.getElementById('active-skills-sidebar').innerHTML = '<span style="color: var(--accent-red);">[X] Fehler beim Laden</span>';
            }
        }

        function renderSkillsSidebar(skills) {
            const container = document.getElementById('active-skills-sidebar');
            container.innerHTML = '';
            const activeSkills = skills.filter(s => s.enabled);
            
            if (activeSkills.length === 0) {
                container.innerHTML = '<span style="color: #666; font-style: italic;">Keine Skills aktiv</span>';
                return;
            }
            
            activeSkills.forEach(s => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; align-items: center; gap: 6px; margin-bottom: 5px; color: var(--accent-green);';
                item.innerHTML = `<span style="font-size: 0.6em;">●</span> <span>${s.name}</span>`;
                container.appendChild(item);
            });
        }

        function renderSkillsModal(skills) {
            const list = document.getElementById('skills-modal-list');
            list.innerHTML = '';
            
            skills.forEach(s => {
                const item = document.createElement('div');
                item.className = 'skill-item';

                const paramBadges = s.parameters && s.parameters.length > 0 
                    ? `<div style="margin-top: 6px; font-size: 0.75em; color: var(--accent-blue);">
                         <span style="color: #777;">Parameter:</span> ${s.parameters.map(p => `<code style="padding: 1px 4px; background: #000; border: 1px solid #333; border-radius: 2px; margin-right: 4px;">${p}</code>`).join('')}
                       </div>`
                    : '';

                item.innerHTML = `
                    <input type="checkbox" id="skill-chk-${s.id}" ${s.enabled ? 'checked' : ''} 
                           style="margin-top: 4px; cursor: pointer; accent-color: var(--accent-green); width: 17px; height: 17px;"
                           onchange="toggleSkill('${s.id}', this.checked)">
                    <div style="flex-grow: 1;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <label for="skill-chk-${s.id}" style="color: var(--accent-blue); font-weight: bold; cursor: pointer;">
                                ${s.name}
                            </label>
                            <div style="display: flex; gap: 6px;">
                                <span class="skill-badge" style="color: var(--accent-green); border-color: var(--accent-green);">${s.category}</span>
                                <span class="skill-badge" style="color: #888;">${s.source}</span>
                            </div>
                        </div>
                        <div style="font-size: 0.85em; color: var(--text-main); line-height: 1.4;">
                            ${s.description}
                        </div>
                        ${paramBadges}
                    </div>
                `;
                list.appendChild(item);
            });
        }

        async function toggleSkill(skillId, enabled) {
            try {
                const res = await fetch('/api/skills/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ skill_id: skillId, enabled: enabled })
                });
                if (res.ok) {
                    await loadSkills();
                }
            } catch (e) {
                console.error("Fehler beim Umschalten des Skills:", e);
            }
        }

        function openSkillsModal() {
            toggleSidebar(false);
            loadSkills();
            document.getElementById('skills-modal').style.display = 'flex';
        }

        function closeSkillsModal() {
            document.getElementById('skills-modal').style.display = 'none';
        }

        // --- Skill Creator Interaktion ---
        const SKILL_PRESETS = {
            lebensplan: {
                name: "lebensplan_reminder",
                desc: "Wenn der Nutzer Pläne äußert, Aufgaben festhalten will oder um eine Erinnerung bittet.",
                params: "datum_uhrzeit, vorhaben_aufgabe",
                handler: "Lebensplan & Smart Reminder",
                action_mode: "reminder",
                push_notify: true,
                speaker_broadcast: false
            },
            email_autoreply: {
                name: "email_autoreply_dispatch",
                desc: "Erstelle bei eingehenden Rechnungen, Mahnungen oder behördlichen Dokumenten automatisch eine professionelle, deeskalierende E-Mail-Antwort.",
                params: "absender, aktenzeichen, betrag, empfaenger_email",
                handler: "E-Mail Dispatch & Draft",
                action_mode: "draft"
            },
            mahnung: {
                name: "mahnungs_alarm",
                desc: "Löse diesen Skill aus, sobald im Text eine Mahnung, Zahlungsfrist oder dringende Forderung erkannt wird.",
                params: "absender, betrag, frist",
                handler: "Desktop-Notification",
                action_mode: "draft"
            },
            telegram: {
                name: "telegram_infosendung",
                desc: "Sende eine Zusammenfassung an Telegram, wenn der Nutzer eine Weiterleitung oder Benachrichtigung aufs Handy wünscht.",
                params: "titel, nachricht, prioritaet",
                handler: "Webhook / Telegram",
                action_mode: "draft"
            },
            system: {
                name: "cluster_system_check",
                desc: "Führe eine Diagnose aus, wenn nach Server-Status, Speicherplatz oder GPU-Auslastung gefragt wird.",
                params: "knoten, dienst",
                handler: "Kommandozeilen-Befehl",
                action_mode: "draft"
            }
        };

        function onCreatorHandlerChange(handlerValue) {
            const emailContainer = document.getElementById('creator-email-action-container');
            if (emailContainer) {
                emailContainer.style.display = (handlerValue === 'E-Mail Dispatch & Draft') ? 'block' : 'none';
            }
            const reminderContainer = document.getElementById('creator-reminder-action-container');
            if (reminderContainer) {
                reminderContainer.style.display = (handlerValue === 'Lebensplan & Smart Reminder') ? 'block' : 'none';
            }
        }

        function applySkillPreset(presetKey) {
            const p = SKILL_PRESETS[presetKey];
            if (!p) return;
            document.getElementById('creator-skill-name').value = p.name;
            document.getElementById('creator-skill-desc').value = p.desc;
            document.getElementById('creator-skill-params').value = p.params;
            document.getElementById('creator-skill-handler').value = p.handler;
            onCreatorHandlerChange(p.handler);

            if (p.action_mode === "auto_send") {
                const autoRadio = document.getElementById('creator-email-action-auto');
                if (autoRadio) autoRadio.checked = true;
            } else {
                const draftRadio = document.getElementById('creator-email-action-draft');
                if (draftRadio) draftRadio.checked = true;
            }

            if (p.push_notify !== undefined) {
                const pushChk = document.getElementById('creator-reminder-push');
                if (pushChk) pushChk.checked = p.push_notify;
            }
            if (p.speaker_broadcast !== undefined) {
                const speakerChk = document.getElementById('creator-reminder-speaker');
                if (speakerChk) speakerChk.checked = p.speaker_broadcast;
            }

            document.getElementById('creator-status').innerHTML = "<span style='color: var(--accent-blue);'>[i] Vorlage geladen – du kannst die Felder direkt anpassen oder sofort speichern.</span>";
        }

        function openSkillCreatorModal() {
            toggleSidebar(false);
            document.getElementById('creator-status').innerText = "";
            document.getElementById('creator-skill-name').value = "";
            document.getElementById('creator-skill-desc').value = "";
            document.getElementById('creator-skill-params').value = "";
            document.getElementById('creator-skill-target').value = "";
            document.getElementById('creator-skill-handler').selectedIndex = 0;
            onCreatorHandlerChange(document.getElementById('creator-skill-handler').value);
            document.getElementById('skill-creator-modal').style.display = 'flex';
        }

        function closeSkillCreatorModal() {
            document.getElementById('skill-creator-modal').style.display = 'none';
            document.getElementById('creator-status').innerText = "";
        }

        async function submitSkillCreator() {
            const name = document.getElementById('creator-skill-name').value.trim();
            const desc = document.getElementById('creator-skill-desc').value.trim();
            const params = document.getElementById('creator-skill-params').value.trim();
            const handler = document.getElementById('creator-skill-handler').value;
            const target = document.getElementById('creator-skill-target').value.trim();
            const statusDiv = document.getElementById('creator-status');

            const actionRadio = document.querySelector('input[name="creator-email-action"]:checked');
            const actionMode = actionRadio ? actionRadio.value : 'draft';

            const pushChk = document.getElementById('creator-reminder-push');
            const speakerChk = document.getElementById('creator-reminder-speaker');
            const pushNotify = pushChk ? pushChk.checked : true;
            const speakerBroadcast = speakerChk ? speakerChk.checked : false;

            if (!name) {
                statusDiv.innerHTML = "<span style='color: var(--accent-red);'>[!] Bitte gib einen Skill-Namen an.</span>";
                return;
            }
            if (!desc) {
                statusDiv.innerHTML = "<span style='color: var(--accent-red);'>[!] Bitte gib eine Beschreibung / Trigger-Bedingung an.</span>";
                return;
            }

            statusDiv.innerHTML = "<span style='color: var(--accent-blue);'>[⚒] Registriere Skill und generiere Function-Calling Schema...</span>";

            try {
                const res = await fetch('/api/skills/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: name,
                        description: desc,
                        parameters: params,
                        handler_type: handler,
                        handler_target: target,
                        action_mode: actionMode,
                        push_notify: pushNotify,
                        speaker_broadcast: speakerBroadcast
                    })
                });

                if (res.ok) {
                    const data = await res.json();
                    statusDiv.innerHTML = `<span style='color: var(--accent-green);'>[✓] Skill '${data.skill.name}' erfolgreich erstellt & gespeichert!</span>`;
                    await loadSkills();
                    setTimeout(() => {
                        closeSkillCreatorModal();
                    }, 1200);
                } else {
                    const err = await res.json().catch(() => ({}));
                    statusDiv.innerHTML = `<span style='color: var(--accent-red);'>[X] Fehler: ${err.detail || res.statusText}</span>`;
                }
            } catch (e) {
                statusDiv.innerHTML = "<span style='color: var(--accent-red);'>[X] Netzwerk- oder Verbindungsfehler.</span>";
            }
        }

        // --- Chat Interaction ---
        async function sendMessage() {
            const input = document.getElementById('chat-input');
            const text = input.value.trim();
            const model = document.getElementById('model-select').value;
            if (!text) return;
            
            appendMessage(text, 'user');
            input.value = '';
            
            const botMsgId = appendMessage('...', 'bot');
            
            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ model: model, prompt: text, node: selectedNodeId })
                });
                const data = await res.json();
                updateMessage(botMsgId, data.response || "Keine Antwort vom Modell.");
            } catch (e) {
                updateMessage(botMsgId, "Systemfehler: Verbindung fehlgeschlagen.");
            }
        }

        function appendMessage(text, sender) {
            const container = document.getElementById('chat-container');
            const div = document.createElement('div');
            div.className = `msg ${sender}`;
            const id = 'msg-' + Date.now();
            div.id = id;
            div.innerText = text;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
            return id;
        }

        function updateMessage(id, text) {
            const el = document.getElementById(id);
            if (el) {
                el.innerText = text;
                document.getElementById('chat-container').scrollTop = document.getElementById('chat-container').scrollHeight;
            }
        }

        // Periodischer Status-Refresh
        async function refreshSilently() {
            try {
                const res = await fetch('/api/nodes');
                const data = await res.json();
                clusterNodesData = data.nodes || [];
                const node = clusterNodesData.find(n => n.id === selectedNodeId);
                if (node) {
                    const activeName = document.getElementById('active-model-name');
                    const activeVram = document.getElementById('active-model-vram');
                    const activeBox = document.getElementById('active-model-box');
                    if (node.vram_status && node.vram_status.active_model) {
                        activeName.innerText = node.vram_status.active_model;
                        activeName.style.color = "var(--accent-green)";
                        activeVram.innerText = node.vram_status.size_vram_formatted || "Geladen";
                        activeBox.style.borderColor = "var(--accent-green)";
                    } else {
                        activeName.innerText = "Standby (Kein Modell im VRAM)";
                        activeName.style.color = "#777";
                        activeVram.innerText = "0 GB VRAM belegt";
                        activeBox.style.borderColor = "var(--border)";
                    }
                }
            } catch (e) {}
        }

        // Event Listeners
        document.getElementById('chat-input').addEventListener('keypress', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // --- Node Creator Modal Logik ---
        function openNodeCreatorModal() {
            toggleSidebar(false);
            document.getElementById('creator-node-id').value = '';
            document.getElementById('creator-node-name').value = '';
            document.getElementById('creator-node-host').value = '';
            document.getElementById('creator-node-tailscale').value = '';
            document.getElementById('creator-node-ollama').value = '11434';
            document.getElementById('creator-node-agent').value = '5005';
            document.getElementById('creator-node-desc').value = '';
            document.getElementById('node-creator-status').innerText = '';
            document.getElementById('node-creator-modal').classList.add('active');
        }

        function closeNodeCreatorModal() {
            document.getElementById('node-creator-modal').classList.remove('active');
        }

        async function submitNodeCreator() {
            const statusEl = document.getElementById('node-creator-status');
            const id = document.getElementById('creator-node-id').value.trim();
            const name = document.getElementById('creator-node-name').value.trim();
            const host = document.getElementById('creator-node-host').value.trim();
            const tailscale_ip = document.getElementById('creator-node-tailscale').value.trim();
            const ollamaRaw = document.getElementById('creator-node-ollama').value.trim();
            const agentRaw = document.getElementById('creator-node-agent').value.trim();
            const desc = document.getElementById('creator-node-desc').value.trim();

            if (!id || !name || !host) {
                statusEl.innerHTML = '<span style="color: var(--accent-red);">[!] ID, Name und Host dürfen nicht leer sein.</span>';
                return;
            }

            statusEl.innerHTML = '<span style="color: var(--accent-blue);">Speichere Knoten in Registry...</span>';

            try {
                const res = await fetch('/api/nodes/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        id: id,
                        name: name,
                        host: host,
                        tailscale_ip: tailscale_ip,
                        ollama_urls: ollamaRaw ? (ollamaRaw.startsWith('http') ? [ollamaRaw] : [`http://${host}:${ollamaRaw}`]) : undefined,
                        agent_urls: agentRaw ? (agentRaw.startsWith('http') ? [agentRaw] : [`http://${host}:${agentRaw}/`]) : undefined,
                        description: desc
                    })
                });

                const data = await res.json();
                if (res.ok) {
                    statusEl.innerHTML = '<span style="color: var(--accent-green);">[✓] Node erfolgreich hinzugefügt!</span>';
                    setTimeout(() => {
                        closeNodeCreatorModal();
                        loadClusterNodes(id);
                    }, 800);
                } else {
                    statusEl.innerHTML = `<span style="color: var(--accent-red);">Fehler: ${data.detail || 'Unbekannt'}</span>`;
                }
            } catch (e) {
                statusEl.innerHTML = `<span style="color: var(--accent-red);">Netzwerkfehler: ${e.message}</span>`;
            }
        }

        // --- Model Transfer Modal Logik ---
        function openModelTransferModal() {
            toggleSidebar(false);
            const srcSelect = document.getElementById('transfer-source-node');
            const tgtSelect = document.getElementById('transfer-target-node');
            srcSelect.innerHTML = '';
            tgtSelect.innerHTML = '';

            clusterNodesData.forEach(n => {
                const opt1 = document.createElement('option');
                opt1.value = n.id;
                opt1.textContent = `${n.name} (${n.host})`;
                srcSelect.appendChild(opt1);

                const opt2 = document.createElement('option');
                opt2.value = n.id;
                opt2.textContent = `${n.name} (${n.host})`;
                tgtSelect.appendChild(opt2);
            });

            // Standard: Source = aktueller Node, Target = anderer Node
            srcSelect.value = selectedNodeId || (clusterNodesData[0] ? clusterNodesData[0].id : '');
            if (clusterNodesData.length > 1) {
                const other = clusterNodesData.find(n => n.id !== srcSelect.value);
                if (other) tgtSelect.value = other.id;
            }

            document.getElementById('transfer-status').innerText = '';
            document.getElementById('transfer-submit-btn').disabled = false;
            updateTransferSourceModels();
            document.getElementById('model-transfer-modal').classList.add('active');
        }

        function closeModelTransferModal() {
            document.getElementById('model-transfer-modal').classList.remove('active');
        }

        function updateTransferSourceModels() {
            const srcNodeId = document.getElementById('transfer-source-node').value;
            const modelSelect = document.getElementById('transfer-model-select');
            modelSelect.innerHTML = '';

            const node = clusterNodesData.find(n => n.id === srcNodeId);
            if (node && node.models && node.models.length > 0) {
                node.models.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m.name;
                    opt.textContent = m.name;
                    modelSelect.appendChild(opt);
                });
                if (selectedModelName && node.models.some(m => m.name === selectedModelName)) {
                    modelSelect.value = selectedModelName;
                }
            } else {
                modelSelect.innerHTML = '<option value="">Keine Modelle auf diesem Knoten</option>';
            }
        }

        async function submitModelTransfer() {
            const src = document.getElementById('transfer-source-node').value;
            const tgt = document.getElementById('transfer-target-node').value;
            const model = document.getElementById('transfer-model-select').value;
            const statusEl = document.getElementById('transfer-status');
            const btn = document.getElementById('transfer-submit-btn');

            if (!model) {
                statusEl.innerHTML = '<span style="color: var(--accent-red);">[!] Kein Modell ausgewählt.</span>';
                return;
            }
            if (src === tgt) {
                statusEl.innerHTML = '<span style="color: var(--accent-red);">[!] Quelle und Ziel müssen unterschiedlich sein.</span>';
                return;
            }

            btn.disabled = true;
            statusEl.innerHTML = `<span style="color: var(--accent-blue);">[⟳] Übertrage ${model} von ${src} nach ${tgt}... Bitte warten...</span>`;

            try {
                const res = await fetch('/api/models/transfer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        source_node: src,
                        target_node: tgt,
                        model_name: model
                    })
                });

                const data = await res.json();
                if (res.ok) {
                    statusEl.innerHTML = `<span style="color: var(--accent-green);">[✓] Transfer erfolgreich: ${data.message || 'Modell übertragen'}</span>`;
                    setTimeout(() => {
                        loadClusterNodes(tgt, model);
                    }, 1200);
                } else {
                    statusEl.innerHTML = `<span style="color: var(--accent-red);">[X] Fehler: ${data.detail || 'Transfer fehlgeschlagen'}</span>`;
                }
            } catch (e) {
                statusEl.innerHTML = `<span style="color: var(--accent-red);">[X] Netzwerkfehler: ${e.message}</span>`;
            } finally {
                btn.disabled = false;
            }
        }

        // =====================================================================
        // PENDING OUTBOX & E-MAIL DRAFTS WORKFLOW
        // =====================================================================
        let currentOutboxTab = 'drafts';
        let currentEditingDraftId = null;

        function openOutboxModal() {
            toggleSidebar(false);
            loadOutboxData();
            document.getElementById('outbox-modal').style.display = 'flex';
        }

        function closeOutboxModal() {
            document.getElementById('outbox-modal').style.display = 'none';
        }

        function switchOutboxTab(tab) {
            currentOutboxTab = tab;
            const btnDrafts = document.getElementById('tab-btn-drafts');
            const btnSent = document.getElementById('tab-btn-sent');
            const tabDrafts = document.getElementById('outbox-tab-drafts');
            const tabSent = document.getElementById('outbox-tab-sent');

            if (tab === 'drafts') {
                btnDrafts.classList.add('active');
                btnDrafts.style.borderColor = 'var(--accent-blue)';
                btnDrafts.style.color = '#fff';
                btnSent.classList.remove('active');
                btnSent.style.borderColor = 'var(--border)';
                btnSent.style.color = '#888';
                tabDrafts.style.display = 'block';
                tabSent.style.display = 'none';
            } else {
                btnSent.classList.add('active');
                btnSent.style.borderColor = 'var(--accent-green)';
                btnSent.style.color = '#fff';
                btnDrafts.classList.remove('active');
                btnDrafts.style.borderColor = 'var(--border)';
                btnDrafts.style.color = '#888';
                tabSent.style.display = 'block';
                tabDrafts.style.display = 'none';
            }
        }

        async function loadOutboxData() {
            try {
                const res = await fetch('/api/email/outbox');
                if (!res.ok) return;
                const data = await res.json();
                const emails = data.emails || [];

                const drafts = emails.filter(e => e.status === 'draft');
                const sent = emails.filter(e => e.status === 'sent');

                // Badges aktualisieren
                const draftsBadge = document.getElementById('drafts-count-badge');
                if (draftsBadge) draftsBadge.innerText = drafts.length;

                const sentBadge = document.getElementById('sent-count-badge');
                if (sentBadge) sentBadge.innerText = sent.length;

                const sidebarBadge = document.getElementById('sidebar-outbox-badge');
                if (sidebarBadge) sidebarBadge.innerText = drafts.length;

                const headerBadge = document.getElementById('header-outbox-badge');
                if (headerBadge) {
                    headerBadge.innerText = drafts.length;
                    headerBadge.style.display = drafts.length > 0 ? 'inline-block' : 'none';
                }

                // Tab 1: Entwürfe rendern
                const draftsContainer = document.getElementById('outbox-drafts-container');
                if (draftsContainer) {
                    if (drafts.length === 0) {
                        draftsContainer.innerHTML = `
                            <div style="background: rgba(0,0,0,0.3); border: 1px dashed var(--border); border-radius: 6px; padding: 30px; text-align: center; color: #888;">
                                <div style="font-size: 1.8em; margin-bottom: 8px;">📭</div>
                                <div style="font-weight: bold; color: #aaa;">Keine ausstehenden Entwürfe vorhanden.</div>
                                <div style="font-size: 0.85em; margin-top: 4px;">Hermes legt neue Entwürfe aus Paperless automatisch hier zur 1-Klick-Freigabe ab.</div>
                            </div>`;
                    } else {
                        draftsContainer.innerHTML = drafts.map(item => {
                            const meta = item.metadata || {};
                            const dateStr = item.created_at ? new Date(item.created_at).toLocaleString('de-DE') : '-';
                            const docRef = item.document_id ? `<span style="background: #24283b; border: 1px solid #414868; border-radius: 3px; padding: 1px 6px; font-size: 0.75em; color: var(--accent-blue);">Doc #${item.document_id}</span>` : '';
                            const azBadge = meta.aktenzeichen ? `<span style="background: #1f2335; border: 1px solid var(--accent-blue); border-radius: 3px; padding: 1px 6px; font-size: 0.75em; color: var(--accent-blue);">AZ: ${meta.aktenzeichen}</span>` : '';
                            const amountBadge = meta.amount_eur ? `<span style="background: #1f2335; border: 1px solid var(--accent-green); border-radius: 3px; padding: 1px 6px; font-size: 0.75em; color: var(--accent-green);">${meta.amount_eur} €</span>` : '';

                            return `
                            <div style="background: var(--bg-dark); border: 1px solid var(--border); border-radius: 5px; padding: 14px; display: flex; flex-direction: column; gap: 8px;">
                                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap;">
                                    <div>
                                        <div style="font-size: 0.82em; color: #888;">Empfänger:</div>
                                        <div style="font-weight: bold; color: var(--accent-blue); font-size: 0.95em;">${escapeHtml(item.recipient)}</div>
                                    </div>
                                    <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                                        ${docRef}
                                        ${azBadge}
                                        ${amountBadge}
                                        <span style="font-size: 0.75em; color: #777;">${dateStr}</span>
                                    </div>
                                </div>
                                <div>
                                    <div style="font-size: 0.82em; color: #888;">Betreff:</div>
                                    <div style="font-weight: bold; color: #fff; font-size: 0.9em;">${escapeHtml(item.subject)}</div>
                                </div>
                                <div>
                                    <div style="font-size: 0.82em; color: #888; margin-bottom: 4px;">Vorschau E-Mail-Text:</div>
                                    <pre style="margin: 0; padding: 10px; background: #000; border: 1px solid #222; border-radius: 4px; font-size: 0.82em; color: #cfcde6; white-space: pre-wrap; word-break: break-word; max-height: 120px; overflow-y: auto; font-family: monospace;">${escapeHtml(item.body)}</pre>
                                </div>
                                <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 10px;">
                                    <button class="btn" style="width: auto; margin: 0; padding: 6px 12px; font-size: 0.82em; border-color: var(--accent-red); color: var(--accent-red);" onclick="cancelOutboxDraft(${item.id})">🗑 Verwerfen</button>
                                    <button class="btn" style="width: auto; margin: 0; padding: 6px 14px; font-size: 0.82em; border-color: var(--accent-blue); color: var(--accent-blue);" onclick="openDraftEditModal(${item.id})">✏️ Bearbeiten</button>
                                    <button class="btn" style="width: auto; margin: 0; padding: 6px 16px; font-size: 0.82em; border-color: var(--accent-green); color: var(--accent-green); font-weight: bold;" onclick="sendOutboxDraft(${item.id}, this)">✅ Absenden</button>
                                </div>
                            </div>`;
                        }).join('');
                    }
                }

                // Tab 2: Gesendete Protokolle rendern
                const sentContainer = document.getElementById('outbox-sent-container');
                if (sentContainer) {
                    if (sent.length === 0) {
                        sentContainer.innerHTML = `
                            <div style="background: rgba(0,0,0,0.3); border: 1px dashed var(--border); border-radius: 6px; padding: 30px; text-align: center; color: #888;">
                                <div style="font-size: 1.8em; margin-bottom: 8px;">📬</div>
                                <div style="font-weight: bold; color: #aaa;">Noch keine versendeten E-Mails protokolliert.</div>
                            </div>`;
                    } else {
                        sentContainer.innerHTML = sent.map(item => {
                            const dateStr = item.sent_at ? new Date(item.sent_at).toLocaleString('de-DE') : (item.created_at ? new Date(item.created_at).toLocaleString('de-DE') : '-');
                            return `
                            <div style="background: var(--bg-dark); border: 1px solid rgba(158, 206, 106, 0.3); border-radius: 5px; padding: 14px; display: flex; flex-direction: column; gap: 8px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                                    <div style="display: flex; align-items: center; gap: 8px;">
                                        <span style="background: rgba(158, 206, 106, 0.15); border: 1px solid var(--accent-green); color: var(--accent-green); padding: 2px 8px; border-radius: 3px; font-size: 0.78em; font-weight: bold;">[✓ GESENDET]</span>
                                        <span style="color: #bbb; font-size: 0.85em;">an <strong>${escapeHtml(item.recipient)}</strong></span>
                                    </div>
                                    <span style="font-size: 0.78em; color: #888;">Gesendet am: ${dateStr}</span>
                                </div>
                                <div>
                                    <div style="font-weight: bold; color: #fff; font-size: 0.9em;">${escapeHtml(item.subject)}</div>
                                </div>
                                <details style="background: rgba(0,0,0,0.4); border: 1px solid #222; border-radius: 4px; padding: 6px 10px;">
                                    <summary style="cursor: pointer; font-size: 0.8em; color: #888; user-select: none;">Vollständigen E-Mail-Text anzeigen</summary>
                                    <pre style="margin: 8px 0 0 0; font-size: 0.82em; color: #9aa5ce; white-space: pre-wrap; word-break: break-word; font-family: monospace;">${escapeHtml(item.body)}</pre>
                                </details>
                            </div>`;
                        }).join('');
                    }
                }

            } catch (e) {
                console.error("Fehler beim Laden der Outbox:", e);
            }
        }

        async function sendOutboxDraft(draftId, btnEl) {
            if (btnEl) {
                btnEl.disabled = true;
                btnEl.innerText = "Sende...";
            }
            const statusMsg = document.getElementById('outbox-status-msg');
            if (statusMsg) {
                statusMsg.style.display = 'block';
                statusMsg.innerHTML = `<span style="color: var(--accent-blue);">[✉️] Versende E-Mail #${draftId} via SMTP...</span>`;
            }

            try {
                const res = await fetch(`/api/email/send/${draftId}`, { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    if (statusMsg) {
                        statusMsg.innerHTML = `<span style="color: var(--accent-green);">[✓] ${data.message || 'E-Mail erfolgreich versendet!'}</span>`;
                    }
                    await loadOutboxData();
                    setTimeout(() => { if (statusMsg) statusMsg.style.display = 'none'; }, 3000);
                } else {
                    if (statusMsg) {
                        statusMsg.innerHTML = `<span style="color: var(--accent-red);">[!] Fehler: ${data.detail || data.message || 'Versand fehlgeschlagen'}</span>`;
                    }
                    if (btnEl) {
                        btnEl.disabled = false;
                        btnEl.innerText = "✅ Absenden";
                    }
                }
            } catch (e) {
                if (statusMsg) {
                    statusMsg.innerHTML = `<span style="color: var(--accent-red);">[!] Netzwerkfehler beim Versand: ${e.message}</span>`;
                }
                if (btnEl) {
                    btnEl.disabled = false;
                    btnEl.innerText = "✅ Absenden";
                }
            }
        }

        async function cancelOutboxDraft(draftId) {
            if (!confirm(`Möchtest du den Entwurf #${draftId} wirklich verwerfen?`)) return;
            try {
                const res = await fetch(`/api/email/outbox/${draftId}`, { method: 'DELETE' });
                if (res.ok) {
                    await loadOutboxData();
                }
            } catch (e) {
                console.error("Fehler beim Verwerfen des Entwurfs:", e);
            }
        }

        async function openDraftEditModal(draftId) {
            currentEditingDraftId = draftId;
            document.getElementById('edit-draft-status').innerText = "";
            try {
                const res = await fetch(`/api/email/outbox/${draftId}`);
                if (!res.ok) return;
                const item = await res.json();
                document.getElementById('edit-draft-id').value = item.id;
                document.getElementById('edit-draft-recipient').value = item.recipient || '';
                document.getElementById('edit-draft-subject').value = item.subject || '';
                document.getElementById('edit-draft-body').value = item.body || '';
                document.getElementById('draft-edit-modal').style.display = 'flex';
            } catch (e) {
                console.error("Konnte Entwurf nicht laden:", e);
            }
        }

        function closeDraftEditModal() {
            document.getElementById('draft-edit-modal').style.display = 'none';
            currentEditingDraftId = null;
        }

        async function submitDraftEdit(andSend = false) {
            const draftId = document.getElementById('edit-draft-id').value;
            const recipient = document.getElementById('edit-draft-recipient').value.trim();
            const subject = document.getElementById('edit-draft-subject').value.trim();
            const body = document.getElementById('edit-draft-body').value.trim();
            const statusDiv = document.getElementById('edit-draft-status');

            if (!recipient) {
                statusDiv.innerHTML = `<span style="color: var(--accent-red);">[!] Bitte Empfänger angeben.</span>`;
                return;
            }

            statusDiv.innerHTML = `<span style="color: var(--accent-blue);">[Speichern...]</span>`;

            try {
                const res = await fetch(`/api/email/outbox/${draftId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ recipient, subject, body })
                });

                if (res.ok) {
                    closeDraftEditModal();
                    await loadOutboxData();
                    if (andSend) {
                        await sendOutboxDraft(draftId);
                    }
                } else {
                    const err = await res.json().catch(() => ({}));
                    statusDiv.innerHTML = `<span style="color: var(--accent-red);">[!] Fehler: ${err.detail || 'Speichern fehlgeschlagen'}</span>`;
                }
            } catch (e) {
                statusDiv.innerHTML = `<span style="color: var(--accent-red);">[!] Netzwerkfehler: ${e.message}</span>`;
            }
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        // --- Dropdown-Menü & Overlay-Handling ---
        function toggleClusterMenu(event) {
            if (event) event.stopPropagation();
            const menu = document.getElementById('cluster-dropdown-menu');
            if (menu) menu.classList.toggle('show');
        }

        function closeClusterMenu() {
            const menu = document.getElementById('cluster-dropdown-menu');
            if (menu) menu.classList.remove('show');
        }

        // Klick außerhalb schließt Dropdowns und Modals
        window.addEventListener('click', function (e) {
            // Dropdown schließen
            const dropdown = document.getElementById('cluster-dropdown-menu');
            const menuBtn = document.getElementById('cluster-menu-btn');
            if (dropdown && dropdown.classList.contains('show')) {
                if (!dropdown.contains(e.target) && (!menuBtn || !menuBtn.contains(e.target))) {
                    closeClusterMenu();
                }
            }

            // Modals schließen beim Klick auf Overlay-Hintergrund
            if (e.target.classList && e.target.classList.contains('modal-overlay')) {
                e.target.classList.remove('active');
            }
            if (e.target.id === 'skills-modal') {
                closeSkillsModal();
            }
            if (e.target.id === 'skill-creator-modal') {
                closeSkillCreatorModal();
            }
            if (e.target.id === 'outbox-modal') {
                closeOutboxModal();
            }
            if (e.target.id === 'draft-edit-modal') {
                closeDraftEditModal();
            }
        });

        // ESC-Taste schließt alle offenen Overlays
        window.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                closeNodeCreatorModal();
                closeModelTransferModal();
                closeSkillsModal();
                closeSkillCreatorModal();
                closeModfileEditor();
                closeClusterMenu();
                closeOutboxModal();
                closeDraftEditModal();
            }
        });

        // =====================================================================
        // PSD BANK (READ-ONLY) & FINANCIAL REPORT
        // =====================================================================
        async function loadBankingBalance() {
            try {
                const res = await fetch('/api/banking/balance');
                if (!res.ok) return;
                const data = await res.json();
                const bal = parseFloat(data.balance_eur || 0).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                const elSide = document.getElementById('psd-balance-display');
                if (elSide) elSide.innerText = `${bal} ${data.currency || 'EUR'}`;
                const elTime = document.getElementById('psd-balance-timestamp');
                if (elTime && data.timestamp) {
                    elTime.innerText = `Stand: ${new Date(data.timestamp).toLocaleTimeString('de-DE')}`;
                }
                const elHead = document.getElementById('header-balance-val');
                if (elHead) elHead.innerText = `${bal} €`;
            } catch (e) {
                console.error("Fehler beim Laden des PSD-Saldos:", e);
            }
        }

        async function triggerBankingReport() {
            const elSide = document.getElementById('psd-balance-display');
            if (elSide) elSide.innerText = "Erstelle Report...";
            try {
                const res = await fetch('/api/banking/report/trigger', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    alert(`[✓] Finanzbericht erfolgreich generiert & versendet!\n\n${data.message}`);
                }
                await loadBankingBalance();
            } catch (e) {
                alert(`Fehler: ${e.message}`);
                await loadBankingBalance();
            }
        }

        // Initialisierung
        loadClusterNodes();
        loadSkills();
        loadOutboxData();
        loadBankingBalance();
        setInterval(() => {
            refreshSilently();
            loadOutboxData();
            loadBankingBalance();
        }, 20000);
    </script>
</body>
</html>
"""

# --- API Endpunkte ---
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_TEMPLATE

@app.get("/api/health")
@app.get("/health")
def health():
    return {"status": "ok", "service": "hermes-agent-kontrollzentrum", "port": PORT}

@app.get("/api/nodes")
def get_cluster_nodes():
    """Prüft alle konfigurierten Cluster-Nodes parallel auf Ollama, VRAM (/api/ps), Agent-Status und Modelle."""
    nodes = load_cluster_nodes()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes) or 2) as executor:
        futures = {executor.submit(check_node_status, node): node for node in nodes}
        for future in futures:
            try:
                results.append(future.result())
            except Exception as e:
                node = futures[future]
                results.append({
                    "id": node.get("id"),
                    "name": node.get("name"),
                    "host": node.get("host", ""),
                    "tailscale_ip": node.get("tailscale_ip", ""),
                    "description": node.get("description", ""),
                    "ollama_online": False,
                    "agent_online": False,
                    "agent_info": "Fehler beim Check",
                    "active_ollama_url": None,
                    "vram_status": {"active_model": None, "size_vram": 0, "size_vram_formatted": "0 GB VRAM", "loaded_models": []},
                    "models": []
                })
    node_order = {node["id"]: i for i, node in enumerate(nodes)}
    results.sort(key=lambda x: node_order.get(x["id"], 999))
    return {
        "nodes": results,
        "default_node": results[0]["id"] if results else "outerheaven"
    }

@app.get("/api/nodes/{node_id}")
def get_single_node(node_id: str):
    node = get_node_by_id(node_id)
    return check_node_status(node)

@app.get("/api/models")
def get_models(node: Optional[str] = "outerheaven"):
    try:
        node_conf = get_node_by_id(node)
        url = get_working_ollama_url(node_conf)
        r = requests.get(f"{url}/api/tags", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/api/modfile/{node_id}/{model_name:path}")
def get_node_modfile(node_id: str, model_name: str):
    try:
        node_conf = get_node_by_id(node_id)
        url = get_working_ollama_url(node_conf)
        r = requests.post(f"{url}/api/show", json={"name": model_name}, timeout=5)
        r.raise_for_status()
        return {"modelfile": r.json().get("modelfile", ""), "node": node_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/modfile/{model_name:path}")
def get_modfile(model_name: str):
    return get_node_modfile("outerheaven", model_name)

@app.post("/api/build")
def build_model(req: BuildRequest):
    try:
        node_id = req.node or "outerheaven"
        node_conf = get_node_by_id(node_id)
        url = get_working_ollama_url(node_conf)
        r = requests.post(f"{url}/api/create", json={"name": req.name, "modelfile": req.modelfile}, timeout=120)
        r.raise_for_status()
        return {"status": "success", "node": node_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat(req: ChatRequest):
    reminder_ack = ""
    if hermes_reminder is not None:
        try:
            intent = hermes_reminder.extract_reminder_intent(req.prompt)
            if intent:
                item = hermes_reminder.add_reminder(
                    task_description=intent["task_description"],
                    due_time=intent["due_time"],
                    push_notify=True,
                    speaker_broadcast=False,
                    raw_prompt=req.prompt
                )
                reminder_ack = (
                    f"\n\n📅 [Lebensplan]: Ich habe deine Erinnerung für {intent['formatted_datetime']} vermerkt: "
                    f"»{intent['task_description']}« (Push-Alarmierung aktiv)."
                )
        except Exception as _e_rem:
            print(f"Warnung bei Reminder-Erkennung: {_e_rem}")

    try:
        node_id = req.node or "outerheaven"
        node_conf = get_node_by_id(node_id)
        url = get_working_ollama_url(node_conf)
        r = requests.post(f"{url}/api/generate", json={"model": req.model, "prompt": req.prompt, "stream": False}, timeout=120)
        r.raise_for_status()
        resp_text = r.json().get("response", "")
        if reminder_ack:
            resp_text += reminder_ack
        return {"response": resp_text, "node": node_id, "reminder_detected": bool(reminder_ack)}
    except Exception as e:
        if reminder_ack:
            return {
                "response": f"Ich habe dein Vorhaben im Lebensplan gesichert!{reminder_ack}",
                "node": req.node or "outerheaven",
                "reminder_detected": True
            }
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/nodes/create")
def create_node(req: NodeCreateRequest):
    """Registriert einen neuen Cluster-Knoten persistent in cluster_nodes.json."""
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", req.id.strip().lower())
    clean_name = req.name.strip()
    clean_host = req.host.strip()

    if not clean_id:
        raise HTTPException(status_code=400, detail="Ungültige oder leere Node-ID.")
    if not clean_name or not clean_host:
        raise HTTPException(status_code=400, detail="Name und Host dürfen nicht leer sein.")

    nodes = load_cluster_nodes()
    for n in nodes:
        if n.get("id") == clean_id:
            raise HTTPException(status_code=400, detail=f"Knoten '{clean_id}' existiert bereits.")

    ollama_urls = req.ollama_urls or [f"http://{clean_host}:11434"]
    if req.tailscale_ip and req.tailscale_ip.strip():
        ts_ip = req.tailscale_ip.strip()
        if f"http://{ts_ip}:11434" not in ollama_urls:
            ollama_urls.append(f"http://{ts_ip}:11434")

    agent_urls = req.agent_urls or [f"http://{clean_host}:5005/"]

    new_node = {
        "id": clean_id,
        "name": clean_name,
        "host": clean_host,
        "tailscale_ip": (req.tailscale_ip or "").strip(),
        "ollama_urls": ollama_urls,
        "agent_urls": agent_urls,
        "description": (req.description or "").strip()
    }

    nodes.append(new_node)
    save_cluster_nodes(nodes)
    return {"status": "success", "node": new_node}

@app.post("/api/models/transfer")
def transfer_model(req: ModelTransferRequest):
    """
    Kopiert ein Modell zwischen zwei Nodes im Cluster:
    1. Holt das Modelfile von der Quell-Instanz via /api/show
    2. Ermittelt Manifest- und Blob-Dateien
    3. Kopiert Manifest und Blobs via scp/rsync (schnell, ohne Rekompilierung)
    4. Fallback auf /api/create auf dem Ziel-Node
    """
    src_node = get_node_by_id(req.source_node)
    tgt_node = get_node_by_id(req.target_node)
    model = req.model_name.strip()

    if req.source_node == req.target_node:
        raise HTTPException(status_code=400, detail="Quell- und Zielknoten sind identisch.")

    src_url = get_working_ollama_url(src_node)
    tgt_url = get_working_ollama_url(tgt_node)

    # 1. Prüfe Existenz auf Quelle
    try:
        r_show = requests.post(f"{src_url}/api/show", json={"name": model}, timeout=10)
        r_show.raise_for_status()
        show_data = r_show.json()
        modelfile_content = show_data.get("modelfile", "")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Modell '{model}' auf Quelle '{req.source_node}' nicht erreichbar: {e}")

    # 2. Versuch 1: Fast Transfer via Manifest & Blob Kopie
    model_tag = model.split(":")[-1] if ":" in model else "latest"
    model_base = model.split(":")[0]
    clean_base = model_base.split("/")[-1]

    def get_ollama_paths(node_id):
        if node_id == "outerheaven":
            return ["/mnt/storage-alpha/ollama_models", "/usr/share/ollama/.ollama/models"]
        return ["/home/fox/.ollama", "/home/fox/.ollama/models"]

    src_root = get_ollama_paths(req.source_node)
    tgt_root = get_ollama_paths(req.target_node)

    transfer_success = False
    details = ""

    try:
        if req.source_node == "outerheaven" and req.target_node == "fox":
            # Suche Manifest auf Outer Heaven
            manifest_rel = f"manifests/registry.ollama.ai/library/{clean_base}/{model_tag}"
            manifest_local = None
            for p in src_root:
                candidate = f"{p}/{manifest_rel}"
                if os.path.exists(candidate):
                    manifest_local = candidate
                    break
            
            if manifest_local and os.path.exists(manifest_local):
                with open(manifest_local, "r", encoding="utf-8") as mf:
                    manifest_data = json.load(mf)
                
                # Fox Zielpfad ist /home/fox/.ollama
                fox_target = tgt_root[0]
                subprocess.run(["ssh", "fox", f"mkdir -p {fox_target}/manifests/registry.ollama.ai/library/{clean_base} {fox_target}/blobs"], check=True, timeout=10)
                subprocess.run(["scp", manifest_local, f"fox:{fox_target}/{manifest_rel}"], check=True, timeout=15)
                
                blobs_to_copy = []
                for layer in manifest_data.get("layers", []):
                    digest = layer.get("digest", "").replace(":", "-")
                    if digest:
                        blobs_to_copy.append(digest)
                cfg_digest = manifest_data.get("config", {}).get("digest", "").replace(":", "-")
                if cfg_digest:
                    blobs_to_copy.append(cfg_digest)

                for b in set(blobs_to_copy):
                    # Finde Blob lokal
                    local_blob = None
                    for p in src_root:
                        if os.path.exists(f"{p}/blobs/{b}"):
                            local_blob = f"{p}/blobs/{b}"
                            break
                    if local_blob:
                        chk = subprocess.run(["ssh", "fox", f"test -f {fox_target}/blobs/{b}"], timeout=5)
                        if chk.returncode != 0:
                            subprocess.run(["scp", local_blob, f"fox:{fox_target}/blobs/{b}"], check=True, timeout=300)
                transfer_success = True
                details = f"Blobs & Manifest direkt nach Fox synchronisiert."

        elif req.source_node == "fox" and req.target_node == "outerheaven":
            manifest_rel = f"manifests/registry.ollama.ai/library/{clean_base}/{model_tag}"
            oh_target = tgt_root[0] # /mnt/storage-alpha/ollama_models
            tgt_manifest_dir = f"{oh_target}/manifests/registry.ollama.ai/library/{clean_base}"
            os.makedirs(tgt_manifest_dir, exist_ok=True)
            os.makedirs(f"{oh_target}/blobs", exist_ok=True)

            # Finde Manifest auf Fox
            fox_src = src_root[0] # /home/fox/.ollama
            manifest_found = False
            for p in src_root:
                chk = subprocess.run(["ssh", "fox", f"test -f {p}/{manifest_rel}"], timeout=5)
                if chk.returncode == 0:
                    subprocess.run(["scp", f"fox:{p}/{manifest_rel}", f"{oh_target}/{manifest_rel}"], check=True, timeout=15)
                    fox_src = p
                    manifest_found = True
                    break
            
            if manifest_found and os.path.exists(f"{oh_target}/{manifest_rel}"):
                with open(f"{oh_target}/{manifest_rel}", "r", encoding="utf-8") as mf:
                    manifest_data = json.load(mf)

                blobs_to_copy = []
                for layer in manifest_data.get("layers", []):
                    digest = layer.get("digest", "").replace(":", "-")
                    if digest:
                        blobs_to_copy.append(digest)
                cfg_digest = manifest_data.get("config", {}).get("digest", "").replace(":", "-")
                if cfg_digest:
                    blobs_to_copy.append(cfg_digest)

                for b in set(blobs_to_copy):
                    local_blob = f"{oh_target}/blobs/{b}"
                    if not os.path.exists(local_blob):
                        # Suche Blob auf Fox in beiden Pfaden
                        copied = False
                        for p in src_root:
                            chk = subprocess.run(["ssh", "fox", f"test -f {p}/blobs/{b}"], timeout=5)
                            if chk.returncode == 0:
                                subprocess.run(["scp", f"fox:{p}/blobs/{b}", local_blob], check=True, timeout=300)
                                copied = True
                                break
                transfer_success = True
                details = f"Blobs & Manifest direkt von Fox auf Outer Heaven synchronisiert."
    except Exception as copy_err:
        print(f"Direkt-Transfer fehlgeschlagen ({copy_err}), nutze API-Fallback...")

    # 3. Fallback auf API /api/create
    if not transfer_success:
        if not modelfile_content:
            raise HTTPException(status_code=500, detail="Konnte weder Blobs kopieren noch Modelfile für API-Build extrahieren.")
        try:
            r_create = requests.post(
                f"{tgt_url}/api/create",
                json={"name": model, "modelfile": modelfile_content},
                timeout=300
            )
            r_create.raise_for_status()
            transfer_success = True
            details = f"Erfolgreich via /api/create auf {tgt_node.get('name')} registriert."
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Transfer fehlgeschlagen: {e}")

    return {
        "status": "success",
        "message": f"Modell '{model}' erfolgreich von '{req.source_node}' nach '{req.target_node}' übertragen! {details}",
        "source": req.source_node,
        "target": req.target_node,
        "model": model
    }

@app.get("/api/skills")
def get_skills():
    """Gibt die Liste aller registrierten Hermes-Skills zurück."""
    return {"skills": load_skills_registry()}

@app.post("/api/skills/toggle")
def toggle_skill(req: SkillToggleRequest):
    """Schaltet einen Skill aktiv oder inaktiv und speichert den Zustand dauerhaft."""
    skills = load_skills_registry()
    for s in skills:
        if s["id"] == req.skill_id:
            s["enabled"] = req.enabled
            save_skills_registry(skills)
            return {"status": "success", "skill": s}
    raise HTTPException(status_code=404, detail="Skill nicht gefunden")

@app.post("/api/skills/create")
def create_skill(req: SkillCreateRequest):
    """
    Erstellt einen neuen Skill:
    - Wandelt die Angaben in ein Function-Calling-Schema (name, description, parameters) um
    - Speichert den Skill dauerhaft in skills_registry.json
    - Gibt den neu registrierten Skill zurück
    """
    clean_name = req.name.strip()
    clean_desc = req.description.strip()
    
    if not clean_name:
        raise HTTPException(status_code=400, detail="Skill-Name darf nicht leer sein.")
    if not clean_desc:
        raise HTTPException(status_code=400, detail="Beschreibung / Trigger-Bedingung darf nicht leer sein.")

    skills = load_skills_registry()
    clean_id = re.sub(r'[^a-zA-Z0-9_]', '_', clean_name.lower())
    
    for s in skills:
        if s["id"] == clean_id:
            raise HTTPException(status_code=400, detail=f"Skill '{clean_id}' existiert bereits.")

    func_schema = build_function_schema(clean_name, clean_desc, req.parameters or "")
    parsed_params = [p.strip() for p in (req.parameters or "").split(",") if p.strip()]

    handler_source = f"custom:{req.handler_type.lower().replace(' ', '_')}"
    handler_target = (req.handler_target or "").strip()
    if req.handler_type == "Lebensplan & Smart Reminder":
        handler_source = "local:OuterHeaven/hermes_reminder.py"
        if not handler_target:
            handler_target = "local:OuterHeaven/lebensplan.json"

    new_skill = {
        "id": clean_id,
        "name": clean_name,
        "description": clean_desc,
        "category": f"{req.handler_type}",
        "enabled": True,
        "source": handler_source,
        "handler": {
            "type": req.handler_type,
            "target": handler_target,
            "action_mode": (req.action_mode or "draft").strip(),
            "push_notify": req.push_notify if req.push_notify is not None else True,
            "speaker_broadcast": req.speaker_broadcast if req.speaker_broadcast is not None else False
        },
        "parameters": parsed_params,
        "function_schema": func_schema
    }

    skills.append(new_skill)
    save_skills_registry(skills)

    return {"status": "success", "skill": new_skill}

# --- Lebensplan & Smart Reminder APIs ---
@app.get("/api/lebensplan")
def get_lebensplan_items(status: Optional[str] = None):
    """Gibt alle Einträge aus lebensplan.json zurück."""
    if hermes_reminder is None:
        return {"items": []}
    return {"items": hermes_reminder.get_reminders(status_filter=status)}

@app.post("/api/lebensplan/add")
def add_lebensplan_item(req: ReminderCreateRequest):
    """Erstellt manuell oder programmatisch einen neuen Reminder im Lebensplan."""
    if hermes_reminder is None:
        raise HTTPException(status_code=500, detail="hermes_reminder Modul nicht verfügbar")
    try:
        item = hermes_reminder.add_reminder(
            task_description=req.task,
            due_time=req.due_time,
            push_notify=req.push_notify if req.push_notify is not None else True,
            speaker_broadcast=req.speaker_broadcast if req.speaker_broadcast is not None else False
        )
        return {"status": "success", "item": item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/lebensplan/{reminder_id}")
def delete_lebensplan_item(reminder_id: str):
    """Löscht einen Eintrag aus lebensplan.json."""
    if hermes_reminder is None:
        raise HTTPException(status_code=500, detail="hermes_reminder Modul nicht verfügbar")
    success = hermes_reminder.delete_reminder(reminder_id)
    if not success:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    return {"status": "success", "id": reminder_id}

@app.post("/api/smart_speaker/test")
def test_smart_speaker(text: Optional[str] = Query("Test-Sprachausgabe von Hermes")):
    """Testet die Smart-Speaker Schnittstelle unter Berücksichtigung von SPEAKER_ENABLED."""
    if hermes_reminder is None:
        raise HTTPException(status_code=500, detail="hermes_reminder Modul nicht verfügbar")
    res = hermes_reminder.dispatch_to_smart_speaker(text)
    return res

# --- E-Mail Outbox & Dispatch APIs ---
@app.get("/api/email/outbox")
def get_email_outbox(status: Optional[str] = None, limit: int = 50):
    """Gibt alle Outbox-Einträge (Entwürfe und gesendete E-Mails) zurück."""
    if hermes_email_handler is None:
        return {"emails": []}
    return {"emails": hermes_email_handler.list_outbox(status_filter=status, limit=limit)}

@app.get("/api/email/outbox/{item_id}")
def get_email_draft(item_id: int):
    """Gibt einen einzelnen Outbox-Eintrag zurück."""
    if hermes_email_handler is None:
        raise HTTPException(status_code=500, detail="E-Mail Modul nicht verfügbar")
    item = hermes_email_handler.get_outbox_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"E-Mail #{item_id} nicht gefunden")
    return item

@app.post("/api/email/send/{item_id}")
def send_email_draft(item_id: int):
    """Gibt einen bestehenden Entwurf frei und versendet ihn über SMTP."""
    if hermes_email_handler is None:
        raise HTTPException(status_code=500, detail="E-Mail Modul nicht verfügbar")
    success, msg, item = hermes_email_handler.send_existing_draft(item_id)
    if not success and not item:
        raise HTTPException(status_code=404, detail=msg)
    return {"status": "success" if success else "failed", "message": msg, "item": item}

@app.put("/api/email/outbox/{item_id}")
def update_email_draft(item_id: int, req: EmailDraftUpdateRequest):
    """Aktualisiert Empfänger, Betreff oder Text eines Entwurfs."""
    if hermes_email_handler is None:
        raise HTTPException(status_code=500, detail="E-Mail Modul nicht verfügbar")
    updated = hermes_email_handler.update_existing_draft(
        draft_id=item_id,
        recipient=req.recipient,
        subject=req.subject,
        body=req.body
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Entwurf #{item_id} nicht gefunden")
    return {"status": "success", "item": updated}

@app.delete("/api/email/outbox/{item_id}")
def delete_email_draft(item_id: int):
    """Löscht oder verwirft einen Entwurf."""
    if hermes_email_handler is None:
        raise HTTPException(status_code=500, detail="E-Mail Modul nicht verfügbar")
    deleted = hermes_email_handler.cancel_existing_draft(item_id, delete_permanent=True)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Entwurf #{item_id} nicht gefunden")
    return {"status": "success", "deleted_id": item_id}

@app.post("/api/email/generate")
def generate_email(req: EmailGenerateRequest):
    """
    Formuliert via LLM (megan:hermes) eine autonome deeskalierende E-Mail
    und speichert sie als Entwurf oder versendet sie direkt.
    """
    if hermes_email_handler is None:
        raise HTTPException(status_code=500, detail="E-Mail Modul nicht verfügbar")

    generated = hermes_email_handler.generate_autonomous_email_text(
        correspondent=req.correspondent,
        aktenzeichen=req.aktenzeichen or "",
        betrag=req.betrag,
        due_date=req.due_date,
        doc_summary=req.doc_summary or "",
        doc_type=req.doc_type or "Rechnung",
        model=os.getenv("HERMES_EMAIL_MODEL", "megan:hermes")
    )

    action = (req.action_mode or "draft").lower()
    is_auto = (action == "auto_send")
    recipient = req.recipient_email or f"kontakt@{req.correspondent.lower().replace(' ', '')}.de"

    record = hermes_email_handler.create_email_record(
        recipient=recipient,
        subject=generated["subject"],
        body=generated["body"],
        is_draft=not is_auto,
        document_id=req.document_id,
        metadata={
            "correspondent": req.correspondent,
            "aktenzeichen": req.aktenzeichen,
            "amount_eur": req.betrag,
            "document_type": req.doc_type,
            "action_mode": action
        },
        auto_send=is_auto
    )

    return {
        "status": "success",
        "action": action,
        "email": record
    }

# --- FinTS Banking & Weekly Friday Report Scheduler ---
def run_weekly_banking_report(force: bool = False) -> Dict[str, Any]:
    """
    Generiert einen proaktiven Finanz- und Rechnungsbericht (z. B. jeden Freitagmittag)
    unter Verwendung von megan:hermes und fints_connector.
    """
    if fints_connector is None:
        return {"status": "error", "message": "fints_connector nicht verfügbar"}

    balance_info = fints_connector.get_current_balance()
    balance_eur = float(balance_info.get("balance_eur", 0.0))

    # Offene Posten ermitteln
    drafts = []
    if hermes_email_handler:
        try:
            drafts = hermes_email_handler.list_outbox(status_filter="draft")
        except Exception:
            drafts = []

    open_count = len(drafts)
    total_open_amount = sum(float(d.get("metadata", {}).get("amount_eur") or 0.0) for d in drafts)

    # Standardisierte Benachrichtigung gemäß Vorgabe:
    # "Boss, die neue Rechnungsübersicht ist fertig. Dein aktueller Kontostand bei der PSD Bank beträgt X.XX €. Es liegen [X] offene Posten an."
    msg = (
        f"Boss, die neue Rechnungsübersicht ist fertig. "
        f"Dein aktueller Kontostand bei der PSD Bank beträgt {balance_eur:.2f} €. "
        f"Es liegen {open_count} offene Posten an."
    )
    if total_open_amount > 0:
        msg += f" (Gesamtsumme: {total_open_amount:.2f} €)"

    fints_connector.dispatch_proactive_notification(msg, title="Hermes Wochenbericht")
    return {
        "status": "success",
        "message": msg,
        "balance_eur": balance_eur,
        "open_count": open_count,
        "total_open_amount": total_open_amount,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

def background_friday_scheduler():
    """Prüft im Hintergrund, ob Freitag 12:00 Uhr erreicht ist und triggert den Bericht."""
    last_reported_week = None
    while True:
        try:
            now = datetime.now()
            # Freitag ist weekday() == 4
            current_week = now.isocalendar()[1]
            if now.weekday() == 4 and now.hour == 12 and (last_reported_week != current_week):
                run_weekly_banking_report()
                last_reported_week = current_week
        except Exception:
            pass
        time.sleep(60)

# Starte Scheduler Daemon-Thread (nur im Standalone-Server-Modus, nicht in Unittests)
if "unittest" not in sys.modules:
    scheduler_thread = threading.Thread(target=background_friday_scheduler, daemon=True)
    scheduler_thread.start()

@app.get("/api/banking/balance")
def get_banking_balance():
    """Gibt den aktuellen Saldo der PSD Bank Hessen-Thüringen zurück (Read-Only)."""
    if fints_connector is None:
        raise HTTPException(status_code=500, detail="Banking-Modul nicht verfügbar")
    return fints_connector.get_current_balance()

@app.post("/api/banking/report/trigger")
def trigger_banking_report_endpoint():
    """Triggert sofort den proaktiven Finanzstatus-Report."""
    result = run_weekly_banking_report(force=True)
    return result

# --- E-Mail Review & Ingest APIs ---
@app.get("/api/email_review")
def get_email_reviews(limit: int = 50):
    """Liefert alle unklaren E-Mails/Dokumente an die PaperworkScanner-App."""
    if hermes_email_ingest is None:
        return {"items": [], "count": 0}
    items = hermes_email_ingest.list_pending_reviews(limit=limit)
    return {"items": items, "count": len(items)}

@app.post("/api/email_review/{item_id}/action")
def take_email_review_action(item_id: int, req: EmailReviewActionRequest):
    """Nimmt die User-Entscheidung aus der App entgegen (DELETE oder SEND_TO_PAPERLESS)."""
    if hermes_email_ingest is None:
        raise HTTPException(status_code=500, detail="E-Mail Ingest Modul nicht verfügbar")
    success, msg = hermes_email_ingest.resolve_review_item(
        item_id=item_id,
        action=req.action,
        custom_title=req.title
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg, "id": item_id, "action": req.action}

@app.post("/api/email_review/poll")
def trigger_email_poll():
    """Triggert manuell einen sofortigen IMAP-Abruf und KI-Triage."""
    if hermes_email_ingest is None:
        raise HTTPException(status_code=500, detail="E-Mail Ingest Modul nicht verfügbar")
    count = hermes_email_ingest.get_ingest_worker().poll_once()
    return {"status": "success", "processed_emails": count}

# Starte E-Mail Ingest Worker (nur im Standalone-Server-Modus, nicht in Unittests)
if hermes_email_ingest and "unittest" not in sys.modules:
    try:
        hermes_email_ingest.init_ingest_db()
        hermes_email_ingest.get_ingest_worker().start()
    except Exception as e:
        print(f"Warnung: E-Mail Ingest Worker konnte nicht gestartet werden: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
