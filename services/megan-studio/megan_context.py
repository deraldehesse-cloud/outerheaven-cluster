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
import subprocess

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
    "<turn|>",
    "<|turn>",
    "<|im_end|>",
    "[Spontaneous"
]

def sanitize_megan_response(text: str) -> str:
    """
    Pre-Save Sanitizer for Megan responses:
    - Truncates strictly before first occurrence of user markers, stop tokens or trigger blocks.
    - If response consists entirely of foreign roles or hallucination, discards it and logs neutral status.
    """
    if not text:
        return ""

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

    # If the response was entirely foreign roles / hallucination, discard and log
    if not cleaned or re.match(r"^[\s\r\n]*(?:User:|Big Boss:|Sebastian:|<turn\|>|<\|turn>|<\|im_end\|>|\[)", cleaned):
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
    lambda: "/mnt/fox-home/fox/gdrive/Agenten-Drive",
    lambda: str(Path.home() / "gdrive" / "Agenten-Drive"),
    lambda: "/home/outerheaven/gdrive/Agenten-Drive",
    lambda: "/home/fox/gdrive/Agenten-Drive",
    lambda: "/mnt/storage-beta/Agenten-Drive",
    lambda: "/mnt/storage-beta/projekte",
]

def get_gdrive_base_path() -> Path:
    """Returns the active, accessible Google Drive / Agenten-Drive base path."""
    for getter in GDRIVE_CANDIDATE_PATHS:
        raw = getter()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
    # Fallback default
    return Path("/mnt/fox-home/fox/gdrive/Agenten-Drive")

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
ALLOWED_SHELL_COMMANDS = [
    "uptime",
    "df -h",
    "free -m",
    "nvidia-smi",
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

def execute_autonomous_tool(tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Executes autonomous diagnostic checks, Google Drive resolution, and workflow triggers.
    Equips Megan with self-sufficient troubleshooting and action rights.
    """
    params = params or {}
    tool = tool_name.lower().strip()

    if tool in ["gdrive_resolve", "resolve_gdrive", "gdrive_search"]:
        query = params.get("project") or params.get("query", "")
        return resolve_gdrive_project_path(query)

    elif tool in ["system_status", "status_check"]:
        status: Dict[str, Any] = {
            "timestamp": int(time.time()),
            "node": "outerheaven",
            "services": {},
            "disk": {},
            "tailscale": "unknown"
        }
        # Check systemd user services
        for svc in ["mission-control.service", "megan-studio.service"]:
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

        # Check GPU / ComfyUI Render Lock
        try:
            from gpu_render_lock import get_gpu_lock_status
            status["gpu_lock"] = get_gpu_lock_status()
        except Exception:
            status["gpu_lock"] = "unlocked"

        return {"success": True, "tool": "system_status", "status": status}

    elif tool in ["shell_check", "diagnose"]:
        cmd = params.get("command", "").strip()
        if not cmd:
            return {"success": False, "error": "Befehl fehlt"}

        # Whitelist / safe prefix verification
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

    elif tool in ["workflow_trigger", "service_control"]:
        action = params.get("action", "").lower().strip()
        target = params.get("target", "megan-studio.service").strip()
        if action in ["restart", "reload"]:
            if target in ["megan-studio.service", "mission-control.service"]:
                try:
                    res = subprocess.run(["systemctl", "--user", action, target], capture_output=True, text=True, timeout=5)
                    return {"success": (res.returncode == 0), "action": action, "target": target, "output": res.stdout}
                except Exception as e:
                    return {"success": False, "error": str(e)}
        return {"success": False, "error": f"Aktion '{action}' auf Ziel '{target}' nicht erlaubt"}

    return {"success": False, "error": f"Unbekanntes Tool: {tool_name}"}

