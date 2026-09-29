"""
megan_memory.py - Säule 3: Megans Langzeit- & Vorlieben-Gedächtnis
Speichert Feedback zu Bildern, Sprachnotizen, geteilten Links und Texten (SQLite)
vollständig getrennt vom unveränderlichen Lebenslauf (canonical.json).
"""

import os
import sqlite3
import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

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
            return True
        except Exception as e:
            logger.error(f"Failed to record image feedback: {e}")
            return False

    def get_top_liked_tags(self, limit: int = 10) -> List[str]:
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT visual_tags, SUM(score) as total_score
                    FROM image_feedback
                    WHERE score > 0 AND visual_tags != ''
                    GROUP BY visual_tags
                    ORDER BY total_score DESC
                    LIMIT ?
                    """,
                    (limit,)
                ).fetchall()
                return [r["visual_tags"] for r in rows if r["visual_tags"]]
        except Exception as e:
            logger.error(f"Failed to get top tags: {e}")
            return []

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
        result = {}
        try:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT key, value FROM user_preferences").fetchall()
                for r in rows:
                    try:
                        result[r["key"]] = json.loads(r["value"])
                    except Exception:
                        result[r["key"]] = r["value"]
        except Exception as e:
            logger.error(f"Failed to get all preferences: {e}")
        return result

    def search_memory(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        results = []
        try:
            pattern = f"%{query}%"
            with self._get_connection() as conn:
                # 1. Search voice notes
                vn_rows = conn.execute(
                    "SELECT timestamp, transcript, 'voice' as type FROM voice_notes WHERE transcript LIKE ? ORDER BY timestamp DESC LIMIT ?",
                    (pattern, limit)
                ).fetchall()
                for r in vn_rows:
                    results.append({"type": r["type"], "text": r["transcript"], "timestamp": r["timestamp"]})

                # 2. Search shared links
                link_rows = conn.execute(
                    "SELECT timestamp, url, title, content_summary, 'link' as type FROM shared_links WHERE title LIKE ? OR content_summary LIKE ? ORDER BY timestamp DESC LIMIT ?",
                    (pattern, pattern, limit)
                ).fetchall()
                for r in link_rows:
                    results.append({"type": r["type"], "title": r["title"], "summary": r["content_summary"], "timestamp": r["timestamp"]})

                # 3. Search preferences
                pref_rows = conn.execute(
                    "SELECT key, value, updated_at, 'preference' as type FROM user_preferences WHERE key LIKE ? OR value LIKE ? ORDER BY updated_at DESC LIMIT ?",
                    (pattern, pattern, limit)
                ).fetchall()
                for r in pref_rows:
                    results.append({"type": r["type"], "key": r["key"], "value": r["value"], "timestamp": r["updated_at"]})
        except Exception as e:
            logger.error(f"Error searching memory: {e}")
        return results[:limit]


megan_memory_store = MeganMemoryStore()
