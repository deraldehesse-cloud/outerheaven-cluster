#!/usr/bin/env python3
"""
dispatch_comfy.py - Asynchronous Remote ComfyUI Image Generation Pipeline.
Designed for the Companion Bot on Outer Heaven / Fox Cluster.

Features:
  - Loads persona & environment presets from persona.yaml
  - Assembles prompt combining appearance, environment, situation, and style
  - Dispatches prompt to ComfyUI on Outer Heaven (100.116.38.29:8188) via REST API
  - Polls /history/{prompt_id} and saves image to /mnt/storage-beta/.../output/
  - Asynchronous non-blocking execution so text chat on Fox never stalls
  - Ring buffer manager for history.json (15 messages)
"""

import os
import sys
import re
import time
import json
import uuid
import secrets
import logging
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, List
from concurrent.futures import ThreadPoolExecutor

import yaml

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [dispatch_comfy]: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("dispatch_comfy")

# --- Directory Constants ---
BASE_DIR = Path(__file__).resolve().parent
if (BASE_DIR / "persona.yaml").is_file():
    PROJECT_ROOT = BASE_DIR
elif (BASE_DIR.parent / "persona.yaml").is_file():
    PROJECT_ROOT = BASE_DIR.parent
elif Path("/mnt/storage-beta/projekte/projekt-sexchat/persona.yaml").is_file():
    PROJECT_ROOT = Path("/mnt/storage-beta/projekte/projekt-sexchat")
else:
    PROJECT_ROOT = BASE_DIR

PERSONA_CONFIG_PATH = PROJECT_ROOT / "persona.yaml"
MEMORY_PATH = PROJECT_ROOT / "MEMORY.md"
HISTORY_PATH = PROJECT_ROOT / "history.json"
BOT_CONFIG_PATH = PROJECT_ROOT / "bot_config.json"
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ComfyUI Host & Port Configuration
COMFYUI_HOST_ENV = os.environ.get("COMFYUI_HOST", "outerheaven").strip()
COMFYUI_PORT_ENV = os.environ.get("COMFYUI_PORT", "8188").strip()

def resolve_comfyui_url(override: Optional[str] = None) -> str:
    h = (override or COMFYUI_HOST_ENV).strip()
    if not h.startswith("http://") and not h.startswith("https://"):
        h = f"http://{h}"
    parsed = urllib.parse.urlparse(h)
    if not parsed.port and COMFYUI_PORT_ENV:
        h = f"{parsed.scheme}://{parsed.hostname}:{COMFYUI_PORT_ENV}"
    return h.rstrip("/")

DEFAULT_COMFYUI_HOST = resolve_comfyui_url()

# Global thread pool for asynchronous background rendering
RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ComfyDispatcher")


# ==============================================================================
# 1. Configuration & Persona Loader
# ==============================================================================


def load_persona_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads persona.yaml configuration and overlays bot_config.json if present."""
    path = config_path or PERSONA_CONFIG_PATH
    if not path.is_file():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    persona = data.get("persona", {})

    # Overlay dynamic bot_config.json render defaults
    if BOT_CONFIG_PATH.is_file():
        try:
            with open(BOT_CONFIG_PATH, "r", encoding="utf-8") as bf:
                b_cfg = json.load(bf)
                render_cfg = b_cfg.get("render", {})
                if render_cfg and isinstance(render_cfg, dict):
                    if "render_defaults" not in persona:
                        persona["render_defaults"] = {}
                    persona["render_defaults"].update(render_cfg)
        except Exception as e:
            logger.warning(f"Failed to overlay bot_config.json: {e}")

    return persona


# ==============================================================================
# 2. Prompt Assembler
# ==============================================================================

def load_visual_preferences_from_memory(memory_path: Optional[Path] = None) -> str:
    """
    Reads MEMORY.md and extracts active preferences from:
    - '## 📸 Visuelle Vorlieben & Kinks von Big Boss'
    Returns a comma-separated string of visual preference tags for ComfyUI.
    """
    path = memory_path or MEMORY_PATH
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        in_section = False
        pref_items = []
        for line in lines:
            line_s = line.strip()
            if line_s.startswith("## ") and any(k in line_s.lower() for k in ["visuell", "kink"]):
                in_section = True
                continue
            elif in_section and line_s.startswith("## "):
                break
            elif in_section and line_s:
                clean_line = re.sub(r"^[\*\-\d\.]+\s*", "", line_s).strip()
                if ":" in clean_line:
                    clean_line = clean_line.split(":", 1)[1].strip()
                if clean_line and not clean_line.startswith("#"):
                    pref_items.append(clean_line)
        if pref_items:
            return ", ".join(pref_items)
    except Exception as e:
        logger.warning(f"Failed to load visual preferences from memory: {e}")
    return ""


def assemble_prompt(
    situation_prompt: str = "",
    environment_key: str = "schreibtisch",
    clothing_override: Optional[str] = None,
    user_custom_tags: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Combines the base persona tags from persona.yaml with the environment preset,
    situative description from the bot, custom user tags, and learned preferences from MEMORY.md.

    Returns:
      (positive_prompt, negative_prompt, render_params)
    """
    cfg = config or load_persona_config()

    app = cfg.get("appearance", {})
    face = app.get("face", "")
    hair = app.get("hair", "")
    body = app.get("body", "")
    features = app.get("features", "")
    style = app.get("style_aesthetic", "")

    # Environment preset
    environments = cfg.get("environments", {})
    env = environments.get(environment_key, environments.get("schreibtisch", {}))
    scene = env.get("scene", "")
    camera = env.get("camera", "")
    env_clothing = clothing_override or env.get("clothing", app.get("clothing_default", ""))

    # Standard Lighting Tokens to prevent underexposed / overly dark images
    lighting_pos = "soft studio lighting, rim lighting, vibrant, well-lit, high key, clear visibility"
    lighting_neg = "underexposed, dark shadows, pitch black background, gloomy, dim lighting"

    # Learned visual preferences from MEMORY.md
    learned_visual_prefs = load_visual_preferences_from_memory()

    # Positive prompt components
    pos_parts = [
        body,
        face,
        hair,
        env_clothing,
        features,
        scene,
        camera,
        situation_prompt.strip() if situation_prompt else "",
        user_custom_tags.strip() if user_custom_tags else "",
        learned_visual_prefs.strip() if learned_visual_prefs else "",
        style,
        lighting_pos
    ]

    # Clean and join
    clean_pos = ", ".join([p.strip() for p in pos_parts if p and p.strip()])
    # Remove duplicate commas
    while ", ," in clean_pos or ",," in clean_pos:
        clean_pos = clean_pos.replace(", ,", ",").replace(",,", ",")

    # Negative prompt
    neg_cfg = cfg.get("negative_prompts", {})
    all_context_for_neg = f"{situation_prompt} {user_custom_tags or ''} {learned_visual_prefs or ''}".lower()
    neg_parts = [
        neg_cfg.get("quality", ""),
        lighting_neg,
        neg_cfg.get("censorship", ""),
        neg_cfg.get("clothing_filter", "") if ("nude" in all_context_for_neg or "naked" in all_context_for_neg) else ""
    ]
    clean_neg = ", ".join([p.strip() for p in neg_parts if p and p.strip()])

    # Render parameters
    render_params = dict(cfg.get("render_defaults", {}))

    return clean_pos, clean_neg, render_params


# ==============================================================================
# 3. Native ComfyUI Workflow Builder
# ==============================================================================

def build_workflow(
    positive_prompt: str,
    negative_prompt: str,
    render_params: Dict[str, Any],
    seed: Optional[int] = None
) -> Dict[str, Any]:
    """Builds a native ComfyUI prompt workflow payload."""
    if seed is None:
        seed = secrets.randbelow(999999999999)

    checkpoint = render_params.get("checkpoint", "DreamShaper8_LCM.safetensors")
    steps = int(render_params.get("steps", 14))
    cfg = float(render_params.get("cfg", 6.5))
    sampler = render_params.get("sampler_name", "euler")
    scheduler = render_params.get("scheduler", "normal")
    denoise = float(render_params.get("denoise", 1.0))
    width = int(render_params.get("width", 512))
    height = int(render_params.get("height", 768))
    prefix = render_params.get("filename_prefix", "Megan_Companion")

    # Anti-Darkness & Optimal CFG / Step Normalization
    is_lcm = "lcm" in checkpoint.lower()
    if is_lcm:
        # LCM requires low CFG (1.5 - 2.5) to avoid extreme burned blacks & contrast artifacts
        if cfg > 3.0:
            cfg = 2.0
        if steps > 12:
            steps = 8
    else:
        # Standard SD1.5 / SDXL checkpoints need balanced CFG (5.5 - 6.5) and adequate steps
        if steps < 20:
            steps = 25
        if cfg > 7.0:
            cfg = 6.0
        if sampler == "euler" and scheduler == "normal":
            sampler = "euler_ancestral"
            scheduler = "karras"

    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint}
        },
        "2": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1}
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": positive_prompt}
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["1", 1], "text": negative_prompt}
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["2", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": denoise
            }
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]}
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": prefix,
                "images": ["6", 0]
            }
        }
    }

    return {"prompt": workflow}


# ==============================================================================
# 4. Synchronous Remote Dispatch & Image Retrieval
# ==============================================================================

# Ollama URL for VRAM arbitration & slot flushing
OLLAMA_LOCAL_URL = os.environ.get("OLLAMA_LOCAL_URL", "http://127.0.0.1:11434").rstrip("/")
HEAVY_MODELS_TO_UNLOAD = [
    "deepseek-r1:14b",
    "deepseek-r1:latest",
    "deepseek",
    "llava:latest"
]


def ensure_comfyui_vram_headroom(ollama_url: str = OLLAMA_LOCAL_URL) -> None:
    """
    [VRAM Arbitrierung - Arc A770 16GB]
    Prueft vor ComfyUI-Render-Jobs, ob schwere Modelle wie DeepSeek im VRAM laufen.
    Falls ja, wird keep_alive: 0 gesendet, um garantiert 8.5 GB VRAM fuer ComfyUI freizuhalten.
    """
    try:
        ps_url = f"{ollama_url}/api/ps"
        req = urllib.request.Request(ps_url, headers={"User-Agent": "OuterHeaven-VRAM-Guard/1.0"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                running_models = data.get("models", [])
                for rm in running_models:
                    m_name = rm.get("name", "")
                    # Check if model matches heavy/non-essential models
                    for heavy in HEAVY_MODELS_TO_UNLOAD:
                        if heavy in m_name.lower():
                            logger.info(f"🧹 [VRAM Guard]: Schweres Modell '{m_name}' vor ComfyUI-Render erkannt -> Sende keep_alive: 0...")
                            flush_url = f"{ollama_url}/api/generate"
                            flush_payload = json.dumps({"model": m_name, "keep_alive": 0}).encode("utf-8")
                            flush_req = urllib.request.Request(
                                flush_url,
                                data=flush_payload,
                                headers={"Content-Type": "application/json"}
                            )
                            try:
                                with urllib.request.urlopen(flush_req, timeout=3) as f_resp:
                                    logger.info(f"✔ [VRAM Guard]: '{m_name}' erfolgreich entladen (Status {f_resp.status}) - Headroom gesichert.")
                            except Exception as fe:
                                logger.warning(f"Fehler beim Entladen von '{m_name}': {fe}")
    except Exception as e:
        logger.debug(f"[VRAM Guard]: Konnte Ollama /api/ps nicht pruefen (harmlos): {e}")


def dispatch_render_sync(
    situation_prompt: str = "",
    environment_key: str = "schreibtisch",
    clothing_override: Optional[str] = None,
    user_custom_tags: Optional[str] = None,
    save_dir: Optional[Path] = None,
    host: Optional[str] = None,
    timeout: float = 120.0,
    poll_interval: float = 1.5,
    on_status: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Synchronously executes the full render generation pipeline:
      0. Pre-Flight VRAM Arbitration: Ensures Arc A770 has >= 8.5 GB free for ComfyUI
      1. Assembles prompt from persona.yaml + environment + situation + custom tags + memory
      2. Checks /queue and reports queue status
      3. POSTs to http://{host}/prompt
      4. Polls /history/{prompt_id} until completion
      5. Downloads/stores image locally in output_dir
      6. Returns dict with local file path, prompt_id, and metadata
    """
    # 0. Pre-flight VRAM arbitration
    ensure_comfyui_vram_headroom()

    config = load_persona_config()
    target_host = host or config.get("render_defaults", {}).get("host", DEFAULT_COMFYUI_HOST)
    comfy_url = resolve_comfyui_url(target_host)

    # Inspect ComfyUI queue on outerheaven
    try:
        with urllib.request.urlopen(f"{comfy_url}/queue", timeout=3) as q_resp:
            if q_resp.status == 200:
                q_data = json.loads(q_resp.read().decode("utf-8"))
                pending = len(q_data.get("queue_pending", []))
                running = len(q_data.get("queue_running", []))
                if pending > 0 or running > 0:
                    msg = f"Image generation queued on outerheaven ({running} aktiv, {pending} wartend)..."
                    logger.info(msg)
                    if on_status:
                        on_status(msg)
    except Exception:
        pass

    target_dir = save_dir or OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    pos_prompt, neg_prompt, render_params = assemble_prompt(
        situation_prompt=situation_prompt,
        environment_key=environment_key,
        clothing_override=clothing_override,
        user_custom_tags=user_custom_tags,
        config=config
    )

    seed = secrets.randbelow(999999999999)
    workflow_payload = build_workflow(pos_prompt, neg_prompt, render_params, seed=seed)
    client_id = str(uuid.uuid4())
    workflow_payload["client_id"] = client_id

    logger.info(f"Dispatching prompt to ComfyUI on {comfy_url} (Env: '{environment_key}')...")
    logger.debug(f"Positive: {pos_prompt[:120]}...")

    # 1. Queue Prompt
    queue_url = f"{comfy_url}/prompt"
    req_data = json.dumps(workflow_payload).encode("utf-8")
    req = urllib.request.Request(
        queue_url,
        data=req_data,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            prompt_id = res_json.get("prompt_id")
            if not prompt_id:
                raise RuntimeError(f"ComfyUI gab keine Prompt-ID zurück: {res_json}")
    except urllib.error.URLError as e:
        logger.error(f"Konnte ComfyUI auf {comfy_url} nicht erreichen: {e}")
        raise ConnectionError(f"ComfyUI auf {comfy_url} nicht erreichbar: {e}")

    logger.info(f"Render eingereiht: Prompt-ID {prompt_id}. Warte auf Fertigstellung...")

    # 2. Poll History
    start_time = time.time()
    history_url = f"{comfy_url}/history/{prompt_id}"
    completed_entry = None

    while time.time() - start_time < timeout:
        time.sleep(poll_interval)
        try:
            with urllib.request.urlopen(history_url, timeout=5) as h_resp:
                if h_resp.status == 200:
                    history_data = json.loads(h_resp.read().decode("utf-8"))
                    if prompt_id in history_data:
                        completed_entry = history_data[prompt_id]
                        break
        except Exception as err:
            logger.debug(f"Poll check error (wird wiederholt): {err}")
            continue

    if not completed_entry:
        raise TimeoutError(f"Render-Timeout nach {timeout}s für Prompt-ID {prompt_id}")

    # 3. Extract Image Metadata
    outputs = completed_entry.get("outputs", {})
    images_meta = []
    for _, node_out in outputs.items():
        if isinstance(node_out, dict) and "images" in node_out:
            for img in node_out["images"]:
                images_meta.append(img)

    if not images_meta:
        raise RuntimeError(f"Keine Bildausgabe in ComfyUI History gefunden: {completed_entry}")

    first_img = images_meta[0]
    remote_filename = first_img.get("filename", "")
    subfolder = first_img.get("subfolder", "")

    # 4. Resolve Local File Path or Download
    local_target_path = target_dir / remote_filename

    # First check if directly readable via shared NFS or local disk
    potential_paths = [
        Path("/home/outerheaven/ComfyUI/output") / subfolder / remote_filename,
        Path("/mnt/storage-beta/ComfyUI/output") / subfolder / remote_filename,
        Path("/mnt/storage-beta/output") / subfolder / remote_filename,
        local_target_path
    ]

    file_found = False
    for p in potential_paths:
        if p.is_file():
            # If not in target_dir, copy or symlink
            if p != local_target_path:
                try:
                    local_target_path.write_bytes(p.read_bytes())
                except Exception:
                    local_target_path = p
            file_found = True
            break

    # If not on shared disk, download via ComfyUI /view API
    if not file_found:
        view_url = (
            f"{comfy_url}/view?"
            f"filename={urllib.parse.quote(remote_filename)}"
            f"&subfolder={urllib.parse.quote(subfolder)}"
            f"&type=output"
        )
        logger.info(f"Lade Bild herunter von {view_url}...")
        try:
            with urllib.request.urlopen(view_url, timeout=15) as view_resp:
                local_target_path.write_bytes(view_resp.read())
                file_found = True
        except Exception as dl_err:
            logger.error(f"Download fehlgeschlagen: {dl_err}")
            raise IOError(f"Konnte generiertes Bild nicht herunterladen: {dl_err}")

    logger.info(f"✔ Bild erfolgreich fertiggestellt: {local_target_path}")

    return {
        "success": True,
        "prompt_id": prompt_id,
        "seed": seed,
        "filename": remote_filename,
        "local_path": str(local_target_path),
        "environment": environment_key,
        "elapsed_seconds": round(time.time() - start_time, 2)
    }


# ==============================================================================
# 5. Asynchronous Background Execution (Non-Blocking for Fox Chat)
# ==============================================================================

def dispatch_render_async(
    situation_prompt: str = "",
    environment_key: str = "schreibtisch",
    clothing_override: Optional[str] = None,
    user_custom_tags: Optional[str] = None,
    host: Optional[str] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None
):
    """
    Dispatches the render job to a background worker thread.
    Returns immediately so the Telegram / Ollama chat loop is NOT blocked.

    When finished, 'on_complete(result_dict)' is called.
    If an error occurs, 'on_error(exception)' is called.
    """
    def _worker():
        try:
            res = dispatch_render_sync(
                situation_prompt=situation_prompt,
                environment_key=environment_key,
                clothing_override=clothing_override,
                user_custom_tags=user_custom_tags,
                host=host,
                on_status=on_status
            )
            if on_complete:
                on_complete(res)
        except Exception as e:
            logger.error(f"Fehler im asynchronen Render-Worker: {e}")
            if on_error:
                on_error(e)

    future = RENDER_EXECUTOR.submit(_worker)
    return future


# ==============================================================================
# 6. Companion Memory & History Ring-Buffer Manager
# ==============================================================================

class CompanionMemoryManager:
    """Manages long-term memory (MEMORY.md) and short-term chat history ring buffer (history.json)."""

    def __init__(self, history_file: Path = HISTORY_PATH, memory_file: Path = MEMORY_PATH, max_length: int = 32):
        self.history_file = history_file
        self.memory_file = memory_file
        self.max_length = max_length

    def load_history(self) -> List[Dict[str, Any]]:
        """Loads messages from history.json with automatic glitch-token sanitization."""
        if not self.history_file.is_file():
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                msgs = data.get("messages", [])
            try:
                from sanitize_history import sanitize_history_data, atomic_save_json
                cleaned_msgs, removed = sanitize_history_data(msgs)
                if removed > 0:
                    data["messages"] = cleaned_msgs
                    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    atomic_save_json(self.history_file, data)
                    logger.info(f"Sanitized {removed} corrupted message(s) from {self.history_file.name}")
                return cleaned_msgs
            except ImportError:
                return msgs
        except Exception as e:
            logger.warning(f"Konnte history.json nicht laden: {e}")
            return []

    def add_message(self, role: str, content: str, media_path: Optional[str] = None):
        """Appends a message and preserves max_length (15 messages ring buffer)."""
        messages = self.load_history()
        msg_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "role": role,
            "content": content
        }
        if media_path:
            msg_entry["media_path"] = str(media_path)

        try:
            from sanitize_history import is_corrupted_text
            is_bad, reason = is_corrupted_text(content)
            if is_bad:
                logger.warning(f"Abweisung korrumpierter Nachricht ({reason}): {content[:40]}...")
                return
        except ImportError:
            pass
        messages.append(msg_entry)

        # Enforce ring buffer of max 15 messages
        if len(messages) > self.max_length:
            messages = messages[-self.max_length:]

        payload = {
            "max_length": self.max_length,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "messages": messages
        }

        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Konnte history.json nicht speichern: {e}")

    def load_long_term_memory(self) -> str:
        """Reads MEMORY.md if available."""
        if self.memory_file.is_file():
            try:
                return self.memory_file.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"Konnte MEMORY.md nicht lesen: {e}")
        return ""


# ==============================================================================
# 7. Command Line Interface (CLI)
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Outer Heaven Companion ComfyUI Remote Pipeline")
    parser.add_argument("--prompt", "-p", default="", help="Situative Szenenbeschreibung")
    parser.add_argument("--env", "-e", default="schreibtisch", choices=["schreibtisch", "couch", "unterwegs", "schlafzimmer"], help="Umgebungs-Preset")
    parser.add_argument("--clothing", default=None, help="Optionale Kleidungsauswahl / Override")
    parser.add_argument("--tags", "-t", default=None, help="Optionale benutzerdefinierte visuelle Tags")
    parser.add_argument("--host", default=DEFAULT_COMFYUI_HOST, help=f"ComfyUI Host (Standard: {DEFAULT_COMFYUI_HOST})")
    parser.add_argument("--dry-run", action="store_true", help="Prompt zusammenstellen und anzeigen, aber nicht rendern")
    parser.add_argument("--test", action="store_true", help="Testet Konfiguration und Prompt-Assemblierung")

    args = parser.parse_args()

    if args.test or args.dry_run:
        pos, neg, params = assemble_prompt(args.prompt, environment_key=args.env, clothing_override=args.clothing, user_custom_tags=args.tags)
        print("=== PROMPT ASSEMBLER ERGEBNIS ===")
        print(f"[+] Umgebung: {args.env}")
        print(f"[+] Positiver Prompt:\n{pos}\n")
        print(f"[+] Negativer Prompt:\n{neg}\n")
        print(f"[+] Render Parameter: {params}")
        if args.test:
            print("\n[✔] Test erfolgreich abgeschlossen!")
        return

    try:
        res = dispatch_render_sync(
            situation_prompt=args.prompt,
            environment_key=args.env,
            clothing_override=args.clothing,
            user_custom_tags=args.tags,
            host=args.host
        )
        print("\n[✔] Render erfolgreich abgeschlossen!")
        print(f"    Prompt-ID:  {res['prompt_id']}")
        print(f"    Datei:      {res['filename']}")
        print(f"    Lokaler Pfad: {res['local_path']}")
        print(f"    Dauer:      {res['elapsed_seconds']}s")
    except Exception as e:
        print(f"[!] Fehler beim Rendern: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
