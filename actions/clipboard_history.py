"""
clipboard_history — remembers what you copied so you can get it back.

A background thread notes each new clipboard text. Privacy choices:
  * history lives in RAM only — nothing is written to disk;
  * it keeps the last 30 entries and is gone when JARVIS closes;
  * text that looks like a password or API key (one long unbroken token mixing
    letters, digits and symbols) is never recorded.
"""
import re
import threading
import time
from collections import deque

try:
    import pyperclip
    _OK = True
except ImportError:
    _OK = False

_MAX_ITEMS = 30
_MAX_CHARS = 5000
_history: deque[dict] = deque(maxlen=_MAX_ITEMS)
_lock = threading.Lock()
_started = False
_last_seen = None


def _looks_secret(text: str) -> bool:
    t = text.strip()
    if " " in t or "\n" in t or len(t) < 12 or len(t) > 200:
        return False
    classes = sum(bool(re.search(p, t)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w]"))
    return classes >= 3 or bool(re.fullmatch(r"[A-Za-z0-9_\-]{24,}", t))


def _record(text: str) -> None:
    if not text or not text.strip() or _looks_secret(text):
        return
    text = text[:_MAX_CHARS]
    with _lock:
        if _history and _history[-1]["text"] == text:
            return
        _history.append({"text": text, "at": time.strftime("%H:%M")})


def _poll() -> None:
    global _last_seen
    while True:
        try:
            cur = pyperclip.paste()
            if cur != _last_seen:
                _last_seen = cur
                _record(cur)
        except Exception:
            pass
        time.sleep(1.0)


def _start() -> None:
    global _started, _last_seen
    if _started or not _OK:
        return
    _started = True
    try:
        _last_seen = pyperclip.paste()      # what was copied before we started is not history
    except Exception:
        _last_seen = None
    threading.Thread(target=_poll, daemon=True, name="clipboard-history").start()


# Start watching as soon as the action is discovered, so "what did I copy
# earlier" works from the first minute of the session.
_start()


def _short(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= 80 else t[:77] + "..."


def clipboard_history(parameters: dict, player=None) -> str:
    global _last_seen
    if not _OK:
        return "Clipboard access needs the pyperclip package."
    _start()
    action = str(parameters.get("action", "list")).strip().lower()
    with _lock:
        items = list(_history)

    if action in ("list", "show", "history"):
        if not items:
            return "Nothing has been copied since JARVIS started."
        recent = list(reversed(items))[:10]
        return "\n".join(f"{i}. [{e['at']}] {_short(e['text'])}" for i, e in enumerate(recent, 1))

    if action in ("search", "find"):
        q = str(parameters.get("text", "")).strip().lower()
        hits = [e for e in reversed(items) if q and q in e["text"].lower()][:10]
        return "\n".join(f"[{e['at']}] {_short(e['text'])}" for e in hits) if hits else f"Nothing copied matches '{q}'."

    if action in ("get", "paste", "restore", "copy"):
        try:
            n = int(parameters.get("index", 1) or 1)
        except (TypeError, ValueError):
            return "Which entry? Give its number from the list (1 is the most recent)."
        recent = list(reversed(items))
        if not 1 <= n <= len(recent):
            return f"There is no entry {n}; I have {len(recent)}."
        pyperclip.copy(recent[n - 1]["text"])
        _last_seen = recent[n - 1]["text"]
        return f"Put entry {n} back on the clipboard: {_short(recent[n - 1]['text'])}"

    if action in ("clear", "forget", "wipe"):
        with _lock:
            _history.clear()
        return "Clipboard history cleared."

    return "Clipboard actions are: list, search, get, clear."


TOOL = {
    "name": "clipboard_history",
    "description": (
        "Remembers recent copied text (kept only in memory, passwords skipped). Use "
        "for 'what did I copy earlier', 'bring back the second thing I copied', "
        "'find the link I copied', 'clear clipboard history'. For one simple "
        "copy/paste keystroke use computer_settings instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | search | get | clear (default list)"},
            "index": {"type": "STRING", "description": "For get: entry number, 1 = most recent"},
            "text": {"type": "STRING", "description": "For search: words to look for"},
        },
        "required": [],
    },
    "handler": clipboard_history,
}
