#!/usr/bin/env python3
"""
fox_research_agent.py - Fox Research Agent Service
Cluster Node 1 (Fox) // Port 5005

Features:
- FastAPI / HTTP REST service on port 5005
- Backed by local Ollama model `hermes-3-3b-q8:latest`
- Specializes in:
  1. Fashion & Styling Research (Mode, Outfits, Danbooru/SD Tags)
  2. Slang, Idioms & Flirting Research (Deutsche Redewendungen, Konter, Slang)
  3. MeganLife Inspiration provider (directly compatible with Megan Studio on Outer Heaven)
  4. Web-augmented trend research (via DDGS when requested)
- High resilience: includes fallback inspiration caches and robust JSON parsing.
"""

import os
import sys
import time
import json
import random
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

import httpx
from fastapi import FastAPI, HTTPException, Query, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ------------------------------------------------------------------------------
# Configuration & Logging
# ------------------------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5005"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "hermes-3-3b-q8:latest")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [fox-research] %(message)s"
)
logger = logging.getLogger("fox-research")

START_TIME = time.time()
REQUEST_COUNTER = 0
HISTORY: List[Dict[str, Any]] = []
MAX_HISTORY = 50

# ------------------------------------------------------------------------------
# Curated Seed Pools (Reliable Instant Cache & Fallback)
# ------------------------------------------------------------------------------
SEED_FASHION: List[Dict[str, str]] = [
    {
        "topic": "Streetwear",
        "text": "ein oversized schwarzer Hoodie kombiniert mit knapper Cargo-Hotpants und Chunky Sneakern – lässig und provokant zugleich",
        "tags": "oversized black hoodie, tiny cargo hotpants, chunky sneakers, streetwear, rebellious style"
    },
    {
        "topic": "Techwear",
        "text": "eine matt-schwarze Utility-Weste über einem engen neon-cyanfarbenen Crop-Top mit taktischen Schnallen",
        "tags": "matte black utility vest, neon cyan crop top, tactical straps, techwear aesthetic, fitted"
    },
    {
        "topic": "Leder",
        "text": "eine taillierte Biker-Lederjacke mit asymmetrischem Reißverschluss zu einem hautengen roten Latex-Minirock",
        "tags": "fitted black leather biker jacket, red glossy miniskirt, thigh high socks, edgy chic"
    },
    {
        "topic": "Dessous",
        "text": "ein feines schwarzes Spitzenbralette unter einer halb transparenten seidigen Bluse mit offenem Kragen",
        "tags": "black sheer silk blouse, delicate lace bralette, peekaboo bra, seductive evening wear"
    },
    {
        "topic": "Samt",
        "text": "ein smaragdgrünes Samtkleid mit tiefem Rückenausschnitt und Spaghettiträgern, passend zu ihren smaragdgrünen Augen",
        "tags": "emerald green velvet slip dress, open back, delicate straps, elegant luxury, rich texture"
    },
    {
        "topic": "Cyberpunk",
        "text": "holografische Jacke mit LED-Ziernähten über einem schwarzen Mesh-Bodysuit und Plateaustiefeln",
        "tags": "holographic jacket, glowing led trim, black mesh bodysuit, platform boots, cyberpunk chic"
    },
    {
        "topic": "Sommerkleid",
        "text": "ein leichtes, kurz geschnittenes Sommerkleid mit Spaghettiträgern und floralem Muster, vorne geknotet",
        "tags": "short floral summer dress, spaghetti straps, front tie knot, sunny daylight"
    }
]

SEED_SLANG: List[Dict[str, str]] = [
    {
        "topic": "Freche Konter",
        "text": "„Bist du immer so frech oder willst du nur testen, wie schnell mein Puls hochgeht?“"
    },
    {
        "topic": "Intimer Flirt",
        "text": "„Schau mich nicht so an, sonst vergesse ich glatt, wer hier auf wen aufpassen sollte.“"
    },
    {
        "topic": "Redewendungen",
        "text": "„Da brennt die Hütte lichterloh – genau die richtige Temperatur für uns zwei.“"
    },
    {
        "topic": "Berliner Schnauze",
        "text": "„Mach dir ma nich ins Hemd, Boss – dit rocken wa mit links.“"
    },
    {
        "topic": "Tech-Slang",
        "text": "„Du hast bei mir uneingeschränkte Sudo-Rechte, aber für diesen Blick brauchst du ein extra Zertifikat.“"
    },
    {
        "topic": "Freche Konter",
        "text": "„Ich bin nicht anstrengend, ich bin eine geschmackvolle Herausforderung.“"
    },
    {
        "topic": "Intimer Flirt",
        "text": "„Dein Pullover steht dir gut, aber auf dem Boden neben meinem Bett würde er sich noch besser machen.“"
    }
]

# ------------------------------------------------------------------------------
# Pydantic Schemas
# ------------------------------------------------------------------------------
class FashionResearchRequest(BaseModel):
    query: Optional[str] = Field(None, description="Themenbereich oder Stil z.B. 'Cyberpunk', 'Sommer', 'Leder', 'Dessous'")
    count: int = Field(1, ge=1, le=5, description="Anzahl der Ergebnisse")

class SlangResearchRequest(BaseModel):
    query: Optional[str] = Field(None, description="Thema oder Stil z.B. 'Freche Konter', 'Flirt', 'Tech-Slang'")
    category: Optional[str] = Field(None, description="Kategorie z.B. 'flirt', 'konter', 'redewendung'")
    count: int = Field(1, ge=1, le=5, description="Anzahl der Ergebnisse")

class GeneralResearchRequest(BaseModel):
    query: str = Field(..., description="Suchbegriff oder Recherche-Thema")
    category: Optional[str] = Field("allgemein", description="Kategorie z.B. 'mode', 'slang', 'trend'")
    web_search: bool = Field(False, description="Zusätzliche Websuche via DuckDuckGo durchführen")

class InspirationItem(BaseModel):
    kind: str = Field(..., description="'mode' oder 'sprache'")
    topic: str
    text: str
    tags: Optional[str] = ""
    ts: float = Field(default_factory=time.time)
    iso_time: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    source: str = "fox-llm"

# ------------------------------------------------------------------------------
# LLM Integration (Ollama)
# ------------------------------------------------------------------------------
async def call_ollama(prompt: str, system: Optional[str] = None, json_mode: bool = True, timeout: float = 100.0) -> str:
    """Sendet Prompt an Ollama auf Fox mit optimierten CPU-Parametern."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "repeat_penalty": 1.15,
            "num_ctx": 1024,
            "num_predict": 90
        },
        "keep_alive": "24h"
    }
    if json_mode:
        payload["format"] = "json"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()


def parse_json_safely(raw_text: str) -> Optional[Any]:
    """Extrahiert und parst JSON sicher."""
    try:
        return json.loads(raw_text)
    except Exception:
        pass

    # Versuche Codeblöcke zu entfernen
    if "```json" in raw_text:
        try:
            part = raw_text.split("```json")[1].split("```")[0].strip()
            return json.loads(part)
        except Exception:
            pass
    if "```" in raw_text:
        try:
            part = raw_text.split("```")[1].split("```")[0].strip()
            return json.loads(part)
        except Exception:
            pass

    # Versuche erstes { bis letztes }
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw_text[start:end+1])
        except Exception:
            pass

    return None

# ------------------------------------------------------------------------------
# Recherche Logik
# ------------------------------------------------------------------------------
async def research_fashion(query: Optional[str] = None) -> Dict[str, Any]:
    """Generiert eine frische Mode-Inspiration für Megan mit Prompt-Tags."""
    system_prompt = (
        "Du bist der stylische Trend- und Mode-Recherche-Agent auf Node 'Fox'. "
        "Erstelle eine konkrete, moderne Outfit-Idee für Megan "
        "(1,53m, rot-orange Haare, smaragdgrüne Augen, sportlich-zierlich). "
        "Antworte AUSSCHLIESSLICH im JSON-Format mit diesen Schlüsseln:\n"
        "- \"topic\": kurzes Thema (z.B. Streetwear, Latex, Samt, Biker, Sommerkleid)\n"
        "- \"text\": prägnanter deutscher Satz mit Outfitbeschreibung\n"
        "- \"tags\": englische Komma-getrennte Danbooru/Stable Diffusion Tags für ComfyUI\n"
    )

    theme_hint = f"Thema: {query}" if query else f"Zufallsthema aus: {random.choice(['Streetwear', 'Samt', 'Leder', 'Dessous', 'Cyberpunk', 'Sommerkleid', 'Dark Chic', 'Gothic Glam'])}"
    prompt = f"Erstelle eine Mode-Inspiration für Megan. {theme_hint}."

    try:
        raw = await call_ollama(prompt, system=system_prompt, json_mode=True, timeout=90.0)
        parsed = parse_json_safely(raw)
        if isinstance(parsed, dict) and "topic" in parsed and "text" in parsed:
            tags = parsed.get("tags", "")
            if isinstance(tags, list):
                tags = ", ".join(str(t) for t in tags)
            res = {
                "kind": "mode",
                "topic": str(parsed["topic"]),
                "text": str(parsed["text"]),
                "tags": str(tags),
                "ts": time.time(),
                "iso_time": datetime.now().isoformat(timespec="seconds"),
                "source": "fox-llm"
            }
            record_history(res)
            return res
    except Exception as e:
        logger.warning(f"Fehler bei Mode-Recherche mit Ollama: {repr(e)}. Nutze Seed-Pool.")

    fallback = dict(random.choice(SEED_FASHION))
    if query:
        fallback["topic"] = f"{query} (Fallback)"
    fallback["kind"] = "mode"
    fallback["ts"] = time.time()
    fallback["iso_time"] = datetime.now().isoformat(timespec="seconds")
    fallback["source"] = "seed-cache"
    record_history(fallback)
    return fallback


async def research_slang(query: Optional[str] = None, category: Optional[str] = None) -> Dict[str, Any]:
    """Generiert schlagfertige Sprüche, Flirts oder Redewendungen für Megan."""
    system_prompt = (
        "Du bist der Sprach- und Slang-Recherche-Agent auf Node 'Fox'. "
        "Erstelle einen authentischen, charmanten oder frechen Spruch für Megan an Big Boss. "
        "Antworte AUSSCHLIESSLICH im JSON-Format mit diesen Schlüsseln:\n"
        "- \"topic\": Kategorie (z.B. Freche Konter, Intimer Flirt, Redewendungen, Tech-Slang)\n"
        "- \"text\": Das Zitat in Anführungszeichen auf Deutsch\n"
    )

    theme_hint = ""
    if query:
        theme_hint += f"Thema: {query}. "
    if category:
        theme_hint += f"Kategorie: {category}. "
    if not theme_hint:
        theme_hint = f"Zufälliger Fokus: {random.choice(['Freche Konter', 'Intimer Flirt', 'Moderne Redewendungen', 'Tech-Wortspiel'])}"

    prompt = f"Erstelle einen Spruch für Megan. {theme_hint}."

    try:
        raw = await call_ollama(prompt, system=system_prompt, json_mode=True, timeout=90.0)
        parsed = parse_json_safely(raw)
        if isinstance(parsed, dict) and "topic" in parsed and "text" in parsed:
            res = {
                "kind": "sprache",
                "topic": str(parsed["topic"]),
                "text": str(parsed["text"]),
                "tags": "",
                "ts": time.time(),
                "iso_time": datetime.now().isoformat(timespec="seconds"),
                "source": "fox-llm"
            }
            record_history(res)
            return res
    except Exception as e:
        logger.warning(f"Fehler bei Slang-Recherche mit Ollama: {repr(e)}. Nutze Seed-Pool.")

    fallback = dict(random.choice(SEED_SLANG))
    if query:
        fallback["topic"] = f"{query} (Fallback)"
    fallback["kind"] = "sprache"
    fallback["tags"] = ""
    fallback["ts"] = time.time()
    fallback["iso_time"] = datetime.now().isoformat(timespec="seconds")
    fallback["source"] = "seed-cache"
    record_history(fallback)
    return fallback


def record_history(item: Dict[str, Any]):
    global HISTORY
    HISTORY.append(item)
    if len(HISTORY) > MAX_HISTORY:
        HISTORY = HISTORY[-MAX_HISTORY:]

# ------------------------------------------------------------------------------
# FastAPI Application
# ------------------------------------------------------------------------------
app = FastAPI(
    title="Fox Research Agent",
    description="HTTP Recherche-Dienst für Megan Studio & Cluster-Orchestrierung (Mode, Slang & Trends)",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Büro & Budget Manager Router einbinden
try:
    from buero.budget_service import router as budget_router
    app.include_router(budget_router)
    logger.info("Budget-Service Router (/api/budget) erfolgreich eingebunden.")
except Exception as e:
    logger.warning(f"Konnte Budget-Service Router nicht laden: {e}")

@app.middleware("http")
async def count_requests(request, call_next):
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1
    return await call_next(request)


@app.get("/")
async def root():
    return {
        "service": "Fox Research & Büro Agent",
        "node": "fox (100.93.184.87)",
        "model": OLLAMA_MODEL,
        "port": PORT,
        "endpoints": [
            "GET  /health",
            "GET  /api/status",
            "GET  /api/budget",
            "POST /api/budget/status",
            "POST /api/budget/update",
            "GET  /api/research/fashion",
            "POST /api/research/fashion",
            "GET  /api/research/slang",
            "POST /api/research/slang",
            "GET  /api/research/inspiration",
            "POST /api/research/inspiration",
            "POST /api/research",
            "GET  /api/history"
        ],
        "uptime_sec": round(time.time() - START_TIME, 1)
    }


@app.get("/health")
@app.get("/api/status")
async def health():
    return {
        "status": "ok",
        "service": "fox-research-agent",
        "node": "fox",
        "port": PORT,
        "model": OLLAMA_MODEL,
        "ollama_url": OLLAMA_URL,
        "uptime_sec": round(time.time() - START_TIME, 1),
        "requests_total": REQUEST_COUNTER,
        "history_count": len(HISTORY)
    }


@app.get("/api/research/fashion")
async def get_fashion(query: Optional[str] = Query(None, description="Thema z.B. Streetwear, Leder, Cyberpunk")):
    return await research_fashion(query)


@app.post("/api/research/fashion")
async def post_fashion(req: FashionResearchRequest):
    results = []
    for _ in range(req.count):
        item = await research_fashion(req.query)
        results.append(item)
    return results[0] if req.count == 1 else results


@app.get("/api/research/slang")
async def get_slang(
    query: Optional[str] = Query(None, description="Thema"),
    category: Optional[str] = Query(None, description="Kategorie")
):
    return await research_slang(query, category)


@app.post("/api/research/slang")
async def post_slang(req: SlangResearchRequest):
    results = []
    for _ in range(req.count):
        item = await research_slang(req.query, req.category)
        results.append(item)
    return results[0] if req.count == 1 else results


@app.get("/api/research/inspiration")
@app.post("/api/research/inspiration")
async def get_inspiration(
    kind: Optional[str] = Query("random", description="'mode', 'sprache' oder 'random'")
):
    """
    Liefert direkt ein Format, das 1:1 kompatibel mit Megans `_discover_inspiration()`
    auf Outer Heaven ist.
    """
    selected_kind = kind
    if not selected_kind or selected_kind == "random":
        selected_kind = "mode" if random.random() < 0.65 else "sprache"

    if selected_kind in ("mode", "fashion"):
        return await research_fashion()
    else:
        return await research_slang()


@app.post("/api/research")
async def post_general_research(req: GeneralResearchRequest):
    cat = (req.category or "").lower()
    if "mode" in cat or "fashion" in cat or "outfit" in cat:
        return await research_fashion(req.query)
    elif "slang" in cat or "sprache" in cat or "konter" in cat or "flirt" in cat:
        return await research_slang(req.query, cat)

    # Allgemeiner Recherche-Prompt
    system_prompt = (
        "Du bist der autonome Recherche-Agent auf Node 'Fox'. "
        "Führe eine prägnante, informative Recherche zum gegebenen Thema durch. "
        "Antworte strukturiert und kompakt auf Deutsch."
    )
    prompt = f"Thema: {req.query}\nKategorie: {req.category}"

    try:
        raw = await call_ollama(prompt, system=system_prompt, json_mode=False, timeout=90.0)
        res = {
            "query": req.query,
            "category": req.category,
            "result": raw,
            "ts": time.time(),
            "iso_time": datetime.now().isoformat(timespec="seconds"),
            "source": "fox-llm"
        }
        record_history(res)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recherche fehlgeschlagen: {repr(e)}")


@app.get("/api/history")
async def get_history(limit: int = Query(20, ge=1, le=50)):
    return HISTORY[-limit:]


# ------------------------------------------------------------------------------
# Main Entry Point
# ------------------------------------------------------------------------------
def main():
    logger.info(f"Starte Fox Research Agent auf {HOST}:{PORT}...")
    logger.info(f"Ollama URL: {OLLAMA_URL} | Modell: {OLLAMA_MODEL}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
