# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
core/offline_skills.py — the things offline mode can do besides opening and closing apps.

Every skill here works with no internet and no AI: the wording is matched with plain patterns and the
work is done by JARVIS's own tools (timer, notes, calculator, computer_settings, screenshot, ...) or by
Windows itself. A request that is not one of these returns None, and the assistant falls back to
searching the saved history.

    time and date            "what time is it", "what day is it tomorrow", "how many days until christmas"
    timers and alarms        "set a timer for 10 minutes", "wake me at 7:30", "cancel the timer"
    notes and to-dos         "note down buy milk", "add eggs to my to-do list", "what's on my list", "mark eggs done"
    music keys               "pause the music", "next song", "previous song"
    volume, screen, windows  "volume up", "set volume to 30", "brightness down", "lock the screen", "show desktop"
    screenshots              "take a screenshot"
    this computer            "how much battery do I have", "how is my cpu", "how much disk space", "uptime", "am I online"
    what is running          "what is using my cpu", "is chrome running"
    clipboard and files      "what did I copy", "find the file budget"
    maths and units          "what is 15% of 240", "12 times 8", "convert 10 km to miles"
    small things             "flip a coin", "roll a dice", "make me a password", "tell me a joke"

Things that need the internet (weather, news, searching, playing a song, messages, pictures…) are not here.
"""
from __future__ import annotations

import random
import re
import socket
import time
from datetime import datetime, timedelta
from typing import Callable

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_JOKES = [
    "Why don't programmers like nature? It has too many bugs.",
    "I told my computer I needed a break, and now it won't stop sending me vacation ads.",
    "Why did the computer go to the doctor? It had a virus.",
    "There are 10 kinds of people: those who understand binary and those who don't.",
    "Why was the laptop cold? It left its Windows open.",
    "A SQL query walks into a bar, walks up to two tables and asks: can I join you?",
    "Why do Java developers wear glasses? Because they don't C sharp.",
    "I would tell you a UDP joke, but you might not get it.",
    "My password is the last 8 digits of pi. Nobody has ever guessed it, and neither have I.",
    "Why did the robot go on vacation? To recharge its batteries.",
    "How does a computer get drunk? It takes screenshots.",
    "I'm reading a book on anti-gravity. It's impossible to put down.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "What do you call a fake noodle? An impasta.",
    "Parallel lines have so much in common. It's a shame they'll never meet.",
]
_COIN_NUM = re.compile(r"(\d+)")


def _num(text: str) -> int | None:
    m = _COIN_NUM.search(text or "")
    return int(m.group(1)) if m else None


class Skills:
    def __init__(self, notify: Callable[[str], None] | None = None):
        self.notify = notify or (lambda text: None)       # how a timer tells the user it is done
        self._joke_i = random.randrange(len(_JOKES))

    # -- plumbing ---------------------------------------------------------------------------------
    def _tool(self, module: str, func: str, params: dict, **kw) -> str:
        """Call one of JARVIS's own tools. A single seam, so tests can replace what really happens."""
        import importlib
        fn = getattr(importlib.import_module(f"actions.{module}"), func)
        return str(fn(params, **kw))

    @staticmethod
    def _clean(text: str) -> str:
        t = re.sub(r"[^\w\s%:.\-+*/^'°]", " ", (text or "").lower())
        t = re.sub(r"^(?:(?:hey|hi|ok|okay|so)\s+)?(?:jarvis\s+)?(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", "", t.strip())
        return " ".join(t.split()).strip(" .")

    _ACTION_START = re.compile(
        r"^(?:set (?:a |an )?(?:\d+\s*\w+\s+)?(?:timer|alarm)|start (?:a |an )?(?:\d+\s*\w+\s+)?timer|timer\b|alarm\b|wake me|cancel (?:the |all )?(?:timers?|alarms?)|"
        r"note down|take a note|make a note|write down|jot down|add .+ to (?:my |the )?(?:to-?do|todo|tasks?|shopping|grocery|notes?|list)|mark .+ (?:done|complete)|"
        r"volume (?:up|down|\d+)|set (?:the )?volume|turn (?:it |the volume )?(?:up|down)|louder|quieter|mute|unmute|brightness|set (?:the )?brightness|lock (?:the |my )?(?:screen|pc|computer|laptop)|"
        r"pause|resume|next song|previous song|skip|take a screenshot|screenshot|show (?:the )?desktop|minimi[sz]e|maximi[sz]e|flip a coin|roll (?:a|an|some|\d)|tell me a joke)", re.I)

    def is_action(self, text: str) -> bool:
        """A clear imperative the voice may act on without hearing the name first (set a timer, volume up, take a
        note…). Questions and look-ups are not in here: those still need "Jarvis"."""
        return bool(self._ACTION_START.match(self._clean(text)))

    # -- the entry point --------------------------------------------------------------------------------
    def try_handle(self, text: str, now: datetime | None = None) -> str | None:
        low = self._clean(text)
        if not low:
            return None
        now = now or datetime.now()
        for skill in (self._time_date, self._timers, self._notes, self._media, self._settings, self._system,
                      self._processes, self._clipboard_files, self._small, self._maths):
            try:
                out = skill(low, text, now)
            except Exception as e:                         # a broken skill must never break the assistant
                out = f"I tried, but something went wrong ({type(e).__name__}: {str(e)[:70]})."
            if out is not None:
                return out
        return None

    # ── time and date ────────────────────────────────────────────────────────────────────────────────
    def _time_date(self, low: str, raw: str, now: datetime) -> str | None:
        if re.search(r"\bwhat(?:'s| is)? the time\b|\bwhat time is it\b|\btell me the time\b|\bcurrent time\b|^time$", low):
            return now.strftime("It's %I:%M %p.").replace("It's 0", "It's ")
        m = re.search(r"\bwhat (?:day|date) (?:is it|will it be|was it) (tomorrow|yesterday|in (\d+) (day|days|week|weeks)|(\d+) (day|days|week|weeks) ago)", low)
        if m:
            when = m.group(1)
            if when == "tomorrow":
                d = now + timedelta(days=1)
            elif when == "yesterday":
                d = now - timedelta(days=1)
            elif when.startswith("in "):
                d = now + timedelta(days=int(m.group(2)) * (7 if m.group(3).startswith("week") else 1))
            else:
                d = now - timedelta(days=int(m.group(4)) * (7 if m.group(5).startswith("week") else 1))
            return d.strftime("That is %A, %d %B %Y.")
        m = re.search(r"\bnext (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low)
        if m and re.search(r"\bwhat(?:'s| is)? the date\b|\bwhen is\b|\bwhat date\b", low):
            target = _WEEKDAYS.index(m.group(1))
            d = now + timedelta(days=(target - now.weekday() - 1) % 7 + 1)
            return d.strftime("Next " + m.group(1).title() + " is %d %B %Y.")
        m = re.search(r"\bhow many (?:days|weeks) (?:until|till|to|before|left until)\s+(.+)", low)
        if m:
            target = self._parse_day(m.group(1), now)
            if target:
                days = (target - now.date()).days
                return (f"{days} day{'s' if days != 1 else ''} until {m.group(1).strip()}." if days >= 0
                        else f"{m.group(1).strip().title()} was {-days} days ago.")
        if re.search(r"\bwhat(?:'s| is)? (?:today(?:'s)? )?(?:the )?date\b|\bwhat day is (?:it|today)\b|\btoday(?:'s)? date\b|^date$|^what is today$|\bwhat'?s today\b", low):
            return now.strftime("Today is %A, %d %B %Y.")
        if re.search(r"\bwhat month is it\b|\bwhich month\b", low):
            return now.strftime("It's %B.")
        if re.search(r"\bwhat year is it\b|\bwhich year\b|\bwhat(?:'s| is) the year\b", low):
            return now.strftime("It's %Y.")
        return None

    @staticmethod
    def _parse_day(text: str, now: datetime):
        t = text.strip().lower()
        fixed = {"christmas": (12, 25), "new year": (1, 1), "new years": (1, 1), "new year's day": (1, 1),
                 "valentine's day": (2, 14), "valentines day": (2, 14), "halloween": (10, 31)}
        md = fixed.get(t)
        if md is None:
            m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)", t) or None
            if m and m.group(2) in _MONTHS:
                md = (_MONTHS.index(m.group(2)) + 1, int(m.group(1)))
                md = (md[0], md[1])
            else:
                m = re.search(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", t)
                if m and m.group(1) in _MONTHS:
                    md = (_MONTHS.index(m.group(1)) + 1, int(m.group(2)))
        if md is None:
            return None
        month, day = md
        try:
            d = now.replace(month=month, day=day).date()
        except ValueError:
            return None
        if d < now.date():
            d = d.replace(year=d.year + 1)
        return d

    # ── timers and alarms ────────────────────────────────────────────────────────────────────────────
    def _timers(self, low: str, raw: str, now: datetime) -> str | None:
        if not re.search(r"\btimers?\b|\balarms?\b|\bwake me\b|\bremind me in\b", low):
            return None
        speak = lambda t: self.notify(re.sub(r"^\[TIMER FIRED\]\s*", "", t).split(". Tell the user")[0])
        if re.search(r"\b(cancel|stop|delete|remove|clear|turn off)\b", low):
            ident = _num(low)
            return self._tool("timer", "timer", {"action": "cancel", "id": str(ident) if ident and "all" not in low else "all"})
        if re.search(r"\b(how long|time left|what timers|any timers|list|which timers|timers? (?:running|left|status)|status)\b", low):
            return self._tool("timer", "timer", {"action": "list"})
        unit = r"(?:hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b"
        m = (re.search(rf"(\d+(?:\.\d+)?\s*{unit}(?:\s*(?:and\s*)?\d+\s*(?:minutes?|mins?|m|seconds?|secs?|s)\b)?)", low)
             or re.search(r"\b(?:at|for|by)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm|a m|p m)?)\b", low)
             or re.search(r"\b(?:at|for)\s+(noon|midnight)\b", low))
        if not m:
            return "For how long? Say something like “set a timer for 10 minutes” or “wake me at 7:30”."
        when = m.group(1).strip().replace(" a m", " am").replace(" p m", " pm")
        if when == "noon":
            when = "12:00 pm"
        elif when == "midnight":
            when = "12:00 am"
        label = ""
        lm = re.search(r"\b(?:to|that|called|named|for the)\s+([a-z' ]{2,40})$", low[m.end():] if False else low[low.find(m.group(1)) + len(m.group(1)):])
        if lm and not re.search(r"\d", lm.group(1)):
            label = lm.group(1).strip()
        return self._tool("timer", "timer", {"action": "set", "when": when, "label": label}, speak=speak)

    # ── notes and to-dos ─────────────────────────────────────────────────────────────────────────────
    def _notes(self, low: str, raw: str, now: datetime) -> str | None:
        n = lambda params: self._tool("notes", "notes", params)
        orig = " ".join(raw.split()).strip(" .")
        m = re.match(r"(?:note down|take a note|make a note|write down|jot down|save a note|note that|note)\s*(?:that|to|:)?\s+(.+)$", low)
        if m:
            return n({"action": "add", "kind": "note", "text": self._orig_tail(orig, m.group(1))})
        m = re.match(r"remember (?:that|to)\s+(.+)$", low)
        if m:
            kind = "todo" if low.startswith("remember to") else "note"
            return n({"action": "add", "kind": kind, "text": self._orig_tail(orig, m.group(1))})
        m = re.match(r"(?:add|put)\s+(.+?)\s+(?:to|on|in)\s+(?:my |the )?(to-?do|todo|task|tasks|shopping|grocery)?\s*(?:list|notes?)?$", low)
        if m and re.search(r"\b(list|notes?)\b", low):
            kind = "note" if re.search(r"\bnotes?$", low) and not m.group(2) else "todo"
            return n({"action": "add", "kind": kind, "text": self._orig_tail(orig, m.group(1))})
        if re.search(r"\b(?:what(?:'s| is)|show|read|list|tell me|check)\b.*\b(?:my )?(to-?do|todo|tasks?|notes?|list)\b", low) \
                or re.match(r"(?:my )?(?:to-?do|todo|tasks?|notes?)(?: list)?$", low):
            kind = "note" if re.search(r"\bnotes?\b", low) and not re.search(r"to-?do|todo|task|list", low) else \
                   ("todo" if re.search(r"to-?do|todo|task", low) else "")
            out = n({"action": "list", "kind": kind})
            lines = [ln for ln in out.splitlines() if ln.strip()]
            return ("Here's what you have:\n" + "\n".join(lines[:10])) if len(lines) > 0 and not out.startswith(("Nothing", "No ")) else out
        m = re.match(r"(?:mark|tick)\s+(.+?)\s+(?:as\s+)?(?:done|complete|completed|finished)$", low) \
            or re.match(r"(?:complete|finish|i (?:finished|completed))\s+(.+)$", low)
        if m:
            ident = _num(m.group(1)) if re.fullmatch(r"(?:number |note |to-?do |task )?\d+", m.group(1).strip()) else None
            return n({"action": "done", "id": ident} if ident else {"action": "done", "text": m.group(1)})
        m = re.match(r"(?:delete|remove)\s+(?:the )?(?:note|to-?do|task)\s+(.+)$", low) \
            or re.match(r"(?:delete|remove)\s+(.+?)\s+from\s+(?:my )?(?:to-?do|todo|task|notes?|list)", low)
        if m:
            ident = _num(m.group(1)) if re.fullmatch(r"(?:number )?\d+", m.group(1).strip()) else None
            return n({"action": "delete", "id": ident} if ident else {"action": "delete", "text": m.group(1)})
        return None

    @staticmethod
    def _orig_tail(original: str, lowered_tail: str) -> str:
        """The user's own spelling and capitals for the tail of the sentence."""
        i = original.lower().rfind(lowered_tail.lower())
        return original[i:i + len(lowered_tail)] if i >= 0 else lowered_tail

    # ── music keys ──────────────────────────────────────────────────────────────────────────────────────
    def _media(self, low: str, raw: str, now: datetime) -> str | None:
        thing = r"(?:the |my )?(?:music|song|songs|video|videos|playback|spotify|track|audio|it)?"
        if re.fullmatch(rf"(?:pause|stop|mute the music|hold)\s*{thing}", low) and low not in ("stop", "hold"):
            return self._tool("spotify", "spotify", {"action": "pause"})
        if re.fullmatch(rf"(?:resume|continue|unpause|play)\s*{thing}", low) and low != "play":
            return self._tool("spotify", "spotify", {"action": "resume"})
        if re.fullmatch(rf"(?:next|skip)\s*(?:the )?(?:song|track|one)?", low):
            return self._tool("spotify", "spotify", {"action": "next"})
        if re.fullmatch(r"(?:previous|last|go back)\s*(?:the )?(?:song|track|one)?|play (?:the )?previous (?:song|track)", low):
            return self._tool("spotify", "spotify", {"action": "previous"})
        return None

    # ── volume, screen, windows ───────────────────────────────────────────────────────────────────────────
    def _settings(self, low: str, raw: str, now: datetime) -> str | None:
        cs = lambda action, value=None: self._tool("computer_settings", "computer_settings", {"action": action, **({"value": value} if value is not None else {})})
        m = re.search(r"\b(?:set |put |make )?(?:the )?volume (?:to |at )?(\d{1,3})\b|\bvolume (\d{1,3})\b", low)
        if m:
            v = max(0, min(100, int(m.group(1) or m.group(2))))
            return cs("volume_set", v)
        if re.fullmatch(r"(?:turn (?:it |the volume )?up|volume up|louder|increase (?:the )?volume|raise (?:the )?volume|more volume)", low):
            return cs("volume_up")
        if re.fullmatch(r"(?:turn (?:it |the volume )?down|volume down|quieter|lower|decrease (?:the )?volume|lower (?:the )?volume|less volume)", low):
            return cs("volume_down")
        if re.fullmatch(r"(?:mute|unmute|toggle mute|mute (?:the )?(?:sound|volume|audio|computer|pc|laptop))", low):
            return cs("mute")
        m = re.search(r"\b(?:set |put )?(?:the )?brightness (?:to |at )?(\d{1,3})\b", low)
        if m:
            v = max(0, min(100, int(m.group(1))))
            try:
                from actions import computer_settings as c
                c.brightness_set(v)
                return f"Brightness set to {v}%."
            except Exception as e:
                return f"I couldn't change the brightness ({type(e).__name__})."
        if re.fullmatch(r"(?:brightness up|brighter|increase (?:the )?brightness|raise (?:the )?brightness|more brightness)", low):
            return cs("brightness_up")
        if re.fullmatch(r"(?:brightness down|dimmer|darker|decrease (?:the )?brightness|lower (?:the )?brightness|less brightness|dim (?:the )?screen)", low):
            return cs("brightness_down")
        if re.fullmatch(r"lock (?:the |my )?(?:screen|pc|computer|laptop|system)|lock it", low):
            return cs("lock_screen")
        if re.fullmatch(r"show (?:the )?desktop|go to (?:the )?desktop|minimi[sz]e (?:all|everything)(?: windows)?", low):
            return cs("show_desktop")
        if re.fullmatch(r"minimi[sz]e(?: this| the| current)?(?: window)?", low):
            return cs("minimize")
        if re.fullmatch(r"maximi[sz]e(?: this| the| current)?(?: window)?", low):
            return cs("maximize")
        if re.fullmatch(r"(?:go |make it |toggle )?full ?screen", low):
            return cs("fullscreen")
        if re.fullmatch(r"(?:switch|next|change) window|alt tab", low):
            return cs("switch_window")
        if re.fullmatch(r"(?:turn on |switch to |toggle |enable )?(?:dark mode|night mode)|(?:turn on |switch to )?light mode", low):
            return cs("dark_mode")
        if re.search(r"\b(?:take|capture|grab)\b.*\bscreenshot\b|^screenshot\b|\bscreenshot (?:this|the|my)\b", low):
            target = "window" if "window" in low else "screen"
            out = self._tool("screenshot", "screenshot", {"action": "take", "target": target})
            return out
        return None

    # ── this computer ───────────────────────────────────────────────────────────────────────────────────────
    def _system(self, low: str, raw: str, now: datetime) -> str | None:
        import psutil
        if re.search(r"\bam i (?:online|connected)\b|\bis (?:the )?(?:internet|wi-?fi|network) (?:working|on|up|connected)\b|\bdo i have (?:internet|wi-?fi)\b|\binternet (?:status|connection)\b", low):
            return ("Yes, the internet is reachable — I'll switch back to the online assistant shortly."
                    if self._internet() else "No — there is no internet connection right now.")
        if re.search(r"\bbattery\b|\bcharging\b|\bplugged in\b|\bhow much (?:charge|power)\b", low):
            b = psutil.sensors_battery()
            if b is None:
                return "This computer has no battery — it's a desktop on mains power."
            left = ""
            if not b.power_plugged and b.secsleft and b.secsleft > 0:
                h, m = divmod(int(b.secsleft) // 60, 60)
                left = f", about {h}h {m}m left" if h else f", about {m} minutes left"
            return f"Battery is at {b.percent:.0f}% and {'charging' if b.power_plugged else 'not charging'}{left}."
        if re.search(r"\b(?:cpu|processor)\b.*\b(?:usage|load|busy|using|percent|doing)\b|\bhow busy is (?:my |the )?(?:cpu|processor|pc|computer)\b|\bcpu usage\b"
                     r"|\b(?:how(?:'s| is)|what(?:'s| is)) (?:my |the )?(?:cpu|processor)\b|^(?:my )?(?:cpu|processor)$", low):
            return f"The CPU is at {psutil.cpu_percent(interval=0.6):.0f}% right now."
        if re.search(r"\b(?:ram|memory)\b.*\b(?:usage|used|free|left|available|using|have)\b|\bhow much (?:ram|memory)\b", low) and "using my" not in low:
            v = psutil.virtual_memory()
            return f"{v.used / 2**30:.1f} GB of {v.total / 2**30:.1f} GB memory is in use ({v.percent:.0f}%)."
        if re.search(r"\b(?:disk|storage|drive|hard ?drive|space)\b.*\b(?:space|left|free|available|used|usage)\b|\bhow much (?:disk|storage|space)\b", low):
            d = psutil.disk_usage("C:\\" if psutil.WINDOWS else "/")
            return f"{d.free / 2**30:.0f} GB free of {d.total / 2**30:.0f} GB on the main drive ({d.percent:.0f}% used)."
        if re.search(r"\buptime\b|\bhow long (?:has|have) (?:my |the |this )?(?:pc|computer|laptop|system|machine) been (?:on|running|up)\b", low):
            secs = time.time() - psutil.boot_time()
            d, rem = divmod(int(secs), 86400)
            h, m = divmod(rem // 60, 60)
            return "The computer has been on for " + (f"{d} day{'s' if d != 1 else ''}, " if d else "") + f"{h} hours and {m} minutes."
        if re.search(r"\bsystem status\b|\bhow(?:'s| is) (?:my |the )?(?:pc|computer|laptop|system)\b|\bcomputer status\b|\bpc status\b", low):
            v, d = psutil.virtual_memory(), psutil.disk_usage("C:\\" if psutil.WINDOWS else "/")
            b = psutil.sensors_battery()
            bat = f" Battery {b.percent:.0f}% ({'charging' if b.power_plugged else 'on battery'})." if b else ""
            return (f"CPU {psutil.cpu_percent(interval=0.6):.0f}%, memory {v.percent:.0f}% used, "
                    f"{d.free / 2**30:.0f} GB free on the main drive.{bat}")
        if re.search(r"\b(?:my |what(?:'s| is) my )?(?:local )?ip(?: address)?\b", low) and re.search(r"\b(?:what|show|tell|my)\b", low):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
                s.close()
            except Exception:
                ip = socket.gethostbyname(socket.gethostname())
            return f"This computer's address on your network is {ip}."
        return None

    @staticmethod
    def _internet() -> bool:
        for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
            try:
                socket.create_connection(host, timeout=1.5).close()
                return True
            except OSError:
                continue
        return False

    # ── what is running ───────────────────────────────────────────────────────────────────────────────────────
    def _processes(self, low: str, raw: str, now: datetime) -> str | None:
        m = re.search(r"\bwhat(?:'s| is)? (?:using|eating|hogging|taking|slowing down)\b.*\b(cpu|processor|memory|ram|computer|pc)\b", low) \
            or re.search(r"\bwhich (?:app|program|apps|programs).*\b(?:most|using)\b.*\b(cpu|memory|ram)\b", low)
        if m:
            sort = "cpu" if m.group(1) in ("cpu", "processor", "computer", "pc") else "memory"
            out = self._tool("process_manager", "process_manager", {"action": "list", "sort": sort, "count": 5})
            return f"The busiest programs by {sort}:\n{out}"
        m = re.fullmatch(r"is (.+?) (?:running|open|on)", low)
        if m and m.group(1) not in ("it", "that", "there", "everything"):
            return self._tool("process_manager", "process_manager", {"action": "find", "name": m.group(1)})
        return None

    # ── clipboard and files ───────────────────────────────────────────────────────────────────────────────────────
    def _clipboard_files(self, low: str, raw: str, now: datetime) -> str | None:
        if re.search(r"\bwhat did i (?:just )?copy\b|\bclipboard history\b|\bwhat(?:'s| is) (?:on|in) my clipboard\b|\brecently copied\b", low):
            return self._tool("clipboard_history", "clipboard_history", {"action": "list"})
        m = re.search(r"\b(?:find|search for|look for|locate)\s+(?:the |my |a )?(?:file|files|folder|document)s?\s*(?:named|called|with)?\s*(.+)$", low) \
            or re.search(r"\bwhere(?:'s| is)\s+(?:my |the )?(.+?)\s+(?:file|folder|document)\b", low)
        if m and m.group(1).strip():
            name = m.group(1).strip()
            lines: list[str] = []
            for where in ("desktop", "documents", "downloads"):
                out = self._tool("file_controller", "file_controller", {"action": "find", "name": name, "path": where})
                lines += [ln for ln in out.splitlines() if ln.strip() and not re.search(r"\b(?:no files?|not found|nothing|0 results)\b", ln, re.I)]
                if len(lines) >= 6:
                    break
            return f"Here is what I found for “{name}”:\n" + "\n".join(lines[:6]) if lines else f"I found no file named “{name}” on the desktop, in Documents or in Downloads."
        return None

    # ── small things ──────────────────────────────────────────────────────────────────────────────────────────────
    def _small(self, low: str, raw: str, now: datetime) -> str | None:
        if re.search(r"\b(?:flip|toss) a coin\b|\bheads or tails\b", low):
            return random.choice(["Heads.", "Tails."])
        m = re.search(r"\broll (?:a |an |some )?(?:(\d+)\s*)?(?:d|dice|die|sided)\s*(\d+)?", low)
        if m:
            count, sides = int(m.group(1) or 1), int(m.group(2) or 6)
            count, sides = max(1, min(count, 20)), max(2, min(sides, 1000))
            rolls = [random.randint(1, sides) for _ in range(count)]
            return f"Rolled {count}d{sides}: {rolls}" + (f" = {sum(rolls)}." if count > 1 else ".")
        m = re.search(r"\b(?:pick|choose|give me|say|tell me)\s+(?:a |me a )?(?:random )?number\s*(?:from|between)\s*(\d+)\s*(?:to|and|-)\s*(\d+)", low)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            return f"{random.randint(lo, hi)}."
        if re.search(r"\b(?:make|generate|create|give|write)\b.*\bpassword\b", low):
            n = _num(low) or 16
            return self._tool("everyday_tools", "everyday", {"action": "password", "length": n})
        if re.search(r"\b(?:tell|say|give) (?:me )?(?:a |another )?joke\b|\bmake me laugh\b|^joke$|\banother one\b", low):
            self._joke_i = (self._joke_i + random.randint(1, 4)) % len(_JOKES)
            return _JOKES[self._joke_i]
        if re.fullmatch(r"(?:thanks|thank you|thank you so much|cheers|thanks a lot|thx)(?: jarvis)?", low):
            return "You're welcome."
        if re.fullmatch(r"how are you(?: doing| today)?|how's it going", low):
            return "I'm running fine — a bit limited while offline, but ready to help."
        if re.fullmatch(r"good night|goodnight|good morning|good evening|good afternoon", low):
            return {"good night": "Good night.", "goodnight": "Good night.", "good morning": "Good morning.",
                    "good evening": "Good evening.", "good afternoon": "Good afternoon."}[low]
        return None

    # ── maths and units ────────────────────────────────────────────────────────────────────────────────────────────────
    def _maths(self, low: str, raw: str, now: datetime) -> str | None:
        if not re.search(r"\d", low):
            return None
        m = re.search(r"(?:convert\s+)?(-?\d+(?:[.,]\d+)?)\s*([a-z°][a-z°\s]{0,18}?)\s+(?:to|in|into|as)\s+([a-z°][a-z°\s]{0,18})$", low)
        if m:
            src, dst = m.group(2).strip(), m.group(3).strip()
            try:
                from actions import calculator as c
                if src.lower() in c._CODES or dst.lower() in c._CODES:
                    return "Currency rates need the internet, so I can't convert money while offline."
            except Exception:
                pass
            out = self._tool("calculator", "calculator", {"action": "convert", "value": m.group(1).replace(",", ""), "from": src, "to": dst})
            if "can't convert" not in out and "Give me" not in out:
                return out
        expr = re.sub(r"^(?:what(?:'s| is)|how much is|calculate|compute|solve|work out|tell me|equals?|math|what are)\s+", "", low).rstrip("?")
        expr = re.sub(r"^the\s+", "", expr)
        mm = re.fullmatch(r"(\d+)\s*(?:mod|modulo|remainder(?: of)?(?: divided by)?)\s*(\d+)", expr)
        if mm and int(mm.group(2)):
            return f"{mm.group(1)} mod {mm.group(2)} = {int(mm.group(1)) % int(mm.group(2))}"
        shown = expr
        num = r"(-?\d+(?:\.\d+)?)"
        expr = re.sub(rf"(?:square root|sqrt)(?: of)?\s*\(?{num}\)?", r"sqrt(\1)", expr)
        expr = re.sub(rf"cube root(?: of)?\s*\(?{num}\)?", r"(\1)**(1/3)", expr)
        expr = re.sub(rf"{num}\s*(?:squared|square)", r"\1**2", expr)
        expr = re.sub(rf"{num}\s*(?:cubed|cube)", r"\1**3", expr)
        expr = re.sub(rf"{num}\s*(?:percent|per cent)\s*of", r"\1% of", expr)
        mathy = re.search(r"[+\-*/^%×÷]|\d\s*x\s*\d|\b(?:plus|minus|times|multiplied|divided|over|percent|power|squared|cubed|root|sqrt|mod|sin|cos|tan|log)\b", expr)
        if not mathy or re.search(r"[a-z]{4,}", re.sub(r"\b(?:plus|minus|times|multiplied|divided|over|percent|power|squared|cubed|root|sqrt|square|cube|by|of|the|to|sin|cos|tan|log)\b", "", expr)):
            return None
        out = self._tool("calculator", "calculator", {"expression": expr})
        if "=" not in out:
            return None
        return f"{shown} = {out.rsplit('=', 1)[1].strip()}" if shown != expr else out
