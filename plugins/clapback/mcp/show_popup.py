#!/usr/bin/env python3
"""Pop up an animated reaction GIF in a borderless, always-on-top window.

Spawned as a detached subprocess by the MCP server so the server stays
responsive. Auto-destroys after N ms. No window chrome, no taskbar entry,
sized to the gif, anchored bottom-right by default.

Usage:
    python show_popup.py <gif-path> [duration_ms] [anchor]

  duration_ms  default 4000
  anchor       "br" (bottom-right, default), "bl", "tr", "tl", "center"

Relies on stdlib Tk only. If the gif has one unreadable frame (rare, e.g.
LZW-free encoding), falls back to showing a single frame. If Pillow is
available, uses it for more robust frame decoding.
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path

DEFAULT_DURATION_MS = 4000
MAX_DIM = 420  # cap huge gifs so popups stay small


def _terminal_rect_windows() -> tuple[int, int, int, int] | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:  # noqa: BLE001
        return None


def _terminal_rect_linux() -> tuple[int, int, int, int] | None:
    """Read the active window rect on X11 / XWayland via xdotool.

    Pure Wayland apps are invisible to xdotool, but the vast majority of
    terminals on Ubuntu (GNOME Terminal, Konsole, Alacritty, Kitty, WezTerm,
    xterm) run on X11 or XWayland so this works in practice.

    Requires `xdotool` on PATH. If missing, return None and let the caller
    fall back to screen-based positioning.
    """
    if sys.platform != "linux":
        return None
    try:
        import subprocess
        out = subprocess.check_output(
            ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    info: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip()
    try:
        x = int(info["X"]); y = int(info["Y"])
        w = int(info["WIDTH"]); h = int(info["HEIGHT"])
    except (KeyError, ValueError):
        return None
    return x, y, x + w, y + h


def _terminal_rect() -> tuple[int, int, int, int] | None:
    return _terminal_rect_windows() or _terminal_rect_linux()


def _load_frames_stdlib(path: str) -> tuple[list[tk.PhotoImage], int, int]:
    frames: list[tk.PhotoImage] = []
    i = 0
    while True:
        try:
            frames.append(tk.PhotoImage(file=path, format=f"gif -index {i}"))
            i += 1
        except tk.TclError:
            break
    if not frames:
        return frames, 0, 0
    w, h = frames[0].width(), frames[0].height()
    factor = max(1, (max(w, h) + MAX_DIM - 1) // MAX_DIM)
    if factor > 1:
        frames = [f.subsample(factor, factor) for f in frames]
        w, h = frames[0].width(), frames[0].height()
    return frames, w, h


def _load_frames_pillow(path: str) -> tuple[list[tk.PhotoImage], int, int] | None:
    try:
        from PIL import Image, ImageSequence, ImageTk
    except ImportError:
        return None
    frames: list[tk.PhotoImage] = []
    with Image.open(path) as im:
        for f in ImageSequence.Iterator(im):
            im2 = f.convert("RGBA")
            if max(im2.size) > MAX_DIM:
                im2.thumbnail((MAX_DIM, MAX_DIM))
            frames.append(ImageTk.PhotoImage(im2))
    if not frames:
        return None
    return frames, frames[0].width(), frames[0].height()


def _position(
    root: tk.Tk,
    w: int,
    h: int,
    anchor: str,
    term: tuple[int, int, int, int] | None,
) -> str:
    """Pick on-screen coords for the popup.

    Prefer the parent terminal's rect (captured BEFORE Tk created its own
    window, otherwise the Tk window itself becomes the foreground). Fall back
    to the whole screen when we can't read it.

    When anchored inside the terminal, we use a deliberately generous inset
    (~60px) so the popup is clearly *inside* the terminal content area, not
    hugging the window edge — otherwise a maximised terminal makes the popup
    look like it's on the screen edge rather than in Claude Code.
    """
    inset = int(os.environ.get("CLAPBACK_INSET", "60"))
    screen_margin = 16
    if term:
        left, top, right, bottom = term
        if anchor == "tl":
            x, y = left + inset, top + inset
        elif anchor == "tr":
            x, y = right - w - inset, top + inset
        elif anchor == "bl":
            x, y = left + inset, bottom - h - inset
        elif anchor == "center":
            x, y = (left + right - w) // 2, (top + bottom - h) // 2
        else:  # br / term / default
            x, y = right - w - inset, bottom - h - inset
        return f"{w}x{h}+{max(0, x)}+{max(0, y)}"

    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    if anchor == "bl":
        x, y = screen_margin, sh - h - screen_margin - 60
    elif anchor == "tr":
        x, y = sw - w - screen_margin, screen_margin
    elif anchor == "tl":
        x, y = screen_margin, screen_margin
    elif anchor == "center":
        x, y = (sw - w) // 2, (sh - h) // 2
    else:
        x, y = sw - w - screen_margin, sh - h - screen_margin - 60
    return f"{w}x{h}+{max(0, x)}+{max(0, y)}"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: show_popup.py <gif-path> [duration_ms] [anchor]", file=sys.stderr)
        return 2
    path = sys.argv[1]
    duration_ms = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_DURATION_MS
    anchor = sys.argv[3] if len(sys.argv) >= 4 else "br"

    if not Path(path).exists():
        print(f"not found: {path}", file=sys.stderr)
        return 2

    # Capture the foreground window rect BEFORE Tk creates its own top-level —
    # otherwise the popup itself becomes the foreground and we'd measure that.
    term_rect = _terminal_rect()

    root = tk.Tk()
    # Pillow handles animated gifs accurately (disposal, alpha, LZW); stdlib
    # Tk works for most Giphy gifs without any dependency.
    loaded = _load_frames_pillow(path)
    if loaded is None:
        loaded = _load_frames_stdlib(path)
        if not loaded[0]:
            try:
                single = tk.PhotoImage(file=path)
                loaded = ([single], single.width(), single.height())
            except tk.TclError as e:
                print(f"tk can't decode gif: {e}", file=sys.stderr)
                root.destroy()
                return 3
    frames, w, h = loaded

    root.overrideredirect(True)  # no title bar / borders
    root.attributes("-topmost", True)
    try:
        # Hide from Windows taskbar. On macOS/Linux this is a no-op or harmless.
        root.attributes("-toolwindow", True)
    except tk.TclError:
        pass
    root.configure(background="black")
    root.geometry(_position(root, w, h, anchor, term_rect))

    label = tk.Label(root, image=frames[0], borderwidth=0, highlightthickness=0, background="black")
    label.pack()

    # Derive per-frame delay: spread duration across frames, clamped to [40, 120]ms.
    if len(frames) > 1:
        per_frame = max(40, min(120, duration_ms // len(frames)))
    else:
        per_frame = duration_ms

    state = {"i": 0}

    def tick() -> None:
        state["i"] = (state["i"] + 1) % len(frames)
        label.configure(image=frames[state["i"]])
        root.after(per_frame, tick)

    if len(frames) > 1:
        root.after(per_frame, tick)

    root.after(duration_ms, root.destroy)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
