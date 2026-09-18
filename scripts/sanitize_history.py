#!/usr/bin/env python3
"""
sanitize_history.py - History Sanitizer & Quarantine Guard for Outer Heaven Companion Bot
Detects and purges corrupted BPE tokens (%{, |||, ;;;, Sternum, 2015ive, vbCrLf)
and repetitive character/syllable loops from history.json and .brain/session.json.
"""

import os
import sys
import re
import json
import time
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parent
HISTORY_PATH = PROJECT_ROOT / "history.json"
SESSION_PATH = PROJECT_ROOT / ".brain" / "session.json"

# Known BPE glitch tokens and defective model artifacts
GLITCH_TOKENS = [
    r"%\{",
    r"\|\|\|",
    r";;;",
    r"Sternum",
    r"Sternant",
    r"Sternals",
    r"theSternly",
    r"forStern",
    r"2015ive",
    r"u2015ive",
    r"2019am",
    r"202cancer",
    r"vbCrLf",
    r"bCrLf",
    r"oFultraled",
    r"P\.SEEPSSTTBDLay",
    r"IPEPRTFLDs",
    r"hybridalike",
    r"scalablytyped",
    r"representologies",
    r"forcedarX",
    r"forcedarxan",
    r"fultraDxan",
    r"outright\|ypersistent",
    r"offtoRuth",
    r"thatchedy",
    r"thatchedrare"
]
GLITCH_PATTERN = re.compile("|".join(GLITCH_TOKENS), re.IGNORECASE)


def is_corrupted_text(text: str) -> Tuple[bool, str]:
    """
    Evaluates whether a message content contains broken BPE artifacts,
    token collapse loops, or defective token chains.
    """
    if not text or not isinstance(text, str):
        return False, ""

    t = text.strip()
    # 0. Pure symbols, isolated brackets, or lacking letters
    if re.match(r"^[\s\(\)\[\]\{\}\#\-_|~]+$", t):
        return True, "Reines Klammer-/Symbol-Fragment"

    alpha_count = sum(1 for c in t if c.isalpha())
    if len(t) <= 10 and alpha_count < 3:
        return True, "Zu wenige Buchstaben / isoliertes Sonderzeichen-Fragment"

    # Abnormal unsegmented subwords (e.g. genfgKarseblevedfesapakigosfand)
    for w in t.split():
        clean_w = w.strip(".,!?:;\"'()[]{}")
        if len(clean_w) > 22 and not (
            clean_w.startswith("http://") or
            clean_w.startswith("https://") or
            clean_w.startswith("/") or
            clean_w.startswith("hf.co") or
            clean_w.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".ogg", ".mp3", ".wav"))
        ):
            return True, f"Abnormal langes Kunstwort / BPE-Verschmelzung ({clean_w[:20]}...)"

    # Schizophrenic / English hallucinations & out-of-character English chatter
    if re.search(r"^##\.\s*\(t\)", t) or re.search(r"\b(?:the death in the Death|Queen'ship|nameless but he is not dead|Deathancedomic)\b", t, re.IGNORECASE):
        return True, "Schizophrene Englisch-Halluzination"
    if re.search(r"\b(?:The only thing more confusing|Please specify a new vibration|Are we discussing something in German again|and during while between)\b", t, re.IGNORECASE):
        return True, "Deplatzierte Englisch-Antwort / Wortsalat"
    if re.search(r"\b(?:and|during|while|between|over|without|together|before)(?:\s+(?:and|during|while|between|over|without|together|before|\.{2,}))+", t, re.IGNORECASE):
        return True, "Präpositions-Kollaps-Kette"
    if re.match(r"^[\(\[\{]\s*\n", t):
        return True, "Isolierte öffnende Klammer am Zeilenanfang"

    # 1. Defective BPE & Known Glitch Tokens
    m = GLITCH_PATTERN.search(t)
    if m:
        return True, f"Glitch-Token erkannt: {m.group(0)}"

    # 2. Defective BPE %-pattern (e.g. %{, %u2015, etc.)
    if re.search(r"%[a-zA-Z0-9\{]", t) and not re.search(r"%\d+\b", t):
        return True, "Defektes BPE %-Muster"

    # Excessive pipe token chains
    pipe_count = t.count("|")
    if t.count("||") >= 2 or pipe_count >= 6:
        if not ("|" in t and "-|-" in t):  # Exclude markdown tables
            return True, f"Excessive Pipes (|): {pipe_count}"

    # Excessive semicolons / broken HTML entities
    if t.count(";;") >= 2 or t.count(';">') >= 2:
        return True, "Defekte Semikolon-Kette"

    # 3. Excessive repetitive characters / symbols (e.g. !!!!, ,,,, -----, >>>>>)
    if re.search(r"([!?,;\-_~|*<>=#–—]){4,}", t):
        return True, "Repetitive Sonderzeichen-Kette"
    if re.search(r"(?:>\s*){4,}", t):
        return True, "Repetitive Quote-Kette (>)"

    # 4. Repetitive syllable / word / phrase loops (e.g. abcabcabcabc)
    rep_sub = re.search(r"([a-zA-Z0-9\-_ ]{2,15}?)\1{3,}", t)
    if rep_sub and len(rep_sub.group(0)) >= 12:
        return True, f"Repetitiver Loop: {rep_sub.group(0)[:30]}"

    # 5. Excessive hyphen-fragment chains (e.g. to-the-syfashion-with-a-little)
    hyphen_chains = re.findall(r"\b\w+(?:-\w+){3,}\b", t)
    if len(hyphen_chains) >= 2 or (hyphen_chains and len(hyphen_chains[0].split("-")) >= 5):
        return True, f"Hyphen-Token-Kette: {hyphen_chains[:2]}"

    # 6. Repetitive word ratio (low lexical diversity in long output)
    words = re.findall(r"\b\w+\b", t.lower())
    if len(words) >= 25:
        unique_words = set(words)
        ratio = len(unique_words) / len(words)
        if ratio < 0.35:
            return True, f"Niedrige lexikalische Diversitaet (Wiederholungs-Loop): {ratio:.2f}"

    # 7. High punctuation / symbol ratio
    if len(t) >= 15:
        punct_count = len(re.findall(r"[!@#$%^&*()_+=|~<>?,./;:'\"–—\\\[\]\{\}]", t))
        if punct_count / len(t) > 0.25:
            return True, f"Hoher Symbol-/Satzzeichen-Anteil: {punct_count / len(t):.2%}"

    # 8. Unrealistic uppercase letter density (random BPE token salad like OHHoYeahNOJXOhIA...)
    letters = [c for c in t if c.isalpha()]
    if len(t) >= 30 and len(letters) >= 20:
        upper = [c for c in letters if c.isupper()]
        if len(upper) / len(letters) > 0.35:
            return True, f"Unrealistische Großbuchstaben-Dichte (BPE-Kollaps): {len(upper) / len(letters):.2%}"

    return False, ""


def sanitize_history_data(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Filters corrupted messages out of a history message list.
    """
    cleaned: List[Dict[str, Any]] = []
    removed_count = 0

    for msg in messages:
        content = msg.get("content", "")
        corrupted, reason = is_corrupted_text(content)
        if corrupted:
            removed_count += 1
            continue
        cleaned.append(msg)

    return cleaned, removed_count


def atomic_save_json(file_path: Union[Path, str], data: Any) -> bool:
    """Safely writes JSON using a temporary file and atomic replace."""
    p = Path(file_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(p)
        return True
    except Exception:
        return False
        if 'tmp' in locals() and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sanitize_history_file(path: Path = HISTORY_PATH) -> int:
    """Scans and cleans history.json."""
    if not path.is_file():
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Sanitizer] Konnte {path} nicht parsen: {e}")
        return 0

    messages = data.get("messages", [])
    cleaned_messages, removed = sanitize_history_data(messages)

    if removed > 0:
        data["messages"] = cleaned_messages
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        atomic_save_json(path, data)
        print(f"[Sanitizer] ✔ {removed} korrumpierte Nachricht(en) aus {path.name} entfernt.")

    return removed


def sanitize_session_file(path: Path = SESSION_PATH) -> int:
    """Scans and cleans .brain/session.json."""
    if not path.is_file():
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Sanitizer] Konnte {path} nicht parsen: {e}")
        return 0

    history_entries = data.get("history", [])
    cleaned_entries = []
    removed = 0

    for entry in history_entries:
        fb = entry.get("feedback", "")
        act = entry.get("actions", "")
        c1, _ = is_corrupted_text(fb)
        c2, _ = is_corrupted_text(act)
        if c1 or c2:
            removed += 1
            continue
        cleaned_entries.append(entry)

    if removed > 0:
        data["history"] = cleaned_entries
        atomic_save_json(path, data)
        print(f"[Sanitizer] ✔ {removed} korrumpierte(r) Eintrag/Einträge aus {path.name} entfernt.")

    return removed


def reset_history_file(path: Path = HISTORY_PATH) -> None:
    """Completely resets history.json to empty messages."""
    payload = {
        "max_length": 8,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "messages": []
    }
    atomic_save_json(path, payload)
    print(f"[Sanitizer] ✔ {path} auf leeren Nachrichtenverlauf [] zurückgesetzt.")


def sanitize_all(project_root: Optional[Path] = None) -> Dict[str, Any]:
    """Sanitizes both history.json and .brain/session.json for the project."""
    root = Path(project_root) if project_root else PROJECT_ROOT
    h_file = root / "history.json"
    s_file = root / ".brain" / "session.json"

    h_removed = sanitize_history_file(h_file)
    s_removed = sanitize_session_file(s_file)

    return {
        "history_removed": h_removed,
        "session_removed": s_removed,
        "clean": (h_removed == 0 and s_removed == 0)
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Outer Heaven Companion History Sanitizer")
    parser.add_argument("--reset-history", action="store_true", help="Setzt history.json komplett auf [] zurueck")
    parser.add_argument("--clean", action="store_true", help="Bereinigt history.json und session.json von korrumpierten Eintraegen")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT), help="Projekt-Stammverzeichnis")

    args = parser.parse_args()
    root = Path(args.project_root)

    if args.reset_history:
        reset_history_file(root / "history.json")
        sanitize_session_file(root / ".brain" / "session.json")
    else:
        res = sanitize_all(root)
        print(f"[Sanitizer] Status: history_removed={res['history_removed']}, session_removed={res['session_removed']}")
