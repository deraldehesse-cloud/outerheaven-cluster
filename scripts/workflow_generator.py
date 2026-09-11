#!/usr/bin/env python3
"""
workflow_generator.py - Dynamic ComfyUI Execution Graph & Prompt Builder.

Programmatically generates valid ComfyUI prompt workflows (JSON API format)
supporting SD1.5, SDXL, Latent Consistency Models (LCM), and LoRA stacks.
"""

import json
import secrets
from typing import Dict, Any, Optional, List


def create_txt2img_workflow(
    positive_prompt: str,
    negative_prompt: str,
    checkpoint: str = "DreamShaper8_LCM.safetensors",
    loras: Optional[List[Dict[str, Any]]] = None,
    width: int = 512,
    height: int = 768,
    steps: int = 14,
    cfg: float = 6.5,
    sampler_name: str = "euler",
    scheduler: str = "normal",
    denoise: float = 1.0,
    seed: Optional[int] = None,
    filename_prefix: str = "Megan_Companion"
) -> Dict[str, Any]:
    """
    Constructs a robust ComfyUI prompt execution graph.
    Supports dynamic LoRA chaining.
    """
    if seed is None:
        seed = secrets.randbelow(999999999999)

    workflow: Dict[str, Any] = {}

    # Node 1: Checkpoint Loader
    workflow["1"] = {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": checkpoint
        }
    }

    current_model_link = ["1", 0]
    current_clip_link = ["1", 1]
    vae_link = ["1", 2]

    # Dynamic LoRA Stacking
    next_node_id = 10
    if loras:
        for lora in loras:
            lora_name = lora.get("name")
            if not lora_name:
                continue
            strength_model = float(lora.get("strength_model", 0.8))
            strength_clip = float(lora.get("strength_clip", 0.8))

            lora_id = str(next_node_id)
            workflow[lora_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": lora_name,
                    "strength_model": strength_model,
                    "strength_clip": strength_clip,
                    "model": current_model_link,
                    "clip": current_clip_link
                }
            }
            current_model_link = [lora_id, 0]
            current_clip_link = [lora_id, 1]
            next_node_id += 1

    # Node 2: Positive CLIP Text Encode
    workflow["2"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": positive_prompt,
            "clip": current_clip_link
        }
    }

    # Node 3: Negative CLIP Text Encode
    workflow["3"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": negative_prompt,
            "clip": current_clip_link
        }
    }

    # Node 4: Empty Latent Image
    workflow["4"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": width,
            "height": height,
            "batch_size": 1
        }
    }

    # Node 5: KSampler
    workflow["5"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "denoise": denoise,
            "model": current_model_link,
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": ["4", 0]
        }
    }

    # Node 6: VAE Decode
    workflow["6"] = {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["5", 0],
            "vae": vae_link
        }
    }

    # Node 7: Save Image
    workflow["7"] = {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": filename_prefix,
            "images": ["6", 0]
        }
    }

    return {"prompt": workflow}


if __name__ == "__main__":
    wf = create_txt2img_workflow(
        positive_prompt="1girl, megan, beautiful cinematic portrait",
        negative_prompt="bad quality, deformed",
        checkpoint="DreamShaper8_LCM.safetensors",
        loras=[{"name": "detail_slider.safetensors", "strength_model": 0.75}]
    )
    print("Generated Workflow Node Count:", len(wf["prompt"]))
    print(json.dumps(wf, indent=2)[:400] + "\n...")
