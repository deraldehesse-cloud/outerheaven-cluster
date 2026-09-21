#!/usr/bin/env python3
"""
comfy_evolution_loop.py - Autonomous ComfyUI Prompt-Evolution & Review Loop

Features:
  1. Batch-Generierung: 10 Bilder via Standard-ComfyUI (:8188) mit inkrementiertem/randomisiertem Seed.
  2. VLM-Review: Lokale Vision-Bewertung (1-10) via Ollama llava:latest (Treffergenauigkeit, Anatomie, Ästhetik).
  3. Prompt-Evolution: Reflexion über Bildfehler & Tag-Gewichtung via LLM für die nächste Runde.
  4. Benachrichtigung: Telegram-Meldung mit Top-1-Foto an Big Boss (7934555424).
  5. Session-Storage: /mnt/storage-beta/projekte/comfyui-evolution/session_YYYYMMDD_HHMMSS/iteration_X/
"""

import os
import sys
import re
import time
import json
import random
import base64
import shutil
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Tuple

# Cluster Defaults
DEFAULT_COMFYUI_HOST = os.environ.get("COMFYUI_HOST", "127.0.0.1:8188")
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_FOX_OLLAMA_URL = os.environ.get("FOX_OLLAMA_URL", "http://100.93.184.87:11434")
DEFAULT_VISION_MODEL = os.environ.get("VISION_MODEL", "llava:latest")
DEFAULT_LLM_MODEL = os.environ.get("LLM_MODEL", "darkidol-8b:latest")
DEFAULT_BASE_STORAGE = Path(os.environ.get("EVOLUTION_STORAGE_DIR", "/mnt/storage-beta/projekte/comfyui-evolution"))

# Telegram Credentials
PRIMARY_ENV_FILE = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / ".env"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_BIGBOSS_HAVEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or ""
if not TELEGRAM_BOT_TOKEN and PRIMARY_ENV_FILE.is_file():
    for line in PRIMARY_ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in ("TELEGRAM_BOT_TOKEN_BIGBOSS_HAVEN", "BIGBOSS_HAVEN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN") and not TELEGRAM_BOT_TOKEN:
            TELEGRAM_BOT_TOKEN = v

TELEGRAM_BIG_BOSS_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "7934555424"))


def sanitize_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def http_post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        resp_body = response.read().decode("utf-8", errors="replace")
        return json.loads(resp_body)


def send_telegram_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        http_post_json(url, payload, timeout=15)
        return True
    except Exception as e:
        print(f"[Telegram] Fehler beim Senden: {e}", file=sys.stderr)
        return False


def send_telegram_photo(chat_id: int, photo_path: str, caption: str = "") -> bool:
    if not TELEGRAM_BOT_TOKEN or not os.path.isfile(photo_path):
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    data = []
    data.append(f"--{boundary}\r\n".encode())
    data.append(f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())
    if caption:
        data.append(f"--{boundary}\r\n".encode())
        data.append(f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode())
        data.append(f"--{boundary}\r\n".encode())
        data.append('Content-Disposition: form-data; name="parse_mode"\r\n\r\nMarkdown\r\n'.encode())

    filename = os.path.basename(photo_path)
    data.append(f"--{boundary}\r\n".encode())
    data.append(f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode())
    data.append(b"Content-Type: image/png\r\n\r\n")
    with open(photo_path, "rb") as f:
        data.append(f.read())
    data.append(f"\r\n--{boundary}--\r\n".encode())

    body = b"".join(data)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[Telegram] Photo-Upload Fehler: {e}", file=sys.stderr)
        return False


# ==============================================================================
# 1. ComfyUI Workflow & Dispatch
# ==============================================================================

def build_comfy_workflow(
    positive_prompt: str,
    negative_prompt: str,
    seed: int,
    checkpoint: str = "DreamShaper8_LCM.safetensors",
    steps: int = 8,
    cfg: float = 2.0,
    sampler: str = "euler_ancestral",
    scheduler: str = "karras",
    width: int = 512,
    height: int = 768,
    prefix: str = "Evolution"
) -> dict:
    """Standard 7-node ComfyUI workflow for ultra-fast Arc A770 LCM rendering."""
    # LCM checkpoint optimization
    if "lcm" in checkpoint.lower():
        if cfg > 3.0: cfg = 2.0
        if steps > 12: steps = 8
        if sampler == "euler" and scheduler == "normal":
            sampler, scheduler = "euler_ancestral", "karras"

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
                "denoise": 1.0
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


def flush_local_ollama_vram(ollama_url: str = DEFAULT_OLLAMA_URL):
    """Signals local Ollama to immediately unload models from Arc A770 VRAM."""
    try:
        req = urllib.request.Request(
            f"{ollama_url.rstrip('/')}/api/generate",
            data=json.dumps({"model": DEFAULT_LLM_MODEL, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception:
        pass


def render_comfy_single(
    prompt: str,
    negative_prompt: str,
    seed: int,
    checkpoint: str = "DreamShaper8_LCM.safetensors",
    steps: int = 8,
    cfg: float = 2.0,
    width: int = 512,
    height: int = 768,
    host: str = DEFAULT_COMFYUI_HOST,
    save_dest: Optional[Path] = None,
    timeout: float = 60.0
) -> Tuple[bool, Optional[str], float]:
    """Renders a single image with strict GPU Render Lock and copies it to save_dest. Returns (success, filepath, elapsed)."""
    t0 = time.time()
    url_prompt = f"http://{host}/prompt"
    wf = build_comfy_workflow(
        positive_prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        checkpoint=checkpoint,
        steps=steps,
        cfg=cfg,
        width=width,
        height=height,
        prefix="EvoBatch"
    )

    try:
        from gpu_render_lock import gpu_render_scope, PRIORITY_HIGH
        has_lock_module = True
    except ImportError:
        has_lock_module = False

    def _execute_render():
        flush_local_ollama_vram()
        try:
            resp = http_post_json(url_prompt, wf, timeout=10)
            prompt_id = resp.get("prompt_id")
            if not prompt_id:
                return False, None, 0.0
        except Exception as e:
            print(f"[ComfyUI] Dispatch Fehler: {e}", file=sys.stderr)
            return False, None, 0.0

        # Poll history until complete
        poll_start = time.time()
        hist_url = f"http://{host}/history/{prompt_id}"
        filename = None
        subfolder = ""
        while (time.time() - poll_start) < timeout:
            try:
                req = urllib.request.Request(hist_url)
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = json.loads(r.read().decode("utf-8"))
                    if prompt_id in data:
                        outputs = data[prompt_id].get("outputs", {})
                        for node_id, node_out in outputs.items():
                            images = node_out.get("images", [])
                            if images:
                                filename = images[0].get("filename")
                                subfolder = images[0].get("subfolder", "")
                                break
                        if filename:
                            break
            except Exception:
                pass
            time.sleep(0.3)

        if not filename:
            return False, None, round(time.time() - t0, 2)

        # Download from ComfyUI view API
        view_url = f"http://{host}/view?filename={filename}&subfolder={subfolder}&type=output"
        dest_path = save_dest or Path("/tmp") / filename
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            urllib.request.urlretrieve(view_url, str(dest_path))
            elapsed = round(time.time() - t0, 2)
            return True, str(dest_path), elapsed
        except Exception as e:
            print(f"[ComfyUI] Download Fehler: {e}", file=sys.stderr)
            return False, None, round(time.time() - t0, 2)

    if has_lock_module:
        try:
            with gpu_render_scope(priority=PRIORITY_HIGH, task_name="evolution_render", timeout=timeout):
                return _execute_render()
        except Exception as err:
            print(f"[GPULock] Lock-Fehler bei Evolution Render: {err}", file=sys.stderr)
            return False, None, round(time.time() - t0, 2)
    else:
        return _execute_render()


# ==============================================================================
# 2. Local VLM Vision Analysis & Scoring
# ==============================================================================

def analyze_image_vlm(
    image_path: str,
    character_context: str = "",
    vision_model: str = DEFAULT_VISION_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL
) -> Dict[str, Any]:
    """
    Analyzes an image using local Ollama Vision (llava:latest).
    Evaluates:
      1. Character fidelity (Tattoo, geek/tactical look, physique)
      2. Anatomical correctness (Hands, fingers, limbs, face)
      3. Aesthetics & lighting (Skin detail, soft glow, realistic lighting)
    Returns: {"score": float (1-10), "flaws": [str], "positive_tags": [str], "negative_tags": [str]}
    """
    p = Path(image_path)
    if not p.is_file():
        return {"score": 5.0, "flaws": ["Datei nicht gefunden"], "positive_tags": [], "negative_tags": []}

    b64_img = base64.b64encode(p.read_bytes()).decode("utf-8")

    # Fast Moondream Vision Evaluator
    if "moondream" in vision_model.lower():
        prompt = (
            "Analyze this image: "
            "1. Describe the woman, her hair, clothing, and whether there is an intricate tattoo on her left arm. "
            "2. Check hands, fingers, and face for any anatomical artifacts, distortions or extra limbs. "
            "3. Note whether she is wearing glasses or spectacles."
        )
        payload = {
            "model": vision_model,
            "prompt": prompt,
            "images": [b64_img],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 250}
        }
        try:
            target_url = DEFAULT_FOX_OLLAMA_URL if "moondream" in vision_model.lower() else ollama_url
            gen_url = target_url.rstrip("/") + "/api/generate"
            res = http_post_json(gen_url, payload, timeout=120)
            desc = res.get("response", "").strip()
            desc = re.sub(r'!!!.*?!!!', '', desc).strip()
            desc_lower = desc.lower()

            flaws = []
            positive_tags = []
            negative_tags = []
            score = 8.0

            # 1. Tattoo check
            if "tattoo" in desc_lower:
                positive_tags.append("intricate arm sleeve tattoo")
                score += 0.5
            else:
                flaws.append("sleeve tattoo missing or obscured")
                negative_tags.append("(sleeve tattoo on left arm:1.2)")
                score -= 1.0

            # 2. Glasses check (strict exclusion)
            if "glasses" in desc_lower or "spectacles" in desc_lower or "eyewear" in desc_lower:
                flaws.append("unwanted glasses/spectacles visible")
                negative_tags.append("glasses, spectacles, eyewear")
                score -= 2.0
            else:
                positive_tags.append("clear alluring eyes, no glasses")

            # 3. Anatomy check
            distortions = ["distort", "deform", "extra limb", "extra finger", "missing finger", "bad hand", "defect", "weird hand"]
            found = [d for d in distortions if d in desc_lower]
            if found:
                flaws.append(f"anatomical distortions ({', '.join(found)})")
                negative_tags.extend(["bad anatomy", "deformed fingers", "extra limbs"])
                score -= 2.0
            else:
                positive_tags.append("clean natural anatomy and hands")

            # 4. Aesthetic & look
            if any(k in desc_lower for k in ["crop top", "tank top", "gamer", "shorts", "cleavage", "woman"]):
                positive_tags.append("gamer companion aesthetic")
                score += 0.5

            score = max(1.0, min(10.0, round(score, 1)))
            return {
                "score": score,
                "flaws": flaws,
                "positive_tags": positive_tags,
                "negative_tags": negative_tags,
                "description": desc
            }
        except Exception as e:
            print(f"[Moondream] Analyse-Fehler: {e}", file=sys.stderr)
            return {
                "score": 6.0,
                "flaws": [f"Moondream Fehler: {e}"],
                "positive_tags": ["photorealistic"],
                "negative_tags": ["bad anatomy"]
            }

    rubric = (
        "You are an expert AI art quality evaluator and VLM critic for an autonomous model evolution loop. "
        "Analyze the provided image in detail against these criteria:\n"
        "1. Character Fidelity: Megan, attractive young woman with very large full natural breasts, massive sleeve tattoo on left arm/shoulder, casual gamer aesthetic, natural dark hair, seductive face (no glasses).\n"
        "2. Anatomical Correctness: Check hands, fingers, limbs, eyes, waist and facial symmetry for distortions or artifacts.\n"
        "3. Aesthetics & Lighting: Soft studio rim lighting, skin texture realism, proper contrast without burned dark shadows.\n\n"
        "Return ONLY a valid JSON object with EXACTLY these keys:\n"
        "{\n"
        '  "score": <float between 1.0 and 10.0>,\n'
        '  "flaws": [<list of specific visible defects or artifacts>],\n'
        '  "positive_tags": [<list of 3-5 tags describing successful visual aspects>],\n'
        '  "negative_tags": [<list of 3-5 negative tags that would fix the observed flaws>]\n'
        "}"
    )

    payload = {
        "model": vision_model,
        "prompt": rubric,
        "images": [b64_img],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.2,
            "num_predict": 300
        }
    }

    try:
        gen_url = f"{ollama_url.rstrip('/')}/api/generate"
        res = http_post_json(gen_url, payload, timeout=30)
        raw_resp = res.get("response", "").strip()
        data = json.loads(raw_resp)
        score = float(data.get("score", 6.5))
        score = max(1.0, min(10.0, score))
        return {
            "score": round(score, 1),
            "flaws": data.get("flaws", []),
            "positive_tags": data.get("positive_tags", []),
            "negative_tags": data.get("negative_tags", [])
        }
    except Exception as e:
        print(f"[VLM] Analyse-Fehler ({vision_model}): {e}", file=sys.stderr)
        return {
            "score": 6.0,
            "flaws": [f"VLM Timeout / Parsing: {e}"],
            "positive_tags": ["photorealistic", "detailed"],
            "negative_tags": ["bad anatomy", "blurry"]
        }


# ==============================================================================
# 3. Prompt-Evolution & LLM Reflection
# ==============================================================================

def evolve_prompt_with_llm(
    current_prompt: str,
    current_negative: str,
    batch_results: List[Dict[str, Any]],
    llm_model: str = DEFAULT_LLM_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    user_feedback: Optional[str] = None,
    extra_negative_flaws: Optional[List[str]] = None
) -> Tuple[str, str, str]:
    """
    Takes 10 batch results (scores, flaws, positive/negative tag suggestions).
    Uses Ollama to evolve the prompt for the next round by:
      - Weighting successful tags (e.g. `(tag:1.2)`)
      - Adding negative prompt safeguards against detected flaws
    Returns (evolved_prompt, evolved_negative_prompt, summary_text).
    """
    sorted_res = sorted(batch_results, key=lambda x: x.get("score", 0), reverse=True)
    top_items = sorted_res[:3]
    bottom_items = sorted_res[-3:]

    top_positive_tags = []
    for item in top_items:
        top_positive_tags.extend(item.get("positive_tags", []))
    
    accumulated_flaws = []
    suggested_negative_tags = []
    for item in bottom_items:
        accumulated_flaws.extend(item.get("flaws", []))
        suggested_negative_tags.extend(item.get("negative_tags", []))

    if extra_negative_flaws:
        suggested_negative_tags = list(extra_negative_flaws) + suggested_negative_tags

    user_feedback_block = ""
    if user_feedback and user_feedback.strip():
        user_feedback_block = (
            "WICHTIGE REGIE-ANWEISUNG VOM USER (HÖCHSTE PRIORITÄT / ZWINGEND UMSETZEN):\n"
            f"\"{user_feedback.strip()}\"\n\n"
            "Priorisiere diese Regie-Anweisung vorrangig! Passe Pose, Beleuchtung, Kleidung, Blickwinkel oder Attribute "
            "genau nach diesem Wunsch an und ergänze passende Danbooru-Tags.\n\n"
        )

    instruction = (
        "Du bist ein meisterhafter Prompt-Engineer für Stable Diffusion / ComfyUI. "
        "Wir führen eine autonome Prompt-Evolution durch. Hier sind die Ergebnisse der letzten 10 Bilder:\n\n"
        f"AKTUELLE POSITIVE PROMPT:\n{current_prompt}\n\n"
        f"AKTUELLE NEGATIVE PROMPT:\n{current_negative}\n\n"
        f"{user_feedback_block}"
        f"ERFOLGREICHE MERKMALE (Top-Bilder):\n- " + "\n- ".join(top_positive_tags[:6]) + "\n\n"
        f"GEFUNDENE BILD-FEHLER (Schlechteste Bilder):\n- " + "\n- ".join(accumulated_flaws[:6]) + "\n\n"
        f"EMPFOHLENE NEGATIV-TAGS:\n- " + "\n- ".join(suggested_negative_tags[:6]) + "\n\n"
        "AUFGABE:\n"
        "1. Erstelle eine verbesserte, weiterentwickelte POSITIVE PROMPT. Nutze gezielte Syntax-Gewichtung "
        "(z. B. (intricate arm tattoo:1.2), (natural body proportions:1.15)), um Schwachstellen auszumerzen "
        "und die besten visuellen Qualitäten hervorzuheben.\n"
        "2. Ergänze die NEGATIVE PROMPT gezielt um die beobachteten Fehler.\n"
        "3. Verfasse eine kurze deutsche Zusammenfassung (2-3 Sätze), was konkret geändert wurde.\n\n"
        "Antworte AUSSCHLIESSLICH in validem JSON mit folgenden Schlüsseln:\n"
        "{\n"
        '  "evolved_prompt": "<optimierter positiver Prompt>",\n'
        '  "evolved_negative_prompt": "<optimierter negativer Prompt>",\n'
        '  "summary": "<Kurze deutsche Erklärung der vorgenommenen Optimierungen>"\n'
        "}"
    )

    payload = {
        "model": llm_model,
        "prompt": instruction,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.4,
            "num_predict": 450
        }
    }

    try:
        gen_url = f"{ollama_url.rstrip('/')}/api/generate"
        res = http_post_json(gen_url, payload, timeout=90)
        resp_text = res.get("response", "").strip()
        data = json.loads(resp_text)
        ev_prompt = data.get("evolved_prompt", "").strip() or current_prompt
        ev_neg = data.get("evolved_negative_prompt", "").strip() or current_negative
        summary = data.get("summary", "").strip() or "Prompt durch Gewichtung und gezielte Negativ-Tags optimiert."
        return ev_prompt, ev_neg, summary
    except Exception as e:
        print(f"[LLM] Prompt-Evolution Fehler: {e}", file=sys.stderr)
        new_neg = current_negative
        for fl in suggested_negative_tags[:4]:
            if fl.lower() not in new_neg.lower():
                new_neg += f", {fl}"
        return current_prompt, new_neg, f"Automatischer Fallback: Negativ-Prompt um gefundene VLM-Mängel erweitert ({e})."


# Evolution Lock File paths for GPU contention control (Arc A770)
EVOLUTION_LOCK_FILE = Path("/mnt/storage-beta/projekte/comfyui-evolution/evolution_lock.json")
FALLBACK_LOCK_FILE = Path("/tmp/outerheaven_evolution_lock.json")


def set_evolution_lock(active: bool, session_id: str = "", iteration: int = 0, duration_minutes: int = 10):
    payload = {
        "active": active,
        "session_id": session_id,
        "iteration": iteration,
        "timestamp": time.time(),
        "duration_minutes": duration_minutes,
        "expires_at": time.time() + (duration_minutes * 60) if active else 0
    }
    for p in [EVOLUTION_LOCK_FILE, FALLBACK_LOCK_FILE]:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass


# ==============================================================================
# 4. Evolution Runner Loop Engine
# ==============================================================================

class EvolutionRunner:
    """
    Manages the 10-batch generation and review loop, metadata saving,
    and state transitions.
    """

    def __init__(
        self,
        base_prompt: str,
        base_negative_prompt: str,
        checkpoint: str = "DreamShaper8_LCM.safetensors",
        steps: int = 8,
        cfg: float = 2.0,
        width: int = 512,
        height: int = 768,
        session_id: Optional[str] = None,
        storage_base: Path = DEFAULT_BASE_STORAGE,
        vision_model: str = DEFAULT_VISION_MODEL,
        llm_model: str = DEFAULT_LLM_MODEL,
        notify_telegram: bool = True,
        duration_minutes: int = 10
    ):
        self.session_id = session_id or datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self.session_dir = storage_base / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)

        self.current_prompt = base_prompt
        self.current_negative_prompt = base_negative_prompt
        self.checkpoint = checkpoint
        self.steps = steps
        self.cfg = cfg
        self.width = width
        self.height = height
        self.vision_model = vision_model
        self.llm_model = llm_model
        self.notify_telegram = notify_telegram
        self.duration_minutes = duration_minutes
        self.started_at = time.time()
        self.expires_at = self.started_at + (duration_minutes * 60)

        self.iteration = 0
        self.state = "IDLE"  # IDLE, RUNNING, PAUSED_FOR_USER, COMPLETED, ABORTED
        self.current_image_idx = 0
        self.total_images = 10
        self.status_message = "Bereit für Initial-Lauf."

        self.last_results: List[Dict[str, Any]] = []
        self.top_3_images: List[Dict[str, Any]] = []
        self.average_score = 0.0
        self.evolved_prompt = ""
        self.evolved_negative_prompt = ""
        self.evolution_summary = ""
        self.user_feedback = ""
        self.accumulated_feedback_flaws: List[str] = []

        self._abort_flag = False

    def abort(self):
        self._abort_flag = True
        self.state = "ABORTED"
        self.status_message = "Loop durch Nutzer abgebrochen."
        set_evolution_lock(active=False, session_id=self.session_id, iteration=self.iteration, duration_minutes=self.duration_minutes)

    def get_state_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "iteration": self.iteration,
            "state": self.state,
            "current_image": self.current_image_idx,
            "total_images": self.total_images,
            "status_message": self.status_message,
            "base_prompt": self.current_prompt,
            "base_negative_prompt": self.current_negative_prompt,
            "evolved_prompt": self.evolved_prompt or self.current_prompt,
            "evolved_negative_prompt": self.evolved_negative_prompt or self.current_negative_prompt,
            "evolution_summary": self.evolution_summary,
            "average_score": self.average_score,
            "top_3": self.top_3_images,
            "images": self.last_results,
            "checkpoint": self.checkpoint,
            "duration_minutes": self.duration_minutes,
            "expires_at": self.expires_at,
            "remaining_seconds": max(0, int(self.expires_at - time.time()))
        }

    def run_10_batch_cycle(self, on_progress: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        """Runs exactly one 10-batch cycle and pauses for user confirmation."""
        self._abort_flag = False
        self.iteration += 1
        self.state = "RUNNING"
        set_evolution_lock(active=True, session_id=self.session_id, iteration=self.iteration, duration_minutes=self.duration_minutes)
        iter_dir = self.session_dir / f"iteration_{self.iteration:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        self.last_results = []
        self.current_image_idx = 0

        print(f"\n=======================================================")
        print(f"[*] Starte Auto-Evolution Loop - Runde {self.iteration}")
        print(f"[*] Session-Ordner: {iter_dir}")
        print(f"[*] Basis-Prompt: {self.current_prompt[:90]}...")
        print(f"=======================================================\n")

        for i in range(1, self.total_images + 1):
            if self._abort_flag:
                self.state = "ABORTED"
                self.status_message = "Loop vorzeitig abgebrochen."
                break

            self.current_image_idx = i
            seed = random.randint(100000000, 999999999)
            img_filename = f"img_{i:02d}.png"
            img_dest = iter_dir / img_filename

            # 1. Render image
            self.status_message = f"Bild {i}/10 wird auf Arc A770 gerendert (Seed: {seed})..."
            if on_progress: on_progress(self.get_state_dict())

            success, local_path, render_sec = render_comfy_single(
                prompt=self.current_prompt,
                negative_prompt=self.current_negative_prompt,
                seed=seed,
                checkpoint=self.checkpoint,
                steps=self.steps,
                cfg=self.cfg,
                width=self.width,
                height=self.height,
                save_dest=img_dest
            )

            if not success or not img_dest.is_file():
                self.status_message = f"Bild {i}/10 Render fehlgeschlagen. Überspringe..."
                print(f"[!] Fehler bei Bild {i}")
                continue

            # 2. VLM Vision Review
            self.status_message = f"Bild {i}/10 gerendert ({render_sec}s). VLM-Review läuft..."
            if on_progress: on_progress(self.get_state_dict())

            vlm_review = analyze_image_vlm(
                image_path=str(img_dest),
                vision_model=self.vision_model
            )
            score = vlm_review.get("score", 5.0)

            record = {
                "index": i,
                "filename": img_filename,
                "local_path": str(img_dest),
                "url": f"/outputs/evolution/{self.session_id}/iteration_{self.iteration:02d}/{img_filename}",
                "seed": seed,
                "render_seconds": render_sec,
                "score": score,
                "flaws": vlm_review.get("flaws", []),
                "positive_tags": vlm_review.get("positive_tags", []),
                "negative_tags": vlm_review.get("negative_tags", [])
            }
            self.last_results.append(record)

            # Mirror to ComfyUI output folder for immediate Cockpit gallery visibility
            try:
                comfy_out_dir = Path(os.environ.get("COMFYUI_OUTPUT_DIR", "/home/outerheaven/ComfyUI/output"))
                if comfy_out_dir.is_dir():
                    gallery_dest = comfy_out_dir / f"Evo_{self.session_id}_it{self.iteration}_{img_filename}"
                    shutil.copy2(str(img_dest), str(gallery_dest))
            except Exception:
                pass

            self.status_message = f"Bild {i}/10 bewertet: VLM-Score {score:.1f}/10"
            print(f"[✔] Bild {i}/10 fertig | Render: {render_sec}s | Score: {score:.1f}/10")
            if on_progress: on_progress(self.get_state_dict())

        if self._abort_flag:
            set_evolution_lock(active=False, session_id=self.session_id, iteration=self.iteration, duration_minutes=self.duration_minutes)
            return self.get_state_dict()

        # 3. Compute stats & Top-3
        scores = [r["score"] for r in self.last_results if "score" in r]
        self.average_score = round(sum(scores) / max(1, len(scores)), 2)
        sorted_by_score = sorted(self.last_results, key=lambda x: x["score"], reverse=True)
        self.top_3_images = sorted_by_score[:3]

        # 4. Prompt Evolution & Reflection
        self.status_message = "10 Bilder analysiert. LLM reflektiert Bildmängel & evolviert Prompt..."
        if on_progress: on_progress(self.get_state_dict())

        ev_prompt, ev_neg, summary = evolve_prompt_with_llm(
            current_prompt=self.current_prompt,
            current_negative=self.current_negative_prompt,
            batch_results=self.last_results,
            llm_model=self.llm_model,
            user_feedback=self.user_feedback,
            extra_negative_flaws=self.accumulated_feedback_flaws
        )
        self.evolved_prompt = ev_prompt
        self.evolved_negative_prompt = ev_neg
        self.evolution_summary = summary

        # 5. Save metadata.json
        meta_payload = {
            "session_id": self.session_id,
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "checkpoint": self.checkpoint,
            "settings": {
                "steps": self.steps,
                "cfg": self.cfg,
                "width": self.width,
                "height": self.height
            },
            "base_prompt": self.current_prompt,
            "base_negative_prompt": self.current_negative_prompt,
            "average_score": self.average_score,
            "top_3": self.top_3_images,
            "images": self.last_results,
            "evolved_prompt": self.evolved_prompt,
            "evolved_negative_prompt": self.evolved_negative_prompt,
            "evolution_summary": self.evolution_summary,
            "user_feedback": self.user_feedback
        }
        meta_file = iter_dir / "metadata.json"
        meta_file.write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        # 6. Telegram Notification to Big Boss (Clean confirmation, no photo spam)
        if self.notify_telegram:
            msg_text = (
                f"Boss, ich habe 10 neue Bilder von mir gerendert und bewertet. Möchtest du sie sehen? 😘\n\n"
                f"• Session: `{self.session_id}` (Runde {self.iteration})\n"
                f"• Durchschnittsscore: *{self.average_score:.1f} / 10*"
            )
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "Ja, zeig her! 🔥", "callback_data": f"evo_show:{self.session_id}"},
                        {"text": "Später ⏳", "callback_data": "evo_dismiss"}
                    ]
                ]
            }
            send_telegram_message(TELEGRAM_BIG_BOSS_ID, msg_text, reply_markup=reply_markup)

        # 7. Pause state for user
        self.state = "PAUSED_FOR_USER"
        set_evolution_lock(active=False, session_id=self.session_id, iteration=self.iteration, duration_minutes=self.duration_minutes)
        self.status_message = f"Runde {self.iteration} abgeschlossen (Ø {self.average_score:.1f}/10). Warte auf Freigabe für Runde {self.iteration + 1}..."
        if on_progress: on_progress(self.get_state_dict())

        return self.get_state_dict()

    def advance_to_next_iteration(self, prompt_override: Optional[str] = None, neg_override: Optional[str] = None, user_feedback: Optional[str] = None):
        """Applies the evolved prompt and readies the runner for the next 10-batch run."""
        self.current_prompt = prompt_override or self.evolved_prompt or self.current_prompt
        self.current_negative_prompt = neg_override or self.evolved_negative_prompt or self.current_negative_prompt
        if user_feedback:
            self.user_feedback = user_feedback
        self.state = "IDLE"
        self.status_message = f"Bereit für Runde {self.iteration + 1} mit neuem optimiertem Prompt."


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Autonomous ComfyUI Prompt-Evolution Loop")
    parser.add_argument("--prompt", type=str, default="tactical gamer girl, intricate arm tattoo, studio lighting, 8k uhd")
    parser.add_argument("--negative", type=str, default="ugly, deformed, bad hands, blurry")
    parser.add_argument("--checkpoint", type=str, default="DreamShaper8_LCM.safetensors")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--cfg", type=float, default=2.0)
    parser.add_argument("--session", type=str, default="")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()

    runner = EvolutionRunner(
        base_prompt=args.prompt,
        base_negative_prompt=args.negative,
        checkpoint=args.checkpoint,
        steps=args.steps,
        cfg=args.cfg,
        session_id=args.session or None,
        notify_telegram=not args.no_telegram
    )

    def progress(st):
        print(f"[{st['current_image']}/{st['total_images']}] {st['status_message']}")

    res = runner.run_10_batch_cycle(on_progress=progress)
    print("\n[✔] Batch-Zyklus beendet!")
    print(f"Top Score: {res.get('top_3', [{}])[0].get('score')} | Ø: {res.get('average_score')}")
    print(f"Evolved Prompt:\n{res.get('evolved_prompt')}")




# ==============================================================================
# Bot Integration Hook (10-Batch Evolution with Best-Image Selection)
# ==============================================================================

def run_evolution_for_bot(
    situation: str = "",
    env_preset: str = "schreibtisch",
    total_images: int = 10,
    on_status: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """
    Executes a 10-batch evolution loop for the Companion Bot (Megan) under PRIORITY_HIGH.
    Acquires exclusive GPU lock, pre-empting any background Catlord training immediately.
    Returns the #1 best image selected by VLM, while saving all images and evolved prompts.
    """
    import dispatch_comfy
    from gpu_render_lock import gpu_render_scope, PRIORITY_HIGH

    cfg = dispatch_comfy.load_persona_config()
    pos_prompt, neg_prompt, render_params = dispatch_comfy.assemble_prompt(
        situation_prompt=situation,
        environment_key=env_preset,
        config=cfg
    )

    runner = EvolutionRunner(
        base_prompt=pos_prompt,
        base_negative_prompt=neg_prompt,
        checkpoint=render_params.get("checkpoint", "DreamShaper8_LCM.safetensors"),
        steps=int(render_params.get("steps", 8)),
        cfg=float(render_params.get("cfg", 2.0)),
        width=int(render_params.get("width", 512)),
        height=int(render_params.get("height", 768)),
        notify_telegram=False
    )
    runner.total_images = total_images

    def _prog(st):
        msg = st.get("status_message", "")
        if on_status and msg:
            on_status(msg)

    # Acquire GPU lock with PRIORITY_HIGH to preempt Catlord or other background workers
    with gpu_render_scope(PRIORITY_HIGH, f"megan_bot_{env_preset}", timeout=180.0):
        state = runner.run_10_batch_cycle(on_progress=_prog)

    best_img = None
    best_score = 0.0
    if runner.top_3_images:
        best = runner.top_3_images[0]
        best_img = best.get("local_path")
        best_score = best.get("score", 0.0)

    return {
        "success": bool(best_img and os.path.exists(best_img)),
        "best_image": best_img,
        "best_score": best_score,
        "average_score": runner.average_score,
        "session_dir": str(runner.session_dir),
        "evolved_prompt": runner.evolved_prompt,
        "evolved_negative_prompt": runner.evolved_negative_prompt,
        "evolution_summary": runner.evolution_summary,
        "all_results": runner.last_results
    }


if __name__ == "__main__":
    main()
