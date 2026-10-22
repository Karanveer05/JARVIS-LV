# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
Shared folder resolution for actions. The leading underscore keeps the action
loader from treating this as a tool.

The Desktop is not always C:\\Users\\<you>\\Desktop — with OneDrive folder
backup it lives under OneDrive — so it is asked of Windows, not guessed.
"""
import os
import platform
import re
from pathlib import Path

_FOLDER_IDS = {  # name -> registry value under "User Shell Folders"
    "desktop":   "Desktop",
    "documents": "Personal",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures":  "My Pictures",
    "music":     "My Music",
    "videos":    "My Video",
}


def known_folder(name: str) -> Path:
    key = name.strip().lower()
    home = Path.home()
    if platform.system() == "Windows" and key in _FOLDER_IDS:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as k:
                value, _ = winreg.QueryValueEx(k, _FOLDER_IDS[key])
            p = Path(os.path.expandvars(value))
            if p.exists():
                return p
        except Exception:
            pass
    fallback = home / key.capitalize()
    return fallback if fallback.exists() else home


def resolve_folder(text: str, default: str = "desktop") -> Path:
    """'desktop' | 'documents' | 'downloads' | a real path. Returns a Path."""
    raw = (text or default).strip().strip('"')
    if raw.lower() in _FOLDER_IDS:
        return known_folder(raw)
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def inside_home(path: Path) -> bool:
    """True when `path` is somewhere under the user's profile. Tools that write
    files use this so a bad parameter cannot drop a file into C:\\Windows."""
    try:
        path.resolve().relative_to(Path.home().resolve())
        return True
    except ValueError:
        return False


def safe_filename(name: str, default: str = "file") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip()).strip(" .")
    return name[:120] or default


def unique_path(path: Path) -> Path:
    """Never overwrite: report-1.txt, report-2.txt, ..."""
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(path)
