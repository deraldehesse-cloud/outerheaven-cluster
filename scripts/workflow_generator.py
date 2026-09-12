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
    # Anti-darkness lighting tokens
    lighting_pos = "soft studio lighting, rim lighting, vibrant, well-lit, high key, clear visibility"
    lighting_neg = "underexposed, dark shadows, pitch black background, gloomy, dim lighting"

    if lighting_pos not in positive_prompt:
        positive_prompt = f"{positive_prompt.rstrip(', ')}, {lighting_pos}"
    if lighting_neg not in negative_prompt:
        negative_prompt = f"{negative_prompt.rstrip(', ')}, {lighting_neg}"

    # CFG & Steps tuning
    is_lcm = "lcm" in checkpoint.lower()
    if is_lcm:
        if cfg > 3.0:
            cfg = 2.0
        if steps > 12:
            steps = 8
    else:
        if steps < 20:
            steps = 25
        if cfg > 7.0:
            cfg = 6.0

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


def create_multiview_pose_workflow(
    base_positive_prompt: str,
    base_negative_prompt: str,
    checkpoint: str = "DreamShaper8_LCM.safetensors",
    loras: Optional[List[Dict[str, Any]]] = None,
    width: int = 512,
    height: int = 768,
    steps: int = 14,
    cfg: float = 6.5,
    seed: Optional[int] = None,
    filename_prefix: str = "Haven_MultiView"
) -> Dict[str, Any]:
    """
    Constructs a 4-angle canonical multi-perspective workflow:
      1. Front (0°): full frontal orthographic view
      2. Side-Left (90°): side profile from left
      3. Back (180°): full rear perspective
      4. Side-Right (270°): side profile from right
    Output batch is optimized for ingestion by reconstruct_3d.py.
    """
    # Anti-darkness lighting tokens
    lighting_pos = "soft studio lighting, rim lighting, vibrant, well-lit, high key, clear visibility"
    lighting_neg = "underexposed, dark shadows, pitch black background, gloomy, dim lighting"

    if lighting_pos not in base_positive_prompt:
        base_positive_prompt = f"{base_positive_prompt.rstrip(', ')}, {lighting_pos}"
    if lighting_neg not in base_negative_prompt:
        base_negative_prompt = f"{base_negative_prompt.rstrip(', ')}, {lighting_neg}"

    is_lcm = "lcm" in checkpoint.lower()
    if is_lcm:
        if cfg > 3.0:
            cfg = 2.0
        if steps > 12:
            steps = 8
    else:
        if steps < 20:
            steps = 25
        if cfg > 7.0:
            cfg = 6.0

    if seed is None:
        seed = secrets.randbelow(999999999999)

    workflow: Dict[str, Any] = {}

    # 1. Base Checkpoint Loader
    workflow["1"] = {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": checkpoint}
    }

    current_model = ["1", 0]
    current_clip = ["1", 1]
    current_vae = ["1", 2]

    # 2. LoRA Chaining
    next_id = 10
    if loras:
        for lora in loras:
            lora_name = lora.get("name")
            if not lora_name:
                continue
            l_id = str(next_id)
            workflow[l_id] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": lora_name,
                    "strength_model": float(lora.get("strength_model", 0.8)),
                    "strength_clip": float(lora.get("strength_clip", 0.8)),
                    "model": current_model,
                    "clip": current_clip
                }
            }
            current_model = [l_id, 0]
            current_clip = [l_id, 1]
            next_id += 1

    # 3. Shared Negative Prompt
    workflow["3"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": f"{base_negative_prompt}, extreme angles, tilted horizon, asymmetric poses, dynamic motion",
            "clip": current_clip
        }
    }

    # 4 Canonical Camera Views
    views = [
        {"suffix": "0_front", "angle_prompt": "full frontal orthographic view, standing straight, looking directly forward, symmetric pose, neutral expression"},
        {"suffix": "1_side_l", "angle_prompt": "side profile view from left, 90 degrees turned left profile, orthographic side reference, arms at side"},
        {"suffix": "2_back", "angle_prompt": "back view, rear perspective, looking away from camera, 180 degrees turn, full body back reference"},
        {"suffix": "3_side_r", "angle_prompt": "side profile view from right, 270 degrees turned right profile, orthographic side reference, arms at side"}
    ]

    for idx, v in enumerate(views):
        pos_id = str(100 + idx * 10)
        latent_id = str(101 + idx * 10)
        sampler_id = str(102 + idx * 10)
        vae_dec_id = str(103 + idx * 10)
        save_id = str(104 + idx * 10)

        # Positive encode with view angle
        workflow[pos_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": f"{base_positive_prompt}, {v['angle_prompt']}, clean neutral background, studio lighting, highly consistent character",
                "clip": current_clip
            }
        }

        # Empty Latent
        workflow[latent_id] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1}
        }

        # KSampler with fixed seed for high consistency
        workflow[sampler_id] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed + idx,  # Correlated seeds for consistency
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": current_model,
                "positive": [pos_id, 0],
                "negative": ["3", 0],
                "latent_image": [latent_id, 0]
            }
        }

        # VAE Decode
        workflow[vae_dec_id] = {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": [sampler_id, 0],
                "vae": current_vae
            }
        }

        # Save Image
        workflow[save_id] = {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": f"{filename_prefix}_{v['suffix']}",
                "images": [vae_dec_id, 0]
            }
        }

    return {
        "prompt": workflow,
        "views": [v["suffix"] for v in views],
        "seed": seed
    }


if __name__ == "__main__":
    wf = create_txt2img_workflow(
        positive_prompt="1girl, haven, beautiful cinematic portrait",
        negative_prompt="bad quality, deformed",
        checkpoint="DreamShaper8_LCM.safetensors",
        loras=[{"name": "detail_slider.safetensors", "strength_model": 0.75}]
    )
    print("Standard Workflow Node Count:", len(wf["prompt"]))

    mv_wf = create_multiview_pose_workflow(
        base_positive_prompt="1girl, haven, tactical crop top, denim shorts",
        base_negative_prompt="bad quality, deformed",
        checkpoint="DreamShaper8_LCM.safetensors"
    )
    print("Multi-View Pose Workflow Node Count:", len(mv_wf["prompt"]), "Views:", mv_wf["views"])

