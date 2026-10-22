# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
powerpoint — drive the real PowerPoint app: open it, open or create a presentation, move
between slides, edit them, run the slide show, save and export.

It talks to PowerPoint itself (COM automation), so everything happens in the window the user is
looking at — "go to slide 5" moves the real deck, "start the presentation" really starts it.
New decks are saved straight away (Documents\\Presentations) so nothing is lost and no save
dialog ever blocks the assistant. Closing or quitting saves first.

Windows with desktop PowerPoint only. On other systems the tool says so rather than guessing.
"""
import os
import platform
import re
import time
from pathlib import Path

from actions._paths import known_folder, safe_filename, unique_path

_OK = platform.system() == "Windows"
_EXTS = (".pptx", ".ppt", ".pptm", ".ppsx", ".potx")
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "AppData", "$RECYCLE.BIN", "venv", ".venv"}

# PowerPoint constants (so nothing needs the generated type library)
_PP_SHOW_ALL, _PP_SHOW_RANGE = 1, 2
_PP_SAVE_AS_PDF = 32
_PH_TITLE, _PH_CENTER_TITLE = 1, 3


class PptError(Exception):
    """A problem to tell the user about in plain words."""


# ── Connecting to PowerPoint ──────────────────────────────────────────────────

def _app():
    import win32com.client
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
    except Exception as e:
        raise PptError("I couldn't start PowerPoint. Is it installed on this PC?") from e
    app.Visible = True
    try:
        if app.WindowState == 2:                            # minimised: bring it back
            app.WindowState = 1
    except Exception:
        pass
    return app


def _pres(app):
    """The presentation being worked on, or a clear error."""
    try:
        if app.Presentations.Count == 0:
            raise PptError("No presentation is open. Say 'open <name>' or 'create a presentation about …'.")
        return app.ActivePresentation
    except PptError:
        raise
    except Exception as e:
        raise PptError("No presentation is open in PowerPoint.") from e


def _in_show(app) -> bool:
    try:
        return app.SlideShowWindows.Count > 0
    except Exception:
        return False


def _current_index(app, pres) -> int:
    try:
        if _in_show(app):
            return app.SlideShowWindows(1).View.CurrentShowPosition
        return app.ActiveWindow.View.Slide.SlideIndex
    except Exception:
        return 1 if pres.Slides.Count else 0


def _front(app) -> None:
    try:
        app.Activate()
    except Exception:
        pass


# ── Finding files ─────────────────────────────────────────────────────────────

def _search_roots() -> list[Path]:
    home = Path.home()
    roots = [known_folder(n) for n in ("desktop", "documents", "downloads")]
    for extra in (home / "OneDrive", home / "OneDrive" / "Documents", home / "OneDrive" / "Desktop"):
        if extra.exists():
            roots.append(extra)
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve()).lower()
        if key not in seen and r.exists():
            seen.add(key)
            out.append(r)
    return out


def find_decks(name: str = "", limit: int = 8) -> list[Path]:
    """Presentations whose file name contains every word of `name` (newest first).
    With no name, the most recently changed ones."""
    words = [w for w in re.split(r"[\s_\-.]+", name.lower()) if w and w not in ("presentation", "ppt", "pptx", "powerpoint", "deck", "slides", "file")]
    hits: dict[str, Path] = {}
    for root in _search_roots():
        base_depth = len(root.parts)
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            if len(Path(dirpath).parts) - base_depth >= 4:
                dirs[:] = []
            for f in files:
                if f.lower().endswith(_EXTS) and not f.startswith("~$"):
                    stem = Path(f).stem.lower()
                    if all(w in stem for w in words):
                        p = Path(dirpath) / f
                        hits[str(p).lower()] = p
    ranked = sorted(hits.values(), key=lambda p: p.stat().st_mtime, reverse=True)
    return ranked[:limit]


# ── Reading and building slides ───────────────────────────────────────────────

def _title_shape(slide):
    try:
        return slide.Shapes.Title
    except Exception:
        return None


def _body_shapes(slide) -> list:
    out = []
    for shp in slide.Shapes:
        try:
            if shp.Type == 14 and shp.HasTextFrame and shp.PlaceholderFormat.Type not in (_PH_TITLE, _PH_CENTER_TITLE):
                out.append(shp)
        except Exception:
            continue
    return out


def _slide_text(slide) -> tuple[str, list[str]]:
    title = ""
    t = _title_shape(slide)
    if t is not None:
        try:
            title = t.TextFrame.TextRange.Text.strip()
        except Exception:
            pass
    lines = []
    for shp in slide.Shapes:
        try:
            if shp.HasTextFrame and shp.TextFrame.HasText and (t is None or shp.Name != t.Name):
                lines += [ln.strip() for ln in shp.TextFrame.TextRange.Text.replace("\x0b", "\n").split("\r") if ln.strip()]
        except Exception:
            continue
    return title, lines


def _notes_shape(slide):
    try:
        for shp in slide.NotesPage.Shapes:
            if shp.Type == 14 and shp.PlaceholderFormat.Type == 2:
                return shp
    except Exception:
        pass
    return None


def _set_notes(slide, text: str) -> None:
    shp = _notes_shape(slide)
    if shp is not None:
        shp.TextFrame.TextRange.Text = text


def _layout(pres, kind: str):
    want = {"title": "title slide", "content": "title and content", "section": "section header",
            "two": "two content", "title_only": "title only", "blank": "blank"}.get(kind, "title and content")
    layouts = pres.SlideMaster.CustomLayouts
    for i in range(1, layouts.Count + 1):
        if want in layouts(i).Name.lower():
            return layouts(i)
    fallback = {"title": 1, "content": 2, "section": 3, "two": 4, "title_only": 6, "blank": 7}.get(kind, 2)
    return layouts(min(fallback, layouts.Count))


def _lines(text: str) -> list[str]:
    out = []
    for ln in (text or "").replace("\\n", "\n").splitlines():
        ln = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip()
        if ln:
            out.append(ln)
    return out


def _fill(slide, title: str | None, body: str | None) -> None:
    if title is not None:
        t = _title_shape(slide)
        if t is not None:
            t.TextFrame.TextRange.Text = title
    if body is not None:
        shapes = _body_shapes(slide)
        lines = _lines(body)
        if shapes:
            shapes[0].TextFrame.TextRange.Text = "\r".join(lines)
        elif lines:                                         # a layout with no body box: add one
            box = slide.Shapes.AddTextbox(1, 60, 130, slide.Parent.PageSetup.SlideWidth - 120, 300)
            box.TextFrame.TextRange.Text = "\r".join(lines)
            box.TextFrame.TextRange.Font.Size = 24


def _add_slide(pres, title: str = "", body: str = "", kind: str = "content", position: int = 0):
    idx = position if 1 <= position <= pres.Slides.Count + 1 else pres.Slides.Count + 1
    slide = pres.Slides.AddSlide(idx, _layout(pres, kind))
    _fill(slide, title or None, body or None)
    return slide


def parse_outline(outline: str) -> list[dict]:
    """Blank-line separated slides. First line = title; the rest = bullets; 'Notes: …' = speaker notes.
    'Slide 3: Title' prefixes and '# ' markers are tolerated."""
    slides = []
    for block in re.split(r"\n\s*\n", (outline or "").replace("\r", "").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        title = re.sub(r"^\s*(?:slide\s*\d+\s*[:.\-]\s*|#+\s*)", "", lines[0], flags=re.I).strip()
        bullets, notes = [], ""
        for ln in lines[1:]:
            m = re.match(r"^\s*notes?\s*:\s*(.*)$", ln, flags=re.I)
            if m:
                notes = m.group(1).strip()
            else:
                bullets.append(ln)
        slides.append({"title": title, "bullets": bullets, "notes": notes})
    return slides


# ── Saving ─────────────────────────────────────────────────────────────────

def _decks_dir() -> Path:
    d = known_folder("documents") / "Presentations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_new(pres, name: str) -> Path:
    path = unique_path(_decks_dir() / f"{safe_filename(name, 'Presentation')}.pptx")
    pres.SaveAs(str(path))
    return path


def _ensure_saved(pres) -> str:
    """Save in place; a deck that never had a name gets one first."""
    if not pres.Path:
        title = ""
        try:
            title = _slide_text(pres.Slides(1))[0] if pres.Slides.Count else ""
        except Exception:
            pass
        return str(_save_new(pres, title or "Presentation"))
    pres.Save()
    return pres.FullName



# ── Design: styles, transitions, animations ───────────────────────────────────

# A style is a complete look: a colour palette, a font pair, a gradient for title and section
# slides, and whether the deck is dark. Applied to the deck's theme, so every slide — including
# ones added later — follows it.
STYLES = {
    "modern":   dict(fonts=("Segoe UI Semibold", "Segoe UI"), dark=False, text="1F2937", bg="FFFFFF", text2="475569", bg2="EEF2FF",
                     accents=("4F46E5", "06B6D4", "F59E0B", "10B981", "EF4444", "8B5CF6"), grad=("4F46E5", "7C3AED")),
    "midnight": dict(fonts=("Segoe UI Semibold", "Segoe UI"), dark=True, text="E2E8F0", bg="0F172A", text2="94A3B8", bg2="1E293B",
                     accents=("38BDF8", "A78BFA", "F472B6", "34D399", "FBBF24", "FB7185"), grad=("0F172A", "1E3A8A")),
    "ocean":    dict(fonts=("Segoe UI Semibold", "Segoe UI"), dark=False, text="0C4A6E", bg="F0F9FF", text2="475569", bg2="E0F2FE",
                     accents=("0284C7", "14B8A6", "F59E0B", "6366F1", "EC4899", "22C55E"), grad=("0369A1", "06B6D4")),
    "sunset":   dict(fonts=("Segoe UI Semibold", "Segoe UI"), dark=False, text="431407", bg="FFF7ED", text2="78350F", bg2="FFEDD5",
                     accents=("F97316", "DB2777", "F59E0B", "8B5CF6", "14B8A6", "EF4444"), grad=("F97316", "DB2777")),
    "forest":   dict(fonts=("Segoe UI Semibold", "Segoe UI"), dark=False, text="14532D", bg="F0FDF4", text2="3F6212", bg2="DCFCE7",
                     accents=("16A34A", "0D9488", "CA8A04", "65A30D", "0EA5E9", "DC2626"), grad=("166534", "0D9488")),
    "minimal":  dict(fonts=("Segoe UI Light", "Segoe UI"), dark=False, text="111827", bg="FFFFFF", text2="6B7280", bg2="F3F4F6",
                     accents=("111827", "2563EB", "6B7280", "059669", "D97706", "DC2626"), grad=("111827", "374151")),
    "bold":     dict(fonts=("Segoe UI Black", "Segoe UI"), dark=True, text="FFFFFF", bg="111111", text2="D1D5DB", bg2="1F1F1F",
                     accents=("FACC15", "FB923C", "F43F5E", "22D3EE", "A3E635", "E879F9"), grad=("111111", "4C1D95")),
}
DEFAULT_STYLE = "modern"

# PowerPoint's own numbers for slide transitions and entrance animations
TRANSITIONS = {"fade": 3849, "dissolve": 1537, "cover": 1281, "wipe": 2817, "cut": 257, "none": 257}
ANIMATIONS = {"fade": 10, "wipe": 22, "fly": 2, "appear": 1}


def _rgb(hex6: str) -> int:
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    return r + g * 256 + b * 65536


def _style_key(name: str) -> str:
    n = (name or "").strip().lower()
    if n in STYLES:
        return n
    aliases = {"dark": "midnight", "night": "midnight", "blue": "ocean", "sea": "ocean", "orange": "sunset", "warm": "sunset",
               "green": "forest", "nature": "forest", "clean": "minimal", "simple": "minimal", "white": "minimal",
               "loud": "bold", "striking": "bold", "professional": "modern", "corporate": "modern", "business": "modern"}
    if n in aliases:
        return aliases[n]
    for k in list(STYLES) + list(aliases):
        if k in n:
            return aliases.get(k, k)
    return DEFAULT_STYLE


def _apply_style(app, p):
    """Recolour and re-font the whole deck, then polish every slide."""
    pres = _pres(app)
    want = (p.get("style") or "").strip().lower()
    if not want or want in ("list", "all"):
        return "Styles I can apply: " + ", ".join(STYLES) + ". (Ask for a built-in design with the theme action.)"
    key = _style_key(want)
    if key == DEFAULT_STYLE and want not in ("modern",) and not any(a in want for a in ("modern", "professional", "corporate", "business")):
        return f"I don't have a '{want}' style. Choose one of: {', '.join(STYLES)}."
    st = STYLES[key]
    _paint_theme(pres, st)
    n = _polish(pres, st, effects=True)
    return f"Applied the {key} style to {pres.Slides.Count} slides{n}."


def _paint_theme(pres, st) -> None:
    """Palette and fonts go on the theme itself, so layouts, new slides and charts all follow."""
    theme = pres.SlideMaster.Theme
    cols = theme.ThemeColorScheme
    # Dark1 = text colour, Light1 = background (PowerPoint draws text with Dark1 on Light1, even in a dark deck)
    slots = [(1, st["text"]), (2, st["bg"]), (3, st["text2"]), (4, st["bg2"])] + [(5 + i, c) for i, c in enumerate(st["accents"])]
    for idx, hexcol in slots:
        cols.Colors(idx).RGB = _rgb(hexcol)
    cols.Colors(11).RGB = _rgb(st["accents"][1])
    fonts = theme.ThemeFontScheme
    try:
        fonts.MajorFont(1).Name = st["fonts"][0]
        fonts.MinorFont(1).Name = st["fonts"][1]
    except Exception:
        pass
    master = pres.SlideMaster
    try:
        t = master.Shapes.Title.TextFrame.TextRange
        t.Font.Size = 42
        t.Font.Bold = False
    except Exception:
        pass
    try:                                                      # the body style: roomy, readable, accent bullets
        for shp in master.Shapes:
            if shp.Type == 14 and shp.PlaceholderFormat.Type == 2:
                shp.TextFrame.TextRange.Font.Size = 32
                shp.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 16
    except Exception:
        pass
    master.Background.Fill.Solid()
    master.Background.Fill.ForeColor.RGB = _rgb(st["bg"])
    _master_accent(master, st)


def _master_accent(master, st) -> None:
    """A slim accent bar down the left edge of every content slide (rebuilt, never doubled)."""
    for shp in list(master.Shapes):
        if shp.Name == "JARVIS accent bar":
            shp.Delete()
    h = master.Height if hasattr(master, "Height") else 540
    bar = master.Shapes.AddShape(1, 0, 0, 12, h)
    bar.Name = "JARVIS accent bar"
    bar.Line.Visible = 0
    bar.Fill.ForeColor.ObjectThemeColor = 5                   # Accent 1
    bar.ZOrder(1)                                             # send to back
    for shp in list(master.Shapes):
        if shp.Name == "JARVIS title underline":
            shp.Delete()
    try:                                                      # a short accent rule under the title
        t = master.Shapes.Title
        rule = master.Shapes.AddShape(5, t.Left + 6, t.Top + t.Height - 4, 72, 5)     # rounded rectangle
        rule.Name = "JARVIS title underline"
        rule.Line.Visible = 0
        rule.Fill.ForeColor.ObjectThemeColor = 5
    except Exception:
        pass


def _hero(slide, st) -> None:
    """Title / section slide: a diagonal gradient and white type, no accent bar."""
    slide.FollowMasterBackground = 0
    slide.DisplayMasterShapes = 0
    fill = slide.Background.Fill
    fill.TwoColorGradient(1, 1)
    fill.ForeColor.RGB = _rgb(st["grad"][0])
    fill.BackColor.RGB = _rgb(st["grad"][1])
    fill.GradientAngle = 35
    for shp in slide.Shapes:
        try:
            if shp.HasTextFrame and shp.TextFrame.HasText:
                shp.TextFrame.TextRange.Font.Color.RGB = _rgb("FFFFFF")
        except Exception:
            pass
    t = _title_shape(slide)
    if t is not None:
        t.TextFrame.TextRange.Font.Size = 56
    for b in _body_shapes(slide):
        try:
            b.TextFrame.TextRange.Font.Size = 26
        except Exception:
            pass


def _polish(pres, st, effects: bool = True, transition: str = "fade", animation: str = "fade") -> str:
    """Per-slide finishing: hero backgrounds, accent bullets, fit-to-box, slide numbers, transitions, animation."""
    total = pres.Slides.Count
    for i in range(1, total + 1):
        slide = pres.Slides(i)
        bodies = _body_shapes(slide)
        has_body = any(b.TextFrame.HasText for b in bodies)
        hero = i == 1 or not has_body
        if st is not None:
            if hero:
                _hero(slide, st)
            else:
                slide.FollowMasterBackground = -1
                slide.DisplayMasterShapes = -1
        for b in bodies:
            try:
                b.TextFrame2.AutoSize = 2                      # shrink text to fit instead of spilling off the slide
                if not hero:
                    b.TextFrame.VerticalAnchor = 3             # bullets centred in the box, not stuck to the top
                    b.TextFrame.TextRange.ParagraphFormat.Bullet.UseTextColor = 0
                    b.TextFrame.TextRange.ParagraphFormat.Bullet.Font.Color.ObjectThemeColor = 5
            except Exception:
                pass
        try:
            slide.HeadersFooters.SlideNumber.Visible = -1 if (not hero and total > 2) else 0
        except Exception:
            pass
        if effects:
            _set_effects(slide, hero, transition, animation)
    return ""


def _set_transition(slide, transition: str) -> None:
    try:
        tr = slide.SlideShowTransition
        tr.EntryEffect = TRANSITIONS.get(transition, TRANSITIONS["fade"])
        try:
            tr.Duration = 0.7
        except Exception:
            pass
    except Exception:
        pass


def _set_animation(slide, hero: bool, animation: str) -> None:
    try:
        seq = slide.TimeLine.MainSequence
        for k in range(seq.Count, 0, -1):                      # replace, never stack
            seq(k).Delete()
        if animation == "none":
            return
        effect = ANIMATIONS.get(animation, ANIMATIONS["fade"])
        for shp in _body_shapes(slide):
            if shp.TextFrame.HasText:
                if hero:
                    seq.AddEffect(shp, effect, 0, 3)           # appears by itself after the title
                else:
                    seq.AddEffect(shp, effect, 1, 1)           # one bullet per click
    except Exception:
        pass


def _set_effects(slide, hero: bool, transition: str, animation: str) -> None:
    _set_transition(slide, transition)
    _set_animation(slide, hero, animation)


def _effects_action(app, p):
    """Transitions and bullet animations for the whole deck (or one slide)."""
    pres = _pres(app)
    tr = (p.get("transition") or "").strip().lower()
    an = (p.get("animation") or "").strip().lower()
    if tr and tr not in TRANSITIONS:
        return f"Transitions I know: {', '.join(TRANSITIONS)}."
    if an and an not in ANIMATIONS and an != "none":
        return f"Animations I know: {', '.join(ANIMATIONS)}, none."
    only = int(p.get("slide") or 0)
    for i in range(1, pres.Slides.Count + 1):
        if only and i != only:
            continue
        slide = pres.Slides(i)
        hero = i == 1 or not any(b.TextFrame.HasText for b in _body_shapes(slide))
        if tr:
            _set_transition(slide, tr)
        if an:
            _set_animation(slide, hero, an)
    bits = [x for x in (f"{tr} transitions" if tr else "", f"{an} animations" if an else "") if x]
    return "Applied " + " and ".join(bits) + (f" to slide {only}." if only else " to every slide.") if bits else \
        "Tell me a transition (fade, dissolve, cover, wipe, cut) and/or an animation (fade, wipe, fly, appear, none)."


# ── Actions ─────────────────────────────────────────────────────────────────

def _open_app(app, p):
    _front(app)
    n = app.Presentations.Count
    return "PowerPoint is open." + (f" {n} presentation{'s' if n != 1 else ''} open." if n else " Nothing is open yet.")


def _new(app, p):
    topic = (p.get("title") or p.get("name") or p.get("topic") or "").strip()
    if p.get("outline"):
        return _create(app, p)
    pres = app.Presentations.Add(True)
    if topic:
        s = _add_slide(pres, topic, "", "title")
    path = _save_new(pres, topic or "Presentation")
    _front(app)
    return f"Created a new presentation, saved as {path}."


def _create(app, p):
    slides = parse_outline(p.get("outline", ""))
    if not slides:
        raise PptError("Give me the slides — a title line, then bullet lines for each, separated by blank lines.")
    pres = app.Presentations.Add(True)
    for i, sl in enumerate(slides):
        if i == 0:
            s = _add_slide(pres, sl["title"], "\n".join(sl["bullets"]), "title")
        else:
            s = _add_slide(pres, sl["title"], "\n".join(sl["bullets"]), "content" if sl["bullets"] else "section")
        if sl["notes"]:
            _set_notes(s, sl["notes"])
    theme = (p.get("theme") or "").strip()
    note = ""
    if theme:                                               # a built-in PowerPoint design was asked for by name
        note = " " + _apply_theme(app, {"theme": theme})
        _polish(pres, None, effects=True)
    else:
        key = _style_key(p.get("style") or "")
        _paint_theme(pres, STYLES[key])
        _polish(pres, STYLES[key], effects=True)
        note = f" Styled '{key}' with fade transitions and bullet animations."
    path = _save_new(pres, p.get("name") or p.get("title") or slides[0]["title"])
    _front(app)
    try:
        app.ActiveWindow.View.GotoSlide(1)
    except Exception:
        pass
    return f"Created a {len(slides)}-slide presentation and saved it as {path}.{note}"


def _open(app, p):
    target = (p.get("path") or "").strip().strip('"')
    name = (p.get("name") or "").strip()
    path = Path(os.path.expandvars(os.path.expanduser(target))) if target else None
    if path is None or not path.exists():
        found = find_decks(name or (Path(target).stem if target else ""))
        if not found:
            where = f" called '{name or target}'" if (name or target) else ""
            raise PptError(f"I couldn't find a presentation{where} in Desktop, Documents or Downloads.")
        path = found[0]
    for i in range(1, app.Presentations.Count + 1):         # already open? just bring it forward
        if app.Presentations(i).FullName.lower() == str(path).lower():
            app.Presentations(i).Windows(1).Activate()
            _front(app)
            return f"{path.name} was already open — switched to it."
    app.Presentations.Open(str(path), False, False, True)
    _front(app)
    pres = app.ActivePresentation
    more = ""
    if name:
        others = [f for f in find_decks(name) if f != path]
        if others:
            more = " Other matches: " + ", ".join(f.name for f in others[:3]) + "."
    return f"Opened {path.name} — {pres.Slides.Count} slide{'s' if pres.Slides.Count != 1 else ''}.{more}"


def _find(app, p):
    found = find_decks(p.get("name") or p.get("title") or "")
    if not found:
        return "I found no presentations in Desktop, Documents or Downloads."
    return "Presentations I found (newest first):\n" + "\n".join(f"- {f.name}  ({f.parent})" for f in found)


def _status(app, p):
    n = app.Presentations.Count
    if not n:
        return "PowerPoint is open, with no presentation."
    pres = _pres(app)
    cur = _current_index(app, pres)
    others = [app.Presentations(i).Name for i in range(1, n + 1) if app.Presentations(i).Name != pres.Name]
    t, _ = _slide_text(pres.Slides(cur)) if cur else ("", [])
    return (f"Working on {pres.Name}: {pres.Slides.Count} slides, on slide {cur}" + (f" (“{t}”)" if t else "")
            + (", slide show running" if _in_show(app) else "") + "."
            + (f" Also open: {', '.join(others)}." if others else ""))


def _read(app, p):
    pres = _pres(app)
    only = int(p.get("slide") or 0)
    out = [f"{pres.Name} — {pres.Slides.Count} slides"]
    for i in range(1, pres.Slides.Count + 1):
        if only and i != only:
            continue
        s = pres.Slides(i)
        title, lines = _slide_text(s)
        out.append(f"\nSlide {i}: {title or '(no title)'}")
        out += [f"  - {ln}" for ln in lines]
        nshape = _notes_shape(s)
        if nshape is not None and nshape.TextFrame.HasText:
            out.append(f"  Notes: {nshape.TextFrame.TextRange.Text.strip()[:300]}")
    text = "\n".join(out)
    return text if len(text) < 6000 else text[:6000] + "\n… (cut short)"


def _go(app, p, action):
    pres = _pres(app)
    total = pres.Slides.Count
    if not total:
        raise PptError("The presentation has no slides.")
    cur = _current_index(app, pres)
    target = {"next": cur + 1, "previous": cur - 1, "first": 1, "last": total}.get(action)
    if action == "goto":
        target = int(p.get("slide") or 0)
    if target is None or not 1 <= target <= total:
        return f"There is no slide {target}. The deck has {total}." if action in ("goto",) else \
            ("That's the last slide." if action == "next" else "That's the first slide.")
    if _in_show(app):
        app.SlideShowWindows(1).View.GotoSlide(target)
    else:
        app.ActiveWindow.View.GotoSlide(target)
    _front(app)
    title, _ = _slide_text(pres.Slides(target))
    return f"Slide {target} of {total}" + (f": {title}" if title else "") + "."


def _slide_arg(pres, p) -> int:
    n = int(p.get("slide") or 0)
    if not 1 <= n <= pres.Slides.Count:
        raise PptError(f"Which slide? The deck has {pres.Slides.Count}.")
    return n


def _add_slide_action(app, p):
    pres = _pres(app)
    kind = (p.get("layout") or "content").lower()
    s = _add_slide(pres, p.get("title") or "", p.get("text") or "", kind, int(p.get("position") or 0))
    if p.get("notes"):
        _set_notes(s, p["notes"])
    app.ActiveWindow.View.GotoSlide(s.SlideIndex)
    _front(app)
    return f"Added slide {s.SlideIndex}" + (f": {p['title']}" if p.get("title") else "") + "."


def _edit_slide(app, p):
    pres = _pres(app)
    n = _slide_arg(pres, p) if p.get("slide") else _current_index(app, pres)
    s = pres.Slides(n)
    if p.get("title") is None and p.get("text") is None and not p.get("notes"):
        raise PptError("Tell me the new title or text for the slide.")
    _fill(s, p.get("title"), p.get("text"))
    if p.get("notes"):
        _set_notes(s, p["notes"])
    return f"Updated slide {n}."


def _delete_slide(app, p):
    pres = _pres(app)
    n = _slide_arg(pres, p)
    title, _ = _slide_text(pres.Slides(n))
    pres.Slides(n).Delete()
    return f"Deleted slide {n}" + (f" (“{title}”)" if title else "") + ". PowerPoint's undo (Ctrl+Z) brings it back."


def _duplicate(app, p):
    pres = _pres(app)
    n = _slide_arg(pres, p)
    pres.Slides(n).Duplicate()
    return f"Duplicated slide {n}; the copy is slide {n + 1}."


def _move(app, p):
    pres = _pres(app)
    n = _slide_arg(pres, p)
    to = int(p.get("position") or 0)
    if not 1 <= to <= pres.Slides.Count:
        raise PptError(f"Move it to which position (1 to {pres.Slides.Count})?")
    pres.Slides(n).MoveTo(to)
    return f"Moved slide {n} to position {to}."


def _replace(app, p):
    pres = _pres(app)
    find, new = p.get("find") or "", p.get("replace")
    if not find or new is None:
        raise PptError("Tell me what to find and what to replace it with.")
    count = 0
    for s in pres.Slides:
        for shp in s.Shapes:
            try:
                if shp.HasTextFrame:
                    tr = shp.TextFrame.TextRange
                    after = 0
                    while count < 500:                       # resume after each hit: 'solar' -> 'Solar' must not re-match itself
                        hit = tr.Replace(find, new, after, False)
                        if hit is None:
                            break
                        count += 1
                        after = hit.Start + hit.Length - 1
            except Exception:
                continue
    return f"Replaced '{find}' with '{new}' {count} time{'s' if count != 1 else ''}." if count else f"I didn't find '{find}' in the slides."


def _add_text(app, p):
    pres = _pres(app)
    n = _slide_arg(pres, p) if p.get("slide") else _current_index(app, pres)
    s = pres.Slides(n)
    w, h = pres.PageSetup.SlideWidth, pres.PageSetup.SlideHeight
    box = s.Shapes.AddTextbox(1, w * 0.1, h * 0.65, w * 0.8, h * 0.2)
    box.TextFrame.TextRange.Text = "\r".join(_lines(p.get("text") or "")) or (p.get("text") or "")
    box.TextFrame.TextRange.Font.Size = int(p.get("size") or 24)
    return f"Added a text box to slide {n}."


def _add_image(app, p):
    pres = _pres(app)
    img = Path(os.path.expandvars(os.path.expanduser((p.get("image") or p.get("path") or "").strip().strip('"'))))
    if not img.is_file():
        raise PptError("I can't find that picture file.")
    n = _slide_arg(pres, p) if p.get("slide") else _current_index(app, pres)
    s = pres.Slides(n)
    w, h = pres.PageSetup.SlideWidth, pres.PageSetup.SlideHeight
    pic = s.Shapes.AddPicture(str(img), False, True, 0, 0, -1, -1)
    scale = min(w * 0.8 / pic.Width, h * 0.7 / pic.Height, 1.0)
    pic.LockAspectRatio = True
    pic.Width, pic.Height = pic.Width * scale, pic.Height * scale
    pic.Left, pic.Top = (w - pic.Width) / 2, (h - pic.Height) / 2 + h * 0.05
    return f"Added the picture to slide {n}."


def _notes(app, p):
    pres = _pres(app)
    n = _slide_arg(pres, p) if p.get("slide") else _current_index(app, pres)
    if not p.get("notes") and not p.get("text"):
        nshape = _notes_shape(pres.Slides(n))
        return f"Notes for slide {n}: {nshape.TextFrame.TextRange.Text.strip() or '(none)'}" if nshape is not None else "That slide has no notes."
    _set_notes(pres.Slides(n), p.get("notes") or p.get("text"))
    return f"Saved the speaker notes on slide {n}."


def _themes_dir(app) -> Path:
    return Path(app.Path).parent / "Document Themes 16"


def _apply_theme(app, p):
    pres = _pres(app)
    d = _themes_dir(app)
    files = sorted(d.glob("*.thmx")) if d.exists() else []
    want = (p.get("theme") or "").strip().lower()
    if not want or want in ("list", "all"):
        return "Themes I can apply: " + ", ".join(f.stem for f in files) if files else "I can't find PowerPoint's theme folder."
    match = next((f for f in files if f.stem.lower() == want), None) or next((f for f in files if want in f.stem.lower()), None)
    if match is None:
        return f"No theme called '{want}'. Try one of: " + ", ".join(f.stem for f in files)
    pres.ApplyTheme(str(match))
    return f"Applied the {match.stem} theme."


def _present(app, p):
    pres = _pres(app)
    if not pres.Slides.Count:
        raise PptError("The presentation has no slides to show.")
    if _in_show(app):
        return "The slide show is already running."
    start = int(p.get("slide") or 0)
    ss = pres.SlideShowSettings
    if start and 1 <= start <= pres.Slides.Count:
        ss.RangeType, ss.StartingSlide, ss.EndingSlide = _PP_SHOW_RANGE, start, pres.Slides.Count
    else:
        ss.RangeType = _PP_SHOW_ALL
        start = 1
    ss.Run()
    time.sleep(0.6)
    return f"Presenting {pres.Name} from slide {start}. Say next, previous, or go to slide … ; say stop the show to end it."


def _end_show(app, p):
    if not _in_show(app):
        return "No slide show is running."
    app.SlideShowWindows(1).View.Exit()
    return "Ended the slide show."


def _save(app, p):
    pres = _pres(app)
    return f"Saved {_ensure_saved(pres)}."


def _save_as(app, p):
    pres = _pres(app)
    name = (p.get("name") or p.get("title") or "").strip()
    target = (p.get("path") or "").strip().strip('"')
    if target:
        path = Path(os.path.expandvars(os.path.expanduser(target)))
    elif name:
        path = _decks_dir() / (safe_filename(Path(name).stem, "Presentation") + ".pptx")
    else:
        raise PptError("What should I call it?")
    path.parent.mkdir(parents=True, exist_ok=True)
    pres.SaveAs(str(unique_path(path) if path.exists() else path))
    return f"Saved a copy as {pres.FullName}."


def _export_pdf(app, p):
    pres = _pres(app)
    _ensure_saved(pres)
    out = Path(pres.FullName).with_suffix(".pdf")
    out = unique_path(out) if out.exists() else out
    pres.SaveCopyAs(str(out), _PP_SAVE_AS_PDF)
    return f"Exported to PDF: {out}."


def _export_images(app, p):
    pres = _pres(app)
    _ensure_saved(pres)
    folder = unique_path(Path(pres.Path) / f"{Path(pres.Name).stem}_slides")
    folder.mkdir(parents=True, exist_ok=True)
    pres.Export(str(folder), "PNG")
    return f"Saved each slide as a PNG in {folder}."


def _close(app, p):
    pres = _pres(app)
    name = pres.Name
    if _in_show(app):
        app.SlideShowWindows(1).View.Exit()
    _ensure_saved(pres)                                     # nothing is ever lost on close
    pres.Close()
    return f"Saved and closed {name}."


def _quit(app, p):
    saved = []
    for i in range(app.Presentations.Count, 0, -1):
        pres = app.Presentations(i)
        try:
            _ensure_saved(pres)
            saved.append(pres.Name)
        except Exception:
            pass
    app.Quit()
    return "Closed PowerPoint." + (f" Saved: {', '.join(saved)}." if saved else "")


_ACTIONS = {
    "open_app": _open_app, "new": _new, "create": _create, "open": _open, "find": _find,
    "status": _status, "read": _read, "add_slide": _add_slide_action, "edit_slide": _edit_slide,
    "delete_slide": _delete_slide, "duplicate_slide": _duplicate, "move_slide": _move,
    "replace": _replace, "add_text": _add_text, "add_image": _add_image, "notes": _notes,
    "theme": _apply_theme, "style": _apply_style, "effects": _effects_action, "present": _present, "end_show": _end_show, "save": _save,
    "save_as": _save_as, "export_pdf": _export_pdf, "export_images": _export_images,
    "close": _close, "quit": _quit,
}
_NAV = ("next", "previous", "first", "last", "goto")
_ALIASES = {"start": "present", "slideshow": "present", "play": "present", "stop": "end_show", "exit_show": "end_show",
            "back": "previous", "prev": "previous", "go_to": "goto", "slide": "goto", "launch": "open_app",
            "list": "find", "info": "status", "outline": "read", "export": "export_pdf", "pdf": "export_pdf",
            "remove_slide": "delete_slide", "new_slide": "add_slide", "change_theme": "theme", "design": "style",
            "restyle": "style", "transitions": "effects", "animate": "effects", "animations": "effects", "transition": "effects"}


def powerpoint(parameters: dict = None, player=None, **_) -> str:
    p = dict(parameters or {})
    action = (p.get("action") or "open_app").lower().strip().replace(" ", "_")
    action = _ALIASES.get(action, action)
    if not _OK:
        return "PowerPoint control works on Windows with the PowerPoint desktop app installed."
    if action not in _ACTIONS and action not in _NAV:
        return "Unknown action. Try: open_app, create, open, read, goto, next, previous, add_slide, edit_slide, replace, present, save, export_pdf."
    if action == "find":
        try:
            return _find(None, p)
        except Exception as e:
            return f"PowerPoint error: {e}"
    import pythoncom
    pythoncom.CoInitialize()
    try:
        app = _app()
        if action in _NAV:
            return _go(app, p, action)
        return _ACTIONS[action](app, p)
    except PptError as e:
        return str(e)
    except Exception as e:
        msg = str(e)
        if "Rejected" in msg or "RPC" in msg or "-2147" in msg:
            msg = "PowerPoint is busy or showing a dialog. Close any open dialog and try again. (" + msg[:80] + ")"
        return f"PowerPoint error: {msg}"
    finally:
        pythoncom.CoUninitialize()


TOOL = {
    "name": "powerpoint",
    "description": (
        "Full control of Microsoft PowerPoint. ALWAYS use this — not open_app, browser or screen tools — for "
        "anything about PowerPoint, presentations, decks or slides: 'open PowerPoint', 'open my sales deck', "
        "'create a presentation about solar energy', 'go to slide 5', 'next slide', 'start the presentation', "
        "'add a slide', 'change the title of slide 2', 'replace X with Y everywhere', 'add speaker notes', "
        "'make it look professional / more colourful / dark', 'add transitions or animations', 'save it', 'export as PDF', 'close it'. "
        "To create a deck, WRITE THE CONTENT yourself and pass it as `outline`: slides separated by a blank line, "
        "first line of each = slide title, then one bullet per line, optional 'Notes: …' line. "
        "Decks are designed automatically — a colour style, fonts, gradient title slides, fade transitions and "
        "one-bullet-per-click animation; pick another look with `style` (modern, midnight, ocean, sunset, forest, "
        "minimal, bold). Use `read` to see what is in the open deck before editing it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": (
                "open_app | create | new | open | find | status | read | goto | next | previous | first | last | "
                "add_slide | edit_slide | delete_slide | duplicate_slide | move_slide | replace | add_text | add_image | "
                "notes | theme | style | effects | present | end_show | save | save_as | export_pdf | export_images | close | quit")},
            "name": {"type": "STRING", "description": "A presentation's file name (open / find / save_as), or a title for create"},
            "path": {"type": "STRING", "description": "Full path of a .pptx to open, or where to save_as"},
            "outline": {"type": "STRING", "description": "create: the deck. Blank line between slides; first line = title, following lines = bullets; 'Notes: …' = speaker notes"},
            "slide": {"type": "INTEGER", "description": "Slide number for goto / read / edit / delete / present-from / notes"},
            "position": {"type": "INTEGER", "description": "add_slide: where to insert; move_slide: the new position"},
            "title": {"type": "STRING", "description": "add_slide / edit_slide: the slide title"},
            "text": {"type": "STRING", "description": "add_slide / edit_slide / add_text: body text, one bullet per line"},
            "layout": {"type": "STRING", "description": "add_slide: content (default) | title | section | two | title_only | blank"},
            "notes": {"type": "STRING", "description": "Speaker notes"},
            "find": {"type": "STRING", "description": "replace: text to find"},
            "replace": {"type": "STRING", "description": "replace: replacement text"},
            "theme": {"type": "STRING", "description": "theme: one of PowerPoint's built-in designs (Ion, Facet, Retrospect…) or 'list'; also usable with create"},
            "style": {"type": "STRING", "description": "style / create: modern (default) | midnight | ocean | sunset | forest | minimal | bold | list"},
            "transition": {"type": "STRING", "description": "effects: fade | dissolve | cover | wipe | cut"},
            "animation": {"type": "STRING", "description": "effects: fade | wipe | fly | appear | none (bullets appear one per click)"},
            "image": {"type": "STRING", "description": "add_image: path of a picture file"},
            "size": {"type": "INTEGER", "description": "add_text: font size"},
        },
        "required": ["action"],
    },
    "handler": powerpoint,
}
