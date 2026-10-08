"""
everyday — small things people ask an assistant all day: a strong password, a QR
code, currency conversion, a word's meaning, the time somewhere else, a translation,
a coin flip.

Currency and dictionary use free public endpoints with no key. Translation uses the
same Gemini ladder as the rest of the app. Everything else is offline.
"""
import random
import re
import secrets
import string
from datetime import datetime

from actions._paths import known_folder, safe_filename, unique_path

_TIMEOUT = 10

# A handful of cities people actually say; anything else is tried as an IANA zone
# ("Europe/Madrid") or matched against the zone database by its last segment.
_CITY_ZONES = {
    "new york": "America/New_York", "nyc": "America/New_York", "los angeles": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles", "chicago": "America/Chicago", "toronto": "America/Toronto",
    "mexico city": "America/Mexico_City", "sao paulo": "America/Sao_Paulo",
    "london": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "moscow": "Europe/Moscow", "dubai": "Asia/Dubai", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "india": "Asia/Kolkata", "singapore": "Asia/Singapore", "hong kong": "Asia/Hong_Kong",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "seoul": "Asia/Seoul", "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai", "sydney": "Australia/Sydney", "auckland": "Pacific/Auckland",
    "cairo": "Africa/Cairo", "lagos": "Africa/Lagos", "johannesburg": "Africa/Johannesburg",
    "utc": "UTC", "gmt": "UTC",
}


def _password(p: dict) -> str:
    n = max(8, min(int(p.get("length") or 16), 128))
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    if str(p.get("symbols", "true")).lower() not in ("false", "no", "0"):
        pools.append("!@#$%^&*-_=+?")
    pw = [secrets.choice(pool) for pool in pools]          # one of each kind, guaranteed
    pw += [secrets.choice("".join(pools)) for _ in range(n - len(pw))]
    random.SystemRandom().shuffle(pw)
    pw = "".join(pw)
    try:
        import pyperclip
        pyperclip.copy(pw)
        return f"Generated a {n}-character password and copied it to the clipboard. It isn't shown or saved."
    except Exception:
        return f"Generated a {n}-character password: {pw}"


def _qr(p: dict) -> str:
    data = (p.get("text") or "").strip()
    if not data:
        return "What should the QR code contain?"
    import qrcode
    out = unique_path(known_folder("pictures") / f"QR_{safe_filename(data[:30], 'code')}.png")
    qrcode.make(data).save(out)
    return f"QR code saved to {out}."


def _currency(p: dict) -> str:
    import requests
    try:
        amount = float(p.get("amount") or 1)
    except (TypeError, ValueError):
        return "That amount isn't a number."
    src = (p.get("from") or "").upper().strip()
    dst = (p.get("to") or "").upper().strip()
    if not (re.fullmatch(r"[A-Z]{3}", src) and re.fullmatch(r"[A-Z]{3}", dst)):
        return "Use three-letter currency codes, like USD and EUR."
    r = requests.get(f"https://open.er-api.com/v6/latest/{src}", timeout=_TIMEOUT)
    r.raise_for_status()
    rate = r.json().get("rates", {}).get(dst)
    if rate is None:
        return f"I don't have a rate for {src} to {dst}."
    return f"{amount:g} {src} = {amount * rate:,.2f} {dst} (rate {rate:.4f}, updated daily)."


def _define(p: dict) -> str:
    import requests
    word = (p.get("text") or "").strip().lower()
    if not word:
        return "Which word?"
    r = requests.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}", timeout=_TIMEOUT)
    if r.status_code == 404:
        return f"No definition found for '{word}'."
    r.raise_for_status()
    lines = []
    for meaning in r.json()[0].get("meanings", [])[:3]:
        d = meaning["definitions"][0]
        lines.append(f"{meaning['partOfSpeech']}: {d['definition']}"
                     + (f" (e.g. \"{d['example']}\")" if d.get("example") else ""))
    return f"{word}: " + " | ".join(lines)


def _zone(place: str):
    from zoneinfo import ZoneInfo, available_timezones
    key = place.strip().lower()
    name = _CITY_ZONES.get(key)
    if name:
        return ZoneInfo(name)
    if "/" in place:
        return ZoneInfo(place.strip())
    tail = key.replace(" ", "_")
    for z in available_timezones():
        if z.lower().endswith("/" + tail):
            return ZoneInfo(z)
    return None


def _world_time(p: dict) -> str:
    place = (p.get("text") or "").strip()
    if not place:
        return datetime.now().strftime("It's %A %d %B, %I:%M %p here.")
    tz = _zone(place)
    if tz is None:
        return f"I don't know a timezone for '{place}'. Try a big city or a zone like Europe/Madrid."
    now = datetime.now(tz)
    return now.strftime(f"It's %A %I:%M %p in {place.title()} (%Z, UTC%z).")


def _translate(p: dict) -> str:
    text = (p.get("text") or "").strip()
    lang = (p.get("to") or "").strip()
    if not (text and lang):
        return "I need the text and the language to translate into."
    from core import gemini
    out = gemini.text(
        f"Translate into {lang}. Output ONLY the translation:\n\n{text}",
        gemini.FAST, None, 20_000,
    )
    return out or "Translation failed — the model didn't answer."


def _random(p: dict) -> str:
    what = (p.get("text") or "coin").lower()
    if "coin" in what or "flip" in what:
        return random.choice(["Heads.", "Tails."])
    m = re.search(r"(\d*)\s*d\s*(\d+)", what)
    if m or "dice" in what or "die" in what:
        count = int(m.group(1) or 1) if m else 1
        sides = int(m.group(2)) if m else 6
        count, sides = min(count, 20), max(2, min(sides, 1000))
        rolls = [random.randint(1, sides) for _ in range(count)]
        return f"Rolled {count}d{sides}: {rolls}" + (f" = {sum(rolls)}" if count > 1 else "")
    nums = re.findall(r"\d+", what)
    if len(nums) >= 2:
        lo, hi = sorted(map(int, nums[:2]))
        return f"{random.randint(lo, hi)} (between {lo} and {hi})."
    return random.choice(["Heads.", "Tails."])


_ACTIONS = {
    "password": _password, "qr": _qr, "currency": _currency, "define": _define,
    "time": _world_time, "translate": _translate, "random": _random,
}


def everyday(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "").lower().strip()
    fn = _ACTIONS.get(action)
    if fn is None:
        return "Unknown action. Use " + ", ".join(_ACTIONS) + "."
    try:
        return fn(p)
    except Exception as e:
        return f"{action} failed: {type(e).__name__}: {e}"


TOOL = {
    "name": "everyday",
    "description": (
        "Quick everyday helpers: 'make me a strong password' (copied, never spoken), 'make a QR "
        "code for this link', 'convert 50 dollars to rupees', 'what does ephemeral mean', 'what "
        "time is it in Tokyo', 'translate good morning to Spanish', 'flip a coin', 'roll 2d6', "
        "'pick a number from 1 to 10'. For maths and unit conversion use calculator."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "password | qr | currency | define | time | translate | random"},
            "text": {"type": "STRING", "description": "qr: contents; define: the word; time: a city or zone (empty = local); translate: the text; random: 'coin', '2d6' or '1 to 10'"},
            "to": {"type": "STRING", "description": "translate: target language; currency: target code like EUR"},
            "from": {"type": "STRING", "description": "currency: source code like USD"},
            "amount": {"type": "NUMBER", "description": "currency: how much (default 1)"},
            "length": {"type": "INTEGER", "description": "password: characters (default 16)"},
            "symbols": {"type": "BOOLEAN", "description": "password: include symbols (default true)"},
        },
        "required": ["action"],
    },
    "handler": everyday,
}
