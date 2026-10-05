"""
comfy_custom_nodes.py - Management & Inspection of ComfyUI Custom Nodes
Outer Heaven Cluster Infrastructure & Megan Studio
"""

import os
import sys
import ast
import re
import json
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("custom-nodes-mgr")

def get_custom_nodes_dir() -> Path:
    """Finds the ComfyUI custom_nodes directory on Outer Heaven or local development."""
    candidates = [
        Path("/mnt/storage-beta/comfyui/custom_nodes"),
        Path("/home/outerheaven/ComfyUI/custom_nodes"),
        Path("/mnt/storage-beta/ComfyUI/custom_nodes"),
        Path(__file__).resolve().parent / "custom_nodes",
        Path(__file__).resolve().parent.parent / "custom_nodes",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    # Fallback default
    return Path("/mnt/storage-beta/comfyui/custom_nodes")


def _get_git_info(node_dir: Path) -> Dict[str, Any]:
    """Extracts git commit hash, branch, and last commit message if available."""
    git_dir = node_dir / ".git"
    if not git_dir.exists() and not (node_dir.parent / ".git").exists():
        return {"has_git": False, "commit": None, "branch": None, "message": None, "remote": None}

    try:
        # Commit hash (short)
        res_commit = subprocess.run(
            ["git", "-C", str(node_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3
        )
        commit = res_commit.stdout.strip() if res_commit.returncode == 0 else None

        # Branch name
        res_branch = subprocess.run(
            ["git", "-C", str(node_dir), "branch", "--show-current"],
            capture_output=True, text=True, timeout=3
        )
        branch = res_branch.stdout.strip() if res_branch.returncode == 0 else None

        # Commit message
        res_msg = subprocess.run(
            ["git", "-C", str(node_dir), "log", "-1", "--pretty=format:%s (%ci)"],
            capture_output=True, text=True, timeout=3
        )
        msg = res_msg.stdout.strip() if res_msg.returncode == 0 else None

        # Remote URL
        res_remote = subprocess.run(
            ["git", "-C", str(node_dir), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=3
        )
        remote = res_remote.stdout.strip() if res_remote.returncode == 0 else None

        return {
            "has_git": bool(commit),
            "commit": commit,
            "branch": branch or "HEAD",
            "message": msg,
            "remote": remote
        }
    except Exception as e:
        logger.debug(f"Git extraction failed for {node_dir.name}: {e}")
        return {"has_git": False, "commit": None, "branch": None, "message": None, "remote": None}


def _extract_node_mappings(target_py_file: Path) -> Tuple[List[str], Dict[str, str]]:
    """
    Parses a python file (__init__.py or single node file) using AST and regex
    to extract NODE_CLASS_MAPPINGS and NODE_DISPLAY_NAME_MAPPINGS.
    """
    if not target_py_file.is_file():
        return [], {}

    try:
        content = target_py_file.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning(f"Failed to read {target_py_file}: {e}")
        return [], {}

    mappings: List[str] = []
    display_names: Dict[str, str] = {}

    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            # 1. Assignments (NODE_CLASS_MAPPINGS = {...} or NODE_CLASS_MAPPINGS['Key'] = ...)
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    # Dict literal assignment
                    if isinstance(target, ast.Name):
                        if target.id == "NODE_CLASS_MAPPINGS" and isinstance(node.value, ast.Dict):
                            for k in node.value.keys:
                                if isinstance(k, ast.Constant) and k.value is not None:
                                    mappings.append(str(k.value))
                        elif target.id == "NODE_DISPLAY_NAME_MAPPINGS" and isinstance(node.value, ast.Dict):
                            for k, v in zip(node.value.keys, node.value.values):
                                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                                    if k.value is not None and v.value is not None:
                                        display_names[str(k.value)] = str(v.value)
                    # Subscript assignment: NODE_CLASS_MAPPINGS['Key'] = Value
                    elif isinstance(target, ast.Subscript):
                        if isinstance(target.value, ast.Name) and target.value.id == "NODE_CLASS_MAPPINGS":
                            if isinstance(target.slice, ast.Constant) and target.slice.value is not None:
                                mappings.append(str(target.slice.value))
                            elif hasattr(target.slice, "value") and getattr(target.slice, "value", None) is not None:
                                mappings.append(str(getattr(target.slice, "value")))

            # 2. Call expression: NODE_CLASS_MAPPINGS.update({...})
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Attribute) and func.attr == "update":
                    if isinstance(func.value, ast.Name) and func.value.id == "NODE_CLASS_MAPPINGS":
                        for arg in node.value.args:
                            if isinstance(arg, ast.Dict):
                                for k in arg.keys:
                                    if isinstance(k, ast.Constant) and k.value is not None:
                                        mappings.append(str(k.value))
    except Exception as e:
        logger.debug(f"AST parse failed on {target_py_file.name}, using regex fallback: {e}")

    # 3. Regex Fallback if AST missed items or dynamic dict definitions
    if not mappings:
        # Matches: NODE_CLASS_MAPPINGS["Key"] = ...
        matches_sub = re.findall(r'NODE_CLASS_MAPPINGS\s*\[\s*[\'"]([^\'"]+)[\'"]\s*\]', content)
        mappings.extend(matches_sub)
        # Matches: NODE_CLASS_MAPPINGS = { "Key": ... }
        dict_match = re.search(r'NODE_CLASS_MAPPINGS\s*=\s*\{([^}]+)\}', content, re.DOTALL)
        if dict_match:
            keys = re.findall(r'[\'"]([^\'"]+)[\'"]\s*:', dict_match.group(1))
            mappings.extend(keys)

    # Clean duplicates while preserving order
    clean_mappings = sorted(list(dict.fromkeys(mappings)))
    return clean_mappings, display_names


def _find_readme(node_dir: Path) -> Optional[str]:
    """Finds and reads README.md or fallback documentation file."""
    if not node_dir.is_dir():
        return None
    names = ["README.md", "readme.md", "README.txt", "readme.txt", "README", "readme"]
    for n in names:
        f = node_dir / n
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
    return None


def list_custom_nodes() -> List[Dict[str, Any]]:
    """Returns a list of all installed custom node packages and standalone modules."""
    nodes_dir = get_custom_nodes_dir()
    if not nodes_dir.exists():
        return []

    results = []
    ignored_prefixes = (".", "__pycache__", ".git", ".venv", ".pytest")
    ignored_names = ("example_node.py.example",)

    for item in sorted(nodes_dir.iterdir()):
        name = item.name
        if any(name.startswith(p) for p in ignored_prefixes) or name in ignored_names:
            continue
        if name.endswith(".zip") or name.endswith(".tar.gz") or name.endswith(".bak"):
            continue

        if item.is_dir():
            init_file = item / "__init__.py"
            classes, _ = _extract_node_mappings(init_file) if init_file.exists() else ([], {})
            git_info = _get_git_info(item)
            readme = _find_readme(item)
            desc_preview = ""
            if readme:
                # Take first non-heading paragraph as preview
                lines = [l.strip() for l in readme.splitlines() if l.strip() and not l.strip().startswith("#")]
                desc_preview = lines[0][:200] if lines else ""

            # Count files in node folder
            file_count = 0
            try:
                file_count = sum(1 for f in item.rglob("*") if f.is_file() and not any(p in f.parts for p in ["__pycache__", ".git"]))
            except Exception:
                pass

            results.append({
                "name": name,
                "folder_name": name,
                "is_directory": True,
                "has_init": init_file.exists(),
                "has_readme": bool(readme),
                "readme_preview": desc_preview,
                "has_git": git_info["has_git"],
                "git_branch": git_info["branch"],
                "git_commit": git_info["commit"],
                "nodes_count": len(classes),
                "node_classes": classes[:10],
                "file_count": file_count
            })

        elif item.is_file() and item.suffix == ".py":
            classes, _ = _extract_node_mappings(item)
            results.append({
                "name": item.stem,
                "folder_name": item.name,
                "is_directory": False,
                "has_init": True,
                "has_readme": False,
                "readme_preview": f"Einzeldatei-Custom-Node: {item.name}",
                "has_git": False,
                "git_branch": None,
                "git_commit": None,
                "nodes_count": len(classes),
                "node_classes": classes,
                "file_count": 1
            })

    return results


def get_custom_node_details(node_name: str) -> Optional[Dict[str, Any]]:
    """Returns detailed metadata, parsed node mappings, README content, and file list."""
    nodes_dir = get_custom_nodes_dir()
    clean_name = re.sub(r'[\/\\\.]+', '', node_name).strip()
    if not clean_name:
        return None

    node_path = nodes_dir / clean_name
    if not node_path.exists():
        # Check if single .py file was requested by stem
        cand_py = nodes_dir / f"{clean_name}.py"
        if cand_py.is_file():
            node_path = cand_py
        else:
            return None

    is_dir = node_path.is_dir()
    if is_dir:
        init_file = node_path / "__init__.py"
        classes, display_names = _extract_node_mappings(init_file) if init_file.exists() else ([], {})
        git_info = _get_git_info(node_path)
        readme = _find_readme(node_path)

        # File list
        files = []
        try:
            for p in sorted(node_path.rglob("*")):
                if p.is_file() and not any(part in p.parts for part in ["__pycache__", ".git", ".venv"]):
                    rel = str(p.relative_to(node_path))
                    files.append({
                        "path": rel,
                        "name": p.name,
                        "size": p.stat().st_size,
                        "modified": int(p.stat().st_mtime),
                        "is_py": p.suffix == ".py",
                        "is_init": p.name == "__init__.py"
                    })
                    if len(files) >= 150:
                        break
        except Exception as e:
            logger.warning(f"Error scanning files in {node_path}: {e}")

        # Sort so __init__.py is first, then python files, then others
        files.sort(key=lambda x: (not x["is_init"], not x["is_py"], x["path"]))

        return {
            "success": True,
            "name": clean_name,
            "folder_name": node_path.name,
            "is_directory": True,
            "abs_path": str(node_path),
            "readme": readme,
            "git": git_info,
            "nodes_count": len(classes),
            "node_classes": classes,
            "node_display_names": display_names,
            "files": files
        }
    else:
        # Single file node
        classes, display_names = _extract_node_mappings(node_path)
        try:
            content = node_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            content = ""

        # Extract module docstring as documentation
        docstring = ""
        try:
            tree = ast.parse(content)
            docstring = ast.get_docstring(tree) or ""
        except Exception:
            pass

        return {
            "success": True,
            "name": clean_name,
            "folder_name": node_path.name,
            "is_directory": False,
            "abs_path": str(node_path),
            "readme": docstring or f"# {clean_name}\n\nEinzeldatei-Node: `{node_path.name}`",
            "git": {"has_git": False, "commit": None, "branch": None, "message": None, "remote": None},
            "nodes_count": len(classes),
            "node_classes": classes,
            "node_display_names": display_names,
            "files": [{
                "path": node_path.name,
                "name": node_path.name,
                "size": node_path.stat().st_size,
                "modified": int(node_path.stat().st_mtime),
                "is_py": True,
                "is_init": True
            }]
        }


def get_custom_node_file(node_name: str, rel_path: Optional[str] = None) -> Tuple[bool, Any]:
    """Reads the source code of a specified file within a custom node."""
    nodes_dir = get_custom_nodes_dir()
    clean_node = re.sub(r'[\/\\\.]+', '', node_name).strip()
    if not clean_node:
        return False, "Ungültiger Node-Name"

    node_path = nodes_dir / clean_node
    if not node_path.exists():
        cand = nodes_dir / f"{clean_node}.py"
        if cand.is_file():
            node_path = cand
        else:
            return False, f"Custom Node '{node_name}' existiert nicht"

    if node_path.is_file():
        target_file = node_path
        rel = node_path.name
    else:
        file_query = (rel_path or "__init__.py").strip().lstrip("/")
        # Path traversal protection
        target_file = (node_path / file_query).resolve()
        if not str(target_file).startswith(str(node_path.resolve())):
            return False, "Pfadüberschreitung (Directory Traversal) nicht gestattet"
        rel = file_query

    if not target_file.is_file():
        return False, f"Datei '{rel}' in Custom Node '{node_name}' nicht gefunden"

    try:
        content = target_file.read_text(encoding="utf-8", errors="replace")
        return True, {
            "success": True,
            "node_name": clean_node,
            "path": rel,
            "filename": target_file.name,
            "size": target_file.stat().st_size,
            "modified": int(target_file.stat().st_mtime),
            "content": content
        }
    except Exception as e:
        return False, f"Fehler beim Lesen der Datei: {e}"


def save_custom_node_file(node_name: str, rel_path: Optional[str], code: str) -> Tuple[bool, Any]:
    """
    Validates Python code via ast.parse, creates a .bak backup,
    and saves the modified file atomically.
    """
    nodes_dir = get_custom_nodes_dir()
    clean_node = re.sub(r'[\/\\\.]+', '', node_name).strip()
    if not clean_node:
        return False, {"error": "Ungültiger Node-Name"}

    node_path = nodes_dir / clean_node
    if not node_path.exists():
        cand = nodes_dir / f"{clean_node}.py"
        if cand.is_file():
            node_path = cand
        else:
            return False, {"error": f"Custom Node '{node_name}' existiert nicht"}

    if node_path.is_file():
        target_file = node_path
        rel = node_path.name
    else:
        file_query = (rel_path or "__init__.py").strip().lstrip("/")
        target_file = (node_path / file_query).resolve()
        if not str(target_file).startswith(str(node_path.resolve())):
            return False, {"error": "Pfadüberschreitung nicht gestattet"}
        rel = file_query

    # 1. Zwingende Validierung für Python-Dateien via ast.parse()
    if target_file.suffix == ".py":
        try:
            ast.parse(code)
        except SyntaxError as err:
            logger.warning(f"Python SyntaxError in {rel} (Zeile {err.lineno}): {err.msg}")
            return False, {
                "error": f"Python Syntaxfehler in Zeile {err.lineno}: {err.msg}",
                "lineno": err.lineno,
                "offset": err.offset,
                "text": err.text
            }
        except Exception as err:
            return False, {"error": f"Fehler bei Syntaxvalidierung: {err}"}

    # 2. Backup (.bak) anlegen
    bak_file = target_file.with_suffix(target_file.suffix + ".bak")
    try:
        if target_file.is_file():
            shutil.copy2(target_file, bak_file)
            logger.info(f"Backup erstellt: {bak_file.name}")
    except Exception as e:
        logger.warning(f"Konnte Backup {bak_file} nicht anlegen: {e}")

    # 3. Datei atomar speichern
    try:
        tmp_file = target_file.with_suffix(f"{target_file.suffix}.tmp_{os.getpid()}")
        tmp_file.write_text(code, encoding="utf-8")
        tmp_file.replace(target_file)
        logger.info(f"Custom Node Datei '{rel}' erfolgreich gespeichert und validiert.")
        return True, {
            "success": True,
            "node_name": clean_node,
            "path": rel,
            "syntax_valid": True,
            "backup": bak_file.name if bak_file.is_file() else None,
            "size": target_file.stat().st_size,
            "modified": int(target_file.stat().st_mtime),
            "message": f"Datei '{rel}' erfolgreich gespeichert und validiert."
        }
    except Exception as e:
        logger.error(f"Fehler beim Speichern von {target_file}: {e}")
        return False, {"error": f"Fehler beim Speichern: {e}"}
