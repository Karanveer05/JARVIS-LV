# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
Builds dist/JARVIS-Setup.exe - ONE file you copy to another Windows PC.

    python portable/build_installer.py

WHAT GOES IN  (an allow-list: a file is only included if a pattern below names it)
    the program (main.py, ui*.py, actions/, core/, plugins/_template.py, tests/ ...),
    requirements.txt, setup.py, the licence and README, and the OFFLINE SPEECH MODEL
    (models/vosk) so offline mode works on the new PC.

WHAT CANNOT GO IN  (nothing here matches the allow-list, and a second check refuses to build anyway)
    config/api_keys.json (Gemini key, settings, plugin credentials), memory/*.json (chats, facts,
    notes, lessons, routines, offline history), config/vault.*, WhatsApp / OAuth sessions,
    AI-built plugins (plugins/ai_*.py), caches, .git.

THEN IT CHECKS ITSELF
    Reads the secrets that exist on THIS PC (the API key, any other key / secret / token in
    config/api_keys.json, and your account name) and searches every byte of the payload for them.
    Any hit stops the build. The values themselves are never printed.

HOW THE EXE WORKS
    Windows' built-in IExpress wraps install.cmd + install.ps1 + payload.zip into one self-extracting
    program. IExpress cannot keep folders, which is why the program travels as payload.zip inside it.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DIST = ROOT / "dist"
EXE = DIST / "JARVIS-Setup.exe"

ALLOW = [
    "main.py", "ui.py", "ui_chat.py", "open_close_apps.py", "setup.py", "requirements.txt",
    "readme.md", "LICENSE",
    "actions/*.py",
    "core/*.py", "core/prompt.txt", "core/face_model.obj",
    "config/__init__.py", "config/jarvis.ico",
    "memory/__init__.py", "memory/config_manager.py", "memory/memory_manager.py",
    "plugins/__init__.py", "plugins/_template.py",
    "tests/*.py",
    "models/vosk/**/*",               # the offline speech model (code + model travel; user data does not)
]
# Second line of defence: even if an allow pattern is widened by mistake, these abort the build.
FORBIDDEN_NAMES = [
    "api_keys.json", "long_term.json", "chats.json", "notes.json", "notes.corrupt.json",
    "offline_history.json", "lessons.json", "routines.json", "ui_state.json",
    "vault.enc", "vault.key", ".env", ".env.*", "token*.json", "client_secret*.json", "*credentials*.json",
    "*.log", "*.db", "*.pyc", "ai_*.py",
]
FORBIDDEN_DIRS = {"__pycache__", ".git", "whatsapp_web", ".venv", "venv", "dist", "downloads", "screenshots", "logs"}


def collect() -> list[Path]:
    seen: dict[str, Path] = {}
    for pat in ALLOW:
        for p in ROOT.glob(pat):
            if p.is_file():
                seen[p.relative_to(ROOT).as_posix()] = p
    return [seen[k] for k in sorted(seen)]


def forbidden_reason(rel: str) -> str:
    parts = rel.split("/")
    for d in parts[:-1]:
        if d in FORBIDDEN_DIRS:
            return f"inside forbidden folder '{d}'"
    name = parts[-1]
    for pat in FORBIDDEN_NAMES:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            return f"matches forbidden name '{pat}'"
    return ""


def local_secrets() -> list[bytes]:
    """Values that must never appear in the payload. Never printed, only counted."""
    vals: set[str] = set()
    cfg = ROOT / "config" / "api_keys.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            for k, v in data.items():
                if isinstance(v, str) and len(v) >= 8 and any(w in k.lower() for w in ("key", "secret", "token", "pass")):
                    vals.add(v)
        except Exception:
            pass
    # Anything long in the vault key file / personal names of this PC's account.
    for name in (os.environ.get("USERNAME", ""), os.environ.get("USER", "")):
        if len(name) >= 4:
            vals.add(name)
    vals.add("nirbh")
    vals.add("karanveer")
    return sorted((v.encode("utf-8") for v in vals if v), key=len)


# The author credit is public on purpose (copyright headers, LICENSE, About text), so it is
# cut out before scanning. Any other appearance of the name - an e-mail, a path - still stops the build.
PUBLIC_CREDITS = [b"karanveer singh", b"karanveer05"]


def scan(data: bytes, secrets: list[bytes]) -> int:
    low = data.lower()
    for c in PUBLIC_CREDITS:
        low = low.replace(c, b"")
    return sum(1 for s in secrets if s.lower() in low)


def stamp_icon(exe: Path, ico: Path) -> str:
    """Give JARVIS-Setup.exe the JARVIS icon (IExpress offers no way to choose one).
    Rewrites the icon resources of the finished exe; the packed installer data is left alone.
    Returns '' on success, or the reason it was skipped - the installer still works without it."""
    try:
        import struct
        import win32api
        import win32con
    except ImportError:
        return "pywin32 is not installed"
    try:
        data = ico.read_bytes()
        _, kind, count = struct.unpack("<HHH", data[:6])
        if kind != 1 or not count:
            return "not a valid .ico file"
        entries = [struct.unpack("<BBBBHHII", data[6 + 16 * i: 22 + 16 * i]) for i in range(count)]

        h = win32api.LoadLibraryEx(str(exe), 0, 0x2)            # LOAD_LIBRARY_AS_DATAFILE
        try:
            names = win32api.EnumResourceNames(h, win32con.RT_GROUP_ICON)
            icons = win32api.EnumResourceNames(h, win32con.RT_ICON)
            group = names[0] if names else 1
            langs = win32api.EnumResourceLanguages(h, win32con.RT_GROUP_ICON, group) if names else [1033]
            lang = langs[0] if langs else 1033
        finally:
            win32api.FreeLibrary(h)
        first_id = (max([i for i in icons if isinstance(i, int)] or [0]) + 1)

        grp = struct.pack("<HHH", 0, 1, count)
        upd = win32api.BeginUpdateResource(str(exe), False)
        try:
            for i, (w, hh, colors, _r, planes, bits, size, off) in enumerate(entries):
                rid = first_id + i
                win32api.UpdateResource(upd, win32con.RT_ICON, rid, data[off: off + size], lang)
                grp += struct.pack("<BBBBHHIH", w, hh, colors, 0, planes, bits, size, rid)
            win32api.UpdateResource(upd, win32con.RT_GROUP_ICON, group, grp, lang)
        except Exception:
            win32api.EndUpdateResource(upd, True)               # discard: leave the exe untouched
            raise
        win32api.EndUpdateResource(upd, False)
        return ""
    except Exception as e:                                       # never break the build over a cosmetic step
        return f"{type(e).__name__}: {e}"


def main() -> int:
    files = collect()
    if not files:
        print("Nothing matched the allow-list - run this from the project.")
        return 1

    bad = [(p, forbidden_reason(p.relative_to(ROOT).as_posix())) for p in files]
    bad = [(p, r) for p, r in bad if r]
    if bad:
        for p, r in bad:
            print(f"REFUSED  {p.relative_to(ROOT).as_posix()}  ({r})")
        print("\nBuild stopped: personal data would have been packed.")
        return 1

    secrets = local_secrets()
    hits = []
    for p in files:
        n = scan(p.read_bytes(), secrets)
        if n:
            hits.append((p.relative_to(ROOT).as_posix(), n))
    if hits:
        for rel, n in hits:
            print(f"SECRET/NAME FOUND in {rel} ({n} match{'es' if n > 1 else ''}) - value not shown")
        print("\nBuild stopped: remove it from that file first.")
        return 1

    DIST.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="jarvis-build-"))
    try:
        payload = work / "payload.zip"
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in files:
                z.write(p, p.relative_to(ROOT).as_posix())
            z.write(HERE / "uninstall.ps1", "uninstall.ps1")
        for name in ("install.cmd", "install.ps1"):
            shutil.copy2(HERE / name, work / name)

        # Re-open the finished payload and check what is really inside it.
        with zipfile.ZipFile(payload) as z:
            names = z.namelist()
            for n in names:
                r = forbidden_reason(n)
                if r:
                    print(f"REFUSED in payload: {n} ({r})")
                    return 1
            leaks = [n for n in names if scan(z.read(n), secrets)]
        if leaks:
            print("SECRET/NAME FOUND in payload:", ", ".join(leaks))
            return 1

        target = EXE.resolve()
        if target.exists():
            target.unlink()
        sed = work / "jarvis.sed"
        sed.write_text(
            "[Version]\nClass=IEXPRESS\nSEDVersion=3\n"
            "[Options]\nPackagePurpose=InstallApp\nShowInstallProgramWindow=0\nHideExtractAnimation=1\n"
            "UseLongFileName=1\nInsideCompressed=0\nCAB_FixedSize=0\nCAB_ResvCodeSigning=0\nRebootMode=N\n"
            "InstallPrompt=%InstallPrompt%\nDisplayLicense=%DisplayLicense%\nFinishMessage=%FinishMessage%\n"
            "TargetName=%TargetName%\nFriendlyName=%FriendlyName%\nAppLaunched=%AppLaunched%\n"
            "PostInstallCmd=%PostInstallCmd%\nAdminQuietInstCmd=%AdminQuietInstCmd%\nUserQuietInstCmd=%UserQuietInstCmd%\n"
            "SourceFiles=SourceFiles\n"
            "[Strings]\nInstallPrompt=\nDisplayLicense=\nFinishMessage=\n"
            f"TargetName={target}\nFriendlyName=JARVIS Setup\nAppLaunched=cmd /c install.cmd\n"
            "PostInstallCmd=<None>\nAdminQuietInstCmd=\nUserQuietInstCmd=\n"
            'FILE0="install.cmd"\nFILE1="install.ps1"\nFILE2="payload.zip"\n'
            f"[SourceFiles]\nSourceFiles0={work}\\\n[SourceFiles0]\n%FILE0%=\n%FILE1%=\n%FILE2%=\n",
            encoding="ascii")
        r = subprocess.run(["iexpress.exe", "/N", "/Q", str(sed)], capture_output=True, text=True, timeout=600)
        if not target.exists():
            print("IExpress did not produce the installer.\n", r.stdout, r.stderr)
            return 1

        problem = stamp_icon(target, ROOT / "config" / "jarvis.ico")
        print("Icon              : " + ("config/jarvis.ico stamped on the .exe" if not problem else f"NOT applied ({problem})"))
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"Files packed      : {len(files)} + uninstall.ps1")
        print(f"Secrets checked   : {len(secrets)} values, 0 found in any file")
        print(f"Installer         : {target}")
        print(f"Size              : {target.stat().st_size / 1_000_000:.1f} MB")
        print(f"SHA-256           : {sha}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
