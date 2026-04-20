#!/usr/bin/env python3
"""
rebuild_bank.py — rescrape Giphy explore pages and rebuild gifs.json.

No API key required. We hit https://giphy.com/explore/<slug> directly and
extract gif IDs from `data-giphy-id="..."` attributes. Those IDs plug into
the stable media URL:

    https://media.giphy.com/media/{ID}/giphy.gif

Usage:
    python rebuild_bank.py              # refresh all categories
    python rebuild_bank.py confused no  # refresh a subset
    python rebuild_bank.py --dry-run    # print what would change

Each category pulls from one or more explore slugs. We merge, dedupe, cap at
~10 IDs per category (the floor requested in the spec) and write back to
gifs.json. If the network fails for a category, we keep whatever IDs were
already there so the bank never regresses to empty.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from _categories import CATEGORIES

SCRIPT_DIR = Path(__file__).resolve().parent
BANK_PATH = SCRIPT_DIR / "gifs.json"

# First slug in each list is the canonical "best match" — ordered so the
# emotional fit stays tight when we dedupe.
CATEGORY_SOURCES: dict[str, list[str]] = {
    "confused": ["confused", "what", "huh"],
    "skeptical": ["skeptical", "side-eye", "suspicious"],
    "disappointed": ["disappointed", "letdown", "sad-face"],
    "shocked": ["shocked", "gasp", "jaw-drop"],
    "deadpan": ["deadpan", "blank-stare", "unimpressed"],
    "how_dare": ["how-dare-you", "greta-thunberg", "outrage"],
    "monkey_puppet": ["monkey-puppet", "awkward-monkey", "awkward-look"],
    "thinking": ["thinking", "hmm", "pondering"],
    "no": ["no", "nope", "shake-head-no"],
    "eyeroll": ["eye-roll", "rolling-eyes", "annoyed"],
    "really": ["really", "are-you-serious", "seriously"],
    "cringe": ["cringe", "yikes", "awkward"],
    "laughing": ["laughing", "lol", "dying-laughing"],
    "judgmental": ["judging", "judgmental", "side-eye"],
}
assert set(CATEGORY_SOURCES) == set(CATEGORIES), "CATEGORY_SOURCES drifted from _categories.CATEGORIES"

PER_CATEGORY_MIN = 10
PER_SOURCE_LIMIT = 20  # never pull more than 20 ids from one slug
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 1.0  # seconds between fetches, be polite

ID_PATTERN = re.compile(r'data-giphy-id="([A-Za-z0-9]{8,32})"')
STICKER_PATTERN = re.compile(
    r'data-giphy-id="([A-Za-z0-9]{8,32})"[^>]*data-giphy-is-sticker="true"'
)


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_ids(html: str) -> list[str]:
    stickers = set(STICKER_PATTERN.findall(html))  # transparent PNGs, skip
    return [gid for gid in dict.fromkeys(ID_PATTERN.findall(html)) if gid not in stickers]


def scrape_category(slugs: list[str]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for slug in slugs:
        url = f"https://giphy.com/explore/{slug}"
        try:
            html = fetch(url)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  ! {slug}: {e}", file=sys.stderr)
            continue
        fresh = [gid for gid in extract_ids(html)[:PER_SOURCE_LIMIT] if gid not in seen]
        for gid in fresh:
            seen.add(gid)
            ids.append(gid)
        print(f"  - {slug}: +{len(fresh)} (total {len(ids)})")
        time.sleep(REQUEST_DELAY)
    return ids


def load_bank() -> dict[str, list[str]]:
    if not BANK_PATH.exists():
        return {}
    with BANK_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_bank(bank: dict[str, list[str]]) -> None:
    ordered = {k: bank[k] for k in sorted(bank)}
    with BANK_PATH.open("w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="rebuild the clapback gif bank")
    parser.add_argument(
        "categories",
        nargs="*",
        help="subset of categories to refresh (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print intended changes, don't write gifs.json",
    )
    args = parser.parse_args()

    targets = args.categories or list(CATEGORY_SOURCES.keys())
    unknown = [c for c in targets if c not in CATEGORY_SOURCES]
    if unknown:
        print(f"unknown categories: {unknown}", file=sys.stderr)
        return 2

    bank = load_bank()
    for cat in targets:
        print(f"[{cat}]")
        ids = scrape_category(CATEGORY_SOURCES[cat])
        if len(ids) < PER_CATEGORY_MIN and bank.get(cat):
            # Merge with existing so we never lose coverage on a partial
            # network failure.
            existing = [gid for gid in bank[cat] if gid not in ids]
            ids.extend(existing)
        if not ids:
            print(f"  ! no ids found, keeping previous {len(bank.get(cat, []))}")
            continue
        bank[cat] = ids

    for cat in CATEGORY_SOURCES:
        bank.setdefault(cat, [])

    print("\nsummary:")
    for cat in sorted(CATEGORY_SOURCES):
        print(f"  {cat:<14} {len(bank.get(cat, []))}")

    if args.dry_run:
        print("\n(dry-run: gifs.json not written)")
        return 0

    save_bank(bank)
    print(f"\nwrote {BANK_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
