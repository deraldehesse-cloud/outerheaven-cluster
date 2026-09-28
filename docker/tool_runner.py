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
import re
import json
import time
import subprocess
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List

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

    session_data = {"history": [], "preferences": {}, "learned_preferences": {}, "visual_preferences": {}}
    if session_file.is_file():
        try:
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "context": context_text,
        "session": session_data,
        "preferences": session_data.get("preferences", {}),
        "learned_preferences": session_data.get("learned_preferences", {}),
        "visual_preferences": session_data.get("visual_preferences", {})
    }

def _sync_to_host_brain(session_data: Dict[str, Any], context_text: str = ""):
    """Best-effort write to sibling host brain directories if accessible."""
    host_targets = [
        (Path("/mnt/storage-beta/projekte/projekt-sexchat/.brain/session.json"),
         Path("/mnt/storage-beta/projekte/projekt-sexchat/.brain/context.md")),
        (Path("/mnt/storage-beta/projekte/projekt-sexchat/workspace/.brain/session.json"),
         Path("/mnt/storage-beta/projekte/projekt-sexchat/workspace/.brain/context.md"))
    ]
    for s_path, c_path in host_targets:
        try:
            if s_path.parent.is_dir() and os.access(str(s_path.parent), os.W_OK):
                s_path.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")
                if context_text and os.access(str(c_path.parent), os.W_OK):
                    c_path.write_text(context_text, encoding="utf-8")
        except Exception:
            pass

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

    ctx = ""
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

    _sync_to_host_brain(session_data, ctx)
    return {"status": "ok", "key": key, "value": value, "all_preferences": session_data["preferences"]}

# Visual Keywords for Classification
VISUAL_KEYWORD_MAP = {
    "clothing": [
        "cockpit suit", "flight suit", "pilot suit", "lingerie", "latex", "leather jacket",
        "bikini", "swimsuit", "evening dress", "mini dress", "dress", "skirt",
        "stockings", "thighhighs", "thigh-highs", "bodysuit", "catsuit", "panties", "bra",
        "oversized sweater", "crop top", "blouse", "business suit", "suit", "t-shirt",
        "jeans", "shorts", "naked", "nude"
    ],
    "lighting": [
        "soft rim lighting", "soft studio lighting", "cinematic lighting",
        "studio lighting", "neon lighting", "neon glow", "dramatic lighting", "volumetric lighting",
        "golden hour", "warm lighting", "moody lighting", "backlit", "cyberpunk neon", "rim lighting"
    ],
    "styles": [
        "photorealistic", "raw photo", "8k uhd", "dslr", "masterpiece", "best quality",
        "hyperdetailed", "film grain", "depth of field", "sharp focus", "anime", "illustration"
    ],
    "settings": [
        "cockpit", "flight simulator", "space station", "cyberpunk city", "neon street",
        "bedroom", "luxury hotel", "balcony", "rainy street", "rain", "stairs", "office"
    ]
}

def extract_visual_tags(prompt: str) -> Dict[str, List[str]]:
    """Extracts known clothing, lighting, style, and setting tags from a prompt."""
    lp = prompt.lower()
    found = {"clothing": [], "lighting": [], "styles": [], "settings": []}
    for category, keywords in VISUAL_KEYWORD_MAP.items():
        for kw in keywords:
            pattern = r'(?:\b|_)' + re.escape(kw) + r'(?:\b|_)'
            if re.search(pattern, lp) or kw in lp:
                if kw not in found[category]:
                    found[category].append(kw)
    return found

def update_visual_preferences(prompt: str, is_manual_save: bool = False) -> Dict[str, Any]:
    """Extracts visual tags from prompt and updates visual_preferences in session.json."""
    if not prompt or not prompt.strip():
        return {"status": "skipped", "reason": "empty prompt"}

    ensure_brain_dir()
    session_file = BRAIN_DIR / "session.json"
    session_data = {"history": [], "preferences": {}}
    if session_file.is_file():
        try:
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            session_data = {"history": [], "preferences": {}}

    vis_prefs = session_data.setdefault("visual_preferences", {
        "clothing": {},
        "lighting": {},
        "styles": {},
        "settings": {},
        "recent_prompts": []
    })

    extracted = extract_visual_tags(prompt)
    weight = 3 if is_manual_save else 1

    for cat in ["clothing", "lighting", "styles", "settings"]:
        cat_dict = vis_prefs.setdefault(cat, {})
        for tag in extracted.get(cat, []):
            cat_dict[tag] = cat_dict.get(tag, 0) + weight

    recents = vis_prefs.setdefault("recent_prompts", [])
    recents.append({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "prompt": prompt[:300],
        "manual_save": is_manual_save,
        "extracted_tags": extracted
    })
    vis_prefs["recent_prompts"] = recents[-15:]

    prefs = session_data.setdefault("preferences", {})
    if vis_prefs.get("clothing"):
        top_cloth = max(vis_prefs["clothing"].items(), key=lambda x: x[1])
        if is_manual_save or top_cloth[1] >= 3:
            prefs["favorite_clothing"] = top_cloth[0]

    if vis_prefs.get("lighting"):
        top_light = max(vis_prefs["lighting"].items(), key=lambda x: x[1])
        if is_manual_save or top_light[1] >= 3:
            prefs["preferred_lighting"] = top_light[0]

    if vis_prefs.get("styles"):
        top_style = max(vis_prefs["styles"].items(), key=lambda x: x[1])
        if is_manual_save or top_style[1] >= 3:
            prefs["image_style"] = top_style[0]

    session_file.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")
    _sync_to_host_brain(session_data)

    return {
        "status": "ok",
        "extracted": extracted,
        "visual_preferences": vis_prefs,
        "updated_preferences": prefs
    }

def record_research_learning(title: str, summary: str, source_type: str = "web", url: str = "") -> Dict[str, Any]:
    """Records research, scrape, or vision ingest into .brain/context.md and session.json."""
    ensure_brain_dir()
    session_file = BRAIN_DIR / "session.json"
    context_file = BRAIN_DIR / "context.md"

    session_data = {"history": [], "preferences": {}}
    if session_file.is_file():
        try:
            session_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            session_data = {"history": [], "preferences": {}}

    learned = session_data.setdefault("learned_preferences", {
        "topics": [],
        "recent_research": []
    })

    stopwords = {
        "https", "http", "www", "com", "html", "eine", "einer", "eines", "einem",
        "dies", "diese", "dieser", "haben", "sein", "wurde", "video", "frame",
        "frames", "bild", "bilder", "analyse", "sehen", "zeigt", "nicht", "auch",
        "dass", "oder", "aber", "über", "unter", "durch", "nach"
    }
    raw_tokens = re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", f"{title} {summary}".lower())
    detected_topics = []
    for t in raw_tokens:
        if t not in stopwords and t not in detected_topics:
            detected_topics.append(t)
        if len(detected_topics) >= 8:
            break

    for dt in detected_topics:
        if dt not in learned.setdefault("topics", []):
            learned["topics"].append(dt)
    learned["topics"] = learned["topics"][-30:]

    clean_summary = summary.replace("\n", " ").strip()
    if len(clean_summary) > 280:
        clean_summary = clean_summary[:280] + "..."

    learned.setdefault("recent_research", []).append({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_type,
        "title": title[:120],
        "summary": clean_summary,
        "url": url,
        "topics": detected_topics
    })
    learned["recent_research"] = learned["recent_research"][-15:]

    session_file.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")

    ctx_text = ""
    try:
        ctx_text = context_file.read_text(encoding="utf-8") if context_file.is_file() else "# Projekt Big Boss Megan Context & Goals\n"
        section_header = "### USER INTERESTS & RESEARCH"
        bullet = f"- [{time.strftime('%Y-%m-%d %H:%M')}] ({source_type.upper()}) **{title[:80]}**: {clean_summary}\n"

        if section_header in ctx_text:
            parts = ctx_text.split(section_header, 1)
            before = parts[0]
            after = parts[1].lstrip("\n")
            lines = after.splitlines(keepends=True)
            sec_lines = []
            rest_lines = []
            in_sec = True
            for line in lines:
                if in_sec and line.startswith("### ") and not line.startswith(section_header):
                    in_sec = False
                if in_sec:
                    sec_lines.append(line)
                else:
                    rest_lines.append(line)

            sec_bullets = [l for l in sec_lines if l.strip().startswith("- ")]
            sec_bullets.insert(0, bullet)
            sec_bullets = sec_bullets[:15]

            ctx_text = before + section_header + "\n\n" + "".join(sec_bullets) + "".join(rest_lines)
        else:
            ctx_text = ctx_text.rstrip() + f"\n\n{section_header}\n\n{bullet}"

        context_file.write_text(ctx_text, encoding="utf-8")
    except Exception as ctx_err:
        print(f"[Warnung] context.md Update: {ctx_err}", file=sys.stderr)

    _sync_to_host_brain(session_data, ctx_text)

    return {
        "status": "ok",
        "title": title,
        "source": source_type,
        "topics": detected_topics,
        "learned": learned
    }

# ==============================================================================
# 2. Autonomous ComfyUI Image Generation Tool
# ==============================================================================

def enrich_prompt_with_preferences(prompt: str, preferences: Dict[str, Any], visual_preferences: Optional[Dict[str, Any]] = None, style_preset: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
    """Applies learned user preferences, visual tags, and Megan character triggers to the prompt."""
    applied = {}
    tokens = [t.strip() for t in prompt.split(",") if t.strip()]
    lower_prompt = prompt.lower()

    if "megan" not in lower_prompt and "1girl" not in lower_prompt:
        tokens.insert(0, "1girl, megan, solo")
        applied["character_trigger"] = "1girl, megan, solo"
    elif "1girl" not in lower_prompt:
        tokens.insert(0, "1girl")

    if "orange hair" not in lower_prompt and "emerald eyes" not in lower_prompt:
        tokens.append("orange hair, emerald eyes, cute mischievous face")
        applied["character_details"] = "orange hair, emerald eyes"

    active_style = style_preset or preferences.get("image_style") or preferences.get("favorite_style")
    if not active_style and visual_preferences and visual_preferences.get("styles"):
        top_s = max(visual_preferences["styles"].items(), key=lambda x: x[1], default=(None, 0))
        if top_s[0] and top_s[1] >= 2:
            active_style = top_s[0]

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
        if "masterpiece" not in lower_prompt and "8k" not in lower_prompt:
            tokens.append("masterpiece, best quality, 8k uhd, soft studio lighting, sharp focus, natural skin texture")
            applied["default_quality"] = True

    clothing_keywords = ["dress", "bikini", "lingerie", "naked", "nude", "top", "shorts", "shirt", "panties", "bra", "cockpit", "suit", "skirt"]
    has_clothing = any(k in lower_prompt for k in clothing_keywords)
    fav_clothing = preferences.get("favorite_clothing") or preferences.get("preferred_outfit")
    if not has_clothing and not fav_clothing and visual_preferences and visual_preferences.get("clothing"):
        top_c = max(visual_preferences["clothing"].items(), key=lambda x: x[1], default=(None, 0))
        if top_c[0] and top_c[1] >= 2:
            fav_clothing = top_c[0]

    if not has_clothing and fav_clothing:
        tokens.append(str(fav_clothing))
        applied["favorite_clothing"] = str(fav_clothing)

    preferred_lighting = preferences.get("preferred_lighting")
    if not preferred_lighting and visual_preferences and visual_preferences.get("lighting"):
        top_l = max(visual_preferences["lighting"].items(), key=lambda x: x[1], default=(None, 0))
        if top_l[0] and top_l[1] >= 2:
            preferred_lighting = top_l[0]

    if preferred_lighting and preferred_lighting.lower() not in lower_prompt:
        light_tag = f"{preferred_lighting} lighting" if not preferred_lighting.endswith("lighting") else preferred_lighting
        tokens.append(light_tag)
        applied["lighting"] = preferred_lighting

    final_prompt = ", ".join(tokens)
    return final_prompt, applied

def run_generate_image(prompt: str, aspect_ratio: str = "portrait", style_preset: Optional[str] = None, save_to_brain: bool = True) -> dict:
    """Dispatches image generation via host gateway with GPURenderLock and persists preferences."""
    if not prompt or not prompt.strip():
        return {"status": "error", "error": "Empty prompt"}

    import requests

    brain = load_brain_data()
    prefs = brain.get("preferences", {})
    vis_prefs = brain.get("visual_preferences", {})

    final_prompt, applied = enrich_prompt_with_preferences(prompt.strip(), prefs, vis_prefs, style_preset)

    if save_to_brain:
        try:
            update_visual_preferences(prompt.strip(), is_manual_save=True)
        except Exception as e:
            print(f"[Warnung] update_visual_preferences: {e}", file=sys.stderr)

    payload = {
        "project": "megan",
        "prompt": final_prompt,
        "aspect_ratio": aspect_ratio or "portrait",
        "style_preset": style_preset or "",
        "raw_prompt": prompt.strip(),
        "workflow": "megan_workflow.json",
        "checkpoint": "sd_xl_base_1.0.safetensors"
    }

    last_error = ""
    for gw in DEFAULT_GATEWAYS:
        url = f"{gw.rstrip('/')}/api/render/direct"
        try:
            resp = requests.post(url, json=payload, timeout=180)
            try:
                data = resp.json()
            except Exception:
                data = {}
            if data.get("status") == "busy":
                return data
            if resp.status_code == 200:
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

def run_search(query: str, max_results: int = 5, save_to_brain: bool = False) -> dict:
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

        if save_to_brain and results:
            summary = "; ".join([f"{r['title']}: {r['body'][:100]}" for r in results[:2]])
            record_research_learning(f"Suche: {query.strip()}", summary, source_type="search")

        return {"status": "ok", "query": query.strip(), "count": len(results), "results": results}
    except Exception as e:
        return {"status": "error", "error": f"Search failed: {e}"}

def run_scrape(url: str, max_chars: int = 4000, save_to_brain: bool = True) -> dict:
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

        if save_to_brain and cleaned:
            record_research_learning(title, cleaned[:400], source_type="scrape", url=clean_url)

        return {"status": "ok", "url": clean_url, "title": title, "content": cleaned}
    except Exception as e:
        return {"status": "error", "error": f"Scrape failed: {e}"}

def run_ytdlp(url: str, extract_audio: bool = False, save_to_brain: bool = True) -> dict:
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
            "max_filesize": 350 * 1024 * 1024,
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
            # 720p cap for fast, resource-friendly downloads
            ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            filename = ydl.prepare_filename(info)
            if extract_audio:
                filename = os.path.splitext(filename)[0] + ".mp3"

            duration = info.get("duration", 0)
            title = info.get("title", "")
            uploader = info.get("uploader") or info.get("channel") or ""
            description = (info.get("description") or "").strip().replace("\n", " ")[:200]
            base_fname = os.path.basename(filename)

            # Frame extraction via ffmpeg
            frame_filename = None
            frame_path = None
            if not extract_audio and os.path.exists(filename):
                try:
                    frame_name = f"frame_{Path(filename).stem}.jpg"
                    target_frame = WORKSPACE / frame_name
                    seek_sec = min(5, max(1, int(duration * 0.1))) if duration else 2
                    cmd = [
                        "ffmpeg", "-y", "-ss", str(seek_sec),
                        "-i", str(filename),
                        "-frames:v", "1",
                        "-q:v", "2",
                        str(target_frame)
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=15)
                    if target_frame.is_file():
                        frame_filename = frame_name
                        frame_path = str(target_frame)
                except Exception as fe:
                    print(f"[Warnung] Keyframe-Extraktion fehlgeschlagen: {fe}", file=sys.stderr)

            # Formulate structured core info summary
            summary_parts = []
            if uploader:
                summary_parts.append(f"Kanal: {uploader}")
            summary_parts.append(f"Dauer: {int(duration)}s")
            if description:
                summary_parts.append(f"Inhalt: {description}")
            if frame_filename:
                summary_parts.append(f"Frame: {frame_filename}")
            summary = " | ".join(summary_parts)

            if save_to_brain and title:
                record_research_learning(title, summary, source_type="ytdlp", url=clean_url)

            return {
                "status": "ok",
                "url": clean_url,
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "filename": base_fname,
                "filepath": str(filename),
                "frame_filename": frame_filename,
                "frame_filepath": frame_path,
                "summary": summary,
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
            "error": "Usage: tool_runner.py <search|scrape|ytdlp|python|generate_image|learn|record_research|update_visual_pref|brain|status> [args...]"
        }))
        sys.exit(1)

    cmd = sys.argv[1].lower()
    raw_args = sys.argv[2:]

    payload = {}
    if "--json" in raw_args:
        idx = raw_args.index("--json")
        if idx + 1 < len(raw_args):
            try:
                payload = json.loads(raw_args[idx + 1])
            except Exception as e:
                print(json.dumps({"status": "error", "error": f"Invalid JSON payload: {e}"}))
                sys.exit(1)

    cli_parser = argparse.ArgumentParser(prog=f"tool_runner.py {cmd}", add_help=False)
    cli_parser.add_argument("--prompt", "-p", default="")
    cli_parser.add_argument("--aspect_ratio", "-a", default="portrait")
    cli_parser.add_argument("--style_preset", "-s", default="")
    cli_parser.add_argument("--key", "-k", default="")
    cli_parser.add_argument("--value", "-v", default="")
    cli_parser.add_argument("--query", "-q", default="")
    cli_parser.add_argument("--url", "-u", default="")
    cli_parser.add_argument("--title", "-t", default="")
    cli_parser.add_argument("--summary", default="")
    cli_parser.add_argument("--source_type", default="web")
    cli_parser.add_argument("--save_to_brain", action="store_true", default=None)
    cli_parser.add_argument("--max_results", type=int, default=5)
    cli_parser.add_argument("--max_chars", type=int, default=4000)
    cli_parser.add_argument("--extract_audio", action="store_true")
    cli_parser.add_argument("--json", dest="_unused_json", default="")

    parsed_cli, unk = cli_parser.parse_known_args(raw_args)
    positional_arg = unk[0] if unk and not unk[0].startswith("-") else ""

    save_brain_flag = payload.get("save_to_brain") if "save_to_brain" in payload else (parsed_cli.save_to_brain if parsed_cli.save_to_brain is not None else True)

    if cmd in ("generate_image", "image", "render"):
        p = payload.get("prompt") or parsed_cli.prompt or positional_arg
        ar = payload.get("aspect_ratio") or parsed_cli.aspect_ratio or "portrait"
        sp = payload.get("style_preset") or parsed_cli.style_preset or None
        res = run_generate_image(p, aspect_ratio=ar, style_preset=sp, save_to_brain=bool(save_brain_flag))

    elif cmd in ("learn", "preference", "save_pref"):
        k = payload.get("key") or parsed_cli.key or positional_arg
        v = payload.get("value") or parsed_cli.value
        if not k or v is None:
            res = {"status": "error", "error": "Both key and value are required"}
        else:
            res = save_brain_preference(k, v)

    elif cmd in ("record_research", "research"):
        title = payload.get("title") or parsed_cli.title or positional_arg
        summary = payload.get("summary") or parsed_cli.summary or ""
        stype = payload.get("source_type") or parsed_cli.source_type or "web"
        u = payload.get("url") or parsed_cli.url or ""
        if not title:
            res = {"status": "error", "error": "Title required for record_research"}
        else:
            res = record_research_learning(title, summary, source_type=stype, url=u)

    elif cmd in ("update_visual_pref", "visual_pref"):
        p = payload.get("prompt") or parsed_cli.prompt or positional_arg
        manual = bool(payload.get("manual", True))
        res = update_visual_preferences(p, is_manual_save=manual)

    elif cmd in ("brain", "memory", "context"):
        res = {"status": "ok", "brain": load_brain_data()}

    elif cmd == "search":
        q = payload.get("query") or parsed_cli.query or positional_arg
        count = int(payload.get("max_results") or parsed_cli.max_results or 5)
        res = run_search(q, max_results=count, save_to_brain=bool(save_brain_flag))

    elif cmd in ("scrape", "fetch"):
        u = payload.get("url") or parsed_cli.url or positional_arg
        chars = int(payload.get("max_chars") or parsed_cli.max_chars or 4000)
        res = run_scrape(u, max_chars=chars, save_to_brain=bool(save_brain_flag))

    elif cmd in ("ytdlp", "youtube", "video"):
        u = payload.get("url") or parsed_cli.url or positional_arg
        audio = bool(payload.get("extract_audio") or parsed_cli.extract_audio or False)
        res = run_ytdlp(u, extract_audio=audio, save_to_brain=bool(save_brain_flag))

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
