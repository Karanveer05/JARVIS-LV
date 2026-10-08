"""
weather_report — real forecast data from Open-Meteo (free, no API key).

This used to open a Google search in the browser, which gave the assistant
nothing to say. It now fetches the numbers and returns them as text.
"""
import json
import urllib.parse
import urllib.request

_TIMEOUT = 10

_WMO = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JARVIS weather)"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _locate_by_ip() -> tuple[float, float, str] | None:
    try:
        d = _get_json("https://ipwho.is/")
        if d.get("success"):
            return d["latitude"], d["longitude"], d.get("city") or "your location"
    except Exception:
        pass
    return None


def _geocode(city: str) -> tuple[float, float, str] | None:
    q = urllib.parse.urlencode({"name": city, "count": 1, "language": "en", "format": "json"})
    try:
        d = _get_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    except Exception:
        return None
    hits = d.get("results") or []
    if not hits:
        return None
    h = hits[0]
    label = h["name"] + (f", {h['country']}" if h.get("country") else "")
    return h["latitude"], h["longitude"], label


def _desc(code) -> str:
    return _WMO.get(int(code), "unknown conditions") if code is not None else "unknown conditions"


def get_weather(city: str = "", when: str = "today") -> str:
    """Returns the weather as plain text, or a sentence explaining the failure."""
    city = (city or "").strip()
    when = (when or "today").strip().lower()

    place = _geocode(city) if city else _locate_by_ip()
    if place is None:
        return (f"I could not find a place called '{city}'." if city
                else "I could not work out your location — tell me a city.")
    lat, lon, label = place

    days = 7 if any(w in when for w in ("week", "7", "forecast", "next")) else 3
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                   "weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
    })
    try:
        d = _get_json(f"https://api.open-meteo.com/v1/forecast?{q}")
        cur, daily = d["current"], d["daily"]
    except Exception as e:
        return f"I could not reach the weather service: {e}"

    def day(i: int) -> str:
        names = {0: "Today", 1: "Tomorrow"}
        name = names.get(i) or daily["time"][i]
        return (f"{name}: {_desc(daily['weather_code'][i])}, "
                f"{round(daily['temperature_2m_min'][i])} to {round(daily['temperature_2m_max'][i])} °C, "
                f"{daily['precipitation_probability_max'][i] or 0}% chance of rain")

    if "tomorrow" in when:
        return f"Weather in {label}. {day(1)}."
    if days == 7:
        return f"Weather in {label}. " + ". ".join(day(i) for i in range(7)) + "."
    return (
        f"Weather in {label}. Right now {_desc(cur['weather_code'])}, "
        f"{round(cur['temperature_2m'])} °C (feels like {round(cur['apparent_temperature'])}), "
        f"humidity {round(cur['relative_humidity_2m'])}%, wind {round(cur['wind_speed_10m'])} km/h. "
        f"{day(0)}. {day(1)}."
    )


def weather_action(parameters: dict, player=None, session_memory=None) -> str:
    msg = get_weather(parameters.get("city", ""), parameters.get("time", "today"))
    print(f"[Weather] {msg}")
    if player:
        try:
            player.write_log(f"JARVIS: {msg}")
        except Exception:
            pass
    return msg


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "weather_report",
    "description": (
        "Gets the real weather: current conditions and the forecast, as text to "
        "report to the user. Use for any weather, temperature or rain question. "
        "If the user names no city, leave `city` empty and their location is used."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING",
                     "description": "City name. Leave empty to use the user's current location."},
            "time": {"type": "STRING",
                     "description": "today | tomorrow | week (default today)"},
        },
        "required": [],
    },
    "handler": weather_action,
}
