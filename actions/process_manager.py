# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
process_manager — see what is running, what is hogging the PC, and stop things.

Listing is free. Stopping a program goes through the HUD confirmation gate
(core/confirm.py): the user presses CONFIRM or it does not happen. A short list of
Windows-critical processes — and JARVIS itself — can never be stopped from here.
"""
import os

import psutil

from core import confirm

_PROTECTED = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe",
    "explorer.exe", "taskmgr.exe", "sihost.exe", "ctfmon.exe", "memory compression",
    "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "securityhealthservice.exe", "msmpeng.exe", "audiodg.exe",
    "init", "systemd", "launchd", "kernel_task",
}


def _own_pids() -> set[int]:
    """This process and every ancestor and child — stopping any of them stops JARVIS."""
    pids = {os.getpid()}
    try:
        me = psutil.Process()
        pids.update(p.pid for p in me.parents())
        pids.update(p.pid for p in me.children(recursive=True))
    except Exception:
        pass
    return pids


def _mb(n: int) -> int:
    return round(n / (1024 * 1024))


def _snapshot() -> list[dict]:
    """One entry per program name, memory and CPU summed over its processes."""
    psutil.cpu_percent(None)
    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))
    for p in procs:                       # prime per-process CPU counters
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    import time
    time.sleep(0.4)
    agg: dict[str, dict] = {}
    for p in procs:
        try:
            name = p.info["name"] or "?"
            if name == "System Idle Process":      # unused CPU, not a program
                continue
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            cpu = p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        e = agg.setdefault(name, {"name": name, "count": 0, "mem": 0, "cpu": 0.0, "pids": []})
        e["count"] += 1
        e["mem"] += mem
        e["cpu"] += cpu
        e["pids"].append(p.pid)
    return list(agg.values())


def _list(sort: str, n: int) -> str:
    snap = _snapshot()
    ncpu = psutil.cpu_count() or 1
    key = (lambda e: e["cpu"]) if sort == "cpu" else (lambda e: e["mem"])
    top = sorted(snap, key=key, reverse=True)[:n]
    head = f"Top {len(top)} programs by {'CPU' if sort == 'cpu' else 'memory'}:"
    lines = [f"{e['name']}: {_mb(e['mem'])} MB, CPU {e['cpu'] / ncpu:.0f}%"
             + (f" ({e['count']} processes)" if e["count"] > 1 else "") for e in top]
    return head + "\n" + "\n".join(lines)


def _find(name: str) -> list[psutil.Process]:
    target = name.strip().lower()
    if not target:
        return []
    hits = []
    for p in psutil.process_iter(["pid", "name"]):
        pn = (p.info["name"] or "").lower()
        if pn == target or pn == target + ".exe" or pn.rsplit(".", 1)[0] == target:
            hits.append(p)
    return hits


def _kill_all(pids: list[int]) -> str:
    stopped, failed = 0, 0
    procs = []
    for pid in pids:
        try:
            p = psutil.Process(pid)
            p.terminate()
            procs.append(p)
        except psutil.NoSuchProcess:
            continue
        except Exception:
            failed += 1
    gone, alive = psutil.wait_procs(procs, timeout=3)
    stopped += len(gone)
    for p in alive:
        try:
            p.kill()
            stopped += 1
        except Exception:
            failed += 1
    return f"Stopped {stopped} process{'es' if stopped != 1 else ''}." + (
        f" {failed} could not be stopped (access denied)." if failed else "")


def process_manager(parameters: dict, player=None) -> str:
    action = str(parameters.get("action", "list")).strip().lower()
    name = str(parameters.get("name", "")).strip()

    if action in ("list", "top", "show"):
        sort = "cpu" if str(parameters.get("sort", "memory")).lower().startswith("c") else "memory"
        try:
            n = max(1, min(int(parameters.get("count", 8) or 8), 25))
        except (TypeError, ValueError):
            n = 8
        return _list(sort, n)

    if action in ("find", "status", "is_running", "check"):
        hits = _find(name)
        if not hits:
            return f"{name or 'That program'} is not running."
        mem = sum(p.memory_info().rss for p in hits if p.is_running())
        return f"{name} is running ({len(hits)} process{'es' if len(hits) != 1 else ''}, {_mb(mem)} MB)."

    if action in ("kill", "stop", "close", "end", "terminate"):
        if not name:
            return "Which program should I stop?"
        if name.lower() in _PROTECTED or name.lower() + ".exe" in _PROTECTED:
            return f"I won't stop {name}: it is a core system process."
        hits = _find(name)
        if not hits:
            return f"{name} is not running."
        own = _own_pids()
        pids = [p.pid for p in hits if p.pid not in own]
        if not pids:
            return f"{name} is part of JARVIS itself, so I won't stop it."
        if confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return confirm.request(
            key=f"kill_{name.lower()}", title=f"Stop {name}",
            detail=f"Force-closes {len(pids)} running process{'es' if len(pids) != 1 else ''} of "
                   f"{name}. Unsaved work in it will be lost.",
            run=lambda: _kill_all(pids),
        )

    return "Process actions are: list, find, kill."


TOOL = {
    "name": "process_manager",
    "description": (
        "See what is running and stop programs. Use for 'what is using my CPU/RAM', "
        "'is Chrome running', 'kill Spotify', 'force close that frozen app'. Stopping "
        "a program puts a confirmation on the user's screen and does NOT happen until "
        "they press it — never claim it is done. To close a window normally use "
        "computer_settings close_app instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "list | find | kill (default list)"},
            "name": {"type": "STRING", "description": "Program name for find / kill, e.g. 'chrome' or 'spotify.exe'"},
            "sort": {"type": "STRING", "description": "For list: memory (default) or cpu"},
            "count": {"type": "STRING", "description": "For list: how many programs, default 8"},
        },
        "required": [],
    },
    "handler": process_manager,
}
