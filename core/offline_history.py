"""
core/offline_history.py — the offline assistant's own copy of everything JARVIS knows, and the
search that answers questions from it.

WHY A SEPARATE COPY
    The offline assistant must work with no internet and no API, and it must be the ONLY thing that
    reads this data. So it does not read chats.json / long_term.json / notes.json directly: it reads
    memory/offline_history.json, a block of its own that the online assistant never touches and never
    sends anywhere.

WHAT IS IN IT (and how each part follows the originals)
    chats      every normal conversation (including ones held in offline mode). Added to as new chats appear; a chat the user deletes is
               removed on the next sync; chats that merely fell off the 60-chat limit are KEPT, so
               nothing is forgotten just because the live history is capped.
    summaries  the one-line summary written after each conversation. The live file keeps only the last
               three, so these accumulate here.
    facts      what is remembered about the user (mirrors the live memory exactly — "forget" propagates).
    notes      the notes and to-do list (mirrors exactly).
    Temporary chats are never saved anywhere, so they can never get in here.

HOW A QUESTION IS ANSWERED
    No AI. The question is cut into words and matched against every saved item with BM25 (the ranking
    search engines use), with a few special cases first: to-do/notes questions, "what did we talk about
    yesterday", and "what is my name / where do I live" style facts.
"""
from __future__ import annotations

import json
import math
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


MEMORY = _base() / "memory"
STORE_FILE = MEMORY / "offline_history.json"
_SRC_CHATS = MEMORY / "chats.json"
_SRC_MEMORY = MEMORY / "long_term.json"
_SRC_NOTES = MEMORY / "notes.json"
MAX_CHATS_LIVE = 60                       # ChatStore.MAX_CHATS — used to tell "deleted" from "aged out"
MAX_SUMMARIES = 500
_lock = threading.RLock()


# ── files ─────────────────────────────────────────────────────────────────────

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load() -> dict:
    d = _read(STORE_FILE, {})
    d = d if isinstance(d, dict) else {}
    d.setdefault("chats", [])
    d.setdefault("summaries", [])
    d.setdefault("facts", [])
    d.setdefault("notes", [])
    return d


# ── keeping the copy up to date ─────────────────────────────────────────────────

def sync() -> dict:
    """Bring the offline copy up to date from the live files. Cheap; safe to call often.
    Returns counts, for logging."""
    with _lock:
        store = load()

        # chats: add new, refresh known, drop user-deleted, keep aged-out
        live = _read(_SRC_CHATS, [])
        live = [c for c in live if isinstance(c, dict) and "id" in c] if isinstance(live, list) else []
        live_ids = {c["id"] for c in live}
        mine = {c["id"]: c for c in store["chats"] if isinstance(c, dict) and "id" in c}
        oldest_live = min(live_ids) if live_ids else None
        at_cap = len(live) >= MAX_CHATS_LIVE
        for cid in list(mine):
            if cid in live_ids:
                continue
            if oldest_live is None or not at_cap or cid >= oldest_live:
                del mine[cid]                                   # it was deleted, not aged out
        for c in live:
            mine[c["id"]] = {"id": c["id"], "title": c.get("title", ""), "created": c.get("created", ""),
                             "messages": [{"role": m.get("role", ""), "text": m.get("text", ""), "ts": m.get("ts", "")}
                                          for m in c.get("messages", []) if isinstance(m, dict)]}
        store["chats"] = sorted(mine.values(), key=lambda c: c["id"])

        # summaries and facts live in long_term.json
        mem = _read(_SRC_MEMORY, {})
        mem = mem if isinstance(mem, dict) else {}
        seen = {(s.get("date"), s.get("summary")) for s in store["summaries"]}
        for s in mem.get("sessions", []) if isinstance(mem.get("sessions"), list) else []:
            if isinstance(s, dict) and s.get("summary") and (s.get("date"), s["summary"]) not in seen:
                store["summaries"].append({"date": s.get("date", ""), "summary": s["summary"]})
        store["summaries"] = store["summaries"][-MAX_SUMMARIES:]
        facts = []
        for cat, items in mem.items():
            if cat == "sessions" or not isinstance(items, dict):
                continue
            for key, entry in items.items():
                val = entry.get("value") if isinstance(entry, dict) else entry
                if val:
                    facts.append({"category": cat, "key": key, "value": str(val),
                                  "updated": entry.get("updated", "") if isinstance(entry, dict) else ""})
        store["facts"] = facts

        notes = _read(_SRC_NOTES, [])
        store["notes"] = [n for n in notes if isinstance(n, dict) and n.get("text")] if isinstance(notes, list) else []
        store["synced"] = datetime.now().isoformat(timespec="seconds")
        _write(STORE_FILE, store)
        return {"chats": len(store["chats"]), "summaries": len(store["summaries"]),
                "facts": len(store["facts"]), "notes": len(store["notes"])}


# ── search ──────────────────────────────────────────────────────────────────────

_STOP = set("""a about above after again all also am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how i if in into
is it its just me more most my no nor not now of off on once only or other our out over own same she should so some such than that
the their them then there these they this those through to too under until up very was we were what when where which while who whom
why will with would you your yours tell please jarvis hey hi hello remember know told say said talk talked ask asked about""".split())
_SYN = {"live": ["city", "location", "home", "address", "hometown", "residence"], "lives": ["city", "location", "home"],
        "from": ["city", "hometown", "country"], "work": ["job", "company", "occupation", "profession"],
        "works": ["job", "company", "occupation"], "old": ["age", "birthday", "born"], "called": ["name"],
        "favorite": ["favourite"], "favourite": ["favorite"], "birthday": ["born", "age"], "job": ["work", "occupation"],
        "eat": ["food", "diet"], "music": ["song", "artist", "spotify"], "song": ["music", "spotify", "artist"]}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str, expand: bool = False) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in _STOP or len(w) < 2:
            continue
        out.append(_stem(w))
        if expand:
            out += [_stem(x) for x in _SYN.get(w, [])]
    return out


def _docs(store: dict) -> list[dict]:
    docs = []
    for c in store["chats"]:
        date = (c.get("created") or "")[:10]
        msgs = c.get("messages", [])
        i = 0
        while i < len(msgs):
            if msgs[i].get("role") == "user":
                q, a, j = msgs[i].get("text", ""), [], i + 1
                while j < len(msgs) and msgs[j].get("role") != "user":
                    a.append(msgs[j].get("text", ""))
                    j += 1
                docs.append({"kind": "chat", "date": date, "title": c.get("title", ""), "q": q, "a": " ".join(a),
                             "text": f"{q} {' '.join(a)}"})
                i = j
            else:
                docs.append({"kind": "chat", "date": date, "title": c.get("title", ""), "q": "", "a": msgs[i].get("text", ""),
                             "text": msgs[i].get("text", "")})
                i += 1
    for s in store["summaries"]:
        docs.append({"kind": "summary", "date": s.get("date", ""), "text": s["summary"], "summary": s["summary"]})
    for f in store["facts"]:
        words = f["key"].replace("_", " ")
        docs.append({"kind": "fact", "date": (f.get("updated") or "")[:10], "key": words, "value": f["value"],
                     "category": f.get("category", ""), "text": f"{words} {f['value']}"})
    for n in store["notes"]:
        docs.append({"kind": "note", "date": (n.get("created") or "")[:10], "done": bool(n.get("done")),
                     "note_kind": n.get("kind", "note"), "text": n["text"]})
    return docs


def _bm25(docs: list[dict], query_tokens: list[str]) -> list[tuple[float, dict]]:
    if not docs or not query_tokens:
        return []
    toks = [tokens(d["text"]) for d in docs]
    n = len(docs)
    avg = sum(len(t) for t in toks) / n or 1.0
    df: dict[str, int] = {}
    for t in toks:
        for w in set(t):
            df[w] = df.get(w, 0) + 1
    k1, b = 1.5, 0.75
    scored = []
    for d, t in zip(docs, toks):
        if not t:
            continue
        tf: dict[str, int] = {}
        for w in t:
            tf[w] = tf.get(w, 0) + 1
        score = 0.0
        for w in set(query_tokens):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ── answering ───────────────────────────────────────────────────────────────────

def _nice_date(iso: str, now: datetime) -> str:
    try:
        d = datetime.fromisoformat(iso[:10]).date()
    except Exception:
        return "earlier"
    delta = (now.date() - d).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if delta < 7:
        return d.strftime("on %A")
    return d.strftime("on %d %B")


def _clip(text: str, n: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _range(low: str, now: datetime):
    today = now.date()
    if re.search(r"\btoday\b|\bearlier today\b|\bthis morning\b", low):
        return today, today
    if "yesterday" in low:
        y = today - timedelta(days=1)
        return y, y
    if re.search(r"last week|past week|this week|few days|recent", low):
        return today - timedelta(days=7), today
    if re.search(r"last month|past month", low):
        return today - timedelta(days=31), today
    return None


def _in_range(date: str, rng) -> bool:
    try:
        return rng[0] <= datetime.fromisoformat(date[:10]).date() <= rng[1]
    except Exception:
        return False


def _fact_sentence(d: dict) -> str:
    return f"Your {d['key']} is {d['value']}."


def answer(question: str, now: datetime | None = None) -> str:
    """Answer from the saved history alone. Always returns a sentence the user can read."""
    now = now or datetime.now()
    q = " ".join((question or "").split())
    low = q.lower()
    with _lock:
        store = load()
    docs = _docs(store)
    if not docs:
        return ("I don't have any saved history to look through yet. Once we have talked while online, "
                "I'll be able to answer from it.")

    # 1. the to-do list and notes
    if re.search(r"\bto-?do\b|\btodo\b|\btask list\b|\bmy notes?\b|\bnotes? (?:did|have) i\b|\bwhat(?:'s| is) on my list\b", low):
        notes = [d for d in docs if d["kind"] == "note"]
        if not notes:
            return "You have no saved notes or to-dos."
        open_ = [n for n in notes if not n["done"]]
        lines = [f"- {_clip(n['text'], 120)}" for n in (open_ or notes)[:6]]
        head = "Your open to-dos and notes:" if open_ else "All your notes are done:"
        return head + "\n" + "\n".join(lines)

    # 1b. "what was my last question"
    if re.search(r"\b(?:last|previous|latest|most recent)\b.*\b(?:question|thing i (?:asked|said)|message|request)\b"
                 r"|\bwhat did i (?:just )?(?:ask|say)(?: you)?(?: last| just now| before)?$", low):
        asked = [d for d in docs if d["kind"] == "chat" and d["q"]]
        if asked:
            d = asked[-1]
            return f"Your last question was “{_clip(d['q'], 140)}” ({_nice_date(d.get('date', ''), now)})."
        return "I don't have a record of an earlier question."

    # 1c. "when did I ask about X" / "how many times did I mention X"
    m = re.search(r"\bwhen did (?:i|we) (?:ask|say|talk|speak|mention|discuss|chat|bring up)(?: to you)?(?: about| of)?\s+(.+)$", low)
    if m:
        qt = tokens(m.group(1), expand=True)
        hits = [(sc, d) for sc, d in _bm25(docs, qt) if d["kind"] == "chat"]      # rank among everything, then keep chats
        if hits and hits[0][0] >= 0.6:
            d = hits[0][1]
            return f"You raised that {_nice_date(d.get('date', ''), now)}: “{_clip(d['q'] or d['a'], 120)}”."
        return "I can't find when that came up in our saved history."
    m = re.search(r"\bhow many times (?:did|have) (?:i|we) (?:ask|say|mention|talk|discuss|bring up)(?: about| of)?\s+(.+)$", low)
    if m:
        qt = set(tokens(m.group(1)))
        hits = [d for d in docs if d["kind"] == "chat" and qt and len(qt & set(tokens(d["text"]))) >= max(1, len(qt) * 0.6)]
        n = len(hits)
        return (f"You've brought up “{m.group(1).strip()}” {n} time{'s' if n != 1 else ''} in our saved chats."
                if n else f"I can't find “{m.group(1).strip()}” anywhere in our saved history.")

    # 2. "what did we talk about yesterday"
    rng = _range(low, now)
    if rng and re.search(r"\b(talk|talked|discuss|discussed|chat|chatted|convers\w*|said|ask|asked|do|did|history|recap|summar\w*|about)\b", low):
        sums = [d for d in docs if d["kind"] == "summary" and _in_range(d["date"], rng)]
        chats = [d for d in docs if d["kind"] == "chat" and d["q"] and _in_range(d["date"], rng)]
        when = ("today" if "today" in low or "this morning" in low else "yesterday" if "yesterday" in low
                else "in the last month" if "month" in low else "in the last week")
        if sums or chats:
            lines = [f"- {_clip(s['summary'], 160)}" for s in sums[-3:]]
            lines += [f"- You asked: {_clip(c['q'], 100)}" for c in chats[-5:]]
            return f"Here is what we covered {when}:\n" + "\n".join(lines)
        return "I have nothing saved from that time."

    # 3. everything else: rank every saved item, and prefer facts for "my …" questions
    qt = tokens(q, expand=True)
    if not qt:
        return "What would you like to know? I can look through our saved history."
    ranked = _bm25(docs, qt)
    personal = bool(re.search(r"\b(my|i|me|am)\b", low))
    if personal:
        ranked = [(s * (1.6 if d["kind"] == "fact" else 1.0), d) for s, d in ranked]
        ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked or ranked[0][0] < 0.9:
        return ("I looked through our saved history and found nothing about that. While I'm offline I can only "
                "open or close apps and answer from what we have talked about before.")
    top_score, top = ranked[0]
    parts = [_render(top, now, qt)]
    for s, d in ranked[1:4]:
        if s >= top_score * 0.6 and d["kind"] != "fact" and _render(d, now, qt) not in parts and len(parts) < 2:
            parts.append(_render(d, now, qt))
    return "\n".join(parts)


def _best_sentence(text: str, qt: list[str], limit: int = 230) -> str:
    """The sentence of a long answer that matches the question best, instead of just its first characters."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    qs = set(qt)
    best = max(sents, key=lambda s: (len(qs & set(tokens(s))), -len(s))) if sents else text
    return _clip(best, limit)


def _render(d: dict, now: datetime, qt: list[str] | None = None) -> str:
    when = _nice_date(d.get("date", ""), now)
    qt = qt or []
    if d["kind"] == "fact":
        return _fact_sentence(d)
    if d["kind"] == "summary":
        return f"From {when}: {_clip(d['summary'])}"
    if d["kind"] == "note":
        return f"From your notes ({when}): {_clip(d['text'])}"
    if d["q"] and d["a"]:
        return f"{when[:1].upper()}{when[1:]} you asked “{_clip(d['q'], 90)}” and I answered: {_best_sentence(d['a'], qt)}"
    return f"From {when}: {_best_sentence(d['a'] or d['q'], qt)}"
