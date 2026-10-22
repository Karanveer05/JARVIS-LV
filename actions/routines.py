# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
routines — "learn my routines" (part of Self-improvement in Settings).

Shows the habits JARVIS has noticed (the same request at about the same time on several days) and forgets them on
request. Noticing happens automatically while the switch is on; see core/routines.py.
"""
from core import routines as store
from memory.config_manager import get_si


def routines(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "list").lower().strip()
    if action in ("forget", "delete", "clear", "remove"):
        return store.forget(p.get("name") or p.get("text") or "")
    if action in ("list", "show", "status"):
        if not get_si("routines"):
            return ("Learning your routines is switched off (Settings > Self-improvement > Learn my routines), "
                    "so I'm not noting anything new.")
        found = store.patterns()
        if not found:
            return "I haven't noticed a routine yet — it takes the same request, around the same time, on three different days."
        return "Routines I've noticed — you usually:\n" + "\n".join(f"- {store.describe(x)}" for x in found[:8])
    return "Unknown action. Use list or forget."


TOOL = {
    "name": "routines",
    "description": (
        "The habits JARVIS has noticed about the user (apps they open, music they play, sites they visit at about the same "
        "time on several days). Use for 'what are my routines', 'what habits have you noticed', 'forget my routines' "
        "(action=forget, name = a few words or 'all'). Never act on a routine without the user saying yes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list (default) | forget"},
            "name": {"type": "STRING", "description": "forget: a few words of the routine, or 'all'"},
        },
        "required": [],
    },
    "handler": routines,
}
