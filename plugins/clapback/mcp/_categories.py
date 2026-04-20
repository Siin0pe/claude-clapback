"""Single source of truth for the 14 reaction categories.

Imported by both server.py (validates tool input) and rebuild_bank.py
(drives scraping). Adding or renaming a category happens here, once.
"""
from __future__ import annotations

CATEGORIES: tuple[str, ...] = (
    "confused",
    "skeptical",
    "disappointed",
    "shocked",
    "deadpan",
    "how_dare",
    "monkey_puppet",
    "thinking",
    "no",
    "eyeroll",
    "really",
    "cringe",
    "laughing",
    "judgmental",
)

CATEGORY_SET: frozenset[str] = frozenset(CATEGORIES)
