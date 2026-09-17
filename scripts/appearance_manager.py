#!/usr/bin/env python3
"""
appearance_manager.py - Modular Physical Appearance Profile Manager for Megan.
Outer Heaven Cluster & Megan Companion Studio.

Manages pure physical appearance presets (Height, Body, Bust, Face, Hair, Tattoos)
and guarantees cluster-wide atomic synchronization across:
  - character.json (Visual tags & German system_prompt conversational body lore)
  - persona.yaml (ComfyUI dispatcher base appearance)
  - bot_config.json (Default render positive prompt)
  - Automatic restart of bigboss-haven-bot.service
"""

import os
import sys
import re
import json
import shutil
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import yaml

PROJECT_ROOT = Path("/mnt/storage-beta/projekte/projekt-sexchat").resolve()
PRESETS_DIR = PROJECT_ROOT / "appearance_presets"
PRESETS_DIR.mkdir(parents=True, exist_ok=True)

CHARACTER_FILE = PROJECT_ROOT / "character.json"
PERSONA_FILE = PROJECT_ROOT / "persona.yaml"
BOT_CONFIG_FILE = PROJECT_ROOT / "bot_config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [appearance_manager]: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("appearance_manager")


def list_appearance_presets() -> List[Dict[str, Any]]:
    """Lists all available presets with metadata and active state flag."""
    active_id = get_active_preset_id()
    presets = []

    for file_path in sorted(PRESETS_DIR.glob("*.json")):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            pid = data.get("id", file_path.stem)
            presets.append({
                "id": pid,
                "name": data.get("name", pid.replace("_", " ").title()),
                "description": data.get("description", ""),
                "prompt_height": data.get("prompt_height", ""),
                "is_active": (pid == active_id),
                "visual": data.get("visual", {}),
                "physical_chat_lore": data.get("physical_chat_lore", {})
            })
        except Exception as e:
            logger.warning(f"Error reading preset {file_path.name}: {e}")

    return presets


def get_active_preset_id() -> str:
    """Detects active preset ID from character.json or bot_config.json."""
    if CHARACTER_FILE.is_file():
        try:
            cdata = json.loads(CHARACTER_FILE.read_text(encoding="utf-8"))
            if "active_appearance_preset" in cdata:
                return cdata["active_appearance_preset"]
        except Exception:
            pass

    if BOT_CONFIG_FILE.is_file():
        try:
            bdata = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            if "active_appearance_preset" in bdata:
                return bdata["active_appearance_preset"]
        except Exception:
            pass

    return "megan_athletic_sleeve"


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    """Loads a specific preset by ID."""
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", preset_id.strip())
    path = PRESETS_DIR / f"{clean_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to read preset {path}: {e}")
        return None


def save_appearance_preset(preset_data: Dict[str, Any]) -> Tuple[bool, str]:
    """Validates and saves a preset to appearance_presets/<id>.json."""
    raw_id = preset_data.get("id", "").strip() or preset_data.get("name", "").strip().lower().replace(" ", "_")
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", raw_id)
    if not clean_id:
        return False, "Ungültige oder fehlende Preset-ID."

    name = preset_data.get("name", "").strip() or clean_id.replace("_", " ").title()
    visual = preset_data.get("visual", {})

    body = visual.get("body", "").strip()
    face = visual.get("face", "").strip()
    hair = visual.get("hair", "").strip()

    if not body:
        return False, "Körper-/Statur-Tags ('body') dürfen nicht leer sein."

    target_path = PRESETS_DIR / f"{clean_id}.json"
    clean_preset = {
        "id": clean_id,
        "name": name,
        "description": preset_data.get("description", "").strip(),
        "prompt_height": preset_data.get("prompt_height", "").strip(),
        "prompt_weight": preset_data.get("prompt_weight", "").strip(),
        "visual": {
            "body": body,
            "eyes": visual.get("eyes", "").strip(),
            "eye_color": visual.get("eye_color", "").strip(),
            "eye_shape": visual.get("eye_shape", "").strip(),
            "hair": hair,
            "hair_color": visual.get("hair_color", "").strip(),
            "hair_style": visual.get("hair_style", "").strip(),
            "face": face,
            "features": visual.get("features", "").strip(),
            "style_aesthetic": visual.get("style_aesthetic", "photorealistic, raw photo, 8k uhd, dslr, high quality, soft studio lighting, rim lighting, vibrant, well-lit, high key, clear visibility, detailed skin texture, natural skin pores, realistic depth of field")
        },
        "physical_chat_lore": preset_data.get("physical_chat_lore", {
            "statur": "Schlank und feminin.",
            "oberweite": "Natürlich und wohlgeformt.",
            "gesicht": "Wunderschönes, markantes Gesicht.",
            "haare": "Lange, glänzende Haare.",
            "markantes_merkmal": visual.get("features", ""),
            "koerperliche_merkmale": "Sinnliche weibliche Kurven."
        })
    }

    try:
        tmp_path = target_path.with_suffix(f".tmp_{os.getpid()}")
        tmp_path.write_text(json.dumps(clean_preset, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, target_path)
        logger.info(f"Saved appearance preset {clean_id} to {target_path}")
        return True, f"Aussehen-Profil '{name}' ({clean_id}) erfolgreich gespeichert."
    except Exception as e:
        return False, f"Fehler beim Speichern des Profils: {e}"


def delete_appearance_preset(preset_id: str) -> Tuple[bool, str]:
    """Deletes a preset if it is not the active or default one."""
    clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "", preset_id.strip())
    if clean_id in ["megan_athletic_sleeve"]:
        return False, "Das Basis-Standardprofil kann nicht gelöscht werden."

    active_id = get_active_preset_id()
    if clean_id == active_id:
        return False, "Ein aktuell aktives Profil kann nicht gelöscht werden. Bitte aktiviere vorher ein anderes Profil."

    path = PRESETS_DIR / f"{clean_id}.json"
    if not path.is_file():
        return False, f"Profil '{clean_id}' existiert nicht."

    try:
        path.unlink()
        return True, f"Profil '{clean_id}' erfolgreich gelöscht."
    except Exception as e:
        return False, f"Fehler beim Löschen: {e}"


def _update_system_prompt_physical_lore(system_prompt: str, chat_lore: Dict[str, str]) -> str:
    """Replaces or injects the physical description bullet points in system_prompt."""
    statur = chat_lore.get("statur", "1,78 m groß, 58 kg schwer. Schlank, athletisch mit sehr weiblicher Sanduhr-Figur.")
    oberweite = chat_lore.get("oberweite", "sehr große, pralle und wunderschön geformte natürliche Brüste, tiefes Dekolleté.")
    gesicht = chat_lore.get("gesicht", "Wunderschönes, markantes Gesicht mit verführerischen grün-blauen Augen und sinnlichen Lippen.")
    haare = chat_lore.get("haare", "Lange, seidig glänzende dunkelbraune Haare mit weichen, sanften Wellen.")
    markant = chat_lore.get("markantes_merkmal", "Ein massives, detailreiches Sleeve-Tattoo auf dem linken Arm.")
    koerper = chat_lore.get("koerperliche_merkmale", "Knackiger Hintern, flacher trainierter Bauch, samtige Haut mit feiner Textur.")

    # Regex replacement for the standard section
    updated = system_prompt

    # Replace Statur & Silhouette
    if "* **Statur & Silhouette:**" in updated:
        updated = re.sub(r"\* \*\*Statur & Silhouette:\*\*.*", f"* **Statur & Silhouette:** {statur}", updated)
    # Replace Oberweite
    if "* **Oberweite:**" in updated:
        updated = re.sub(r"\* \*\*Oberweite:\*\*.*", f"* **Oberweite:** {oberweite}", updated)
    # Replace Gesicht & Blick
    if "* **Gesicht & Blick:**" in updated:
        updated = re.sub(r"\* \*\*Gesicht & Blick:\*\*.*", f"* **Gesicht & Blick:** {gesicht}", updated)
    # Replace Haare
    if "* **Haare:**" in updated:
        updated = re.sub(r"\* \*\*Haare:\*\*.*", f"* **Haare:** {haare}", updated)
    # Replace Markantes Merkmal
    if "* **Markantes Merkmal:**" in updated:
        updated = re.sub(r"\* \*\*Markantes Merkmal:\*\*.*", f"* **Markantes Merkmal:** {markant}", updated)
    # Replace Körperliche Merkmale
    if "* **Körperliche Merkmale:**" in updated:
        updated = re.sub(r"\* \*\*Körperliche Merkmale:\*\*.*", f"* **Körperliche Merkmale:** {koerper}", updated)

    return updated


def apply_appearance_preset(preset_id: str, trigger_bot_reload: bool = True) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Activates an appearance preset and synchronizes all cluster files:
      1. character.json (visual tags + German system_prompt body lore)
      2. persona.yaml (persona.appearance)
      3. bot_config.json (render.positive_prompt)
      4. Restarts bigboss-haven-bot.service if requested
    """
    preset = get_preset(preset_id)
    if not preset:
        return False, f"Aussehen-Profil '{preset_id}' nicht gefunden.", {}

    visual = preset.get("visual", {})
    body = visual.get("body", "")
    eyes = visual.get("eyes", "") or visual.get("eye_color", "")
    hair = visual.get("hair", "") or visual.get("hair_color", "")
    face = visual.get("face", "")
    features = visual.get("features", "")
    style = visual.get("style_aesthetic", "")
    chat_lore = preset.get("physical_chat_lore", {})

    errors = []

    # 1. Update character.json
    if CHARACTER_FILE.is_file():
        try:
            cdata = json.loads(CHARACTER_FILE.read_text(encoding="utf-8"))
            if "visual" not in cdata:
                cdata["visual"] = {}

            cdata["active_appearance_preset"] = preset["id"]
            cdata["visual"]["body"] = body
            cdata["visual"]["face"] = face
            cdata["visual"]["hair"] = hair
            if eyes:
                cdata["visual"]["eyes"] = eyes
            cdata["visual"]["features"] = features
            if style:
                cdata["visual"]["style_aesthetic"] = style

            # Rebuild base_positive_prompt while keeping clothes separate
            base_tags = [body, eyes, hair, face, features, style]
            cdata["visual"]["base_positive_prompt"] = ", ".join([t for t in base_tags if t])

            # Update German system_prompt body description
            if "system_prompt" in cdata:
                cdata["system_prompt"] = _update_system_prompt_physical_lore(cdata["system_prompt"], chat_lore)

            # Atomic save with .bak
            bak_char = CHARACTER_FILE.with_suffix(".json.bak")
            shutil.copy2(CHARACTER_FILE, bak_char)
            tmp_char = CHARACTER_FILE.with_suffix(f".tmp_{os.getpid()}")
            tmp_char.write_text(json.dumps(cdata, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_char, CHARACTER_FILE)
            logger.info(f"Updated character.json with preset {preset_id}")
        except Exception as e:
            errors.append(f"character.json: {e}")
    else:
        errors.append("character.json existiert nicht.")

    # 2. Update persona.yaml
    if PERSONA_FILE.is_file():
        try:
            bak_yaml = PERSONA_FILE.with_suffix(".yaml.bak")
            shutil.copy2(PERSONA_FILE, bak_yaml)
            with open(PERSONA_FILE, "r", encoding="utf-8") as f:
                ydata = yaml.safe_load(f) or {}

            if "persona" in ydata and "appearance" in ydata["persona"]:
                ydata["persona"]["appearance"]["body"] = body
                ydata["persona"]["appearance"]["face"] = face
                ydata["persona"]["appearance"]["hair"] = hair
                ydata["persona"]["appearance"]["features"] = features
                if style:
                    ydata["persona"]["appearance"]["style_aesthetic"] = style

            tmp_yaml = PERSONA_FILE.with_suffix(f".tmp_{os.getpid()}")
            with open(tmp_yaml, "w", encoding="utf-8") as f:
                yaml.dump(ydata, f, allow_unicode=True, default_flow_style=False)
            os.replace(tmp_yaml, PERSONA_FILE)
            logger.info(f"Updated persona.yaml with preset {preset_id}")
        except Exception as e:
            errors.append(f"persona.yaml: {e}")

    # 3. Update bot_config.json
    if BOT_CONFIG_FILE.is_file():
        try:
            bak_bot = BOT_CONFIG_FILE.with_suffix(".json.bak")
            shutil.copy2(BOT_CONFIG_FILE, bak_bot)
            bdata = json.loads(BOT_CONFIG_FILE.read_text(encoding="utf-8"))
            bdata["active_appearance_preset"] = preset["id"]
            if "render" in bdata:
                bdata["render"]["positive_prompt"] = ", ".join([t for t in [body, face, hair, features, style] if t])

            tmp_bot = BOT_CONFIG_FILE.with_suffix(f".tmp_{os.getpid()}")
            tmp_bot.write_text(json.dumps(bdata, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_bot, BOT_CONFIG_FILE)
            logger.info(f"Updated bot_config.json with preset {preset_id}")
        except Exception as e:
            errors.append(f"bot_config.json: {e}")

    # 4. Trigger bot service reload
    bot_reloaded = False
    if trigger_bot_reload:
        try:
            logger.info("Restarting bigboss-haven-bot.service after appearance switch...")
            res = subprocess.run(["systemctl", "--user", "restart", "bigboss-haven-bot.service"], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                bot_reloaded = True
            else:
                # Fallback via SSH to outerheaven if running from another node (e.g. bigboss workstation)
                res_ssh = subprocess.run(["ssh", "outerheaven", "systemctl --user restart bigboss-haven-bot.service"], capture_output=True, text=True, check=False)
                if res_ssh.returncode == 0:
                    bot_reloaded = True
                else:
                    logger.warning(f"Could not restart bigboss-haven-bot: {res.stderr or res_ssh.stderr}")
        except Exception as e:
            logger.warning(f"Failed to restart bigboss-haven-bot.service: {e}")

    if errors:
        return False, f"Fehler bei Synchronisation: {'; '.join(errors)}", preset

    msg = f"Aussehen-Profil '{preset.get('name')}' erfolgreich aktiviert und clusterweit synchronisiert!"
    if bot_reloaded:
        msg += " • bigboss-haven-bot.service neu geladen."

    return True, msg, preset


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Megan Appearance Manager")
    parser.add_argument("--list", action="store_true", help="List presets")
    parser.add_argument("--apply", type=str, help="Apply preset by ID")
    args = parser.parse_args()

    if args.list:
        print(json.dumps(list_appearance_presets(), indent=2, ensure_ascii=False))
    elif args.apply:
        success, msg, _ = apply_appearance_preset(args.apply)
        print(f"Result: {success} - {msg}")
