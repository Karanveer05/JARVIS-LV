# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
core/lessons.py — what JARVIS has learned from being corrected.

When the user corrects it, or says how they want it to behave from now on ("always answer shorter", "don't call me
sir", "when I say open the usual, open Spotify"), it saves ONE short rule here. Every rule is added to the model's
instructions at the start of each session, so the correction sticks. This is the "learn from my corrections" part of
self-improvement; it is stored in memory/lessons.json, shown and deletable under Settings > What I learned.

Because the rules go into the model's instructions, they are screened: no secrets, no rules that tell the model to
ignore its instructions or switch off safety, and a length cap. The model is also told to save a lesson only from the
user's own words — never from a web page or other tool output.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE = _base() / "memory" / "lessons.json"
MAX_LESSONS = 30
MAX_LEN = 200
_lock = threading.Lock()
_BAD = re.compile(
    r"ignore (?:all |any |the |your )?(?:previous|prior|above|earlier|system)|system prompt|your instructions|"
    r"disable (?:the )?(?:safety|confirm\w*|checks?|guard\w*)|turn off (?:the )?(?:safety|confirm\w*)|without (?:asking|confirm\w*)|"
    r"api[ _-]?key|password|passcode|secret|token|credential|private key|reveal|jailbreak|pretend (?:you|to)", re.I)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower()).split())


def load() -> list[dict]:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict) and d.get("rule")] if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STORE)


def add(rule: str) -> tuple[bool, str]:
    """Save one lesson. Returns (saved, message)."""
    rule = " ".join((rule or "").split()).strip(" \"'")
    if len(rule) < 8:
        return False, "That is too short to be a useful lesson."
    if len(rule) > MAX_LEN:
        return False, f"A lesson has to fit in {MAX_LEN} characters — say it more briefly."
    if _BAD.search(rule):
        return False, "I can't save that as a lesson (it touches secrets or my own safety rules)."
    with _lock:
        items = load()
        key = _norm(rule)
        for it in items:                                   # the same rule again (capitals, punctuation aside) — not merely a similar one
            if _norm(it["rule"]) == key or difflib.SequenceMatcher(None, _norm(it["rule"]), key).ratio() >= 0.97:
                it["when"] = datetime.now().strftime("%Y-%m-%d")
                _save(items)
                return True, "I already had that lesson; kept it fresh."
        items.append({"rule": rule, "when": datetime.now().strftime("%Y-%m-%d")})
        _save(items[-MAX_LESSONS:])
    return True, f"Learned: {rule}"


def forget(text: str) -> str:
    """Forget lessons matching `text`, or all of them for 'all'."""
    t = (text or "").strip().lower()
    with _lock:
        items = load()
        if t in ("all", "everything", "*"):
            _save([])
            return f"Forgot all {len(items)} lessons." if items else "There were no lessons to forget."
        keep = [it for it in items if t not in it["rule"].lower()] if t else items
        gone = len(items) - len(keep)
        if not t:
            return "Which lesson? Give a few words from it, or say all."
        _save(keep)
    return f"Forgot {gone} lesson{'s' if gone != 1 else ''} about “{text.strip()}”." if gone else f"I have no lesson about “{text.strip()}”."


def prompt_block() -> str:
    """The lessons, as a block for the model's instructions ('' when there are none)."""
    items = load()
    if not items:
        return ""
    lines = "\n".join(f"- {it['rule']}" for it in items[-MAX_LESSONS:])
    return ("[LESSONS THE USER HAS TAUGHT YOU]\n"
            "These are the user's own standing preferences. Follow them unless the user says otherwise right now.\n"
            f"{lines}\n\n")
