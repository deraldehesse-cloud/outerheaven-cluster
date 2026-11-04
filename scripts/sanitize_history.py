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
        clean_w = w.strip(".,!?:;\"'()[]{}<>")
        if clean_w.startswith("http://") or clean_w.startswith("https://") or clean_w.startswith("/") or clean_w.startswith("hf.co"):
            continue
        # Split tokens separated by underscores, hyphens, slashes, brackets or colons (e.g. tags or paths)
        subparts = [p for p in re.split(r"[-_/:<>]", clean_w) if p]
        max_subpart_len = max([len(p) for p in subparts], default=len(clean_w))
        if max_subpart_len > 32:
            return True, f"Abnormal langes Kunstwort / BPE-Verschmelzung ({clean_w[:20]}...)"

    # Schizophrenic / English hallucinations & out-of-character English chatter
    if re.search(r"^##\.\s*\(t\)", t) or re.search(r"\b(?:the death in the Death|Queen'ship|nameless but he is not dead)\b", t, re.IGNORECASE):
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

    # 9. Prompt-Leaking / System-Instruktionen
    if re.search(r"\[(?:Wenn Big Boss|SYSTEM-|Spontaneous Daytime|IMAGE:\s*<|IMAGE:\s*1girl.*?\.{3})", t, re.IGNORECASE):
        return True, "System-Prompt-Direktive / Leaking-Fragment"
    if re.search(r"\b(?:Wenn Big Boss nach einem Foto fragt|AUTONOME PROMPT-ERSTELLUNG|ROLLENTREUE:)\b", t, re.IGNORECASE):
        return True, "System-Prompt-Instruktion im Text"
    if re.search(r"\b###\s*(?:UNVERRÜCKBARE|MEGAN AUTONOMIE|YOUR LONG-TERM|PERSÖNLICHES INTERESSE|LOCAL PROJECT|FOTO-ANWEISUNG)", t, re.IGNORECASE):
        return True, "System-Prompt-Header im Text"

    # 10. Out-of-Character: Megan verlangt ComfyUI-Tags / Prompts vom User
    if re.search(r"\b(?:gib mir|schick mir|nenn mir|sag mir|brauch(?:e)?)\b.*?\b(?:comfyui[- ](?:prompt|tag)|danbooru|sd[- ]tag|bild[- ]tag)", t, re.IGNORECASE):
        return True, "Aufforderung zu ComfyUI-Tags (Rollenbruch)"
    if re.search(r"\b(?:deinen|deine|gewünschten)\s+comfyui[- ](?:prompt|tag)", t, re.IGNORECASE):
        return True, "ComfyUI-Prompt-Abfrage (Rollenbruch)"

    # 11. Out-of-Character: Meta-Berater / Schreibcoach Rollenbruch
    if re.search(r"\b(?:Viel Erfolg (?:bei|mit) deiner Kommunikation|Ich helfe dir bei Big Boss|in deiner Kommunikation mit Big Boss|Lass uns ein gutes Gespräch führen|Ratschläge darüber, wie man mit Big Boss redet)\b", t, re.IGNORECASE):
        return True, "Meta-Berater / Schreibcoach Rollenbruch"

    # 12. Male anatomy confusion for Megan
    if re.search(r"\bmein(?:er|en)?\s+(?:prügel|penis|schwanz|riemen|zauberstab)\b", t, re.IGNORECASE):
        return True, "Anatomie-Verwechslung (Männliche Geschlechtsmerkmale bei Megan)"

    # 13. Letter format leftovers
    if re.search(r"\b(?:Liebe[rs]?\s+(?:Big Boss|Sebastian|Basti|Süßer))\b", t, re.IGNORECASE):
        return True, "Brief-Format Anrede (Rollenbruch)"
    if re.search(r"\b(?:Deine Megan|In Liebe(?:,| deine)?\s+Megan)\b", t, re.IGNORECASE):
        return True, "Brief-Format Schlussformel (Rollenbruch)"
    if re.search(r"\bP\.?S\.?:", t, re.IGNORECASE):
        return True, "Brief-Format P.S.-Zusatz (Rollenbruch)"

    return False, ""


TRUNCATE_MARKERS = [
    "\nUser:", "\nBig Boss:", "Big Boss:",
    "\nSebastian:", "Sebastian:",
    "\n[", "[Spontaneous",
    "Wenn Big Boss", "[Wenn Big Boss",
    "FOTO-ANWEISUNG", "###",
    "ComfyUI-Prompt:", "(ComfyUI-Prompt:",
    "SYSTEM-DIREKTIVE", "[SYSTEM-",
    "AUTONOME PROMPT-ERSTELLUNG", "ROLLENTREUE:",
    "<turn|>", "<|turn|>", "<|im_end|>", "<|end_of_text|>", "<|eot_id|>"
]

LEAK_LINE_PREFIXES = (
    "wenn big boss",
    "###",
    "foto-",
    "fotoanweisung",
    "system-",
    "[system",
    "[wenn big boss",
    "(comfyui",
    "comfyui-prompt",
    "autonome prompt",
    "rollentreue"
)

BRACKET_START_REGEX = re.compile(r"^\[(?:Spontaneous|Context|System|OOC|Thought|Scene)[^\]]*\]:?\s*", re.IGNORECASE)
FOREIGN_ROLE_START_REGEX = re.compile(r"^(?:User|Big Boss|Sebastian):", re.IGNORECASE)


def clean_text_content(role: str, text: str, user_name: str = "Big Boss") -> Tuple[str, bool, str]:
    """
    Sanitizes single message content:
    - Truncates leaks at any marker in TRUNCATE_MARKERS or user-specific marker
    - Cuts off lines starting with system directives
    - Strips sentences where assistant asks user for ComfyUI-Tags / technical prompts
    - Strips placeholders like <dein_ausgearbeiteter_prompt> or <passende_tags>
    - Strips stray asterisks
    - Validates against is_corrupted_text
    Returns: (cleaned_text, is_valid, reason_if_invalid)
    """
    if not text or not isinstance(text, str):
        return "", False, "Leerer oder ungültiger Text"

    t = text.strip()
    if not t:
        return "", False, "Leere Nachricht"

    if role == "assistant":
        # Check foreign role prefix or pure system bracket
        if FOREIGN_ROLE_START_REGEX.search(t) or (t.startswith("[") and t.endswith("]") and BRACKET_START_REGEX.search(t)):
            return "", False, "Fremde Rolle oder System-Bracket"

        # 1. Truncate at leak markers
        markers = list(TRUNCATE_MARKERS)
        if user_name:
            markers.extend([f"Wenn {user_name}", f"[Wenn {user_name}", f"{user_name}:"])

        earliest_idx = -1
        for marker in markers:
            idx = t.find(marker)
            if idx != -1:
                if earliest_idx == -1 or idx < earliest_idx:
                    earliest_idx = idx

        if earliest_idx != -1:
            t = t[:earliest_idx].rstrip()

        # 2. Line-by-line prefix cutoff
        clean_lines = []
        for line in t.splitlines():
            s_lower = line.strip().lower()
            if any(s_lower.startswith(pfx) for pfx in LEAK_LINE_PREFIXES):
                break
            if user_name and (s_lower.startswith(f"wenn {user_name.lower()}") or s_lower.startswith(f"[wenn {user_name.lower()}")):
                break
            clean_lines.append(line)
        t = "\n".join(clean_lines).strip()

        # 3. Strip sentences where assistant asks user for ComfyUI tags / prompt
        if re.search(r"\b(?:gib mir|schick mir|nenn mir|sag mir|brauch(?:e)?|deinen|deine)\b.*?\b(?:comfyui|danbooru|sd[- ]tag)", t, re.IGNORECASE):
            sentences = re.split(r"(?<=[.!?\n])\s+", t)
            kept = [
                s for s in sentences
                if not re.search(r"\b(?:gib mir|schick mir|nenn mir|sag mir|brauch(?:e)?|deinen|deine)\b.*?\b(?:comfyui|danbooru|sd[- ]tag)", s, re.IGNORECASE)
            ]
            t = " ".join(kept).strip()

        # 4. Strip placeholders
        t = re.sub(r"<\s*(?:dein_ausgearbeiteter_prompt|passende_tags|weitere_tags)[^>]*>", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\[IMAGE:\s*1girl,\s*megan,\s*solo,\s*cute\s*face.*?\.{3}\s*\]", "", t, flags=re.IGNORECASE)

        # 5. Strip residual asterisks stage directions
        t = re.sub(r"\*[^*]+\*", "", t).strip()
        t = re.sub(r"\n{3,}", "\n\n", t).strip()

        # 6. Strip letter greetings (e.g. "Liebe Big Boss,", "Lieber Big Boss,")
        t = re.sub(r"^(?:Liebe[rs]?\s+(?:Big Boss|Sebastian|Basti|Süßer)[,:]?\s*\n*)+", "", t, flags=re.IGNORECASE).strip()

        # 7. Strip sign-offs like "Deine Megan 💕...", "In Liebe, deine Megan", "Deine Megan 🚀💫"
        t = re.sub(r"(?:\n|^)\s*(?:Deine|In Liebe(?:,| deine)?)\s+Megan[^\n]*$", "", t, flags=re.IGNORECASE).strip()

        # 8. Strip P.S. lines / trailers
        t = re.sub(r"(?:\n|^)\s*P\.?S\.?:?.*$", "", t, flags=re.IGNORECASE | re.DOTALL).strip()

        # 9. Male anatomy confusion for Megan: strip sentences where Megan claims male anatomy
        if re.search(r"\bmein(?:er|en)?\s+(?:prügel|penis|schwanz|riemen|zauberstab)\b", t, re.IGNORECASE):
            sentences = re.split(r"(?<=[.!?\n])\s+", t)
            kept = [
                s for s in sentences
                if not re.search(r"\bmein(?:er|en)?\s+(?:prügel|penis|schwanz|riemen|zauberstab)\b", s, re.IGNORECASE)
            ]
            t = " ".join(kept).strip()

    if not t.strip():
        return "", False, "Inhalt nach Bereinigung leer"

    corrupted, reason = is_corrupted_text(t)
    if corrupted:
        return "", False, reason

    return t.strip(), True, ""


def sanitize_history_data_detailed(messages: List[Dict[str, Any]], user_name: str = "Big Boss") -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Cleans a list of message dictionaries:
    - Filters out corrupted, leaked, or empty messages
    - Cleans inline leaks/trailers from messages
    - Deduplicates identical consecutive messages
    Returns: (cleaned_messages, removed_count, modified_count)
    """
    cleaned: List[Dict[str, Any]] = []
    removed_count = 0
    modified_count = 0
    last_content = None
    last_role = None

    for msg in messages:
        if not isinstance(msg, dict):
            removed_count += 1
            continue

        raw_content = msg.get("content", "")
        role = msg.get("role", "user")

        if not isinstance(raw_content, str) or not raw_content.strip():
            removed_count += 1
            continue

        clean_content, is_valid, reason = clean_text_content(role, raw_content, user_name=user_name)
        if not is_valid or not clean_content:
            removed_count += 1
            continue

        is_mod = (clean_content != raw_content.strip())
        if is_mod:
            modified_count += 1

        # Deduplicate exact consecutive messages (avoid repetition loops)
        if clean_content == last_content and role == last_role:
            removed_count += 1
            continue

        new_entry = dict(msg)
        new_entry["content"] = clean_content
        cleaned.append(new_entry)
        last_content = clean_content
        last_role = role

    return cleaned, removed_count, modified_count


def sanitize_history_data(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """
    Filters corrupted messages out of a history message list and truncates rogue markers.
    Backward-compatible wrapper returning (cleaned_messages, removed_count).
    """
    cleaned, removed_count, _ = sanitize_history_data_detailed(messages)
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
        if 'tmp' in locals() and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return False


def sanitize_history_file(path: Path = HISTORY_PATH, max_length: int = 30, user_name: str = "Big Boss") -> Tuple[int, int]:
    """
    Scans and cleans history.json, creating a backup before saving.
    Returns: (removed_count, modified_count)
    """
    if not path.is_file():
        return 0, 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Sanitizer] Konnte {path} nicht parsen: {e}")
        return 0, 0

    messages = data.get("messages", [])
    initial_count = len(messages)
    cleaned_messages, removed, modified = sanitize_history_data_detailed(messages, user_name=user_name)

    effective_limit = max(15, max_length)
    if len(cleaned_messages) > effective_limit:
        removed += (len(cleaned_messages) - effective_limit)
        cleaned_messages = cleaned_messages[-effective_limit:]

    if removed > 0 or modified > 0 or len(cleaned_messages) != initial_count:
        data["messages"] = cleaned_messages
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Backup before writing
        bak = path.with_suffix(".json.bak")
        try:
            import shutil
            shutil.copy2(path, bak)
        except Exception as be:
            print(f"[Sanitizer] Backup-Fehler vor Save: {be}")
        atomic_save_json(path, data)
        print(f"[Sanitizer] ✔ {removed} entfernt, {modified} bereinigt in {path.name} (neu: {len(cleaned_messages)} Einträge).")

    return removed, modified


def sanitize_session_file(path: Path = SESSION_PATH, user_name: str = "Big Boss") -> int:
    """Scans and cleans .brain/session.json history entries."""
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
    modified = 0

    for entry in history_entries:
        fb = entry.get("feedback", "")
        act = entry.get("actions", "")

        clean_fb, valid_fb, _ = clean_text_content("assistant", str(fb), user_name=user_name)
        clean_act, valid_act, _ = clean_text_content("user", str(act), user_name=user_name)

        if not valid_fb or not valid_act:
            removed += 1
            continue

        if clean_fb != fb or clean_act != act:
            modified += 1
            entry = dict(entry)
            entry["feedback"] = clean_fb
            entry["actions"] = clean_act

        cleaned_entries.append(entry)

    if removed > 0 or modified > 0:
        data["history"] = cleaned_entries
        atomic_save_json(path, data)
        print(f"[Sanitizer] ✔ {removed} entfernt, {modified} bereinigt in {path.name}.")

    return removed


def maintain_clean_history(
    history_file: Union[Path, str] = HISTORY_PATH,
    session_file: Optional[Union[Path, str]] = SESSION_PATH,
    max_length: int = 30,
    user_name: str = "Big Boss"
) -> Dict[str, Any]:
    """
    All-in-one general maintenance function to keep history.json (and .brain/session.json) clean.
    - Loads history safely
    - Scans and cleans all corrupted, leaked, or out-of-character entries
    - Sanitizes system prompt leaks, ComfyUI tag requests, and glitch tokens
    - Deduplicates identical consecutive messages
    - Enforces ring buffer size
    - Backs up if modifications are made
    - Atomically saves cleaned history
    - Also cleans .brain/session.json history/feedback entries
    Returns summary dict with stats.
    """
    h_path = Path(history_file)
    s_path = Path(session_file) if session_file else None

    h_removed, h_mod = sanitize_history_file(h_path, max_length=max_length, user_name=user_name)
    s_removed = sanitize_session_file(s_path, user_name=user_name) if (s_path and s_path.is_file()) else 0

    total_msgs = 0
    if h_path.is_file():
        try:
            with open(h_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                total_msgs = len(d.get("messages", []))
        except Exception:
            pass

    return {
        "history_removed": h_removed,
        "history_modified": h_mod,
        "session_removed": s_removed,
        "total_messages": total_msgs,
        "clean": (h_removed == 0 and h_mod == 0 and s_removed == 0)
    }


def reset_history_file(path: Path = HISTORY_PATH) -> None:
    """Completely resets history.json to empty messages."""
    payload = {
        "max_length": 8,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "messages": []
    }
    atomic_save_json(path, payload)
    print(f"[Sanitizer] ✔ {path} auf leeren Nachrichtenverlauf [] zurückgesetzt.")


def sanitize_all(project_root: Optional[Path] = None, user_name: str = "Big Boss") -> Dict[str, Any]:
    """Sanitizes both history.json and .brain/session.json for the project."""
    root = Path(project_root) if project_root else PROJECT_ROOT
    h_file = root / "history.json"
    s_file = root / ".brain" / "session.json"

    return maintain_clean_history(history_file=h_file, session_file=s_file, user_name=user_name)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Outer Heaven Companion History Sanitizer")
    parser.add_argument("--reset-history", action="store_true", help="Setzt history.json komplett auf [] zurueck")
    parser.add_argument("--clean", action="store_true", help="Bereinigt history.json und session.json von korrumpierten Eintraegen")
    parser.add_argument("--watch", type=int, nargs="?", const=60, help="Startet kontinuierliche Überwachung (z. B. alle 60 Sekunden)")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT), help="Projekt-Stammverzeichnis")

    args = parser.parse_args()
    root = Path(args.project_root)

    if args.reset_history:
        reset_history_file(root / "history.json")
        sanitize_session_file(root / ".brain" / "session.json")
    elif args.watch:
        interval = max(5, args.watch)
        print(f"[Sanitizer] Starte kontinuierliche Überwachung von {root} (Intervall: {interval}s)...")
        try:
            while True:
                res = maintain_clean_history(root / "history.json", root / ".brain" / "session.json")
                if not res["clean"]:
                    print(f"[{time.strftime('%X')}] Bereinigung durchgeführt: {res}")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[Sanitizer] Überwachung beendet.")
    else:
        res = maintain_clean_history(root / "history.json", root / ".brain" / "session.json")
        print(f"[Sanitizer] Status: history_removed={res['history_removed']}, history_modified={res['history_modified']}, session_removed={res['session_removed']}, total_messages={res['total_messages']}")
