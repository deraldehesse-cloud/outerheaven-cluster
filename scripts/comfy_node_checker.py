#!/usr/bin/env python3
"""
comfy_node_checker.py - Autonomous ComfyUI Custom Node Verifier & Installer
Outer Heaven Cluster Infrastructure

Features:
1. Validates required nodes of a workflow against live http://127.0.0.1:8188/object_info
2. Storage Safeguard: Ensures >= 15 GB free space on /mnt/storage-beta before any download.
3. Automated Git Clone & Dependency Installation into /mnt/storage-beta/comfyui/custom_nodes/
4. Fallback & Alert: Logs missing manual nodes to .shared_brain/tools/comfyui.md
"""

import os
import sys
import json
import shutil
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
CENTRAL_NODES_DIR = Path("/mnt/storage-beta/comfyui/custom_nodes")
SHARED_BRAIN_COMFY = Path("/mnt/storage-beta/projekte/.shared_brain/tools/comfyui.md")
COMFY_VENV_PYTHON = Path("/home/outerheaven/ComfyUI/venv/bin/python")
MIN_FREE_DISK_GB = 15.0

# Canonical repository catalog for automated node provisioning
KNOWN_NODE_REPOS: Dict[str, str] = {
    "comfyui_controlnet_aux": "https://github.com/Fannovel16/comfyui_controlnet_aux.git",
    "ComfyUI-Manager": "https://github.com/ltdrdata/ComfyUI-Manager.git",
    "ComfyUI_IPAdapter_plus": "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git",
    "ComfyUI-Impact-Pack": "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git",
    "ComfyUI_UltimateSDUpscale": "https://github.com/ssitu/ComfyUI_UltimateSDUpscale.git",
}

# Mapping common node class types to repository identifiers
CLASS_TO_REPO_MAP: Dict[str, str] = {
    # ControlNet Preprocessors
    "DWPreprocessor": "comfyui_controlnet_aux",
    "OpenposePreprocessor": "comfyui_controlnet_aux",
    "CannyEdgePreprocessor": "comfyui_controlnet_aux",
    "M-LSDDetector": "comfyui_controlnet_aux",
    "LineArtPreprocessor": "comfyui_controlnet_aux",
    "DepthAnythingPreprocessor": "comfyui_controlnet_aux",
    "MiDaS-DepthMapPreprocessor": "comfyui_controlnet_aux",
    "Zoe-DepthMapPreprocessor": "comfyui_controlnet_aux",
    "AnimeFace_SemSegPreprocessor": "comfyui_controlnet_aux",
    # IPAdapter
    "IPAdapter": "ComfyUI_IPAdapter_plus",
    "IPAdapterModelLoader": "ComfyUI_IPAdapter_plus",
    # Upscale
    "UltimateSDUpscale": "ComfyUI_UltimateSDUpscale",
}


def check_storage_space(target_path: Path = Path("/mnt/storage-beta")) -> Tuple[bool, float]:
    """Ensures at least 15 GB free disk space exists before downloading any models or nodes."""
    try:
        usage = shutil.disk_usage(str(target_path))
        free_gb = usage.free / (1024 ** 3)
        return (free_gb >= MIN_FREE_DISK_GB, round(free_gb, 2))
    except Exception as e:
        print(f"[Warnung] Disk-Check fehlgeschlagen: {e}")
        return (False, 0.0)


def get_available_nodes(comfy_url: str = COMFYUI_URL) -> Set[str]:
    """Fetches list of registered node class names from ComfyUI /object_info."""
    try:
        req = urllib.request.Request(f"{comfy_url}/object_info", headers={"User-Agent": "ComfyNodeChecker/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return set(data.keys())
    except Exception as e:
        print(f"[Warnung] Konnte ComfyUI /object_info nicht abrufen: {e}")
        return set()


def extract_workflow_nodes(workflow_data: Any) -> Set[str]:
    """Parses prompt JSON or workflow graph to collect all required node class names."""
    required = set()
    if isinstance(workflow_data, dict):
        # Format A: ComfyUI API Prompt format { "1": { "class_type": "KSampler", ... } }
        for _, node_info in workflow_data.items():
            if isinstance(node_info, dict) and "class_type" in node_info:
                required.add(node_info["class_type"])

        # Format B: Web UI Workflow format { "nodes": [ { "type": "KSampler" } ] }
        if "nodes" in workflow_data and isinstance(workflow_data["nodes"], list):
            for n in workflow_data["nodes"]:
                if isinstance(n, dict):
                    ntype = n.get("type") or n.get("class_type")
                    if ntype:
                        required.add(ntype)

    return required


def record_missing_node_alert(node_name: str, reason: str = "Manuelle Installation erforderlich"):
    """Appends unresolvable nodes into .shared_brain/tools/comfyui.md under Fehlende / Manuelle Nodes."""
    print(f"[ALERT] Workflow benötigt Node [{node_name}], bitte manuell prüfen. Grund: {reason}")
    if SHARED_BRAIN_COMFY.is_file():
        try:
            content = SHARED_BRAIN_COMFY.read_text(encoding="utf-8", errors="replace")
            marker = "## Fehlende / Manuelle Nodes"
            entry = f"- `{node_name}`: {reason} (Erfasst am {os.popen('date +%Y-%m-%d').read().strip()})"
            if entry not in content:
                if marker in content:
                    parts = content.split(marker, 1)
                    new_content = parts[0] + marker + "\n" + entry + "\n" + parts[1].lstrip()
                    SHARED_BRAIN_COMFY.write_text(new_content, encoding="utf-8")
                else:
                    new_content = content.rstrip() + f"\n\n{marker}\n{entry}\n"
                    SHARED_BRAIN_COMFY.write_text(new_content, encoding="utf-8")
                print(f"✔ Notiert in {SHARED_BRAIN_COMFY.name}")
        except Exception as e:
            print(f"[Warnung] Konnte Alert nicht in shared_brain eintragen: {e}")


def install_custom_node(repo_key: str) -> bool:
    """Clones repo into central storage and installs requirements into ComfyUI venv."""
    repo_url = KNOWN_NODE_REPOS.get(repo_key)
    if not repo_url:
        return False

    has_space, free_gb = check_storage_space()
    if not has_space:
        print(f"[ABBRUCH] Speicher-Disziplin: Nur {free_gb} GB frei auf /mnt/storage-beta/ (Minimum: {MIN_FREE_DISK_GB} GB)!")
        return False

    CENTRAL_NODES_DIR.mkdir(parents=True, exist_ok=True)
    target_dir = CENTRAL_NODES_DIR / repo_key

    if not target_dir.exists():
        print(f"[*] Klone {repo_key} nach {target_dir}...")
        res = subprocess.run(["git", "clone", repo_url, str(target_dir)], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[Fehler] Git-Clone fehlgeschlagen: {res.stderr}")
            return False

    # Pip requirements installation
    req_file = target_dir / "requirements.txt"
    if req_file.is_file() and COMFY_VENV_PYTHON.is_file():
        print(f"[*] Installiere Abhängigkeiten aus {req_file}...")
        pip_res = subprocess.run([str(COMFY_VENV_PYTHON), "-m", "pip", "install", "-r", str(req_file)], capture_output=True, text=True)
        if pip_res.returncode != 0:
            print(f"[Warnung] Pip-Installation mit Warnungen beendet: {pip_res.stderr[:200]}")

    return True


def verify_and_provision_workflow(workflow_data: Any) -> Tuple[bool, List[str]]:
    """
    Validates all nodes in a workflow. Auto-provisions installable custom nodes.
    Returns (True, []) if all nodes available or installed; (False, missing_nodes) if any remain unsatisfied.
    """
    required_nodes = extract_workflow_nodes(workflow_data)
    available_nodes = get_available_nodes()

    missing = [n for n in required_nodes if n not in available_nodes]
    if not missing:
        return (True, [])

    print(f"[*] {len(missing)} fehlende Node(s) im Workflow erkannt: {missing}")
    still_missing = []

    for node in missing:
        repo_key = CLASS_TO_REPO_MAP.get(node)
        if repo_key:
            print(f"[*] Versuche automatische Bereitstellung für Node '{node}' via Repo '{repo_key}'...")
            success = install_custom_node(repo_key)
            if not success:
                still_missing.append(node)
                record_missing_node_alert(node, "Auto-Download oder Speicherprüfung fehlgeschlagen")
        else:
            still_missing.append(node)
            record_missing_node_alert(node, "Kein automatisches Mapping vorhanden - externe C-Dependencies möglich")

    if still_missing:
        return (False, still_missing)

    return (True, [])


if __name__ == "__main__":
    has_space, free_gb = check_storage_space()
    print(f"[*] Storage-Status /mnt/storage-beta: {free_gb} GB frei (Safe: {has_space})")
    nodes = get_available_nodes()
    print(f"[*] ComfyUI Nodes aktiv: {len(nodes)} Node-Typen registriert.")
    if len(sys.argv) > 1:
        wf_path = Path(sys.argv[1])
        if wf_path.is_file():
            wf_data = json.loads(wf_path.read_text(encoding="utf-8"))
            ok, miss = verify_and_provision_workflow(wf_data)
            print(f"Resultat: OK={ok}, Fehlend={miss}")
