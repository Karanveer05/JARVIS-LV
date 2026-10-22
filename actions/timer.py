# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
timer — countdown timers and clock-time alarms that live inside JARVIS.

Unlike `reminder` (a Windows Task Scheduler job that survives a restart but needs
an exact date), these start instantly from "10 minutes" or "at 7:30" and speak
when they fire. They live in the running app: if JARVIS is closed they are gone,
and the tool says so when one is set that is long enough for that to matter.
"""
import itertools
import re
import threading
import time
from datetime import datetime, timedelta

_lock = threading.Lock()
_timers: dict[int, dict] = {}
_ids = itertools.count(1)

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}
_MAX_SECONDS = 24 * 3600
_LONG_WARNING = 2 * 3600


def parse_duration(text: str) -> float | None:
    """'10 minutes', '1h30m', '1 hour 15 min', '90' (seconds), '1.5 hours'."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return float(t)
    total, matched = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", t):
        if unit not in _UNITS:
            return None
        total += float(num) * _UNITS[unit]
        matched = True
    return total if matched else None


def parse_clock(text: str, now: datetime | None = None) -> float | None:
    """Seconds until the next occurrence of '7:30', '7:30 pm', '19:05', '7pm'."""
    t = (text or "").strip().lower().replace(".", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    now = now or datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _pretty(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if sec and not h:
        parts.append(f"{sec} second{'s' if sec != 1 else ''}")
    return " ".join(parts) or "0 seconds"


def _fire(tid: int, label: str, speak, player) -> None:
    with _lock:
        if _timers.pop(tid, None) is None:
            return                       # cancelled
    msg = f"Timer finished: {label}" if label else "Timer finished."
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:
        pass
    if player:
        try:
            player.write_log(f"JARVIS: ⏰ {msg}")
        except Exception:
            pass
    if speak:
        try:
            speak(f"[TIMER FIRED] {msg}. Tell the user in one short sentence, in their language.")
        except Exception:
            pass


def _set(parameters: dict, speak, player) -> str:
    when = str(parameters.get("when", "")).strip()
    label = str(parameters.get("label", "")).strip()[:80]
    secs = parse_duration(when)
    if secs is None:
        secs = parse_clock(when)
    if secs is None:
        return ("I could not understand that time. Say a duration like '10 minutes' "
                "or a clock time like '7:30 pm'.")
    if secs < 1:
        return "That is too short for a timer."
    if secs > _MAX_SECONDS:
        return "Timers can run up to 24 hours. For later than that use the reminder tool."

    tid = next(_ids)
    th = threading.Timer(secs, _fire, args=(tid, label, speak, player))
    th.daemon = True
    with _lock:
        _timers[tid] = {"thread": th, "label": label, "ends": time.time() + secs}
    th.start()

    out = f"Timer {tid} set for {_pretty(secs)}" + (f" ({label})" if label else "") + "."
    if secs >= _LONG_WARNING:
        out += " It only runs while JARVIS stays open; for something that must survive a restart use reminder."
    return out


def _list() -> str:
    with _lock:
        items = sorted(_timers.items())
    if not items:
        return "No timers are running."
    now = time.time()
    return "; ".join(
        f"Timer {tid}: {_pretty(max(0, t['ends'] - now))} left" + (f" ({t['label']})" if t["label"] else "")
        for tid, t in items
    )


def _cancel(parameters: dict) -> str:
    raw = str(parameters.get("id", "")).strip().lower()
    with _lock:
        if raw in ("", "all", "every"):
            n = len(_timers)
            for t in _timers.values():
                t["thread"].cancel()
            _timers.clear()
            return f"Cancelled {n} timer{'s' if n != 1 else ''}." if n else "No timers are running."
        try:
            tid = int(re.sub(r"\D", "", raw))
        except ValueError:
            return "Which timer? Give its number, or say all."
        t = _timers.pop(tid, None)
    if t is None:
        return f"There is no timer {tid}."
    t["thread"].cancel()
    return f"Cancelled timer {tid}."


def timer(parameters: dict, player=None, speak=None) -> str:
    action = str(parameters.get("action", "set")).strip().lower()
    if action in ("set", "start", "add", "create"):
        return _set(parameters, speak, player)
    if action in ("list", "status", "show"):
        return _list()
    if action in ("cancel", "stop", "delete", "remove", "clear"):
        return _cancel(parameters)
    return "Timer actions are: set, list, cancel."


TOOL = {
    "name": "timer",
    "description": (
        "Countdown timers and alarms that speak when done. Use for 'set a timer for "
        "10 minutes', 'wake me at 7:30', 'how long is left on my timer', 'cancel the "
        "timer'. For reminders on a specific future DATE or ones that must survive "
        "closing JARVIS, use reminder instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "set | list | cancel (default set)"},
            "when": {"type": "STRING",
                     "description": "For set: a duration ('10 minutes', '1h30m') or a clock time ('7:30 pm')"},
            "label": {"type": "STRING", "description": "Optional name, e.g. 'tea'"},
            "id": {"type": "STRING", "description": "For cancel: the timer number, or 'all'"},
        },
        "required": [],
    },
    "handler": timer,
}
