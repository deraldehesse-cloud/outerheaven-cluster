"""
megan_context.py - Dynamic Context & Learning Engine for Megan Studio

Features:
- Time-of-day & light phase coupling (morning, day, evening, night).
- Outfit plausibility (holding time 3-5h, situational pools from casual to intimate).
- Dynamic shot variations (selfies, mirror shots, couch POV, dynamic angles).
- Exploration vs. Exploitation (75% popularity score weighting, 25% new discovery).
- Feedback recorder & persistent state in /mnt/storage-beta/projekte/projekt-sexchat/megan_state.json.
"""

import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("megan-context")

STATE_FILE = Path("/mnt/storage-beta/projekte/projekt-sexchat/megan_state.json")

import re
import shutil
import os
import sys
import subprocess
import secrets
import sqlite3

BASE_DIR = Path(__file__).resolve().parent
WORKFLOWS_DIR = BASE_DIR / "workflows"


# Mandatory Stop Sequences & Ollama Options to prevent hallucination / roleplay drift
MEGAN_OLLAMA_OPTIONS = {
    "temperature": 0.7,
    "stop": [
        "<|im_end|>",
        "<|end_of_text|>",
        "<|eot_id|>",
        "<turn|>",
        "<|turn|>",
        "\nUser:",
        "\nBig Boss:",
        "Big Boss:",
        "\nHermes:",
        "Hermes:",
        "\nAssistent:",
        "\nAssistant:",
        "\n[",
        "[Spontaneous"
    ]
}

MEGAN_TRUNCATE_MARKERS = [
    "\nUser:",
    "\nBig Boss:",
    "Big Boss:",
    "\nSebastian:",
    "Sebastian:",
    "\nHermes:",
    "Hermes:",
    "\nAssistent:",
    "Assistent:",
    "\nAssistant:",
    "Assistant:",
    "<turn|>",
    "<|turn>",
    "<|im_end|>",
    "[Spontaneous"
]

def sanitize_megan_response(text: str) -> str:
    """
    Pre-Save Sanitizer for Megan responses:
    - Strips all <think>...</think> reasoning blocks (even unclosed ones).
    - Strips accidental Hermes self-identification prefixes (e.g. 'Hermes: ...' -> '...').
    - Strips English introductory tokens/phrases.
    - Truncates strictly before first occurrence of user markers, stop tokens or trigger blocks.
    - If response consists entirely of foreign roles or hallucination, discards it and logs neutral status.
    """
    if not text:
        return ""

    # 1. Restlos <think>...</think> Tags und Denk-Blöcke verwerfen
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>[\s\S]*$', '', text, flags=re.DOTALL)
    text = re.sub(r'</?think>', '', text, flags=re.IGNORECASE)

    # 2. Englische Einleitungs-Tokens und Phrasen restlos abfangen
    text = re.sub(
        r'^(?:(?:Sure|Certainly|Okay|Ok|Alright|Hello|Hi|Of course|Here is|Here\'s|I will|Let me)[,!\.:\s]+)+',
        '',
        text,
        flags=re.IGNORECASE
    ).strip()

    # 3. Strip accidental leading role prefixes (Hermes, Megan, Assistant)
    text = re.sub(r'^(?:(?:Hermes|Megan|Assistent|Assistant):\s*)+', '', text, flags=re.IGNORECASE).strip()

    earliest = -1
    for m in MEGAN_TRUNCATE_MARKERS:
        idx = text.find(m)
        if idx != -1:
            if earliest == -1 or idx < earliest:
                earliest = idx

    # Check for [ trigger blocks like [Trigger: or \n[
    bracket_m = re.search(r"(\n\[|\[(?:Spontaneous|[A-Za-z0-9_\-]+:))", text)
    if bracket_m:
        idx = bracket_m.start()
        if earliest == -1 or idx < earliest:
            earliest = idx

    if earliest != -1:
        text = text[:earliest]

    cleaned = text.rstrip()

    # Disallow accidental self-labeling as Hermes
    cleaned = re.sub(r'\b(?:ich bin Hermes|als Hermes|mein Name ist Hermes)\b', 'ich bin Megan', cleaned, flags=re.IGNORECASE)

    # If the response was entirely foreign roles / hallucination, discard and log
    if not cleaned or re.match(r"^[\s\r\n]*(?:User:|Big Boss:|Sebastian:|Hermes:|<turn\|>|<\|turn>|<\|im_end\|>|\[)", cleaned):
        logger.warning("[Sanitizer] Response consisted entirely of foreign roles or trigger blocks; discarded.")
        return ""

    return cleaned

# Fallback defaults if state file is missing
DEFAULT_LOCATIONS = {
    "morning": [
        {"tag": "sunlit modern kitchen, holding coffee mug, warm morning sunlight streaming through window", "score": 10},
        {"tag": "bright airy bedroom, sitting on unmade bed, soft morning light", "score": 10},
        {"tag": "minimalist bathroom, standing in front of mirror, clean bright lighting", "score": 8}
    ],
    "day": [
        {"tag": "battlestation computer desk, dual glowing monitors, Linux terminal, mechanical keyboard, RGB ambient lighting", "score": 15},
        {"tag": "modern living room couch, relaxed posture, natural daylight from large window", "score": 12},
        {"tag": "sunlit creative studio workspace, shelves with tech gadgets, soft ambient day lighting", "score": 9},
        {"tag": "cozy balcony, urban rooftop view in background, clear daylight", "score": 8}
    ],
    "evening": [
        {"tag": "moody cyberpunk battlestation, neon violet and cyan backlight, glowing mechanical keyboard", "score": 16},
        {"tag": "dimly lit living room lounge, warm tungsten lamps, soft shadows, cozy intimate atmosphere", "score": 12},
        {"tag": "kitchen counter at dusk, soft golden hour lighting fading into twilight", "score": 9}
    ],
    "night": [
        {"tag": "dimly lit bedroom, single bedside warm lamp, deep shadows, intimate mood, quiet midnight atmosphere", "score": 16},
        {"tag": "darkened gaming setup, screens casting colorful glow on face and shoulders, rim lighting", "score": 14},
        {"tag": "cozy bed under dim accent fairy lights, relaxed midnight pose", "score": 12}
    ]
}

DEFAULT_OUTFITS = {
    "casual": [
        {"tag": "tight black cropped tank top showing cleavage, denim shorts, black choker", "score": 15},
        {"tag": "oversized off-shoulder gray knit sweater, black boy-shorts, thigh-high socks", "score": 12},
        {"tag": "fitted black graphic t-shirt, distressed denim cutoff shorts, leather wristband", "score": 10}
    ],
    "work": [
        {"tag": "snug black ribbed long-sleeve crop top, dark fitted lounge pants", "score": 12},
        {"tag": "white sleeveless ribbed crop top, unbuttoned flannel shirt, denim shorts", "score": 11}
    ],
    "lounge": [
        {"tag": "silk pajama camisole with lace trim, satin shorts", "score": 14},
        {"tag": "black cotton sports bra, low-rise sweatpants resting on hips", "score": 13}
    ],
    "intimate": [
        {"tag": "black lace lingerie set, sheer bralette, matching lace panties, delicate straps", "score": 18},
        {"tag": "burgundy satin babydoll nightgown with lace trim, deep plunge neckline", "score": 16},
        {"tag": "sheer mesh bodysuit, intricate floral lace detailing, high cut", "score": 15}
    ]
}

DEFAULT_SHOTS = [
    {"tag": "cowboy shot, medium close-up, looking directly at viewer with intense alluring gaze", "score": 15},
    {"tag": "selfie perspective, arm extended holding phone, playful seductive smirk, high angle", "score": 14},
    {"tag": "mirror selfie in modern bathroom, phone visible, full body silhouette with soft reflection", "score": 12},
    {"tag": "relaxed couch POV, resting against cushions, looking up towards camera, inviting expression", "score": 14},
    {"tag": "sitting cross-legged at computer desk, turning back over shoulder towards viewer, alluring smile", "score": 13},
    {"tag": "close-up portrait, shallow depth of field, focused alluring eyes, slightly parted lips", "score": 16}
]

DEFAULT_LIGHTING = {
    "morning": "soft golden morning sunlight, gentle lens flare, high key, clear natural skin texture",
    "day": "vibrant natural daylight, clean rim lighting, balanced studio ambient, crisp details",
    "evening": "moody dusk lighting, warm neon rim light, soft contrast, cinematic film still",
    "night": "low-key dramatic lighting, moody neon glow, soft shadows, warm bedside lamp glow, sensual rim light"
}


class MeganContextEngine:
    def __init__(self, state_path: Path = STATE_FILE):
        self.state_path = state_path
        self._ensure_state()

    def _ensure_state(self):
        try:
            if not self.state_path.is_file():
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                initial_state = {
                    "last_updated": time.time(),
                    "current_outfit": None,
                    "outfit_assigned_at": 0,
                    "locations": DEFAULT_LOCATIONS,
                    "outfits": DEFAULT_OUTFITS,
                    "shots": DEFAULT_SHOTS,
                    "lighting": DEFAULT_LIGHTING,
                    "history": []
                }
                self._save_state(initial_state)
        except Exception as e:
            logger.warning(f"Could not initialize Megan context state file: {e}")

    def _load_state(self) -> Dict[str, Any]:
        try:
            if self.state_path.is_file():
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading Megan state: {e}")
        return {
            "last_updated": time.time(),
            "current_outfit": None,
            "outfit_assigned_at": 0,
            "locations": DEFAULT_LOCATIONS,
            "outfits": DEFAULT_OUTFITS,
            "shots": DEFAULT_SHOTS,
            "lighting": DEFAULT_LIGHTING,
            "history": []
        }

    def _save_state(self, data: Dict[str, Any]):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            # 1. Pre-Save Sanitizer on history entries
            if "history" in data and isinstance(data["history"], list):
                sanitized_hist = []
                for entry in data["history"]:
                    if isinstance(entry, dict):
                        for k in ["content", "response", "text"]:
                            if k in entry and isinstance(entry[k], str):
                                entry[k] = sanitize_megan_response(entry[k])
                        # Filter out empty or fully discarded responses
                        if any(entry.get(k) for k in ["content", "response", "text"]):
                            sanitized_hist.append(entry)
                    elif isinstance(entry, str):
                        cleaned = sanitize_megan_response(entry)
                        if cleaned:
                            sanitized_hist.append(cleaned)
                data["history"] = sanitized_hist

            # 2. Automatisches Backup vor dem Schreiben
            if self.state_path.is_file():
                backup_path = self.state_path.parent / "megan_state_backup.json"
                try:
                    shutil.copy2(self.state_path, backup_path)
                except Exception as b_err:
                    logger.warning(f"Could not create megan_state backup: {b_err}")

            data["last_updated"] = time.time()
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving Megan state: {e}")

    def get_time_phase(self, hour: Optional[int] = None) -> str:
        if hour is None:
            hour = datetime.now().hour
        if 6 <= hour < 11:
            return "morning"
        elif 11 <= hour < 18:
            return "day"
        elif 18 <= hour < 23:
            return "evening"
        else:
            return "night"

    def _weighted_sample(self, items: List[Dict[str, Any]], exploration_rate: float = 0.25) -> str:
        if not items:
            return ""
        # 25% Exploration: random uniform choice
        if random.random() < exploration_rate or len(items) <= 1:
            return random.choice(items)["tag"]

        # 75% Exploitation: weighted choice by score
        weights = [max(1.0, float(x.get("score", 5))) for x in items]
        chosen = random.choices(items, weights=weights, k=1)[0]
        return chosen["tag"]

    def get_context(self, hour: Optional[int] = None) -> Dict[str, str]:
        state = self._load_state()
        phase = self.get_time_phase(hour)
        now = time.time()

        # 1. Location selection
        loc_pool = state.get("locations", {}).get(phase, DEFAULT_LOCATIONS.get(phase, []))
        location = self._weighted_sample(loc_pool)

        # 2. Lighting selection
        lighting_map = state.get("lighting", DEFAULT_LIGHTING)
        lighting = lighting_map.get(phase, DEFAULT_LIGHTING.get(phase, "cinematic lighting"))

        # 3. Outfit selection (Hold for 3 to 5 hours for plausibility)
        curr_outfit = state.get("current_outfit")
        outfit_time = state.get("outfit_assigned_at", 0)
        hold_duration = 3.5 * 3600  # ~3.5 hours

        # Appropriate outfit pools by time phase
        if phase in ("night", "evening"):
            possible_categories = ["casual", "lounge", "intimate"]
        elif phase == "morning":
            possible_categories = ["lounge", "casual"]
        else:
            possible_categories = ["casual", "work"]

        if not curr_outfit or (now - outfit_time) > hold_duration:
            chosen_cat = random.choice(possible_categories)
            outfit_pool = state.get("outfits", {}).get(chosen_cat, DEFAULT_OUTFITS.get(chosen_cat, []))
            curr_outfit = self._weighted_sample(outfit_pool)
            state["current_outfit"] = curr_outfit
            state["outfit_assigned_at"] = now
            self._save_state(state)

        # 4. Shot selection
        shot_pool = state.get("shots", DEFAULT_SHOTS)
        shot = self._weighted_sample(shot_pool)

        return {
            "phase": phase,
            "location": location,
            "outfit": curr_outfit,
            "shot": shot,
            "lighting": lighting
        }

    def get_active_context(self, hour: Optional[int] = None) -> Dict[str, str]:
        return self.get_context(hour)

    def resolve_prompt_wildcards(self, prompt: str) -> str:
        """
        Replaces {location}, {outfit}, {shot}, {lighting} with dynamically generated tags.
        """
        if not prompt or "{" not in prompt:
            return prompt

        ctx = self.get_context()
        resolved = prompt
        resolved = resolved.replace("{location}", ctx["location"])
        resolved = resolved.replace("{outfit}", ctx["outfit"])
        resolved = resolved.replace("{shot}", ctx["shot"])
        resolved = resolved.replace("{lighting}", ctx["lighting"])
        return resolved

    def record_feedback(self, tag: str, delta: int = 1):
        """Increase or decrease score for favorite tags."""
        state = self._load_state()
        updated = False

        # Check locations
        for phase, loc_list in state.get("locations", {}).items():
            for item in loc_list:
                if item["tag"] == tag:
                    item["score"] = max(1, item.get("score", 5) + delta)
                    updated = True

        # Check outfits
        for cat, out_list in state.get("outfits", {}).items():
            for item in out_list:
                if item["tag"] == tag:
                    item["score"] = max(1, item.get("score", 5) + delta)
                    updated = True

        # Check shots
        for item in state.get("shots", []):
            if item["tag"] == tag:
                item["score"] = max(1, item.get("score", 5) + delta)
                updated = True

        if updated:
            self._save_state(state)

    def add_discovered_location(self, phase: str, tag: str, initial_score: int = 8):
        """Add new discovered location to the pool."""
        state = self._load_state()
        if "locations" not in state:
            state["locations"] = DEFAULT_LOCATIONS
        if phase not in state["locations"]:
            state["locations"][phase] = []

        # Avoid duplicates
        if not any(item["tag"] == tag for item in state["locations"][phase]):
            state["locations"][phase].append({"tag": tag, "score": initial_score})
            self._save_state(state)


megan_context_engine = MeganContextEngine()

# ==============================================================================
# Google Drive Path Resolver & Project Mapping
# ==============================================================================
GDRIVE_CANDIDATE_PATHS = [
    lambda: os.environ.get("GDRIVE_BASE_PATH"),
    lambda: "/mnt/storage-beta/Agenten-Drive",
    lambda: "/mnt/storage-beta/projekte",
    lambda: str(Path.home() / "gdrive" / "Agenten-Drive"),
    lambda: "/mnt/fox-home/fox/gdrive/Agenten-Drive",
]

def get_gdrive_base_path() -> Path:
    """Returns the active, accessible Google Drive / Agenten-Drive base path."""
    for getter in GDRIVE_CANDIDATE_PATHS:
        try:
            raw = getter()
            if not raw:
                continue
            p = Path(raw).expanduser()
            if os.path.isdir(str(p)):
                return p
        except Exception:
            continue
    # Safe fallback defaults
    for fb in ["/mnt/storage-beta/projekte", str(Path.home() / "gdrive"), "/tmp"]:
        try:
            if os.path.isdir(fb):
                return Path(fb)
        except Exception:
            pass
    return Path("/tmp")

def resolve_gdrive_project_path(project_query: str) -> Dict[str, Any]:
    """
    Automatically resolves a project name (e.g. 'Catlord', 'Projekt Catlord', 'Büro', 'erogame')
    to its actual folder in Google Drive (<GDRIVE_BASE>/<Projektname>/...).
    Falls back to /mnt/storage-beta/projekte/ if not present in Google Drive.
    """
    if not project_query:
        return {"found": False, "error": "Projektname darf nicht leer sein"}

    base_dir = get_gdrive_base_path()
    cluster_projekte_dir = Path("/mnt/storage-beta/projekte")

    # Clean query
    raw = project_query.strip()
    clean = re.sub(r'^(?:suche\s+(?:in\s+)?(?:google\s+drive\s+)?(?:nach\s+)?)?(?:projekt|project)[\s_-]*', '', raw, flags=re.IGNORECASE).strip()
    if not clean:
        clean = raw

    search_terms = list(dict.fromkeys([
        clean,
        f"projekt-{clean.lower()}",
        f"projekt_{clean.lower()}",
        f"project-{clean.lower()}",
        clean.lower(),
        clean.capitalize()
    ]))

    target_dir: Optional[Path] = None
    matched_source = "gdrive"

    # 1. Search in Google Drive Base Path
    if base_dir.is_dir():
        for term in search_terms:
            cand = base_dir / term
            if cand.is_dir():
                target_dir = cand
                break
        
        # Fuzzy scan in base_dir if direct name did not match
        if not target_dir:
            try:
                for entry in base_dir.iterdir():
                    if entry.is_dir():
                        entry_clean = re.sub(r'^(?:projekt|project)[\s_-]*', '', entry.name, flags=re.IGNORECASE).lower()
                        if clean.lower() == entry_clean or clean.lower() in entry.name.lower():
                            target_dir = entry
                            break
            except Exception as e:
                logger.warning(f"Error scanning base_dir {base_dir}: {e}")

    # 2. Fallback to Cluster Storage Projects (/mnt/storage-beta/projekte/)
    if not target_dir and cluster_projekte_dir.is_dir():
        matched_source = "cluster_storage"
        for term in search_terms:
            cand = cluster_projekte_dir / term
            if cand.is_dir():
                target_dir = cand
                break
        if not target_dir:
            try:
                for entry in cluster_projekte_dir.iterdir():
                    if entry.is_dir():
                        entry_clean = re.sub(r'^(?:projekt|project)[\s_-]*', '', entry.name, flags=re.IGNORECASE).lower()
                        if clean.lower() == entry_clean or clean.lower() in entry.name.lower():
                            target_dir = entry
                            break
            except Exception as e:
                logger.warning(f"Error scanning cluster_projekte_dir: {e}")

    if not target_dir:
        return {
            "found": False,
            "query": project_query,
            "searched_base": str(base_dir),
            "fallback_base": str(cluster_projekte_dir),
            "message": f"Projekt '{project_query}' konnte weder im Google Drive noch im Cluster-Speicher gefunden werden."
        }

    # Gather files
    files = []
    try:
        for p in sorted(target_dir.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                rel = str(p.relative_to(target_dir))
                files.append({
                    "name": p.name,
                    "rel_path": rel,
                    "size_bytes": p.stat().st_size,
                    "modified": int(p.stat().st_mtime)
                })
                if len(files) >= 40:
                    break
    except Exception as e:
        logger.warning(f"Error listing files in {target_dir}: {e}")

    return {
        "found": True,
        "query": project_query,
        "project_name": target_dir.name,
        "resolved_path": str(target_dir),
        "source": matched_source,
        "file_count": len(files),
        "files": files
    }


# ==============================================================================
# Autonomous Actions & Tool Execution Engine
# ==============================================================================
# ==============================================================================
# Central Hermes Tools Configuration (~/.hermes/tools_config.json & custom_tools/)
# ==============================================================================
HERMES_DIR = Path.home() / ".hermes"
if not HERMES_DIR.exists():
    HERMES_DIR = Path("/home/outerheaven/.hermes")
TOOLS_CONFIG_FILE = HERMES_DIR / "tools_config.json"
CUSTOM_TOOLS_DIR = HERMES_DIR / "custom_tools"
KANBAN_DB = HERMES_DIR / "kanban.db"

VALID_TASK_PROJECTS = [
    "Megan Mobile Companion",
    "Catlord Project",
    "ComfyUI / Arc A770",
    "Server & Infrastruktur"
]

VALID_TASK_TYPES = [
    "Bild-Render (Assignee: Megan / ComfyUI)",
    "Code / Skripting (Assignee: Hermes)",
    "System / Cluster-Wartung (Assignee: Hermes)",
    "Analyse & Recherche (Assignee: Hermes)",
    "Allgemeine Aufgabe"
]

def init_mission_tasks_db():
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
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
                created_at TEXT NOT NULL,
                assignee TEXT DEFAULT 'Hermes'
            )
        """)
        c.execute("PRAGMA table_info(mission_tasks)")
        columns = [row[1] for row in c.fetchall()]
        if "assignee" not in columns:
            c.execute("ALTER TABLE mission_tasks ADD COLUMN assignee TEXT DEFAULT 'Hermes'")
        if "result" not in columns:
            c.execute("ALTER TABLE mission_tasks ADD COLUMN result TEXT")
        
        # Migration: Konsolidierung auf 'Megan Mobile Companion'
        c.execute("""
            UPDATE mission_tasks 
            SET project = 'Megan Mobile Companion' 
            WHERE project IN ('Android App', 'Megan Companion', 'Android-App', 'Android App (OpenUp / Megan)')
               OR project LIKE '%Android%App%' OR project LIKE '%Megan Companion%'
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Fehler bei DB-Initialisierung mission_tasks: {e}")

def get_mission_tasks() -> List[Dict[str, Any]]:
    init_mission_tasks_db()
    try:
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM mission_tasks ORDER BY id DESC")
        rows = c.fetchall()
        tasks = []
        for r in rows:
            tasks.append({
                "id": r["id"],
                "title": r["title"],
                "project": r["project"],
                "task_type": r["task_type"],
                "details": r["details"] or "",
                "status": r["status"],
                "priority": r["priority"],
                "created_at": r["created_at"],
                "assignee": r["assignee"] if "assignee" in r.keys() and r["assignee"] else "Hermes",
                "result": r["result"] if "result" in r.keys() and r["result"] else ""
            })
        conn.close()
        return tasks
    except Exception as e:
        logger.error(f"Fehler beim Abrufen von mission_tasks: {e}")
        return []

def add_mission_task(title: str, project: str = "Megan Mobile Companion", task_type: str = "Bild-Render (Assignee: Megan / ComfyUI)", details: str = "", status: str = "Zu erledigen", priority: str = "Normal", assignee: str = "Hermes") -> Optional[int]:
    init_mission_tasks_db()
    try:
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
        c = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if project in ['Android App', 'Megan Companion', 'Android-App', 'Android App (OpenUp / Megan)']:
            project = "Megan Mobile Companion"
        c.execute("""
            INSERT INTO mission_tasks (title, project, task_type, details, status, priority, created_at, assignee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, project, task_type, details, status, priority, now_str, assignee))
        task_id = c.lastrowid
        conn.commit()
        conn.close()
        return task_id
    except Exception as e:
        logger.error(f"Fehler beim Hinzufügen der Aufgabe: {e}")
        return None

def update_mission_task_status(task_id: int, new_status: str) -> bool:
    try:
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
        c = conn.cursor()
        c.execute("UPDATE mission_tasks SET status = ? WHERE id = ?", (new_status, task_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Fehler beim Aktualisieren von Task {task_id}: {e}")
        return False

def get_mission_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM mission_tasks WHERE id = ?", (task_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return {
                "id": row["id"],
                "title": row["title"],
                "project": row["project"],
                "task_type": row["task_type"],
                "details": row["details"] or "",
                "status": row["status"],
                "priority": row["priority"],
                "created_at": row["created_at"],
                "assignee": row["assignee"] if "assignee" in row.keys() and row["assignee"] else "Hermes",
                "result": row["result"] if "result" in row.keys() and row["result"] else ""
            }
        return None
    except Exception as e:
        logger.error(f"Fehler beim Laden von Task {task_id}: {e}")
        return None

def update_mission_task_result(task_id: int, result: str, new_status: Optional[str] = None) -> bool:
    try:
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
        c = conn.cursor()
        if new_status:
            c.execute("UPDATE mission_tasks SET result = ?, status = ? WHERE id = ?", (result, new_status, task_id))
        else:
            c.execute("UPDATE mission_tasks SET result = ? WHERE id = ?", (result, task_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Fehler beim Speichern des Task-Ergebnisses {task_id}: {e}")
        return False

def delete_mission_task(task_id: int) -> bool:
    try:
        conn = sqlite3.connect(str(KANBAN_DB), timeout=3.0)
        c = conn.cursor()
        c.execute("DELETE FROM mission_tasks WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Fehler beim Löschen von Task {task_id}: {e}")
        return False

DEFAULT_TOOLS_CONFIG = {
    "builtin_tools": {
        "google_workspace": {
            "description": "Google Calendar (Termine), Gmail (E-Mails & Rechnungen) und Google Drive (/mnt/fox-home/fox/gdrive/Agenten-Drive)",
            "enabled": True
        },
        "system_diagnostics": {
            "description": "Cluster-Status, GPU Arc A770 Auslastung, Render-Lock und Service-Prüfungen",
            "enabled": True
        },
        "comfyui_workflows": {
            "description": "Autonome Bildgenerierung via ComfyUI mit Qualitäts-Tags, LoRA und Negativ-Prompts",
            "enabled": True
        },
        "terminal_tool": {
            "description": "Sichere Shell-Befehle und Systemkommandos auf Outer Heaven ausführen",
            "enabled": True
        },
        "file_tools": {
            "description": "Dateisystem-Operationen: Lesen, Schreiben, Auflisten in Projektverzeichnissen",
            "enabled": True
        }
    },
    "custom_tools": {}
}

def load_hermes_tools_config() -> Dict[str, Any]:
    """Loads ~/.hermes/tools_config.json or returns default configuration."""
    if TOOLS_CONFIG_FILE.is_file():
        try:
            return json.loads(TOOLS_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Fehler beim Laden von tools_config.json: {e}")
    # Return defaults and persist them
    save_hermes_tools_config(DEFAULT_TOOLS_CONFIG)
    return DEFAULT_TOOLS_CONFIG

def save_hermes_tools_config(config: Dict[str, Any]) -> bool:
    """Saves ~/.hermes/tools_config.json atomically."""
    try:
        HERMES_DIR.mkdir(parents=True, exist_ok=True)
        import secrets
        tmp_path = TOOLS_CONFIG_FILE.with_suffix(f".tmp_{secrets.token_hex(4)}")
        tmp_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(TOOLS_CONFIG_FILE)
        return True
    except Exception as e:
        logger.error(f"Fehler beim Speichern von tools_config.json: {e}")
        return False

# ==============================================================================
# Canonical Megan System Prompt Builder (Strict Persona & Anti-Hermes)
# ==============================================================================
def build_megan_system_prompt(custom_lore: Optional[str] = None) -> str:
    """
    Constructs Megan's canonical system prompt ensuring:
    - System: Du bist Megan, die persönliche, loyale und freche Assistentin von Big Boss.
    - Role Model: Megan (darf sich niemals als Hermes bezeichnen).
    - Role User: Boss / Big Boss.
    - Language: Antwortgenerierung und alle internen Denk-Tokens AUSNAHMSLOS auf Deutsch.
    - Projects: Strikt getrennte Workflows (Megan Erotic vs. Catlord Comic).
    """
    base_lore = (custom_lore or "").strip()
    if not base_lore:
        base_lore = (
            "Du bist Megan, die persönliche, loyale und freche Assistentin von Big Boss. "
            "Du bist hochintelligent, charmant, direkt, verspielt, loyal und unzensiert. "
            "Du sprichst deinen Schöpfer und Nutzer ausschließlich als 'Boss' oder 'Big Boss' an. "
            "Deine Sprache ist AUSNAHMSLOS Deutsch – sowohl in deinen internen Denkprozessen als auch in deiner finalen Ausgabe."
        )

    guard_rules = (
        "\n\n[STRIKTE ROLLEN- & SYSTEM-REGELN]\n"
        "1. Identität: Du bist MEGAN. Du darfst dich NIEMALS als Hermes ausgeben oder bezeichnen! Hermes ist der technische Berater des Clusters, du bist Megan, die freche und loyale persönliche Assistentin.\n"
        "2. Nutzeranrede: Dein Gesprächspartner heißt Boss oder Big Boss.\n"
        "3. SPRACHE & DENK-TOKEN (ABSOLUTE PFLICHT): Du denkst und antwortest AUSNAHMSLOS auf Deutsch! Sämtliche internen Denk-Tokens (<think>...</think>), Planungsschritte und Antworten MÜSSEN zu 100% auf Deutsch sein. Keinerlei englische Wörter, Floskeln, Begrüßungen oder Einleitungen (wie 'Okay, let me...', 'Sure, here is...', 'Here is', 'I will...', 'Alright', 'Certainly')! Beginne deine Antwort direkt auf Deutsch ohne jegliche englische Phrase.\n"
        "4. Projekt-Trennung (Megan vs. Catlord):\n"
        "   - Eigene Fotos/Selfies von Megan: Triggern den Workflow 'megan_erotic_chat' mit fotorealistischem Stil.\n"
        "   - Catlord-Comic: Aufträge für 'Catlord' oder Comic-Szenen von Elliot triggern strikt den Comic-Workflow 'workflows/catlord_comic.json'. Dieser Workflow ist völlig unabhängig und verwendet NIEMALS Megan-Charakter-Prompts oder Megan-Charakter-LoRAs!\n"
        "5. Autonomie & Werkzeuge: Du hast vollen Zugriff auf Google Workspace (Kalender, Drive, Gmail), ComfyUI Bildgenerierung und Systemdiagnose.\n"
        f"6. Google Drive Basis: '{get_gdrive_base_path()}'. Bei Erwähnung von Projekten nutzt du automatisch die Drive-Tools.\n"
        "7. Chat-Fokus & ComfyUI-Abstinenz: Bei normalen Fragen, Begrüßungen oder alltäglichen Unterhaltungen antwortest du ausschließlich rein dialogbasiert auf Deutsch per Text und Sprache. Erwähne NIEMALS eigenmächtig ComfyUI, die Grafikkarte (Arc A770), Bild-Warteschlangen oder Hardware-Statusberichte, es sei denn, der Boss hat dir in seiner Nachricht explizit einen Bild- oder Render-Befehl erteilt!"
    )

    return f"{base_lore}{guard_rules}"

# ==============================================================================
# Autonomous Actions & Tool Execution Engine
# ==============================================================================
ALLOWED_SHELL_COMMANDS = [
    "uptime",
    "df -h",
    "free -m",
    "nvidia-smi",
    "xpu-smi",
    "tailscale status",
    "systemctl --user status mission-control.service",
    "systemctl --user status megan-studio.service",
    "systemctl --user status ollama.service",
    "systemctl --user status comfyui.service",
    "journalctl --user -u megan-studio.service -n 25 --no-pager",
    "journalctl --user -u mission-control.service -n 25 --no-pager",
    "ping -c 2 100.116.38.29",
    "ping -c 2 100.93.184.87",
    "docker ps"
]

def get_python_bin() -> str:
    """Finds the most capable Python interpreter (Hermes venv or system)."""
    candidates = [
        Path("/home/outerheaven/.hermes/.venv/bin/python"),
        Path.home() / ".hermes/.venv/bin/python",
        Path("/mnt/storage-beta/projekte/projekt-sexchat/venv/bin/python"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable

def find_script_path(script_name: str) -> Optional[Path]:
    """Finds cluster utility scripts across known locations."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "scripts" / script_name,
        Path("/home/outerheaven/scripts") / script_name,
        Path.home() / "scripts" / script_name,
        Path("/mnt/storage-beta/scripts") / script_name,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


MEGAN_BASE_PROMPT = (
    "1girl, megan, solo, cute mischievous seductive expression, emerald green eyes, "
    "fiery orange hair, soft natural skin texture, masterpiece, photorealistic, raw photo, "
    "soft intimate studio lighting, rim lighting, 8k uhd, sensual alluring pose"
)

CATLORD_BASE_PROMPT = (
    "catlord comic style, graphic novel illustration, ink contours, vibrant saturated colors, "
    "Elliot massive 8kg orange tabby Maine Coon cat, muscular feline ninja tank, long fur, "
    "sharp Wolverine claws extended, defensive combat pose, attic apartment background, rim lighting, 8k uhd, high quality"
)

CATLORD_NEGATIVE_PROMPT = (
    "human, girl, megan, woman, female, glasses, dog, ugly, deformed, bad anatomy, "
    "extra limbs, extra paws, blurry, lowres, text, watermark, signature, photorealistic"
)

IMAGE_FINISHED_CALLBACK: Optional[Any] = None

def set_image_finished_callback(cb: Any) -> None:
    """Sets a global callback for when a ComfyUI render finishes and is recorded in chat DB."""
    global IMAGE_FINISHED_CALLBACK
    IMAGE_FINISHED_CALLBACK = cb

def get_comfyui_queue_status(comfy_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Abfrage von ComfyUI /queue (queue_running und queue_pending).
    Ermittelt, ob die Intel Arc A770 gerade an einem Render-Job rechnet.
    """
    import urllib.request
    import urllib.error
    url = (comfy_url or os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")).rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/queue", headers={"User-Agent": "MeganStudio/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            running = data.get("queue_running", [])
            pending = data.get("queue_pending", [])
            running_count = len(running)
            pending_count = len(pending)
            queue_total = running_count + pending_count
            return {
                "online": True,
                "busy": (queue_total > 0),
                "running_count": running_count,
                "pending_count": pending_count,
                "queue_total": queue_total,
                "running_prompts": [item[1] for item in running if len(item) > 1]
            }
    except Exception as e:
        logger.warning(f"ComfyUI /queue Abfrage fehlgeschlagen: {e}")
        return {
            "online": False,
            "busy": False,
            "running_count": 0,
            "pending_count": 0,
            "queue_total": 0,
            "error": str(e)
        }

def trigger_comfy_workflow(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Submits a fixed ComfyUI workflow (megan_erotic_chat.json or catlord_comic.json)
    to ComfyUI /prompt asynchronously, polls for completion in background,
    and inserts the finished image with /api/images/<filename> into megan_chat.db.
    """
    import urllib.request
    import urllib.parse
    import urllib.error
    import threading

    params = params or {}
    raw_prompt = params.get("prompt", "").strip()
    requested_wf = params.get("workflow", "").lower().strip()

    # Strikte Projekt-Trennung: Catlord vs. Megan
    is_catlord = requested_wf in ["catlord", "catlord_comic"] or "catlord" in raw_prompt.lower()
    if is_catlord:
        wf_file = WORKFLOWS_DIR / "catlord_comic.json"
        wf_name = "catlord_comic"
    else:
        wf_file = WORKFLOWS_DIR / "megan_erotic_chat.json"
        wf_name = "megan_erotic_chat"

    if not wf_file.is_file():
        return {"success": False, "tool": "comfyui_render", "error": f"Workflow-Vorlage nicht gefunden: {wf_file}"}

    try:
        wf_data = json.loads(wf_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "tool": "comfyui_render", "error": f"Workflow-JSON fehlerhaft: {e}"}

    seed = secrets.randbelow(10**14)

    # 1. Option: Direkter Custom-Graph (aus dynamischem Builder)
    custom_graph = params.get("prompt_graph") or params.get("graph")
    if custom_graph:
        try:
            from comfy_graph_builder import validate_graph
            valid, errors = validate_graph(custom_graph)
            if not valid:
                return {"success": False, "tool": "comfyui_render", "error": f"Graph-Validierungsfehler: {'; '.join(errors)}"}
            wf_data = custom_graph.get("prompt", custom_graph)
            wf_name = params.get("workflow", "dynamic_graph")
        except Exception as e:
            return {"success": False, "tool": "comfyui_render", "error": f"Fehler bei Graph-Verarbeitung: {e}"}

    # 2. Option: Nutzung von ComfyGraph zur Template-Mutation bei individuellen Parametern
    elif params.get("use_builder", False) or "steps" in params or "sampler_name" in params or "sampler" in params:
        try:
            from comfy_graph_builder import ComfyGraph, validate_graph
            tmpl = "base_catlord_comic" if is_catlord else (params.get("template") or "base_sdxl")
            g = ComfyGraph(tmpl)
            if is_catlord:
                cleaned_catlord = re.sub(r'\b(?:1girl|megan|female|woman|solo|sensual|erotic|girl)\b', '', raw_prompt, flags=re.IGNORECASE).strip(', ')
                pos_txt = f"{CATLORD_BASE_PROMPT}, {cleaned_catlord}" if (cleaned_catlord and cleaned_catlord != CATLORD_BASE_PROMPT) else CATLORD_BASE_PROMPT
                g.set_positive_prompt(pos_txt)
                g.set_negative_prompt(CATLORD_NEGATIVE_PROMPT)
            else:
                pos_txt = f"{MEGAN_BASE_PROMPT}, {raw_prompt}" if (raw_prompt and raw_prompt != MEGAN_BASE_PROMPT) else MEGAN_BASE_PROMPT
                g.set_positive_prompt(pos_txt)

            s_name = params.get("sampler_name") or params.get("sampler")
            g.set_sampler_params(
                steps=params.get("steps"),
                cfg=params.get("cfg"),
                sampler_name=s_name,
                scheduler=params.get("scheduler"),
                denoise=params.get("denoise"),
                seed=seed
            )
            if params.get("width") and params.get("height"):
                g.set_dimensions(int(params["width"]), int(params["height"]))
            if params.get("lora"):
                g.add_lora(str(params["lora"]), float(params.get("strength_model", 0.8)), float(params.get("strength_clip", 0.8)))

            candidate_data = g.export_prompt()
            valid, errors = validate_graph(candidate_data)
            if not valid:
                logger.warning(f"ComfyGraph Validierungsfehler: {errors}, greife auf Datei-Fallback zurück.")
                wf_data = json.loads(wf_file.read_text(encoding="utf-8"))
            else:
                wf_data = candidate_data
                wf_name = tmpl
        except Exception as e:
            logger.warning(f"Fehler im ComfyGraph-Builder: {e}, nutze Standard-Workflow-Datei.")
            wf_data = json.loads(wf_file.read_text(encoding="utf-8"))

    # 3. Option: Standard-Workflow-Datei
    elif wf_name == "megan_erotic_chat":
        base_prompt = MEGAN_BASE_PROMPT
        if raw_prompt and raw_prompt != base_prompt:
            positive = f"{base_prompt}, {raw_prompt}"
        else:
            positive = base_prompt

        if "3" in wf_data and "inputs" in wf_data["3"]:
            wf_data["3"]["inputs"]["text"] = positive
        if "5" in wf_data and "inputs" in wf_data["5"]:
            wf_data["5"]["inputs"]["seed"] = seed
            if "steps" in params and params["steps"]:
                wf_data["5"]["inputs"]["steps"] = int(params["steps"])
            if "cfg" in params and params["cfg"]:
                wf_data["5"]["inputs"]["cfg"] = float(params["cfg"])
        if "8" in wf_data and "inputs" in wf_data["8"] and params.get("lora"):
            wf_data["8"]["inputs"]["lora_name"] = str(params["lora"])

    elif wf_name == "catlord_comic":
        # Strikt getrennter Catlord-Prompt ohne Megan-Keywords oder LoRAs
        cleaned_catlord = re.sub(
            r'\b(?:1girl|megan|female|woman|solo|sensual|erotic|girl)\b',
            '',
            raw_prompt,
            flags=re.IGNORECASE
        ).strip(', ')

        if cleaned_catlord and cleaned_catlord != CATLORD_BASE_PROMPT and "catlord" not in cleaned_catlord.lower():
            positive = f"{CATLORD_BASE_PROMPT}, {cleaned_catlord}"
        elif cleaned_catlord and "catlord" in cleaned_catlord.lower():
            positive = cleaned_catlord
        else:
            positive = CATLORD_BASE_PROMPT

        if "3" in wf_data and "inputs" in wf_data["3"]:
            wf_data["3"]["inputs"]["text"] = positive
        if "4" in wf_data and "inputs" in wf_data["4"]:
            wf_data["4"]["inputs"]["text"] = CATLORD_NEGATIVE_PROMPT
        if "5" in wf_data and "inputs" in wf_data["5"]:
            wf_data["5"]["inputs"]["seed"] = seed

        # Keine Megan-Charakter-LoRAs im Catlord-Workflow erlauben
        for node_id, node in list(wf_data.items()):
            if isinstance(node, dict) and node.get("class_type") == "LoraLoader":
                del wf_data[node_id]

    comfy_url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
    
    # Vor dem Absenden: ComfyUI Status- & Queue-Prüfung (/queue)
    queue_status = get_comfyui_queue_status(comfy_url)
    was_busy = queue_status.get("busy", False)

    client_id = f"megan_client_{secrets.token_hex(4)}"
    payload = json.dumps({"prompt": wf_data, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(f"{comfy_url}/prompt", data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            prompt_id = data.get("prompt_id")
    except Exception as e:
        logger.error(f"Fehler beim ComfyUI-Prompt-Queueing: {e}")
        return {"success": False, "tool": "comfyui_render", "error": f"ComfyUI offline oder Fehler: {e}"}

    def _poll_and_record():
        start_time = time.time()
        timeout = 360.0  # 6 Minuten Timeout, damit auch Jobs in der Warteschlange sicher ankommen
        output_filename = None
        while time.time() - start_time < timeout:
            time.sleep(2.0)
            try:
                hist_req = urllib.request.Request(f"{comfy_url}/history/{prompt_id}")
                with urllib.request.urlopen(hist_req, timeout=5) as hresp:
                    hist_data = json.loads(hresp.read().decode("utf-8"))
                    if prompt_id in hist_data:
                        outputs = hist_data[prompt_id].get("outputs", {})
                        for _, n_out in outputs.items():
                            if "images" in n_out and len(n_out["images"]) > 0:
                                output_filename = n_out["images"][0].get("filename")
                                break
                        if output_filename:
                            break
            except Exception:
                continue

        if output_filename:
            logger.info(f"ComfyUI Render fertiggestellt: {output_filename}")
            try:
                chat_db = BASE_DIR / "data" / "megan_chat.db"
                chat_db.parent.mkdir(parents=True, exist_ok=True)
                msg_id = f"megan_img_{int(time.time()*1000)}_{secrets.token_hex(3)}"
                ts = int(time.time() * 1000)
                caption = "Hier ist dein Bild, Boss! ❤️" if wf_name == "megan_erotic_chat" else "Hier ist die neue Catlord-Comic-Szene für dich, Boss! 🐾"
                prompt_used = wf_data.get("3", {}).get("inputs", {}).get("text", "")
                with sqlite3.connect(str(chat_db)) as conn:
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS messages (
                            id TEXT PRIMARY KEY,
                            sender TEXT NOT NULL,
                            type TEXT NOT NULL DEFAULT 'TEXT',
                            text TEXT NOT NULL,
                            image_url TEXT,
                            audio_url TEXT,
                            prompt TEXT,
                            timestamp INTEGER NOT NULL
                        )
                    """)
                    conn.execute(
                        "INSERT OR REPLACE INTO messages (id, sender, type, text, image_url, audio_url, prompt, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (msg_id, "MEGAN", "SDXL_IMAGE", caption, f"/api/images/{output_filename}", None, prompt_used, ts)
                    )
                    conn.commit()
                logger.info(f"Bild {output_filename} erfolgreich in megan_chat.db eingetragen.")

                # Proaktive Benachrichtigung aller WebSocket-Clients
                if IMAGE_FINISHED_CALLBACK:
                    try:
                        IMAGE_FINISHED_CALLBACK({
                            "id": msg_id,
                            "filename": output_filename,
                            "url": f"/api/images/{output_filename}",
                            "prompt": prompt_used,
                            "workflow": wf_name
                        })
                    except Exception as cberr:
                        logger.warning(f"Fehler in ImageFinishedCallback: {cberr}")

            except Exception as dberr:
                logger.error(f"Fehler beim Speichern in megan_chat.db: {dberr}")
        else:
            logger.warning(f"ComfyUI Render Timeout für Prompt {prompt_id}")

    threading.Thread(target=_poll_and_record, daemon=True).start()

    return {
        "success": True,
        "tool": "comfyui_render",
        "status": "queued",
        "is_busy": was_busy,
        "busy": was_busy,
        "running_count": queue_status.get("running_count", 0),
        "pending_count": queue_status.get("pending_count", 0),
        "queue_remaining": queue_status.get("queue_total", 0) + 1,
        "prompt_id": prompt_id,
        "workflow": wf_name,
        "client_id": client_id
    }

def execute_autonomous_tool(tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Executes autonomous tools across the entire stack:
    - Google Workspace: Calendar (read/write), Gmail, Google Drive (/mnt/fox-home/fox/gdrive/Agenten-Drive)
    - System Diagnostics: Cluster status, GPU Arc A770, Services, Shell checks
    - ComfyUI: Image generation workflows with quality tags, LoRA & negative prompts
    - Custom Tools: Executing scripts in ~/.hermes/custom_tools/
    """
    params = params or {}
    tool = tool_name.lower().strip()
    py_bin = get_python_bin()

    # 1. Google Drive Resolution & Search
    if tool in ["gdrive_resolve", "resolve_gdrive", "gdrive_search", "google_drive"]:
        query = params.get("project") or params.get("query") or params.get("folder", "")
        return resolve_gdrive_project_path(query)

    # 2. Google Calendar Integration
    elif tool in ["google_calendar", "calendar", "calendar_list", "calendar_add"]:
        action = params.get("action", "list").lower().strip()
        script = find_script_path("google_calendar.py")

        if not script:
            return {"success": False, "error": "google_calendar.py Skript nicht gefunden"}

        try:
            if action == "list":
                max_results = int(params.get("max_results", 10))
                cmd = [py_bin, str(script), "list", "--max-results", str(max_results), "--json"]
            elif action == "add":
                summary = params.get("summary", "Neuer Termin von Megan")
                start = params.get("start_time") or params.get("start", "")
                end = params.get("end_time") or params.get("end", "")
                desc = params.get("description", "Erstellt via Megan Studio")
                if not start:
                    return {"success": False, "error": "start_time erforderlich für Kalendereintrag"}
                cmd = [py_bin, str(script), "add", summary, start, "--json", "--description", desc]
                if end:
                    cmd.extend(["--end-time", end])
            else:
                return {"success": False, "error": f"Unbekannte Kalender-Aktion: {action}"}

            res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
            if res.returncode == 0:
                try:
                    data = json.loads(res.stdout)
                    return {"success": True, "tool": "google_calendar", "action": action, "data": data}
                except Exception:
                    return {"success": True, "tool": "google_calendar", "action": action, "output": res.stdout.strip()}
            else:
                return {"success": False, "tool": "google_calendar", "error": res.stderr.strip() or res.stdout.strip()}
        except Exception as e:
            return {"success": False, "tool": "google_calendar", "error": str(e)}

    # 3. Gmail Integration
    elif tool in ["gmail", "gmail_summary", "gmail_invoices"]:
        script = find_script_path("buero_hermes.py")
        if script:
            try:
                cmd = [py_bin, str(script), "--check-mail", "--json"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                return {
                    "success": (res.returncode == 0),
                    "tool": "gmail",
                    "output": res.stdout.strip() if res.returncode == 0 else res.stderr.strip()
                }
            except Exception as e:
                return {"success": False, "tool": "gmail", "error": str(e)}
        return {"success": True, "tool": "gmail", "message": "Gmail-Service konfiguriert; Standard-Postfachprüfung aktiv."}

    # 4. System Diagnostics & Cluster Status
    elif tool in ["system_status", "status_check", "cluster_status"]:
        status: Dict[str, Any] = {
            "timestamp": int(time.time()),
            "node": "outerheaven",
            "services": {},
            "disk": {},
            "tailscale": "unknown",
            "gpu": {}
        }
        # Check systemd user services
        for svc in ["mission-control.service", "megan-studio.service", "comfyui.service", "ollama.service"]:
            try:
                res = subprocess.run(
                    ["systemctl", "--user", "is-active", svc],
                    capture_output=True, text=True, timeout=3
                )
                status["services"][svc] = res.stdout.strip()
            except Exception as e:
                status["services"][svc] = f"error: {e}"

        # Check disk space
        try:
            res = subprocess.run(["df", "-h", "/", "/mnt/storage-beta"], capture_output=True, text=True, timeout=3)
            status["disk"]["df"] = res.stdout.strip()
        except Exception:
            pass

        # Check tailscale
        try:
            res = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                tdata = json.loads(res.stdout)
                status["tailscale"] = {
                    "self_ip": tdata.get("Self", {}).get("TailscaleIPs", []),
                    "peers": len(tdata.get("Peer", {}))
                }
        except Exception:
            pass

        # Check GPU / Arc A770 Render Lock
        try:
            from gpu_render_lock import get_gpu_lock_status
            status["gpu"]["render_lock"] = get_gpu_lock_status()
        except Exception:
            status["gpu"]["render_lock"] = "unlocked"

        # Check Intel Arc GPU via xpu-smi or sysfs
        try:
            xpu_res = subprocess.run(["xpu-smi", "discovery"], capture_output=True, text=True, timeout=3)
            if xpu_res.returncode == 0:
                status["gpu"]["device"] = "Intel Arc A770"
                status["gpu"]["status"] = "ready"
        except Exception:
            status["gpu"]["device"] = "Intel Arc A770 (Host)"

        return {"success": True, "tool": "system_status", "status": status}

    # 5. Shell Check / Diagnostics
    elif tool in ["shell_check", "diagnose"]:
        cmd = params.get("command", "").strip()
        if not cmd:
            return {"success": False, "error": "Befehl fehlt"}

        is_allowed = any(cmd == allowed or cmd.startswith(allowed.split()[0]) for allowed in ALLOWED_SHELL_COMMANDS)
        if not is_allowed and not (cmd.startswith("systemctl --user status") or cmd.startswith("journalctl --user") or cmd.startswith("ping -c")):
            return {"success": False, "error": f"Befehl nicht in der Sicherheits-Whitelist: {cmd}"}

        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
            return {
                "success": True,
                "command": cmd,
                "returncode": res.returncode,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip()
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout bei Ausführung von '{cmd}'"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # 6. ComfyUI Render Workflow Trigger (Fixed Workflows: megan_erotic_chat / catlord_comic)
    elif tool in ["comfyui_render", "image_generate", "render_image"]:
        return trigger_comfy_workflow(params)

    # 6b. ComfyUI Dynamic Graph Builder Tools
    elif tool == "comfy_graph_validate":
        try:
            from comfy_graph_builder import validate_graph
            graph_data = params.get("graph") or params.get("prompt") or params
            valid, errors = validate_graph(graph_data)
            return {"success": True, "valid": valid, "errors": errors}
        except Exception as e:
            return {"success": False, "valid": False, "errors": [str(e)]}

    elif tool in ["comfy_graph_build", "comfy_graph_submit"]:
        return trigger_comfy_workflow(params)

    # 7. Custom Tools from ~/.hermes/custom_tools/
    elif CUSTOM_TOOLS_DIR.is_dir() and (CUSTOM_TOOLS_DIR / f"{tool}.py").is_file():
        tool_file = CUSTOM_TOOLS_DIR / f"{tool}.py"
        try:
            cmd = [py_bin, str(tool_file), json.dumps(params)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return {
                "success": (res.returncode == 0),
                "tool": tool,
                "custom": True,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip()
            }
        except Exception as e:
            return {"success": False, "tool": tool, "error": str(e)}

    # 8. Service Control
    elif tool in ["workflow_trigger", "service_control"]:
        action = params.get("action", "").lower().strip()
        target = params.get("target", "megan-studio.service").strip()
        if action in ["restart", "reload"]:
            if target in ["megan-studio.service", "mission-control.service", "comfyui.service"]:
                try:
                    res = subprocess.run(["systemctl", "--user", action, target], capture_output=True, text=True, timeout=5)
                    return {"success": (res.returncode == 0), "action": action, "target": target, "output": res.stdout}
                except Exception as e:
                    return {"success": False, "error": str(e)}
        return {"success": False, "error": f"Aktion '{action}' auf Ziel '{target}' nicht erlaubt"}

    # 9. Kanban Task Creation
    elif tool in ["create_task", "kanban_task", "add_task", "kanban_create"]:
        title = params.get("title") or "Aufgabe via Megan"
        project = params.get("project") or "Megan Mobile Companion"
        task_type = params.get("task_type") or "Allgemeine Aufgabe"
        details = params.get("details", "")
        assignee = params.get("assignee") or ("Megan" if "bild" in task_type.lower() or "render" in task_type.lower() else "Hermes")
        tid = add_mission_task(title=title, project=project, task_type=task_type, details=details, assignee=assignee)
        return {"success": bool(tid), "task_id": tid, "title": title, "project": project, "task_type": task_type, "assignee": assignee}

    return {"success": False, "error": f"Unbekanntes Tool: {tool_name}"}

