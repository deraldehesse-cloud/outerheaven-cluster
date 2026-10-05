import streamlit as st
import sqlite3
import pandas as pd
import subprocess
import json
import ast
import requests
from pathlib import Path
from datetime import datetime

st.set_page_config(
    page_title="Outerheaven Leitstand & Hermes",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# PFADE & KONFIGURATION
# ==============================================================================
HERMES_DIR = Path.home() / ".hermes"
KANBAN_DB = HERMES_DIR / "kanban.db"
STATE_DB = HERMES_DIR / "state.db"
LOG_FILE = HERMES_DIR / "logs" / "agent.log"
TOOLS_CONFIG_FILE = HERMES_DIR / "tools_config.json"
CUSTOM_TOOLS_DIR = HERMES_DIR / "custom_tools"
MISSION_DIR = Path.home() / "mission-control" / "data"

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "deepseek-r1:14b"
SYSTEM_PROMPT = (
    "Du bist Hermes, ein hochentwickelter KI-Assistent für das Outer Heaven Cluster. "
    "Antworte stets auf Deutsch, präzise, lösungsorientiert und beratend für Workflows und Tools. "
    "Unterstütze den Benutzer strukturiert bei der Automatisierung, Systemüberwachung und Pipeline-Steuerung."
)

# ==============================================================================
# FREEZE-SCHUTZ & SUBPROZESS-HELPER
# ==============================================================================
def check_service(unit_name):
    """Prüft systemd-User-Service mit striktem 2.0s Timeout."""
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", unit_name],
            capture_output=True,
            text=True,
            timeout=2.0
        )
        return res.stdout.strip() == "active"
    except (subprocess.TimeoutExpired, Exception):
        return False

def check_process(pattern):
    """Prüft Prozess via pgrep mit striktem 2.0s Timeout."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=2.0
        )
        return len(res.stdout.strip()) > 0
    except (subprocess.TimeoutExpired, Exception):
        return False

# ==============================================================================
# KANBAN DATENBANK
# ==============================================================================
def init_kanban_db():
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(KANBAN_DB, timeout=2.0)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS mission_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                project TEXT NOT NULL,
                task_type TEXT NOT NULL,
                details TEXT,
                status TEXT NOT NULL,
                priority TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Fehler bei DB-Initialisierung: {e}")

init_kanban_db()

def get_tasks():
    try:
        conn = sqlite3.connect(KANBAN_DB, timeout=2.0)
        df = pd.read_sql_query("SELECT * FROM mission_tasks ORDER BY id DESC", conn)
        conn.close()
        return df
    except Exception:
        return pd.DataFrame(columns=["id", "title", "project", "task_type", "details", "status", "priority", "created_at"])

def update_task_status(task_id, new_status):
    try:
        conn = sqlite3.connect(KANBAN_DB, timeout=2.0)
        c = conn.cursor()
        c.execute("UPDATE mission_tasks SET status = ? WHERE id = ?", (new_status, task_id))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Fehler beim Aktualisieren der Aufgabe: {e}")

def add_task(title, project, task_type, details, status, priority):
    try:
        conn = sqlite3.connect(KANBAN_DB, timeout=2.0)
        c = conn.cursor()
        c.execute("""
            INSERT INTO mission_tasks (title, project, task_type, details, status, priority, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (title, project, task_type, details, status, priority, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"Fehler beim Hinzufügen der Aufgabe: {e}")

# ==============================================================================
# TOOL-KONFIGURATION
# ==============================================================================
def load_tools_config():
    if TOOLS_CONFIG_FILE.exists():
        try:
            with open(TOOLS_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            st.error(f"Fehler beim Lesen von tools_config.json: {e}")

    # Fallback-Standardkonfiguration
    return {
        "builtin_tools": {
            "terminal_tool": {"description": "Shell-Befehle und Systemkommandos auf Outer Heaven ausführen", "enabled": True},
            "file_tools": {"description": "Dateisystem-Operationen: Lesen, Schreiben, Auflisten", "enabled": True},
            "code_execution_tool": {"description": "Ausführung von Python-Skripten im lokalen Python-Interpreter", "enabled": True},
            "browser_tool": {"description": "Headless-Browser-Steuerung und Webseiten-Interaktion", "enabled": True},
            "image_generation_tool": {"description": "Bildgenerierung via lokaler ComfyUI-Instanz (Intel Arc A770)", "enabled": True},
            "web_tools": {"description": "Webrecherche, DuckDuckGo-Suche und URL-Content-Scraping", "enabled": True},
            "memory_tool": {"description": "Kontext- und Langzeitgedächtnis des Agenten speichern & abfragen", "enabled": True},
            "todo_tool": {"description": "Verwaltung von ToDo-Listen und Zwischenaufgaben", "enabled": True},
            "vision_tools": {"description": "Visuelle Analyse von Bildern via Multimodal-Modell", "enabled": True},
            "tts_tool": {"description": "Text-to-Speech Sprachsynthese für Audio-Ausgaben", "enabled": True},
            "skills_tool": {"description": "Dynamische Skills-Verwaltung und Plug-in Ausführung", "enabled": True},
            "delegate_tool": {"description": "Cluster-Delegation von Aufgaben an andere Nodes", "enabled": True}
        },
        "custom_tools": {}
    }

def save_tools_config(config):
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        with open(TOOLS_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        st.error(f"Fehler beim Speichern von tools_config.json: {e}")
        return False

# ==============================================================================
# OLLAMA STREAMING CHAT HELPER
# ==============================================================================
def stream_ollama_chat(messages):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "stream": True
    }
    try:
        response = requests.post(
            OLLAMA_CHAT_URL,
            json=payload,
            stream=True,
            timeout=(3.0, 60.0)
        )
        response.raise_for_status()
        for line in response.iter_lines():
            if line:
                chunk = json.loads(line.decode("utf-8"))
                if "message" in chunk and "content" in chunk["message"]:
                    yield chunk["message"]["content"]
    except requests.exceptions.RequestException as e:
        yield f"\n\n⚠️ **Verbindungsfehler zu Ollama ({OLLAMA_CHAT_URL}):** {e}"

# ==============================================================================
# SEITENLEISTE (STATUS & MONITORING)
# ==============================================================================
with st.sidebar:
    st.title("⚡ Leitstand")
    st.caption("Host: outerheaven (Arc A770)")
    st.divider()

    st.subheader("Systemdienste")
    megan_active = check_service("mission-control.service")
    st.write(f"**Megan Mission Control:** {'🟢 Aktiv' if megan_active else '🔴 Inaktiv'}")

    hermes_active = check_service("hermes-dashboard.service")
    st.write(f"**Hermes Dashboard:** {'🟢 Aktiv' if hermes_active else '🔴 Inaktiv'}")

    comfy_active = check_process("ComfyUI/main.py")
    st.write(f"**ComfyUI Engine:** {'🟢 Aktiv' if comfy_active else '🔴 Inaktiv'}")

    st.divider()
    st.subheader("Schnellzugriff")
    st.markdown("[ComfyUI Web (Port 8188)](http://100.116.38.29:8188)")
    st.markdown("[Megan Base (Port 8080)](http://100.116.38.29:8080)")

# ==============================================================================
# HAUPTBEREICH MIT REITER-NAVIGATION
# ==============================================================================
st.title("⚡ Hermes Leitstand & Steuerungszentrale")

tab_chat, tab_tools, tab_kanban, tab_creator, tab_projects, tab_logs = st.tabs([
    "💬 Hermes Chat",
    "⚙️ Tool-Leitstand",
    "📋 Aufgaben-Board",
    "➕ Neue Aufgabe erstellen",
    "📁 Mission Control Projekte",
    "📜 System-Protokolle"
])

# ==============================================================================
# REITER 1: 💬 HERMES CHAT
# ==============================================================================
with tab_chat:
    st.subheader("💬 Hermes Chat & Workflow-Beratung")
    st.caption(f"LLM Engine: `{OLLAMA_MODEL}` via `{OLLAMA_CHAT_URL}` | Sprache: Deutsch")

    if "hermes_chat_messages" not in st.session_state:
        st.session_state.hermes_chat_messages = []

    c_head, c_clear = st.columns([5, 1])
    with c_clear:
        if st.button("🗑️ Chat leeren", key="btn_clear_chat", use_container_width=True):
            st.session_state.hermes_chat_messages = []
            st.rerun()

    # Chat-Historie rendern
    for msg in st.session_state.hermes_chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat-Eingabe
    prompt = st.chat_input("Nachricht oder Workflow-Frage an Hermes...")
    if prompt:
        # Benutzer-Nachricht hinzufügen & darstellen
        st.session_state.hermes_chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Antwort mit Streaming generieren
        with st.chat_message("assistant"):
            with st.spinner("Hermes denkt nach..."):
                response_generator = stream_ollama_chat(st.session_state.hermes_chat_messages)
                full_reply = st.write_stream(response_generator)
            st.session_state.hermes_chat_messages.append({"role": "assistant", "content": full_reply})

# ==============================================================================
# REITER 2: ⚙️ TOOL-LEITSTAND
# ==============================================================================
with tab_tools:
    st.subheader("⚙️ Tool-Leitstand & Fähigkeiten")
    st.caption(f"Persistente Konfiguration: `{TOOLS_CONFIG_FILE}`")

    tools_cfg = load_tools_config()
    col_builtin, col_custom = st.columns([1, 1], gap="large")

    with col_builtin:
        st.markdown("### 🛠️ Built-in Tools")
        st.caption("Standardwerkzeuge des Hermes-Agenten (Live schaltbar)")

        builtin_tools = tools_cfg.get("builtin_tools", {})
        config_changed = False

        for t_name, t_meta in builtin_tools.items():
            current_state = t_meta.get("enabled", True)
            t_desc = t_meta.get("description", "")

            with st.container(border=True):
                new_state = st.toggle(
                    label=f"**{t_name}**",
                    value=current_state,
                    key=f"toggle_bi_{t_name}"
                )
                st.caption(t_desc)

                if new_state != current_state:
                    tools_cfg["builtin_tools"][t_name]["enabled"] = new_state
                    config_changed = True

        if config_changed:
            if save_tools_config(tools_cfg):
                st.toast("Built-in Tools gespeichert!", icon="💾")

    with col_custom:
        st.markdown("### 🧩 Custom Tools")
        st.caption(f"Verzeichnis: `{CUSTOM_TOOLS_DIR}`")

        custom_tools = tools_cfg.get("custom_tools", {})
        cust_changed = False

        if not custom_tools:
            st.info("Noch keine benutzerdefinierten Werkzeuge registriert.")
        else:
            for c_name, c_meta in list(custom_tools.items()):
                c_state = c_meta.get("enabled", True)
                c_desc = c_meta.get("description", "")
                c_file = c_meta.get("file", "")

                with st.container(border=True):
                    c_new_state = st.toggle(
                        label=f"**{c_name}**",
                        value=c_state,
                        key=f"toggle_ct_{c_name}"
                    )
                    st.markdown(f"*{c_desc}*")
                    if c_file:
                        st.caption(f"Datei: `{c_file}`")

                    if c_new_state != c_state:
                        tools_cfg["custom_tools"][c_name]["enabled"] = c_new_state
                        cust_changed = True

            if cust_changed:
                if save_tools_config(tools_cfg):
                    st.toast("Custom Tools gespeichert!", icon="💾")

        st.divider()
        st.markdown("### ➕ Neues Tool erstellen")
        st.caption("Erstelle ein Python-Tool mit zwingender AST-Syntaxprüfung.")

        with st.form("form_create_tool", clear_on_submit=False):
            new_tool_name = st.text_input("Tool-Name / Bezeichner:", placeholder="z.B. network_scanner")
            new_tool_desc = st.text_input("Beschreibung:", placeholder="z.B. Scannt Ports im Cluster-Subnetz")

            code_template = (
                '"""\n'
                'Hermes Custom Tool\n'
                '"""\n'
                'def run(*args, **kwargs):\n'
                '    # Tool-Logik hier implementieren\n'
                '    return "Ergebnis der Ausführung"\n'
            )
            new_tool_code = st.text_area("Python-Code:", value=code_template, height=180)
            btn_create = st.form_submit_button("Tool validieren & speichern 🚀")

            if btn_create:
                clean_name = new_tool_name.strip().replace(" ", "_").lower()
                if not clean_name:
                    st.error("Bitte einen gültigen Tool-Namen angeben.")
                elif not new_tool_code.strip():
                    st.error("Der Code darf nicht leer sein.")
                else:
                    # Zwingende Syntaxprüfung via ast.parse()
                    try:
                        ast.parse(new_tool_code)
                        is_valid = True
                    except SyntaxError as err:
                        st.error(f"❌ Syntaxfehler (Zeile {err.lineno}): {err.msg}")
                        is_valid = False
                    except Exception as err:
                        st.error(f"❌ Fehler bei der Code-Prüfung: {err}")
                        is_valid = False

                    if is_valid:
                        try:
                            CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
                            tool_file_path = CUSTOM_TOOLS_DIR / f"{clean_name}.py"
                            with open(tool_file_path, "w", encoding="utf-8") as f_out:
                                f_out.write(new_tool_code)

                            if "custom_tools" not in tools_cfg:
                                tools_cfg["custom_tools"] = {}

                            tools_cfg["custom_tools"][clean_name] = {
                                "description": new_tool_desc.strip() or "Benutzerdefiniertes Werkzeug",
                                "enabled": True,
                                "file": str(tool_file_path),
                                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            if save_tools_config(tools_cfg):
                                st.success(f"✅ Tool '{clean_name}' erfolgreich validiert und gespeichert in `{tool_file_path}`!")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Fehler beim Speichern der Datei: {e}")

# ==============================================================================
# REITER 3: 📋 AUFGABEN-BOARD (KANBAN)
# ==============================================================================
with tab_kanban:
    df_tasks = get_tasks()

    col_todo, col_prog, col_review, col_done = st.columns(4)

    columns_map = [
        ("Zu erledigen", col_todo, "⏳"),
        ("In Bearbeitung", col_prog, "⚙️"),
        ("Render / Review", col_review, "🎨"),
        ("Erledigt", col_done, "✅")
    ]

    for status_name, col, icon in columns_map:
        with col:
            st.markdown(f"### {icon} {status_name}")
            sub_tasks = df_tasks[df_tasks["status"] == status_name] if not df_tasks.empty else pd.DataFrame()

            if sub_tasks.empty:
                st.caption("Keine Aufgaben")

            for _, task in sub_tasks.iterrows():
                with st.container(border=True):
                    st.markdown(f"**{task['title']}**")
                    st.caption(f"📁 {task['project']} | 🏷️ {task['task_type']} | ⚡ {task['priority']}")
                    if task['details']:
                        st.text(task['details'])

                    # Status Weiterschieben Buttons
                    btn_cols = st.columns(2)
                    if status_name != "Zu erledigen":
                        if btn_cols[0].button("◀", key=f"prev_{task['id']}", help="Einen Schritt zurück"):
                            prev_status = "Zu erledigen" if status_name == "In Bearbeitung" else ("In Bearbeitung" if status_name == "Render / Review" else "Render / Review")
                            update_task_status(task['id'], prev_status)
                            st.rerun()
                    if status_name != "Erledigt":
                        if btn_cols[1].button("▶", key=f"next_{task['id']}", help="Einen Schritt vor"):
                            next_status = "In Bearbeitung" if status_name == "Zu erledigen" else ("Render / Review" if status_name == "In Bearbeitung" else "Erledigt")
                            update_task_status(task['id'], next_status)
                            st.rerun()

# ==============================================================================
# REITER 4: ➕ NEUE AUFGABE ERSTELLEN
# ==============================================================================
with tab_creator:
    st.subheader("Geführte Aufgabenerstellung")
    st.caption("Erstelle neue Pipeline-Aufträge für ComfyUI, Catlord oder Megan.")

    with st.form("new_task_form", clear_on_submit=True):
        f_title = st.text_input("Aufgaben-Titel / Kurzbeschreibung:", placeholder="z.B. Catlord Szene 01: Cash Dach-Render")

        c1, c2, c3 = st.columns(3)
        with c1:
            f_project = st.selectbox("Projekt:", [
                "Megan Mobile Companion",
                "Catlord Project",
                "ComfyUI / Arc A770",
                "Server & Infrastruktur"
            ])
        with c2:
            f_type = st.selectbox("Aufgaben-Typ:", [
                "Bild-Render (Assignee: Megan / ComfyUI)",
                "Code / Skripting (Assignee: Hermes)",
                "System / Cluster-Wartung (Assignee: Hermes)",
                "Analyse & Recherche (Assignee: Hermes)",
                "Allgemeine Aufgabe"
            ])
        with c3:
            f_priority = st.selectbox("Priorität:", ["Normal", "Hoch", "Kritisch"])

        st.markdown("**Workflow-Parameter & Details:**")

        details_template = ""
        if f_type == "Bild-Render (SDXL / LoRA)":
            c_res, c_steps = st.columns(2)
            with c_res:
                res_preset = st.selectbox("SDXL-Auflösung:", ["832x1216 (Hochformat)", "1024x1024 (Quadrat)", "1216x832 (Querformat)"])
            with c_steps:
                steps_preset = st.slider("KSampler Steps:", 12, 30, 18)
            details_template = f"Auflösung: {res_preset} | Steps: {steps_preset} | Sampler: Euler Karras | Model: Juggernaut-XL"

        f_details = st.text_area("Details / Prompt / Pfade:", value=details_template, height=120)
        f_init_status = st.selectbox("Start-Status:", ["Zu erledigen", "In Bearbeitung"])

        submitted = st.form_submit_button("Aufgabe ins Board einhängen 🚀")
        if submitted:
            if f_title.strip():
                add_task(f_title, f_project, f_type, f_details, f_init_status, f_priority)
                st.success(f"Aufgabe '{f_title}' erfolgreich ins Board eingetragen!")
                st.rerun()
            else:
                st.error("Bitte gib mindestens einen Titel für die Aufgabe ein.")

# ==============================================================================
# REITER 5: 📁 PROJEKT-STATUS AUS MISSION CONTROL
# ==============================================================================
with tab_projects:
    st.subheader("Mission Control Projektdaten & Konfigurationen")

    try:
        if MISSION_DIR.exists():
            cfg_files = list(MISSION_DIR.glob("*.json"))
            if cfg_files:
                selected_cfg = st.selectbox("Konfigurationsdatei wählen:", [f.name for f in cfg_files])
                cfg_path = MISSION_DIR / selected_cfg
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg_data = json.load(f)
                    st.json(cfg_data)
                except Exception as e:
                    st.error(f"Fehler beim Lesen der Datei: {e}")
            else:
                st.info("Keine JSON-Konfigurationen in mission-control/data gefunden.")
        else:
            st.warning(f"Pfad nicht gefunden: `{MISSION_DIR}`")
    except Exception as e:
        st.error(f"Fehler beim Zugriff auf Mission Control Daten: {e}")

# ==============================================================================
# REITER 6: 📜 PROTOKOLLE
# ==============================================================================
with tab_logs:
    st.subheader("Systemprotokolle")
    try:
        if LOG_FILE.exists():
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-150:]
            st.text_area("Agent Log (letzte 150 Zeilen):", "".join(lines), height=450)
        else:
            st.info("Kein Logfile gefunden unter ~/.hermes/logs/agent.log")
    except Exception as e:
        st.error(f"Fehler beim Lesen des Logfiles: {e}")
