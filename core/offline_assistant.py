"""
core/offline_assistant.py — what JARVIS does when it cannot reach its online brain.

Offline mode starts when there is no internet, when every online model has hit its daily limit, or
when the user switches it on in Settings. It is deliberately small and runs entirely on this PC:

  * OPEN / CLOSE applications — by calling open_close_apps.py, and nothing else of what JARVIS
    normally does (no music, messages, timers, settings, pictures…).
  * QUESTIONS — answered by searching the offline copy of the saved history (core/offline_history.py).
    The online assistant's other tools are not available, and neither is general knowledge.

There is no AI model in here: the wording of a command is understood with plain rules, and an answer
is the best match found in what was said before. That is what keeps it fast, tiny and truly offline.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from core import offline_history
from core.offline_skills import Skills

_ROOT = Path(__file__).resolve().parent.parent

# Never closed, whatever is asked: JARVIS itself, the interpreter running it, and Windows' own plumbing.
# open_close_apps.close_app matches names as substrings and ends processes, so this guard matters.
PROTECTED = ("python", "pythonw", "jarvis", "explorer", "system", "svchost", "winlogon", "csrss", "dwm", "lsass",
             "services", "wininit", "smss", "taskmgr", "conhost", "powershell", "cmd", "windows", "registry", "ollama")
_TOO_VAGUE = {"app", "apps", "it", "this", "that", "everything", "all", "window", "windows", "program", "thing", "stuff"}

_PREAMBLE = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|and|then)[,\s]+)?(?:jarvis[,\s]+)?(?:please[,\s]+)?"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please[,\s]+)?"
    r"(?:(?:i\s+want\s+(?:you\s+)?to|i'?d\s+like\s+(?:you\s+)?to|i\s+need\s+(?:you\s+)?to|let'?s|lets|go\s+ahead\s+and)\s+)?", re.I)
_OPEN = re.compile(r"^(open|launch|start|run|load|boot|fire\s+up|bring\s+up)\b\s*(.*)$", re.I)
_CLOSE = re.compile(r"^(close|quit|exit|kill|terminate|shut(?!\s*down))\b\s*(.*)$", re.I)
_FILLER = re.compile(r"\b(please|kindly|for\s+me|right\s+now|now|up|the|a|an|my|app|application|program|software|"
                     r"window|windows|tab|again|jarvis|thanks|thank\s+you)\b", re.I)
_ACTIONS_WE_LACK = re.compile(
    r"^(play|pause|resume|skip|send|text|message|call|set|turn|switch|remind|search|google|look\s*up|create|make|write|"
    r"take|generate|draw|paint|download|install|uninstall|delete|remove|shutdown|restart|reboot|lock|mute|unmute|"
    r"increase|decrease|raise|lower|change|translate|convert|calculate|record|screenshot|schedule|book|find|show|"
    r"read|summari[sz]e|add|save|copy|paste|type|click|scroll|volume|brightness)\b", re.I)
# Things that only the online assistant can do. Asked for while offline, the answer is an honest "not now"
# rather than a search of old chats that happens to mention the word.
_NEEDS_INTERNET = re.compile(
    r"\b(weather|forecast|temperature outside|news|headlines|stock|share price|score|cricket|match|"
    r"google|search the web|search for|look up|lookup|wikipedia|youtube|translate|meaning of|define|definition|"
    r"exchange rate|currency|flight|email|gmail|whatsapp|telegram|send (?:a )?message|text (?:him|her|them)|"
    r"play (?!pause|the music$)\w+|spotify|song|draw|generate|picture|image|photo|logo|wallpaper|"
    r"directions|route|nearby|restaurant|traffic|who (?:is|was) the (?:president|prime minister|ceo|founder)|capital of|"
    r"how tall is|how old is|population of|recipe)\b", re.I)
_ABOUT_THE_PAST = re.compile(r"\b(did i|did we|have i|have we|i asked|i said|we talked|we discussed|last time|earlier|before|"
                             r"yesterday|history|you said|you told|remember when|previous|was|were|told me)\b", re.I)
_QUESTION_START = re.compile(r"^(what|when|where|who|whom|whose|which|why|how|do|does|did|is|are|was|were|can|could|"
                             r"have|has|tell\s+me|remind\s+me\s+(?:what|when|where|who)|any|anything)\b", re.I)


def parse(text: str) -> tuple[str, list[str]]:
    """('open' | 'close' | 'other', [app names]). Pure — no side effects, so it is easy to test."""
    t = " ".join((text or "").strip().rstrip(".!?").split())
    core = _PREAMBLE.sub("", t, count=1).strip()
    for kind, rx in (("open", _OPEN), ("close", _CLOSE)):
        m = rx.match(core)
        if m:
            rest = m.group(2)
            names = []
            for part in re.split(r"\s*(?:,|&|\band\b|\bthen\b|\bplus\b|\balso\b)\s*", rest, flags=re.I):
                part = " ".join(_FILLER.sub(" ", part).split()).strip(" ,.")
                if part:
                    names.append(part)
            return kind, names
    return "other", []


class OfflineAssistant:
    def __init__(self, name: str = "JARVIS", notify=None):
        self.name = name
        self.reason = "no connection to the online assistant"
        self.skills = Skills(notify)

    # -- open / close ----------------------------------------------------------
    @staticmethod
    def _oca():
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        import open_close_apps
        return open_close_apps

    def _open(self, name: str) -> str:
        try:
            result = self._oca().open_item(name)
        except Exception as e:
            return f"I couldn't open {name}: {type(e).__name__}: {str(e)[:80]}"
        return f"I couldn't find an app called {name}." if str(result).startswith("Could not find") else f"{result}."

    def _close(self, name: str) -> str:
        low = name.lower().strip()
        if low in _TOO_VAGUE or len(low) < 3:
            return "Which app should I close? Tell me its name."
        guard = {self.name.lower(), *PROTECTED}
        if any(g and (g == low or g in low or low in g) for g in guard):
            return f"I won't close {name} — it's part of JARVIS or Windows itself."
        try:
            result = self._oca().close_app(name)
        except Exception as e:
            return f"I couldn't close {name}: {type(e).__name__}: {str(e)[:80]}"
        return f"I couldn't find {name} running." if str(result).startswith("Could not find") else f"{result}."

    # -- the one entry point ---------------------------------------------------
    def handle(self, text: str) -> str:
        """The reply to one thing the user said or typed. Does the opening/closing itself."""
        said = (text or "").strip()
        if not said:
            return "I'm here. I can open or close apps, or look things up in what we've talked about."
        low = said.lower().strip(" .!?")
        kind, names = parse(said)

        if kind in ("open", "close"):
            if not names:
                return f"Which app should I {kind}?"
            if kind == "close" and any(n.lower() in ("all", "everything", "all apps", "everything open") for n in names):
                return "I won't close everything at once. Tell me which app to close."
            out = [self._open(n) if kind == "open" else self._close(n) for n in names[:5]]
            return " ".join(out)

        if re.fullmatch(r"(hi|hello|hey|hey jarvis|jarvis|good (?:morning|afternoon|evening)|yo)", low):
            return f"Hello. I'm offline right now ({self.reason}), so I can open and close apps and look things up in our history."
        if re.search(r"\b(are you (?:online|offline)|why (?:are you )?offline|what can you do|help|what do you do|what are your (?:skills|abilities|commands))\b", low) \
                or low in ("status", "offline status"):
            return (f"I'm in offline mode ({self.reason}). Without the internet I can: open and close apps; tell the time and date; "
                    f"set timers and alarms; take notes and keep a to-do list; do maths and unit conversions; control the volume, "
                    f"brightness and music keys; take screenshots; lock the screen; check battery, CPU, memory and disk; find files; "
                    f"flip a coin, roll dice, make a password, tell a joke — and answer questions from our saved history. "
                    f"Weather, news, searching, playing songs, messages and pictures need the internet.")
        if re.search(r"\bwho are you\b|\byour name\b", low) and not re.search(r"\bmy\b", low):
            return f"I'm {self.name}, running in offline mode."

        skill = self.skills.try_handle(said)                  # time, timers, notes, maths, volume, battery…
        if skill is not None:
            return skill

        if _NEEDS_INTERNET.search(low) and not _ABOUT_THE_PAST.search(low):
            return ("That needs the internet, and I'm offline right now. I can still open and close apps, set timers, take notes, "
                    "do maths, check your battery and more — or look it up in our saved history. Say “what can you do” for the list.")

        if _QUESTION_START.match(low) or "?" in said or re.search(r"\b(remember|recall|did we|did i|have i|history)\b", low):
            return offline_history.answer(said)

        if _ACTIONS_WE_LACK.match(low):
            return ("I'm offline, so I can only open and close apps right now. I'll do that as soon as I'm back online — "
                    "or ask me something and I'll look through our history.")
        return offline_history.answer(said)
