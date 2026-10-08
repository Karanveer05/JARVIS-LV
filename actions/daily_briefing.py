"""
daily_briefing — one call that gathers weather, your open to-dos, running timers and
the top headlines, so JARVIS can open the day with a single spoken summary.

Each section is gathered independently and bounded in time: if the network is down
the briefing still reports your to-dos instead of failing as a whole.
"""
import concurrent.futures as cf

from actions import notes as _notes
from actions import timer as _timer
from actions.weather_report import get_weather

_BUDGET = 9          # seconds to wait for the slow, network-bound sections


def _todos() -> str:
    try:
        with _notes._lock:
            items = _notes._load()
    except Exception:
        return ""
    open_ = [i for i in items if i["kind"] == "todo" and not i["done"]]
    if not open_:
        return "To-do list: nothing open."
    shown = "; ".join(i["text"] for i in open_[:6])
    more = f" (+{len(open_) - 6} more)" if len(open_) > 6 else ""
    return f"To-do list ({len(open_)} open): {shown}{more}."


def _timers() -> str:
    text = _timer._list()
    return "" if text.startswith("No timers") else f"Timers: {text}."


def _headlines() -> str:
    try:
        from actions.web_search import _news
        text = _news("top world news today")
    except Exception:
        return ""
    if not text or text.startswith("No news"):
        return ""
    lines = [l.strip(" -•*") for l in text.splitlines() if l.strip()]
    return "Headlines: " + " | ".join(lines[1:5] if len(lines) > 1 else lines[:4])[:600]


def daily_briefing(parameters: dict, player=None) -> str:
    city = str(parameters.get("city", "")).strip()
    parts = []
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        weather_f = pool.submit(get_weather, city, "today")
        news_f = pool.submit(_headlines)
        parts.append(_todos())
        parts.append(_timers())
        try:
            parts.insert(0, weather_f.result(timeout=_BUDGET))
        except Exception:
            parts.insert(0, "Weather is unavailable right now.")
        try:
            news = news_f.result(timeout=_BUDGET)
            if news:
                parts.append(news)
        except Exception:
            pass
    body = " ".join(p for p in parts if p)
    return ("[BRIEFING DATA — summarise this naturally in the user's language, in a few "
            "sentences; do not read it out word for word.]\n" + body)


TOOL = {
    "name": "daily_briefing",
    "description": (
        "Gives the user their briefing in one call: weather, their open to-dos, running "
        "timers and top headlines. Use for 'good morning', 'brief me', 'what's my day "
        "look like', 'catch me up'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING", "description": "Optional city for the weather; default is the user's location"},
        },
        "required": [],
    },
    "handler": daily_briefing,
}
