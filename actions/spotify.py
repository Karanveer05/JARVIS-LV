"""
spotify — play a song, an artist's newest release or a playlist in the Spotify desktop
app, and pause / resume / skip.

"Play Arjan Dhillon's new song" used to be left to the model to work out with clicks and
screenshots: slow, and it failed. This does it directly:

  1. FIND   — the track's Spotify id.
              * With a free Spotify developer app (spotify_client_id / spotify_client_secret in
                config/api_keys.json) the official search answers in well under a second, and
                "newest song" is Spotify's own newest release.
              * Without one, "newest song by X" comes from the iTunes catalogue (free, no key,
                sortable by date) and the Spotify page is found with a web search.
  2. OPEN   — `spotify:track:<id>`. The desktop app goes to the track's album page with that
              row highlighted. It does NOT start playing by itself — that is what made the
              old approach look broken.
  3. PLAY   — make sure Spotify really is the window in front (never type into another app),
              press Enter on the highlighted row, and read the window title, which Spotify
              sets to "Artist - Song" while playing. The tool only says "playing" when the
              title shows the song that was asked for.
"""
import os
import platform
import re
import subprocess
import time
from urllib.parse import quote

_OS = platform.system()
_TIMEOUT = 10
_LINK = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|artist|playlist)/([A-Za-z0-9]{22})")
_LATEST_WORDS = re.compile(r"\b(new|newest|latest|recent|just released|fresh)\b", re.I)
_FILLER = re.compile(r"\b(please|on spotify|in spotify|spotify|song|songs|track|music|play|the|a|latest|new|newest|"
                     r"recent|release|released|by|from|of)\b", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower().replace("&", " and ")).strip()


def _clean(query: str) -> str:
    return re.sub(r"\s+", " ", _FILLER.sub(" ", query or "")).strip()


def _words_in(words: str, text: str) -> bool:
    """Every word of `words` appears somewhere in `text` (order and punctuation ignored)."""
    w = _norm(words).split()
    t = _norm(text)
    return bool(w) and all(x in t for x in w)


# ── Finding the song: Spotify's own search (optional keys) ────────────────────

_token = {"value": "", "expires": 0.0}


def _creds() -> tuple[str, str]:
    try:
        from memory.config_manager import load_api_keys
        cfg = load_api_keys()
        return (cfg.get("spotify_client_id") or "").strip(), (cfg.get("spotify_client_secret") or "").strip()
    except Exception:
        return "", ""


def _api_get(path: str, params: dict) -> dict:
    import requests
    cid, secret = _creds()
    if time.time() >= _token["expires"]:
        r = requests.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                          auth=(cid, secret), timeout=_TIMEOUT)
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], expires=time.time() + j.get("expires_in", 3600) - 60)
    r = requests.get(f"https://api.spotify.com/v1{path}", params=params, timeout=_TIMEOUT,
                     headers={"Authorization": f"Bearer {_token['value']}"})
    r.raise_for_status()
    return r.json()


def _api_track(artist: str, song: str) -> tuple[str, str, str] | None:
    q = f"track:{song} artist:{artist}" if artist else song
    for item in _api_get("/search", {"q": q, "type": "track", "limit": 5}).get("tracks", {}).get("items", []):
        if _words_in(song, item["name"]):
            return ", ".join(a["name"] for a in item["artists"]), item["name"], item["uri"]
    return None


def _pad_date(d: str) -> str:
    return (d + "-01-01")[:10] if len(d) == 4 else (d + "-01")[:10] if len(d) == 7 else d


def _api_latest(artist: str) -> tuple[str, str, str, str] | None:
    """(artist, song, spotify uri, release date) for the artist's newest release."""
    found = _api_get("/search", {"q": artist, "type": "artist", "limit": 3}).get("artists", {}).get("items", [])
    who = next((a for a in found if _words_in(artist, a["name"])), None)
    if not who:
        return None
    albums = _api_get(f"/artists/{who['id']}/albums",
                      {"include_groups": "single,album", "limit": 20}).get("items", [])
    if not albums:
        return None
    newest = max(albums, key=lambda a: _pad_date(a.get("release_date", "")))
    tracks = _api_get(f"/albums/{newest['id']}/tracks", {"limit": 1}).get("items", [])
    if not tracks:
        return None
    return who["name"], tracks[0]["name"], tracks[0]["uri"], _pad_date(newest.get("release_date", ""))


# ── Finding the song: no keys ───────────────────────────────────────────────

def _itunes(artist: str) -> list[dict]:
    import requests
    r = requests.get("https://itunes.apple.com/search", timeout=_TIMEOUT,
                     params={"term": artist, "entity": "song", "attribute": "artistTerm", "limit": 200})
    r.raise_for_status()
    return [x for x in r.json().get("results", [])
            if x.get("trackName") and _words_in(artist, x.get("artistName", ""))]


def latest_release(artist: str) -> tuple[str, str, str] | None:
    """(artist as credited, song title, release date) for the artist's newest song."""
    rows = [x for x in _itunes(artist) if x.get("releaseDate")]
    if not rows:
        return None
    newest = max(x["releaseDate"][:10] for x in rows)
    top = sorted((x for x in rows if x["releaseDate"][:10] == newest),
                 key=lambda x: (x.get("trackNumber") or 99, x["trackName"]))[0]
    return top["artistName"], top["trackName"], newest


def top_song(artist: str) -> tuple[str, str] | None:
    """The artist's best-known song: the catalogue's first match for their name."""
    rows = _itunes(artist)
    return (rows[0]["artistName"], rows[0]["trackName"]) if rows else None


def _search_links(query: str, kind: str = "track") -> list[tuple[str, str, str]]:
    """[(kind, spotify id, page title)] for Spotify pages matching `query`.

    The search engine is flaky — it answers "no results" for a query it answered a
    minute ago — so the same question is asked three ways before giving up."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    for q in (f"site:open.spotify.com/{kind} {query}", f"{query} spotify {kind}",
              f"open.spotify.com {kind} {query}"):
        try:
            hits = list(DDGS().text(q, max_results=8))
        except Exception:
            time.sleep(0.4)
            continue
        out = []
        for hit in hits:
            m = _LINK.search(hit.get("href", ""))
            if m:
                out.append((m.group(1), m.group(2), hit.get("title", "")))
        if out:
            return out
    return []


def find_uri(artist: str, title: str, kind: str = "track") -> str | None:
    """The spotify: URI whose page title names `title` (and, if given, `artist`)."""
    best = None
    for k, ident, page in _search_links(f"{artist} {title}".strip(), kind):
        if title and _words_in(title, page) and (not artist or any(w in _norm(page) for w in _norm(artist).split())):
            return f"spotify:{k}:{ident}"
        if best is None and k == kind and (not title or _words_in(title, page)):
            best = f"spotify:{k}:{ident}"
    return best


# ── Talking to the desktop app ────────────────────────────────────────────────

def _spotify_windows() -> list[tuple[int, str]]:
    """Visible top-level Spotify windows as (handle, title). Windows only."""
    if _OS != "Windows":
        return []
    try:
        import psutil
        import win32gui
        import win32process
        pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "spotify.exe"}
        found: list[tuple[int, str]] = []

        def visit(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title and title not in ("Default IME", "MSCTFIME UI") \
                        and win32process.GetWindowThreadProcessId(hwnd)[1] in pids:
                    found.append((hwnd, title))
            return True

        win32gui.EnumWindows(visit, None)
        return found
    except Exception:
        return []


def _window_title() -> str:
    """Spotify's window title: 'Artist - Song' while playing, 'Spotify Free' otherwise."""
    wins = _spotify_windows()
    return next((t for _, t in wins if " - " in t), wins[0][1] if wins else "")


def _is_playing(title: str) -> bool:
    return " - " in title and not title.lower().startswith("spotify")


def _focus_spotify() -> bool:
    """Bring Spotify to the front and confirm it is there. Keys are only ever sent after
    this returns True, so they cannot land in whatever else was open."""
    wins = _spotify_windows()
    if not wins:
        return False
    try:
        import pyautogui
        import win32con
        import win32gui
        import win32process
        hwnd = wins[0][0]
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        pyautogui.press("alt")                # Windows only lets a foreground-less process steal focus after a key
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.35)
        front = win32gui.GetForegroundWindow()
        return win32process.GetWindowThreadProcessId(front)[1] == win32process.GetWindowThreadProcessId(hwnd)[1]
    except Exception:
        return False


def _open_uri(uri: str) -> None:
    if _OS == "Windows":
        os.startfile(uri)
    elif _OS == "Darwin":
        subprocess.Popen(["open", uri])
    else:
        subprocess.Popen(["xdg-open", uri])


def _press(key: str) -> bool:
    try:
        import pyautogui
        pyautogui.press(key)
        return True
    except Exception:
        return False


def _wait_title(song: str, seconds: float) -> str:
    """Poll until Spotify's title shows `song` playing (or any track when `song` is empty)."""
    end = time.time() + seconds
    title = _window_title()
    while time.time() < end:
        title = _window_title()
        if _is_playing(title) and (not song or _words_in(song, title)):
            return title
        time.sleep(0.4)
    return title


def _pause_current() -> None:
    """Spotify only auto-starts a track link when it is idle: with something playing, the link
    just opens the page. So stop what is playing first, and wait for the title to confirm."""
    if _is_playing(_window_title()):
        _press("playpause")
        end = time.time() + 2.5
        while time.time() < end and _is_playing(_window_title()):
            time.sleep(0.25)


def _start_highlighted(song: str) -> str:
    """The track's page is open with its row highlighted but nothing started: press Enter on it."""
    time.sleep(1.0)
    title = _window_title()
    for _ in range(2):
        if not _focus_spotify():
            return title
        _press("enter")
        title = _wait_title(song, 3.5)
        if _is_playing(title) and _words_in(song, title):
            return title
    return title


# ── Actions ─────────────────────────────────────────────────────────────────

def _resolve(artist: str, song: str, query: str, kind: str, latest: bool):
    """-> (artist, song, uri or None, note, error or None)."""
    keyed = all(_creds())
    note = ""
    if latest:
        who = artist or _clean(query)
        if not who:
            return artist, song, None, note, "Whose newest song should I play?"
        if keyed:
            try:
                got = _api_latest(who)
                if got:
                    return got[0], got[1], got[2], f" (newest, released {got[3]})", None
            except Exception:
                pass
        found = latest_release(who)
        if not found:
            return artist, song, None, note, (f"I couldn't find any releases for '{who}'. "
                                              "Tell me the song name and I'll play that.")
        artist, song, date = found
        return artist, song, find_uri(artist, song), f" (newest, released {date})", None

    if kind == "artist" or (not song and artist and not query):
        who = artist or _clean(query)
        top = top_song(who) if who else None
        if not top:
            return artist, song, None, note, f"I couldn't find songs by '{who}'."
        artist, song = top
        note = " (their top song)"
    elif not song:
        song = _clean(query) or query

    if keyed:
        try:
            got = _api_track(artist, song)
            if got:
                return got[0], got[1], got[2], note, None
        except Exception:
            pass
    return artist, song, find_uri(artist, song), note, None


def _play(p: dict) -> str:
    artist = (p.get("artist") or "").strip()
    song = (p.get("song") or "").strip()
    query = (p.get("query") or "").strip()
    kind = (p.get("kind") or "track").lower().strip()
    if kind not in ("track", "album", "artist", "playlist"):
        kind = "track"
    latest = bool(p.get("latest")) or (not song and bool(_LATEST_WORDS.search(query)))
    if not (artist or song or query):
        return "Tell me what to play — a song, an artist, or 'the newest song by …'."

    started = time.time()
    if kind in ("album", "playlist"):                     # these open as pages; there is no row to press Enter on
        uri = find_uri(artist, song or _clean(query), kind)
        if not uri:
            _open_uri(f"spotify:search:{quote(f'{artist} {song or _clean(query)}'.strip())}")
            return "I couldn't find that on Spotify, so I opened Spotify's search. It is not playing yet."
        _open_uri(uri)
        return f"Opened that {kind} in Spotify. Press the green play button to start it."

    artist, song, uri, note, error = _resolve(artist, song, query, kind, latest)
    if error:
        return error
    name = f"{song} by {artist}" if song and artist else (song or artist)
    if not uri:
        _open_uri(f"spotify:search:{quote(f'{artist} {song}'.strip())}")
        return (f"I couldn't pin down '{name}' on Spotify, so I opened Spotify's search for it. "
                "It is not playing yet.")

    before = _window_title()
    cold = not before
    if _is_playing(before) and _words_in(song, before):
        return f"{before} is already playing on Spotify."
    if _OS != "Windows":
        _open_uri(uri)
        return f"Opened {name} in Spotify{note}. Press play to start it."

    _pause_current()
    _open_uri(uri)
    title = _wait_title(song, 14 if cold else 5)          # an idle Spotify starts the song by itself
    if not (_is_playing(title) and _words_in(song, title)) and _spotify_windows():
        title = _start_highlighted(song)
    took = time.time() - started
    if _is_playing(title) and _words_in(song, title):
        return f"Playing {title} on Spotify{note}. ({took:.0f}s)"
    if not _spotify_windows():
        return ("Spotify didn't open. Is it installed and signed in? Open it once, then ask again.")
    if _is_playing(title):
        return (f"I opened {name} in Spotify{note}, but it is still playing {title}. "
                "The song's page is open — pressing play on it will start it.")
    return (f"I opened {name} in Spotify{note}, but it hasn't started playing. "
            "It may be waiting for you to sign in or press play.")


def _control(action: str) -> str:
    key = {"pause": "playpause", "resume": "playpause", "play": "playpause", "toggle": "playpause",
           "next": "nexttrack", "skip": "nexttrack", "previous": "prevtrack", "back": "prevtrack"}[action]
    before = _window_title()
    if action == "pause" and before and not _is_playing(before):
        return "Spotify isn't playing anything right now."
    if action in ("resume", "play") and _is_playing(before):
        return f"Already playing {before}."
    if not _press(key):
        return "I couldn't send the media key."
    if action in ("next", "skip", "previous", "back"):
        time.sleep(1.2)
        now = _window_title()
        return f"Now playing {now}." if _is_playing(now) else "Skipped."
    return {"pause": "Paused.", "resume": "Resumed.", "play": "Resumed.", "toggle": "Toggled."}[action]


def spotify(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "play").lower().strip()
    try:
        if action == "play":
            return _play(p)
        if action == "now_playing":
            t = _window_title()
            return f"Now playing {t}." if _is_playing(t) else "Nothing is playing in Spotify right now."
        if action in ("pause", "resume", "next", "skip", "previous", "back", "toggle"):
            return _control(action)
        return "Unknown action. Use play, pause, resume, next, previous or now_playing."
    except Exception as e:
        return f"Spotify error: {type(e).__name__}: {e}"


TOOL = {
    "name": "spotify",
    "description": (
        "Plays music in the Spotify app and controls it. ALWAYS use this — not browser, screen or "
        "app-opening tools — for 'play <song> on Spotify', 'play Arjan Dhillon's new song', 'play the "
        "latest song by <artist>', 'play <artist>', 'play <album/playlist>', 'pause the music', 'next "
        "song', 'previous song', 'what's playing'. For 'new/latest song by an artist', pass the artist "
        "and latest=true. Takes up to ten seconds; the result says what is really playing."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "play (default) | pause | resume | next | previous | now_playing"},
            "artist": {"type": "STRING", "description": "The artist, e.g. 'Arjan Dhillon'"},
            "song": {"type": "STRING", "description": "The song, album or playlist name, if one was named"},
            "latest": {"type": "BOOLEAN", "description": "True for 'new', 'latest' or 'newest' song by the artist"},
            "kind": {"type": "STRING", "description": "track (default) | album | artist | playlist"},
            "query": {"type": "STRING", "description": "The request as said, if it doesn't split into artist and song"},
        },
        "required": [],
    },
    "handler": spotify,
}
