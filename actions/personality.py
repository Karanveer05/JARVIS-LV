# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
personality — change how JARVIS talks, by voice or text.

The same choice is in Settings. It is saved, and the session is rebuilt a few seconds later
(after JARVIS has had time to answer this request) so the new tone takes effect. The
conversation so far is not carried over — the new personality starts fresh.
"""
import threading

from memory.config_manager import DEFAULT_PERSONALITY, PERSONALITIES, get_personality, normalize_personality, save_personality


def _apply_later(player, seconds: float = 3.5) -> None:
    def go():
        cb = getattr(player, "on_personality_change", None)
        if callable(cb):
            cb()
    threading.Timer(seconds, go).start()


def personality(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "set").lower().strip()
    style = (p.get("style") or "").strip()
    current = get_personality()

    if action in ("list", "options") or (action == "set" and not style):
        menu = ", ".join(label for label, _ in PERSONALITIES.values())
        return f"Current personality: {PERSONALITIES[current][0]}. Available: {menu}."
    if action == "current":
        return f"Current personality: {PERSONALITIES[current][0]}."

    key = normalize_personality(style)
    if action == "reset":
        key = DEFAULT_PERSONALITY
    named = style.lower() in PERSONALITIES or key != DEFAULT_PERSONALITY or "default" in style.lower() \
        or "normal" in style.lower() or action == "reset"
    if not named:
        menu = ", ".join(PERSONALITIES)
        return f"I don't have a '{style}' personality. Choose one of: {menu}."
    if key == current:
        return f"I'm already in {PERSONALITIES[key][0]} mode."
    save_personality(key)
    _apply_later(player)
    return (f"Switching to {PERSONALITIES[key][0]} mode. Tell the user in ONE short sentence, in that new style, that "
            f"you are switching — the change applies in a few seconds, and this conversation starts fresh.")


TOOL = {
    "name": "personality",
    "description": (
        "Changes HOW the assistant talks (its personality). Use for 'be more casual', 'talk like you're angry', "
        "'be grumpy', 'be funny / humorous', 'be professional', 'be sarcastic', 'be friendly', 'calm down', "
        "'be hyped', 'go back to normal', 'what personalities do you have'. Options: default, professional, "
        "casual, funny, grumpy (angry), sarcastic, friendly, calm, hype."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "set (default) | list | current | reset"},
            "style": {"type": "STRING", "description": "The personality: casual, professional, funny, grumpy/angry, sarcastic, friendly, calm, hype, default"},
        },
        "required": [],
    },
    "handler": personality,
}
