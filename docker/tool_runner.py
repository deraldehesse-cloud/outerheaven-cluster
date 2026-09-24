#!/usr/bin/env python3
"""
Megan Autonomous Tool Runner
Executes sandboxed tools inside the megan-tools container:
- duckduckgo search (via ddgs / duckduckgo_search)
- web scraping (via requests & beautifulsoup4)
- yt-dlp media ingest
- sandboxed python execution (30s timeout, restricted to /workspace)
- autonomous comfyui image generation (via host gateway http://172.17.0.1:8080/api/render/direct)
- persistent memory & learning (.brain/session.json and .brain/context.md in /workspace)
"""

import sys
import os
import json
import time
import subprocess
import argparse
from pathlib import Path
from typing import Dict, Any, Optional

WORKSPACE = Path("/workspace")
BRAIN_DIR = WORKSPACE / ".brain"
DEFAULT_GATEWAYS = [
    os.environ.get("HOST_GATEWAY", "http://172.17.0.1:8080"),
    "http://100.116.38.29:8080",
    "http://127.0.0.1:8080"
]

def get_ddgs():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS

# ==============================================================================
# 1. Autonomous Memory & .brain Persistence
# ==============================================================================

def ensure_brain_dir():
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)

def load_brain_data() -> Dict[str, Any]:
    """Loads context.md and session.json from /workspace/.brain/."""
    ensure_brain_dir()
    context_file = BRAIN_DIR / "context.md"
    session_file = BRAIN_DIR / "session.json"

    context_text = ""
    if context_file.is_file():
        try:
            context_text = context_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    session_data = {"history": [], "preferences": {}}
    if session_file.is_file():
        try:
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "context": context_text,
        "session": session_data,
        "preferences": session_data.get("preferences", {})
    }

def save_brain_preference(key: str, value: Any) -> Dict[str, Any]:
    """Saves or updates a preference in /workspace/.brain/session.json and context.md."""
    ensure_brain_dir()
    session_file = BRAIN_DIR / "session.json"
    context_file = BRAIN_DIR / "context.md"

    session_data = {"history": [], "preferences": {}}
    if session_file.is_file():
        try:
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            session_data = {"history": [], "preferences": {}}

    session_data.setdefault("preferences", {})[key] = value
    session_data.setdefault("history", []).append({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "preference_learned",
        "key": key,
        "value": value
    })
    session_file.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Update context.md note if not already mentioned
    try:
        ctx = context_file.read_text(encoding="utf-8") if context_file.is_file() else "# Megan Memory & Context\n"
        bullet = f"- **{key}**: {value}\n"
        if key not in ctx:
            if "## Gelernte Vorlieben von Big Boss" not in ctx:
                ctx += "\n## Gelernte Vorlieben von Big Boss\n"
            ctx += bullet
            context_file.write_text(ctx, encoding="utf-8")
    except Exception:
        pass

    return {"status": "ok", "key": key, "value": value, "all_preferences": session_data["preferences"]}

# ==============================================================================
# 2. Autonomous ComfyUI Image Generation Tool
# ==============================================================================

def enrich_prompt_with_preferences(prompt: str, preferences: Dict[str, Any], style_preset: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
    """Applies learned user preferences and Megan character triggers to the prompt."""
    applied = {}
    tokens = [t.strip() for t in prompt.split(",") if t.strip()]

    # Standard character trigger if not present
    lower_prompt = prompt.lower()
    if "megan" not in lower_prompt and "1girl" not in lower_prompt:
        tokens.insert(0, "1girl, megan, solo")
        applied["character_trigger"] = "1girl, megan, solo"
    elif "1girl" not in lower_prompt:
        tokens.insert(0, "1girl")

    # Character attributes
    if "orange hair" not in lower_prompt and "emerald eyes" not in lower_prompt:
        tokens.append("orange hair, emerald eyes, cute mischievous face")
        applied["character_details"] = "orange hair, emerald eyes"

    # Style Preset or Learned Style
    active_style = style_preset or preferences.get("image_style") or preferences.get("favorite_style")
    if active_style:
        if "photorealistic" in str(active_style).lower() and "photorealistic" not in lower_prompt:
            tokens.append("photorealistic, raw photo, 8k uhd, dslr, high quality, soft studio lighting")
            applied["style"] = "photorealistic"
        elif "cinematic" in str(active_style).lower() and "cinematic" not in lower_prompt:
            tokens.append("cinematic lighting, dramatic atmosphere, depth of field, 8k")
            applied["style"] = "cinematic"
        else:
            tokens.append(str(active_style))
            applied["style"] = str(active_style)
    else:
        # Default quality tags
        if "masterpiece" not in lower_prompt and "8k" not in lower_prompt:
            tokens.append("masterpiece, best quality, 8k uhd, soft studio lighting, sharp focus, natural skin texture")
            applied["default_quality"] = True

    # Learned favorite clothing (if no clothing or nakedness specified)
    clothing_keywords = ["dress", "bikini", "lingerie", "naked", "nude", "top", "shorts", "shirt", "panties", "bra", "cockpit", "suit", "skirt"]
    has_clothing = any(k in lower_prompt for k in clothing_keywords)
    fav_clothing = preferences.get("favorite_clothing") or preferences.get("preferred_outfit")
    if not has_clothing and fav_clothing:
        tokens.append(str(fav_clothing))
        applied["favorite_clothing"] = str(fav_clothing)

    # Lighting preference
    preferred_lighting = preferences.get("preferred_lighting")
    if preferred_lighting and preferred_lighting.lower() not in lower_prompt:
        tokens.append(f"{preferred_lighting} lighting")
        applied["lighting"] = preferred_lighting

    final_prompt = ", ".join(tokens)
    return final_prompt, applied

def run_generate_image(prompt: str, aspect_ratio: str = "portrait", style_preset: Optional[str] = None) -> dict:
    """Dispatches image generation via host gateway with GPURenderLock."""
    if not prompt or not prompt.strip():
        return {"status": "error", "error": "Empty prompt"}

    import requests

    brain = load_brain_data()
    prefs = brain.get("preferences", {})

    final_prompt, applied = enrich_prompt_with_preferences(prompt.strip(), prefs, style_preset)

    payload = {
        "prompt": final_prompt,
        "aspect_ratio": aspect_ratio or "portrait",
        "style_preset": style_preset or "",
        "raw_prompt": prompt.strip()
    }

    last_error = ""
    for gw in DEFAULT_GATEWAYS:
        url = f"{gw.rstrip('/')}/api/render/direct"
        try:
            resp = requests.post(url, json=payload, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "ok" or data.get("success"):
                    rel_url = data.get("url", "")
                    full_url = f"{gw.rstrip('/')}{rel_url}" if rel_url.startswith("/") else rel_url
                    return {
                        "status": "ok",
                        "prompt": final_prompt,
                        "aspect_ratio": aspect_ratio,
                        "prompt_id": data.get("prompt_id"),
                        "filename": data.get("filename"),
                        "filepath": data.get("filepath"),
                        "url": rel_url,
                        "full_url": full_url,
                        "gateway": gw,
                        "preferences_applied": applied
                    }
                else:
                    return {"status": "error", "error": data.get("error", "Unknown render error"), "gateway": gw}
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            last_error = str(e)
            continue

    return {"status": "error", "error": f"Failed to contact host gateway for image generation: {last_error}"}

# ==============================================================================
# 3. Web Tools: Search, Scrape, Yt-Dlp, Python
# ==============================================================================

def run_search(query: str, max_results: int = 5) -> dict:
    if not query or not query.strip():
        return {"status": "error", "error": "Empty query"}
    try:
        DDGSClass = get_ddgs()
        results = []
        with DDGSClass() as ddgs:
            for r in ddgs.text(query.strip(), max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "href": r.get("href", ""),
                    "body": r.get("body", "")
                })
        return {"status": "ok", "query": query.strip(), "count": len(results), "results": results}
    except Exception as e:
        return {"status": "error", "error": f"Search failed: {e}"}

def run_scrape(url: str, max_chars: int = 4000) -> dict:
    if not url or not url.strip():
        return {"status": "error", "error": "Empty url"}
    try:
        import requests
        from bs4 import BeautifulSoup

        clean_url = url.strip()
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(clean_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {"status": "error", "error": f"HTTP status {resp.status_code}"}

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned = "\n".join(lines)
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars] + "\n...[truncated]"

        title = soup.title.string.strip() if soup.title and soup.title.string else clean_url
        return {"status": "ok", "url": clean_url, "title": title, "content": cleaned}
    except Exception as e:
        return {"status": "error", "error": f"Scrape failed: {e}"}

def run_ytdlp(url: str, extract_audio: bool = False) -> dict:
    if not url or not url.strip():
        return {"status": "error", "error": "Empty url"}
    try:
        import yt_dlp
        clean_url = url.strip()
        out_template = str(WORKSPACE / "%(title).60s [%(id)s].%(ext)s")

        ydl_opts = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "max_filesize": 350 * 1024 * 1024, # 350MB limit
        }
        if extract_audio:
            ydl_opts.update({
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            })
        else:
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            filename = ydl.prepare_filename(info)
            if extract_audio:
                filename = os.path.splitext(filename)[0] + ".mp3"

            return {
                "status": "ok",
                "url": clean_url,
                "title": info.get("title", ""),
                "duration": info.get("duration", 0),
                "filename": os.path.basename(filename),
                "filepath": str(filename),
                "filesize": os.path.getsize(filename) if os.path.exists(filename) else 0
            }
    except Exception as e:
        return {"status": "error", "error": f"yt-dlp failed: {e}"}

def run_python_code(code: str, timeout: float = 30.0) -> dict:
    if not code or not code.strip():
        return {"status": "error", "error": "Empty python code"}
    try:
        t0 = time.time()
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        elapsed = time.time() - t0
        return {
            "status": "ok",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "elapsed_seconds": round(elapsed, 3)
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"Execution timed out after {timeout} seconds"}
    except Exception as e:
        return {"status": "error", "error": f"Subprocess error: {e}"}

# ==============================================================================
# 4. CLI & Dispatcher
# ==============================================================================

def main():
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "error": "Usage: tool_runner.py <search|scrape|ytdlp|python|generate_image|learn|brain|status> [args...]"
        }))
        sys.exit(1)

    cmd = sys.argv[1].lower()
    raw_args = sys.argv[2:]

    # Parse JSON if passed
    payload = {}
    if "--json" in raw_args:
        idx = raw_args.index("--json")
        if idx + 1 < len(raw_args):
            try:
                payload = json.loads(raw_args[idx + 1])
            except Exception as e:
                print(json.dumps({"status": "error", "error": f"Invalid JSON payload: {e}"}))
                sys.exit(1)

    # Secondary Argument Parser for direct CLI flags (e.g. generate_image --prompt "..." --aspect_ratio portrait)
    cli_parser = argparse.ArgumentParser(prog=f"tool_runner.py {cmd}", add_help=False)
    cli_parser.add_argument("--prompt", "-p", default="")
    cli_parser.add_argument("--aspect_ratio", "-a", default="portrait")
    cli_parser.add_argument("--style_preset", "-s", default="")
    cli_parser.add_argument("--key", "-k", default="")
    cli_parser.add_argument("--value", "-v", default="")
    cli_parser.add_argument("--query", "-q", default="")
    cli_parser.add_argument("--url", "-u", default="")
    cli_parser.add_argument("--max_results", type=int, default=5)
    cli_parser.add_argument("--max_chars", type=int, default=4000)
    cli_parser.add_argument("--extract_audio", action="store_true")
    cli_parser.add_argument("--json", dest="_unused_json", default="")

    parsed_cli, unk = cli_parser.parse_known_args(raw_args)
    positional_arg = unk[0] if unk and not unk[0].startswith("-") else ""

    if cmd in ("generate_image", "image", "render"):
        p = payload.get("prompt") or parsed_cli.prompt or positional_arg
        ar = payload.get("aspect_ratio") or parsed_cli.aspect_ratio or "portrait"
        sp = payload.get("style_preset") or parsed_cli.style_preset or None
        res = run_generate_image(p, aspect_ratio=ar, style_preset=sp)

    elif cmd in ("learn", "preference", "save_pref"):
        k = payload.get("key") or parsed_cli.key or positional_arg
        v = payload.get("value") or parsed_cli.value
        if not k or v is None:
            res = {"status": "error", "error": "Both key and value are required"}
        else:
            res = save_brain_preference(k, v)

    elif cmd in ("brain", "memory", "context"):
        res = {"status": "ok", "brain": load_brain_data()}

    elif cmd == "search":
        q = payload.get("query") or parsed_cli.query or positional_arg
        count = int(payload.get("max_results") or parsed_cli.max_results or 5)
        res = run_search(q, max_results=count)

    elif cmd in ("scrape", "fetch"):
        u = payload.get("url") or parsed_cli.url or positional_arg
        chars = int(payload.get("max_chars") or parsed_cli.max_chars or 4000)
        res = run_scrape(u, max_chars=chars)

    elif cmd in ("ytdlp", "youtube", "video"):
        u = payload.get("url") or parsed_cli.url or positional_arg
        audio = bool(payload.get("extract_audio") or parsed_cli.extract_audio or False)
        res = run_ytdlp(u, extract_audio=audio)

    elif cmd in ("python", "code", "eval"):
        c = payload.get("code") or positional_arg
        res = run_python_code(c)

    elif cmd == "status":
        ensure_brain_dir()
        files = [f.name for f in WORKSPACE.iterdir() if f.is_file()][:20]
        brain_files = [f.name for f in BRAIN_DIR.iterdir() if f.is_file()] if BRAIN_DIR.is_dir() else []
        res = {
            "status": "ok",
            "workspace": str(WORKSPACE),
            "sample_files": files,
            "brain_files": brain_files,
            "brain_data": load_brain_data()
        }
    else:
        res = {"status": "error", "error": f"Unknown tool action: {cmd}"}

    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
