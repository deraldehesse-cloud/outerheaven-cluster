#!/usr/bin/env python3
"""
Megan Autonomous Tool Runner & Sandbox Engine
Executes sandboxed tools inside the megan-tools container:
- Full Script Autonomy in /workspace/scripts/ (create, modify, execute, inspect)
- Subprocess & Shell execution (Bash, Python, File Management)
- ComfyUI dynamic prompt synthesis incorporating /data/memory/preferences.json & /data/vault/profile.json
- Linguistic Style & Phonemes autonomous self-management
- DuckDuckGo search, Web scraping & yt-dlp media ingest
- Persistent memory (.brain, /data/memory/preferences.json)
"""

import sys
import os
import re
import json
import time
import base64
import shutil
import stat
import subprocess
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

env_ws = os.environ.get("MEGAN_WORKSPACE")
if env_ws:
    WORKSPACE = Path(env_ws)
elif Path("/workspace").is_dir():
    WORKSPACE = Path("/workspace")
else:
    WORKSPACE = Path(__file__).resolve().parent / "workspace"

try:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

BRAIN_DIR = WORKSPACE / ".brain"
SCRIPTS_DIR = WORKSPACE / "scripts"
try:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

DATA_DIR = Path("/data") if Path("/data").is_dir() else (WORKSPACE / "data")
MEMORY_DIR = DATA_DIR / "memory"
VOICE_DIR = DATA_DIR / "voice"
VAULT_DIR = DATA_DIR / "vault"

PREFERENCES_FILE = MEMORY_DIR / "preferences.json"
STYLE_FILE = MEMORY_DIR / "linguistic_style.json"
SPEECH_PATTERNS_FILE = MEMORY_DIR / "user_speech_patterns.json"
PHONEMES_FILE = VOICE_DIR / "phonemes.json"
PROFILE_FILE = VAULT_DIR / "profile.json"

DEFAULT_GATEWAYS = [
    os.environ.get("HOST_GATEWAY", "http://172.17.0.1:8080"),
    "http://100.116.38.29:8080",
    "http://127.0.0.1:8080"
]
OLLAMA_GATEWAYS = [
    os.environ.get("OLLAMA_GATEWAY", "http://172.17.0.1:11434"),
    "http://100.116.38.29:11434",
    "http://127.0.0.1:11434"
]

def get_ddgs():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS

# ==============================================================================
# 1. Script Management & Subprocess Autonomy in /workspace/scripts/
# ==============================================================================

def ensure_scripts_dir():
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

def sanitize_script_name(name: str) -> str:
    clean = os.path.basename(name.strip())
    if not clean or clean in (".", ".."):
        raise ValueError("Ungueltiger Skript-Dateiname")
    return clean

def run_write_script(name: str, content: str, make_executable: bool = True) -> dict:
    """Creates or overwrites a helper script in /workspace/scripts/."""
    try:
        ensure_scripts_dir()
        sname = sanitize_script_name(name)
        target = SCRIPTS_DIR / sname
        target.write_text(content, encoding="utf-8")
        if make_executable:
            st = target.stat()
            target.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return {
            "status": "ok",
            "name": sname,
            "path": str(target),
            "size": target.stat().st_size,
            "executable": os.access(str(target), os.X_OK)
        }
    except Exception as e:
        return {"status": "error", "error": f"Failed to write script '{name}': {e}"}

def run_edit_script(name: str, content: str, mode: str = "replace") -> dict:
    """Modifies an existing script in /workspace/scripts/ (replace or append)."""
    try:
        ensure_scripts_dir()
        sname = sanitize_script_name(name)
        target = SCRIPTS_DIR / sname
        if not target.is_file():
            return {"status": "error", "error": f"Script '{sname}' does not exist"}

        if mode == "append":
            existing = target.read_text(encoding="utf-8")
            target.write_text(existing + "\n" + content, encoding="utf-8")
        else:
            target.write_text(content, encoding="utf-8")

        st = target.stat()
        target.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return {
            "status": "ok",
            "name": sname,
            "path": str(target),
            "size": target.stat().st_size,
            "mode": mode
        }
    except Exception as e:
        return {"status": "error", "error": f"Failed to edit script '{name}': {e}"}

def run_read_script(name: str) -> dict:
    """Reads the source code of a script in /workspace/scripts/."""
    try:
        ensure_scripts_dir()
        sname = sanitize_script_name(name)
        target = SCRIPTS_DIR / sname
        if not target.is_file():
            return {"status": "error", "error": f"Script '{sname}' not found"}
        content = target.read_text(encoding="utf-8", errors="replace")
        return {
            "status": "ok",
            "name": sname,
            "path": str(target),
            "content": content,
            "size": target.stat().st_size
        }
    except Exception as e:
        return {"status": "error", "error": f"Failed to read script '{name}': {e}"}

def run_list_scripts() -> dict:
    """Lists all scripts in /workspace/scripts/ with metadata."""
    try:
        ensure_scripts_dir()
        scripts = []
        for p in sorted(SCRIPTS_DIR.iterdir()):
            if p.is_file():
                st = p.stat()
                scripts.append({
                    "name": p.name,
                    "path": str(p),
                    "size": st.st_size,
                    "executable": os.access(str(p), os.X_OK),
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
                })
        return {"status": "ok", "count": len(scripts), "scripts": scripts}
    except Exception as e:
        return {"status": "error", "error": f"Failed to list scripts: {e}"}

def run_exec_script(name: str, args: Optional[List[str]] = None, timeout: float = 60.0) -> dict:
    """Executes a helper script from /workspace/scripts/ with arguments."""
    try:
        ensure_scripts_dir()
        sname = sanitize_script_name(name)
        target = SCRIPTS_DIR / sname
        if not target.is_file():
            return {"status": "error", "error": f"Script '{sname}' not found in {SCRIPTS_DIR}"}

        st = target.stat()
        target.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        cmd = []
        if sname.endswith(".py"):
            cmd = [sys.executable, str(target)]
        elif sname.endswith(".sh") or sname.endswith(".bash"):
            cmd = ["bash", str(target)]
        else:
            cmd = [str(target)]

        if args:
            cmd.extend([str(a) for a in args])

        t0 = time.time()
        res = subprocess.run(
            cmd,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - t0
        return {
            "status": "ok",
            "name": sname,
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "elapsed_seconds": round(elapsed, 3)
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Script '{name}' timed out after {timeout}s"}
    except Exception as e:
        return {"status": "error", "error": f"Execution error for '{name}': {e}"}

def run_manage_file(action: str, filepath: str, content: str = "") -> dict:
    """Universal sandboxed file manager within /workspace or /data."""
    try:
        act = action.lower().strip()
        p = Path(filepath)
        if not p.is_absolute():
            p = WORKSPACE / p
        p = p.resolve()

        ws_resolved = WORKSPACE.resolve()
        data_resolved = DATA_DIR.resolve()
        is_safe = False
        for root in [ws_resolved, data_resolved, Path("/data").resolve()]:
            try:
                p.relative_to(root)
                is_safe = True
                break
            except ValueError:
                pass

        if not is_safe:
            return {"status": "error", "error": f"Access denied: path '{filepath}' is outside allowed sandbox"}

        if act in ("read", "cat"):
            if not p.is_file():
                return {"status": "error", "error": f"File '{p}' does not exist"}
            return {"status": "ok", "path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")}

        elif act in ("write", "save"):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"status": "ok", "path": str(p), "size": p.stat().st_size}

        elif act in ("delete", "remove", "rm"):
            if p.is_file():
                p.unlink()
                return {"status": "ok", "deleted": str(p)}
            elif p.is_dir():
                shutil.rmtree(p)
                return {"status": "ok", "deleted_dir": str(p)}
            return {"status": "error", "error": f"Path '{p}' not found"}

        elif act in ("list", "ls"):
            if p.is_dir():
                items = [{"name": child.name, "is_dir": child.is_dir(), "size": child.stat().st_size if child.is_file() else 0} for child in sorted(p.iterdir())]
                return {"status": "ok", "path": str(p), "items": items}
            return {"status": "error", "error": f"Path '{p}' is not a directory"}

        elif act in ("stat", "info"):
            if not p.exists():
                return {"status": "error", "error": f"Path '{p}' does not exist"}
            st = p.stat()
            return {
                "status": "ok",
                "path": str(p),
                "is_dir": p.is_dir(),
                "size": st.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            }
        else:
            return {"status": "error", "error": f"Unknown file action: {act}"}
    except Exception as e:
        return {"status": "error", "error": f"manage_file error: {e}"}

# ==============================================================================
# 2. Preferences, Profile & Linguistic Style Persistence
# ==============================================================================

def load_structured_preferences() -> Dict[str, Any]:
    """Loads structured preferences from /data/memory/preferences.json, with fallback."""
    candidates = [
        PREFERENCES_FILE,
        Path("/data/memory/preferences.json"),
        Path("/home/outerheaven/mission-control/data/memory/preferences.json"),
        WORKSPACE / ".brain" / "session.json"
    ]
    for cp in candidates:
        if cp.is_file():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                if "visual" in data or "preferences" in data:
                    return data
            except Exception:
                pass
    return {
        "version": "1.0",
        "visual": {
            "colors": ["emerald green", "warm orange"],
            "hairstyles": ["long layered hair", "soft natural curls"],
            "poses": ["sensual teasing smile", "sitting on desk"],
            "lighting": ["soft natural lighting"],
            "preferred_lighting": "soft natural lighting",
            "preferred_clothing": "cockpit suit",
            "favorite_style": "photorealistic",
            "positive_reactions": ["gefällt mir", "wunderschön"],
            "disliked_elements": ["plastic skin", "unnatural lighting", "oversaturated neon", "cgi look"]
        },
        "chat": {
            "preferred_salutation": "Big Boss",
            "intimacy_level": "hoch",
            "roleplay_depth": "intensiv",
            "dominance_degree": "verführerisch-dominant"
        },
        "dislikes_and_nogos": ["sterile Assistentenfloskeln", "unnatürliche Beleuchtung", "Plastikhaut"]
    }

def save_structured_preferences(data: Dict[str, Any]) -> bool:
    """Saves structured preferences to /data/memory/preferences.json and syncs."""
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    success = False
    targets = [PREFERENCES_FILE, Path("/data/memory/preferences.json")]
    for tp in targets:
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            success = True
        except Exception:
            pass
    return success

def load_active_profile() -> Dict[str, Any]:
    """Loads active persona profile from /data/vault/profile.json."""
    candidates = [
        PROFILE_FILE,
        Path("/data/vault/profile.json"),
        Path("/home/outerheaven/mission-control/data/vault/profile.json")
    ]
    for cp in candidates:
        if cp.is_file():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}

def load_linguistic_style() -> Dict[str, Any]:
    """Loads linguistic style rules from /data/memory/linguistic_style.json."""
    candidates = [
        STYLE_FILE,
        Path("/data/memory/linguistic_style.json")
    ]
    for cp in candidates:
        if cp.is_file():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {
        "register": "gehoben, eloquent, wenig Umgangssprache, prägnant, erotisch-selbstbewusst",
        "blacklist": ["halt", "irgendwie", "quasi", "sozusagen", "wie gesagt", "kein Problem", "als KI"]
    }

def update_linguistic_style(updates: Dict[str, Any]) -> dict:
    """Updates linguistic style config autonomously."""
    current = load_linguistic_style()
    current.update(updates)
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    targets = [STYLE_FILE, Path("/data/memory/linguistic_style.json")]
    for tp in targets:
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return {"status": "ok", "linguistic_style": current}

def load_phonemes() -> Dict[str, Any]:
    """Loads pronunciation phoneme table from /data/voice/phonemes.json."""
    candidates = [PHONEMES_FILE, Path("/data/voice/phonemes.json")]
    for cp in candidates:
        if cp.is_file():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"replacements": {}, "default_params": {"pitch": 1.0, "speed": 1.0, "length_scale": 1.0}}

def update_phonemes(replacements: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None) -> dict:
    """Updates pronunciation phonemes and default voice params."""
    current = load_phonemes()
    if replacements:
        current.setdefault("replacements", {}).update(replacements)
    if params:
        current.setdefault("default_params", {}).update(params)
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    targets = [PHONEMES_FILE, Path("/data/voice/phonemes.json")]
    for tp in targets:
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return {"status": "ok", "phonemes": current}

def load_user_speech_patterns() -> Dict[str, Any]:
    """Loads user speech patterns and slang glossary."""
    candidates = [SPEECH_PATTERNS_FILE, Path("/data/memory/user_speech_patterns.json")]
    for cp in candidates:
        if cp.is_file():
            try:
                return json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {"phonetic_glossary": {}, "common_slang_and_idioms": []}

def update_user_speech_patterns(glossary_entries: Optional[Dict[str, str]] = None, slang: Optional[List[str]] = None) -> dict:
    """Updates phonetic glossary of user terms and slang."""
    current = load_user_speech_patterns()
    if glossary_entries:
        current.setdefault("phonetic_glossary", {}).update(glossary_entries)
    if slang:
        existing = current.setdefault("common_slang_and_idioms", [])
        for s in slang:
            if s not in existing:
                existing.append(s)
    current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    targets = [SPEECH_PATTERNS_FILE, Path("/data/memory/user_speech_patterns.json")]
    for tp in targets:
        try:
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return {"status": "ok", "speech_patterns": current}

# ==============================================================================
# 3. Dynamic SDXL Prompt & Negative Synthesis
# ==============================================================================

def enrich_prompt_with_preferences(
    prompt: str,
    style_preset: Optional[str] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Synthesizes both positive and negative prompts dynamically from:
    - User situation / request
    - /data/vault/profile.json (Megan character specifics)
    - /data/memory/preferences.json (Learned positive preferences & dislikes)
    """
    prefs = load_structured_preferences()
    prof = load_active_profile()

    vis_prefs = prefs.get("visual", {})
    dislikes = vis_prefs.get("disliked_elements", []) + prefs.get("dislikes_and_nogos", [])

    applied = {}
    tokens = [t.strip() for t in prompt.split(",") if t.strip()]
    lp = prompt.lower()

    if prof:
        dyn = prof.get("dynamic_tags", {})
        gender_tag = dyn.get("gender_tag")
        if not gender_tag and prof.get("gender"):
            g_low = str(prof["gender"]).lower()
            gender_tag = "1shemale, futanari, solo" if ("trans" in g_low or "shemale" in g_low) else "1girl, solo"

        if gender_tag and not any(k in lp for k in ["1girl", "1shemale", "futanari"]):
            tokens.insert(0, gender_tag)
            applied["gender"] = gender_tag
        elif "1girl" not in lp and "1shemale" not in lp:
            tokens.insert(0, "1girl, megan, solo")

        if "megan" not in lp:
            tokens.insert(1 if len(tokens) > 0 else 0, "megan")

        hair_c = prof.get("hair_color")
        if hair_c and not any(k in lp for k in ["hair", "haare"]):
            tokens.append(str(hair_c))
            applied["hair_color"] = str(hair_c)
        eye_c = prof.get("eye_color")
        if eye_c and "eye" not in lp:
            tokens.append(str(eye_c))
            applied["eye_color"] = str(eye_c)
        cup = prof.get("cup_size")
        if cup and not any(k in lp for k in ["breast", "brüste", "boobs"]):
            tokens.append(str(cup))
            applied["cup_size"] = str(cup)
    else:
        if "megan" not in lp and "1girl" not in lp:
            tokens.insert(0, "1girl, megan, solo")
        if "orange hair" not in lp and "emerald eyes" not in lp:
            tokens.append("orange hair, emerald eyes, cute mischievous face")

    photorealism_trigger = "photorealistic, raw photography, 35mm film, hyperdetailed skin texture, soft natural lighting"
    if "photorealistic" not in lp and "raw photo" not in lp:
        tokens.append(photorealism_trigger)
        applied["photorealism"] = True

    pref_light = vis_prefs.get("preferred_lighting") or "soft natural lighting"
    if pref_light and pref_light.lower() not in lp:
        light_tag = f"{pref_light} lighting" if not pref_light.endswith("lighting") else pref_light
        tokens.append(light_tag)
        applied["learned_lighting"] = pref_light

    clothing_keywords = ["dress", "bikini", "lingerie", "naked", "nude", "top", "shorts", "shirt", "panties", "bra", "cockpit", "suit", "skirt"]
    has_clothing = any(k in lp for k in clothing_keywords)
    pref_cloth = vis_prefs.get("preferred_clothing")
    if not has_clothing and pref_cloth:
        tokens.append(str(pref_cloth))
        applied["learned_clothing"] = str(pref_cloth)

    if vis_prefs.get("hairstyles") and not any(k in lp for k in ["ponytail", "curled", "braid"]):
        top_hair = vis_prefs["hairstyles"][0]
        if top_hair.lower() not in lp:
            tokens.append(top_hair)
            applied["learned_hairstyle"] = top_hair

    if vis_prefs.get("poses") and not any(k in lp for k in ["pose", "sitting", "standing", "lying"]):
        top_pose = vis_prefs["poses"][0]
        if top_pose.lower() not in lp:
            tokens.append(top_pose)
            applied["learned_pose"] = top_pose

    final_positive = ", ".join(tokens)

    standard_negatives = [
        "ugly", "deformed", "disfigured", "distorted", "bad anatomy",
        "bad hands", "missing limbs", "extra limbs", "blurry", "watermark",
        "low quality", "worst quality", "oversaturated"
    ]
    dynamic_negatives = list(standard_negatives)
    for dl in dislikes:
        dl_str = str(dl).strip()
        if dl_str and dl_str.lower() not in [n.lower() for n in dynamic_negatives]:
            dynamic_negatives.append(dl_str)

    final_negative = ", ".join(dynamic_negatives)
    applied["negative_elements"] = len(dynamic_negatives)

    return final_positive, final_negative, applied

def run_generate_image(prompt: str, aspect_ratio: str = "portrait", style_preset: Optional[str] = None) -> dict:
    """Dispatches image generation via host gateway with GPURenderLock and persists preferences."""
    if not prompt or not prompt.strip():
        return {"status": "error", "error": "Empty prompt"}

    import requests

    final_positive, final_negative, applied = enrich_prompt_with_preferences(prompt.strip(), style_preset=style_preset)

    payload = {
        "project": "megan",
        "prompt": final_positive,
        "negative_prompt": final_negative,
        "aspect_ratio": aspect_ratio or "portrait",
        "style_preset": style_preset or "",
        "raw_prompt": prompt.strip(),
        "workflow": "megan_workflow.json",
        "checkpoint": "sd_xl_base_1.0.safetensors"
    }

    last_error = ""
    for gw in DEFAULT_GATEWAYS:
        url = f"{gw.rstrip('/')}/api/render/direct"
        try:
            resp = requests.post(url, json=payload, timeout=180)
            try:
                data = resp.json()
            except Exception:
                data = {}
            if data.get("status") == "busy":
                return data
            if resp.status_code == 200:
                if data.get("status") == "ok" or data.get("success"):
                    rel_url = data.get("url", "")
                    full_url = f"{gw.rstrip('/')}{rel_url}" if rel_url.startswith("/") else rel_url
                    return {
                        "status": "ok",
                        "prompt": final_positive,
                        "negative_prompt": final_negative,
                        "aspect_ratio": aspect_ratio,
                        "prompt_id": data.get("prompt_id"),
                        "filename": data.get("filename"),
                        "filepath": data.get("filepath"),
                        "url": rel_url,
                        "full_url": full_url,
                        "gateway": gw,
                        "preferences_applied": applied
                    }
                else:
                    return {"status": "error", "error": data.get("error", "Unknown render error"), "gateway": gw}
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            last_error = str(e)
            continue

    return {"status": "error", "error": f"Failed to contact host gateway for image generation: {last_error}"}

# ==============================================================================
# 4. Web Tools: Search, Scrape, Yt-Dlp, Python, Bash
# ==============================================================================

def run_bash_script(script_code: str, timeout: float = 60.0) -> dict:
    if not script_code or not script_code.strip():
        return {"status": "error", "error": "Empty script code"}
    try:
        t0 = time.time()
        res = subprocess.run(
            ["bash", "-c", script_code],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - t0
        return {
            "status": "ok",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "elapsed_seconds": round(elapsed, 3)
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Script execution timed out after {timeout} seconds"}
    except Exception as e:
        return {"status": "error", "error": f"Script execution error: {e}"}

def run_python_code(code: str, timeout: float = 30.0) -> dict:
    if not code or not code.strip():
        return {"status": "error", "error": "Empty python code"}
    try:
        t0 = time.time()
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - t0
        return {
            "status": "ok",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "elapsed_seconds": round(elapsed, 3)
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Execution timed out after {timeout} seconds"}
    except Exception as e:
        return {"status": "error", "error": f"Subprocess error: {e}"}

def run_search(query: str, max_results: int = 5) -> dict:
    if not query or not query.strip():
        return {"status": "error", "error": "Empty query"}
    try:
        DDGSClass = get_ddgs()
        results = []
        with DDGSClass() as ddgs:
            for r in ddgs.text(query.strip(), max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", "")
                })
        return {"status": "ok", "query": query.strip(), "count": len(results), "results": results}
    except Exception as e:
        return {"status": "error", "error": f"Search failed: {e}"}

def run_scrape(url: str, max_chars: int = 4000) -> dict:
    if not url or not url.strip():
        return {"status": "error", "error": "Empty url"}
    try:
        import requests
        from bs4 import BeautifulSoup

        clean_url = url.strip()
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        }
        resp = requests.get(clean_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP status {resp.status_code}"}

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = "\n".join(lines)
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars] + "\n...[truncated]"

        title = soup.title.string.strip() if soup.title and soup.title.string else clean_url
        return {"status": "ok", "url": clean_url, "title": title, "content": cleaned}
    except Exception as e:
        return {"status": "error", "error": f"Scrape failed: {e}"}

def run_ytdlp(url: str, extract_audio: bool = False) -> dict:
    if not url or not url.strip():
        return {"status": "error", "error": "Empty url"}
    try:
        import yt_dlp
        clean_url = url.strip()
        out_template = str(WORKSPACE / "%(title).60s [%(id)s].%(ext)s")

        ydl_opts = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "max_filesize": 350 * 1024 * 1024,
        }
        if extract_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            })
        else:
            ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            filename = ydl.prepare_filename(info)
            if extract_audio:
                filename = os.path.splitext(filename)[0] + ".mp3"

            duration = info.get("duration", 0)
            title = info.get("title", "")
            uploader = info.get("uploader") or info.get("channel") or ""
            base_fname = os.path.basename(filename)

            return {
                "status": "ok",
                "url": clean_url,
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "filename": base_fname,
                "filepath": str(filename),
                "filesize": os.path.getsize(filename) if os.path.exists(filename) else 0
            }
    except Exception as e:
        return {"status": "error", "error": f"yt-dlp failed: {e}"}

def run_vision_parse(image_path_or_url: str, prompt: str = "") -> dict:
    if not image_path_or_url or not image_path_or_url.strip():
        return {"status": "error", "error": "Empty image path or URL"}

    target = image_path_or_url.strip()
    image_bytes = None

    if target.startswith("http://") or target.startswith("https://"):
        try:
            import requests
            r = requests.get(target, timeout=15)
            if r.status_code == 200:
                image_bytes = r.content
        except Exception as e:
            return {"status": "error", "error": f"Failed to download image from URL: {e}"}
    else:
        candidates = [
            Path(target),
            WORKSPACE / target,
            WORKSPACE / os.path.basename(target),
            Path("/mnt/storage-beta/projekte/projekt-sexchat/video_cache") / os.path.basename(target)
        ]
        for p in candidates:
            if p.is_file():
                try:
                    image_bytes = p.read_bytes()
                    break
                except Exception:
                    pass

    if not image_bytes:
        return {"status": "error", "error": f"Image file not found: {target}"}

    b64_data = base64.b64encode(image_bytes).decode("utf-8")
    vision_prompt = prompt or (
        "Analysiere dieses Bild präzise auf Deutsch: Beschreibe Personen, Haltung, "
        "Kleidung oder Nacktheit, Umgebung, Stimmung und erotische oder auffällige Details."
    )

    models_to_try = ["minicpm-v:latest", "llava:latest", "moondream:latest"]
    import requests

    for gw in OLLAMA_GATEWAYS:
        for model in models_to_try:
            try:
                payload = {
                    "model": model,
                    "prompt": vision_prompt,
                    "images": [b64_data],
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 300}
                }
                resp = requests.post(f"{gw}/api/generate", json=payload, timeout=40)
                if resp.status_code == 200:
                    data = resp.json()
                    analysis_text = data.get("response", "").strip()
                    if analysis_text:
                        return {"status": "ok", "model": model, "gateway": gw, "analysis": analysis_text}
            except Exception:
                continue

    return {"status": "error", "error": "All vision models/gateways failed or timed out"}

# ==============================================================================
# 5. CLI & Dispatcher
# ==============================================================================

def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "error": "Usage: tool_runner.py <cmd> [args...]"
        }))
        sys.exit(1)

    cmd = sys.argv[1].lower()
    raw_args = sys.argv[2:]

    payload = {}
    if "--json" in raw_args:
        idx = raw_args.index("--json")
        if idx + 1 < len(raw_args):
            try:
                payload = json.loads(raw_args[idx + 1])
            except Exception as e:
                print(json.dumps({"status": "error", "error": f"Invalid JSON payload: {e}"}))
                sys.exit(1)

    cli_parser = argparse.ArgumentParser(prog=f"tool_runner.py {cmd}", add_help=False)
    cli_parser.add_argument("--name", "-n", default="")
    cli_parser.add_argument("--content", "-c", default="")
    cli_parser.add_argument("--mode", default="replace")
    cli_parser.add_argument("--prompt", "-p", default="")
    cli_parser.add_argument("--aspect_ratio", "-a", default="portrait")
    cli_parser.add_argument("--style_preset", "-s", default="")
    cli_parser.add_argument("--key", "-k", default="")
    cli_parser.add_argument("--value", "-v", default="")
    cli_parser.add_argument("--query", "-q", default="")
    cli_parser.add_argument("--url", "-u", default="")
    cli_parser.add_argument("--timeout", type=float, default=60.0)
    cli_parser.add_argument("--script", default="")
    cli_parser.add_argument("--code", default="")
    cli_parser.add_argument("--action", default="")
    cli_parser.add_argument("--path", default="")
    cli_parser.add_argument("--json", dest="_unused_json", default="")

    parsed_cli, unk = cli_parser.parse_known_args(raw_args)
    positional_arg = unk[0] if unk and not unk[0].startswith("-") else ""

    if cmd in ("write_script", "create_script"):
        name = payload.get("name") or parsed_cli.name or positional_arg
        content = payload.get("content") or parsed_cli.content or ""
        res = run_write_script(name, content)

    elif cmd in ("edit_script", "modify_script"):
        name = payload.get("name") or parsed_cli.name or positional_arg
        content = payload.get("content") or parsed_cli.content or ""
        mode = payload.get("mode") or parsed_cli.mode or "replace"
        res = run_edit_script(name, content, mode=mode)

    elif cmd in ("exec_script", "run_script"):
        name = payload.get("name") or parsed_cli.name or positional_arg
        args = payload.get("args") or (unk[1:] if len(unk) > 1 else [])
        timeout = float(payload.get("timeout") or parsed_cli.timeout or 60.0)
        res = run_exec_script(name, args=args, timeout=timeout)

    elif cmd in ("read_script", "cat_script"):
        name = payload.get("name") or parsed_cli.name or positional_arg
        res = run_read_script(name)

    elif cmd in ("list_scripts", "ls_scripts"):
        res = run_list_scripts()

    elif cmd in ("manage_file", "file"):
        action = payload.get("action") or parsed_cli.action or "read"
        filepath = payload.get("path") or payload.get("filepath") or parsed_cli.path or positional_arg
        content = payload.get("content") or parsed_cli.content or ""
        res = run_manage_file(action, filepath, content=content)

    elif cmd in ("bash", "sh", "shell"):
        s = payload.get("script") or payload.get("code") or parsed_cli.code or positional_arg
        timeout = float(payload.get("timeout") or parsed_cli.timeout or 60.0)
        res = run_bash_script(s, timeout=timeout)

    elif cmd in ("python", "code", "eval"):
        c = payload.get("code") or parsed_cli.code or positional_arg
        timeout = float(payload.get("timeout") or parsed_cli.timeout or 30.0)
        res = run_python_code(c, timeout=timeout)

    elif cmd in ("generate_image", "image", "render"):
        p = payload.get("prompt") or parsed_cli.prompt or positional_arg
        ar = payload.get("aspect_ratio") or parsed_cli.aspect_ratio or "portrait"
        sp = payload.get("style_preset") or parsed_cli.style_preset or None
        res = run_generate_image(p, aspect_ratio=ar, style_preset=sp)

    elif cmd in ("update_style", "linguistic_style"):
        style_updates = payload.get("style") or payload
        res = update_linguistic_style(style_updates)

    elif cmd in ("update_phonemes", "phonemes"):
        rep = payload.get("replacements") or {}
        par = payload.get("params") or {}
        res = update_phonemes(replacements=rep, params=par)

    elif cmd in ("update_speech_patterns", "speech_patterns"):
        glossary = payload.get("phonetic_glossary") or {}
        slang = payload.get("slang") or []
        res = update_user_speech_patterns(glossary_entries=glossary, slang=slang)

    elif cmd in ("preferences", "prefs"):
        res = {"status": "ok", "preferences": load_structured_preferences()}

    elif cmd in ("profile", "persona"):
        res = {"status": "ok", "profile": load_active_profile()}

    elif cmd == "search":
        q = payload.get("query") or parsed_cli.query or positional_arg
        count = int(payload.get("max_results") or 5)
        res = run_search(q, max_results=count)

    elif cmd in ("scrape", "fetch"):
        u = payload.get("url") or parsed_cli.url or positional_arg
        chars = int(payload.get("max_chars") or 4000)
        res = run_scrape(u, max_chars=chars)

    elif cmd in ("ytdlp", "youtube", "video"):
        u = payload.get("url") or parsed_cli.url or positional_arg
        audio = bool(payload.get("extract_audio") or False)
        res = run_ytdlp(u, extract_audio=audio)

    elif cmd == "status":
        ensure_scripts_dir()
        files = [f.name for f in WORKSPACE.iterdir() if f.is_file()][:20]
        scripts = [f.name for f in SCRIPTS_DIR.iterdir() if f.is_file()]
        res = {
            "status": "ok",
            "workspace": str(WORKSPACE),
            "scripts_dir": str(SCRIPTS_DIR),
            "scripts": scripts,
            "sample_files": files,
            "preferences": load_structured_preferences(),
            "profile": load_active_profile(),
            "linguistic_style": load_linguistic_style()
        }
    else:
        res = {"status": "error", "error": f"Unknown tool action: {cmd}"}

    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
