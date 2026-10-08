"""
notes — voice notes and a to-do list, kept in memory/notes.json on this PC.

Notes and to-dos share one store: a to-do is a note with a done flag. Nothing
leaves the machine. The file is rewritten atomically so a crash mid-save cannot
leave half a list behind.
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE = _base_dir() / "memory" / "notes.json"
_lock = threading.Lock()
_MAX_TEXT = 500


def _load() -> list[dict]:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        # A damaged file is set aside, not silently overwritten.
        try:
            STORE.replace(STORE.with_suffix(".corrupt.json"))
        except Exception:
            pass
        return []


def _save(items: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STORE.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STORE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _next_id(items: list[dict]) -> int:
    return max((i["id"] for i in items), default=0) + 1


def _fmt(i: dict) -> str:
    if i["kind"] == "todo":
        return f"{i['id']}. [{'x' if i['done'] else ' '}] {i['text']}"
    return f"{i['id']}. {i['text']}"


def _find(items: list[dict], params: dict):
    raw = str(params.get("id", "")).strip()
    if raw.isdigit():
        return next((i for i in items if i["id"] == int(raw)), None)
    text = str(params.get("text", "")).strip().lower()
    if text:
        hits = [i for i in items if text in i["text"].lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def notes(parameters: dict, player=None) -> str:
    action = str(parameters.get("action", "list")).strip().lower()
    kind = str(parameters.get("kind", "")).strip().lower()
    kind = "todo" if kind in ("todo", "to-do", "task", "tasks") else ("note" if kind in ("note", "notes") else "")
    text = str(parameters.get("text", "")).strip()[:_MAX_TEXT]

    with _lock:
        items = _load()

        if action in ("add", "save", "create", "new"):
            if not text:
                return "What should I write down?"
            item = {"id": _next_id(items), "kind": kind or "note", "text": text,
                    "done": False, "created": datetime.now().isoformat(timespec="minutes")}
            items.append(item)
            _save(items)
            return f"Saved {'to-do' if item['kind'] == 'todo' else 'note'} {item['id']}: {text}"

        if action in ("list", "show", "read"):
            shown = [i for i in items if not kind or i["kind"] == kind]
            if not shown:
                return "Nothing saved yet." if not kind else f"No {kind}s saved."
            open_first = sorted(shown, key=lambda i: (i["done"], i["id"]))
            return "\n".join(_fmt(i) for i in open_first[:40])

        if action in ("search", "find"):
            q = text.lower()
            hits = [i for i in items if q and q in i["text"].lower()]
            return "\n".join(_fmt(i) for i in hits[:20]) if hits else f"Nothing matches '{text}'."

        if action in ("done", "complete", "finish", "check"):
            it = _find(items, parameters)
            if not it:
                return "Which one? Give its number or a unique part of its text."
            it["done"] = True
            _save(items)
            return f"Marked done: {it['text']}"

        if action in ("delete", "remove"):
            it = _find(items, parameters)
            if not it:
                return "Which one? Give its number or a unique part of its text."
            items.remove(it)
            _save(items)
            return f"Deleted: {it['text']}"

        if action == "clear_done":
            keep = [i for i in items if not i["done"]]
            n = len(items) - len(keep)
            _save(keep)
            return f"Removed {n} finished item{'s' if n != 1 else ''}."

    return "Notes actions are: add, list, search, done, delete, clear_done."


TOOL = {
    "name": "notes",
    "description": (
        "Voice notes and a to-do list saved on this PC. Use for 'note down...', "
        "'remember this idea', 'add X to my to-do list', 'what is on my list', "
        "'mark X done', 'delete note 3'. For facts ABOUT THE USER that you should "
        "remember in conversation, use save_memory instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "add | list | search | done | delete | clear_done"},
            "kind": {"type": "STRING", "description": "note | todo (for add and list)"},
            "text": {"type": "STRING", "description": "The note or task text, or search words"},
            "id": {"type": "STRING", "description": "Item number for done / delete"},
        },
        "required": ["action"],
    },
    "handler": notes,
}
