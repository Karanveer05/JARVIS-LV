"""
file_tools — what file_controller does not do: look INSIDE files, find duplicates,
find what changed recently, and zip / unzip.

Everything works only inside the user's own folder. Nothing here deletes or
overwrites: new archives and extracted folders get a fresh name (name-1, name-2...),
and the duplicate finder only reports — removing a file is a separate, deliberate
step through file_controller.
"""
import hashlib
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

from actions._paths import inside_home, resolve_folder, unique_path

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData", ".idea", ".vscode",
              "$RECYCLE.BIN", "System Volume Information"}
_TEXT_EXT = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css", ".xml", ".yml",
             ".yaml", ".ini", ".cfg", ".log", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
             ".sql", ".sh", ".bat", ".ps1", ".tex", ".rtf", ".toml"}
_MAX_FILE = 2 * 1024 * 1024
_MAX_SCAN = 40_000                  # files visited per call
_MAX_UNZIP_BYTES = 2 * 1024**3
_MAX_UNZIP_FILES = 20_000


def _walk(root: Path):
    seen = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in files:
            seen += 1
            if seen > _MAX_SCAN:
                return
            yield Path(dirpath) / f


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _folder(parameters: dict, key: str = "folder", default: str = "documents") -> tuple[Path | None, str]:
    p = resolve_folder(str(parameters.get(key, "")), default)
    if not inside_home(p):
        return None, "I only work inside your user folder."
    if not p.exists():
        return None, f"{p} does not exist."
    return p, ""


def _search(parameters: dict) -> str:
    text = str(parameters.get("text", "")).strip()
    if len(text) < 2:
        return "What text should I look for?"
    root, err = _folder(parameters)
    if err:
        return err
    exts = {("." + e.strip().lower().lstrip(".")) for e in str(parameters.get("extension", "")).split(",") if e.strip()}
    needle = text.lower()
    hits, files = [], 0
    for f in _walk(root):
        ext = f.suffix.lower()
        if (exts and ext not in exts) or (not exts and ext not in _TEXT_EXT):
            continue
        try:
            if f.stat().st_size > _MAX_FILE:
                continue
            files += 1
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for no, line in enumerate(fh, 1):
                    if needle in line.lower():
                        hits.append(f"{f}:{no}: {line.strip()[:100]}")
                        break                     # one hit per file keeps the list readable
        except OSError:
            continue
        if len(hits) >= 15:
            break
    if not hits:
        return f"No text file in {root} contains '{text}' ({files} files searched)."
    more = " (showing the first 15)" if len(hits) >= 15 else ""
    return f"Found '{text}' in {len(hits)} file{'s' if len(hits) != 1 else ''}{more}:\n" + "\n".join(hits)


def _hash(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        remaining = limit
        while True:
            chunk = fh.read(1 << 20 if remaining is None else min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining <= 0:
                    break
    return h.hexdigest()


def _duplicates(parameters: dict) -> str:
    root, err = _folder(parameters, default="downloads")
    if err:
        return err
    by_size: dict[int, list[Path]] = {}
    for f in _walk(root):
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size >= 1024:
            by_size.setdefault(size, []).append(f)
    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        quick: dict[str, list[Path]] = {}
        for p in paths:
            try:
                quick.setdefault(_hash(p, 65536), []).append(p)
            except OSError:
                continue
        for same in quick.values():
            if len(same) < 2:
                continue
            full: dict[str, list[Path]] = {}
            for p in same:
                try:
                    full.setdefault(_hash(p), []).append(p)
                except OSError:
                    continue
            groups += [(size, g) for g in full.values() if len(g) > 1]
    if not groups:
        return f"No duplicate files found in {root}."
    groups.sort(key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)
    wasted = sum(s * (len(g) - 1) for s, g in groups)
    lines = [f"{len(groups)} set(s) of identical files in {root}, wasting about {_human(wasted)}:"]
    for size, g in groups[:6]:
        lines.append(f"{_human(size)} x {len(g)}: " + "  |  ".join(p.name for p in g[:4]))
    lines.append("Nothing was deleted.")
    return "\n".join(lines)


def _recent(parameters: dict) -> str:
    root, err = _folder(parameters, default="documents")
    if err:
        return err
    try:
        days = max(1, min(int(parameters.get("days", 3) or 3), 365))
    except (TypeError, ValueError):
        days = 3
    cutoff = time.time() - days * 86400
    found = []
    for f in _walk(root):
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if m >= cutoff:
            found.append((m, f))
    if not found:
        return f"Nothing in {root} changed in the last {days} day{'s' if days != 1 else ''}."
    found.sort(reverse=True)
    lines = [f"{datetime.fromtimestamp(m):%b %d %H:%M}  {f}" for m, f in found[:12]]
    return f"{len(found)} file(s) changed in the last {days} day(s); newest:\n" + "\n".join(lines)


def _zip(parameters: dict) -> str:
    src = resolve_folder(str(parameters.get("path", "")), "")
    if not str(parameters.get("path", "")).strip():
        return "What should I zip?"
    if not inside_home(src) or not src.exists():
        return "I can only zip something that exists inside your user folder."
    out = unique_path(src.parent / f"{src.stem if src.is_file() else src.name}.zip")
    try:
        count = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            if src.is_file():
                zf.write(src, src.name)
                count = 1
            else:
                for dirpath, _dirs, files in os.walk(src):     # everything, hidden included
                    for name in files:
                        f = Path(dirpath) / name
                        if f == out or f.is_symlink():
                            continue
                        zf.write(f, f.relative_to(src.parent))
                        count += 1
    except Exception as e:
        out.unlink(missing_ok=True)
        return f"Zipping failed: {e}"
    return f"Zipped {count} file{'s' if count != 1 else ''} ({_human(out.stat().st_size)}) to {out}."


def _unzip(parameters: dict) -> str:
    src = resolve_folder(str(parameters.get("path", "")), "")
    if not str(parameters.get("path", "")).strip() or not src.is_file() or not inside_home(src):
        return "Give the path of a .zip file inside your user folder."
    if not zipfile.is_zipfile(src):
        return f"{src.name} is not a valid zip file."
    dest_raw = str(parameters.get("dest", "")).strip()
    dest = resolve_folder(dest_raw, "") if dest_raw else src.with_suffix("")
    if not inside_home(dest):
        return "I only extract into your user folder."
    dest = unique_path(dest) if dest.exists() and any(dest.iterdir()) else dest
    try:
        with zipfile.ZipFile(src) as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_UNZIP_FILES or sum(i.file_size for i in infos) > _MAX_UNZIP_BYTES:
                return "That archive is unusually large (over 2 GB or 20,000 files); I won't extract it."
            dest.mkdir(parents=True, exist_ok=True)
            root = dest.resolve()
            for i in infos:
                target = (dest / i.filename).resolve()
                if root != target and root not in target.parents:
                    return f"Refused: {i.filename} would be written outside the destination folder."
            zf.extractall(dest)
    except Exception as e:
        return f"Extracting failed: {e}"
    return f"Extracted {len(infos)} item{'s' if len(infos) != 1 else ''} to {dest}."


def file_tools(parameters: dict, player=None) -> str:
    action = str(parameters.get("action", "")).strip().lower()
    table = {"search": _search, "find_text": _search, "grep": _search,
             "duplicates": _duplicates, "duplicate": _duplicates, "dupes": _duplicates,
             "recent": _recent, "changed": _recent,
             "zip": _zip, "compress": _zip, "unzip": _unzip, "extract": _unzip}
    fn = table.get(action)
    if not fn:
        return "File tool actions are: search, duplicates, recent, zip, unzip."
    return fn(parameters)


TOOL = {
    "name": "file_tools",
    "description": (
        "Finds and packages files. Use for 'which file mentions <text>' (search inside "
        "files), 'find duplicate files in Downloads', 'what files changed in the last "
        "few days', 'zip this folder', 'unzip that archive'. Never deletes or "
        "overwrites. To list, move, copy, rename or delete files use file_controller."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "search | duplicates | recent | zip | unzip"},
            "text": {"type": "STRING", "description": "For search: the text to find inside files"},
            "folder": {"type": "STRING",
                       "description": "For search/duplicates/recent: desktop | documents | downloads | a path"},
            "extension": {"type": "STRING", "description": "For search: file types, e.g. 'py,txt'"},
            "days": {"type": "STRING", "description": "For recent: how many days back, default 3"},
            "path": {"type": "STRING", "description": "For zip/unzip: the file or folder"},
            "dest": {"type": "STRING", "description": "For unzip: folder to extract into (optional)"},
        },
        "required": ["action"],
    },
    "handler": file_tools,
}
