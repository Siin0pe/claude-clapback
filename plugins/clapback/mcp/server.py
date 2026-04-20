#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp>=1.2.0",
#   "pillow>=10.0",
# ]
# ///
"""clapback MCP server — pops up an animated reaction GIF in a small
borderless always-on-top window, then auto-closes.

Why a popup: Claude Code's TUI repaints over anything written to the terminal
device (CONOUT$ / /dev/tty) and doesn't render terminal graphics protocols,
so inline display in the transcript isn't possible. A tiny Tkinter popup
spawned as a detached subprocess is the cleanest cross-platform path: stdlib
only on Windows and macOS; Linux needs `python3-tk` once.
"""
from __future__ import annotations

import functools
import json
import os
import random
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from _categories import CATEGORIES, CATEGORY_SET

SCRIPT_DIR = Path(__file__).resolve().parent
BANK_PATH = SCRIPT_DIR / "gifs.json"
POPUP_SCRIPT = SCRIPT_DIR / "show_popup.py"
CACHE_DIR = Path(
    os.environ.get("CLAPBACK_CACHE_DIR")
    or (Path.home() / ".cache" / "claude-clapback")
)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DURATION_MS = int(os.environ.get("CLAPBACK_DURATION_MS", "4000"))
DEFAULT_ANCHOR = os.environ.get("CLAPBACK_ANCHOR", "br")  # br bl tr tl center


@functools.lru_cache(maxsize=1)
def _load_bank() -> dict[str, list[str]]:
    if not BANK_PATH.exists():
        return {c: [] for c in CATEGORIES}
    with BANK_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: list(v) for k, v in data.items()}


def _giphy_url_from_id(gif_id: str) -> str:
    if gif_id.startswith(("http://", "https://")):
        return gif_id
    return f"https://media.giphy.com/media/{gif_id}/giphy.gif"


def _fetch_gif(gif_id: str) -> Path:
    local = CACHE_DIR / f"{gif_id}.gif"
    if local.exists() and local.stat().st_size > 0:
        return local
    req = urllib.request.Request(
        _giphy_url_from_id(gif_id),
        headers={
            "User-Agent": "Mozilla/5.0 (clapback-mcp/1.0)",
            "Accept": "image/gif,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp, local.open("wb") as out:
        # Stream to disk; tmp -> rename would be safer but urllib never hands
        # us a partial-on-error response, so a direct write is fine.
        while chunk := resp.read(65536):
            out.write(chunk)
    return local


@functools.lru_cache(maxsize=1)
def _tk_available() -> bool:
    # Importing tkinter is cheap and deterministic — it's stdlib, only missing
    # on slim Linux builds where the distro split it into `python3-tk`.
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@functools.lru_cache(maxsize=1)
def _popup_python() -> str:
    """Pick the Python interpreter that spawns the popup.

    On Windows, pythonw.exe is the windowed (console-less) variant — using it
    guarantees zero console flash when Tk launches. Fall back to sys.executable
    when pythonw.exe isn't adjacent (rare, e.g. stripped builds).
    """
    if os.name == "nt":
        exe = Path(sys.executable)
        for name in ("pythonw.exe", "pythonw3.exe"):
            cand = exe.with_name(name)
            if cand.exists():
                return str(cand)
    return sys.executable


def _spawn_popup(gif_path: Path) -> tuple[bool, str]:
    if not _tk_available():
        return (
            False,
            "tkinter not available. On Linux: sudo apt install python3-tk "
            "(or dnf install python3-tkinter).",
        )
    if not POPUP_SCRIPT.exists():
        return False, f"popup script missing: {POPUP_SCRIPT}"

    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        # CREATE_NO_WINDOW | DETACHED_PROCESS — belt + braces alongside pythonw.
        creationflags = 0x08000000 | 0x00000008
    else:
        start_new_session = True

    try:
        subprocess.Popen(
            [_popup_python(), str(POPUP_SCRIPT), str(gif_path),
             str(DEFAULT_DURATION_MS), DEFAULT_ANCHOR],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=start_new_session,
            close_fds=True,
        )
    except OSError as e:
        return False, f"could not spawn popup: {e}"
    return True, "spawned"


mcp = FastMCP("clapback")


@mcp.tool()
def show_reaction(category: str) -> dict[str, Any]:
    """Pop up a sarcastic reaction GIF for a few seconds, bottom-right.

    Args:
        category: One of:
            confused, skeptical, disappointed, shocked, deadpan, how_dare,
            monkey_puppet, thinking, no, eyeroll, really, cringe, laughing,
            judgmental.

    The popup is borderless, always-on-top, auto-closes after ~4s. The MCP
    response only carries a status dict; the gif itself lives in the spawned
    subprocess's window.
    """
    cat = category.strip().lower()
    if cat not in CATEGORY_SET:
        return {
            "ok": False,
            "error": f"unknown category '{category}'",
            "valid": list(CATEGORIES),
        }

    pool = _load_bank().get(cat) or []
    if not pool:
        return {
            "ok": False,
            "error": f"no gifs for '{cat}' — run rebuild_bank.py to populate.",
            "category": cat,
        }

    order = list(pool)
    random.shuffle(order)
    last_err: str | None = None
    for gif_id in order[:5]:
        try:
            path = _fetch_gif(gif_id)
        except Exception as e:  # noqa: BLE001
            last_err = f"fetch failed for {gif_id}: {e}"
            continue
        ok, msg = _spawn_popup(path)
        if ok:
            return {"ok": True, "category": cat, "gif_id": gif_id}
        last_err = msg
        if "tkinter not available" in msg or "popup script missing" in msg:
            return {"ok": False, "category": cat, "gif_id": gif_id, "error": msg}

    return {"ok": False, "category": cat, "error": last_err or "unknown failure"}


@mcp.tool()
def list_categories() -> dict[str, Any]:
    """Return the list of supported reaction categories and pool sizes."""
    bank = _load_bank()
    return {
        "categories": list(CATEGORIES),
        "counts": {c: len(bank.get(c, [])) for c in CATEGORIES},
    }


@mcp.tool()
def diagnose() -> dict[str, Any]:
    """Report environment diagnostics — useful when the user reports issues."""
    info: dict[str, Any] = {
        "tk_available": _tk_available(),
        "popup_script_exists": POPUP_SCRIPT.exists(),
        "cache_dir": str(CACHE_DIR),
        "bank_path": str(BANK_PATH),
        "bank_exists": BANK_PATH.exists(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "duration_ms": DEFAULT_DURATION_MS,
        "anchor": DEFAULT_ANCHOR,
    }
    if sys.platform == "linux":
        # xdotool enables terminal-anchored popups on X11 / XWayland. If it's
        # missing, popups still work but anchor to the screen edge.
        info["xdotool"] = shutil.which("xdotool")
    if BANK_PATH.exists():
        bank = _load_bank()
        info["bank_counts"] = {c: len(bank.get(c, [])) for c in CATEGORIES}
    return info


if __name__ == "__main__":
    mcp.run()
