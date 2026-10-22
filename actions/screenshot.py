# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
screenshot — the screenshot jobs people actually ask for, plus screen recording.

Full screen, one monitor, the active window or a region; an optional delay so
the user can set the scene; copy straight to the clipboard; open the result.
Files go to Pictures/Screenshots, never overwriting an earlier one. Recording
writes an .mp4 to Videos until told to stop.
"""
import platform
import threading
import time
from datetime import datetime
from pathlib import Path

from actions._paths import known_folder, unique_path

_MAX_DELAY = 60
_MAX_RECORD_SECONDS = 15 * 60
_FPS = 10

_rec_lock = threading.Lock()
_rec: dict | None = None


def _out_dir() -> Path:
    d = known_folder("pictures") / "Screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _active_window_box() -> dict | None:
    if platform.system() != "Windows":
        return None
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        # Maximised windows overhang the screen by the border width.
        l, t = max(l, 0), max(t, 0)
        if r - l < 10 or b - t < 10:
            return None
        return {"left": l, "top": t, "width": r - l, "height": b - t}
    except Exception:
        return None


def _grab(target: str, region: dict | None):
    import mss
    from PIL import Image
    with mss.mss() as sct:
        if region:
            box = region
        elif target == "window" and _active_window_box():
            box = _active_window_box()
        elif target.startswith("monitor"):
            n = int(target[7:] or 1) if target[7:].strip().isdigit() else 1
            box = sct.monitors[n] if 1 <= n < len(sct.monitors) else sct.monitors[1]
        else:
            box = sct.monitors[0]          # every monitor stitched together
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _copy_to_clipboard(img) -> bool:
    """Put the image on the clipboard so it can be pasted into chats and documents."""
    if platform.system() != "Windows":
        return False
    try:
        import io
        import win32clipboard
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "BMP")
        dib = buf.getvalue()[14:]          # a clipboard DIB is a BMP without its file header
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def _open(path: Path) -> None:
    import os
    import subprocess
    if platform.system() == "Windows":
        os.startfile(str(path))
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _take(p: dict) -> str:
    target = (p.get("target") or "screen").lower().strip()
    region = None
    if all(p.get(k) is not None for k in ("left", "top", "width", "height")):
        region = {k: int(p[k]) for k in ("left", "top", "width", "height")}
        if region["width"] < 1 or region["height"] < 1:
            return "That region has no size."
    delay = max(0, min(int(p.get("delay_seconds") or 0), _MAX_DELAY))
    if delay:
        time.sleep(delay)

    img = _grab(target, region)
    notes = []
    if p.get("copy_to_clipboard") or p.get("clipboard_only"):
        notes.append("copied to the clipboard" if _copy_to_clipboard(img)
                     else "couldn't copy to the clipboard on this system")
        if p.get("clipboard_only") and notes[-1].startswith("copied"):
            return f"Screenshot {notes[0]}."
    path = unique_path(_out_dir() / f"Screenshot_{_stamp()}.png")
    img.save(path)
    notes.insert(0, f"saved to {path}")
    if p.get("open"):
        _open(path)
        notes.append("opened")
    return "Screenshot " + ", ".join(notes) + "."


def _record_loop(path: Path, stop: threading.Event, monitor: int) -> None:
    import cv2
    import mss
    import numpy as np
    writer = None
    try:
        with mss.mss() as sct:
            box = sct.monitors[monitor if 0 < monitor < len(sct.monitors) else 1]
            size = (box["width"], box["height"])
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), _FPS, size)
            started, frame_time = time.time(), 1 / _FPS
            while not stop.is_set() and time.time() - started < _MAX_RECORD_SECONDS:
                t0 = time.time()
                frame = np.array(sct.grab(box))[:, :, :3]
                writer.write(frame)
                time.sleep(max(0, frame_time - (time.time() - t0)))
    finally:
        if writer is not None:
            writer.release()


def _record(action: str, p: dict) -> str:
    global _rec
    with _rec_lock:
        if action == "start_recording":
            if _rec and _rec["thread"].is_alive():
                return "Already recording. Say stop recording to finish."
            d = known_folder("videos") / "Recordings"
            d.mkdir(parents=True, exist_ok=True)
            path = unique_path(d / f"Recording_{_stamp()}.mp4")
            stop = threading.Event()
            th = threading.Thread(target=_record_loop,
                                  args=(path, stop, int(p.get("monitor") or 1)), daemon=True)
            th.start()
            _rec = {"thread": th, "stop": stop, "path": path}
            return (f"Recording the screen (no sound), up to {_MAX_RECORD_SECONDS // 60} minutes. "
                    "Say stop recording when done.")
        if not _rec:
            return "Nothing is being recorded."
        _rec["stop"].set()
        _rec["thread"].join(timeout=10)
        path, _rec = _rec["path"], None
        return f"Recording saved to {path}." if path.exists() else "The recording produced no file."


def _recent(n: int = 5) -> str:
    files = sorted(_out_dir().glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    if not files:
        return "No screenshots saved yet."
    return "Latest screenshots:\n" + "\n".join(f"- {f}" for f in files)


def screenshot(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "take").lower().strip()
    try:
        if action in ("take", "capture"):
            return _take(p)
        if action in ("start_recording", "stop_recording"):
            return _record(action, p)
        if action == "recent":
            return _recent()
        if action == "open_folder":
            _open(_out_dir())
            return f"Opened {_out_dir()}."
        return "Unknown action. Use take, start_recording, stop_recording, recent or open_folder."
    except Exception as e:
        return f"Screenshot error: {e}"


TOOL = {
    "name": "screenshot",
    "description": (
        "Screenshots and screen recording. Use for 'take a screenshot', 'screenshot this window', "
        "'copy a screenshot to my clipboard', 'screenshot in 5 seconds', 'screenshot my second "
        "monitor', 'record my screen', 'stop recording', 'show my recent screenshots'. Saves to "
        "Pictures/Screenshots. To look at and describe the screen, use the screen-reading tool instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "take | start_recording | stop_recording | recent | open_folder (default take)"},
            "target": {"type": "STRING", "description": "take: screen (all monitors) | window (the active one) | monitor1 | monitor2"},
            "left": {"type": "INTEGER", "description": "take: region left edge in pixels"},
            "top": {"type": "INTEGER", "description": "take: region top edge"},
            "width": {"type": "INTEGER", "description": "take: region width"},
            "height": {"type": "INTEGER", "description": "take: region height"},
            "delay_seconds": {"type": "INTEGER", "description": "take: wait this long first (max 60)"},
            "copy_to_clipboard": {"type": "BOOLEAN", "description": "take: also put the image on the clipboard"},
            "clipboard_only": {"type": "BOOLEAN", "description": "take: clipboard only, no file"},
            "open": {"type": "BOOLEAN", "description": "take: open the picture afterwards"},
            "monitor": {"type": "INTEGER", "description": "start_recording: which monitor (default 1)"},
        },
        "required": [],
    },
    "handler": screenshot,
}
