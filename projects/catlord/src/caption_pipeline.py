#!/usr/bin/env python3
"""
Catlord LoRA Caption & Dataset Pipeline
---------------------------------------
Validates images, prepares Kohya-compatible folder structures, and generates
training caption (.txt) files with designated trigger words ('cash_cat', 'elliot_cat')
and scene descriptions via local Ollama VLM (llava:latest on Outer Heaven cluster).
"""

import os
import sys
import argparse
import base64
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import requests
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("catlord_pipeline")

BASE_DIR = Path("/mnt/storage-beta/projekte/projekt-catlord")
DATASET_DIR = BASE_DIR / "dataset"
TRAINING_DIR = BASE_DIR / "training"

CONFIG = {
    "cash": {
        "dataset_dir": DATASET_DIR / "cash",
        "training_dir": TRAINING_DIR / "cash" / "img" / "10_cash_cat",
        "trigger_word": "cash_cat",
        "default_desc": "grey cat with white markings, tactical domestic cat",
    },
    "elliot": {
        "dataset_dir": DATASET_DIR / "elliot",
        "training_dir": TRAINING_DIR / "elliot" / "img" / "10_elliot_cat",
        "trigger_word": "elliot_cat",
        "default_desc": "orange and white tabby cat, fluffy feline, relaxed in domestic setting",
    }
}

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://100.116.38.29:11434")
DEFAULT_VLM_MODEL = os.environ.get("VLM_MODEL", "llava:latest")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def validate_images(directory: Path, target_size: Tuple[int, int] = (1024, 1024)) -> Dict[str, any]:
    """Validates image dimensions in a directory."""
    if not directory.exists():
        logger.error(f"Directory not found: {directory}")
        return {"total": 0, "matching": 0, "dimensions": {}, "files": []}

    files = [f for f in directory.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    dims_count: Dict[Tuple[int, int], int] = {}
    details = []

    for f in sorted(files):
        try:
            with Image.open(f) as img:
                sz = img.size
                dims_count[sz] = dims_count.get(sz, 0) + 1
                is_match = (sz == target_size)
                details.append({
                    "filename": f.name,
                    "size": sz,
                    "exact_target": is_match
                })
        except Exception as e:
            logger.warning(f"Could not read {f.name}: {e}")

    matching = dims_count.get(target_size, 0)
    logger.info(
        f"Validation for {directory.name}: {len(files)} images, "
        f"{matching}/{len(files)} exactly {target_size[0]}x{target_size[1]}"
    )
    for sz, count in sorted(dims_count.items()):
        logger.info(f"  - {sz[0]}x{sz[1]}: {count} images")

    return {
        "directory": str(directory),
        "total": len(files),
        "matching": matching,
        "dimensions": {f"{k[0]}x{k[1]}": v for k, v in dims_count.items()},
        "details": details
    }


def copy_or_link_dataset(char_key: str, link_only: bool = False):
    """Copies or hardlinks images from dataset into the Kohya training folder."""
    cfg = CONFIG[char_key]
    src_dir = cfg["dataset_dir"]
    dst_dir = cfg["training_dir"]
    dst_dir.mkdir(parents=True, exist_ok=True)

    src_files = [f for f in src_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    count = 0
    for f in src_files:
        target = dst_dir / f.name
        if not target.exists():
            if link_only:
                try:
                    os.link(f, target)
                except OSError:
                    import shutil
                    shutil.copy2(f, target)
            else:
                import shutil
                shutil.copy2(f, target)
            count += 1

    logger.info(f"Populated {count} images into {dst_dir}")
    os.chmod(dst_dir, 0o775)


def generate_vlm_caption(
    image_path: Path,
    trigger_word: str,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    vlm_model: str = DEFAULT_VLM_MODEL,
    fallback_desc: str = ""
) -> str:
    """Queries Ollama LLaVA to produce a concise scene description."""
    try:
        with open(image_path, "rb") as f:
            b64_img = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            "Describe this cat scene in one concise phrase (under 25 words). "
            "Focus on the cat pose, angle, expression, and environment. "
            "Do NOT include conversational preamble like 'The image depicts'."
        )

        payload = {
            "model": vlm_model,
            "prompt": prompt,
            "images": [b64_img],
            "stream": False,
            "options": {
                "temperature": 0.2,
                "top_p": 0.9
            }
        }

        res = requests.post(f"{ollama_url.rstrip('/')}/api/generate", json=payload, timeout=45)
        if res.status_code == 200:
            raw_text = res.json().get("response", "").strip()
            # Clean up common filler words
            for prefix in [
                "The image shows ", "This image shows ", "The image depicts ",
                "In this photo, ", "A photo of ", "A picture of "
            ]:
                if raw_text.lower().startswith(prefix.lower()):
                    raw_text = raw_text[len(prefix):].strip()
            raw_text = raw_text.rstrip(".")
            caption = f"{trigger_word}, {raw_text}"
            return caption
        else:
            logger.warning(f"Ollama returned HTTP {res.status_code}: {res.text}")
    except Exception as e:
        logger.warning(f"Failed to query VLM for {image_path.name}: {e}")

    # Fallback caption
    return f"{trigger_word}, {fallback_desc}"


def process_captions(
    char_key: str,
    use_vlm: bool = True,
    overwrite: bool = False,
    ollama_url: str = DEFAULT_OLLAMA_URL
):
    """Processes captions for all images in the training directory."""
    cfg = CONFIG[char_key]
    img_dir = cfg["training_dir"]
    trigger_word = cfg["trigger_word"]
    fallback = cfg["default_desc"]

    if not img_dir.exists():
        logger.error(f"Training dir does not exist: {img_dir}. Run --populate first.")
        return

    images = [f for f in img_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    logger.info(f"Generating captions for {len(images)} images in {img_dir.name}...")

    success_count = 0
    for img_path in sorted(images):
        txt_path = img_path.with_suffix(".txt")
        if txt_path.exists() and not overwrite:
            continue

        if use_vlm:
            caption = generate_vlm_caption(
                img_path, trigger_word, ollama_url=ollama_url, fallback_desc=fallback
            )
        else:
            caption = f"{trigger_word}, {fallback}"

        txt_path.write_text(caption + "\n", encoding="utf-8")
        os.chmod(txt_path, 0o664)
        success_count += 1
        logger.info(f"[{img_path.stem}] -> {caption}")

    logger.info(f"Completed captioning for {char_key}: {success_count} caption files written.")


def main():
    parser = argparse.ArgumentParser(description="Catlord LoRA Caption & Dataset Pipeline")
    parser.add_argument("--character", choices=["cash", "elliot", "all"], default="all",
                        help="Target character (default: all)")
    parser.add_argument("--validate", action="store_true",
                        help="Validate raw dataset images dimensions")
    parser.add_argument("--populate", action="store_true",
                        help="Populate images into Kohya training/ directory")
    parser.add_argument("--caption", action="store_true",
                        help="Generate .txt caption files for training")
    parser.add_argument("--no-vlm", action="store_true",
                        help="Use fallback tag captions without querying Ollama VLM")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing .txt caption files")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL,
                        help=f"Ollama server URL (default: {DEFAULT_OLLAMA_URL})")

    args = parser.parse_args()

    characters = ["cash", "elliot"] if args.character == "all" else [args.character]

    if not (args.validate or args.populate or args.caption):
        # Default action: run validation
        args.validate = True

    if args.validate:
        logger.info("=== DATASET VALIDATION ===")
        report = {}
        for char in characters:
            report[char] = validate_images(CONFIG[char]["dataset_dir"])
        print("\n" + "=" * 50)
        print("SUMMARY REPORT (Image Dimensions):")
        for char, rep in report.items():
            print(f"[{char.upper()}]: Total {rep['total']} images")
            print(f"  Exact 1024x1024: {rep['matching']}")
            print(f"  Dimensions: {json.dumps(rep['dimensions'], indent=2)}")
        print("=" * 50 + "\n")

    if args.populate:
        logger.info("=== POPULATING TRAINING DIRECTORIES ===")
        for char in characters:
            copy_or_link_dataset(char, link_only=False)

    if args.caption:
        logger.info("=== GENERATING CAPTIONS ===")
        for char in characters:
            process_captions(
                char,
                use_vlm=not args.no_vlm,
                overwrite=args.overwrite,
                ollama_url=args.ollama_url
            )


if __name__ == "__main__":
    main()
