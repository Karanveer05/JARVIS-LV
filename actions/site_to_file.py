"""
site_to_file — reach a website, pull out what it says, and save it as a text file.

"Fetch the details of stonicai.com and put them in a file on the Desktop."

The page's own words go into the file. What the tool RETURNS to the model is only
a short summary and the saved path: a web page is untrusted data and should not
be handed back to the model as if it were instructions.
"""
import ipaddress
import re
import socket
from datetime import datetime
from urllib.parse import urljoin, urlparse

from actions._paths import inside_home, resolve_folder, safe_filename, unique_path

_TIMEOUT = 20
_MAX_BYTES = 3_000_000
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def _normalize(url: str) -> str:
    url = (url or "").strip().strip('"')
    if not url:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def _blocked_reason(url: str) -> str:
    """Refuse non-web schemes and addresses on this computer or its local
    network, so a prompt-injected page cannot aim the fetcher inward."""
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return "only http and https addresses are allowed"
    host = p.hostname
    if not host:
        return "the address has no host name"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"could not find '{host}'"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return "that address points to a private or local network"
    return ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _fetch(url: str):
    import requests
    r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "en"},
                     timeout=_TIMEOUT, stream=True, allow_redirects=True)
    # A redirect can land somewhere private; check where we actually ended up.
    final_block = _blocked_reason(r.url)
    if final_block:
        r.close()
        raise RuntimeError(f"redirected somewhere not allowed: {final_block}")
    body = b""
    for chunk in r.iter_content(65536):
        body += chunk
        if len(body) > _MAX_BYTES:
            break
    r.encoding = r.encoding or "utf-8"
    return r, body.decode(r.encoding if r.encoding else "utf-8", errors="replace")


def _extract(html: str, base: str) -> dict:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
            if t and t.get("content"):
                return _clean(t["content"])
        return ""

    title = _clean(soup.title.get_text()) if soup.title else meta("og:title")
    desc = meta("description", "og:description")

    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full = urljoin(base, href)
        if full not in seen:
            seen.add(full)
            links.append((_clean(a.get_text())[:60], full))

    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", html)))
    emails = [e for e in emails if not e.lower().endswith((".png", ".jpg", ".svg", ".webp", ".gif", ".css", ".js"))]

    headings = [(h.name.upper(), _clean(h.get_text())) for h in soup.find_all(["h1", "h2", "h3"])
                if _clean(h.get_text())]

    for t in soup(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    block = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "button"]
    lines, seen_l = [], set()
    for t in soup.find_all(block):
        if t.find(block):          # only leaf blocks, so nothing is repeated
            continue
        line = _clean(t.get_text(" "))
        if line and line not in seen_l:
            seen_l.add(line)
            lines.append(line)
    return {"title": title, "description": desc, "headings": headings,
            "text": lines, "links": links, "emails": emails}


def site_to_file(parameters: dict, player=None) -> str:
    url = _normalize(parameters.get("url", ""))
    if not url:
        return "Tell me which website to fetch."
    bad = _blocked_reason(url)
    if bad:
        return f"I won't fetch that: {bad}."

    try:
        resp, html = _fetch(url)
    except Exception as e:
        return f"I could not reach {url}: {e}"
    if resp.status_code >= 400:
        return f"{urlparse(url).netloc} answered with an error ({resp.status_code}); nothing saved."
    if "html" not in resp.headers.get("Content-Type", "html").lower():
        return f"That address is not a web page ({resp.headers.get('Content-Type')}); nothing saved."

    info = _extract(html, resp.url)
    host = urlparse(resp.url).netloc
    folder = resolve_folder(parameters.get("folder", ""), "desktop")
    if not inside_home(folder):
        return "I only save files inside your user folder."
    name = safe_filename(parameters.get("filename", "") or f"{host}-details")
    if not name.lower().endswith((".txt", ".md")):
        name += ".txt"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = unique_path(folder / name)
    except Exception as e:
        return f"I could not prepare the folder {folder}: {e}"

    out = [
        f"{info['title'] or host}", f"Source: {resp.url}",
        f"Fetched: {datetime.now():%Y-%m-%d %H:%M}   HTTP {resp.status_code}",
        "=" * 70, "",
    ]
    if info["description"]:
        out += ["DESCRIPTION", f"  {info['description']}", ""]
    if info["headings"]:
        out += ["HEADINGS"] + [f"  {t}: {h}" for t, h in info["headings"][:60]] + [""]
    out += ["PAGE TEXT"] + [f"  {l}" for l in info["text"][:400]] + [""]
    if info["emails"]:
        out += ["EMAILS"] + [f"  {e}" for e in info["emails"][:20]] + [""]
    if info["links"]:
        out += ["LINKS"] + [f"  {t or '(no text)'} -> {u}" for t, u in info["links"][:80]] + [""]
    if len(" ".join(info["text"])) < 200:
        out += ["NOTE: very little text was found. This site probably builds its "
                "content with JavaScript; use the browser tool to read it fully.", ""]

    try:
        path.write_text("\n".join(out), encoding="utf-8")
    except Exception as e:
        return f"I fetched the page but could not save the file: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: saved {host} details to {path}")
        except Exception:
            pass
    return (f"Saved the details of {host} to {path}. Title: {info['title'] or 'none'}. "
            f"{len(info['text'])} text lines, {len(info['links'])} links"
            f"{', ' + str(len(info['emails'])) + ' email(s)' if info['emails'] else ''} captured."
            + (" The page has very little text, so it may need the browser to read fully."
               if len(" ".join(info["text"])) < 200 else ""))


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "site_to_file",
    "description": (
        "Reaches a website, extracts its details (title, description, headings, "
        "page text, links, emails) and SAVES them to a text file — by default on "
        "the Desktop. Use when the user says 'fetch the details of <site> and put "
        "them in a file', 'save this website's info', 'make a file about <site>'. "
        "Returns where the file was saved; tell the user when it is done. For just "
        "answering a question about the web, use web_search instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "url": {"type": "STRING", "description": "Website address, e.g. stonicai.com"},
            "filename": {"type": "STRING",
                         "description": "Optional file name; default is '<site>-details.txt'"},
            "folder": {"type": "STRING",
                       "description": "desktop (default) | documents | downloads | a folder path"},
        },
        "required": ["url"],
    },
    "handler": site_to_file,
}
