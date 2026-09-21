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

# Mandatory Stop Sequences & Ollama Options to prevent hallucination / roleplay drift
MEGAN_OLLAMA_OPTIONS = {
    "temperature": 0.7,
    "stop": [
        "<|im_end|>",
        "<|eot_id|>",
        "<|end_of_text|>",
        "\nUser:",
        "\nBig Boss:",
        "\nSebastian:",
        "Big Boss:",
        "Sebastian:"
    ]
}

def sanitize_megan_response(text: str) -> str:
    """
    Truncates text strictly before '*Big Boss' or '*Sebastian' to prevent the model
    from speaking or roleplaying for the user.
    """
    if not text:
        return ""
    patterns = ["*Big Boss", "*big boss", "*Sebastian", "*sebastian"]
    earliest = -1
    for p in patterns:
        idx = text.find(p)
        if idx != -1:
            if earliest == -1 or idx < earliest:
                earliest = idx
    if earliest != -1:
        return text[:earliest].rstrip()
    return text

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
