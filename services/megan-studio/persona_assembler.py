"""
persona_assembler.py - Stufe 3: Dynamischer Prompt-Assembler & Situations-Injektor

Aufbau des System-Prompts:
  1. Base Injection:   Stufe 1 (megan_canonical_lore.json) + Stufe 2 (persona_profile.json)
  2. Sprachklammer:    deterministische deutsche Ausgabe, fest am Ende des System-Headers
  3. Situation/Task:   Uhrzeit, Tagesphase, offene Aufgaben, Render-Status, Gedächtnis-Snippets
Zusätzlich: StreamGuard (Stream-Schutz gegen englische Floskeln vor WebSocket-Dispatch).

Beide Config-Dateien werden bei jedem Aufruf per mtime neu geladen (hot-swap).
"""

import json
import logging
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("persona-assembler")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("MEGAN_CONFIG_DIR", str(BASE_DIR / "config")))
LORE_FILE = CONFIG_DIR / "megan_canonical_lore.json"
PERSONA_FILE = CONFIG_DIR / "persona_profile.json"
PRESETS_DIR = CONFIG_DIR / "persona_presets"

# Schlüssel, die im Canon-Core NICHT vorkommen dürfen (gehören in Stufe 2)
FORBIDDEN_LORE_KEYS = {
    "gender", "geschlecht", "pronouns", "appearance", "aussehen", "body", "koerper", "körper", "körperbau",
    "height", "height_cm", "größe", "groesse", "hair", "hair_color", "haarfarbe", "eyes", "eye_color", "augenfarbe",
}

LANGUAGE_CLAMP = (
    "[SPRACHKLAMMER - UNVERRÜCKBAR]\n"
    "Du denkst und antwortest AUSNAHMSLOS auf Deutsch - auch in internen Denkschritten (<think>). "
    "Beginne jede Antwort direkt auf Deutsch. Keine englischen Wörter, Floskeln oder Einleitungen "
    "(z. B. \"Sure\", \"Certainly\", \"Okay\", \"Here is\", \"I will\", \"Let me\")."
)

DEFAULT_LORE: Dict[str, Any] = {
    "identity": {"name": "Megan", "role": "persönliche, loyale Assistentin von Big Boss"},
    "traits": ["hochintelligent", "charmant", "direkt", "verspielt", "loyal"],
    "speech": {"language": "Deutsch", "tone": "warm und selbstbewusst"},
    "rules": ["Du bist MEGAN, niemals Hermes.", "Dein Gesprächspartner heißt Boss oder Big Boss."],
}
DEFAULT_PERSONA: Dict[str, Any] = {
    "identity": {"gender": "weiblich"},
    "appearance": {},
    "roleplay": {"mode": "Begleiterin"},
}

_cache: Dict[str, Any] = {}


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp_{secrets.token_hex(4)}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    """mtime-basierter Cache → Änderungen an der Datei greifen sofort (hot-swap)."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return dict(default)
    hit = _cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet")
        _cache[str(path)] = (mtime, data)
        return data
    except Exception as e:
        logger.error(f"{path.name} fehlerhaft ({e}) – nutze Fallback")
        return dict(default)


def load_lore() -> Dict[str, Any]:
    return _load_json(LORE_FILE, DEFAULT_LORE)


def load_persona() -> Dict[str, Any]:
    return _load_json(PERSONA_FILE, DEFAULT_PERSONA)


# ------------------------------------------------------------------------------
# Validierung / Schreiben (für Mission-Control-APIs)
# ------------------------------------------------------------------------------
def _find_forbidden(d: Any, path: str = "") -> List[str]:
    found = []
    if isinstance(d, dict):
        for k, v in d.items():
            if str(k).lower() in FORBIDDEN_LORE_KEYS:
                found.append(f"{path}{k}")
            found += _find_forbidden(v, f"{path}{k}.")
    return found


def save_lore(data: Dict[str, Any]) -> None:
    bad = _find_forbidden(data)
    if bad:
        raise ValueError(f"Canon-Core darf keine physischen Attribute enthalten: {', '.join(bad)} (→ persona_profile.json)")
    _atomic_write(LORE_FILE, data)


def save_persona(patch: Dict[str, Any], replace: bool = False) -> Dict[str, Any]:
    """Persona-Sheet aktualisieren (Deep-Merge). Canon-Core wird nie angefasst."""
    current = {} if replace else json.loads(json.dumps(load_persona()))

    def merge(a: Dict[str, Any], b: Dict[str, Any]) -> None:
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v

    if not isinstance(patch, dict):
        raise ValueError("Objekt erwartet")
    merge(current, patch)
    _atomic_write(PERSONA_FILE, current)
    return current


def list_presets() -> List[str]:
    return sorted(p.stem for p in PRESETS_DIR.glob("*.json")) if PRESETS_DIR.is_dir() else []


def activate_preset(name: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[\w\-]+", name or ""):
        raise ValueError("Ungültiger Preset-Name")
    p = PRESETS_DIR / f"{name}.json"
    if not p.is_file():
        raise FileNotFoundError(name)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["preset_name"] = name
    _atomic_write(PERSONA_FILE, data)
    return data


def save_preset(name: str) -> None:
    if not re.fullmatch(r"[\w\-]+", name or ""):
        raise ValueError("Ungültiger Preset-Name")
    _atomic_write(PRESETS_DIR / f"{name}.json", load_persona())


# ------------------------------------------------------------------------------
# Renderer
# ------------------------------------------------------------------------------
def _bullets(items: Any) -> str:
    if isinstance(items, str):
        items = [items]
    return "\n".join(f"- {i}" for i in (items or []) if i)


def render_lore(lore: Dict[str, Any]) -> str:
    ident = lore.get("identity", {})
    parts = [
        f"Du bist {ident.get('name', 'Megan')}, {ident.get('role', 'die persönliche Assistentin von Big Boss')}.",
    ]
    if ident.get("user_address"):
        parts.append("Du sprichst deinen Nutzer als " + " oder ".join(f"'{a}'" for a in ident["user_address"]) + " an.")
    if lore.get("traits"):
        parts.append("[WESENSZÜGE]\n" + _bullets(lore["traits"]))
    intel = lore.get("intelligence")
    if intel:
        parts.append(f"[INTELLIGENZ] {intel.get('level', '')}; {intel.get('style', '')}".strip("; "))
    loy = lore.get("loyalty")
    if loy:
        parts.append(f"[LOYALITÄT] gegenüber {loy.get('to', 'dem Boss')}\n" + _bullets(loy.get("principles")))
    tech = lore.get("tech_affinity")
    if tech:
        parts.append(f"[TECHNIK-AFFINITÄT] {', '.join(tech.get('domains', []))}. {tech.get('attitude', '')}")
    if lore.get("values"):
        parts.append("[WERTE] " + ", ".join(lore["values"]))
    sp = lore.get("speech")
    if sp:
        s = f"[SPRECHDYNAMIK & TONALITÄT] {sp.get('dynamics', '')} Ton: {sp.get('tone', '')}."
        if sp.get("forbidden"):
            s += "\nVermeide: " + "; ".join(sp["forbidden"])
        parts.append(s)
    if lore.get("rules"):
        parts.append("[STRIKTE ROLLEN- & SYSTEM-REGELN]\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(lore["rules"], 1)))
    if (lore.get("freitext_ergaenzung") or "").strip():
        parts.append("[ERGÄNZUNG]\n" + lore["freitext_ergaenzung"].strip())
    return "\n\n".join(p for p in parts if p)


def render_persona(p: Dict[str, Any]) -> str:
    ident, app, rp = p.get("identity", {}), p.get("appearance", {}), p.get("roleplay", {})
    lines = []
    if ident.get("gender"):
        lines.append(f"Geschlecht/Identität: {ident['gender']}" + (f" ({ident['pronouns']})" if ident.get("pronouns") else ""))
    labels = [("height_cm", "Körpergröße", " cm"), ("build", "Körperbau", ""), ("hair_color", "Haarfarbe", ""),
              ("eye_color", "Augenfarbe", ""), ("style", "Stil/Ästhetik", "")]
    for key, label, suffix in labels:
        if app.get(key) not in (None, ""):
            lines.append(f"{label}: {app[key]}{suffix}")
    if rp.get("mode"):
        lines.append(f"Aktueller Rollenmodus: {rp['mode']}")
    if rp.get("scenario"):
        lines.append(f"Szenario: {rp['scenario']}")
    if rp.get("notes"):
        lines.append(f"Hinweise: {rp['notes']}")
    return "[PERSONA & AVATAR]\n" + _bullets(lines) if lines else ""


def build_header(legacy_lore: Optional[str] = None) -> str:
    """Stufe 1 + 2 + Sprachklammer (Klammer steht immer am Ende)."""
    if LORE_FILE.is_file():
        base = render_lore(load_lore())
    else:  # Fallback bis Migration gelaufen ist
        base = (legacy_lore or "").strip() or render_lore(DEFAULT_LORE)
    return "\n\n".join(x for x in (base, render_persona(load_persona()), LANGUAGE_CLAMP) if x)


_PHASE_DE = {"morning": "Morgen", "day": "Tag", "evening": "Abend", "night": "Nacht"}
_WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
_RENDER_HINT = re.compile(r"\b(bild|foto|selfie|render|comfy|gpu|grafikkarte|generier|queue|warteschlange)\w*", re.I)


def build_situation(
    user_text: str = "",
    phase: Optional[str] = None,
    open_tasks: Optional[List[Dict[str, Any]]] = None,
    render_status: Optional[Dict[str, Any]] = None,
    memory_snippets: Optional[List[str]] = None,
) -> str:
    now = datetime.now()
    if phase is None:
        h = now.hour
        phase = "morning" if 6 <= h < 11 else "day" if 11 <= h < 18 else "evening" if 18 <= h < 23 else "night"
    lines = [f"Aktuelle Zeit: {_WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y %H:%M} Uhr (Tagesphase: {_PHASE_DE.get(phase, phase)})"]

    if open_tasks:
        lines.append("Offene Aufgaben (nur bei Bedarf erwähnen):")
        for t in open_tasks[:5]:
            lines.append(f"  - #{t.get('id')} {t.get('title')} [{t.get('status')}, {t.get('assignee', 'Hermes')}]")

    # Render-Status nur injizieren, wenn relevant (Regel: ComfyUI-Abstinenz im Normal-Chat)
    if render_status and (render_status.get("busy") or _RENDER_HINT.search(user_text or "")):
        lines.append(
            "ComfyUI-Render: " + (f"aktiv ({render_status.get('running_count', 0)} laufend, {render_status.get('pending_count', 0)} wartend)"
                                   if render_status.get("busy") else "frei")
            + " - nur erwähnen, wenn der Boss danach fragt."
        )
    block = "[SITUATION]\n" + "\n".join(lines)
    if memory_snippets:
        block += "\n\n[GEDÄCHTNIS - Was du über den Boss gelernt hast; nutze es natürlich, ohne es aufzuzählen]\n" + _bullets(memory_snippets)
    return block


def assemble_system_prompt(
    user_text: str = "",
    legacy_lore: Optional[str] = None,
    situation_provider: Optional[Callable[[str], Dict[str, Any]]] = None,
    memory_provider: Optional[Callable[[str], List[str]]] = None,
) -> str:
    """Header (Lore + Persona + Sprachklammer) gefolgt von dynamischer Situation."""
    header = build_header(legacy_lore)
    try:
        sit_kwargs = situation_provider(user_text) if situation_provider else {}
    except Exception as e:
        logger.warning(f"Situation-Provider fehlgeschlagen: {e}")
        sit_kwargs = {}
    try:
        snippets = memory_provider(user_text) if memory_provider else []
    except Exception as e:
        logger.warning(f"Memory-Provider fehlgeschlagen: {e}")
        snippets = []
    return f"{header}\n\n{build_situation(user_text, memory_snippets=snippets, **sit_kwargs)}"


# ------------------------------------------------------------------------------
# Stream-Schutz
# ------------------------------------------------------------------------------
ENGLISH_INTRO_RE = re.compile(
    r"^\W*(?:(?:Sure(?: thing)?|Certainly|Okay|Ok|Alright|Hello|Hi|Of course|Absolutely|Understood|Got it|Great|"
    r"Here is|Here's|Here are|I will|I'll|I can|Let me|As Megan)\b[,!\.:\s]*)+",
    re.IGNORECASE,
)


def strip_english_intro(text: str) -> str:
    return ENGLISH_INTRO_RE.sub("", text).lstrip()


class StreamGuard:
    """
    Puffert die ersten ~hold_tokens sichtbaren Tokens, verwirft englische Einleitungsfloskeln
    serverseitig und filtert <think>-Blöcke – erst danach wird an den WebSocket weitergereicht.
    Nutzung: out = guard.feed(token) (leer = noch gepuffert); am Ende guard.flush().
    """

    def __init__(self, hold_tokens: int = 10, max_hold_chars: int = 90):
        self.hold_tokens = hold_tokens
        self.max_hold_chars = max_hold_chars
        self._raw = ""
        self._head = ""
        self._tokens = 0
        self._in_think = False
        self.released = False

    def _drain_think(self) -> str:
        visible = ""
        while True:
            if self._in_think:
                i = self._raw.find("</think>")
                if i == -1:
                    self._raw = self._raw[-8:]
                    return visible
                self._raw = self._raw[i + 8:]
                self._in_think = False
            else:
                i = self._raw.find("<think>")
                if i != -1:
                    visible += self._raw[:i]
                    self._raw = self._raw[i + 7:]
                    self._in_think = True
                    continue
                keep = 0
                for k in range(min(6, len(self._raw)), 0, -1):
                    if "<think>".startswith(self._raw[-k:]):
                        keep = k
                        break
                visible += self._raw[: len(self._raw) - keep]
                self._raw = self._raw[len(self._raw) - keep:]
                return visible

    def _release(self) -> str:
        cleaned = strip_english_intro(self._head)
        if cleaned != self._head.lstrip():
            logger.info(f"[StreamGuard] englische Einleitung verworfen: {self._head[:40]!r}")
        self._head = ""
        self.released = True
        return cleaned

    def feed(self, token: str) -> str:
        self._raw += token
        visible = self._drain_think()
        if not visible:
            return ""
        if self.released:
            return visible
        self._head += visible
        self._tokens += 1
        if self._tokens >= self.hold_tokens or len(self._head) >= self.max_hold_chars:
            return self._release()
        return ""

    def flush(self) -> str:
        tail = "" if self._in_think else self._raw
        self._raw = ""
        if self.released:
            return tail
        self._head += tail
        return self._release()
