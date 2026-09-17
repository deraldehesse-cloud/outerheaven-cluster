#!/usr/bin/env python3
"""
danbooru_tagger.py - Danbooru Tag Helper & Validation Utility
Outer Heaven Cluster ComfyUI & Companion Bot Pipeline
"""

import os
import sys
import csv
import re
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

DEFAULT_CSV_PATH = Path("/mnt/storage-beta/projekte/projekt-sexchat/danbooru_tags.csv")

# Category mapping in Danbooru
# 0 = general, 1 = artist, 3 = copyright, 4 = character, 5 = meta
CATEGORY_NAMES = {
    0: "General",
    1: "Artist",
    3: "Copyright",
    4: "Character",
    5: "Meta"
}

_TAG_CACHE: Optional[List[Dict[str, Any]]] = None
_TAG_DICT: Optional[Dict[str, Dict[str, Any]]] = None


def load_danbooru_tags(csv_path: Optional[Path] = None, force_reload: bool = False) -> List[Dict[str, Any]]:
    """Loads and caches Danbooru tags from CSV file sorted descending by count."""
    global _TAG_CACHE, _TAG_DICT
    if _TAG_CACHE is not None and not force_reload:
        return _TAG_CACHE

    target_path = Path(csv_path) if csv_path else DEFAULT_CSV_PATH
    if not target_path.is_file():
        alt_paths = [
            Path("/mnt/storage-beta/projekte/projekt-sexchat/danbooru_tags.csv"),
            Path(__file__).parent / "danbooru_tags.csv",
        ]
        for p in alt_paths:
            if p.is_file():
                target_path = p
                break

    if not target_path.is_file():
        print(f"[danbooru_tagger] Warning: CSV not found at {target_path}", file=sys.stderr)
        _TAG_CACHE = []
        _TAG_DICT = {}
        return _TAG_CACHE

    tags = []
    tag_dict = {}
    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) >= 3:
                try:
                    tag_name = row[0].strip()
                    category = int(row[1])
                    count = int(row[2])
                    aliases = row[3].strip() if len(row) > 3 else ""
                    item = {
                        "tag": tag_name,
                        "category": category,
                        "category_name": CATEGORY_NAMES.get(category, "Unknown"),
                        "count": count,
                        "aliases": aliases
                    }
                    tags.append(item)
                    tag_dict[tag_name.lower()] = item
                except ValueError:
                    continue

    tags.sort(key=lambda x: x["count"], reverse=True)
    _TAG_CACHE = tags
    _TAG_DICT = tag_dict
    return _TAG_CACHE


def search_tags(query: str, limit: int = 15, category: Optional[int] = None, csv_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Search tags by prefix and substring matching with count ordering."""
    all_tags = load_danbooru_tags(csv_path)
    clean_q = query.strip().lower().replace(" ", "_")
    # Clean leading brackets or weights if accidentally passed
    clean_q = re.sub(r"^[\(\[\{\s]+", "", clean_q)
    clean_q = clean_q.split(":")[0].strip()
    clean_q = re.sub(r"[\)\]\}\s]+$", "", clean_q)

    if not clean_q:
        return []

    prefix_matches = []
    contain_matches = []

    for item in all_tags:
        if category is not None and item["category"] != category:
            continue

        tag_lower = item["tag"].lower()
        if tag_lower.startswith(clean_q):
            prefix_matches.append(item)
            if len(prefix_matches) >= limit:
                break
        elif clean_q in tag_lower and len(contain_matches) < limit:
            contain_matches.append(item)

    if len(prefix_matches) < limit:
        needed = limit - len(prefix_matches)
        return prefix_matches + contain_matches[:needed]
    return prefix_matches[:limit]


def get_tag_info(tag: str, csv_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Lookup exact tag metadata."""
    load_danbooru_tags(csv_path)
    normalized = tag.strip().lower().replace(" ", "_")
    normalized = re.sub(r"^[\(\[\{\s]+", "", normalized)
    normalized = normalized.split(":")[0].strip()
    normalized = re.sub(r"[\)\]\}\s]+$", "", normalized)
    if _TAG_DICT and normalized in _TAG_DICT:
        return _TAG_DICT[normalized]
    return None


def extract_tags_from_prompt(prompt: str) -> List[Tuple[str, str, Optional[float]]]:
    """
    Parses prompt string into structured tags.
    Returns list of (clean_tag_name, full_token, weight_or_none).
    """
    results = []
    tokens = [t.strip() for t in re.split(r",|\n", prompt) if t.strip()]
    for token in tokens:
        inner = re.sub(r"^[\(\[\{\s]+", "", token)
        inner = re.sub(r"[\)\]\}\s]+$", "", inner).strip()
        weight = None
        if ":" in inner:
            parts = inner.rsplit(":", 1)
            raw_tag = parts[0].strip()
            try:
                weight = float(parts[1])
            except ValueError:
                raw_tag = inner
        else:
            raw_tag = inner

        clean_tag = raw_tag.lower().replace(" ", "_")
        results.append((clean_tag, token, weight))
    return results


def validate_prompt(prompt: str, csv_path: Optional[Path] = None) -> Dict[str, Any]:
    """Validates which tags in a prompt are known Danbooru tags and flags unknown/typos."""
    load_danbooru_tags(csv_path)
    parsed = extract_tags_from_prompt(prompt)
    known = []
    unknown = []

    for clean_tag, raw_token, weight in parsed:
        if not clean_tag:
            continue
        info = get_tag_info(clean_tag, csv_path)
        if info:
            known.append({
                "tag": clean_tag,
                "raw": raw_token,
                "weight": weight,
                "category": info["category_name"],
                "count": info["count"]
            })
        else:
            suggestions = search_tags(clean_tag, limit=3, csv_path=csv_path)
            unknown.append({
                "tag": clean_tag,
                "raw": raw_token,
                "weight": weight,
                "suggestions": [s["tag"] for s in suggestions]
            })

    return {
        "total_tags": len(parsed),
        "known_count": len(known),
        "unknown_count": len(unknown),
        "known_tags": known,
        "unknown_tags": unknown
    }


def clean_and_dedup_prompt(prompt: str) -> str:
    """Removes duplicate tags while preserving order and the highest weight."""
    parsed = extract_tags_from_prompt(prompt)
    seen = {}
    ordered_keys = []

    for clean_tag, raw_token, weight in parsed:
        if not clean_tag:
            continue
        if clean_tag not in seen:
            seen[clean_tag] = (raw_token, weight or 1.0)
            ordered_keys.append(clean_tag)
        else:
            cur_weight = seen[clean_tag][1]
            new_weight = weight or 1.0
            if new_weight > cur_weight:
                seen[clean_tag] = (raw_token, new_weight)

    return ", ".join(seen[k][0] for k in ordered_keys)


def main():
    parser = argparse.ArgumentParser(description="Danbooru Tag Helper & CLI Tool")
    subparsers = parser.add_subparsers(dest="command")

    search_p = subparsers.add_parser("search", help="Search Danbooru tags")
    search_p.add_argument("query", help="Query string or prefix")
    search_p.add_argument("-l", "--limit", type=int, default=15, help="Max results")
    search_p.add_argument("-c", "--category", type=int, choices=[0, 1, 3, 4, 5], help="Tag category filter")

    check_p = subparsers.add_parser("check", help="Check and validate prompt tags")
    check_p.add_argument("prompt", help="Full prompt string")

    clean_p = subparsers.add_parser("clean", help="Deduplicate prompt tags")
    clean_p.add_argument("prompt", help="Full prompt string")

    subparsers.add_parser("stats", help="Show CSV tag database stats")

    args = parser.parse_args()

    if args.command == "search":
        results = search_tags(args.query, limit=args.limit, category=args.category)
        print(f"\nFound {len(results)} matches for '{args.query}':")
        print(f"{'TAG':<35} {'CATEGORY':<12} {'COUNT':>10}")
        print("-" * 60)
        for r in results:
            print(f"{r['tag']:<35} {r['category_name']:<12} {r['count']:>10,}")

    elif args.command == "check":
        res = validate_prompt(args.prompt)
        print(f"\nPrompt Validation ({res['known_count']}/{res['total_tags']} tags verified in Danbooru):")
        if res["known_tags"]:
            print("\n[VERIFIED TAGS]")
            for t in res["known_tags"]:
                w_str = f" (weight: {t['weight']})" if t['weight'] else ""
                print(f"  ✓ {t['tag']:<28} [{t['category']}] ({t['count']:,} posts){w_str}")
        if res["unknown_tags"]:
            print("\n[UNKNOWN / POTENTIAL TYPOS]")
            for t in res["unknown_tags"]:
                sugg_str = f" -> suggestions: {', '.join(t['suggestions'])}" if t['suggestions'] else ""
                print(f"  ✗ {t['tag']:<28}{sugg_str}")

    elif args.command == "clean":
        cleaned = clean_and_dedup_prompt(args.prompt)
        print("\nCleaned Prompt:")
        print(cleaned)

    elif args.command == "stats":
        tags = load_danbooru_tags()
        cat_counts = {}
        for t in tags:
            c = t["category_name"]
            cat_counts[c] = cat_counts.get(c, 0) + 1
        print(f"\nTotal Danbooru Tags Loaded: {len(tags):,}")
        for c, count in sorted(cat_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {c:<12}: {count:>6,} tags")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
