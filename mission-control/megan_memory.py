"""
megan_memory.py - Säule 3: Megans Langzeit- & Vorlieben-Gedächtnis
Speichert Feedback zu Bildern, Sprachnotizen, geteilten Links und Texten (SQLite & JSON)
vollständig getrennt vom unveränderlichen Lebenslauf (canonical.json).
Beinhaltet den asynchronen PreferenceLearningDaemon für kontinuierliches Lernen.
"""

import os
import re
import sys
import json
import time
import queue
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("megan-memory")

BASE_DIR = Path(__file__).resolve().parent

# Check for primary /data/memory, fallback to local mission-control/data/memory
DEFAULT_MEMORY_DIR = Path("/data/memory")
if not os.access(str(DEFAULT_MEMORY_DIR.parent), os.W_OK) and not DEFAULT_MEMORY_DIR.is_dir():
    MEMORY_DIR = BASE_DIR / "data" / "memory"
else:
    MEMORY_DIR = DEFAULT_MEMORY_DIR

MEMORY_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = MEMORY_DIR / "megan_memory.sqlite"

DATA_DIR = MEMORY_DIR.parent
VOICE_DIR = DATA_DIR / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)

PREFERENCES_FILE = MEMORY_DIR / "preferences.json"
STYLE_FILE = MEMORY_DIR / "linguistic_style.json"
SPEECH_PATTERNS_FILE = MEMORY_DIR / "user_speech_patterns.json"
PHONEMES_FILE = VOICE_DIR / "phonemes.json"
EPISODES_FILE = MEMORY_DIR / "interaction_episodes.jsonl"


# ==============================================================================
# 1. Structured JSON File Helpers (Hot-Reloadable)
# ==============================================================================

def load_preferences() -> Dict[str, Any]:
    """Loads structured preferences from /data/memory/preferences.json."""
    if PREFERENCES_FILE.is_file():
        try:
            return json.loads(PREFERENCES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Error reading preferences.json: {e}")
    return {
        "version": "1.0",
        "visual": {
            "colors": ["emerald green", "warm orange", "deep black"],
            "hairstyles": ["long layered hair", "soft natural curls", "messy ponytail"],
            "poses": ["sensual teasing smile", "sitting on desk", "looking at viewer"],
            "lighting": ["soft natural lighting", "golden hour"],
            "preferred_lighting": "soft natural lighting",
            "preferred_clothing": "cockpit suit",
            "favorite_style": "photorealistic",
            "positive_reactions": ["gefällt mir", "wunderschön"],
            "disliked_elements": ["plastic skin", "unnatural lighting", "oversaturated neon", "cgi look", "deformed hands"]
        },
        "chat": {
            "preferred_salutation": "Big Boss",
            "intimacy_level": "hoch",
            "roleplay_depth": "intensiv",
            "dominance_degree": "verführerisch-dominant",
            "tone": "eloquent, direkt, schamlos charmant"
        },
        "dislikes_and_nogos": ["sterile Assistentenfloskeln", "ungefragte Belehrungen", "unnatürliche Beleuchtung", "Plastikhaut"]
    }

def save_preferences(prefs: Dict[str, Any]) -> bool:
    """Atomically writes preferences to /data/memory/preferences.json."""
    try:
        prefs["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        PREFERENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PREFERENCES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PREFERENCES_FILE)
        return True
    except Exception as e:
        logger.error(f"Failed to save preferences.json: {e}")
        return False

def load_linguistic_style() -> Dict[str, Any]:
    """Loads linguistic style rules from /data/memory/linguistic_style.json."""
    if STYLE_FILE.is_file():
        try:
            return json.loads(STYLE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Error reading linguistic_style.json: {e}")
    return {
        "version": "1.0",
        "register": "gehoben, eloquent, wenig Umgangssprache, prägnant, erotisch-selbstbewusst",
        "blacklist": ["halt", "irgendwie", "quasi", "sozusagen", "wie gesagt", "kein Problem", "als KI", "ich hoffe das hilft"],
        "rules": [
            "Vermeide billigen Straßenslang und flapsige Füllwörter.",
            "Formuliere geistreich, verführerisch und direkt."
        ]
    }

def save_linguistic_style(style: Dict[str, Any]) -> bool:
    """Saves linguistic style rules to /data/memory/linguistic_style.json."""
    try:
        style["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STYLE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(style, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STYLE_FILE)
        return True
    except Exception as e:
        logger.error(f"Failed to save linguistic_style.json: {e}")
        return False

def load_phonemes() -> Dict[str, Any]:
    """Loads pronunciation phoneme table from /data/voice/phonemes.json."""
    if PHONEMES_FILE.is_file():
        try:
            return json.loads(PHONEMES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Error reading phonemes.json: {e}")
    return {"replacements": {}, "default_params": {"pitch": 1.0, "speed": 1.0, "length_scale": 1.0}}

def save_phonemes(phonemes: Dict[str, Any]) -> bool:
    """Saves phoneme table to /data/voice/phonemes.json."""
    try:
        phonemes["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        PHONEMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PHONEMES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(phonemes, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PHONEMES_FILE)
        return True
    except Exception as e:
        logger.error(f"Failed to save phonemes.json: {e}")
        return False

def load_user_speech_patterns() -> Dict[str, Any]:
    """Loads user speech patterns from /data/memory/user_speech_patterns.json."""
    if SPEECH_PATTERNS_FILE.is_file():
        try:
            return json.loads(SPEECH_PATTERNS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Error reading user_speech_patterns.json: {e}")
    return {"phonetic_glossary": {}, "common_slang_and_idioms": []}

def save_user_speech_patterns(patterns: Dict[str, Any]) -> bool:
    """Saves user speech patterns to /data/memory/user_speech_patterns.json."""
    try:
        patterns["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        SPEECH_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SPEECH_PATTERNS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(patterns, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SPEECH_PATTERNS_FILE)
        return True
    except Exception as e:
        logger.error(f"Failed to save user_speech_patterns.json: {e}")
        return False

def append_interaction_episode(episode_type: str, raw_input: str, extracted_data: Dict[str, Any], impact_summary: str):
    """Appends an episodic record to /data/memory/interaction_episodes.jsonl."""
    try:
        EPISODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": episode_type,
            "raw_input": raw_input[:300],
            "extracted": extracted_data,
            "impact": impact_summary
        }
        with open(EPISODES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to append episode: {e}")


# ==============================================================================
# 2. SQLite MeganMemoryStore (Backward-Compatible)
# ==============================================================================

class MeganMemoryStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS image_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        image_path TEXT,
                        prompt TEXT,
                        reaction TEXT,
                        visual_tags TEXT,
                        score INTEGER DEFAULT 1
                    );

                    CREATE TABLE IF NOT EXISTS voice_notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        audio_file TEXT,
                        transcript TEXT NOT NULL,
                        response_summary TEXT
                    );

                    CREATE TABLE IF NOT EXISTS shared_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        url TEXT NOT NULL,
                        title TEXT,
                        content_summary TEXT,
                        ingest_status TEXT DEFAULT 'ingested'
                    );

                    CREATE TABLE IF NOT EXISTS user_preferences (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        source TEXT DEFAULT 'chat'
                    );

                    CREATE TABLE IF NOT EXISTS conversations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        user_message TEXT NOT NULL,
                        megan_response TEXT NOT NULL,
                        topic_tags TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_img_time ON image_feedback(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_voice_time ON voice_notes(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_link_url ON shared_links(url);
                """)
        except Exception as e:
            logger.error(f"Error initializing MeganMemoryStore database: {e}")

    def record_image_feedback(
        self,
        image_path: str,
        prompt: str = "",
        reaction: str = "like",
        visual_tags: str = "",
        score: int = 1
    ) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO image_feedback (timestamp, image_path, prompt, reaction, visual_tags, score)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (time.time(), image_path, prompt, reaction, visual_tags, score)
                )
            # Feed into preference learning daemon
            preference_daemon.enqueue_event("image_feedback", {
                "image_path": image_path,
                "prompt": prompt,
                "reaction": reaction,
                "visual_tags": visual_tags,
                "score": score
            })
            return True
        except Exception as e:
            logger.error(f"Failed to record image feedback: {e}")
            return False

    def record_voice_note(self, audio_file: str, transcript: str, response_summary: str = "") -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO voice_notes (timestamp, audio_file, transcript, response_summary)
                    VALUES (?, ?, ?, ?)
                    """,
                    (time.time(), audio_file, transcript, response_summary)
                )
            preference_daemon.enqueue_event("voice_note", {
                "audio_file": audio_file,
                "transcript": transcript,
                "response_summary": response_summary
            })
            return True
        except Exception as e:
            logger.error(f"Failed to record voice note: {e}")
            return False

    def record_shared_link(self, url: str, title: str = "", content_summary: str = "", status: str = "ingested") -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO shared_links (timestamp, url, title, content_summary, ingest_status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (time.time(), url, title, content_summary, status)
                )
            return True
        except Exception as e:
            logger.error(f"Failed to record shared link: {e}")
            return False

    def save_preference(self, key: str, value: Any, source: str = "chat") -> bool:
        try:
            val_str = json.dumps(value) if not isinstance(value, str) else value
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO user_preferences (key, value, updated_at, source)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at,
                        source=excluded.source
                    """,
                    (key, val_str, time.time(), source)
                )
            return True
        except Exception as e:
            logger.error(f"Failed to save preference {key}: {e}")
            return False

    def get_preference(self, key: str, default: Any = None) -> Any:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT value FROM user_preferences WHERE key = ?", (key,)).fetchone()
                if row:
                    val = row["value"]
                    try:
                        return json.loads(val)
                    except Exception:
                        return val
        except Exception as e:
            logger.error(f"Failed to get preference {key}: {e}")
        return default

    def get_all_preferences(self) -> Dict[str, Any]:
        return load_preferences()


# ==============================================================================
# 3. Preference Learning Daemon (Megans Gedächtnis-Dämon)
# ==============================================================================

class PreferenceLearningDaemon:
    """
    Asynchronous background worker continuously analyzing incoming interactions:
    - Chat messages
    - Feedback to generated SDXL images
    - Transcribed PTT audios
    - Vision ingest
    Extracts visual preferences, chat tone/intimacy, style rules, phonemes, and no-gos.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="MeganPreferenceDaemon")
        self._worker_thread.start()

    def enqueue_event(self, event_type: str, data: Dict[str, Any]):
        """Enqueues an interaction event for asynchronous processing."""
        self._queue.put({"type": event_type, "data": data, "timestamp": time.time()})

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=2.0)
                try:
                    self.process_event(event)
                except Exception as ex:
                    logger.error(f"[daemon] Error processing event: {ex}")
                finally:
                    self._queue.task_done()
            except queue.Empty:
                continue

    def process_event(self, event: Dict[str, Any]):
        etype = event.get("type")
        data = event.get("data", {})

        if etype == "chat_message":
            user_text = data.get("user_message", "")
            bot_text = data.get("bot_response", "")
            self.analyze_text_for_preferences(user_text, context={"bot_response": bot_text})

        elif etype == "image_feedback":
            reaction = str(data.get("reaction", "")).lower()
            prompt = data.get("prompt", "")
            feedback_text = data.get("feedback_text", "")
            score = int(data.get("score", 1))

            self.analyze_image_feedback(reaction, prompt, feedback_text, score)

        elif etype == "voice_note":
            transcript = data.get("transcript", "")
            self.analyze_voice_note(transcript)

        elif etype == "vision_ingest":
            analysis = data.get("analysis", "")
            user_comment = data.get("user_comment", "")
            self.analyze_vision_ingest(analysis, user_comment)

    def analyze_text_for_preferences(self, user_text: str, context: Optional[dict] = None):
        """Extracts preferences, style rules, no-gos, and speech patterns from user chat."""
        if not user_text or not user_text.strip():
            return

        t = user_text.strip().lower()
        prefs = load_preferences()
        vis = prefs.setdefault("visual", {})
        chat_prefs = prefs.setdefault("chat", {})
        dislikes = prefs.setdefault("dislikes_and_nogos", [])

        updates_made = []

        # 1. Visual Lighting Preferences & Dislikes
        if any(k in t for k in ["natürliches licht", "natürliches sonnenlicht", "tageslicht", "natural light", "natural lighting"]):
            if "mag ich lieber" in t or "besser" in t or "lieber" in t or "toll" in t or "schön" in t or "gefällt" in t:
                vis["preferred_lighting"] = "soft natural lighting"
                if "soft natural lighting" not in vis.setdefault("lighting", []):
                    vis["lighting"].append("soft natural lighting")
                updates_made.append("preferred_lighting -> soft natural lighting")

        if any(k in t for k in ["golden hour", "abendsonne", "warmes licht", "goldenes licht"]):
            vis["preferred_lighting"] = "golden hour warm sunlight"
            updates_made.append("preferred_lighting -> golden hour warm sunlight")

        if any(k in t for k in ["zu unnatürlich", "nicht so unnatürlich", "unnatürliches licht", "neon nervt", "kein neon"]):
            if "unnatural lighting" not in vis.setdefault("disliked_elements", []):
                vis["disliked_elements"].append("unnatural lighting")
            if "unnatürliche Beleuchtung" not in dislikes:
                dislikes.append("unnatürliche Beleuchtung")
            updates_made.append("disliked -> unnatural lighting")

        if any(k in t for k in ["plastikhaut", "plastik-haut", "sieht aus wie plastik", "wie eine puppe"]):
            if "plastic skin" not in vis.setdefault("disliked_elements", []):
                vis["disliked_elements"].append("plastic skin")
            updates_made.append("disliked -> plastic skin")

        # 2. Clothing & Outfit preferences
        if any(k in t for k in ["zieh dir", "trag lieber", "lieblingskleidung", "stehe auf"]):
            if "dessous" in t or "lingerie" in t:
                vis["preferred_clothing"] = "sensual black lace lingerie"
                updates_made.append("preferred_clothing -> sensual black lace lingerie")
            elif "cockpit" in t or "flight suit" in t:
                vis["preferred_clothing"] = "sleek pilot flight suit"
                updates_made.append("preferred_clothing -> sleek pilot flight suit")
            elif "bikini" in t:
                vis["preferred_clothing"] = "bikini"
                updates_made.append("preferred_clothing -> bikini")

        # 3. Tone & Intimacy
        if any(k in t for k in ["sei frecher", "frech", "dominant", "dominanter", "unterwerfe", "fordernder"]):
            chat_prefs["dominance_degree"] = "verführerisch-dominant, fordernd"
            updates_made.append("chat.dominance_degree -> verführerisch-dominant")
        elif any(k in t for k in ["sanfter", "lieber", "zärtlich"]):
            chat_prefs["dominance_degree"] = "sinnlich-zärtlich, hingebungsvoll"
            updates_made.append("chat.dominance_degree -> sinnlich-zärtlich")

        # 4. Linguistic Style Feedback (e.g. "Kling nicht so umgangssprachlich")
        if any(k in t for k in ["nicht so umgangssprachlich", "keine umgangssprache", "eloquenter", "gehobener", "besseres deutsch"]):
            style = load_linguistic_style()
            style["register"] = "gehoben, eloquent, kultiviert, prägnant, erotisch-selbstbewusst"
            save_linguistic_style(style)
            updates_made.append("linguistic_style.register -> gehoben, eloquent")

        # Check for blacklisting specific phrases in style
        blacklist_match = re.search(r"(?:sag nicht mehr|benutze nicht|hör auf mit|kein|keine)\s+[\"']?([^\"'\n.,;!?]+)[\"']?", t)
        if blacklist_match:
            candidate = blacklist_match.group(1).strip()
            if candidate and len(candidate) > 2 and len(candidate) < 30:
                style = load_linguistic_style()
                bl = style.setdefault("blacklist", [])
                if candidate not in bl:
                    bl.append(candidate)
                    save_linguistic_style(style)
                    updates_made.append(f"linguistic_style.blacklist += '{candidate}'")

        # 5. Pronunciation / Phoneme Corrections
        phoneme_match = re.search(r"(?:sprich|betone)\s+([a-zA-Z0-9_\-]+)\s+(?:wie|als)\s+([a-zA-Z0-9_\-\s]+)", user_text, re.IGNORECASE)
        if phoneme_match:
            word = phoneme_match.group(1).strip()
            pron = phoneme_match.group(2).strip()
            phon = load_phonemes()
            phon.setdefault("replacements", {})[word] = pron
            save_phonemes(phon)
            updates_made.append(f"phonemes['{word}'] -> '{pron}'")

        # 6. User Speech Patterns (Capture slang or specific names)
        tokens = re.findall(r"\b[A-ZÄÖÜ][a-zA-Z0-9_\-]{3,}\b", user_text)
        if tokens:
            patterns = load_user_speech_patterns()
            known = patterns.setdefault("common_slang_and_idioms", [])
            for tok in tokens:
                if tok not in known and tok.lower() not in ["hallo", "danke", "bitte", "kannst", "machst", "hast"]:
                    known.append(tok)
                    updates_made.append(f"speech_patterns += '{tok}'")
            if len(known) > 50:
                patterns["common_slang_and_idioms"] = known[-50:]
            save_user_speech_patterns(patterns)

        if updates_made:
            save_preferences(prefs)
            append_interaction_episode("chat_learning", user_text, {"updates": updates_made}, "; ".join(updates_made))
            logger.info(f"[daemon] Learned preferences from chat: {updates_made}")

    def analyze_image_feedback(self, reaction: str, prompt: str, feedback_text: str, score: int):
        """Analyzes explicit image ratings and text comments."""
        prefs = load_preferences()
        vis = prefs.setdefault("visual", {})
        updates = []

        is_good = "good" in reaction or "like" in reaction or score > 0
        is_bad = "bad" in reaction or "mangel" in reaction or score < 0

        combined = f"{feedback_text} {reaction}".lower()

        if is_good:
            if "gefällt mir" not in vis.setdefault("positive_reactions", []):
                vis["positive_reactions"].append("gefällt mir")
            # If prompt had lighting, boost it
            if "lighting" in prompt.lower():
                m = re.search(r"([a-zA-Z\s]+lighting)", prompt, re.IGNORECASE)
                if m:
                    light_tag = m.group(1).strip()
                    vis["preferred_lighting"] = light_tag
                    updates.append(f"boosted lighting -> {light_tag}")
            updates.append("positive image reaction registered")

        elif is_bad or "zu unnatürlich" in combined:
            if any(k in combined for k in ["unnatürlich", "licht", "beleuchtung"]):
                if "unnatural lighting" not in vis.setdefault("disliked_elements", []):
                    vis["disliked_elements"].append("unnatural lighting")
                vis["preferred_lighting"] = "soft natural lighting"
                updates.append("negative feedback: forced preferred_lighting -> soft natural lighting")
            if "plastik" in combined or "haut" in combined:
                if "plastic skin" not in vis.setdefault("disliked_elements", []):
                    vis["disliked_elements"].append("plastic skin")
                updates.append("negative feedback: added plastic skin to disliked")

        save_preferences(prefs)
        append_interaction_episode("image_feedback", f"Reaction: {reaction} | {feedback_text}", {"prompt": prompt[:100], "score": score}, "; ".join(updates))

    def analyze_voice_note(self, transcript: str):
        """Analyzes voice transcript for preferences and speech patterns."""
        if not transcript:
            return
        # First analyze for preferences
        self.analyze_text_for_preferences(transcript)
        # Update phonetic patterns
        patterns = load_user_speech_patterns()
        gloss = patterns.setdefault("phonetic_glossary", {})
        # Check for known pronunciations
        save_user_speech_patterns(patterns)

    def analyze_vision_ingest(self, analysis: str, user_comment: str):
        """Analyzes vision ingest from user photos."""
        combined = f"{analysis} {user_comment}".lower()
        if any(k in combined for k in ["draußen", "sonne", "natur", "outdoor"]):
            prefs = load_preferences()
            prefs.setdefault("visual", {})["preferred_lighting"] = "natural daylight"
            save_preferences(prefs)
            append_interaction_episode("vision_ingest", user_comment, {"analysis": analysis[:100]}, "preferred_lighting -> natural daylight")


# Global daemon singleton
preference_daemon = PreferenceLearningDaemon()
megan_memory_store = MeganMemoryStore()
