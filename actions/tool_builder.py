# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
tool_builder — lets JARVIS grow: when you ask for something none of its tools can do, it can
write a new tool (a plugin) for it, check it, and install it.

This is the "self-improving tools" switch in Settings, and it is OFF by default.

What it can touch
    Only new files under plugins/ named ai_<something>.py. It never edits main.py, actions/,
    core/ or any existing file, and it only deletes tools it made itself.

What stands between generated code and your computer
    1. The switch must be on.
    2. The code is read as a syntax tree before it is saved: no running of strings as code, no
       deleting files, no subprocess / shell, no sockets, and only an allow-list of imports.
    3. It is loaded once in a separate throw-away Python to prove it imports and has the right
       shape.
    4. With "Ask before risky actions" on, installing it waits for your CONFIRM press, showing
       the tool's name and what it does. With that off, it installs by itself.
    These checks lower the risk; they do not make AI-written code trustworthy. A tool runs with
    your permissions, so keep the confirmation on unless you are happy with that.
"""
import ast
import json
import re
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

from core import confirm

BASE_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = BASE_DIR / "plugins"
PREFIX = "ai_"
MAX_TOOLS = 25

ALLOWED_IMPORTS = {
    "json", "re", "math", "random", "time", "datetime", "pathlib", "os", "sys", "typing", "collections", "itertools",
    "functools", "statistics", "textwrap", "string", "html", "csv", "base64", "hashlib", "uuid", "calendar", "zoneinfo",
    "platform", "tempfile", "threading", "webbrowser", "urllib", "difflib", "decimal", "fractions", "secrets", "shutil",
    "requests", "bs4", "psutil", "pyperclip", "pyautogui", "PIL", "numpy", "mss", "qrcode", "ddgs", "send2trash",
}
# Packages a new tool may use IF "Install missing Python packages" is on. An import name -> the name on PyPI. Only these,
# never an arbitrary name the model makes up (a misspelt or look-alike package is how malware gets installed), and each
# install is confirmed by the user. Add a line here to approve another package.
INSTALLABLE = {
    "pandas": "pandas", "openpyxl": "openpyxl", "matplotlib": "matplotlib", "docx": "python-docx", "pptx": "python-pptx",
    "pypdf": "pypdf", "PyPDF2": "PyPDF2", "pdfplumber": "pdfplumber", "dateutil": "python-dateutil", "pytz": "pytz",
    "lxml": "lxml", "feedparser": "feedparser", "wikipedia": "wikipedia", "yfinance": "yfinance", "pyttsx3": "pyttsx3",
    "scipy": "scipy", "sympy": "sympy", "markdown": "markdown", "yaml": "pyyaml", "tabulate": "tabulate", "emoji": "emoji",
    "isodate": "isodate", "pint": "pint", "humanize": "humanize", "babel": "babel", "xlrd": "xlrd", "xlsxwriter": "xlsxwriter",
}
BANNED_NAMES = {
    "eval", "exec", "compile", "__import__", "globals", "locals", "popen", "Popen", "run_module",
    "rmtree", "remove", "unlink", "rmdir", "removedirs", "truncate", "execv", "execl", "spawn",
    "setattr", "delattr", "input", "breakpoint", "exit", "quit", "_exit", "kill", "killpg", "chmod", "chown",
}
BANNED_ATTR_PREFIX = ("subprocess.", "socket.", "ctypes.", "importlib.", "pickle.", "marshal.", "os.system")

_RULES = """You write JARVIS plugins: one small, self-contained Python file that adds ONE tool.

The file MUST define exactly these two things:

PLUGIN = {
    "name": "snake_case_name",
    "description": "When to call this tool, with the phrases a user would say. One or two sentences.",
    "parameters": {"type": "OBJECT", "properties": {"arg": {"type": "STRING", "description": "..."}}, "required": []},
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    ...  # do the job; return ONE short sentence (or a few) to be spoken back. Never raise: catch errors and return a plain error sentence.

Hard rules:
- Output ONLY the Python source. No markdown fences, no explanation.
- Use only the standard library plus: requests, bs4, psutil, pyperclip, pyautogui, PIL, numpy, qrcode, ddgs, mss.
- FORBIDDEN (the file is rejected): eval, exec, compile, __import__, subprocess, os.system, popen, sockets, ctypes,
  importlib, pickle, deleting or removing files or folders (os.remove, unlink, rmtree, rmdir), chmod, input(), exit().
- Do not read or write anything in the JARVIS install folder (config, memory, actions, core, plugins). Files you create
  go under the user's Documents or Desktop.
- Network calls must have a timeout (<= 10 s) and go only to what the task needs.
- Keep it short and readable, with a one-line comment saying what it does. Return text a person can hear.
"""


def _snake(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s or "custom_tool")[:40]


def _strip_fences(code: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", code, flags=re.S)
    return (m.group(1) if m else code).strip() + "\n"


def check_code(code: str, allow_packages: bool = False) -> tuple[bool, str, dict]:
    """(ok, problem, info) — static review of generated source. Nothing is executed here.
    With allow_packages, the approved extra packages (INSTALLABLE) may be imported too."""
    permitted = ALLOWED_IMPORTS | (set(INSTALLABLE) if allow_packages else set())
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"it has a syntax error on line {e.lineno}: {e.msg}", {}
    plugin, has_run = None, False
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PLUGIN" for t in node.targets):
            try:
                plugin = ast.literal_eval(node.value)
            except Exception:
                return False, "PLUGIN must be a plain dictionary literal", {}
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            has_run = True
    if not isinstance(plugin, dict) or not plugin.get("name") or not plugin.get("description"):
        return False, "it does not define PLUGIN with a name and description", {}
    if not has_run:
        return False, "it does not define run(parameters, player, session_memory)", {}
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", str(plugin["name"])):
        return False, "the tool name is not a valid identifier", {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in permitted:
                    return False, _import_problem(a.name), {}
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in permitted or node.level:
                return False, _import_problem(node.module or ""), {}
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            return False, f"it uses '{node.id}', which is not allowed", {}
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_NAMES:
                return False, f"it uses '.{node.attr}', which is not allowed", {}
            dotted = ast.unparse(node) if hasattr(ast, "unparse") else ""
            if dotted.startswith(BANNED_ATTR_PREFIX):
                return False, f"it uses '{dotted.split('(')[0]}', which is not allowed", {}
    low = code.lower()
    for forbidden in ("plugins/", "plugins\\\\", "config/api_keys", "api_keys.json", "long_term.json", "vault.enc"):
        if forbidden in low:
            return False, f"it refers to '{forbidden}', which tools may not touch", {}
    return True, "", plugin


def _import_problem(module: str) -> str:
    top = (module or "").split(".")[0]
    if top in INSTALLABLE:
        return (f"it needs the package '{INSTALLABLE[top]}', which a tool may use only when “Install missing Python "
                f"packages” is switched on in Settings")
    return f"it imports '{module}', which is not allowed"


def _imported_packages(code: str) -> set[str]:
    out: set[str] = set()
    try:
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Import):
                out |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module.split(".")[0])
    except SyntaxError:
        pass
    return out


def missing_packages(code: str) -> list[str]:
    """PyPI names of approved packages the code imports that are not installed yet."""
    import importlib.util
    return sorted({INSTALLABLE[m] for m in _imported_packages(code)
                   if m in INSTALLABLE and importlib.util.find_spec(m) is None})


def pip_install(packages: list[str]) -> tuple[bool, str]:
    """pip-install approved packages into this Python. Refuses anything not on the approved list."""
    approved = set(INSTALLABLE.values())
    bad = [p for p in packages if p not in approved]
    if bad or not packages:
        return False, f"{', '.join(bad) or 'nothing'} is not on the approved list"
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *packages],
                           capture_output=True, text=True, timeout=600,
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        return False, "the install took too long"
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["pip failed"])[-1]
        return False, tail[:200]
    return True, ""


def dry_load(path: Path) -> tuple[bool, str]:
    """Import the file in a separate, isolated Python and confirm PLUGIN and run() are really there."""
    probe = textwrap.dedent("""
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("probe_plugin", sys.argv[1])
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert callable(getattr(m, "run", None)), "no run()"
        assert isinstance(getattr(m, "PLUGIN", None), dict), "no PLUGIN"
        print(json.dumps({"name": m.PLUGIN["name"]}))
    """)
    try:
        r = subprocess.run([sys.executable, "-I", "-c", probe, str(path)], capture_output=True, text=True, timeout=20,
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
    except subprocess.TimeoutExpired:
        return False, "it took too long just to load"
    if r.returncode != 0:
        return False, (r.stderr.strip().splitlines() or ["it failed to load"])[-1][:200]
    return True, ""


def _generate(request: str, existing: str = "", packages: bool = False) -> str:
    from core import gemini
    rules = _RULES + (("\nYou may ALSO import these extra packages if the task really needs them (they will be installed for "
                       "the user): " + ", ".join(sorted(INSTALLABLE)) + ".\n") if packages else "")
    if existing:
        task = (f"Update this existing plugin as requested. Keep its PLUGIN name unchanged.\n\nREQUEST: {request}\n\n"
                f"EXISTING CODE:\n{existing}")
    else:
        task = f"Write a plugin for this request:\n\n{request}"
    return gemini.text(rules + "\n\n" + task, gemini.SMART, None, 60_000)


def _installed() -> list[Path]:
    return sorted(PLUGINS_DIR.glob(f"{PREFIX}*.py"))


def _taken_names(player) -> set[str]:
    try:
        reg = getattr(player, "tool_names", None)
        return set(reg()) if callable(reg) else set()
    except Exception:
        return set()


def _reload(player) -> None:
    cb = getattr(player, "on_plugins_changed", None)
    if callable(cb):
        cb()


def _install(path: Path, code: str, player, announce: str) -> str:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(".py.bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(code, encoding="utf-8")
    try:
        if player:
            player.write_log(f"SYS: {announce} ({path.name})")
    except Exception:
        pass
    threading.Timer(1.0, _reload, args=(player,)).start()
    return f"{announce} It will be ready in a few seconds."


def tool_builder(parameters: dict = None, player=None, **_) -> str:
    from memory.config_manager import get_self_improve
    p = parameters or {}
    action = (p.get("action") or "create").lower().strip()

    if action == "list":
        files = _installed()
        if not files:
            return "I haven't built any tools of my own yet."
        out = []
        for f in files:
            ok, _msg, info = check_code(f.read_text(encoding="utf-8"), True)
            out.append(f"- {info.get('name', f.stem)}: {str(info.get('description', ''))[:110]}")
        return "Tools I built:\n" + "\n".join(out)

    if action == "remove":
        name = _snake(p.get("name") or "")
        target = next((f for f in _installed() if f.stem in (PREFIX + name, name)), None)
        if target is None:
            return f"I don't have a tool of my own called '{name}'."
        try:
            from send2trash import send2trash
            send2trash(str(target))
        except Exception:
            target.rename(target.with_suffix(".py.removed"))
        threading.Timer(0.5, _reload, args=(player,)).start()
        return f"Removed the {name} tool. It will be gone in a few seconds."

    if action == "install_package":
        from memory.config_manager import get_si
        if not get_si("packages"):
            return ("Installing packages is switched off (Settings > Self-improvement > Install missing Python packages). "
                    "Tell the user where the switch is; do not try another way.")
        want = (p.get("name") or "").strip()
        pip_name = INSTALLABLE.get(want) or (want if want in INSTALLABLE.values() else None) or INSTALLABLE.get(want.lower())
        if not pip_name:
            return (f"I can only install packages from an approved list, and '{want}' is not on it. Approved: "
                    + ", ".join(sorted(set(INSTALLABLE.values()))) + ".")
        imp = next(k for k, v in INSTALLABLE.items() if v == pip_name)
        import importlib.util
        if importlib.util.find_spec(imp) is not None:
            return f"{pip_name} is already installed."
        return confirm.request(
            key=f"pip_install:{pip_name}", title=f"Install the Python package “{pip_name}”?",
            detail="Downloads it from PyPI and installs it for JARVIS. It runs code from the internet with your permissions.",
            run=lambda: (lambda r: f"Installed {pip_name}." if r[0] else f"Could not install {pip_name}: {r[1]}")(pip_install([pip_name])),
        )

    if action not in ("create", "update"):
        return "Unknown action. Use create, update, list, remove or install_package."

    if not get_self_improve():
        return ("Self-improving tools is switched off. Tell the user they can turn on 'Self-improving tools' in "
                "Settings if they want me to build new tools for myself — and don't try to do the task another way "
                "unless they ask.")
    request = (p.get("request") or "").strip()
    if len(request) < 8:
        return "Describe what the tool should do, in a sentence or two."
    if action == "create" and len(_installed()) >= MAX_TOOLS:
        return f"I already have {MAX_TOOLS} tools of my own. Remove some first."

    existing_path, existing = None, ""
    if action == "update":
        name = _snake(p.get("name") or "")
        existing_path = next((f for f in _installed() if f.stem in (PREFIX + name, name)), None)
        if existing_path is None:
            return f"I don't have a tool of my own called '{name}' to update."
        existing = existing_path.read_text(encoding="utf-8")

    from memory.config_manager import get_si
    allow_pkgs = get_si("packages")
    code, problem = "", "nothing came back from the model"
    for attempt in range(2):                                   # one retry, telling the model what was wrong
        raw = _generate(request if attempt == 0 else f"{request}\n\nYour previous attempt was rejected because {problem}. Fix that.",
                        existing, allow_pkgs)
        if not raw:
            continue
        code = _strip_fences(raw)
        ok, problem, info = check_code(code, allow_pkgs)
        if ok:
            break
    else:
        return f"I couldn't build a safe, working tool for that — {problem}."

    taken = _taken_names(player)
    name = str(info["name"])
    if existing_path is None:
        base, n = name, 2
        while name in taken or (PLUGINS_DIR / f"{PREFIX}{name}.py").exists():
            name, n = f"{base}_{n}", n + 1
        if name != info["name"]:
            code = re.sub(r"(['\"]name['\"]\s*:\s*['\"])" + re.escape(info["name"]) + r"(['\"])", rf"\g<1>{name}\g<2>", code, count=1)
            ok, problem, info = check_code(code, allow_pkgs)
            if not ok:
                return f"I couldn't build a safe, working tool for that — {problem}."
    path = existing_path or (PLUGINS_DIR / f"{PREFIX}{name}.py")
    needs = missing_packages(code) if allow_pkgs else []

    def load_check() -> tuple[bool, str]:
        tmp = PLUGINS_DIR / f"_candidate_{name}.py"            # leading underscore: never discovered as a plugin
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(code, encoding="utf-8")
        try:
            return dry_load(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    if not needs:
        ok, why = load_check()
        if not ok:
            return f"I wrote a tool for that but it doesn't load: {why}. Nothing was installed."

    header = f'"""AI-generated by JARVIS tool_builder — request: {request[:160].replace(chr(34), chr(39))}"""\n'
    final = header + code
    verb = "Updated" if existing_path else "Built"
    summary = f"{verb} a new tool '{name}': {str(info['description'])[:140]}"
    if needs:                                                  # packages first — always asked, even with confirmations off
        def install_all():
            ok_pip, why_pip = pip_install(needs)
            if not ok_pip:
                return f"Could not install {', '.join(needs)} ({why_pip}); the tool '{name}' was not added."
            ok_load, why_load = load_check()
            if not ok_load:
                return f"Installed {', '.join(needs)}, but the new tool does not load ({why_load}); it was not added."
            return _install(path, final, player, summary)
        return confirm.request(
            key=f"pip_install:{name}",
            title=f"Install {', '.join(needs)} and the AI-written tool “{name}”?",
            detail=f"The new tool needs the Python package{'s' if len(needs) > 1 else ''} {', '.join(needs)} (from PyPI, on the approved list). "
                   f"{str(info['description'])[:140]}",
            run=install_all,
        )
    return confirm.request(
        key=f"tool_builder:{name}",
        title=f"{'Update' if existing_path else 'Install'} the AI-written tool “{name}”?",
        detail=f"{str(info['description'])[:200]}  — Python written by the assistant, saved as plugins/{path.name}, "
               f"runs with your permissions.",
        run=lambda: _install(path, final, player, summary),
    )


TOOL = {
    "name": "tool_builder",
    "description": (
        "Builds new tools for the assistant itself. When 'Self-improving tools' is on and the user asks for something "
        "NO existing tool can do (and a small, safe Python tool could), call this with action=create and a clear "
        "`request` describing what the tool should do — instead of saying you can't. Also: update a tool you built "
        "(action=update, name, request), list them (action=list), remove one (action=remove, name). Never use it "
        "for things an existing tool already does. If it replies that the feature is switched off, tell the user "
        "where the switch is."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create (default) | update | list | remove | install_package (name = an approved package, when the user asks for one)"},
            "request": {"type": "STRING", "description": "What the new or updated tool should do, in plain words, with inputs and what it returns"},
            "name": {"type": "STRING", "description": "update / remove: the tool's name. install_package: the package, e.g. pandas"},
        },
        "required": [],
    },
    "handler": tool_builder,
}
