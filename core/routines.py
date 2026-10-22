# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
core/routines.py — noticing what the user does at about the same time most days.

When "learn my routines" is on, JARVIS notes a few kinds of request (opening an app, playing music, visiting a site,
playing a video) with the time. If the same thing keeps happening around the same time of day — at least three times,
on three different days — it becomes a routine. At the start of a session, the routines that are due about now are
passed to the model, which may MENTION one, once, and ask — it never does anything on its own.

Only the label ("open Spotify") and the time are kept, in memory/routines.json. Settings > What I learned lists them
and can forget them.
"""
from __future__ import annotations

import json
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


STORE = _base() / "memory" / "routines.json"
MAX_EVENTS = 1500
MIN_TIMES = 3
MIN_DAYS = 3
WINDOW_MIN = 75                 # events this close together in the day count as "the same time"
LOOKBACK_DAYS = 60
_lock = threading.Lock()


def label_for(tool: str, args: dict) -> tuple[str, str] | None:
    """(kind, label) for a tool call worth tracking, or None."""
    args = args or {}
    if tool == "open_app" and args.get("app_name"):
        return "open", str(args["app_name"]).strip().title()
    if tool == "spotify" and str(args.get("action", "play")).lower() == "play":
        what = args.get("artist") or args.get("song") or args.get("query")
        return ("music", str(what).strip().title()) if what else None
    if tool == "browser_control" and str(args.get("action", "")).lower() == "go_to" and args.get("url"):
        host = urlparse(args["url"] if "://" in str(args["url"]) else "https://" + str(args["url"])).hostname or ""
        return ("website", host.removeprefix("www.")) if host else None
    if tool == "youtube_video" and str(args.get("action", "play")).lower() == "play" and args.get("query"):
        return "video", str(args["query"]).strip().title()
    return None


def _load() -> list[dict]:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(events: list[dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STORE)


def record(tool: str, args: dict, when: datetime | None = None) -> bool:
    """Note one tracked request. Returns True if it was kept."""
    hit = label_for(tool, args)
    if not hit:
        return False
    when = when or datetime.now()
    kind, label = hit
    with _lock:
        ev = _load()
        for e in reversed(ev[-20:]):                      # the same thing twice within ten minutes is one event
            if e["kind"] == kind and e["label"].lower() == label.lower() and \
                    abs((when - datetime.fromisoformat(e["ts"])).total_seconds()) < 600:
                return False
        ev.append({"kind": kind, "label": label, "ts": when.isoformat(timespec="minutes")})
        _save(ev[-MAX_EVENTS:])
    return True


def _minutes(ts: str) -> int:
    d = datetime.fromisoformat(ts)
    return d.hour * 60 + d.minute


def patterns(now: datetime | None = None) -> list[dict]:
    """Every routine found: {kind, label, count, days, minute (typical time of day), weekend (mostly weekends?)}."""
    now = now or datetime.now()
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    groups: dict[tuple[str, str], list[datetime]] = {}
    for e in _load():
        try:
            ts = datetime.fromisoformat(e["ts"])
        except Exception:
            continue
        if ts >= cutoff:
            groups.setdefault((e["kind"], e["label"].lower()), []).append(ts)
    found = []
    for (kind, low), times in groups.items():
        mins = sorted(t.hour * 60 + t.minute for t in times)
        best: list[int] = []
        for centre in mins:                                # the biggest cluster of times of day
            near = [m for m in mins if abs(m - centre) <= WINDOW_MIN]
            if len(near) > len(best):
                best = near
        cluster = [t for t in times if abs((t.hour * 60 + t.minute) - sorted(best)[len(best) // 2]) <= WINDOW_MIN] if best else []
        days = {t.date() for t in cluster}
        if len(cluster) >= MIN_TIMES and len(days) >= MIN_DAYS:
            label = next(e["label"] for e in _load() if e["kind"] == kind and e["label"].lower() == low)
            weekend = sum(1 for t in cluster if t.weekday() >= 5) / len(cluster)
            found.append({"kind": kind, "label": label, "count": len(cluster), "days": len(days),
                          "minute": sorted(best)[len(best) // 2], "weekend": weekend})
    found.sort(key=lambda p: -p["count"])
    return found


def describe(p: dict) -> str:
    verb = {"open": "opens", "music": "plays", "website": "visits", "video": "watches"}.get(p["kind"], "does")
    h, m = divmod(p["minute"], 60)
    clock = datetime(2000, 1, 1, h, m).strftime("%I:%M %p").lstrip("0")
    return f"{verb} {p['label']} around {clock} ({p['count']} times on {p['days']} days)"


def due_now(now: datetime | None = None, window: int = 90) -> list[dict]:
    """Routines whose usual time is within `window` minutes of now, on the right kind of day (weekday/weekend)."""
    now = now or datetime.now()
    nowmin = now.hour * 60 + now.minute
    today_weekend = now.weekday() >= 5
    out = []
    for p in patterns(now):
        if abs(p["minute"] - nowmin) > window:
            continue
        if (p["weekend"] >= 0.7 and not today_weekend) or (p["weekend"] <= 0.3 and today_weekend):
            continue
        out.append(p)
    return out[:4]


def prompt_block(now: datetime | None = None) -> str:
    due = due_now(now)
    if not due:
        return ""
    lines = "\n".join(f"- The user usually {describe(p)}." for p in due)
    return ("[ROUTINES NOTICED]\n"
            "At about this time the user habitually does the following. You MAY mention one of them, once, briefly, and ask "
            "if they would like it — and only if it fits the moment. Never do it unless they say yes.\n"
            f"{lines}\n\n")


def forget(what: str) -> str:
    w = (what or "").strip().lower()
    with _lock:
        ev = _load()
        if w in ("all", "everything", "*"):
            _save([])
            return f"Forgot everything I had noted about your habits ({len(ev)} entries)." if ev else "I hadn't noted anything yet."
        if not w:
            return "Which routine? Give its name, or say all."
        keep = [e for e in ev if w not in e["label"].lower()]
        _save(keep)
    n = len(ev) - len(keep)
    return f"Forgot {n} note{'s' if n != 1 else ''} about “{what.strip()}”." if n else f"I have nothing noted about “{what.strip()}”."
