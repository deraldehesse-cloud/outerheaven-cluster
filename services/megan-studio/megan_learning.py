"""
megan_learning.py - Stufe 4: Autonomes Lern- & Reflexions-Modul (Memory & Growth)

- SQLite-Store `megan_memory.db` (Kurz- & Langzeitgedächtnis, Feedback, Themen-Tracker).
- Leichter Reflexions-Hook: regelbasierte Faktenextraktion nach jeder Interaktion
  + optionale periodische LLM-Reflexion (Prompt/Parser hier, HTTP-Call im Server).
- Relevante Gedächtnis-Snippets werden vom persona_assembler als Kontext injiziert.

Hinweis: Unabhängig von der älteren megan_memory.py (megan_memory.sqlite).
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("megan-learning")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MEGAN_MEMORY_DB", str(BASE_DIR / "data" / "megan_memory.db")))

SHORT_TERM_TTL = 7 * 86400          # Kurzzeit-Fakten verfallen nach 7 Tagen ohne Bestätigung
PROMOTE_MIN_HITS = 3                # Beförderung in das Langzeitgedächtnis
PROMOTE_MIN_AGE_HITS = (86400, 2)   # oder: älter als 1 Tag und >= 2 Treffer
REFLECT_EVERY_N = 20                # periodische LLM-Reflexion alle N Interaktionen

STOPWORDS = set("""
aber alle allem allen aller alles also auch dass dein deine denn dich dies diese dieser doch dort durch eine einen einer
eines einem einmal erst etwas euch habe haben hast hatte hier ihre immer jetzt kann kannst kein keine machen mach mein
meine meinen mehr meine mich mit muss nach nicht noch nur oder ohne sehr sein seine sich sind soll sollen über um und
uns unter viel von vor waren was weil wenn werden wie wieder will wir wird wirst wollen wurde würde zum zur zwar
""".split())

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=5.0)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,           -- praeferenz | abneigung | fakt | cluster | gewohnheit
                text TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'short', -- short | long
                hits INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'heuristic',
                created_at REAL NOT NULL,
                last_seen REAL NOT NULL,
                UNIQUE(category, text)
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                sentiment INTEGER NOT NULL,       -- +1 / -1 / 0
                user_text TEXT,
                megan_text TEXT
            );
            CREATE TABLE IF NOT EXISTS topics (
                topic TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 1,
                last_seen REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)


init_db()

# ------------------------------------------------------------------------------
# Heuristische Extraktion
# ------------------------------------------------------------------------------
_PATTERNS = [
    ("praeferenz", re.compile(r"\bich (?:mag|liebe|bevorzuge|steh(?:e)? auf|finde .{1,40}? (?:geil|toll|super))\s+(.{3,100})", re.I)),
    ("abneigung", re.compile(r"\bich (?:hasse|mag .{0,30}?nicht|will kein(?:e|en)?|möchte kein(?:e|en)?)\s*(.{3,100})", re.I)),
    ("fakt", re.compile(r"\bmein(?:e|en)?\s+([a-zäöüß\- ]{3,30}?)\s+(?:ist|sind|heißt|heissen)\s+(.{2,80})", re.I)),
    ("gewohnheit", re.compile(r"\bich (?:arbeite|zocke|schlafe|stehe auf|code|bastle)\s+(?:meistens|oft|immer|gern(?:e)?|abends|morgens|nachts)\s*(.{0,80})", re.I)),
    ("cluster", re.compile(r"\b(?:unser|mein|der)\s+(?:cluster|server|node|rechner)\s+([\w\-]+)\s+(?:ist|hat|läuft|steht)\s+(.{2,100})", re.I)),
]
_EXPLICIT = re.compile(r"\b(?:merk(?:e)? dir|vergiss nicht|wichtig:)\s*[:,]?\s*(.{3,200})", re.I)
_POSITIVE = re.compile(r"\b(?:super|perfekt|geil|top|genau so|gut gemacht|klasse|toll|danke|stark)\b", re.I)
_NEGATIVE = re.compile(r"\b(?:schlecht|falsch|nicht gut|so nicht|nervt|blöd|unpassend|stopp?|lass das)\b", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,!?:;\"'")


def extract_facts(user_text: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", user_text):
        m = _EXPLICIT.search(sentence)
        if m:
            out.append({"category": "fakt", "text": _clean(m.group(1)), "tier": "long"})
        for cat, rx in _PATTERNS:
            for m in rx.finditer(sentence):
                groups = [g for g in m.groups() if g]
                text = _clean(" ".join(groups) if cat != "fakt" else f"{groups[0]}: {groups[1]}")
                if len(text) >= 3:
                    out.append({"category": cat, "text": text, "tier": "short"})
    return out


def _topic_words(text: str) -> List[str]:
    words = re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9\-]{4,}", text)
    return [w.lower() for w in words if w.lower() not in STOPWORDS]


# ------------------------------------------------------------------------------
# Schreib-API
# ------------------------------------------------------------------------------
def remember(category: str, text: str, tier: str = "short", source: str = "heuristic") -> None:
    text = _clean(text)
    if not text or len(text) > 300:
        return
    now = time.time()
    with _lock, _conn() as c:
        row = c.execute("SELECT id, tier FROM facts WHERE category=? AND text=?", (category, text)).fetchone()
        if row:
            new_tier = "long" if (tier == "long" or row["tier"] == "long") else "short"
            c.execute("UPDATE facts SET hits=hits+1, last_seen=?, tier=? WHERE id=?", (now, new_tier, row["id"]))
        else:
            c.execute(
                "INSERT INTO facts (category, text, tier, hits, source, created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                (category, text, tier, 1, source, now, now),
            )


def record_feedback(user_text: str, megan_text: str = "", sentiment: Optional[int] = None) -> int:
    if sentiment is None:
        sentiment = 1 if _POSITIVE.search(user_text) else (-1 if _NEGATIVE.search(user_text) else 0)
    if sentiment == 0 and not megan_text:
        return 0
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO feedback (ts, sentiment, user_text, megan_text) VALUES (?,?,?,?)",
            (time.time(), sentiment, user_text[:500], megan_text[:500]),
        )
    return sentiment


def reflect_interaction(user_text: str, megan_text: str = "") -> int:
    """Leichter Reflexions-Hook: nach jeder Interaktion aufrufen (thread-safe, schnell).
    Gibt zurück, wie viele Fakten extrahiert wurden."""
    if not user_text or not user_text.strip():
        return 0
    facts = extract_facts(user_text)
    for f in facts:
        remember(f["category"], f["text"], f["tier"])
    now = time.time()
    with _lock, _conn() as c:
        for w in set(_topic_words(user_text)):
            c.execute(
                "INSERT INTO topics (topic, count, last_seen) VALUES (?,1,?) "
                "ON CONFLICT(topic) DO UPDATE SET count=count+1, last_seen=excluded.last_seen",
                (w, now),
            )
        n = int((c.execute("SELECT value FROM meta WHERE key='interactions'").fetchone() or {"value": 0})["value"] or 0) + 1
        c.execute("INSERT INTO meta (key,value) VALUES ('interactions',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(n),))
    record_feedback(user_text, megan_text)
    if n % 10 == 0:
        consolidate()
    return len(facts)


def consolidate() -> Dict[str, int]:
    """Kurz → Langzeit befördern, abgelaufene Kurzzeit-Fakten löschen."""
    now = time.time()
    with _lock, _conn() as c:
        promoted = c.execute(
            "UPDATE facts SET tier='long' WHERE tier='short' AND (hits>=? OR (hits>=? AND ?-created_at>=?))",
            (PROMOTE_MIN_HITS, PROMOTE_MIN_AGE_HITS[1], now, PROMOTE_MIN_AGE_HITS[0]),
        ).rowcount
        expired = c.execute(
            "DELETE FROM facts WHERE tier='short' AND hits<2 AND ?-last_seen>?", (now, SHORT_TERM_TTL)
        ).rowcount
    return {"promoted": promoted, "expired": expired}


def interaction_count() -> int:
    with _lock, _conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key='interactions'").fetchone()
        return int(r["value"]) if r else 0


def should_reflect_with_llm() -> bool:
    n = interaction_count()
    return n > 0 and n % REFLECT_EVERY_N == 0


# ------------------------------------------------------------------------------
# Optionale LLM-Reflexion (HTTP-Call erfolgt im Server)
# ------------------------------------------------------------------------------
def build_reflection_prompt(recent_messages: List[Dict[str, Any]]) -> str:
    lines = []
    for m in recent_messages[-16:]:
        who = "Boss" if m.get("sender") == "USER" else "Megan"
        lines.append(f"{who}: {str(m.get('text', ''))[:300]}")
    return (
        "Analysiere den folgenden Chatverlauf und extrahiere dauerhaft wichtige Fakten über den Boss, "
        "seine Vorlieben, Abneigungen, Gewohnheiten und seinen Cluster. Antworte AUSSCHLIESSLICH mit JSON in der Form "
        '{"facts":[{"category":"praeferenz|abneigung|fakt|cluster|gewohnheit","text":"kurzer deutscher Satz"}]}. '
        "Maximal 5 Einträge, nichts Spekulatives.\n\n" + "\n".join(lines)
    )


def ingest_reflection(llm_output: str) -> int:
    m = re.search(r"\{[\s\S]*\}", llm_output or "")
    if not m:
        return 0
    try:
        data = json.loads(m.group(0))
    except Exception:
        return 0
    n = 0
    for f in (data.get("facts") or [])[:5]:
        if isinstance(f, dict) and f.get("text"):
            cat = f.get("category") if f.get("category") in ("praeferenz", "abneigung", "fakt", "cluster", "gewohnheit") else "fakt"
            remember(cat, str(f["text"]), tier="short", source="llm_reflection")
            n += 1
    return n


# ------------------------------------------------------------------------------
# Abruf
# ------------------------------------------------------------------------------
def get_relevant_snippets(user_text: str, limit: int = 6) -> List[str]:
    """Relevante Gedächtnis-Snippets: Keyword-Überlappung + Langzeit-Gewichtung."""
    words = set(_topic_words(user_text or ""))
    with _lock, _conn() as c:
        rows = c.execute("SELECT category, text, tier, hits FROM facts").fetchall()
    scored = []
    for r in rows:
        fw = set(_topic_words(r["text"]))
        overlap = len(words & fw)
        base = (1.5 if r["tier"] == "long" else 0.5) + min(r["hits"], 5) * 0.2
        if overlap:
            scored.append((overlap * 3 + base, r))
        elif r["tier"] == "long" and r["category"] in ("praeferenz", "abneigung"):
            scored.append((base * 0.5, r))  # wichtige Grundpräferenzen immer leicht berücksichtigen
    scored.sort(key=lambda x: x[0], reverse=True)
    snippets = [f"[{r['category']}] {r['text']}" for _, r in scored[:limit]]
    # Hinweis auf zuletzt negatives Feedback
    with _lock, _conn() as c:
        neg = c.execute("SELECT user_text FROM feedback WHERE sentiment<0 ORDER BY ts DESC LIMIT 1").fetchone()
    if neg and neg["user_text"] and len(snippets) < limit:
        snippets.append(f"[feedback] Zuletzt unzufrieden mit: \"{neg['user_text'][:100]}\"")
    return snippets


def list_facts(tier: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    q = "SELECT id, category, text, tier, hits, source, created_at, last_seen FROM facts"
    args: tuple = ()
    if tier in ("short", "long"):
        q += " WHERE tier=?"
        args = (tier,)
    q += " ORDER BY last_seen DESC LIMIT ?"
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(q, args + (limit,)).fetchall()]


def delete_fact(fact_id: int) -> bool:
    with _lock, _conn() as c:
        return c.execute("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount > 0


def get_stats() -> Dict[str, Any]:
    with _lock, _conn() as c:
        short = c.execute("SELECT COUNT(*) n FROM facts WHERE tier='short'").fetchone()["n"]
        long_ = c.execute("SELECT COUNT(*) n FROM facts WHERE tier='long'").fetchone()["n"]
        fb = c.execute("SELECT COALESCE(SUM(sentiment),0) s, COUNT(*) n FROM feedback").fetchone()
        topics = [dict(r) for r in c.execute("SELECT topic, count FROM topics ORDER BY count DESC LIMIT 10").fetchall()]
    return {
        "short_term": short, "long_term": long_, "feedback_total": fb["n"], "feedback_balance": fb["s"],
        "top_topics": topics, "interactions": interaction_count(), "db": str(DB_PATH),
    }
