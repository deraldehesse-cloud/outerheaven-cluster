import os
import json
import re
import requests
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Hermes Agent Kontrollzentrum")
PORT = int(os.getenv("PORT", "8081"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")

# Pfad zur persistenten JSON-Konfigurationsdatei für Skills
SKILLS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_registry.json")

# --- Datenmodelle ---
class ChatRequest(BaseModel):
    model: str
    prompt: str

class BuildRequest(BaseModel):
    name: str
    modelfile: str

class SkillToggleRequest(BaseModel):
    skill_id: str
    enabled: bool

class SkillCreateRequest(BaseModel):
    name: str
    description: str
    parameters: Optional[str] = ""
    handler_type: Optional[str] = "Desktop-Notification"
    handler_target: Optional[str] = ""

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
    """Lädt Skills aus skills_registry.json oder initialisiert mit DEFAULT_SKILLS."""
    if os.path.exists(SKILLS_FILE):
        try:
            with open(SKILLS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warnung: Konnte {SKILLS_FILE} nicht laden: {e}")
    # Initialisiere und speichere Default-Skills
    save_skills_registry(DEFAULT_SKILLS)
    return list(DEFAULT_SKILLS)

def save_skills_registry(skills: list):
    """Speichert Skills dauerhaft in skills_registry.json."""
    try:
        with open(SKILLS_FILE, "w", encoding="utf-8") as f:
            json.dump(skills, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Fehler beim Speichern von {SKILLS_FILE}: {e}")

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
        .sidebar-section { margin-bottom: 25px; }
        .sidebar-section h3 {
            color: var(--accent-blue);
            font-size: 0.9em;
            text-transform: uppercase;
            margin-bottom: 10px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 5px;
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
        
        /* Modelfile View Panel */
        #modfile-view-panel {
            background: #0a0d14;
            border-bottom: 1px solid var(--border);
            padding: 10px 20px;
            display: flex;
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

        #mobile-menu-btn { display: none; }
        #sidebar-backdrop { display: none; }

        @media (max-width: 900px) {
            html, body {
                width: 100%;
                height: 100dvh;
                overflow: hidden;
                font-size: 16px;
            }
            input, textarea, select {
                font-size: 16px !important;
            }
            #sidebar {
                position: fixed;
                top: 0; left: 0; bottom: 0;
                width: 82vw;
                max-width: 300px;
                z-index: 95;
                transform: translateX(-100%);
                transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 6px 0 24px rgba(0, 0, 0, 0.85);
            }
            #sidebar.mobile-open, #sidebar.open { transform: translateX(0); }
            #sidebar-backdrop {
                position: fixed;
                inset: 0;
                background: rgba(5, 6, 10, 0.72);
                backdrop-filter: blur(2px);
                z-index: 90;
            }
            #sidebar-backdrop.mobile-open, #sidebar-backdrop.active { display: block; }
            #main { width: 100%; height: 100dvh; flex: 1 1 100%; }
            #header { padding: 10px 12px; gap: 8px; flex-wrap: wrap; }
            #header h1 { font-size: 1.02rem; display: flex; align-items: center; gap: 8px; }
            #mobile-menu-btn {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 44px;
                height: 44px;
                min-width: 44px;
                min-height: 44px;
                background: rgba(122, 162, 247, 0.12);
                color: var(--accent-blue);
                border: 1px solid var(--accent-blue);
                border-radius: 4px;
                padding: 0;
                font-family: inherit;
                font-size: 24px;
                line-height: 1;
                font-weight: bold;
                cursor: pointer;
            }
            #modfile-view-panel { padding: 8px 12px; max-height: 150px; }
            #modfile-view-header { flex-wrap: wrap; gap: 6px; }
            #chat-container { padding: 12px; gap: 10px; }
            .msg {
                max-width: 92%;
                font-size: 16px;
                line-height: 1.45;
                padding: 10px 13px;
                word-break: break-word;
                overflow-wrap: anywhere;
            }
            #input-area { padding: 10px 12px; gap: 8px; align-items: stretch; }
            #chat-input { font-size: 16px; height: 48px; padding: 10px; border-radius: 4px; }
            #send-btn {
                width: auto;
                min-width: 82px;
                padding: 0 14px;
                margin-bottom: 0;
                font-weight: bold;
                font-size: 16px;
                display: flex;
                align-items: center;
                justify-content: center;
            }
            #modfile-editor, #skills-modal, #skill-creator-modal {
                position: fixed !important;
                inset: 0 !important;
                padding: 12px !important;
                z-index: 120 !important;
                overflow-y: auto !important;
            }
            #skills-modal-container { padding: 14px; max-height: 92dvh; }
            .skill-item { flex-direction: column; }
        }
    </style>
</head>
<body>
    <div id="sidebar-backdrop" onclick="if(window.toggleHermesSidebar) window.toggleHermesSidebar(false);"></div>

    <!-- SIDEBAR -->
    <div id="sidebar">
        <div class="sidebar-section">
            <h3>System Status</h3>
            <div id="status-indicator" style="color: var(--accent-green);">[✓] Ollama Online</div>
        </div>

        <div class="sidebar-section">
            <h3>Modell-Kontrolle</h3>
            <label style="font-size: 0.8em; color: #888; display: block; margin-bottom: 4px;">Aktives Modell:</label>
            <select id="model-select" class="btn" style="background: var(--bg-dark);" onchange="onModelSelectChange(this.value)">
                <option>Lade Modelle...</option>
            </select>
            <button class="btn" onclick="openModfileEditor()">[⚙] Modfile bearbeiten</button>
            <button class="btn" id="toggle-view-btn" onclick="toggleModfileView()">[📄] Modelfile-Ansicht: AN</button>
        </div>

        <!-- NEUER BEREICH: Skill-Bibliothek -->
        <div class="sidebar-section">
            <h3>Skill-Bibliothek</h3>
            <button class="btn" style="border-color: var(--accent-blue); color: var(--accent-blue);" onclick="openSkillsModal()">[+] Skills durchsuchen/laden</button>
            <button class="btn" style="border-color: var(--accent-green); color: var(--accent-green);" onclick="openSkillCreatorModal()">[+ Neuen Skill erstellen]</button>
            <div id="active-skills-sidebar" style="margin-top: 10px; font-size: 0.82em;">
                <span style="color: #666; font-style: italic;">Lade Skills...</span>
            </div>
        </div>

        <div class="sidebar-section">
            <h3>Sitzungen</h3>
            <button class="btn active">[+] Neuer Task</button>
            <button class="btn" style="color: #555;">[-] Historie laden...</button>
        </div>
    </div>

    <!-- MAIN AREA -->
    <div id="main">
        <div id="header">
            <h1><button id="mobile-menu-btn" type="button" title="Menü öffnen" onclick="if(window.toggleHermesSidebar) window.toggleHermesSidebar();">☰</button>// HERMES AGENT _</h1>
            <div style="display: flex; align-items: center; gap: 15px;">
                <span style="font-size: 0.85em; color: var(--text-main);">
                    Aktives Modell: <strong id="current-model-display" style="color: var(--accent-blue);">Lade...</strong>
                </span>
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
                        <select id="creator-skill-handler" class="btn" style="background: var(--bg-dark); width: 100%; padding: 11px 12px; margin: 0; font-size: 16px;">
                            <option value="Desktop-Notification">🔔 Hinweis / Push-Benachrichtigung senden</option>
                            <option value="Webhook / Telegram">📲 Nachricht an Telegram / Webhook senden</option>
                            <option value="Kommandozeilen-Befehl">⚡ Terminal-Befehl / Skript ausführen</option>
                        </select>
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

    <script>
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
SYSTEM \\\"\\\"\\\"Du bist Hermes, ein hochentwickelter KI-Assistent im Outer Heaven Cluster. Antworte präzise, faktenbasiert und professionell auf Deutsch.\\\"\\\"\\\"
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
SYSTEM \\\"\\\"\\\"Du bist Hermes Coder/Dev, ein spezialisierter Senior Software Engineer und System-Architekt. Du schreibst sauberen, modularen und performanten Code mit präziser Dokumentation und Best Practices.\\\"\\\"\\\"
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
SYSTEM \\\"\\\"\\\"Du bist Hermes Creative, ein immersiver Rollenspiel- und Storytelling-Agent. Verwende eine atmosphärische, lebendige Sprache im Cyberpunk- und Outer-Heaven-Setting.\\\"\\\"\\\"
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
SYSTEM \\\"\\\"\\\"Du bist Hermes Ops im Outer Heaven Cluster. Du hast Zugriff auf System-Terminal, Dateiverwaltung und Paperless-Integration. Handle methodisch, führe Pläne schrittweise aus und dokumentiere jeden Statusbericht.\\\"\\\"\\\"
`
            }
        };

        // --- Model Management & Modelfile Loader ---
        async function fetchModels(autoSelectModel = null) {
            try {
                const res = await fetch('/api/models');
                const data = await res.json();
                const select = document.getElementById('model-select');
                select.innerHTML = '';
                
                if (data.models && data.models.length > 0) {
                    data.models.forEach(m => {
                        const opt = document.createElement('option');
                        opt.value = m.name;
                        opt.textContent = m.name;
                        if (autoSelectModel && m.name === autoSelectModel) {
                            opt.selected = true;
                        }
                        select.appendChild(opt);
                    });

                    const activeModel = select.value;
                    onModelSelectChange(activeModel);
                } else {
                    select.innerHTML = '<option value="">Keine Modelle gefunden</option>';
                    document.getElementById('current-model-display').innerText = 'Kein Modell';
                    document.getElementById('view-panel-model-name').innerText = 'Kein Modell';
                    document.getElementById('modfile-view-pre').innerText = '# Keine Modelle in Ollama gefunden.';
                }
            } catch (e) {
                document.getElementById('status-indicator').innerHTML = '<span style="color: var(--accent-red);">[X] Ollama Offline</span>';
                document.getElementById('modfile-view-pre').innerText = '# Ollama-Verbindung nicht verfügbar.';
            }
        }

        async function onModelSelectChange(modelName) {
            if (!modelName) return;
            // Aktualisiere Header
            document.getElementById('current-model-display').innerText = modelName;
            document.getElementById('view-panel-model-name').innerText = modelName;
            
            // Setze Standard für Zielname im Editor falls nicht manuell angepasst
            document.getElementById('target-model-name').value = modelName;
            
            // Rufe automatisch /api/modfile/{model_name} auf
            await loadAndDisplayModfile(modelName);
        }

        async function loadAndDisplayModfile(modelName) {
            const viewPre = document.getElementById('modfile-view-pre');
            const textarea = document.getElementById('modfile-textarea');
            viewPre.innerText = `# Lade Modelfile für ${modelName}...`;
            
            try {
                const res = await fetch(`/api/modfile/${encodeURIComponent(modelName)}`);
                const data = await res.json();
                const content = data.modelfile || `# Kein Modelfile in Ollama für ${modelName} hinterlegt.`;
                
                // Klartext im Ansichts-Panel anzeigen
                viewPre.innerText = content;
                // Editor-Textarea synchronisieren
                textarea.value = content;
            } catch (e) {
                viewPre.innerText = `# Fehler beim Abrufen des Modelfiles für ${modelName}.`;
            }
        }

        function toggleModfileView() {
            const panel = document.getElementById('modfile-view-panel');
            const btn = document.getElementById('toggle-view-btn');
            if (panel.style.display === 'none') {
                panel.style.display = 'flex';
                if (btn) btn.innerText = '[📄] Modelfile-Ansicht: AN';
            } else {
                panel.style.display = 'none';
                if (btn) btn.innerText = '[📄] Modelfile-Ansicht: AUS';
            }
        }

        // --- Modelfile Editor & Vorlagen ---
        function openModfileEditor() {
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
            
            // Fülle Textfeld mit Basis-Template
            document.getElementById('modfile-textarea').value = tpl.content(currentModel);
            
            // Passe Zielmodell-Namen an
            const basePrefix = currentModel.split(':')[0] || 'hermes';
            document.getElementById('target-model-name').value = `${basePrefix}${tpl.nameSuffix}`;
            
            // Zurücksetzen des Dropdowns für erneute Auswahl
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
            
            statusDiv.innerHTML = `<span style='color: var(--accent-blue);'>[⚒] Kompiliere Modell '${targetName}'... Bitte warten.</span>`;
            try {
                const res = await fetch('/api/build', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: targetName, modelfile: modfile })
                });
                if (res.ok) {
                    statusDiv.innerHTML = `<span style='color: var(--accent-green);'>[✓] Modell '${targetName}' erfolgreich kompiliert!</span>`;
                    
                    // Automatisch Modell-Liste im Dropdown neu laden und neues Modell aktivieren
                    await fetchModels(targetName);
                    
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
            loadSkills();
            document.getElementById('skills-modal').style.display = 'flex';
        }

        function closeSkillsModal() {
            document.getElementById('skills-modal').style.display = 'none';
        }

        // --- Skill Creator Interaktion ---
        const SKILL_PRESETS = {
            mahnung: {
                name: "mahnungs_alarm",
                desc: "Löse diesen Skill aus, sobald im Text eine Mahnung, Zahlungsfrist oder dringende Forderung erkannt wird.",
                params: "absender, betrag, frist",
                handler: "Desktop-Notification"
            },
            telegram: {
                name: "telegram_infosendung",
                desc: "Sende eine Zusammenfassung an Telegram, wenn der Nutzer eine Weiterleitung oder Benachrichtigung aufs Handy wünscht.",
                params: "titel, nachricht, prioritaet",
                handler: "Webhook / Telegram"
            },
            system: {
                name: "cluster_system_check",
                desc: "Führe eine Diagnose aus, wenn nach Server-Status, Speicherplatz oder GPU-Auslastung gefragt wird.",
                params: "knoten, dienst",
                handler: "Kommandozeilen-Befehl"
            }
        };

        function applySkillPreset(presetKey) {
            const p = SKILL_PRESETS[presetKey];
            if (!p) return;
            document.getElementById('creator-skill-name').value = p.name;
            document.getElementById('creator-skill-desc').value = p.desc;
            document.getElementById('creator-skill-params').value = p.params;
            document.getElementById('creator-skill-handler').value = p.handler;
            document.getElementById('creator-status').innerHTML = "<span style='color: var(--accent-blue);'>[i] Vorlage geladen – du kannst die Felder direkt anpassen oder sofort speichern.</span>";
        }

        function openSkillCreatorModal() {
            document.getElementById('creator-status').innerText = "";
            document.getElementById('creator-skill-name').value = "";
            document.getElementById('creator-skill-desc').value = "";
            document.getElementById('creator-skill-params').value = "";
            document.getElementById('creator-skill-target').value = "";
            document.getElementById('creator-skill-handler').selectedIndex = 0;
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
                        handler_target: target
                    })
                });

                if (res.ok) {
                    const data = await res.json();
                    statusDiv.innerHTML = `<span style='color: var(--accent-green);'>[✓] Skill '${data.skill.name}' erfolgreich erstellt & gespeichert!</span>`;
                    
                    // Aktualisiere Skill-Liste und Sidebar
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
                    body: JSON.stringify({ model: model, prompt: text })
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

        // Event Listeners
        document.getElementById('chat-input').addEventListener('keypress', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // Mobile Sidebar Drawer Helper
        window.toggleHermesSidebar = function(forceOpen) {
            const sb = document.getElementById('sidebar');
            const bd = document.getElementById('sidebar-backdrop');
            if (!sb || !bd) return;
            const shouldOpen = (typeof forceOpen === 'boolean') ? forceOpen : !sb.classList.contains('mobile-open');
            if (shouldOpen) {
                sb.classList.add('mobile-open');
                bd.classList.add('mobile-open');
            } else {
                sb.classList.remove('mobile-open');
                bd.classList.remove('mobile-open');
            }
        };

        document.querySelectorAll('#sidebar button.btn').forEach(btn => {
            btn.addEventListener('click', () => {
                if (window.innerWidth <= 900) window.toggleHermesSidebar(false);
            });
        });

        if (window.innerWidth <= 900) {
            const modPanel = document.getElementById('modfile-view-panel');
            const toggleBtn = document.getElementById('toggle-view-btn');
            if (modPanel) {
                modPanel.style.display = 'none';
                if (toggleBtn) toggleBtn.innerText = '[📄] Modelfile-Ansicht: AUS';
            }
        }

        // Init
        fetchModels();
        loadSkills();
    </script>
</body>
</html>
"""

# --- API Endpunkte ---
@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_TEMPLATE

@app.get("/api/models")
def get_models():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/api/modfile/{model_name:path}")
def get_modfile(model_name: str):
    try:
        r = requests.post(f"{OLLAMA_URL}/api/show", json={"name": model_name}, timeout=5)
        r.raise_for_status()
        return {"modelfile": r.json().get("modelfile", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/build")
def build_model(req: BuildRequest):
    try:
        # Synchrone API call an Ollama /api/create
        r = requests.post(f"{OLLAMA_URL}/api/create", json={"name": req.name, "modelfile": req.modelfile}, timeout=60)
        r.raise_for_status()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json={"model": req.model, "prompt": req.prompt, "stream": False}, timeout=120)
        r.raise_for_status()
        return {"response": r.json().get("response", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
    
    # ID erzeugen (z. B. mahnungs_alarm)
    clean_id = re.sub(r'[^a-zA-Z0-9_]', '_', clean_name.lower())
    
    # Prüfen, ob ID bereits existiert
    for s in skills:
        if s["id"] == clean_id:
            raise HTTPException(status_code=400, detail=f"Skill '{clean_id}' existiert bereits.")

    # Function-Calling-Schema generieren
    func_schema = build_function_schema(clean_name, clean_desc, req.parameters or "")
    
    parsed_params = [p.strip() for p in (req.parameters or "").split(",") if p.strip()]

    new_skill = {
        "id": clean_id,
        "name": clean_name,
        "description": clean_desc,
        "category": f"{req.handler_type}",
        "enabled": True,
        "source": f"custom:{req.handler_type.lower().replace(' ', '_')}",
        "handler": {
            "type": req.handler_type,
            "target": (req.handler_target or "").strip()
        },
        "parameters": parsed_params,
        "function_schema": func_schema
    }

    skills.append(new_skill)
    save_skills_registry(skills)

    return {"status": "success", "skill": new_skill}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
