#!/usr/bin/env python3
"""
catlord_idle_worker.py - Autonomous Background Practice & Training Loop for Catlord.
Runs when Outer Heaven Intel Arc A770 is IDLE (no Megan Bot & no User Schnell-Render).

Features:
  - Rotates through Catlord scenarios (Elliot, Loco, Attic Guy, Gert Baert).
  - Low-priority GPU lock: automatically yields immediately when Megan or User requests GPU.
  - VLM-evaluated artwork quality & style scoring.
  - Storage: /mnt/storage-beta/projekte/projekt-catlord/assets/training/
"""

import os
import sys
import time
import json
import random
import logging
from pathlib import Path
from typing import Dict, Any, Optional

# Add project paths
for p in ["/mnt/storage-beta/projekte/projekt-sexchat", "/home/outerheaven/mission-control"]:
    if p not in sys.path and Path(p).is_dir():
        sys.path.insert(0, p)

from gpu_render_lock import GPURenderLock, PRIORITY_LOW, is_preempt_requested, get_gpu_lock_status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [catlord_worker]: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("catlord_worker")

TRAINING_DIR = Path("/mnt/storage-beta/projekte/projekt-catlord/assets/training")
TRAINING_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = TRAINING_DIR / "training_log.json"
STATE_FILE = Path("/tmp/catlord_worker_state.json")

SCENARIOS = [
    {
        "id": "elliot_tank",
        "character": "Elliot (Tank)",
        "prompt": "catlord comic style, graphic novel illustration, ink contours, vibrant saturated colors, Elliot massive 8kg orange tabby Maine Coon cat, muscular feline ninja tank, long fur, sharp Wolverine claws extended, defensive combat pose blocking apartment doorway, dramatic angle, attic apartment background, rim lighting, 8k uhd, high quality",
        "negative": "human, girl, glasses, dog, ugly, deformed, bad anatomy, extra limbs, extra paws, blurry, lowres, text, watermark, signature, photorealistic"
    },
    {
        "id": "loco_assassin",
        "character": "Loco (Assassin)",
        "prompt": "catlord comic style, graphic novel panel, dynamic action scene, Loco agile fluffy grey-white Maine Coon ninja cat, leaping through air with bushy tail motion blur, acrobatic assassin pose, intense green cat eyes, high speed action, cinematic comic angle, ink line art, vibrant comic colors",
        "negative": "human, dog, glasses, ugly, deformed, distorted, extra legs, extra ears, blurry, low quality, 3d render, watermark, photorealistic"
    },
    {
        "id": "attic_guy_sebastian",
        "character": "Sebastian (Attic Guy)",
        "prompt": "catlord comic style, graphic novel illustration, ink contours, Sebastian Attic Guy sitting at cluttered wooden workbench in attic, relaxed posture, holding tools, surrounded by giant fluffy Maine Coon ninja cats, roof window sunlight beams, comic book art, detailed background, expressive line art",
        "negative": "glasses, photorealistic, ugly, deformed, bad hands, bad fingers, extra limbs, blurry, lowres, watermark"
    },
    {
        "id": "gert_baert_boss",
        "character": "Gert Baert (Zentrum der Macht)",
        "prompt": "catlord comic style, graphic novel illustration, ink contours, Gert Baert frantic eccentric construction boss shouting into vintage telephone receiver, wild eyes, messy hair, chaotic construction office blueprints and unpaid bills flying, comic satire style, expressive comic linework, vibrant colors",
        "negative": "calm, serene, photorealistic, ugly, deformed, blurry, lowres, watermark, 3d render"
    }
]


class CatlordIdleWorker:
    """Manages autonomous idle training for Catlord Manga Panels."""

    def __init__(self, idle_cooldown: float = 20.0, render_interval: float = 25.0):
        self.idle_cooldown = idle_cooldown
        self.render_interval = render_interval
        self.enabled = True
        self.running = False
        self.total_generated = 0
        self.current_scenario_idx = 0
        self.current_state = "INITIALIZING"
        self.last_panel_path: Optional[str] = None
        self.last_score = 0.0

    def get_state(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "state": self.current_state,
            "total_generated": self.total_generated,
            "last_panel": self.last_panel_path,
            "last_score": self.last_score,
            "current_scenario": SCENARIOS[self.current_scenario_idx]["character"] if SCENARIOS else None
        }

    def save_state_file(self):
        try:
            STATE_FILE.write_text(json.dumps(self.get_state(), indent=2), encoding="utf-8")
        except Exception:
            pass

    def run_step(self) -> bool:
        """Executes a single practice step if GPU is idle. Returns True if rendered, False if yielded."""
        if not self.enabled:
            self.current_state = "DISABLED"
            self.save_state_file()
            return False

        # 1. Check GPU telemetry & preemption
        gpu_status = get_gpu_lock_status()
        if gpu_status.get("locked") or gpu_status.get("preempt_requested") or is_preempt_requested():
            self.current_state = "PAUSED_FOR_HIGH_PRIORITY"
            self.save_state_file()
            return False

        last_high = gpu_status.get("last_high_priority_time", 0.0)
        time_since_high = time.time() - last_high
        if time_since_high < self.idle_cooldown:
            self.current_state = f"COOLDOWN ({int(self.idle_cooldown - time_since_high)}s)"
            self.save_state_file()
            return False

        # 2. Select next scenario
        scenario = SCENARIOS[self.current_scenario_idx % len(SCENARIOS)]
        self.current_scenario_idx += 1

        # 3. Attempt to acquire LOW-priority lock
        lock = GPURenderLock(priority=PRIORITY_LOW, task_name=f"catlord_{scenario['id']}", project="catlord")
        if not lock.acquire():
            self.current_state = "YIELDED_LOCK"
            self.save_state_file()
            return False

        try:
            self.current_state = f"RENDERING ({scenario['character']})"
            self.save_state_file()
            logger.info(f"🎨 Starte Catlord-Training: {scenario['character']}...")

            # Render image via ComfyUI
            import comfy_evolution_loop
            img_dest = TRAINING_DIR / f"catlord_panel_{int(time.time())}_{scenario['id']}.png"
            seed = random.randint(100000000, 999999999)

            success, path_str, elapsed = comfy_evolution_loop.render_comfy_single(
                prompt=scenario["prompt"],
                negative_prompt=scenario["negative"],
                seed=seed,
                checkpoint="DreamShaper8_LCM.safetensors",
                steps=8,
                cfg=2.0,
                width=512,
                height=768,
                save_dest=img_dest
            )
        finally:
            # Release GPU lock IMMEDIATELY after ComfyUI generation (2.5s)
            # so high-priority requests are never blocked by VLM evaluation.
            lock.release()

        if not success or not img_dest.is_file():
            logger.warning(f"Render fehlgeschlagen für {scenario['character']}")
            return False

        self.last_panel_path = str(img_dest)
        self.total_generated += 1

        # 4. Quick VLM Evaluation if no high-priority task is waiting
        score = 7.5
        if not is_preempt_requested():
            self.current_state = "EVALUATING"
            self.save_state_file()
            vlm_res = comfy_evolution_loop.analyze_image_vlm(
                image_path=str(img_dest),
                vision_model="llava:latest"
            )
            score = vlm_res.get("score", 7.5)
            self.last_score = score
            logger.info(f"✔ {scenario['character']} gerendert ({elapsed:.2f}s) | VLM-Score: {score:.1f}/10")
        else:
            logger.info(f"✔ {scenario['character']} gerendert ({elapsed:.2f}s) | VLM übersprungen (Preempt aktiv)")

    def start_loop(self):
        """Infinite worker loop for background execution."""
        self.running = True
        logger.info("Catlord Idle Worker gestartet.")
        while self.running:
            try:
                rendered = self.run_step()
                sleep_time = self.render_interval if rendered else 3.0
                # Sleep in small increments to respond quickly to shutdown or preemption
                for _ in range(int(sleep_time * 2)):
                    if not self.running:
                        break
                    time.sleep(0.5)
            except Exception as e:
                logger.error(f"Fehler im Catlord-Worker: {e}")
                time.sleep(5.0)


_GLOBAL_CATLORD_WORKER: Optional[CatlordIdleWorker] = None

def get_catlord_worker() -> CatlordIdleWorker:
    global _GLOBAL_CATLORD_WORKER
    if _GLOBAL_CATLORD_WORKER is None:
        _GLOBAL_CATLORD_WORKER = CatlordIdleWorker()
    return _GLOBAL_CATLORD_WORKER


if __name__ == "__main__":
    worker = get_catlord_worker()
    worker.start_loop()
