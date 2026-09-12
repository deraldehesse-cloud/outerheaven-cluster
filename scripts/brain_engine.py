#!/usr/bin/env python3
"""
brain_engine.py - Autonomous Hybrid-Gedächtnis & Knowledge Hub
Outer Heaven Cluster Infrastructure

Features:
1. Global Tool Knowledge Sharing (.shared_brain/tools/):
   - comfyui.md, godot.md, blender.md, google_services.md, rules.md
2. Strict Airgap & Quarantine for projekt-sexchat:
   - ZERO LEAK: Never loads .shared_brain/ and never writes back learnings.
3. Autonomous Reflection & Pruning (reflect_and_learn):
   - Extracts distilled best practices via local Ollama (deepseek-r1:14b).
   - Auto-pruning: Automatically consolidates tool files when entries exceed 30.
"""

import os
import sys
import json
import time
import shutil
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional

SHARED_BRAIN_PATH = Path("/mnt/storage-beta/projekte/.shared_brain")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:14b")
AIRGAP_PROJECTS = {"projekt-sexchat"}


def is_airgapped_project(project_path: str) -> bool:
    """Checks whether the given project path is in the quarantined airgap zone."""
    resolved = Path(project_path).resolve().name
    return any(ag in resolved or ag in str(project_path) for ag in AIRGAP_PROJECTS)


def load_project_brain(project_path: str) -> Dict[str, Any]:
    """
    Loads project-specific memory (.brain/) and merges with global shared brain (.shared_brain/)
    EXCEPT for airgapped projects (projekt-sexchat), which receive strictly isolated local context.
    """
    p_path = Path(project_path).resolve()
    local_brain = p_path / ".brain"
    local_brain.mkdir(parents=True, exist_ok=True)

    # 1. Local project context
    context_file = local_brain / "context.md"
    session_file = local_brain / "session.json"

    context_text = ""
    if context_file.is_file():
        context_text = context_file.read_text(encoding="utf-8", errors="replace")

    session_data = {}
    if session_file.is_file():
        try:
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            session_data = {}

    # 2. Airgap Check
    if is_airgapped_project(str(p_path)):
        return {
            "project": p_path.name,
            "is_airgapped": True,
            "quarantine_active": True,
            "context": context_text,
            "session": session_data,
            "rules": "STRICT AIRGAP: Zero communication with .shared_brain/.",
            "tools": {}
        }

    # 3. Global Shared Brain Ingestion for standard projects
    tools_dict = {}
    rules_text = ""

    # Allow local repo fallback if running outside live cluster mount
    shared_root = SHARED_BRAIN_PATH
    if not shared_root.is_dir():
        local_fallback = Path(__file__).resolve().parent.parent / "shared_brain"
        if local_fallback.is_dir():
            shared_root = local_fallback

    if shared_root.is_dir():
        rules_file = shared_root / "rules.md"
        if rules_file.is_file():
            rules_text = rules_file.read_text(encoding="utf-8", errors="replace")

        tools_dir = shared_root / "tools"
        if tools_dir.is_dir():
            for t_file in tools_dir.glob("*.md"):
                tool_name = t_file.stem
                tools_dict[tool_name] = t_file.read_text(encoding="utf-8", errors="replace")

    return {
        "project": p_path.name,
        "is_airgapped": False,
        "quarantine_active": False,
        "context": context_text,
        "session": session_data,
        "rules": rules_text,
        "tools": tools_dict
    }


def query_ollama(prompt: str, system_prompt: str = "", model: str = OLLAMA_MODEL) -> str:
    """Queries local Ollama instance and returns generated text."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "options": {
            "temperature": 0.2
        }
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("message", {}).get("content", "").strip()
        # Strip DeepSeek-R1 <think> blocks
        if "<think>" in raw and "</think>" in raw:
            parts = raw.split("</think>", 1)
            raw = parts[1].strip()
        return raw


def auto_prune_tool_file(tool_file: Path, max_entries: int = 30) -> bool:
    """
    Consolidates and prunes a tool knowledge file if it exceeds max_entries lines of learnings.
    Keeps core architectural sections intact.
    """
    if not tool_file.is_file():
        return False

    content = tool_file.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # Count bullet items
    bullet_count = sum(1 for line in lines if line.strip().startswith("- ") or line.strip().startswith("* "))
    if bullet_count <= max_entries:
        return False

    system_prompt = (
        "Du bist der autonome Wissens-Konsolidierer des Outer Heaven Clusters. "
        "Führe redundante Stichpunkte prägnant zusammen. Behalte alle Hardware-Spezifikationen, "
        "funktionierenden Nodes und kritischen Regeln vollständig bei. Fasse verstreute Learnings "
        "auf maximal 20 hochpräzise Best-Practices zusammen."
    )
    prompt = f"Hier ist der aktuelle Wissensstand der Datei {tool_file.name}:\n\n{content}\n\nKonsolidiere den Text sauber in Markdown."

    try:
        pruned_content = query_ollama(prompt, system_prompt)
        if pruned_content and len(pruned_content) > 100:
            tool_file.write_text(pruned_content, encoding="utf-8")
            return True
    except Exception as e:
        print(f"[Warnung] Auto-Pruning fehlgeschlagen: {e}")

    return False


def save_user_preference(project_path: str, key: str, value: Any) -> bool:
    """Saves a learned user preference into the local project .brain/session.json."""
    try:
        p_path = Path(project_path).resolve()
        local_session = p_path / ".brain" / "session.json"
        local_session.parent.mkdir(parents=True, exist_ok=True)
        session_data = {"history": [], "preferences": {}}
        if local_session.is_file():
            try:
                session_data = json.loads(local_session.read_text(encoding="utf-8"))
            except Exception:
                session_data = {"history": [], "preferences": {}}
        session_data.setdefault("preferences", {})[key] = value
        local_session.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[Warnung] Fehler beim Speichern der Präferenz '{key}': {e}")
        return False


def extract_and_save_preferences(project_path: str, user_text: str) -> Dict[str, Any]:
    """
    Lightweight rule-based extraction of user preferences & intimate roleplay details
    for local project memory adaptation.
    """
    extracted = {}
    lower = user_text.lower()

    # Nickname / Name detection
    if any(p in lower for p in ["nenn mich ", "ich heiße ", "mein name ist "]):
        for prefix in ["nenn mich ", "ich heiße ", "mein name ist "]:
            if prefix in lower:
                name_cand = user_text[lower.find(prefix) + len(prefix):].split()[0].strip(".,!?:;\"'")
                if name_cand:
                    extracted["user_nickname"] = name_cand
                    save_user_preference(project_path, "user_nickname", name_cand)
                break

    # Favorite location / setting
    if any(p in lower for p in ["lieblingsort", "am liebsten auf der couch", "am liebsten im bett", "am liebsten am schreibtisch"]):
        if "couch" in lower:
            extracted["preferred_environment"] = "couch"
            save_user_preference(project_path, "preferred_environment", "couch")
        elif "bett" in lower or "schlafzimmer" in lower:
            extracted["preferred_environment"] = "schlafzimmer"
            save_user_preference(project_path, "preferred_environment", "schlafzimmer")
        elif "schreibtisch" in lower or "battlestation" in lower:
            extracted["preferred_environment"] = "schreibtisch"
            save_user_preference(project_path, "preferred_environment", "schreibtisch")

    # Toy intensity preference
    if "stufe" in lower or "intensität" in lower:
        import re
        m = re.search(r'(?:stufe|intensität)\s*(\d{1,2})', lower)
        if m:
            lvl = int(m.group(1))
            extracted["preferred_toy_intensity"] = lvl
            save_user_preference(project_path, "preferred_toy_intensity", lvl)

    return extracted


def reflect_and_learn(project_path: str, feedback: str, actions: str, target_tool: Optional[str] = None) -> Dict[str, Any]:
    """
    Autonomous self-learning routine:
    1. Always updates local project .brain/session.json (Memory-Loop active for all projects).
    2. Strictly blocks any writing from quarantined projects to .shared_brain/ (Zero-Leak).
    3. For standard projects: uses local LLM to extract best-practice bullets into .shared_brain/tools/.
    """
    p_path = Path(project_path).resolve()
    is_airgapped = is_airgapped_project(str(p_path))

    # 1. Update local session (Active for ALL projects including quarantined projekt-sexchat)
    local_session = p_path / ".brain" / "session.json"
    session_data = {"history": [], "preferences": {}}
    if local_session.is_file():
        try:
            session_data = json.loads(local_session.read_text(encoding="utf-8"))
        except Exception:
            session_data = {"history": [], "preferences": {}}

    session_data.setdefault("history", []).append({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "feedback": feedback[:500] if isinstance(feedback, str) else str(feedback),
        "actions": actions[:500] if isinstance(actions, str) else str(actions),
        "target_tool": target_tool
    })
    # Keep session history bounded to last 50 entries
    if len(session_data.get("history", [])) > 50:
        session_data["history"] = session_data["history"][-50:]

    local_session.parent.mkdir(parents=True, exist_ok=True)
    local_session.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. AIRGAP ENFORCEMENT: Strictly block shared brain tool updates for quarantined projects
    if is_airgapped:
        return {
            "success": True,
            "airgap_blocked": True,
            "tool_updated": None,
            "message": f"ZERO-LEAK ENFORCED: {p_path.name} is quarantined. Local session updated, zero knowledge exported to .shared_brain/."
        }

    # 3. If target_tool specified, extract learning and append to shared brain for standard projects
    shared_root = SHARED_BRAIN_PATH
    if not shared_root.is_dir():
        local_fallback = Path(__file__).resolve().parent.parent / "shared_brain"
        if local_fallback.is_dir():
            shared_root = local_fallback

    if target_tool and shared_root.is_dir():
        tool_file = shared_root / "tools" / f"{target_tool}.md"
        if not tool_file.exists():
            tool_file = shared_root / "tools" / "comfyui.md"

        system_prompt = (
            "Du bist die Knowledge-Extraction-Engine von Hermes. "
            "Formuliere aus dem Feedback und den Aktionen exakt 1 bis 2 prägnante, "
            "technische Stichpunkte (Markdown-Bulletpoints '- ...') als Best Practice für künftige Runs. "
            "Keine Einleitung, keine Begrüßung, kein Geschwafel."
        )
        prompt = f"Aktionen:\n{actions}\n\nFeedback / Ergebnis:\n{feedback}"

        try:
            insight = query_ollama(prompt, system_prompt)
            if insight:
                current_text = tool_file.read_text(encoding="utf-8", errors="replace") if tool_file.is_file() else f"# {target_tool.capitalize()} Knowledge\n"
                new_text = current_text.rstrip() + f"\n- [{time.strftime('%Y-%m-%d')}] {insight.strip()}\n"
                tool_file.write_text(new_text, encoding="utf-8")
                pruned = auto_prune_tool_file(tool_file, max_entries=30)
                return {
                    "success": True,
                    "airgap_blocked": False,
                    "tool_updated": tool_file.name,
                    "insight": insight,
                    "pruned": pruned
                }
        except Exception as e:
            return {
                "success": False,
                "airgap_blocked": False,
                "error": str(e)
            }

    return {"success": True, "airgap_blocked": False, "message": "Session updated locally."}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Hermes Brain Engine")
    parser.add_argument("--project", default="/mnt/storage-beta/projekte/projekt-dr-franks-bowling")
    parser.add_argument("--test-airgap", action="store_true")
    args = parser.parse_args()

    if args.test_airgap:
        print("[1/2] Testing Airgapped Project (projekt-sexchat)...")
        b_sexchat = load_project_brain("/mnt/storage-beta/projekte/projekt-sexchat")
        assert b_sexchat["is_airgapped"] is True, "Airgap check failed!"
        assert len(b_sexchat["tools"]) == 0, "Airgap leaked shared tools!"
        print("✔ Airgap verified: Zero shared tools loaded for projekt-sexchat.")

        refl_sex = reflect_and_learn("/mnt/storage-beta/projekte/projekt-sexchat", "Test Feedback", "Test Actions")
        assert refl_sex["airgap_blocked"] is True, "Airgap reflection write was not blocked!"
        print("✔ Airgap verified: Reflection write blocked for projekt-sexchat.")

        print("[2/2] Testing Normal Project (projekt-dr-franks-bowling)...")
        b_normal = load_project_brain("/mnt/storage-beta/projekte/projekt-dr-franks-bowling")
        assert b_normal["is_airgapped"] is False, "Normal project incorrectly marked as airgapped!"
        print(f"✔ Normal project loaded {len(b_normal['tools'])} tools and rules.")
        print("All brain engine checks passed!")
    else:
        brain = load_project_brain(args.project)
        print(f"Project: {brain['project']} (Airgapped: {brain['is_airgapped']})")
        print(f"Available tools: {list(brain['tools'].keys())}")
