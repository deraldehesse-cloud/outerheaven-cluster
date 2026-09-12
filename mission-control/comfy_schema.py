#!/usr/bin/env python3
"""
comfy_schema.py - Intent-based, type-safe ComfyUI Schema & Pipeline Engine.
Provides:
  - Pydantic models for Nodes, Slots, Pipelines, and Archetypes
  - ComfyUI /object_info parser & schema normalizer
  - Slot type-compatibility checker & pipeline validator
  - Native ComfyUI prompt JSON exporter
  - Comprehensive fallback node catalog for offline / startup resilience
  - FastAPI APIRouter for modern typed API usage
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set, Union
from pydantic import BaseModel, Field

logger = logging.getLogger("mission-control.comfy-schema")

# --- Standard & Advanced Connection Types in ComfyUI ---
KNOWN_CONNECTION_TYPES = {
    "MODEL", "CLIP", "VAE", "CONDITIONING", "LATENT", "IMAGE", "MASK",
    "CONTROL_NET", "STYLE_MODEL", "GLIGEN", "UPSCALE_MODEL", "MOTION_MODEL",
    "IPADAPTER", "FACE_ANALYSIS", "BBOX_DETECTOR", "SEGS", "SAM_MODEL",
    "DETAILER_HOOK", "LORA_STACK", "CONTROL_NET_STACK", "IMAGE_BOUNDS",
    "GUIDER", "NOISE", "SIGMAS", "PHOTOMAKER", "INSIGHTFACE"
}

PRIMITIVE_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


def is_connection_slot_type(t: Any) -> bool:
    """Returns True if the type represents a connectable slot rather than a widget."""
    if not t or not isinstance(t, str):
        return False
    up = t.strip().upper()
    if up in PRIMITIVE_TYPES or up.startswith("COMBO"):
        return False
    return True


# --- Pydantic Schema Models ---

class NodeInputTypes(BaseModel):
    required: Dict[str, str] = Field(default_factory=dict, description="Required input slot names mapped to their types (e.g. {'model': 'MODEL', 'clip': 'CLIP'})")
    optional: Dict[str, str] = Field(default_factory=dict, description="Optional input slot names mapped to their types")

class InputMeta(BaseModel):
    type: str
    is_slot: bool = False
    default: Optional[Any] = None
    min: Optional[Union[int, float]] = None
    max: Optional[Union[int, float]] = None
    step: Optional[Union[int, float]] = None
    options: Optional[List[str]] = None
    multiline: bool = False

class NodeSchema(BaseModel):
    id: str = Field(..., description="Node class type identifier, e.g. KSampler")
    name: str = Field(..., description="Node name")
    display_name: str = Field(..., description="Human-friendly node title")
    category: str = Field(default="custom", description="Node category in ComfyUI")
    description: str = Field(default="", description="Description of the node purpose")
    output_node: bool = Field(default=False, description="Whether node is an output node like SaveImage")
    input_types: NodeInputTypes = Field(default_factory=NodeInputTypes)
    output_types: List[str] = Field(default_factory=list, description="List of output slot types, e.g. ['LATENT'] or ['IMAGE']")
    output_names: List[str] = Field(default_factory=list, description="List of output slot names")
    inputs_meta: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Widget metadata and options")

class PipelineConnection(BaseModel):
    from_node: str = Field(..., description="Source step ID, e.g. step_1")
    output_name: str = Field(..., description="Source output slot name, e.g. MODEL")
    output_type: str = Field(..., description="Source output slot type, e.g. MODEL")
    to_node: str = Field(..., description="Target step ID, e.g. step_5")
    input_name: str = Field(..., description="Target input slot name, e.g. model")
    input_type: str = Field(..., description="Target input slot type, e.g. MODEL")

class PipelineStep(BaseModel):
    id: str = Field(..., description="Unique step ID, e.g. step_1")
    node_type: str = Field(..., description="ComfyUI node class name, e.g. CheckpointLoaderSimple")
    title: Optional[str] = None
    category: Optional[str] = None
    role: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict, description="Configured widget parameters")

class PipelineWorkflow(BaseModel):
    id: Optional[str] = "custom_pipeline"
    name: str = "Custom Pipeline"
    description: Optional[str] = ""
    icon: Optional[str] = "🧩"
    category: Optional[str] = "custom"
    steps: List[PipelineStep] = Field(default_factory=list)
    connections: List[PipelineConnection] = Field(default_factory=list)

class SlotValidationStatus(BaseModel):
    valid: bool
    status: str  # "connected", "unbound_optional", "missing_required", "type_mismatch"
    message: str
    connected_from: Optional[str] = None

class ValidationResult(BaseModel):
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    slot_statuses: Dict[str, Dict[str, SlotValidationStatus]] = Field(default_factory=dict)


# --- Type Compatibility Logic ---

def are_types_compatible(out_type: str, in_type: str) -> bool:
    """
    Checks whether an output slot type can connect to an input slot type in ComfyUI.
    Handles exact matches, case-insensitivity, and wildcard matching.
    """
    if not out_type or not in_type:
        return False
    o = str(out_type).strip().upper()
    i = str(in_type).strip().upper()

    if o == "*" or i == "*":
        return True
    if o == i:
        return True

    # ComfyUI specifics: MASK can sometimes connect to IMAGE or LATENT channel masks
    if (o == "MASK" and i == "IMAGE") or (o == "IMAGE" and i == "MASK"):
        return True

    # Stack & Advanced Model Compatibility
    if (o == "CONTROL_NET" and "CONTROL_NET" in i) or ("CONTROL_NET" in o and i == "CONTROL_NET"):
        return True
    if (o == "LORA_STACK" and "MODEL" in i) or ("MODEL" in o and i == "LORA_STACK"):
        return True
    if (o == "IPADAPTER" and "MODEL" in i) or ("MODEL" in o and i == "IPADAPTER"):
        return True

    return False


def get_compatible_nodes_for_output(out_type: str, all_nodes: Dict[str, NodeSchema]) -> List[NodeSchema]:
    """
    Returns all nodes that accept out_type as an input slot.
    """
    compatible = []
    for node in all_nodes.values():
        all_inputs = {**node.input_types.required, **node.input_types.optional}
        for _, in_type in all_inputs.items():
            if are_types_compatible(out_type, in_type):
                compatible.append(node)
                break
    return compatible


# --- Object Info Parser ---

def parse_comfy_object_info(raw_info: Dict[str, Any]) -> Dict[str, NodeSchema]:
    """
    Parses ComfyUI's raw /object_info dictionary into a clean map of NodeSchema.
    """
    parsed: Dict[str, NodeSchema] = {}
    if not isinstance(raw_info, dict):
        return parsed

    for node_name, data in raw_info.items():
        if not isinstance(data, dict):
            continue

        display_name = data.get("display_name") or node_name
        category = data.get("category") or "custom"
        description = data.get("description") or ""
        output_node = bool(data.get("output_node", False))

        raw_outputs = data.get("output") or []
        output_names = data.get("output_name") or raw_outputs

        output_types = [str(t).upper() for t in raw_outputs]

        input_data = data.get("input", {})
        req_raw = input_data.get("required", {})
        opt_raw = input_data.get("optional", {})

        req_types: Dict[str, str] = {}
        opt_types: Dict[str, str] = {}
        inputs_meta: Dict[str, Dict[str, Any]] = {}

        for group_name, group_dict, target_types in [
            ("required", req_raw, req_types),
            ("optional", opt_raw, opt_types)
        ]:
            if not isinstance(group_dict, dict):
                continue
            for inp_name, spec in group_dict.items():
                if not isinstance(spec, (list, tuple)) or len(spec) == 0:
                    continue

                first_val = spec[0]
                meta: Dict[str, Any] = {"required": (group_name == "required")}

                if isinstance(first_val, list):
                    # Dropdown / Combo widget
                    in_type = "COMBO"
                    meta["type"] = "COMBO"
                    meta["is_slot"] = False
                    meta["options"] = [str(x) for x in first_val]
                    if len(spec) > 1 and isinstance(spec[1], dict):
                        meta.update(spec[1])
                elif isinstance(first_val, str):
                    type_str = first_val.upper()
                    if type_str in KNOWN_CONNECTION_TYPES:
                        in_type = type_str
                        meta["type"] = type_str
                        meta["is_slot"] = True
                    elif type_str in PRIMITIVE_TYPES:
                        in_type = type_str
                        meta["type"] = type_str
                        meta["is_slot"] = False
                        if len(spec) > 1 and isinstance(spec[1], dict):
                            meta.update(spec[1])
                    else:
                        # Could be custom connection type (e.g. IPADAPTER, CONTROL_NET, etc.)
                        in_type = type_str
                        meta["type"] = type_str
                        meta["is_slot"] = True
                else:
                    in_type = "UNKNOWN"
                    meta["type"] = "UNKNOWN"

                target_types[inp_name] = in_type
                inputs_meta[inp_name] = meta

        parsed[node_name] = NodeSchema(
            id=node_name,
            name=node_name,
            display_name=display_name,
            category=category,
            description=description,
            output_node=output_node,
            input_types=NodeInputTypes(required=req_types, optional=opt_types),
            output_types=output_types,
            output_names=[str(n) for n in output_names],
            inputs_meta=inputs_meta
        )

    return parsed


# --- Fallback Node Catalog ---

FALLBACK_OBJECT_INFO: Dict[str, Any] = {
    "CheckpointLoaderSimple": {
        "input": {
            "required": {
                "ckpt_name": [["DreamShaper8_LCM.safetensors", "v1-5-pruned-emaonly.safetensors", "sd_xl_base_1.0.safetensors"]]
            }
        },
        "output": ["MODEL", "CLIP", "VAE"],
        "output_name": ["MODEL", "CLIP", "VAE"],
        "display_name": "Load Checkpoint",
        "category": "loaders",
        "description": "Loads a Stable Diffusion / SDXL checkpoint model."
    },
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": ["INT", {"default": 512, "min": 64, "max": 8192, "step": 8}],
                "height": ["INT", {"default": 512, "min": 64, "max": 8192, "step": 8}],
                "batch_size": ["INT", {"default": 1, "min": 1, "max": 64}]
            }
        },
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "display_name": "Empty Latent Image",
        "category": "latent",
        "description": "Creates an empty latent tensor for txt2img or video generation."
    },
    "CLIPTextEncode": {
        "input": {
            "required": {
                "text": ["STRING", {"multiline": True, "default": ""}],
                "clip": ["CLIP"]
            }
        },
        "output": ["CONDITIONING"],
        "output_name": ["CONDITIONING"],
        "display_name": "CLIP Text Encode (Prompt)",
        "category": "conditioning",
        "description": "Encodes a text prompt into conditioning vectors using CLIP."
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "seed": ["INT", {"default": 0, "min": 0, "max": 18446744073709551615}],
                "steps": ["INT", {"default": 14, "min": 1, "max": 1000}],
                "cfg": ["FLOAT", {"default": 6.5, "min": 0.0, "max": 100.0, "step": 0.1}],
                "sampler_name": [["euler", "euler_ancestral", "heun", "dpm_2", "dpmpp_2m", "dpmpp_sde", "lcm", "ddim", "uni_pc"]],
                "scheduler": [["normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform"]],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}]
            }
        },
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "display_name": "KSampler",
        "category": "sampling",
        "description": "Core diffusion sampling engine."
    },
    "VAEDecode": {
        "input": {
            "required": {
                "samples": ["LATENT"],
                "vae": ["VAE"]
            }
        },
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "display_name": "VAE Decode",
        "category": "latent",
        "description": "Decodes latent images to RGB pixel images."
    },
    "VAEEncode": {
        "input": {
            "required": {
                "pixels": ["IMAGE"],
                "vae": ["VAE"]
            }
        },
        "output": ["LATENT"],
        "output_name": ["LATENT"],
        "display_name": "VAE Encode",
        "category": "latent",
        "description": "Encodes RGB pixel images into latent space for img2img."
    },
    "SaveImage": {
        "input": {
            "required": {
                "images": ["IMAGE"],
                "filename_prefix": ["STRING", {"default": "ComfyUI"}]
            }
        },
        "output": [],
        "output_name": [],
        "output_node": True,
        "display_name": "Save Image",
        "category": "image",
        "description": "Saves rendered images to disk."
    },
    "LoadImage": {
        "input": {
            "required": {
                "image": [["input.png", "reference.png", "pose.png"]]
            }
        },
        "output": ["IMAGE", "MASK"],
        "output_name": ["IMAGE", "MASK"],
        "display_name": "Load Image",
        "category": "image",
        "description": "Loads an input image for image-to-image or ControlNet."
    },
    "LoraLoader": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "clip": ["CLIP"],
                "lora_name": [["none"]],
                "strength_model": ["FLOAT", {"default": 1.0, "min": -20.0, "max": 20.0, "step": 0.05}],
                "strength_clip": ["FLOAT", {"default": 1.0, "min": -20.0, "max": 20.0, "step": 0.05}]
            }
        },
        "output": ["MODEL", "CLIP"],
        "output_name": ["MODEL", "CLIP"],
        "display_name": "Load LoRA",
        "category": "loaders",
        "description": "Applies a LoRA to model and clip."
    },
    "ControlNetLoader": {
        "input": {
            "required": {
                "control_net_name": [["control_v11p_sd15_canny.pth", "control_v11f1p_sd15_depth.pth", "control_v11p_sd15_openpose.pth"]]
            }
        },
        "output": ["CONTROL_NET"],
        "output_name": ["CONTROL_NET"],
        "display_name": "Load ControlNet Model",
        "category": "loaders",
        "description": "Loads a ControlNet weights model."
    },
    "ApplyControlNet": {
        "input": {
            "required": {
                "conditioning": ["CONDITIONING"],
                "control_net": ["CONTROL_NET"],
                "image": ["IMAGE"],
                "strength": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}]
            }
        },
        "output": ["CONDITIONING"],
        "output_name": ["CONDITIONING"],
        "display_name": "Apply ControlNet",
        "category": "conditioning",
        "description": "Applies ControlNet guidance to text conditioning."
    },
    "Canny": {
        "input": {
            "required": {
                "image": ["IMAGE"],
                "low_threshold": ["FLOAT", {"default": 100.0, "min": 0.0, "max": 255.0, "step": 1.0}],
                "high_threshold": ["FLOAT", {"default": 200.0, "min": 0.0, "max": 255.0, "step": 1.0}]
            }
        },
        "output": ["IMAGE"],
        "output_name": ["IMAGE"],
        "display_name": "Canny Edge Detector",
        "category": "image/preprocessors",
        "description": "Extracts edge outlines from an input image."
    },
    "AnimateDiffLoaderWithContext": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "model_name": [["mm_sd_v15_v2.ckpt", "v3_sd15_mm.ckpt"]],
                "beta_schedule": [["sqrt_linear (AnimateDiff)", "linear (AnimateDiff)", "autoselect"]]
            }
        },
        "output": ["MODEL"],
        "output_name": ["MODEL"],
        "display_name": "AnimateDiff Loader",
        "category": "AnimateDiff",
        "description": "Injects AnimateDiff motion model into the diffusion UNet."
    },
    "AnimateDiffCombine": {
        "input": {
            "required": {
                "images": ["IMAGE"],
                "frame_rate": ["INT", {"default": 8, "min": 1, "max": 60}],
                "loop_count": ["INT", {"default": 0, "min": 0, "max": 100}],
                "filename_prefix": ["STRING", {"default": "OuterHeaven_Motion"}],
                "format": [["image/gif", "image/webp", "video/h264-mp4"]],
                "pingpong": ["BOOLEAN", {"default": False}]
            }
        },
        "output": [],
        "output_name": [],
        "output_node": True,
        "display_name": "AnimateDiff Combine",
        "category": "AnimateDiff",
        "description": "Combines generated frames into an animated GIF or video."
    }
}


# --- Validation Engine ---

def validate_pipeline(workflow: PipelineWorkflow, node_schemas: Dict[str, NodeSchema]) -> ValidationResult:
    """
    Validates a complete pipeline against ComfyUI schema rules:
    - Verifies existence of all referenced nodes
    - Checks that connection types match (e.g. green: LATENT->LATENT, red: IMAGE->MODEL)
    - Checks for missing required slot connections
    """
    errors: List[str] = []
    warnings: List[str] = []
    slot_statuses: Dict[str, Dict[str, SlotValidationStatus]] = {}

    step_map: Dict[str, PipelineStep] = {s.id: s for s in workflow.steps}

    # Step existence & schema check
    for step in workflow.steps:
        slot_statuses[step.id] = {}
        if step.node_type not in node_schemas:
            warnings.append(f"Step '{step.id}': Unbekannter Node-Typ '{step.node_type}'. Schema-Validierung für diesen Node eingeschränkt.")

    # Validate each connection
    incoming_connections: Dict[Tuple[str, str], PipelineConnection] = {}

    for conn in workflow.connections:
        from_step = step_map.get(conn.from_node)
        to_step = step_map.get(conn.to_node)

        if not from_step:
            errors.append(f"Verbindung ungültig: Quell-Step '{conn.from_node}' existiert nicht.")
            continue
        if not to_step:
            errors.append(f"Verbindung ungültig: Ziel-Step '{conn.to_node}' existiert nicht.")
            continue

        from_schema = node_schemas.get(from_step.node_type)
        to_schema = node_schemas.get(to_step.node_type)

        out_type = conn.output_type
        in_type = conn.input_type

        # Verify against schema if available
        if from_schema and from_schema.output_types:
            if out_type not in from_schema.output_types:
                # Fallback to check by output name
                pass

        if to_schema:
            all_in_types = {**to_schema.input_types.required, **to_schema.input_types.optional}
            if conn.input_name in all_in_types:
                expected_in = all_in_types[conn.input_name]
                if is_connection_slot_type(expected_in):
                    in_type = expected_in

        # Check type compatibility
        if not are_types_compatible(out_type, in_type):
            err_msg = (
                f"Typ-Mismatch: Verbindung von '{from_step.title or from_step.id}' "
                f"({out_type}) zu '{to_step.title or to_step.id}.{conn.input_name}' ({in_type}) ist inkompatibel!"
            )
            errors.append(err_msg)
            if conn.to_node in slot_statuses:
                slot_statuses[conn.to_node][conn.input_name] = SlotValidationStatus(
                    valid=False,
                    status="type_mismatch",
                    message=f"Inkompatibler Typ {out_type} -> {in_type}",
                    connected_from=conn.from_node
                )
        else:
            if conn.to_node in slot_statuses:
                slot_statuses[conn.to_node][conn.input_name] = SlotValidationStatus(
                    valid=True,
                    status="connected",
                    message=f"Verbunden mit {conn.from_node} ({out_type})",
                    connected_from=conn.from_node
                )

        incoming_connections[(conn.to_node, conn.input_name)] = conn

    # Check for missing required connection slots
    for step in workflow.steps:
        schema = node_schemas.get(step.node_type)
        if not schema:
            continue

        for req_slot, req_type in schema.input_types.required.items():
            if is_connection_slot_type(req_type):
                if (step.id, req_slot) not in incoming_connections:
                    err_msg = f"Fehlender Pflicht-Slot: '{step.title or step.id}' benötigt Eingang '{req_slot}' ({req_type})."
                    errors.append(err_msg)
                    slot_statuses[step.id][req_slot] = SlotValidationStatus(
                        valid=False,
                        status="missing_required",
                        message=f"Pflicht-Slot '{req_slot}' ({req_type}) nicht verbunden!"
                    )

    is_valid = len(errors) == 0
    return ValidationResult(
        valid=is_valid,
        errors=errors,
        warnings=warnings,
        slot_statuses=slot_statuses
    )


# --- ComfyUI Prompt Exporter ---

def convert_pipeline_to_comfy_prompt(
    workflow: PipelineWorkflow,
    node_schemas: Optional[Dict[str, NodeSchema]] = None
) -> Dict[str, Any]:
    """
    Converts a sequential / visual PipelineWorkflow into native ComfyUI prompt JSON format.
    Format:
    {
      "prompt": {
        "1": {
          "class_type": "CheckpointLoaderSimple",
          "inputs": {"ckpt_name": "..."}
        },
        "2": {
          "class_type": "KSampler",
          "inputs": {
            "model": ["1", 0],
            ...
          }
        }
      }
    }
    """
    prompt_dict: Dict[str, Any] = {}

    # Map step IDs to numerical node IDs
    step_id_to_num: Dict[str, str] = {}
    for idx, step in enumerate(workflow.steps, start=1):
        step_id_to_num[step.id] = str(idx)

    # Build prompt nodes
    for step in workflow.steps:
        num_id = step_id_to_num[step.id]
        schema = node_schemas.get(step.node_type) if node_schemas else None

        inputs: Dict[str, Any] = {}

        # 1. Copy configured widget parameters
        for param_k, param_v in step.params.items():
            inputs[param_k] = param_v

        # 2. Wire connections
        for conn in workflow.connections:
            if conn.to_node == step.id:
                source_step = next((s for s in workflow.steps if s.id == conn.from_node), None)
                if not source_step:
                    continue
                source_num = step_id_to_num.get(conn.from_node)
                if not source_num:
                    continue

                source_schema = node_schemas.get(source_step.node_type) if node_schemas else None
                out_idx = 0
                if source_schema and conn.output_name in source_schema.output_names:
                    out_idx = source_schema.output_names.index(conn.output_name)
                elif source_schema and conn.output_type in source_schema.output_types:
                    out_idx = source_schema.output_types.index(conn.output_type)

                inputs[conn.input_name] = [source_num, out_idx]

        prompt_dict[num_id] = {
            "class_type": step.node_type,
            "inputs": inputs
        }

    return {"prompt": prompt_dict}


# --- Schema Manager & Archetype Loader ---

class ComfySchemaManager:
    """
    Manages loading, caching, and querying of ComfyUI node schemas and archetype workflows.
    """
    def __init__(self, cache_file: Path, archetypes_dirs: List[Path]):
        self.cache_file = cache_file
        self.archetypes_dirs = archetypes_dirs
        self.nodes: Dict[str, NodeSchema] = {}
        self.raw_object_info: Dict[str, Any] = {}
        self._load_initial_schema()

    def _load_initial_schema(self):
        """Load from disk cache if exists, otherwise load fallback catalog."""
        if self.cache_file.is_file():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                self.raw_object_info = data
                self.nodes = parse_comfy_object_info(data)
                logger.info(f"Loaded {len(self.nodes)} nodes from schema cache {self.cache_file}")
                return
            except Exception as e:
                logger.warning(f"Could not read schema cache {self.cache_file}: {e}")

        # Fallback catalog
        self.raw_object_info = FALLBACK_OBJECT_INFO
        self.nodes = parse_comfy_object_info(FALLBACK_OBJECT_INFO)
        logger.info(f"Loaded {len(self.nodes)} built-in fallback nodes")

    def update_schema(self, raw_object_info: Dict[str, Any]):
        """Update schema from live ComfyUI /object_info and persist to cache."""
        if not isinstance(raw_object_info, dict) or not raw_object_info:
            return
        self.raw_object_info = raw_object_info
        self.nodes = parse_comfy_object_info(raw_object_info)
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(raw_object_info, indent=2), encoding="utf-8")
            logger.info(f"Successfully cached {len(self.nodes)} live ComfyUI nodes to {self.cache_file}")
        except Exception as e:
            logger.warning(f"Failed to persist schema cache to {self.cache_file}: {e}")

    def get_nodes_payload(self) -> Dict[str, Any]:
        """Formats the GET /api/comfy/nodes response payload."""
        result = {}
        for nid, schema in self.nodes.items():
            result[nid] = {
                "id": schema.id,
                "name": schema.name,
                "display_name": schema.display_name,
                "category": schema.category,
                "description": schema.description,
                "output_node": schema.output_node,
                "input_types": {
                    "required": schema.input_types.required,
                    "optional": schema.input_types.optional
                },
                "output_types": schema.output_types,
                "output_names": schema.output_names,
                "inputs_meta": schema.inputs_meta
            }
        return {
            "count": len(result),
            "nodes": result
        }

    def get_archetypes(self) -> List[Dict[str, Any]]:
        """Loads and returns all archetype workflow JSON files."""
        archetypes = []
        seen_ids = set()
        for d in self.archetypes_dirs:
            if d.is_dir():
                for f in sorted(d.glob("*.json")):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        aid = data.get("id") or f.stem
                        if aid not in seen_ids:
                            seen_ids.add(aid)
                            archetypes.append(data)
                    except Exception as e:
                        logger.warning(f"Error loading archetype {f}: {e}")
        return archetypes

    def get_archetype_by_id(self, archetype_id: str) -> Optional[Dict[str, Any]]:
        for arch in self.get_archetypes():
            if arch.get("id") == archetype_id:
                return arch
        return None


# --- Optional FastAPI APIRouter Definition ---
try:
    from fastapi import APIRouter, HTTPException, Query
    comfy_router = APIRouter(prefix="/api/comfy", tags=["ComfyUI Pipeline Builder"])

    @comfy_router.get("/nodes")
    def api_get_nodes():
        """FastAPI endpoint to retrieve all registered ComfyUI nodes with typed schemas."""
        # Note: Managed instance will be bound in production
        return {"status": "ok"}
except ImportError:
    comfy_router = None

