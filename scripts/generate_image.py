#!/usr/bin/env python3
"""
generate_image.py - Fox -> Outer Heaven ComfyUI Image Generator
Triggers ComfyUI on Outer Heaven (100.116.38.29:8188) via REST API.
"""

import os
import sys
import json
import time
import uuid
import urllib.request
import urllib.parse
import urllib.error
import argparse

COMFYUI_HOST = os.environ.get("COMFYUI_HOST", "100.116.38.29:8188")
COMFYUI_URL = f"http://{COMFYUI_HOST}"
DEFAULT_CHECKPOINT = os.environ.get("COMFYUI_CHECKPOINT", "DreamShaper8_LCM.safetensors")
DEFAULT_NEGATIVE = "ugly, deformed, bad anatomy, bad limbs, blurry, low quality, artifacts, text, watermark"

def build_workflow(prompt_text, negative_prompt=DEFAULT_NEGATIVE, checkpoint=DEFAULT_CHECKPOINT, lora=None, lora_strength=1.0, width=512, height=512, steps=14, cfg=6.5, seed=None):
    if seed is None:
        import secrets
        seed = secrets.randbelow(999999999999)

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
            "inputs": {"clip": ["1", 1], "text": prompt_text}
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
                "sampler_name": "euler",
                "scheduler": "normal",
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
                "filename_prefix": "OH_%date:yyyy-MM-dd%_%batch_num%",
                "images": ["6", 0]
            }
        }
    }

    if lora and lora.lower() not in ("none", "keines", "", "false"):
        workflow["8"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": lora,
                "strength_model": lora_strength,
                "strength_clip": lora_strength
            }
        }
        workflow["3"]["inputs"]["clip"] = ["8", 1]
        workflow["4"]["inputs"]["clip"] = ["8", 1]
        workflow["5"]["inputs"]["model"] = ["8", 0]

    return {"prompt": workflow}

def queue_prompt(workflow, client_id=None):
    payload = dict(workflow)
    if client_id:
        payload["client_id"] = client_id
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{COMFYUI_URL}/prompt", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

def wait_for_completion(prompt_id, timeout=120.0, poll_interval=2.0):
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(poll_interval)
        try:
            req = urllib.request.Request(f"{COMFYUI_URL}/history/{prompt_id}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                history = json.loads(resp.read().decode("utf-8"))
                if prompt_id in history:
                    return history[prompt_id]
        except Exception:
            continue
    raise TimeoutError(f"Render timeout nach {timeout}s für Prompt-ID {prompt_id}")

def extract_images(history_entry):
    images = []
    outputs = history_entry.get("outputs", {})
    for node_id, node_output in outputs.items():
        if "images" in node_output:
            for img in node_output["images"]:
                img_copy = dict(img)
                img_copy["view_url"] = (
                    f"{COMFYUI_URL}/view?filename={urllib.parse.quote(img['filename'])}"
                    f"&subfolder={urllib.parse.quote(img.get('subfolder', ''))}"
                    f"&type={urllib.parse.quote(img.get('type', 'output'))}"
                )
                images.append(img_copy)
    return images

def generate_image(prompt, negative_prompt=DEFAULT_NEGATIVE, checkpoint=DEFAULT_CHECKPOINT, lora=None, lora_strength=1.0, width=512, height=512, steps=14, cfg=6.5, seed=None, wait=True, timeout=120.0):
    client_id = str(uuid.uuid4())
    wf = build_workflow(
        prompt_text=prompt,
        negative_prompt=negative_prompt,
        checkpoint=checkpoint,
        lora=lora,
        lora_strength=lora_strength,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        seed=seed
    )
    res = queue_prompt(wf, client_id=client_id)
    prompt_id = res.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI gab keine Prompt-ID zurück: {res}")

    result = {
        "success": True,
        "prompt_id": prompt_id,
        "status": "queued",
        "checkpoint": checkpoint,
        "lora": lora,
        "client_id": client_id
    }

    if wait:
        history = wait_for_completion(prompt_id, timeout=timeout)
        images = extract_images(history)
        result["status"] = "completed"
        result["images"] = images
        if images:
            result["primary_image_url"] = images[0]["view_url"]
            result["filename"] = images[0]["filename"]

    return result

def main():
    parser = argparse.ArgumentParser(description="Fox -> Outer Heaven ComfyUI Image Generator")
    parser.add_argument("prompt", nargs="?", default="tactical Foxhound operative in digital stealth camo, 8k masterpiece", help="Image prompt")
    parser.add_argument("--negative", default=DEFAULT_NEGATIVE, help="Negative prompt")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help="Checkpoint model name")
    parser.add_argument("--lora", default=None, help="LoRA model name")
    parser.add_argument("--lora-strength", type=float, default=1.0, help="LoRA strength")
    parser.add_argument("--width", type=int, default=512, help="Width")
    parser.add_argument("--height", type=int, default=512, help="Height")
    parser.add_argument("--steps", type=int, default=14, help="Steps")
    parser.add_argument("--cfg", type=float, default=6.5, help="CFG scale")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for completion")
    parser.add_argument("--timeout", type=float, default=120.0, help="Wait timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    try:
        res = generate_image(
            prompt=args.prompt,
            negative_prompt=args.negative,
            checkpoint=args.checkpoint,
            lora=args.lora,
            lora_strength=args.lora_strength,
            width=args.width,
            height=args.height,
            steps=args.steps,
            cfg=args.cfg,
            seed=args.seed,
            wait=not args.no_wait,
            timeout=args.timeout,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[✔] Status: {res.get('status')}")
            print(f"    Prompt-ID: {res.get('prompt_id')}")
            if "images" in res:
                for img in res["images"]:
                    print(f"    Datei: {img.get('filename')}")
                    print(f"    URL:   {img.get('view_url')}")
    except Exception as e:
        print(f"[!] Fehler bei Bilderstellung: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
