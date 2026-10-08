"""
image_generate — draw a picture from a description, save it on this PC, and show it in the chat.

Two sources, tried in order:
  1. Gemini's own image models — clean, high quality. They need a Gemini API key with billing
     enabled; on a free-tier key Google gives them a quota of zero, so they fail at once and the
     tool remembers that for ten minutes instead of asking again.
  2. Pollinations (image.pollinations.ai) — a free public service that needs no key. Pictures are
     smaller (about 768 px) and carry a small "pollinations.ai" mark. The description text is sent
     to that service to make the picture — the same as typing it into any web image generator.

Pictures are saved to Pictures/JARVIS Images (never overwriting), and the chat shows them with
Open / Save as… / Show in folder buttons.
"""
import os
import platform
import random
import re
import subprocess
import time
import urllib.parse
from io import BytesIO
from pathlib import Path

from actions._paths import known_folder, safe_filename, unique_path

_TIMEOUT = 90
_GEMINI_MODELS = ("gemini-3.1-flash-image", "gemini-2.5-flash-image", "gemini-3-pro-image")
_ASPECTS = {"square": (1024, 1024, "square"), "wide": (1024, 576, "wide 16:9 landscape"),
            "landscape": (1024, 576, "wide 16:9 landscape"), "tall": (576, 1024, "tall 9:16 portrait"),
            "portrait": (576, 1024, "tall 9:16 portrait")}
_gemini_blocked_until = 0.0

# The free service does no filtering of its own, so the obvious things are refused here. Gemini's models
# apply their own safety rules.
_REFUSE = re.compile(
    r"\b(nude|naked|nsfw|porn\w*|sex(?:ual|y)?|erotic|hentai|genitals?|topless|gore|beheading|"
    r"child\s*(?:porn|abuse)|loli\w*|undress\w*)\b", re.I)


def _out_dir() -> Path:
    d = known_folder("pictures") / "JARVIS Images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gemini_image(prompt: str, aspect_hint: str) -> bytes | None:
    """PNG/JPEG bytes from a Gemini image model, or None when none is available to this key."""
    global _gemini_blocked_until
    if time.time() < _gemini_blocked_until:
        return None
    try:
        from google.genai import types
        from core import gemini
        client = gemini.client(timeout_ms=_TIMEOUT * 1000)
    except Exception:
        return None
    quota_hits = 0
    for model in _GEMINI_MODELS:
        try:
            r = client.models.generate_content(
                model=model, contents=f"{prompt}. Composition: {aspect_hint}.",
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]))
            for part in (r.candidates[0].content.parts if r.candidates else []):
                data = getattr(getattr(part, "inline_data", None), "data", None)
                if data:
                    return data
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                quota_hits += 1
            continue
    if quota_hits == len(_GEMINI_MODELS):
        _gemini_blocked_until = time.time() + 600
    return None


def _pollinations_image(prompt: str, width: int, height: int) -> bytes:
    import requests
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
    r = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 JARVIS"},
                     params={"width": width, "height": height, "nologo": "true", "model": "flux",
                             "seed": random.randint(1, 10_000_000)})
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image"):
        raise RuntimeError("the image service did not return a picture")
    return r.content


def _save(data: bytes, prompt: str) -> Path:
    from PIL import Image
    img = Image.open(BytesIO(data))
    img.load()
    if img.width < 64 or img.height < 64:
        raise RuntimeError("the picture that came back was empty")
    slug = safe_filename("_".join(re.findall(r"[A-Za-z0-9]+", prompt)[:6]).lower(), "image")
    path = unique_path(_out_dir() / f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}.png")
    img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(path, "PNG")
    return path


def _open(path: Path) -> None:
    if platform.system() == "Windows":
        os.startfile(str(path))
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def image_generate(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "generate").lower().strip()
    if action == "open_folder":
        _open(_out_dir())
        return f"Opened {_out_dir()}."
    if action == "list":
        files = sorted(_out_dir().glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
        return ("Latest pictures:\n" + "\n".join(f"- {f}" for f in files)) if files else "No pictures generated yet."

    prompt = " ".join((p.get("prompt") or "").split())
    if len(prompt) < 3:
        return "What should the picture show?"
    style = (p.get("style") or "").strip()
    if _REFUSE.search(prompt) or _REFUSE.search(style):
        return "I can't make that kind of picture. Ask for something else and I'll draw it."
    full = f"{prompt}, {style}" if style else prompt
    width, height, hint = _ASPECTS.get((p.get("aspect") or "square").lower().strip(), _ASPECTS["square"])

    started = time.time()
    source, data, last_error = "", None, ""
    got = _gemini_image(full, hint)
    if got:
        source, data = "Gemini", got
    else:
        try:
            data = _pollinations_image(full, width, height)
            source = "the free image service"
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:120]}"
    if not data:
        return (f"I couldn't make the picture right now ({last_error or 'no image service answered'}). "
                "Check the internet connection and try again.")
    try:
        path = _save(data, prompt)
    except Exception as e:
        return f"The picture came back damaged and wasn't saved ({e})."

    try:
        if player:
            player.write_log(f"IMAGE: {path}")
    except Exception:
        pass
    note = "" if source == "Gemini" else " It has a small watermark and is lower resolution — a Gemini key with billing enabled makes clean, larger ones."
    return (f"Made the picture with {source} in {time.time() - started:.0f}s and saved it to {path}. "
            f"It is showing in the chat with Open, Save as and Show in folder buttons.{note} "
            "Tell the user it is ready in one short sentence; do not read the file path aloud.")


TOOL = {
    "name": "image_generate",
    "description": (
        "Generates a picture from a description and saves it on the computer (Pictures/JARVIS Images), also showing it in "
        "the chat. ALWAYS use this — not web_search, browser or screenshot tools — for 'generate / create / draw / make / "
        "paint a picture, image, photo, logo, wallpaper or illustration of …'. Write `prompt` as a vivid, specific "
        "description in English. Takes about 5-15 seconds. Also: action=open_folder to open the pictures folder, "
        "action=list for the latest ones. For editing or describing an existing image, use file_processor instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "generate (default) | open_folder | list"},
            "prompt": {"type": "STRING", "description": "A vivid description of the picture, in English"},
            "style": {"type": "STRING", "description": "Optional look: 'watercolor', 'photorealistic', 'anime', 'flat vector logo', '3D render'…"},
            "aspect": {"type": "STRING", "description": "square (default) | wide | tall"},
        },
        "required": [],
    },
    "handler": image_generate,
}
