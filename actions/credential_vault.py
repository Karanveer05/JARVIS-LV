# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
logins — an encrypted vault of site logins that JARVIS can type into login pages.

The vault is a Fernet-encrypted file (config/vault.enc). The key never sits next
to it in the clear: on Windows it is wrapped with DPAPI, so only this Windows
user on this PC can unwrap it; elsewhere it is a key file readable by the owner
only. Copying vault.enc to another machine therefore gets an attacker nothing.

Passwords never come back out of this tool. `list` shows sites and usernames,
`fill` types the password straight into the page through the browser driver, and
there is deliberately no "reveal" action — whatever the model can read ends up in
the conversation transcript.

`fill` only types into a page whose address belongs to the saved site, so a
redirect to a look-alike domain gets nothing.
"""
import asyncio
import json
import os
import platform
import time
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
VAULT_FILE = BASE_DIR / "config" / "vault.enc"
KEY_FILE = BASE_DIR / "config" / "vault.key"


# ── Key handling ──────────────────────────────────────────────────────────────

def _wrap(raw: bytes) -> bytes:
    if platform.system() == "Windows":
        import win32crypt
        return win32crypt.CryptProtectData(raw, "JARVIS vault", None, None, None, 0)
    return raw


def _unwrap(blob: bytes) -> bytes:
    if platform.system() == "Windows":
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    return blob


def _fernet():
    from cryptography.fernet import Fernet
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        key = _unwrap(KEY_FILE.read_bytes())
    else:
        key = Fernet.generate_key()
        KEY_FILE.write_bytes(_wrap(key))
        if platform.system() != "Windows":
            os.chmod(KEY_FILE, 0o600)
    return Fernet(key)


def _load() -> dict:
    if not VAULT_FILE.exists():
        return {}
    try:
        return json.loads(_fernet().decrypt(VAULT_FILE.read_bytes()).decode("utf-8"))
    except Exception as e:
        raise RuntimeError(
            "The login vault could not be decrypted (wrong Windows user, or the key "
            f"file changed): {type(e).__name__}"
        ) from e


def _save(data: dict) -> None:
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VAULT_FILE.with_suffix(".tmp")
    tmp.write_bytes(_fernet().encrypt(json.dumps(data).encode("utf-8")))
    tmp.replace(VAULT_FILE)
    if platform.system() != "Windows":
        os.chmod(VAULT_FILE, 0o600)


# ── Site matching ─────────────────────────────────────────────────────────────

def _host(text: str) -> str:
    t = (text or "").strip().lower()
    if "://" not in t:
        t = "https://" + t
    h = (urlparse(t).hostname or "").removeprefix("www.")
    return h


def _site_key(site: str) -> str:
    """'GitHub', 'github.com', 'https://github.com/login' -> a stable key."""
    h = _host(site)
    return h if "." in h else (site or "").strip().lower()


def _find(data: dict, site: str, username: str = "") -> tuple[str, dict] | None:
    key = _site_key(site)
    hits = [(k, v) for k, v in data.items()
            if k == key or k.split(".")[0] == key or key in k]
    if username:
        hits = [h for h in hits if h[1].get("username", "").lower() == username.lower()]
    return hits[0] if hits else None


def _same_site(page_url: str, saved_host: str) -> bool:
    h = _host(page_url)
    return h == saved_host or h.endswith("." + saved_host)


# ── Browser fill ──────────────────────────────────────────────────────────────

_USER_SEL = (
    'input[autocomplete="username"], input[type="email"], input[name*="user" i], '
    'input[name*="email" i], input[name*="login" i], input[id*="user" i], '
    'input[id*="email" i], input[id*="login" i], input[type="text"]'
)
_NEXT_SEL = 'button:has-text("Next"), button:has-text("Continue"), input[type="submit"]'
_SUBMIT_SEL = (
    'button[type="submit"], input[type="submit"], button:has-text("Sign in"), '
    'button:has-text("Log in"), button:has-text("Login")'
)


async def _first_visible(page, selector: str):
    loc = page.locator(selector)
    for i in range(min(await loc.count(), 8)):
        el = loc.nth(i)
        if await el.is_visible():
            return el
    return None


async def _fill_login(sess, host: str, entry: dict, submit: bool) -> str:
    page = await sess._get_page()
    if not _same_site(page.url, host):
        await sess.go_to(entry.get("url") or f"https://{host}")
        page = await sess._get_page()
        await asyncio.sleep(1.0)
    if not _same_site(page.url, host):
        return (f"Stopped: the browser is on {_host(page.url) or 'another page'}, not {host}, "
                "so nothing was typed.")

    pw = await _first_visible(page, 'input[type="password"]')
    user = await _first_visible(page, _USER_SEL)
    if user is not None:
        await user.fill(entry["username"])
    if pw is None and user is not None:
        # Two-step sign-in (Google, Microsoft, ...): username, Next, then password.
        nxt = await _first_visible(page, _NEXT_SEL)
        await (nxt.click() if nxt else user.press("Enter"))
        try:
            await page.wait_for_selector('input[type="password"]', state="visible", timeout=8000)
        except Exception:
            return "Typed the username, but no password box appeared (maybe a captcha or code step)."
        pw = await _first_visible(page, 'input[type="password"]')
    if pw is None:
        return "Couldn't find a login form on this page."
    if not _same_site(page.url, host):
        return f"Stopped: the page moved to {_host(page.url)}, so the password was not typed."

    await pw.fill(entry["password"])
    if not submit:
        return f"Filled the login for {entry['username']}; press Enter or click sign in when ready."
    btn = await _first_visible(page, _SUBMIT_SEL)
    await (btn.click() if btn else pw.press("Enter"))
    return f"Filled and submitted the login for {entry['username']} on {host}."


def _fill(site: str, username: str, submit: bool, browser: str | None) -> str:
    data = _load()
    found = _find(data, site, username)
    if not found:
        return f"No saved login for {site}."
    host, entry = found
    from actions import browser_control as bc
    sess = bc._registry.get(browser or None)
    result = sess.run(_fill_login(sess, host, entry, submit), timeout=90)
    entry["last_used"] = time.strftime("%Y-%m-%d %H:%M")
    data[host] = entry
    _save(data)
    return result


# ── Tool entry point ──────────────────────────────────────────────────────────

def logins(parameters: dict = None, player=None, **_) -> str:
    p = parameters or {}
    action = (p.get("action") or "list").lower().strip()
    site = (p.get("site") or "").strip()
    username = (p.get("username") or "").strip()
    try:
        if action == "save":
            password = p.get("password") or ""
            if not (site and username and password):
                return "To save a login I need the site, the username and the password."
            key = _site_key(site)
            if "." not in key:
                return "Give me the site's address (like github.com) so I can match the page."
            data = _load()
            data[key] = {
                "username": username,
                "password": password,
                "url": p.get("login_url") or (site if "://" in site else ""),
                "saved": time.strftime("%Y-%m-%d %H:%M"),
            }
            _save(data)
            return f"Saved the login for {username} on {key}. The password is encrypted and I won't read it back."

        if action == "list":
            data = _load()
            if not data:
                return "No logins saved yet."
            return "Saved logins:\n" + "\n".join(
                f"- {k}: {v['username']}" + (f" (last used {v['last_used']})" if v.get("last_used") else "")
                for k, v in sorted(data.items())
            )

        if action in ("forget", "delete", "remove"):
            data = _load()
            found = _find(data, site)
            if not found:
                return f"No saved login for {site}."
            del data[found[0]]
            _save(data)
            return f"Deleted the saved login for {found[0]}."

        if action in ("fill", "login", "sign_in"):
            if not site:
                return "Which site should I log in to?"
            submit = str(p.get("submit", "true")).lower() not in ("false", "no", "0")
            return _fill(site, username, submit, (p.get("browser") or "").lower() or None)

        return "Unknown action. Use save, list, fill or forget."
    except Exception as e:
        return f"Logins error: {e}"


TOOL = {
    "name": "logins",
    "description": (
        "Encrypted vault of website logins. Use for 'save my login for github', 'log me in to "
        "netflix', 'what sites do I have saved', 'forget my reddit login'. `fill` opens the site's "
        "login page and types the saved username and password straight into it (the password is "
        "never shown or read aloud). Only call save when the user has told you the password."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "save | list | fill | forget (default list)"},
            "site": {"type": "STRING", "description": "Site name or address, e.g. 'github.com'"},
            "username": {"type": "STRING", "description": "Account name or email (save; or to pick between accounts on fill)"},
            "password": {"type": "STRING", "description": "For save only"},
            "login_url": {"type": "STRING", "description": "For save: the exact sign-in page, if not the site's home"},
            "submit": {"type": "BOOLEAN", "description": "For fill: press sign in afterwards (default true)"},
            "browser": {"type": "STRING", "description": "Optional browser name for fill"},
        },
        "required": ["action"],
    },
    "handler": logins,
}
