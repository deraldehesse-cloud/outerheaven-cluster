#!/usr/bin/env python3
"""
migrate_persona.py - Migration & Bereinigung für die 3-Stufen-Persona-Engine.

- Sichert Legacy-Prompt-Dateien (character.json, megan_system_prompt.txt) nach config/legacy_backup_<ts>/
- Legt fehlende Config-Dateien an (Canon-Lore, Persona-Sheet) aus eingebauten Defaults
- Prüft, dass der Canon-Core keine physischen Attribute enthält
- Initialisiert megan_memory.db und übernimmt alte Präferenzen (preferences.json) als Langzeit-Fakten
- Räumt __pycache__ und verwaiste *.tmp_* Dateien auf
- Führt einen StreamGuard-Selbsttest aus
Aufruf: python3 migrate_persona.py [--dry-run]
"""
import json
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

SEXCHAT = Path("/mnt/storage-beta/projekte/projekt-sexchat")
LEGACY = [SEXCHAT / "character.json", SEXCHAT / "docs" / "megan_system_prompt.txt"]
DRY = "--dry-run" in sys.argv


def log(msg: str) -> None:
    print(("[dry-run] " if DRY else "") + msg)


def main() -> int:
    import persona_assembler as pa

    # 1. Backup Legacy
    existing = [p for p in LEGACY if p.is_file()]
    if existing:
        dest = pa.CONFIG_DIR / f"legacy_backup_{time.strftime('%Y%m%d_%H%M%S')}"
        for p in existing:
            log(f"Backup {p} → {dest}")
            if not DRY:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest / p.name)

    # 2. Config-Dateien
    for path, default in ((pa.LORE_FILE, pa.DEFAULT_LORE), (pa.PERSONA_FILE, pa.DEFAULT_PERSONA)):
        if path.is_file():
            log(f"OK vorhanden: {path.name}")
        else:
            log(f"Erzeuge {path.name} aus Defaults")
            if not DRY:
                pa._atomic_write(path, default)

    # 3. Canon-Validierung
    bad = pa._find_forbidden(pa.load_lore())
    if bad:
        log(f"FEHLER: Canon enthält physische Attribute: {bad}")
        return 1
    log("Canon-Core ist frei von physischen Attributen ✔")

    # 4. Memory-DB
    import megan_learning as ml
    prefs_file = BASE / "data" / "memory" / "preferences.json"
    if prefs_file.is_file():
        try:
            prefs = json.loads(prefs_file.read_text(encoding="utf-8"))
            n = 0
            for k, v in (prefs.items() if isinstance(prefs, dict) else []):
                if isinstance(v, (str, int, float)) and str(v).strip():
                    log(f"Übernehme Präferenz {k}={v}")
                    if not DRY:
                        ml.remember("praeferenz", f"{k}: {v}", tier="long", source="migration")
                    n += 1
            log(f"{n} Präferenzen übernommen")
        except Exception as e:
            log(f"Präferenzen nicht lesbar: {e}")
    log(f"Memory-DB: {ml.DB_PATH}")

    # 5. Cleanup
    for pc in BASE.rglob("__pycache__"):
        if "workflows" in pc.parts:
            continue
        log(f"Entferne {pc}")
        if not DRY:
            shutil.rmtree(pc, ignore_errors=True)
    for tmp in list(BASE.rglob("*.tmp_*")):
        log(f"Entferne {tmp}")
        if not DRY:
            tmp.unlink(missing_ok=True)

    # 6. Selbsttest StreamGuard
    g = pa.StreamGuard(hold_tokens=10)
    out = "".join(g.feed(t) for t in ["Sure", ",", " here", " is", " mein", " Bericht", ",", " Boss", ".", " Alles", " klar", "!"]) + g.flush()
    assert not out.lower().startswith(("sure", "here")), out
    log(f"StreamGuard-Selbsttest ✔ → {out!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
