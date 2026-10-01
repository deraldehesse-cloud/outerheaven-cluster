import streamlit as st
import sqlite3
import pandas as pd
import subprocess
import json
from pathlib import Path
from datetime import datetime

st.set_page_config(
    page_title="Outerheaven Leitstand & Kanban",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Pfade
HERMES_DIR = Path.home() / ".hermes"
KANBAN_DB = HERMES_DIR / "kanban.db"
STATE_DB = HERMES_DIR / "state.db"
LOG_FILE = HERMES_DIR / "logs" / "agent.log"
MISSION_DIR = Path.home() / "mission-control" / "data"

# DB-Initialisierung sicherstellen
def init_kanban_db():
    conn = sqlite3.connect(KANBAN_DB)
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

init_kanban_db()

def get_tasks():
    conn = sqlite3.connect(KANBAN_DB)
    df = pd.read_sql_query("SELECT * FROM mission_tasks ORDER BY id DESC", conn)
    conn.close()
    return df

def update_task_status(task_id, new_status):
    conn = sqlite3.connect(KANBAN_DB)
    c = conn.cursor()
    c.execute("UPDATE mission_tasks SET status = ? WHERE id = ?", (new_status, task_id))
    conn.commit()
    conn.close()

def add_task(title, project, task_type, details, status, priority):
    conn = sqlite3.connect(KANBAN_DB)
    c = conn.cursor()
    c.execute("""
        INSERT INTO mission_tasks (title, project, task_type, details, status, priority, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (title, project, task_type, details, status, priority, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def check_service(unit_name):
    try:
        res = subprocess.run(["systemctl", "is-active", unit_name], capture_output=True, text=True)
        return res.stdout.strip() == "active"
    except Exception:
        return False

# --- SEITENLEISTE ---
with st.sidebar:
    st.title("⚡ Leitstand")
    st.caption("Host: outerheaven (Arc A770)")
    st.divider()

    st.subheader("Systemdienste")
    megan_active = check_service("mission-control.service")
    st.write(f"**Megan Mission Control:** {'🟢 Aktiv' if megan_active else '🔴 Inaktiv'}")
    
    comfy_proc = subprocess.run(["pgrep", "-f", "ComfyUI/main.py"], capture_output=True, text=True)
    comfy_active = len(comfy_proc.stdout.strip()) > 0
    st.write(f"**ComfyUI Engine:** {'🟢 Aktiv' if comfy_active else '🔴 Inaktiv'}")

    st.divider()
    st.subheader("Schnellzugriff")
    st.markdown("[ComfyUI Web (Port 8188)](http://100.116.38.29:8188)")
    st.markdown("[Megan Base (Port 8080)](http://100.116.38.29:8080)")

# --- HAUPTBEREICH ---
st.title("Zentrales Aufgaben- & Projektboard")

tab_kanban, tab_creator, tab_projects, tab_logs = st.tabs([
    "📋 Aufgaben-Board",
    "➕ Neue Aufgabe erstellen",
    "📁 Mission Control Projekte",
    "📜 System-Protokolle"
])

# --- TAB 1: ECHTES KANBAN BOARD ---
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
            sub_tasks = df_tasks[df_tasks["status"] == status_name]
            
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

# --- TAB 2: GEFÜHRTER AUFGABEN-ERSTELLER ---
with tab_creator:
    st.subheader("Geführte Aufgabenerstellung")
    st.caption("Erstelle neue Pipeline-Aufträge für ComfyUI, Catlord oder Megan.")
    
    with st.form("new_task_form", clear_on_submit=True):
        f_title = st.text_input("Aufgaben-Titel / Kurzbeschreibung:", placeholder="z.B. Catlord Szene 01: Cash Dach-Render")
        
        c1, c2, c3 = st.columns(3)
        with c1:
            f_project = st.selectbox("Projekt:", [
                "Catlord Project",
                "Megan Companion",
                "ComfyUI / Arc A770",
                "Android App (OpenUp / Megan)",
                "Server & Infrastruktur"
            ])
        with c2:
            f_type = st.selectbox("Aufgaben-Typ:", [
                "Bild-Render (SDXL / LoRA)",
                "3D-Mesh Rekonstruktion",
                "LLM / Prompt-Optimierung",
                "Audio / TTS Pipeline",
                "System-Wartung"
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

# --- TAB 3: PROJEKT-STATUS AUS MISSION CONTROL ---
with tab_projects:
    st.subheader("Mission Control Projektdaten & Konfigurationen")
    
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

# --- TAB 4: PROTOKOLLE ---
with tab_logs:
    st.subheader("Systemprotokolle")
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-150:]
        st.text_area("Agent Log (letzte 150 Zeilen):", "".join(lines), height=450)
    else:
        st.info("Kein Logfile gefunden unter ~/.hermes/logs/agent.log")
