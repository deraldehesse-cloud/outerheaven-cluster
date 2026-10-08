"""
ComfyUI Graph-Builder & Validator Engine
Shared module for Megan Studio (:8080), Mission Control (:8000), and Hermes Tools.

Provides:
- ComfyUI object_info node catalog caching (data/comfy_node_registry.json)
- Dynamic graph assemblage & template mutation (ComfyGraph)
- Pre-execution graph validation (validate_graph)
"""

import copy
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("comfy-graph-builder")

# Base paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REGISTRY_CACHE_FILE = DATA_DIR / "comfy_node_registry.json"

# Fallback cache location in mission-control if present
MISSION_CONTROL_CACHE = BASE_DIR.parent.parent / "mission-control" / "data" / "object_info_cache.json"

TEMPLATE_SEARCH_PATHS = [
    BASE_DIR / "workflows" / "templates",
    BASE_DIR / "workflows",
    BASE_DIR.parent.parent / "workflows" / "templates",
    BASE_DIR.parent.parent / "workflows",
]

DEFAULT_COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")

# Standard fallback definitions for core nodes in case ComfyUI is completely offline
CORE_FALLBACK_NODES: Dict[str, Dict[str, Any]] = {
    "CheckpointLoaderSimple": {
        "required": {"ckpt_name": ["COMBO", {}]},
        "optional": {},
        "output": ["MODEL", "CLIP", "VAE"],
        "output_name": ["MODEL", "CLIP", "VAE"],
        "category": "loaders"
    },
    "EmptyLatentImage": {
        "required": {
            "width": ["INT", {"default": 1024}],
            "height": ["INT", {"default": 1024}],
            "batch_size": ["INT", {"default": 1}]
        },
        "optional": {},
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "category": "latent"
    },
    "CLIPTextEncode": {
        "required": {
            "text": ["STRING", {"multiline": True}],
            "clip": ["CLIP", {}]
        },
        "optional": {},
        "output": ["CONDITIONING"],
        "output_name": ["CONDITIONING"],
        "category": "conditioning"
    },
    "KSampler": {
        "required": {
            "model": ["MODEL", {}],
            "seed": ["INT", {"default": 0}],
            "steps": ["INT", {"default": 20}],
            "cfg": ["FLOAT", {"default": 7.0}],
            "sampler_name": ["COMBO", {}],
            "scheduler": ["COMBO", {}],
            "positive": ["CONDITIONING", {}],
            "negative": ["CONDITIONING", {}],
            "latent_image": ["LATENT", {}],
            "denoise": ["FLOAT", {"default": 1.0}]
        },
        "optional": {},
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "category": "sampling"
    },
    "VAEDecode": {
        "required": {
            "samples": ["LATENT", {}],
            "vae": ["VAE", {}]
        },
        "optional": {},
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "category": "latent"
    },
    "SaveImage": {
        "required": {
            "images": ["IMAGE", {}],
            "filename_prefix": ["STRING", {"default": "ComfyUI"}]
        },
        "optional": {},
        "output": [],
        "output_name": [],
        "category": "image"
    },
    "LoraLoader": {
        "required": {
            "model": ["MODEL", {}],
            "clip": ["CLIP", {}],
            "lora_name": ["COMBO", {}],
            "strength_model": ["FLOAT", {"default": 1.0}],
            "strength_clip": ["FLOAT", {"default": 1.0}]
        },
        "optional": {},
        "output": ["MODEL", "CLIP"],
        "output_name": ["MODEL", "CLIP"],
        "category": "loaders"
    }
}


# ==============================================================================
# 1. Node Catalog Caching
# ==============================================================================

def sync_node_definitions(comfy_url: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """
    Fetches node definitions from ComfyUI /object_info and caches a compact
    registry in data/comfy_node_registry.json.
    Falls back to existing cache or default definitions if offline.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not force and REGISTRY_CACHE_FILE.is_file():
        try:
            with open(REGISTRY_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if isinstance(cached, dict) and len(cached) > 5:
                    return cached
        except Exception as e:
            logger.warning(f"Konnte bestehende Node-Registry nicht laden: {e}")

    url = (comfy_url or DEFAULT_COMFYUI_URL).rstrip("/")
    target = f"{url}/object_info"

    raw_data: Optional[Dict[str, Any]] = None
    try:
        req = urllib.request.Request(target, headers={"User-Agent": "Megan-Graph-Builder/1.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                raw_data = json.loads(resp.read().decode("utf-8"))
                logger.info(f"ComfyUI /object_info erfolgreich von {url} geladen ({len(raw_data)} Nodes).")
    except Exception as e:
        logger.warning(f"Konnte ComfyUI /object_info nicht von {target} abrufen: {e}")

    # Fallback to mission-control full cache if network call failed
    if not raw_data and MISSION_CONTROL_CACHE.is_file():
        try:
            with open(MISSION_CONTROL_CACHE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                logger.info(f"ComfyUI /object_info aus Mission Control Cache geladen ({len(raw_data)} Nodes).")
        except Exception as e:
            logger.warning(f"Konnte Fallback-Cache nicht laden: {e}")

    compact_registry: Dict[str, Any] = {}

    if raw_data and isinstance(raw_data, dict):
        for class_name, info in raw_data.items():
            if not isinstance(info, dict):
                continue
            inputs_meta = info.get("input", {})
            compact_registry[class_name] = {
                "required": inputs_meta.get("required", {}),
                "optional": inputs_meta.get("optional", {}),
                "output": info.get("output", []),
                "output_name": info.get("output_name", []),
                "category": info.get("category", "")
            }
    else:
        logger.warning("Nutze Basis-Fallback-Katalog für ComfyUI Node-Registry.")
        compact_registry = copy.deepcopy(CORE_FALLBACK_NODES)

    try:
        with open(REGISTRY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(compact_registry, f, indent=2, ensure_ascii=False)
        logger.info(f"Node-Registry erfolgreich nach {REGISTRY_CACHE_FILE} gecacht ({len(compact_registry)} Nodes).")
    except Exception as e:
        logger.error(f"Fehler beim Speichern der Node-Registry: {e}")

    return compact_registry


def get_available_nodes() -> List[str]:
    """Returns sorted list of all available node class types."""
    registry = sync_node_definitions()
    return sorted(list(registry.keys()))


def get_node_inputs(class_name: str) -> Dict[str, Any]:
    """Returns required and optional input definitions for a node class."""
    registry = sync_node_definitions()
    node_info = registry.get(class_name, {})
    return {
        "required": node_info.get("required", {}),
        "optional": node_info.get("optional", {})
    }


def get_node_outputs(class_name: str) -> List[str]:
    """Returns output type names for a node class."""
    registry = sync_node_definitions()
    return registry.get(class_name, {}).get("output", [])


# ==============================================================================
# 2. Template-Mutation & Graph-Assembler
# ==============================================================================

class ComfyGraph:
    """
    Builder and mutator for ComfyUI API Prompt format graphs.
    """

    def __init__(self, template_name: Optional[str] = None):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        if template_name:
            self.load_template(template_name)

    def load_template(self, template_name: str) -> "ComfyGraph":
        """
        Loads a base graph template by name (e.g. 'base_sd15', 'base_sdxl', 'base_catlord_comic').
        """
        clean_name = template_name.replace(".json", "")
        file_candidates = [
            f"{clean_name}.json",
            f"{template_name}",
        ]

        found_path: Optional[Path] = None
        for d in TEMPLATE_SEARCH_PATHS:
            if not d.is_dir():
                continue
            for cand in file_candidates:
                p = d / cand
                if p.is_file():
                    found_path = p
                    break
            if found_path:
                break

        if not found_path:
            raise FileNotFoundError(f"ComfyUI Template '{template_name}' nicht in bekannten Pfaden gefunden.")

        with open(found_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Ungültiges Template-Format in {found_path}: Erwartetes Dict.")

        # If wrapped under 'prompt', unwrap
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]

        self.nodes = copy.deepcopy(data)
        logger.info(f"Template '{template_name}' geladen ({len(self.nodes)} Nodes).")
        return self

    def add_node(self, node_id: Optional[str] = None, class_type: str = "", inputs: Optional[Dict[str, Any]] = None) -> str:
        """
        Adds a node to the graph. Generates a new unique numeric ID if node_id is None.
        """
        if not node_id:
            existing_ids = [int(k) for k in self.nodes.keys() if k.isdigit()]
            next_id = max(existing_ids) + 1 if existing_ids else 1
            node_id = str(next_id)
        else:
            node_id = str(node_id)

        self.nodes[node_id] = {
            "class_type": class_type,
            "inputs": copy.deepcopy(inputs or {})
        }
        return node_id

    def connect(self, from_node_id: str, from_socket_idx: int, to_node_id: str, input_name: str) -> "ComfyGraph":
        """
        Connects an output of from_node to an input socket of to_node.
        Format: to_node.inputs[input_name] = [str(from_node_id), int(from_socket_idx)]
        """
        from_id_str = str(from_node_id)
        to_id_str = str(to_node_id)

        if to_id_str not in self.nodes:
            raise KeyError(f"Ziel-Node #{to_id_str} existiert nicht im Graph.")
        if from_id_str not in self.nodes:
            logger.warning(f"Quell-Node #{from_id_str} existiert aktuell noch nicht im Graph (Verbindung vorbereitet).")

        if "inputs" not in self.nodes[to_id_str]:
            self.nodes[to_id_str]["inputs"] = {}

        self.nodes[to_id_str]["inputs"][input_name] = [from_id_str, int(from_socket_idx)]
        return self

    def set_param(self, node_id: str, param_name: str, value: Any) -> "ComfyGraph":
        """
        Sets a specific parameter or input value on a node.
        """
        n_id = str(node_id)
        if n_id not in self.nodes:
            raise KeyError(f"Node #{n_id} existiert nicht im Graph.")

        if "inputs" not in self.nodes[n_id]:
            self.nodes[n_id]["inputs"] = {}

        self.nodes[n_id]["inputs"][param_name] = value
        return self

    def remove_node(self, node_id: str) -> "ComfyGraph":
        """
        Removes a node and clears any dangling link references pointing to it.
        """
        n_id = str(node_id)
        if n_id in self.nodes:
            del self.nodes[n_id]

        # Clean dangling references
        for other_id, other_data in self.nodes.items():
            inps = other_data.get("inputs", {})
            to_delete = []
            for k, v in inps.items():
                if isinstance(v, list) and len(v) == 2 and str(v[0]) == n_id:
                    to_delete.append(k)
            for k in to_delete:
                del inps[k]

        return self

    def get_nodes_by_type(self, class_type: str) -> List[str]:
        """Returns all node IDs matching a class_type."""
        return [nid for nid, data in self.nodes.items() if data.get("class_type") == class_type]

    def set_positive_prompt(self, text: str) -> "ComfyGraph":
        """Convenience method: sets text on the positive CLIPTextEncode node."""
        ksamplers = self.get_nodes_by_type("KSampler")
        if ksamplers:
            ks_inputs = self.nodes[ksamplers[0]].get("inputs", {})
            pos_link = ks_inputs.get("positive")
            if isinstance(pos_link, list) and len(pos_link) == 2:
                pos_node_id = str(pos_link[0])
                if pos_node_id in self.nodes:
                    self.set_param(pos_node_id, "text", text)
                    return self

        # Fallback: search any CLIPTextEncode whose ID is 3 or first found
        clip_nodes = self.get_nodes_by_type("CLIPTextEncode")
        if "3" in clip_nodes:
            self.set_param("3", "text", text)
        elif clip_nodes:
            self.set_param(clip_nodes[0], "text", text)
        return self

    def set_negative_prompt(self, text: str) -> "ComfyGraph":
        """Convenience method: sets text on the negative CLIPTextEncode node."""
        ksamplers = self.get_nodes_by_type("KSampler")
        if ksamplers:
            ks_inputs = self.nodes[ksamplers[0]].get("inputs", {})
            neg_link = ks_inputs.get("negative")
            if isinstance(neg_link, list) and len(neg_link) == 2:
                neg_node_id = str(neg_link[0])
                if neg_node_id in self.nodes:
                    self.set_param(neg_node_id, "text", text)
                    return self

        clip_nodes = self.get_nodes_by_type("CLIPTextEncode")
        if "4" in clip_nodes:
            self.set_param("4", "text", text)
        elif len(clip_nodes) > 1:
            self.set_param(clip_nodes[1], "text", text)
        return self

    def set_sampler_params(
        self,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        sampler_name: Optional[str] = None,
        scheduler: Optional[str] = None,
        denoise: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> "ComfyGraph":
        """Convenience method: updates KSampler parameters."""
        ksamplers = self.get_nodes_by_type("KSampler")
        if not ksamplers:
            return self
        ks_id = ksamplers[0]
        if steps is not None:
            self.set_param(ks_id, "steps", int(steps))
        if cfg is not None:
            self.set_param(ks_id, "cfg", float(cfg))
        if sampler_name is not None:
            self.set_param(ks_id, "sampler_name", str(sampler_name))
        if scheduler is not None:
            self.set_param(ks_id, "scheduler", str(scheduler))
        if denoise is not None:
            self.set_param(ks_id, "denoise", float(denoise))
        if seed is not None:
            self.set_param(ks_id, "seed", int(seed))
        return self

    def set_dimensions(self, width: int, height: int, batch_size: int = 1) -> "ComfyGraph":
        """Convenience method: updates EmptyLatentImage dimensions."""
        latents = self.get_nodes_by_type("EmptyLatentImage")
        if latents:
            l_id = latents[0]
            self.set_param(l_id, "width", int(width))
            self.set_param(l_id, "height", int(height))
            self.set_param(l_id, "batch_size", int(batch_size))
        return self

    def add_lora(self, lora_name: str, strength_model: float = 0.8, strength_clip: float = 0.8) -> str:
        """
        Inserts a LoraLoader node between the Checkpoint loader and the KSampler/CLIPTextEncode nodes.
        """
        ckpts = self.get_nodes_by_type("CheckpointLoaderSimple")
        if not ckpts:
            raise RuntimeError("Kein CheckpointLoaderSimple im Graph gefunden, um LoRA einzuhängen.")
        ckpt_id = ckpts[0]

        lora_node_id = self.add_node(
            None,
            "LoraLoader",
            {
                "lora_name": lora_name,
                "strength_model": float(strength_model),
                "strength_clip": float(strength_clip),
                "model": [ckpt_id, 0],
                "clip": [ckpt_id, 1]
            }
        )

        # Redirect existing model and clip links to the new LoRA node
        for nid, data in self.nodes.items():
            if nid == lora_node_id:
                continue
            inps = data.get("inputs", {})
            for inp_key, link in list(inps.items()):
                if isinstance(link, list) and len(link) == 2 and str(link[0]) == str(ckpt_id):
                    socket_idx = link[1]
                    if socket_idx == 0:  # MODEL output of Checkpoint
                        inps[inp_key] = [lora_node_id, 0]
                    elif socket_idx == 1:  # CLIP output of Checkpoint
                        inps[inp_key] = [lora_node_id, 1]

        return lora_node_id

    def export_prompt(self) -> Dict[str, Any]:
        """Returns deep copy of graph in ComfyUI API Prompt format."""
        return copy.deepcopy(self.nodes)

    def to_dict(self) -> Dict[str, Any]:
        return self.export_prompt()


# ==============================================================================
# 3. Graph-Validator vor Ausführung
# ==============================================================================

def validate_graph(prompt_dict: Dict[str, Any], registry: Optional[Dict[str, Any]] = None) -> Tuple[bool, List[str]]:
    """
    Validates a ComfyUI API prompt graph before submission.
    Checks:
    - Non-empty graph structure
    - class_type existence in node registry
    - Socket link references ["node_id", socket_idx] point to existing nodes
    - Required inputs on core nodes (KSampler, CheckpointLoaderSimple, etc.)
    Returns (is_valid, list_of_error_messages).
    """
    errors: List[str] = []

    if not isinstance(prompt_dict, dict):
        return False, ["Graph muss ein JSON-Dictionary sein."]

    nodes = prompt_dict.get("prompt", prompt_dict)
    if not isinstance(nodes, dict) or not nodes:
        return False, ["Graph enthält keine Nodes oder ist leer."]

    if registry is None:
        registry = sync_node_definitions()

    node_ids = set(str(k) for k in nodes.keys())

    # Required inputs for known key nodes
    CRITICAL_REQUIREMENTS = {
        "KSampler": ["model", "positive", "negative", "latent_image"],
        "CheckpointLoaderSimple": ["ckpt_name"],
        "EmptyLatentImage": ["width", "height"],
        "CLIPTextEncode": ["clip", "text"],
        "VAEDecode": ["samples", "vae"],
        "SaveImage": ["images"],
        "LoraLoader": ["model", "clip", "lora_name"],
    }

    has_sampler = False
    has_output = False

    for nid, node in nodes.items():
        nid_str = str(nid)
        if not isinstance(node, dict):
            errors.append(f"Node #{nid_str} ist kein gültiges Node-Objekt.")
            continue

        class_type = node.get("class_type")
        if not class_type or not isinstance(class_type, str):
            errors.append(f"Node #{nid_str} besitzt keinen gültigen 'class_type'.")
            continue

        # Check if class_type is in registry
        if registry and class_type not in registry:
            # Only flag as error if registry is loaded from live ComfyUI
            errors.append(f"Node #{nid_str}: Klasse '{class_type}' ist in der ComfyUI-Registry nicht registriert.")

        if "KSampler" in class_type:
            has_sampler = True
        if "SaveImage" in class_type or "PreviewImage" in class_type:
            has_output = True

        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            errors.append(f"Node #{nid_str} ({class_type}): 'inputs' muss ein Dictionary sein.")
            continue

        # Check critical input requirements
        reqs = CRITICAL_REQUIREMENTS.get(class_type, [])
        for r in reqs:
            if r not in inputs or inputs[r] is None:
                errors.append(f"Node #{nid_str} ({class_type}): Pflicht-Eingang '{r}' fehlt oder ist nicht belegt.")

        # Check all link references
        for inp_name, val in inputs.items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[1], int):
                src_id = str(val[0])
                socket_idx = val[1]
                if src_id not in node_ids:
                    errors.append(
                        f"Node #{nid_str} ({class_type}) Eingang '{inp_name}': "
                        f"Verlinkte Quell-Node #{src_id} existiert nicht im Graph."
                    )
                else:
                    src_class = nodes[src_id].get("class_type")
                    if registry and src_class in registry:
                        src_outputs = registry[src_class].get("output", [])
                        if src_outputs and socket_idx >= len(src_outputs):
                            errors.append(
                                f"Node #{nid_str} Eingang '{inp_name}': Quell-Node #{src_id} ({src_class}) "
                                f"hat nur {len(src_outputs)} Ausgänge, Socket-Index {socket_idx} ist ungültig."
                            )

    if not has_sampler:
        errors.append("Graph enthält keinen KSampler für die Inferenz.")
    if not has_output:
        errors.append("Graph enthält keine Ausgabe-Node (SaveImage / PreviewImage).")

    is_valid = len(errors) == 0
    return is_valid, errors
