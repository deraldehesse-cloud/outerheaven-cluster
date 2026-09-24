#!/usr/bin/env python3
"""
Megan Autonomous Tool Runner
Executes sandboxed tools inside the megan-tools container:
- duckduckgo search (via ddgs / duckduckgo_search)
- web scraping (via requests & beautifulsoup4)
- yt-dlp media ingest
- sandboxed python execution (30s timeout, restricted to /workspace)
"""

import sys
import os
import json
import time
import subprocess
from pathlib import Path

WORKSPACE = Path("/workspace")

def get_ddgs():
    try:
        from ddgs import DDGS
        return DDGS
    except ImportError:
        from duckduckgo_search import DDGS
        return DDGS

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

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "error": "Usage: tool_runner.py <search|scrape|ytdlp|python|status> [arg|--json <payload>]"}))
        sys.exit(1)

    cmd = sys.argv[1].lower()
    arg = ""
    payload = {}

    if len(sys.argv) >= 4 and sys.argv[2] == "--json":
        try:
            payload = json.loads(sys.argv[3])
        except Exception as e:
            print(json.dumps({"status": "error", "error": f"Invalid JSON payload: {e}"}))
            sys.exit(1)
    elif len(sys.argv) >= 3:
        arg = sys.argv[2]

    if cmd == "search":
        q = payload.get("query", arg)
        count = int(payload.get("max_results", 5))
        res = run_search(q, max_results=count)
    elif cmd in ("scrape", "fetch"):
        u = payload.get("url", arg)
        chars = int(payload.get("max_chars", 4000))
        res = run_scrape(u, max_chars=chars)
    elif cmd in ("ytdlp", "youtube", "video"):
        u = payload.get("url", arg)
        audio = bool(payload.get("extract_audio", False))
        res = run_ytdlp(u, extract_audio=audio)
    elif cmd in ("python", "code", "eval"):
        c = payload.get("code", arg)
        res = run_python_code(c)
    elif cmd == "status":
        files = [f.name for f in WORKSPACE.iterdir() if f.is_file()][:20]
        res = {"status": "ok", "workspace": str(WORKSPACE), "sample_files": files}
    else:
        res = {"status": "error", "error": f"Unknown tool action: {cmd}"}

    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
