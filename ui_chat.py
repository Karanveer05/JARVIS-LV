"""
ui_chat.py — the floating robot and the chat interface that opens from it.

    * RobotWidget   a small always-on-top robot that floats on the desktop, reacts
                    to listening / thinking / speaking / muted, and talks through a
                    speech bubble. Click it and the chat window opens.
    * ChatWindow    a light, glassy chat interface: icon rail, conversation, input
                    bar, and a side panel with quick actions, mic toggle, history
                    and settings.
    * ChatController  stands in for the old MainWindow. It exposes exactly the
                    attributes and signals that JarvisUI (ui.py) drives, so main.py,
                    the actions and the plugins need no change.

Everything runs on the Qt thread; other threads reach it through the signals on
ChatController, the same way they reached the old window. The old HUD is still in
ui.py: set "ui_mode": "classic" in config/api_keys.json to get it back.
"""
from __future__ import annotations

import json
import math
import os
import platform
import random
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
    QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QDesktopServices, QBrush, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence,
    QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizeGrip, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    HAVE_VIDEO = True
except Exception:                       # pragma: no cover - depends on the Qt install
    HAVE_VIDEO = False

_OS = platform.system()


def _base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"
CHATS_FILE = BASE_DIR / "memory" / "chats.json"
STATE_FILE = BASE_DIR / "memory" / "ui_state.json"


# ── Palette ───────────────────────────────────────────────────────────────────
class P:
    """The colours of the chat window. `P.apply(True)` switches every one of them to the dark set;
    widgets read these when they are built, so a theme change rebuilds the window."""

    BG1 = "#dfe4fb"
    BG2 = "#f2eefa"
    CARD = "#fbfbfe"
    INK = "#14162b"
    INK2 = "#4b5068"
    DIM = "#9298b0"
    LINE = "#e7e9f3"
    SOFT = "#f1f2fa"
    BLUE = "#6d7cff"
    CYAN = "#52dcff"
    RED = "#ff4d6d"
    GREEN = "#2fcf8a"
    AMBER = "#ffb347"
    SURFACE = "#ffffff"              # cards, bubbles, inputs
    ON_INK = "#ffffff"               # text on an INK-coloured button
    GLASS = "rgba(255,255,255,0.75)"
    HOVER = "rgba(255,255,255,0.85)"
    HOVER2 = "rgba(255,255,255,0.95)"
    RING = "rgba(255,255,255,0.9)"
    ERR_BG = "#fff0f3"
    OK_BG = "#effbf5"
    WARN_BG = "#fff2dc"
    DARK = False

    _LIGHT: dict = {}
    _DARKSET = {
        "BG1": "#0e1124", "BG2": "#1a1637", "CARD": "#1d2140", "INK": "#eef0ff", "INK2": "#b4b9d8", "DIM": "#868cae",
        "LINE": "#2c3052", "SOFT": "#242848", "BLUE": "#8592ff", "SURFACE": "#1c2040", "ON_INK": "#13152b",
        "GLASS": "rgba(255,255,255,0.05)", "HOVER": "rgba(255,255,255,0.09)", "HOVER2": "rgba(255,255,255,0.14)",
        "RING": "rgba(255,255,255,0.10)", "ERR_BG": "#3a1f2b", "OK_BG": "#17352c", "WARN_BG": "#3d3320",
    }

    @classmethod
    def apply(cls, dark: bool) -> None:
        if not cls._LIGHT:                                 # remember the light values the first time
            cls._LIGHT = {k: getattr(cls, k) for k in cls._DARKSET}
        for k, v in (cls._DARKSET if dark else cls._LIGHT).items():
            setattr(cls, k, v)
        cls.DARK = bool(dark)


_FONT = "Segoe UI" if _OS == "Windows" else "Helvetica Neue" if _OS == "Darwin" else "Sans Serif"


def _font(size: int = 10, bold: bool = False) -> QFont:
    f = QFont(_FONT, size)
    f.setBold(bold)
    return f


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _read_config() -> dict:
    d = _read_json(API_FILE, {})
    return d if isinstance(d, dict) else {}


def _shadow(widget: QWidget, blur: int = 30, alpha: int = 45, dy: int = 8) -> None:
    fx = QGraphicsDropShadowEffect(widget)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(QColor(60, 70, 140, alpha))
    widget.setGraphicsEffect(fx)


def apply_app_palette(dark: bool) -> None:
    """Default widget colours — text with no style of its own, combo-box popups, menus, scroll bars."""
    app = QApplication.instance()
    if app is None:
        return
    from PyQt6.QtGui import QPalette
    pal = QPalette()
    if dark:
        roles = {QPalette.ColorRole.Window: P.BG1, QPalette.ColorRole.WindowText: P.INK, QPalette.ColorRole.Base: P.SURFACE,
                 QPalette.ColorRole.AlternateBase: P.SOFT, QPalette.ColorRole.Text: P.INK, QPalette.ColorRole.Button: P.SOFT,
                 QPalette.ColorRole.ButtonText: P.INK, QPalette.ColorRole.ToolTipBase: P.SURFACE,
                 QPalette.ColorRole.ToolTipText: P.INK, QPalette.ColorRole.Highlight: P.BLUE,
                 QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.PlaceholderText: P.DIM}
        for role, col in roles.items():
            pal.setColor(role, QColor(col))
    app.setPalette(pal)


def _screen_rect():
    s = QApplication.primaryScreen()
    return s.availableGeometry() if s else None


# ══════════════════════════════════════════════════════════════════════════════
#  Robot
# ══════════════════════════════════════════════════════════════════════════════
def _draw_heart(p: QPainter, x: float, y: float, s: float, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(x, y + s * 0.9)
    path.cubicTo(x - s * 1.6, y - s * 0.1, x - s * 0.7, y - s * 1.2, x, y - s * 0.3)
    path.cubicTo(x + s * 0.7, y - s * 1.2, x + s * 1.6, y - s * 0.1, x, y + s * 0.9)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _draw_sparkle(p: QPainter, x: float, y: float, s: float, color: QColor) -> None:
    star = QPainterPath()
    star.moveTo(x, y - s)
    star.quadTo(x, y, x + s, y)
    star.quadTo(x, y, x, y + s)
    star.quadTo(x, y, x - s, y)
    star.quadTo(x, y, x, y - s)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(star)


class RobotWidget(QWidget):
    """The floating robot. Drawn entirely with QPainter — no image files."""

    clicked = pyqtSignal()
    moved = pyqtSignal()
    menu_requested = pyqtSignal(QPoint)
    shaken = pyqtSignal(int)         # the user shook it: 1 = dizzy, 2 and up = angry
    reacted = pyqtSignal(str)        # something else was done to it: pickup, drop, resize_big, resize_small, woke

    W, H = 128, 172            # drawn in a 128 x 150 box with 22 px of headroom for the "?" and sparkles

    # What it gets up to when nobody is talking to it: (mood, seconds). Picked at
    # random every few seconds, never the same one twice in a row.
    ANTICS = (("WINK", 1.4), ("SILLY", 2.8), ("DANCE", 4.5), ("LAUGH", 3.0), ("YAWN", 3.4),
              ("COOL", 3.6), ("SURPRISED", 1.7), ("LOVE", 2.8), ("SCAN", 3.4), ("DIZZY", 2.4))
    ANTIC_EVERY = (6.0, 13.0)        # seconds between antics
    NAP_AFTER = 150.0                # seconds of being left alone before it dozes off
    # Per-mood face colour; anything not listed is the usual cyan.
    TINT = {"MUTED": P.AMBER, "ANGRY": P.RED, "LOVE": "#ff7aa8", "DIZZY": P.AMBER, "SURPRISED": "#8fe9ff"}

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.scale_factor = 1.0
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setToolTip("Click to chat · drag to move · scroll to resize")

        self.state = "LISTENING"
        self.muted = False
        self.alert = False          # a confirmation is waiting
        self._mood = ""             # CONFUSED / ASK / HAPPY / WAVE / ANGRY / DIZZY ... for a few seconds
        self._mood_until = 0.0
        self._mood_start = 0.0
        self._prog = 0.0            # how far through the current mood we are, 0..1
        self._nap = False           # dozed off after being left alone
        self._last_active = time.monotonic()
        self._next_antic = time.monotonic() + random.uniform(*self.ANTIC_EVERY)
        self._last_antic = ""
        self._trail: list[tuple[float, int, int]] = []   # recent drag positions, for shake detection
        self._shakes = 0
        self._last_shake = 0.0
        self._shake_cool = 0.0
        self._paint_failed = False
        self._wheel_net = 0
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.timeout.connect(self._wheel_done)
        self.level = 0.0            # live output level 0..1
        self._hover = False
        self._t0 = time.monotonic()
        self._next_blink = self._t0 + 3.0
        self._blink_until = 0.0
        self._visemes: list[list] = []     # [at, hop, frames]
        self._vlock = threading.Lock()
        self._press_pos: QPoint | None = None
        self._win_pos: QPoint | None = None
        self._dragging = False

        self._tick = QTimer(self)
        self._tick.timeout.connect(self.update)
        self._tick.start(33)

    # -- data in ---------------------------------------------------------------
    def push_visemes(self, frames, hop: float, at: float) -> None:
        if not frames:
            return
        with self._vlock:
            self._visemes.append([float(at), max(1e-3, float(hop)), list(frames)])
            self._visemes = self._visemes[-12:]

    def _mouth(self) -> float:
        now = time.time()
        with self._vlock:
            while self._visemes and now > self._visemes[0][0] + len(self._visemes[0][2]) * self._visemes[0][1]:
                self._visemes.pop(0)
            if self._visemes:
                at, hop, frames = self._visemes[0]
                i = int((now - at) / hop)
                if 0 <= i < len(frames):
                    try:
                        return max(0.0, min(1.0, float(frames[i][1])))
                    except Exception:
                        pass
        return max(0.0, min(1.0, self.level * 1.6))

    # -- mouse -----------------------------------------------------------------
    def mousePressEvent(self, e):
        self._touched()
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._win_pos = self.pos()
            self._dragging = False
            self._trail.clear()
        elif e.button() == Qt.MouseButton.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._press_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._press_pos
            if self._dragging or abs(delta.x()) + abs(delta.y()) > 6:
                if not self._dragging:
                    self.reacted.emit("pickup")
                self._dragging = True
                self.move(self._win_pos + delta)
                self.moved.emit()
                self._track_shake(e.globalPosition().toPoint(), time.monotonic())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            was_drag = self._dragging
            self._press_pos = None
            self._dragging = False
            self._trail.clear()
            if was_drag:
                self.clamp_to_screen()
                self.moved.emit()
                if time.monotonic() - self._last_shake > 3.0:       # after a shake it has already had its say
                    self.reacted.emit("drop")
            else:
                self.clicked.emit()

    def enterEvent(self, e):
        self._hover = True
        self._touched()

    def leaveEvent(self, e):
        self._hover = False

    # -- shaking it ------------------------------------------------------------
    def _track_shake(self, pos: QPoint, now: float) -> None:
        """Dragging it back and forth, fast, is a shake. Counts direction reversals over the
        last second; small jitters and slow moves do not add up to one."""
        tr = self._trail
        tr.append((now, pos.x(), pos.y()))
        while tr and now - tr[0][0] > 0.9:
            tr.pop(0)
        if len(tr) < 8 or now < self._shake_cool:
            return
        xs, ys = [q[1] for q in tr], [q[2] for q in tr]
        if max(max(xs) - min(xs), max(ys) - min(ys)) < 45:   # a tremor is not a shake: it has to swing wide
            return
        reversals, path = 0, 0
        for axis in (1, 2):
            last = 0
            for a, b in zip(tr, tr[1:]):
                d = b[axis] - a[axis]
                if abs(d) < 4:
                    continue
                path += abs(d)
                sign = 1 if d > 0 else -1
                if last and sign != last:
                    reversals += 1
                last = sign
        if reversals >= 4 and path >= 160:
            self._on_shake(now)

    def _on_shake(self, now: float) -> None:
        self._shake_cool = now + 1.1
        self._trail.clear()
        if now - self._last_shake > 10.0:               # calmed down since last time
            self._shakes = 0
        self._last_shake = now
        self._shakes += 1
        if self._shakes == 1:
            self.set_mood("DIZZY", 3.0)
        else:
            self.set_mood("ANGRY", 4.5 + min(self._shakes, 4))
        self.shaken.emit(self._shakes)

    # -- being left alone ------------------------------------------------------
    def _touched(self) -> None:
        """Anything that involves the user: wakes it up if it was napping."""
        self._last_active = time.monotonic()
        if self._nap:
            self._nap = False
            self.set_mood("YAWN" if not self._dragging else "SURPRISED", 2.6)
            self.reacted.emit("woke")

    def _life(self, now: float) -> None:
        """Idle behaviour: every few seconds do something silly; after a long quiet, nap."""
        busy = (self.state != "LISTENING" or self.alert or self.muted or self._hover or self._dragging)
        if busy:
            self._last_active = now
            self._nap = False
            self._next_antic = max(self._next_antic, now + 4.0)
            return
        if self._mood and now < self._mood_until:
            return                                      # already feeling something
        if self._nap:
            return
        if now - self._last_active > self.NAP_AFTER:
            self._nap = True
            return
        if now >= self._next_antic:
            choices = [a for a in self.ANTICS if a[0] != self._last_antic]
            mood, secs = random.choice(choices)
            self._last_antic = mood
            self.set_mood(mood, secs)
            self._next_antic = now + secs + random.uniform(*self.ANTIC_EVERY)

    MIN_SCALE, MAX_SCALE = 0.8, 2.2

    def set_scale(self, s: float) -> None:
        """Resize the robot. It grows and shrinks around its centre, so it does not
        jump away from where the user put it."""
        s = max(self.MIN_SCALE, min(self.MAX_SCALE, float(s)))
        if abs(s - self.scale_factor) < 1e-3:
            return
        c = self.geometry().center()
        self.scale_factor = s
        self.setFixedSize(int(self.W * s), int(self.H * s))
        self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)
        self.clamp_to_screen()
        self.moved.emit()

    def wheelEvent(self, e):
        step = 0.1 if e.angleDelta().y() > 0 else -0.1
        before = self.scale_factor
        self.set_scale(self.scale_factor + step)
        if abs(self.scale_factor - before) > 1e-3:
            self._wheel_net += 1 if self.scale_factor > before else -1
            self._wheel_timer.start(700)                         # react once, when the scrolling stops

    def _wheel_done(self) -> None:
        net, self._wheel_net = self._wheel_net, 0
        if net:
            self.reacted.emit("resize_big" if net > 0 else "resize_small")

    def clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        r = screen.availableGeometry() if screen else None
        if not r:
            return
        x = max(r.left(), min(self.x(), r.right() - self.width() + 1))
        y = max(r.top(), min(self.y(), r.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def keep_visible(self) -> None:
        """Run on a timer. A hidden window, a lost always-on-top flag, a monitor that was
        unplugged or a lost position must never leave the robot unseen: put it back."""
        if not self.isVisible():
            self.show()
        if self.windowOpacity() < 1.0:
            self.setWindowOpacity(1.0)
        if QGuiApplication.screenAt(self.frameGeometry().center()) is None:      # off every screen
            r = _screen_rect()
            if r:
                self.move(r.right() - self.width() - 40, r.bottom() - self.height() - 40)
                self.moved.emit()
        else:
            self.clamp_to_screen()
        if _OS == "Windows":
            try:                                            # reassert always-on-top without stealing focus
                import ctypes
                from ctypes import wintypes
                fn = ctypes.windll.user32.SetWindowPos
                fn.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
                fn(wintypes.HWND(int(self.winId())), wintypes.HWND(-1), 0, 0, 0, 0,
                   0x0001 | 0x0002 | 0x0010 | 0x0040)       # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
            except Exception:
                self.raise_()
        else:
            self.raise_()
        self.update()

    # -- moods -----------------------------------------------------------------
    def set_mood(self, mood: str, seconds: float = 3.0) -> None:
        """A short-lived expression that overrides the state: CONFUSED, ASK, HAPPY, WAVE, and the
        antics — ANGRY, DIZZY, LAUGH, SURPRISED, LOVE, SILLY, WINK, DANCE, YAWN, COOL, SCAN."""
        now = time.monotonic()
        self._mood = mood
        self._mood_start = now
        self._mood_until = now + seconds
        if mood:
            self._nap = False

    def _expr(self, now: float) -> str:
        if self._mood and now < self._mood_until:
            return self._mood
        if self.state == "SPEAKING":
            return "SPEAKING"
        if self.muted:
            return "MUTED"
        if self._nap and self.state == "LISTENING":
            return "SLEEPING"
        if self._hover and self.state == "LISTENING":
            return "WAVE"
        return self.state if self.state in ("LISTENING", "THINKING", "SLEEPING") else "LISTENING"

    # -- drawing ---------------------------------------------------------------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint_scene(p)
        except Exception as err:                           # a bug in one expression must not make it vanish
            if not self._paint_failed:
                self._paint_failed = True
                print(f"[Robot] drawing failed ({type(err).__name__}: {err}); showing a plain face instead")
            self._mood, self._nap = "", False
            p.resetTransform()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#e9edfa"))
            p.drawEllipse(QRectF(self.width() * 0.15, self.height() * 0.25, self.width() * 0.7, self.height() * 0.55))
            p.setBrush(QColor("#232a52"))
            p.drawEllipse(QRectF(self.width() * 0.25, self.height() * 0.35, self.width() * 0.5, self.height() * 0.3))
            p.setBrush(QColor(P.CYAN))
            for fx in (0.38, 0.62):
                p.drawEllipse(QPointF(self.width() * fx, self.height() * 0.5), self.width() * 0.05, self.width() * 0.07)
        finally:
            p.end()

    def _paint_scene(self, p: QPainter) -> None:
        p.scale(self.scale_factor, self.scale_factor)
        p.translate(0, 22)                                 # headroom above the head
        now = time.monotonic()
        t = now - self._t0
        self._life(now)
        ex = self._expr(now)
        self._prog = (min(1.0, (now - self._mood_start) / max(0.1, self._mood_until - self._mood_start))
                      if self._mood and now < self._mood_until else 0.0)
        prog = self._prog

        amp = 5.0 if ex in ("HAPPY", "WAVE", "DANCE", "LOVE") else 4.0
        # extra body movement per mood: hops (negative = up) and sideways sway
        jump, sway = 0.0, 0.0
        if ex == "DANCE":
            jump, sway = -abs(math.sin(t * 6)) * 6, math.sin(t * 3) * 9
        elif ex == "LAUGH":
            jump = -abs(math.sin(t * 11)) * 5
        elif ex == "SURPRISED":
            jump = -16 * max(0.0, 1.0 - prog * 4.5)       # a startled hop, then it settles
        elif ex == "ANGRY":
            sway = math.sin(t * 52) * 1.8                 # trembling with rage
        elif ex == "DIZZY":
            sway = math.sin(t * 3.2) * 7
        elif ex == "SILLY":
            sway = math.sin(t * 5) * 4
        bob = math.sin(t * 2.0) * amp + jump
        cx = self.W / 2 + sway
        hc = 56 + bob                                      # head centre y
        tilt = {"CONFUSED": -9 + math.sin(t * 3) * 2, "ASK": 8, "THINKING": -5,
                "LISTENING": 4, "SLEEPING": 10,
                "ANGRY": math.sin(t * 45) * 2.2, "DIZZY": math.sin(t * 3.2) * 13,
                "LAUGH": math.sin(t * 11) * 4, "SILLY": math.sin(t * 5) * 9,
                "DANCE": math.sin(t * 3) * 11, "LOVE": 6 + math.sin(t * 2) * 3, "COOL": -5,
                "WINK": 7, "SCAN": math.sin(t * 1.6) * 7,
                "YAWN": -6 * math.sin(math.pi * prog), "SURPRISED": -3,
                }.get(ex, math.sin(t * 1.3) * 1.2)
        if ex == "SPEAKING":
            tilt += math.sin(t * 7) * 1.5

        # ground shadow shrinks as the robot rises
        sh = max(0.25, 0.55 + 0.25 * (bob / amp))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(70, 80, 150, int(55 * sh + 20)))
        p.drawEllipse(QRectF(cx - 30 * sh - 6, 134, 60 * sh + 12, 9))

        # thruster glow under the body
        g = QRadialGradient(QPointF(cx, 128 + bob * 0.4), 30)
        glow = QColor(self.TINT.get(ex, P.CYAN))
        glow.setAlpha(60 if ex in ("SLEEPING", "MUTED") else 150)
        g.setColorAt(0, glow)
        g.setColorAt(1, QColor(82, 220, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - 30, 118 + bob * 0.4, 60, 22))

        # body
        body = QLinearGradient(0, 0, 0, 1)
        body.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
        body.setColorAt(0, QColor("#ffffff"))
        body.setColorAt(1, QColor("#d3daf0"))
        p.setBrush(QBrush(body))
        p.setPen(QPen(QColor(150, 160, 205, 110), 1))
        p.drawRoundedRect(QRectF(cx - 23, 86 + bob, 46, 38), 19, 19)
        chest = QColor(self.TINT.get(ex, P.CYAN))
        chest.setAlpha(70 if ex == "SLEEPING" else int(190 + 40 * math.sin(t * 4)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(chest)
        p.drawEllipse(QRectF(cx - 4, 101 + bob, 8, 8))

        # head (tilts around the neck)
        p.save()
        p.translate(cx, hc + 30)
        p.rotate(tilt)
        p.translate(-cx, -(hc + 30))
        self._draw_head(p, ex, t, now, cx, hc)
        p.restore()

        # arms, in front of everything; hand targets that touch the head follow its tilt
        def on_head(dx: float, dy: float) -> QPointF:
            a = math.radians(tilt)
            vx, vy = dx, dy - 30                           # relative to the neck pivot
            return QPointF(cx + vx * math.cos(a) - vy * math.sin(a),
                           hc + 30 + vx * math.sin(a) + vy * math.cos(a))

        left, right = self._arm_targets(ex, t, cx, hc, bob, on_head)
        self._draw_arm(p, QPointF(cx - 31, 97 + bob), left, -1, cx)
        self._draw_arm(p, QPointF(cx + 31, 97 + bob), right, +1, cx)

        if self.alert:                                     # a confirmation is waiting
            pulse = 0.5 + 0.5 * math.sin(t * 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 77, 109, int(190 + 60 * pulse)))
            p.drawEllipse(QRectF(self.W - 30, -14, 22, 22))
            p.setPen(QColor("white"))
            p.setFont(_font(11, True))
            p.drawText(QRectF(self.W - 30, -14, 22, 22), Qt.AlignmentFlag.AlignCenter, "!")

    # -- arms ------------------------------------------------------------------
    def _arm_targets(self, ex, t, cx, hc, bob, on_head):
        """Where each hand goes for this expression: (left, right) as QPointF."""
        rest_l = QPointF(cx - 40 + math.sin(t * 2.0 + 1) * 2, 124 + bob + math.sin(t * 2.0) * 1.5)
        rest_r = QPointF(cx + 40 - math.sin(t * 2.0 + 1) * 2, 124 + bob - math.sin(t * 2.0) * 1.5)
        if ex == "LISTENING":                              # cupping a hand to the ear
            return rest_l, on_head(46 + math.sin(t * 3) * 1.5, 6)
        if ex == "THINKING":                               # hand on chin, other arm across
            return QPointF(cx - 6, 112 + bob), on_head(10 + math.sin(t * 2.2) * 1.2, 34)
        if ex == "SPEAKING":                               # talking with the hands
            return (QPointF(cx - 44 + math.sin(t * 3.1) * 5, 108 + bob + math.sin(t * 4.3) * 7),
                    QPointF(cx + 44 + math.sin(t * 2.7 + 1) * 5, 106 + bob + math.sin(t * 5.1) * 8))
        if ex == "CONFUSED":                               # scratching the head, shrugging
            return (QPointF(cx - 50, 100 + bob + math.sin(t * 2) * 2),
                    on_head(26 + math.sin(t * 11) * 4, -36))
        if ex == "ASK":                                    # an open hand held out: "well?"
            return rest_l, QPointF(cx + 48, 104 + bob + math.sin(t * 3) * 3)
        if ex == "HAPPY":                                  # arms up
            return (QPointF(cx - 46, hc - 16 + math.sin(t * 8) * 4),
                    QPointF(cx + 46, hc - 16 + math.sin(t * 8 + 1.5) * 4))
        if ex == "WAVE":
            return rest_l, QPointF(cx + 46 + math.sin(t * 10) * 7, hc - 14)
        if ex == "MUTED":                                  # finger to the lips: "shh"
            return rest_l, on_head(6, 22)
        prog = self._prog
        if ex == "ANGRY":                                  # fists clenched at the hips, trembling
            j = math.sin(t * 50) * 1.6
            return QPointF(cx - 31 + j, 113 + bob), QPointF(cx + 31 - j, 113 + bob)
        if ex == "DIZZY":                                  # arms flopping out for balance
            return (QPointF(cx - 50, 108 + bob + math.sin(t * 3.2) * 9),
                    QPointF(cx + 50, 108 + bob - math.sin(t * 3.2) * 9))
        if ex == "LAUGH":                                  # holding its belly
            k = math.sin(t * 14) * 2
            return QPointF(cx - 12, 116 + bob + k), QPointF(cx + 12, 116 + bob - k)
        if ex == "SURPRISED":                              # hands up to the cheeks
            return on_head(-31, 16), on_head(31, 16)
        if ex == "LOVE":                                   # hands clasped over the heart
            return QPointF(cx - 6, 108 + bob), QPointF(cx + 6, 108 + bob)
        if ex == "SILLY":                                  # waggling hands by the ears
            w = math.sin(t * 9) * 5
            return on_head(-48 + w, 2), on_head(48 - w, 2)
        if ex == "WINK":
            return rest_l, on_head(31, -8)                 # a little salute
        if ex == "YAWN":                                   # big stretch
            lift = math.sin(math.pi * prog)
            return (QPointF(cx - 44, 112 + bob - lift * 40), QPointF(cx + 44, 112 + bob - lift * 40))
        if ex == "COOL":                                   # arms folded
            return QPointF(cx + 11, 110 + bob), QPointF(cx - 11, 113 + bob)
        if ex == "DANCE":                                  # one arm up, one down, swapping
            s = math.sin(t * 6)
            return QPointF(cx - 42, hc - 8 - s * 18), QPointF(cx + 42, hc - 8 + s * 18)
        if ex == "SCAN":                                   # hand over the brow, looking far away
            return rest_l, on_head(30, -4)
        if ex == "SLEEPING":
            return (QPointF(cx - 36, 122 + bob), QPointF(cx + 36, 122 + bob))
        return rest_l, rest_r

    @staticmethod
    def _elbow(s: QPointF, h: QPointF, l1: float, l2: float, side: int, cx: float):
        """Two-link inverse kinematics: where the elbow sits so the hand reaches `h`."""
        dx, dy = h.x() - s.x(), h.y() - s.y()
        n = max(1e-3, math.hypot(dx, dy))
        d = min(n, l1 + l2 - 0.5)                          # cannot reach further than the arm is long
        ux, uy = dx / n, dy / n
        a = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
        hh = math.sqrt(max(0.0, l1 * l1 - a * a))
        mx, my = s.x() + ux * a, s.y() + uy * a
        c1 = QPointF(mx - uy * hh, my + ux * hh)
        c2 = QPointF(mx + uy * hh, my - ux * hh)

        def outward_and_low(c: QPointF) -> float:         # the elbow bends away from the body and down
            return (c.x() - cx) * side + c.y() * 0.4

        elbow = c1 if outward_and_low(c1) >= outward_and_low(c2) else c2
        hand = QPointF(s.x() + ux * d, s.y() + uy * d)
        return elbow, hand

    def _draw_arm(self, p: QPainter, shoulder: QPointF, hand: QPointF, side: int, cx: float) -> None:
        l1 = l2 = 15.5
        elbow, hand = self._elbow(shoulder, hand, l1, l2, side, cx)
        for width, col in ((10.5, QColor(150, 162, 214, 150)), (8.0, QColor("#eef1fb"))):
            pen = QPen(col, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(shoulder)
            path.lineTo(elbow)
            path.lineTo(hand)
            p.drawPath(path)
        hg = QRadialGradient(hand + QPointF(-1.5, -1.5), 8)
        hg.setColorAt(0, QColor("#ffffff"))
        hg.setColorAt(1, QColor("#cfd6ee"))
        p.setPen(QPen(QColor(150, 162, 214, 170), 1))
        p.setBrush(QBrush(hg))
        p.drawEllipse(hand, 6.2, 6.2)

    # -- head and face ---------------------------------------------------------
    def _draw_head(self, p: QPainter, ex: str, t: float, now: float, cx: float, hc: float) -> None:
        # ears
        ear = QRadialGradient(QPointF(0.35, 0.3), 0.9)
        ear.setCoordinateMode(QRadialGradient.CoordinateMode.ObjectBoundingMode)
        ear.setColorAt(0, QColor("#f4f8ff"))
        ear.setColorAt(1, QColor("#aebbe6"))
        p.setBrush(QBrush(ear))
        p.setPen(QPen(QColor(130, 145, 200, 140), 1))
        p.drawEllipse(QRectF(cx - 50, hc - 9, 14, 18))
        p.drawEllipse(QRectF(cx + 36, hc - 9, 14, 18))

        # head shell
        head_rect = QRectF(cx - 42, hc - 36, 84, 72)
        hg = QRadialGradient(QPointF(0.35, 0.2), 1.05)
        hg.setCoordinateMode(QRadialGradient.CoordinateMode.ObjectBoundingMode)
        hg.setColorAt(0, QColor("#ffffff"))
        hg.setColorAt(0.7, QColor("#e9edfa"))
        hg.setColorAt(1, QColor("#c3cbe8"))
        p.setBrush(QBrush(hg))
        p.setPen(QPen(QColor(150, 160, 210, 120), 1.2))
        p.drawRoundedRect(head_rect, 34, 34)

        # visor
        visor = QRectF(cx - 33, hc - 25, 66, 47)
        vg = QLinearGradient(0, 0, 0, 1)
        vg.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
        vg.setColorAt(0, QColor("#232a52"))
        vg.setColorAt(1, QColor("#0b0e22"))
        p.setBrush(QBrush(vg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(visor, 21, 21)
        gloss = QPainterPath()
        gloss.addRoundedRect(QRectF(visor.left() + 5, visor.top() + 3, visor.width() - 10, 9), 5, 5)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawPath(gloss)

        self._draw_face(p, ex, t, now, cx, hc)
        self._draw_effects(p, ex, t, cx, hc)

    def _draw_face(self, p: QPainter, ex: str, t: float, now: float, cx: float, hc: float) -> None:
        col = QColor(self.TINT.get(ex, P.CYAN))
        cy = hc - 1
        if now >= self._next_blink:
            self._blink_until = now + 0.14
            self._next_blink = now + random.uniform(2.5, 5.5)
        blink = now < self._blink_until
        speaking = self.state == "SPEAKING"

        # where the eyes look
        lx, ly = 0.0, 0.0
        if ex == "THINKING":
            lx, ly = math.sin(t * 1.7) * 5, -3.5            # glancing up and around
        elif ex == "CONFUSED":
            lx = math.sin(t * 2.4) * 5                      # looking about for an answer
        elif ex == "ASK":
            ly = 1.0
        elif ex == "SCAN":
            lx = math.sin(t * 2.4) * 7                      # sweeping the horizon

        def glow(x, y, rx, ry):
            for r, a in ((1.9, 26), (1.4, 52)):
                gc = QColor(col)
                gc.setAlpha(a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(gc)
                p.drawEllipse(QPointF(x, y), rx * r, ry * r)

        def eye_rect(x, y, w, h):
            glow(x, y, w / 2, h / 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x - w / 2, y - h / 2, w, h), w / 2, w / 2)

        def eye_arc(x, y, up=True):
            p.setPen(QPen(col, 3.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(x - 6.5, y + (4 if up else -3))
            path.quadTo(x, y + (-8 if up else 8), x + 6.5, y + (4 if up else -3))
            p.drawPath(path)

        def eye_line(x, y, alpha=255):
            c = QColor(col)
            c.setAlpha(alpha)
            p.setPen(QPen(c, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x - 5.5, y + 1), QPointF(x + 5.5, y + 1))

        def brow(x, y, angle=0.0, length=6.5, alpha=215):
            c = QColor(col)
            c.setAlpha(alpha)
            p.save()
            p.translate(x, y)
            p.rotate(angle)
            p.setPen(QPen(c, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(-length, 0), QPointF(length, 0))
            p.restore()

        def spiral(x, y, r, phase):
            path = QPainterPath()
            for i in range(37):
                a, rr = phase + i * 0.55, r * i / 36
                pt = QPointF(x + math.cos(a) * rr, y + math.sin(a) * rr)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        xl, xr = cx - 14 + lx, cx + 14 + lx
        by = cy - 15                                        # brow baseline
        no_blink = ("DIZZY", "COOL", "LOVE", "ANGRY", "SURPRISED", "LAUGH", "WINK", "YAWN")

        if ex == "SLEEPING":
            eye_line(xl, cy, 120)
            eye_line(xr, cy, 120)
        elif blink and ex not in no_blink:
            eye_line(xl, cy)
            eye_line(xr, cy)
        elif ex in ("HAPPY", "WAVE", "DANCE"):
            eye_arc(xl, cy)
            eye_arc(xr, cy)
            brow(xl, by - 3, -8, 5)
            brow(xr, by - 3, 8, 5)
        elif ex == "LISTENING":
            eye_rect(xl, cy + ly, 10, 18)                   # wide open, attentive
            eye_rect(xr, cy + ly, 10, 18)
            brow(xl, by - 3, -6, 5.5)
            brow(xr, by - 3, 6, 5.5)
        elif ex == "THINKING":
            eye_rect(xl, cy + ly, 9, 14)
            eye_rect(xr, cy + ly, 9, 14)
            brow(xl, by + 1, 6, 5.5)                        # one lowered, one raised
            brow(xr, by - 5, -10, 5.5)
        elif ex == "CONFUSED":
            glow(xl, cy, 6.5, 6.5)                          # one big round eye, one small
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(xl, cy), 6.3, 6.3)
            p.setBrush(QColor("#0b0e22"))
            p.drawEllipse(QPointF(xl + lx * 0.2, cy), 2.3, 2.3)
            eye_rect(xr, cy + 2, 8, 8)
            brow(xl, by - 5, -16, 5.5)                      # worried, uneven
            brow(xr, by + 1, 14, 5.5)
        elif ex == "ASK":
            eye_rect(xl, cy + ly, 9, 16)
            eye_rect(xr, cy + ly, 9, 16)
            brow(xl, by, 0, 5.5)
            brow(xr, by - 6, -14, 5.5)                      # one brow up: "hmm?"
        elif ex == "MUTED":
            eye_rect(xl, cy + 1, 9, 7)
            eye_rect(xr, cy + 1, 9, 7)
            brow(xl, by + 1, 8, 5)
            brow(xr, by + 1, -8, 5)
        elif ex == "ANGRY":                                 # narrowed eyes under slammed-down brows
            eye_rect(xl, cy + 2, 11, 8)
            eye_rect(xr, cy + 2, 11, 8)
            brow(xl, by + 2, 24, 7.5, 255)
            brow(xr, by + 2, -24, 7.5, 255)
        elif ex == "DIZZY":
            spiral(xl, cy, 7.5, t * 7)
            spiral(xr, cy, 7.5, -t * 7)
        elif ex == "LAUGH":
            eye_arc(xl, cy)
            eye_arc(xr, cy)
            brow(xl, by - 4, -10, 5)
            brow(xr, by - 4, 10, 5)
        elif ex == "SURPRISED":
            eye_rect(xl, cy - 1, 13, 22)
            eye_rect(xr, cy - 1, 13, 22)
            brow(xl, by - 8, -8, 5.5)
            brow(xr, by - 8, 8, 5.5)
        elif ex == "LOVE":
            beat = 1 + 0.14 * math.sin(t * 9)
            glow(xl, cy, 7, 7)
            glow(xr, cy, 7, 7)
            _draw_heart(p, xl, cy, 8.5 * beat, col)
            _draw_heart(p, xr, cy, 8.5 * beat, col)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 122, 168, 95))
            p.drawEllipse(QPointF(xl - 6, cy + 12), 5, 2.8)
            p.drawEllipse(QPointF(xr + 6, cy + 12), 5, 2.8)
        elif ex == "SILLY":                                 # cross-eyed, brows at odds
            eye_rect(xl + 3.5, cy, 10, 13)
            eye_rect(xr - 3.5, cy, 10, 13)
            brow(xl, by - 4, -12, 5.5)
            brow(xr, by - 1, 12, 5.5)
        elif ex == "WINK":
            eye_rect(xl, cy, 9, 15)
            eye_arc(xr, cy)
            brow(xl, by - 3, -6, 5)
            brow(xr, by - 5, 8, 5)
        elif ex == "YAWN":                                  # eyes screwed shut
            eye_line(xl, cy + 1)
            eye_line(xr, cy + 1)
            brow(xl, by + 2, 10, 5)
            brow(xr, by + 2, -10, 5)
        elif ex == "COOL":                                  # sunglasses
            for x in (xl, xr):
                glow(x, cy, 9, 6)
                p.setPen(QPen(col, 2.0))
                p.setBrush(QColor("#02030c"))
                p.drawRoundedRect(QRectF(x - 9.5, cy - 6.5, 19, 13), 5, 5)
                p.setPen(QPen(QColor(255, 255, 255, 110), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x - 5, cy - 2), QPointF(x - 1, cy - 4.5))
            p.setPen(QPen(col, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(xl + 9.5, cy - 3), QPointF(xr - 9.5, cy - 3))
        elif ex == "SCAN":
            eye_rect(xl, cy, 10, 17)
            eye_rect(xr, cy, 10, 17)
            brow(xl, by - 4, -8, 5.5)
            brow(xr, by - 4, 8, 5.5)
        else:
            eye_rect(xl, cy, 9, 15)
            eye_rect(xr, cy, 9, 15)

        # mouth
        my = cy + 15
        mc = QColor(col)
        if speaking and ex not in ("SLEEPING",):
            o = self._mouth()
            mc.setAlpha(225)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mc)
            p.drawEllipse(QPointF(cx, my - 1), 3.2 + 5 * o, 1.4 + 4.6 * o)
        elif ex == "ANGRY":                                 # teeth gritted
            p.setPen(QPen(mc, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cx - 8, my - 3, 16, 7), 2.5, 2.5)
            for dx in (-4, 0, 4):
                p.drawLine(QPointF(cx + dx, my - 3), QPointF(cx + dx, my + 4))
        elif ex == "LAUGH":                                 # wide open, flapping
            h = 8 + 8 * abs(math.sin(t * 11))
            mc.setAlpha(230)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mc)
            p.drawChord(QRectF(cx - 10, my - 7, 20, h), 180 * 16, 180 * 16)
        elif ex == "SURPRISED":
            p.setPen(QPen(mc, 2.2))
            fill = QColor(mc)
            fill.setAlpha(80)
            p.setBrush(fill)
            p.drawEllipse(QPointF(cx, my + 1), 3.8, 5.0)
        elif ex == "YAWN":                                  # a huge yawn that opens and closes
            o = math.sin(math.pi * self._prog)
            mc.setAlpha(215)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(mc)
            p.drawEllipse(QPointF(cx, my + 1), 3.5 + 3.5 * o, 2 + 8 * o)
        elif ex == "SILLY":                                 # grin with the tongue out
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 8, my - 2)
            path.quadTo(cx, my + 6, cx + 8, my - 2)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ff7aa8"))
            p.drawEllipse(QPointF(cx + 3, my + 5 + math.sin(t * 10) * 1.5), 3.4, 4.4)
        elif ex == "COOL":                                  # a smirk
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 6, my)
            path.quadTo(cx + 2, my + 4, cx + 8, my - 3)
            p.drawPath(path)
        elif ex == "DIZZY":                                 # a queasy wobble
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 8, my)
            for i in range(4):
                path.quadTo(cx - 8 + i * 4 + 2, my + (3.5 if (i + int(t * 6)) % 2 == 0 else -3.5),
                            cx - 8 + (i + 1) * 4, my)
            p.drawPath(path)
        elif ex in ("HAPPY", "WAVE", "LISTENING", "LOVE", "WINK", "DANCE"):
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            w = 7 if ex != "LISTENING" else 4.5
            path.moveTo(cx - w, my - 2)
            path.quadTo(cx, my + (6 if ex != "LISTENING" else 3), cx + w, my - 2)
            p.drawPath(path)
        elif ex == "CONFUSED":
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(cx - 8, my)
            for i in range(4):                              # a wobbly line
                path.quadTo(cx - 8 + i * 4 + 2, my + (3 if i % 2 == 0 else -3), cx - 8 + (i + 1) * 4, my)
            p.drawPath(path)
        elif ex == "ASK":
            p.setPen(QPen(mc, 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx + 1, my), 2.6, 2.9)
        elif ex == "THINKING":
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - 3, my + 1), QPointF(cx + 5, my - 1))
        elif ex in ("MUTED", "SCAN"):
            p.setPen(QPen(mc, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - 4, my), QPointF(cx + 4, my))

    def _draw_effects(self, p: QPainter, ex: str, t: float, cx: float, hc: float) -> None:
        """Little symbols around the head that say what it is feeling."""
        col = QColor(self.TINT.get(ex, P.CYAN))
        if ex in ("CONFUSED", "ASK"):
            wob = math.sin(t * 4) * 3
            y = hc - 46 - abs(math.sin(t * 2.4)) * 4
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 179, 71, 60))
            p.drawEllipse(QPointF(cx + 30, y + 2), 12, 12)
            p.setPen(QColor(P.AMBER).darker(112))
            p.setFont(_font(17, True))
            p.save()
            p.translate(cx + 30, y)
            p.rotate(wob)
            p.drawText(QRectF(-12, -12, 24, 24), Qt.AlignmentFlag.AlignCenter, "?")
            p.restore()
            if ex == "CONFUSED":                            # a nervous sweat drop
                d = QPainterPath()
                sx, sy = cx - 40, hc - 14 + (math.sin(t * 3) + 1) * 2
                d.moveTo(sx, sy - 6)
                d.quadTo(sx + 5, sy + 2, sx, sy + 5)
                d.quadTo(sx - 5, sy + 2, sx, sy - 6)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(120, 205, 255, 220))
                p.drawPath(d)
        elif ex == "THINKING":                              # a thought trail rising up and to the right
            for i, (dx, dy, r) in enumerate(((22, -42, 2.2), (30, -50, 3.4), (40, -60, 5.0))):
                a = 0.5 + 0.5 * math.sin(t * 4.5 - i * 0.9)
                c = QColor(col)
                c.setAlpha(int(70 + 170 * a))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawEllipse(QPointF(cx + dx, hc + dy), r, r)
        elif ex == "LISTENING":                             # sound arriving at the ear
            for i, r in enumerate((9, 15, 21)):
                a = 0.5 + 0.5 * math.sin(t * 5 - i * 0.8)
                c = QColor(col)
                c.setAlpha(int(40 + 150 * a))
                p.setPen(QPen(c, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(cx + 53 - r, hc - 4 - r, 2 * r, 2 * r), int(-55 * 16), int(110 * 16))
        elif ex in ("HAPPY", "WAVE"):
            for k, (dx, dy) in enumerate(((-47, -34), (49, -30), (-52, -4))):
                s = 3.0 + 3.0 * (0.5 + 0.5 * math.sin(t * 6 + k * 2))
                c = QColor(P.CYAN)
                c.setAlpha(210)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                star = QPainterPath()
                x0, y0 = cx + dx, hc + dy
                star.moveTo(x0, y0 - s)
                star.quadTo(x0, y0, x0 + s, y0)
                star.quadTo(x0, y0, x0, y0 + s)
                star.quadTo(x0, y0, x0 - s, y0)
                star.quadTo(x0, y0, x0, y0 - s)
                p.drawPath(star)
        elif ex == "ANGRY":
            for side in (-1, 1):                            # steam from both ears
                for i in range(3):
                    ph = (t * 1.7 + i / 3 + (0.17 if side > 0 else 0)) % 1.0
                    x = cx + side * (46 + ph * 9 + math.sin(ph * 9 + i) * 2)
                    y = hc - 12 - ph * 34
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 140, 160, int(200 * (1 - ph))))
                    p.drawEllipse(QPointF(x, y), 3 + ph * 6, 3 + ph * 6)
            k = 1 + 0.2 * math.sin(t * 14)                  # the throbbing anger mark
            x0, y0, r = cx + 31, hc - 42, 8 * k
            p.setPen(QPen(QColor(P.RED), 2.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                p.drawLine(QPointF(x0 + dx * 2.6, y0 + dy * 2.6), QPointF(x0 + dx * r, y0 + dy * r))
        elif ex == "DIZZY":                                 # stars circling the head
            for i in range(3):
                a = t * 4 + i * 2.094
                _draw_sparkle(p, cx + math.cos(a) * 27, hc - 42 + math.sin(a) * 6,
                              4.5 + 1.5 * math.sin(a), QColor(P.AMBER))
        elif ex == "LAUGH":
            for side in (-1, 1):                            # tears of laughter
                ph = (t * 3.2 + (0.5 if side > 0 else 0)) % 1.0
                x = cx + side * (21 + ph * 22)
                y = hc + 2 - ph * 10 + ph * ph * 26
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(120, 205, 255, int(230 * (1 - ph * 0.7))))
                p.drawEllipse(QPointF(x, y), 2.7, 3.2)
            p.setPen(QColor(P.AMBER).darker(115))
            p.setFont(_font(11, True))
            p.save()
            p.translate(cx + 34, hc - 42)
            p.rotate(math.sin(t * 11) * 8)
            p.drawText(QRectF(-24, -10, 48, 20), Qt.AlignmentFlag.AlignCenter,
                       "HA HA" if int(t * 5) % 2 else "HA!")
            p.restore()
        elif ex == "SURPRISED":
            pop = min(1.0, 0.45 + self._prog * 5)
            p.setPen(QColor(P.AMBER).darker(112))
            p.setFont(_font(int(24 * pop), True))
            p.drawText(QRectF(cx + 14, hc - 66, 34, 34), Qt.AlignmentFlag.AlignCenter, "!")
        elif ex == "LOVE":
            for i in range(4):                              # hearts drifting up
                ph = (t * 0.75 + i / 4) % 1.0
                x = cx + math.sin(ph * 6 + i * 1.7) * 20 + (i - 1.5) * 13
                _draw_heart(p, x, hc - 34 - ph * 34, 3.2 + 2.4 * (1 - abs(ph - 0.4)),
                            QColor(255, 122, 168, int(240 * (1 - ph))))
        elif ex == "DANCE":
            for i in range(3):                              # notes floating off
                ph = (t * 0.9 + i / 3) % 1.0
                side = -1 if i % 2 else 1
                c = QColor(col)
                c.setAlpha(int(255 * (1 - ph)))
                p.setPen(c)
                p.setFont(_font(14, True))
                p.drawText(QPointF(cx + side * (46 + math.sin(ph * 7) * 6), hc - 4 - ph * 42),
                           "♪" if i % 2 == 0 else "♫")
        elif ex == "COOL":                                  # a glint sliding across the lenses
            ph = (t * 0.9) % 1.7
            if ph < 1.0:
                _draw_sparkle(p, cx - 26 + ph * 52, hc - 5, 5.5 * math.sin(math.pi * ph) + 0.3,
                              QColor(255, 255, 255, 235))
        elif ex == "WINK":
            _draw_sparkle(p, cx + 24, hc - 12, 4.5 + 2 * math.sin(t * 14), QColor(P.CYAN))
        elif ex == "SLEEPING":
            p.setPen(QColor(P.DIM))
            for i, size in enumerate((9, 12, 15)):
                p.setFont(_font(size, True))
                p.drawText(QPointF(cx + 30 + i * 8, hc - 28 - i * 11 - (t * 6 % 6)), "z")


_NOT_SURE = (
    "i don't know", "i do not know", "i'm not sure", "i am not sure", "not sure", "no idea",
    "i couldn't", "i could not", "i can't", "i cannot", "i wasn't able", "unable to", "i'm unable",
    "didn't catch", "did not catch", "didn't understand", "don't understand", "couldn't find",
    "could not find", "don't have access", "not able to", "no results", "that failed", "didn't work",
)
_CLARIFY = ("could you clarify", "can you clarify", "do you mean", "which one", "what do you mean",
            "can you tell me more", "could you tell me", "which do you", "did you mean")
_GREET = ("hello", "hi ", "hi,", "hi!", "hey", "good morning", "good afternoon", "good evening", "welcome")
_LAUGH = ("haha", "hehe", "lol", "ha ha", "that's funny", "joke", "hilarious")
_PLEASED = ("you're welcome", "you are welcome", "my pleasure", "happy to help", "glad to", "all done",
            "all set", "done!", "here you go", "there you go", "of course", "sure thing")


def mood_for_reply(text: str) -> tuple[str, float] | None:
    """Pick the expression that fits what the assistant just said.

    Order matters: admitting it does not know beats asking a question, which beats
    a greeting, which beats looking pleased."""
    low = " ".join(text.lower().split())
    if not low:
        return None
    if any(k in low for k in _NOT_SURE):
        return "CONFUSED", 4.5
    if any(k in low for k in _LAUGH):
        return "LAUGH", 3.2
    if any(k in low for k in _CLARIFY):
        return "ASK", 4.5
    if low.startswith(_GREET):                       # "Hello! How can I help?" is a wave, not a puzzle
        return "WAVE", 3.0
    if low.rstrip().endswith("?"):
        return "ASK", 4.5
    if any(k in low for k in _PLEASED):
        return "HAPPY", 3.0
    return None


class SpeechBubble(QWidget):
    """A speech bubble that sits beside the robot and fades away on its own."""

    clicked = pyqtSignal()
    _MAXW = 290
    _MAX_CHARS = 280

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text = ""
        self._kind = "answer"
        self._tail_right = True
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)

    def say(self, text: str, near: QWidget, ms: int = 0, kind: str = "answer") -> None:
        """kind: 'answer' (white, what the assistant says) or 'you' (dark, what was heard).
        Stays up long enough to read, in proportion to its length."""
        text = " ".join(text.split())
        if not text:
            return
        self._kind = kind
        self._text = text if len(text) <= self._MAX_CHARS else text[:self._MAX_CHARS - 1].rstrip() + "… (click for more)"
        if not ms:
            ms = int(max(4500, min(22000, 3200 + 60 * len(self._text))))
        fm = QFontMetrics(_font(10))
        r = fm.boundingRect(0, 0, self._MAXW - 28, 400,
                            int(Qt.TextFlag.TextWordWrap), self._text)
        w, h = r.width() + 28, r.height() + 24
        self.setFixedSize(w + 8, h + 18)
        scr = _screen_rect()
        # to the left of the robot, tail pointing at it — or to the right near the edge
        x = near.x() - w + 14
        self._tail_right = True
        if scr and x < scr.left() + 4:
            x = near.x() + near.width() - 14
            self._tail_right = False
        y = near.y() - h + 22
        if scr:
            y = max(scr.top() + 4, y)
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()
        self._hide.start(ms)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width() - 8, self.height() - 18
        body = QRectF(4, 2, w, h)
        for i in range(5, 0, -1):                                   # soft shadow
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(70, 80, 150, 9))
            p.drawRoundedRect(body.adjusted(-i, -i + 3, i, i + 3), 16 + i, 16 + i)
        you = self._kind == "you"
        fill = QColor(P.INK) if you else QColor(P.SURFACE)
        p.setBrush(fill)
        p.setPen(QPen(QColor(P.INK if you else P.LINE), 1))
        p.drawRoundedRect(body, 16, 16)
        tail = QPainterPath()
        tx = body.right() - 26 if self._tail_right else body.left() + 26
        tail.moveTo(tx - 8, body.bottom() - 1)
        tail.lineTo(tx + (6 if self._tail_right else -6), body.bottom() + 12)
        tail.lineTo(tx + 8, body.bottom() - 1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPath(tail)
        p.setPen(QColor(P.ON_INK) if you else QColor(P.INK))
        p.setFont(_font(10))
        p.drawText(body.adjusted(14, 10, -14, -10),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), self._text)
        p.end()

    def mousePressEvent(self, e):
        self.hide()
        self.clicked.emit()


# ══════════════════════════════════════════════════════════════════════════════
#  Chat widgets
# ══════════════════════════════════════════════════════════════════════════════
def _icon_btn(text: str, tip: str = "", size: int = 40, dark: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setFixedSize(size, size)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(tip)
    b.setFont(_font(13))
    if dark:
        b.setStyleSheet(f"QPushButton{{background:{P.INK};color:{P.ON_INK};border:none;border-radius:{size // 2}px;}}"
                        f"QPushButton:hover{{background:{P.INK2};}}")
    else:
        b.setStyleSheet(f"QPushButton{{background:{P.SURFACE};color:{P.INK2};border:1px solid {P.LINE};"
                        f"border-radius:{size // 2}px;}}"
                        f"QPushButton:hover{{background:{P.SOFT};color:{P.INK};}}"
                        f"QPushButton:checked{{background:{P.INK};color:{P.ON_INK};}}")
    return b


class _Avatar(QWidget):
    """The small round robot-face badge beside assistant messages."""

    def __init__(self, size: int = 28):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P.INK))
        p.drawEllipse(0, 0, s, s)
        p.setBrush(QColor(P.CYAN))
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.38, s * 0.14, s * 0.26), 2, 2)
        p.drawRoundedRect(QRectF(s * 0.60, s * 0.38, s * 0.14, s * 0.26), 2, 2)
        p.end()


class MessageRow(QWidget):
    """One chat bubble. role: 'user' | 'assistant'."""

    def __init__(self, role: str, text: str, ts: str = ""):
        super().__init__()
        self.role = role
        self._lbl = QLabel(text)
        self._lbl.setWordWrap(True)
        self._lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lbl.setFont(_font(10))
        self._lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        user = role == "user"
        self._lbl.setStyleSheet(
            f"QLabel{{background:{P.INK if user else P.SURFACE};color:{P.ON_INK if user else P.INK};"
            f"border:{'none' if user else '1px solid ' + P.LINE};"
            f"border-radius:18px;padding:11px 16px;}}")

        stamp = QLabel(ts or datetime.now().strftime("%I:%M %p").lstrip("0"))
        stamp.setFont(_font(7))
        stamp.setStyleSheet(f"color:{P.DIM};background:transparent;")
        col = QVBoxLayout()
        col.setSpacing(3)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._lbl)
        col.addWidget(stamp, alignment=Qt.AlignmentFlag.AlignRight if user else Qt.AlignmentFlag.AlignLeft)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        if user:
            row.addStretch(1)
            row.addLayout(col)
        else:
            row.addWidget(_Avatar(), alignment=Qt.AlignmentFlag.AlignTop)
            row.addLayout(col)
            row.addStretch(1)

    def set_max_width(self, w: int) -> None:
        self._lbl.setMaximumWidth(max(200, w))


class SystemNote(QLabel):
    """A quiet centred line for SYS / ERR / FILE messages."""

    def __init__(self, text: str, kind: str = "sys"):
        super().__init__(text)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(_font(8))
        color = {"err": P.RED, "file": P.GREEN}.get(kind, P.DIM)
        bg = {"err": P.ERR_BG, "file": P.OK_BG}.get(kind, P.SOFT)
        self.setStyleSheet(f"QLabel{{color:{color};background:{bg};border-radius:11px;padding:4px 12px;}}")
        # A word-wrapped QLabel sizes itself badly (it can report one line of height for two lines of text),
        # so give it the width the text needs, capped, and let the height follow from that.
        w = min(QFontMetrics(self.font()).horizontalAdvance(text) + 44, 560)
        self.setFixedWidth(max(w, 60))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)


class ImageCard(QFrame):
    """A generated picture in the conversation, with ways to use it."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.setObjectName("imgcard")
        self.setStyleSheet(f"QFrame#imgcard{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:16px;}}")
        self.setFixedWidth(396)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        pic = QLabel()
        pm = QPixmap(path)
        if not pm.isNull():
            pic.setPixmap(pm.scaled(372, 372, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            pic.setText("(the picture could not be shown)")
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic.setStyleSheet("background:transparent;")
        lay.addWidget(pic)
        cap = QLabel(f"Saved: Pictures / JARVIS Images / {Path(path).name}")
        cap.setFont(_font(8))
        cap.setWordWrap(True)
        cap.setStyleSheet(f"color:{P.DIM};background:transparent;")
        lay.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, slot in (("Open", self._open), ("Save as…", self._save_as), ("Show in folder", self._show)):
            b = QPushButton(label)
            b.setFixedHeight(30)
            b.setFont(_font(9, True))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton{{background:{P.SOFT};color:{P.INK};border:none;border-radius:15px;padding:0 12px;}}"
                            f"QPushButton:hover{{background:{P.INK};color:{P.ON_INK};}}")
            b.clicked.connect(lambda _=False, f=slot: f())
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

    def _open(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def _show(self) -> None:
        if _OS == "Windows":
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.path)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.path).parent)))

    def _save_as(self) -> None:
        name = Path(self.path).name
        dest, _ = QFileDialog.getSaveFileName(self, "Save picture", str(Path.home() / "Pictures" / name),
                                              "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not dest:
            return
        try:
            if dest.lower().endswith((".jpg", ".jpeg")):
                from PIL import Image
                Image.open(self.path).convert("RGB").save(dest, "JPEG", quality=95)
            else:
                import shutil
                shutil.copyfile(self.path, dest)
        except Exception as e:
            print(f"[Image] could not save a copy: {e}")


class ContentCard(QFrame):
    """A titled block of output — search results, news, a document review."""

    def __init__(self, title: str, text: str):
        super().__init__()
        self.setStyleSheet(f"ContentCard{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:16px;}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        t = QLabel(title)
        t.setFont(_font(9, True))
        t.setStyleSheet(f"color:{P.BLUE};background:transparent;")
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setFont(_font(9))
        body.setStyleSheet(f"color:{P.INK2};background:transparent;")
        lay.addWidget(t)
        lay.addWidget(body)
        self._body = body

    def set_max_width(self, w: int) -> None:
        self.setMaximumWidth(max(260, w + 40))


class TypingRow(QWidget):
    """Three animated dots while the assistant is thinking."""

    def __init__(self):
        super().__init__()
        self._n = 0
        self._dots = QLabel("•")
        self._dots.setFont(_font(14, True))
        self._dots.setStyleSheet(f"QLabel{{background:{P.SURFACE};color:{P.BLUE};border:1px solid {P.LINE};"
                                 f"border-radius:18px;padding:4px 16px;}}")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(_Avatar(), alignment=Qt.AlignmentFlag.AlignTop)
        row.addWidget(self._dots)
        row.addStretch(1)
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(380)

    def _step(self):
        self._n = (self._n + 1) % 3
        self._dots.setText("•" + " •" * self._n)

    def stop(self) -> None:
        self._tmr.stop()


class ChatView(QScrollArea):
    """The scrolling conversation."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            f"QScrollBar::handle:vertical{{background:{P.LINE};border-radius:4px;min-height:30px;}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(26, 14, 26, 14)
        self._lay.setSpacing(12)
        self._lay.addStretch(1)
        self.setWidget(inner)
        self._typing: TypingRow | None = None
        self._items: list[QWidget] = []

    def _max_w(self) -> int:
        return int(self.viewport().width() * 0.66)

    def show_welcome(self, name: str, temporary: bool = False) -> None:
        self._welcome = WelcomeCard(name, temporary)
        self._lay.insertWidget(self._lay.count() - 1, self._welcome)

    def _drop_welcome(self) -> None:
        w = getattr(self, "_welcome", None)
        if w is not None:
            self._welcome = None
            self._lay.removeWidget(w)
            w.hide()
            w.setParent(None)
            w.deleteLater()

    def add(self, w: QWidget) -> None:
        self.hide_typing()
        self._drop_welcome()
        if hasattr(w, "set_max_width"):
            w.set_max_width(self._max_w())
        if isinstance(w, SystemNote):
            self._lay.insertWidget(self._lay.count() - 1, w, alignment=Qt.AlignmentFlag.AlignHCenter)
        else:
            self._lay.insertWidget(self._lay.count() - 1, w)
        self._items.append(w)
        if len(self._items) > 400:                         # keep the widget tree bounded
            old = self._items.pop(0)
            old.deleteLater()
        QTimer.singleShot(30, self.to_bottom)

    def clear(self) -> None:
        self.hide_typing()
        self._drop_welcome()
        for w in self._items:
            w.deleteLater()
        self._items.clear()

    def show_typing(self) -> None:
        if self._typing is None:
            self._typing = TypingRow()
            self._lay.insertWidget(self._lay.count() - 1, self._typing)
            QTimer.singleShot(30, self.to_bottom)

    def hide_typing(self) -> None:
        t, self._typing = self._typing, None
        if t is not None:
            t.stop()
            self._lay.removeWidget(t)       # out of the layout now, not "later"
            t.hide()
            t.setParent(None)
            t.deleteLater()

    def to_bottom(self) -> None:
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for w in self._items:
            if hasattr(w, "set_max_width"):
                w.set_max_width(self._max_w())


_DOC_KINDS = {
    "pdf": ("📕", ["Summarize", "Key points", "Extract text"]),
    "doc": ("📘", ["Summarize", "Key points", "Fix grammar"]),
    "docx": ("📘", ["Summarize", "Key points", "Fix grammar"]),
    "txt": ("📄", ["Summarize", "Key points", "Fix grammar"]),
    "md": ("📄", ["Summarize", "Key points", "Fix grammar"]),
    "csv": ("📗", ["Analyze", "Statistics", "Summarize"]),
    "xlsx": ("📗", ["Analyze", "Statistics", "Summarize"]),
    "xls": ("📗", ["Analyze", "Statistics", "Summarize"]),
    "pptx": ("📙", ["Summarize", "Key points", "Extract text"]),
    "json": ("🧩", ["Explain", "Validate", "Analyze"]),
    "zip": ("🗜", ["List contents", "Extract", "Info"]),
}
for _e in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
    _DOC_KINDS[_e] = ("🖼", ["Describe", "Read the text", "Info"])
for _e in ("py", "js", "ts", "html", "css", "java", "c", "cpp", "cs", "go", "rs", "sql", "sh"):
    _DOC_KINDS[_e] = ("💻", ["Explain", "Review", "Find bugs"])
for _e in ("mp3", "wav", "m4a", "ogg", "flac"):
    _DOC_KINDS[_e] = ("🎵", ["Transcribe", "Info", "Summarize"])
for _e in ("mp4", "mov", "mkv", "avi", "webm"):
    _DOC_KINDS[_e] = ("🎬", ["Transcribe", "Info", "Extract audio"])

_DOC_PHRASES = {
    "Summarize": "Summarize the file I attached.",
    "Key points": "Give me the key points of the file I attached.",
    "Extract text": "Extract the text from the file I attached.",
    "Fix grammar": "Fix the grammar and spelling in the file I attached.",
    "Analyze": "Analyze the data in the file I attached.",
    "Statistics": "Give me the statistics of the file I attached.",
    "Explain": "Explain the file I attached.",
    "Validate": "Validate the file I attached.",
    "List contents": "List what is inside the archive I attached.",
    "Extract": "Extract the archive I attached.",
    "Info": "Give me the details of the file I attached.",
    "Describe": "Describe the image I attached.",
    "Read the text": "Read the text in the image I attached.",
    "Review": "Review the code in the file I attached.",
    "Find bugs": "Find bugs in the code in the file I attached.",
    "Transcribe": "Transcribe the file I attached.",
    "Extract audio": "Extract the audio from the file I attached.",
}


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


class DocPanel(QFrame):
    """Upload area in the side panel: drop or click to add a document; once one is
    attached it shows what it is and offers the obvious things to do with it."""

    browse = pyqtSignal()
    cleared = pyqtSignal()
    command = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("doc")
        self.setStyleSheet("QFrame#doc{background:transparent;}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        lay.addWidget(self._stack)

        empty = QPushButton("⬆   Upload a document\nPDF · Word · Excel · image · code")
        empty.setFixedHeight(62)
        empty.setFont(_font(9))
        empty.setCursor(Qt.CursorShape.PointingHandCursor)
        empty.setToolTip("Click to choose a file, or drop one anywhere on this window")
        empty.setStyleSheet(
            f"QPushButton{{background:{P.GLASS};color:{P.INK2};border:1.5px dashed {P.BLUE};"
            f"border-radius:16px;}}QPushButton:hover{{background:{P.SURFACE};color:{P.INK};}}")
        empty.clicked.connect(self.browse.emit)
        self._stack.addWidget(empty)

        full = QFrame()
        full.setObjectName("full")
        full.setStyleSheet(f"QFrame#full{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:16px;}}")
        fl = QVBoxLayout(full)
        fl.setContentsMargins(12, 10, 10, 10)
        fl.setSpacing(8)
        top = QHBoxLayout()
        self._icon = QLabel("📄")
        self._icon.setFont(_font(16))
        self._icon.setStyleSheet("background:transparent;")
        names = QVBoxLayout()
        names.setSpacing(0)
        self._name = QLabel("")
        self._name.setFont(_font(9, True))
        self._name.setStyleSheet(f"color:{P.INK};background:transparent;")
        self._meta = QLabel("")
        self._meta.setFont(_font(8))
        self._meta.setStyleSheet(f"color:{P.DIM};background:transparent;")
        names.addWidget(self._name)
        names.addWidget(self._meta)
        x = QPushButton("✕")
        x.setFixedSize(24, 24)
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.setToolTip("Remove")
        x.setStyleSheet(f"QPushButton{{color:{P.DIM};background:{P.SOFT};border:none;border-radius:12px;}}"
                        f"QPushButton:hover{{color:white;background:{P.RED};}}")
        x.clicked.connect(self.cleared.emit)
        top.addWidget(self._icon)
        top.addLayout(names, 1)
        top.addWidget(x)
        fl.addLayout(top)
        self._btn_row = QHBoxLayout()
        self._btn_row.setSpacing(6)
        fl.addLayout(self._btn_row)
        self._stack.addWidget(full)

    def set_file(self, path: str | None) -> None:
        if not path:
            self._stack.setCurrentIndex(0)
            return
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        icon, verbs = _DOC_KINDS.get(ext, ("📄", ["Summarize", "Describe", "Info"]))
        try:
            size = _human_size(p.stat().st_size)
        except OSError:
            size = ""
        self._icon.setText(icon)
        self._name.setText(QFontMetrics(self._name.font()).elidedText(p.name, Qt.TextElideMode.ElideMiddle, 150))
        self._name.setToolTip(str(p))
        self._meta.setText(f"{ext.upper() or 'FILE'}  ·  {size}".strip(" ·"))
        while self._btn_row.count():
            it = self._btn_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for v in verbs:
            b = QPushButton(v)
            b.setFixedHeight(26)
            b.setFont(_font(8))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton{{background:{P.SOFT};color:{P.INK};border:none;border-radius:13px;padding:0 8px;}}"
                            f"QPushButton:hover{{background:{P.INK};color:{P.ON_INK};}}")
            b.clicked.connect(lambda _=False, verb=v: self.command.emit(_DOC_PHRASES.get(verb, verb)))
            self._btn_row.addWidget(b)
        self._stack.setCurrentIndex(1)


class WelcomeCard(QWidget):
    """Shown in an empty conversation: says what the assistant can do."""

    def __init__(self, name: str, temporary: bool = False):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 40, 0, 10)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        h = QLabel("Temporary chat" if temporary else f"Hi, I'm {name.title()}")
        h.setFont(_font(20, True))
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.setStyleSheet(f"color:{P.INK};background:transparent;")
        s = QLabel("Nothing said here is saved — not in your history, and not in my memory.\nIt disappears when you leave it."
                   if temporary else
                   "Just talk — I'm listening. Or type below.\nUpload a document and ask me about it.")
        s.setFont(_font(10))
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setStyleSheet(f"color:{P.DIM};background:transparent;")
        lay.addWidget(h)
        lay.addWidget(s)


class SetupCard(QFrame):
    """First-run (and bad-key) screen: the Gemini API key and the OS."""

    done = pyqtSignal(str, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet(f"SetupCard{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:22px;}}")
        _shadow(self, 40, 70, 12)
        self.setFixedWidth(420)
        detected = {"Darwin": "mac", "Windows": "windows"}.get(_OS, "linux")
        self._os = detected
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(10)
        h = QLabel("Let's get you set up")
        h.setFont(_font(15, True))
        h.setStyleSheet(f"color:{P.INK};background:transparent;")
        s = QLabel("Paste your Gemini API key so the assistant can think and talk.")
        s.setWordWrap(True)
        s.setFont(_font(9))
        s.setStyleSheet(f"color:{P.DIM};background:transparent;")
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("AIza…")
        self._key.setFixedHeight(40)
        self._key.setFont(_font(10))
        self._key.setStyleSheet(f"QLineEdit{{background:{P.SOFT};border:1px solid {P.LINE};border-radius:12px;"
                                f"padding:0 12px;color:{P.INK};}}QLineEdit:focus{{border:1px solid {P.BLUE};}}")
        self._key.returnPressed.connect(self._submit)
        lay.addWidget(h)
        lay.addWidget(s)
        lay.addSpacing(4)
        lay.addWidget(self._key)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._btns: dict[str, QPushButton] = {}
        for key, label in (("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")):
            b = QPushButton(label)
            b.setFixedHeight(34)
            b.setFont(_font(9))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            row.addWidget(b)
            self._btns[key] = b
        lay.addLayout(row)
        go = QPushButton("Start")
        go.setFixedHeight(42)
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.setFont(_font(10, True))
        go.setStyleSheet(f"QPushButton{{background:{P.INK};color:{P.ON_INK};border:none;border-radius:21px;}}"
                         "QPushButton:hover{background:#262a4a;}")
        go.clicked.connect(self._submit)
        lay.addSpacing(4)
        lay.addWidget(go)
        self._pick(detected)

    def _pick(self, key: str) -> None:
        self._os = key
        for k, b in self._btns.items():
            on = k == key
            b.setStyleSheet(
                f"QPushButton{{background:{P.BLUE if on else P.SURFACE};color:{'white' if on else P.INK2};"
                f"border:1px solid {P.BLUE if on else P.LINE};border-radius:17px;}}")

    def _submit(self) -> None:
        key = self._key.text().strip()
        if not key:
            self._key.setStyleSheet(self._key.styleSheet() + f"QLineEdit{{border:1px solid {P.RED};}}")
            return
        self.done.emit(key, self._os)


class ConfirmCard(QFrame):
    """The gate for actions that cannot be undone. The model cannot press these."""

    answered = pyqtSignal(bool)

    def __init__(self, parent, title: str, detail: str):
        super().__init__(parent)
        self.setStyleSheet(f"ConfirmCard{{background:{P.SURFACE};border:2px solid {P.RED};border-radius:22px;}}")
        _shadow(self, 44, 90, 12)
        self.setFixedWidth(400)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(8)
        tag = QLabel("CONFIRMATION NEEDED")
        tag.setFont(_font(8, True))
        tag.setStyleSheet(f"color:{P.RED};background:transparent;letter-spacing:1px;")
        t = QLabel(title)
        t.setFont(_font(14, True))
        t.setWordWrap(True)
        t.setStyleSheet(f"color:{P.INK};background:transparent;")
        d = QLabel(detail)
        d.setFont(_font(9))
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{P.INK2};background:transparent;")
        row = QHBoxLayout()
        row.setSpacing(10)
        no = QPushButton("Cancel")
        yes = QPushButton("Confirm")
        for b, bg, fg, bd in ((no, P.SURFACE, P.INK, P.LINE), (yes, P.RED, "white", P.RED)):
            b.setFixedHeight(40)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(_font(10, True))
            b.setStyleSheet(f"QPushButton{{background:{bg};color:{fg};border:1px solid {bd};border-radius:20px;}}"
                            "QPushButton:hover{opacity:0.9;}")
            row.addWidget(b)
        no.clicked.connect(lambda: self.answered.emit(False))
        yes.clicked.connect(lambda: self.answered.emit(True))
        lay.addWidget(tag)
        lay.addWidget(t)
        lay.addWidget(d)
        lay.addSpacing(6)
        lay.addLayout(row)


# ── panels: audio devices, memory ─────────────────────────────────────────────
def _pill(text: str, dark: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(40)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFont(_font(10, True))
    b.setStyleSheet(
        (f"QPushButton{{background:{P.INK};color:{P.ON_INK};border:none;border-radius:20px;}}"
         f"QPushButton:hover{{background:{P.INK2};}}") if dark else
        (f"QPushButton{{background:{P.SURFACE};color:{P.INK};border:1px solid {P.LINE};border-radius:20px;}}"
         f"QPushButton:hover{{background:{P.SOFT};}}"))
    return b


class _Sheet(QFrame):
    """A white rounded panel that floats over the chat — the look the rest of this
    window has, for anything that needs a small form or a list."""

    def __init__(self, parent, title: str, subtitle: str = "", width: int = 440):
        super().__init__(parent)
        self.setObjectName("sheet")
        self.setStyleSheet(f"QFrame#sheet{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:24px;}}")
        _shadow(self, 44, 70, 12)
        self.setFixedWidth(width)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(26, 22, 26, 22)
        self.body.setSpacing(8)
        t = QLabel(title)
        t.setFont(_font(15, True))
        t.setStyleSheet(f"color:{P.INK};background:transparent;")
        self.body.addWidget(t)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setFont(_font(9))
            sub.setStyleSheet(f"color:{P.DIM};background:transparent;")
            self.body.addWidget(sub)

    def caption(self, text: str) -> QLabel:
        c = QLabel(text)
        c.setFont(_font(9, True))
        c.setStyleSheet(f"color:{P.INK2};background:transparent;")
        return c

    def recentre(self) -> None:
        """Fit the panel to its content and put it back in the middle of the chat."""
        self.body.activate()
        self.adjustSize()
        p = self.parentWidget()
        if p is not None:
            self.move(max(0, (p.width() - self.width()) // 2), max(0, (p.height() - self.height()) // 2))


class AudioCard(_Sheet):
    """Which microphone JARVIS listens to and which speakers it talks through."""

    picked = pyqtSignal()      # after Apply, only when something actually changed

    def __init__(self, parent):
        super().__init__(parent, "Audio devices", "Applying reconnects the session. Your conversation is kept.")
        from core.audio_devices import DEFAULT_LABEL, list_devices
        from memory.config_manager import get_input_device, get_output_device
        combo_css = (f"QComboBox{{background:{P.SOFT};color:{P.INK};border:1px solid {P.LINE};"
                     f"border-radius:14px;padding:6px 12px;}}"
                     f"QComboBox::drop-down{{border:none;width:22px;}}"
                     f"QComboBox QAbstractItemView{{background:{P.SURFACE};color:{P.INK};border:1px solid {P.LINE};"
                     f"selection-background-color:{P.SOFT};selection-color:{P.INK};outline:0;}}")

        def row(label: str, kind: str, current: str) -> QComboBox:
            self.body.addWidget(self.caption(label))
            box = QComboBox()
            box.setFont(_font(10))
            box.setFixedHeight(38)
            box.setStyleSheet(combo_css)
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:          # saved device unplugged: show it, don't reset the choice
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            self.body.addWidget(box)
            return box

        self._in = row("Microphone", "input", get_input_device())
        self._out = row("Speakers", "output", get_output_device())
        self.body.addSpacing(6)
        btns = QHBoxLayout()
        btns.setSpacing(10)
        close, ok = _pill("Close"), _pill("Apply", dark=True)
        close.clicked.connect(self.hide)
        ok.clicked.connect(self._apply)
        btns.addWidget(close)
        btns.addWidget(ok)
        self.body.addLayout(btns)

    def _apply(self) -> None:
        from memory.config_manager import (get_input_device, get_output_device,
                                           save_input_device, save_output_device)
        new_in, new_out = self._in.currentData() or "", self._out.currentData() or ""
        changed = new_in != get_input_device() or new_out != get_output_device()
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        if changed:
            self.picked.emit()


class MemoryCard(_Sheet):
    """Everything stored about you, newest first, each with a way to forget it."""

    def __init__(self, parent):
        super().__init__(parent, "What I remember", width=520)
        self._rows_host = QVBoxLayout()
        self._rows_host.setSpacing(8)
        self.body.addLayout(self._rows_host)
        close = _pill("Close")
        close.clicked.connect(self.hide)
        self.body.addSpacing(4)
        self.body.addWidget(close)
        self._rebuild()

    def _clear(self) -> None:
        while self._rows_host.count():
            item = self._rows_host.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _rebuild(self) -> None:
        from memory.memory_manager import all_entries_for_ui
        self._clear()
        rows = all_entries_for_ui()
        cap = QLabel(f"{len(rows)} stored fact{'s' if len(rows) != 1 else ''}. Nothing here is sent "
                     "anywhere; it lives in memory/long_term.json on this computer.")
        cap.setWordWrap(True)
        cap.setFont(_font(9))
        cap.setStyleSheet(f"color:{P.DIM};background:transparent;")
        self._rows_host.addWidget(cap)
        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(_font(10))
            empty.setStyleSheet(f"color:{P.INK2};background:transparent;")
            self._rows_host.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(380, 52 * len(rows) + 8))
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                                 "QScrollArea>QWidget>QWidget{background:transparent;}")
            inner = QWidget()
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(0, 0, 6, 0)
            lay.setSpacing(6)
            for r in rows:
                card = QFrame()
                card.setStyleSheet(f"QFrame{{background:{P.SOFT};border-radius:14px;}}")
                line = QHBoxLayout(card)
                line.setContentsMargins(14, 8, 8, 8)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b><br>"
                             f"<span style='color:{P.INK2}'>{r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(_font(9))
                txt.setStyleSheet(f"color:{P.INK};background:transparent;")
                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(_font(8))
                meta.setStyleSheet(f"color:{P.DIM};background:transparent;")
                rm = QPushButton("✕")
                rm.setFixedSize(26, 26)
                rm.setToolTip("Forget this")
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setStyleSheet(f"QPushButton{{background:{P.SURFACE};color:{P.INK2};border:none;border-radius:13px;}}"
                                 f"QPushButton:hover{{background:{P.RED};color:white;}}")
                rm.clicked.connect(lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(txt, 1)
                line.addWidget(meta)
                line.addWidget(rm)
                lay.addWidget(card)
            lay.addStretch(1)
            scroll.setWidget(inner)
            self._rows_host.addWidget(scroll)
        self.recentre()
        QTimer.singleShot(0, self.recentre)        # the size hint is only final once Qt has polished the rows

    def _forget(self, category: str, key: str) -> None:
        from memory.memory_manager import forget
        forget(key, category)
        QTimer.singleShot(0, self._rebuild)        # not inside the click: it destroys the button that sent it


class LessonsCard(_Sheet):
    """What JARVIS has learned from the user: the lessons from corrections, and the routines it has noticed."""

    def __init__(self, parent):
        super().__init__(parent, "What I've learned", width=540)
        self._host = QVBoxLayout()
        self._host.setSpacing(8)
        self.body.addLayout(self._host)
        close = _pill("Close")
        close.clicked.connect(self.hide)
        self.body.addSpacing(4)
        self.body.addWidget(close)
        self._rebuild()

    def _clear(self) -> None:
        while self._host.count():
            item = self._host.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def _row(self, text: str, on_forget) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame{{background:{P.SOFT};border-radius:14px;}}")
        line = QHBoxLayout(card)
        line.setContentsMargins(14, 8, 8, 8)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setFont(_font(9))
        lab.setStyleSheet(f"color:{P.INK};background:transparent;")
        rm = QPushButton("✕")
        rm.setFixedSize(26, 26)
        rm.setToolTip("Forget this")
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.setStyleSheet(f"QPushButton{{background:{P.SURFACE};color:{P.INK2};border:none;border-radius:13px;}}"
                         f"QPushButton:hover{{background:{P.RED};color:white;}}")
        rm.clicked.connect(lambda _=False: (on_forget(), QTimer.singleShot(0, self._rebuild)))
        line.addWidget(lab, 1)
        line.addWidget(rm)
        return card

    def _caption(self, text: str, bold: bool = False) -> QLabel:
        c = QLabel(text)
        c.setWordWrap(True)
        c.setFont(_font(10 if bold else 9, bold))
        c.setStyleSheet(f"color:{P.INK if bold else P.DIM};background:transparent;")
        return c

    def _rebuild(self) -> None:
        from core import lessons, routines
        self._clear()
        items = lessons.load()
        self._host.addWidget(self._caption("Lessons from your corrections", bold=True))
        if not items:
            self._host.addWidget(self._caption("None yet. Turn on “Learn from my corrections”, then tell me when I get something wrong."))
        for it in items:
            self._host.addWidget(self._row(it["rule"], lambda r=it["rule"]: lessons.forget(r)))
        self._host.addSpacing(6)
        self._host.addWidget(self._caption("Routines I've noticed", bold=True))
        found = routines.patterns()
        if not found:
            self._host.addWidget(self._caption("None yet. A routine is the same request, around the same time, on three different days."))
        for pat in found[:8]:
            self._host.addWidget(self._row(f"You usually {routines.describe(pat)}", lambda label=pat["label"]: routines.forget(label)))
        self._host.addWidget(self._caption("Nothing here leaves this computer. It is kept in memory/lessons.json and memory/routines.json."))
        self.recentre()
        QTimer.singleShot(0, self.recentre)


# ── media dock: camera and video ──────────────────────────────────────────────
class MediaDock(QFrame):
    """A panel above the input bar that holds the live camera or a video."""

    close_requested = pyqtSignal()
    sound_toggled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"MediaDock{{background:#0e1124;border-radius:18px;}}")
        self.setFixedHeight(250)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self._title = QLabel("")
        self._title.setFont(_font(9, True))
        self._title.setStyleSheet("color:white;background:transparent;")
        self._sound = QPushButton("🔇  Sound off")
        self._sound.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sound.setStyleSheet("QPushButton{color:#c9cef0;background:rgba(255,255,255,0.08);border:none;"
                                  "border-radius:11px;padding:3px 10px;}QPushButton:hover{color:white;}")
        self._sound.clicked.connect(self.sound_toggled.emit)
        close = QPushButton("✕")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFixedSize(26, 26)
        close.setStyleSheet("QPushButton{color:#c9cef0;background:rgba(255,255,255,0.08);border:none;"
                            "border-radius:13px;}QPushButton:hover{color:white;background:#ff4d6d;}")
        close.clicked.connect(self.close_requested.emit)
        top.addWidget(self._title)
        top.addStretch(1)
        top.addWidget(self._sound)
        top.addWidget(close)
        lay.addLayout(top)
        self._stack = QStackedWidget()
        self.cam = QLabel()
        self.cam.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cam.setStyleSheet("background:transparent;")
        self.cam.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._stack.addWidget(self.cam)
        self.video = QVideoWidget() if HAVE_VIDEO else QLabel("Video is not available in this Qt install.")
        self._stack.addWidget(self.video)
        lay.addWidget(self._stack, 1)
        self.hide()

    def show_camera(self, title: str = "Camera") -> None:
        self._title.setText(f"📷  {title}")
        self._sound.hide()
        self._stack.setCurrentIndex(0)
        self.show()

    def show_video(self, title: str) -> None:
        self._title.setText(f"▶  {title[:60]}")
        self._sound.show()
        self._stack.setCurrentIndex(1)
        self.show()

    def set_sound_label(self, muted: bool) -> None:
        self._sound.setText("🔇  Sound off" if muted else "🔊  Sound on")

    def set_frame(self, data: bytes) -> None:
        px = QPixmap()
        if px.loadFromData(data) and self.cam.width() > 1:
            self.cam.setPixmap(px.scaled(self.cam.width(), self.cam.height(),
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))


# ══════════════════════════════════════════════════════════════════════════════
#  Conversation history
# ══════════════════════════════════════════════════════════════════════════════
class ChatStore:
    """Conversations saved to memory/chats.json: user and assistant lines only."""

    MAX_CHATS, MAX_MSGS = 60, 300

    def __init__(self):
        data = _read_json(CHATS_FILE, [])
        self.chats: list[dict] = data if isinstance(data, list) else []
        self.current: dict | None = None
        self.temporary = False          # temporary chat: messages live in memory only, never in chats.json
        self._dirty = False
        self._tmr = QTimer()
        self._tmr.setSingleShot(True)
        self._tmr.timeout.connect(self.flush)

    def new(self) -> dict:
        self.current = {"id": int(time.time() * 1000), "title": "New chat",
                        "created": datetime.now().isoformat(timespec="seconds"), "messages": []}
        return self.current

    def add(self, role: str, text: str) -> None:
        if self.current is None:
            self.new()
        c = self.current
        c["messages"].append({"role": role, "text": text,
                              "ts": datetime.now().strftime("%I:%M %p").lstrip("0")})
        c["messages"] = c["messages"][-self.MAX_MSGS:]
        if self.temporary:
            return                                              # kept on screen only
        if role == "user" and c["title"] == "New chat":
            c["title"] = " ".join(text.split())[:48] or "New chat"
        if c not in self.chats:
            self.chats.insert(0, c)
            self.chats = self.chats[:self.MAX_CHATS]
        self._dirty = True
        self._tmr.start(1500)

    def open(self, cid: int) -> dict | None:
        for c in self.chats:
            if c["id"] == cid:
                self.current = c
                return c
        return None

    def delete(self, cid: int) -> None:
        self.chats = [c for c in self.chats if c["id"] != cid]
        if self.current and self.current["id"] == cid:
            self.current = None
        self._dirty = True
        self.flush()

    def flush(self) -> None:
        if self._dirty:
            _write_json(CHATS_FILE, self.chats)
            self._dirty = False

    def grouped(self, query: str = "") -> list[tuple[str, list[dict]]]:
        q = query.strip().lower()
        today = datetime.now().date()
        groups: dict[str, list[dict]] = {"Today": [], "Yesterday": [], "Previous 7 days": [], "Older": []}
        for c in self.chats:
            if q and q not in c["title"].lower() and not any(q in m["text"].lower() for m in c["messages"]):
                continue
            try:
                d = datetime.fromisoformat(c["created"]).date()
            except Exception:
                d = today
            age = (today - d).days
            key = "Today" if age <= 0 else "Yesterday" if age == 1 else "Previous 7 days" if age <= 7 else "Older"
            groups[key].append(c)
        return [(k, v) for k, v in groups.items() if v]


# ══════════════════════════════════════════════════════════════════════════════
#  Chat window
def _apply_app_icon(window) -> None:
    """The robot icon on the chat window and the taskbar button. Without an explicit id Windows files a
    pythonw process under Python's own icon, so the process gets its own identity first."""
    try:
        ico = CONFIG_DIR / "jarvis.ico"
        if ico.exists():
            icon = QIcon(str(ico))
            window.setWindowIcon(icon)
            app = QApplication.instance()
            if app is not None:
                app.setWindowIcon(icon)
        if _OS == "Windows":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JARVIS.VoiceAssistant")
    except Exception:
        pass                                              # an icon is cosmetic; never block the window



# ══════════════════════════════════════════════════════════════════════════════
class ChatWindow(QWidget):
    """The chat interface. Talks to the outside world only through ChatController."""

    hidden_by_user = pyqtSignal()
    geometry_changed = pyqtSignal()

    def __init__(self, ctl: "ChatController"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.ctl = ctl
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(f"{ctl._assistant_name}")
        _apply_app_icon(self)
        self.setAcceptDrops(True)
        self.setMinimumSize(760, 500)
        self.resize(1040, 700)
        # A frameless window has no edges to grab, so give it a resize handle.
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(20, 20)
        self._grip.setStyleSheet("background:transparent;")
        self._grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._grip.setToolTip("Drag to resize")
        self._drag_from: QPoint | None = None
        self._state = "LISTENING"
        self._attached: str | None = None
        self._overlay: QWidget | None = None
        self.store = ChatStore()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 20)
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            f"QFrame#card{{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {P.BG1},stop:1 {P.BG2});"
            f"border-radius:30px;border:1px solid {P.RING};}}")
        _shadow(self.card, 50, 60, 14)
        outer.addWidget(self.card)

        root = QHBoxLayout(self.card)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        root.addLayout(self._build_rail())
        root.addWidget(self._build_center(), 1)
        root.addWidget(self._build_side())

        QShortcut(QKeySequence("F4"), self, activated=ctl.toggle_mute)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape)
        self.new_chat(save_old=False)
        self.refresh_history()
        self.set_state("LISTENING")

    # -- layout ----------------------------------------------------------------
    def _build_rail(self) -> QVBoxLayout:
        rail = QVBoxLayout()
        rail.setContentsMargins(0, 6, 0, 6)
        rail.setSpacing(12)
        self.btn_new = _icon_btn("＋", "New chat", 44, dark=True)
        self.btn_new.clicked.connect(lambda: self.new_chat())
        self.btn_set = _icon_btn("⚙", "Settings", 40)
        self.btn_set.clicked.connect(lambda: self._side_page(1))
        self.btn_hist = _icon_btn("↺", "History", 40)
        self.btn_hist.clicked.connect(lambda: (self._side_page(0), self._focus_search(False)))
        self.btn_temp = _icon_btn("◌", "Temporary chat — nothing is saved", 40)
        self.btn_temp.setFont(_font(17))                         # the dotted circle is a small glyph
        self.btn_temp.setCheckable(True)
        self.btn_temp.clicked.connect(lambda checked: self.ctl.set_temp_chat(bool(checked)))
        for b in (self.btn_new, self.btn_set, self.btn_hist, self.btn_temp):
            rail.addWidget(b, alignment=Qt.AlignmentFlag.AlignHCenter)
        rail.addStretch(1)
        hide = _icon_btn("–", "Hide to robot", 40)
        hide.clicked.connect(self.hide_to_robot)
        rail.addWidget(hide, alignment=Qt.AlignmentFlag.AlignHCenter)
        return rail

    def _build_center(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("center")
        panel.setStyleSheet(f"QFrame#center{{background:{P.GLASS};border-radius:24px;}}")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 14, 6, 14)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(22, 0, 18, 0)
        self.title = QLabel("New chat")
        self.title.setFont(_font(11, True))
        self.title.setStyleSheet(f"color:{P.INK};background:transparent;")
        self.chip = QLabel("")
        self.chip.setFont(_font(8, True))
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.chip)
        lay.addLayout(head)

        self.view = ChatView()
        lay.addWidget(self.view, 1)

        self.dock = MediaDock()
        dock_wrap = QHBoxLayout()
        dock_wrap.setContentsMargins(22, 0, 22, 0)
        dock_wrap.addWidget(self.dock)
        lay.addLayout(dock_wrap)

        self.attach_chip = QLabel("")
        self.attach_chip.setFont(_font(8))
        self.attach_chip.setStyleSheet(f"QLabel{{background:{P.SOFT};color:{P.INK2};border-radius:10px;padding:3px 10px;}}")
        self.attach_chip.mousePressEvent = lambda e: self.attach(None)
        self.attach_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_chip.hide()
        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(26, 0, 26, 0)
        chip_row.addWidget(self.attach_chip)
        chip_row.addStretch(1)
        lay.addLayout(chip_row)

        bar = QFrame()
        bar.setObjectName("bar")
        bar.setStyleSheet(f"QFrame#bar{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:25px;}}")
        _shadow(bar, 24, 30, 4)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(6)
        plus = QPushButton("＋")
        plus.setFixedSize(34, 34)
        plus.setCursor(Qt.CursorShape.PointingHandCursor)
        plus.setToolTip("Attach a file")
        plus.setStyleSheet(f"QPushButton{{background:transparent;color:{P.INK2};border:none;font-size:18px;}}"
                           f"QPushButton:hover{{color:{P.INK};}}")
        plus.clicked.connect(self._pick_file)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type your message…")
        self.input.setFont(_font(10))
        self.input.setStyleSheet(f"QLineEdit{{background:transparent;border:none;color:{P.INK};padding:6px 4px;}}")
        self.input.returnPressed.connect(self._send)
        self.mic = QPushButton("🎙")
        self.mic.setFixedSize(36, 36)
        self.mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic.clicked.connect(self.ctl.toggle_mute)
        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(42, 42)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(f"QPushButton{{background:{P.INK};color:{P.ON_INK};border:none;border-radius:21px;font-size:14px;}}"
                                    "QPushButton:hover{background:#262a4a;}")
        self.send_btn.clicked.connect(self._send)
        row.addWidget(plus)
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(self.send_btn)
        bar_wrap = QHBoxLayout()
        bar_wrap.setContentsMargins(20, 0, 20, 0)
        bar_wrap.addWidget(bar)
        lay.addLayout(bar_wrap)
        self._style_mic()
        return panel

    def _build_side(self) -> QWidget:
        side = QFrame()
        side.setFixedWidth(262)
        side.setObjectName("side")
        side.setStyleSheet("QFrame#side{background:transparent;}")
        lay = QVBoxLayout(side)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)

        self.pill = QPushButton(self._status_text())
        self.pill.setFixedHeight(38)
        self.pill.setFont(_font(9, True))
        self.pill.setEnabled(False)
        self.pill.setStyleSheet(f"QPushButton{{background:{P.SURFACE};color:{P.INK};border:1px solid {P.LINE};"
                                f"border-radius:19px;padding:0 14px;text-align:left;}}")
        # The close button sits beside the status pill, which puts it in the window's top-right corner.
        close = _icon_btn("✕", "Close JARVIS", 38)
        close.setFont(_font(11))
        close.setStyleSheet(f"QPushButton{{background:{P.SURFACE};color:{P.INK2};border:1px solid {P.LINE};border-radius:19px;}}"
                            "QPushButton:hover{background:#e5484d;color:#ffffff;border:1px solid #e5484d;}")
        close.clicked.connect(self.ctl.quit)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self.pill, 1)
        top.addWidget(close)
        lay.addLayout(top)

        self.side_stack = QStackedWidget()
        self.side_stack.addWidget(self._build_home_page())
        self.side_stack.addWidget(self._build_settings_page())
        lay.addWidget(self.side_stack, 1)
        return side

    def _status_text(self) -> str:
        return f"✦  {self.ctl._assistant_name.title()}  ·  {'offline' if getattr(self.ctl, '_offline', False) else 'online'}"

    def refresh_status(self) -> None:
        self.pill.setText(self._status_text())

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.doc = DocPanel()
        self.doc.browse.connect(self._pick_file)
        self.doc.cleared.connect(lambda: self.attach(None))
        self.doc.command.connect(self.say)
        lay.addWidget(self.doc)
        lay.addSpacing(4)
        self.mute_big = QPushButton("")
        self.mute_big.setFixedHeight(44)
        self.mute_big.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_big.setFont(_font(10, True))
        self.mute_big.clicked.connect(self.ctl.toggle_mute)
        lay.addSpacing(4)
        lay.addWidget(self.mute_big)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search chats")
        self.search.setFixedHeight(32)
        self.search.setFont(_font(9))
        self.search.setStyleSheet(f"QLineEdit{{background:{P.GLASS};border:1px solid {P.LINE};"
                                  f"border-radius:16px;padding:0 12px;color:{P.INK};}}")
        self.search.textChanged.connect(self.refresh_history)
        lay.addWidget(self.search)

        self.hist_area = QScrollArea()
        self.hist_area.setWidgetResizable(True)
        self.hist_area.setFrameShape(QFrame.Shape.NoFrame)
        self.hist_area.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                                     "QScrollArea>QWidget>QWidget{background:transparent;}QScrollBar:vertical{width:6px;}")
        self.hist_area.viewport().setAutoFillBackground(False)
        self.hist_area.viewport().setStyleSheet("background:transparent;")
        self.hist_inner = QWidget()
        self.hist_inner.setStyleSheet("background:transparent;")
        self.hist_lay = QVBoxLayout(self.hist_inner)
        self.hist_lay.setContentsMargins(0, 0, 0, 0)
        self.hist_lay.setSpacing(2)
        self.hist_lay.addStretch(1)
        self.hist_area.setWidget(self.hist_inner)
        lay.addWidget(self.hist_area, 1)
        self._style_mute_big()
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        back = QPushButton("←  Settings")
        back.setFont(_font(10, True))
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setStyleSheet(f"QPushButton{{background:transparent;color:{P.INK};border:none;text-align:left;}}")
        back.clicked.connect(lambda: self._side_page(0))
        lay.addWidget(back)

        def toggle(text: str, slot) -> QPushButton:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setFixedHeight(36)
            b.setFont(_font(9))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton{{background:{P.SURFACE};color:{P.INK};border:1px solid {P.LINE};"
                            f"border-radius:18px;text-align:left;padding:0 14px;}}"
                            f"QPushButton:checked{{background:{P.INK};color:{P.ON_INK};border-color:{P.INK};}}")
            b.clicked.connect(slot)
            return b

        def action(text: str, slot) -> QPushButton:
            b = toggle(text, slot)
            b.setCheckable(False)
            return b

        self.set_wake = toggle("Wake word", self.ctl.toggle_wake_word)
        self.set_ptt = toggle("Push-to-talk", self.ctl.toggle_ptt)
        lay.addWidget(self.set_wake)
        lay.addWidget(self.set_ptt)
        self.set_confirm = toggle("Ask before risky actions", self.ctl.toggle_confirm)
        self.set_confirm.setToolTip("On: restart, shutdown, stopping programs and installing new tools wait for your CONFIRM press.\n"
                                    "Off: they happen as soon as you ask. Quitting JARVIS always asks.")
        self.set_dark = toggle("Dark mode", self.ctl.toggle_dark_mode)
        self.set_offline_sw = toggle("Offline mode", self.ctl.toggle_offline)
        self.set_offline_sw.setToolTip("On: JARVIS stops using the internet and only opens/closes apps and answers from your saved history.\n"
                                       "It also switches itself on when there is no internet or the online limit is reached.")
        lay.addWidget(self.set_confirm)
        lay.addWidget(self.set_dark)
        lay.addWidget(self.set_offline_sw)

        # Self-improvement: a master switch, and the parts it is made of. Turning a part off stops it ADDING anything;
        # what was already saved stays and is still used.
        self.set_si = toggle("Self-improvement", self.ctl.toggle_si_master)
        self.set_si.setToolTip("Master switch for everything below. Off: JARVIS stops adding anything new — facts, tools, lessons, routines.\n"
                               "What it already knows is kept and still used.")
        lay.addWidget(self.set_si)
        subs = QWidget()
        subs.setStyleSheet("background:transparent;")
        sub_lay = QVBoxLayout(subs)
        sub_lay.setContentsMargins(16, 0, 0, 0)
        sub_lay.setSpacing(6)

        def sub(text: str, tip: str, slot) -> QPushButton:
            b = toggle(text, slot)
            b.setFixedHeight(32)
            b.setToolTip(tip)
            sub_lay.addWidget(b)
            return b

        self.set_remember = sub("Remember facts about me", "Name, city, preferences… saved so I know you next time.\nOff: nothing new is saved; what I know stays.",
                                lambda: self.ctl.toggle_si("memory"))
        self.set_grow = sub("Build new tools", "If you ask for something no tool can do, I may write a new tool for it.\n"
                            "It only adds files under plugins/ — never changes my own core code.", self.ctl.toggle_self_improve)
        self.set_learn = sub("Learn from my corrections", "When you correct me or say how you want me to behave, I save a short lesson and follow it.",
                             lambda: self.ctl.toggle_si("corrections"))
        self.set_routines = sub("Learn my routines", "I notice what you do at the same time most days and may offer it. I never do it on my own.",
                                lambda: self.ctl.toggle_si("routines"))
        self.set_packages = sub("Install missing packages", "A tool I build may need a Python package. I can install it from an approved list —\n"
                                "always after you press CONFIRM, even if confirmations are off.", lambda: self.ctl.toggle_si("packages"))
        self._si_subs = [self.set_remember, self.set_grow, self.set_learn, self.set_routines, self.set_packages]
        lay.addWidget(subs)
        lay.addWidget(action("What I've learned…", self.ctl.open_lessons))

        vl = QLabel("Voice")
        vl.setFont(_font(8, True))
        vl.setStyleSheet(f"color:{P.DIM};background:transparent;")
        self.voice = QComboBox()
        self.voice.setFixedHeight(34)
        self.voice.setFont(_font(9))
        self.voice.setStyleSheet(
            f"QComboBox{{background:{P.SURFACE};border:1px solid {P.LINE};border-radius:17px;padding:0 14px;color:{P.INK};}}"
            "QComboBox::drop-down{border:none;width:26px;}"
            f"QComboBox QAbstractItemView{{background:{P.SURFACE};color:{P.INK};border:1px solid {P.LINE};"
            f"selection-background-color:{P.SOFT};selection-color:{P.INK};outline:0;}}")
        try:
            from memory.config_manager import AVAILABLE_VOICES, get_voice
            self.voice.addItems(AVAILABLE_VOICES)
            self.voice.setCurrentText(get_voice())
        except Exception:
            self.voice.setEnabled(False)
        self.voice.activated.connect(lambda _i: self.ctl.set_voice(self.voice.currentText()))
        lay.addWidget(vl)
        lay.addWidget(self.voice)

        pl = QLabel("Personality")
        pl.setFont(_font(8, True))
        pl.setStyleSheet(f"color:{P.DIM};background:transparent;")
        self.persona = QComboBox()
        self.persona.setFixedHeight(34)
        self.persona.setFont(_font(9))
        self.persona.setStyleSheet(self.voice.styleSheet())
        try:
            from memory.config_manager import PERSONALITIES, get_personality
            for key, (label, _instr) in PERSONALITIES.items():
                self.persona.addItem(label, key)
            self.persona.setCurrentIndex(max(0, self.persona.findData(get_personality())))
        except Exception:
            self.persona.setEnabled(False)
        self.persona.activated.connect(lambda _i: self.ctl.set_personality(self.persona.currentData()))
        lay.addWidget(pl)
        lay.addWidget(self.persona)

        lay.addWidget(action("Audio devices…", self.ctl.open_audio_devices))
        lay.addWidget(action("What I remember…", self.ctl.open_memory))
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent;}QScrollBar:vertical{width:6px;}")
        scroll.viewport().setAutoFillBackground(False)
        page.setAutoFillBackground(False)
        page.setStyleSheet("QWidget#settings_page{background:transparent;}")
        page.setObjectName("settings_page")
        scroll.setWidget(page)
        return scroll

    # -- behaviour -------------------------------------------------------------
    def _side_page(self, idx: int) -> None:
        self.side_stack.setCurrentIndex(idx)
        self.btn_set.setChecked(False)
        if idx == 1:
            self.refresh_settings()

    def refresh_settings(self) -> None:
        st = self.ctl.wake_state()
        self.set_wake.setChecked(bool(st.get("enabled")))
        self.set_wake.setText("Wake word" if st.get("ready") else "Wake word (needs download)")
        try:
            from memory.config_manager import (get_confirm_risky, get_dark_mode, get_push_to_talk_enabled,
                                               get_si_flag, get_si_master)
            self.set_ptt.setChecked(bool(get_push_to_talk_enabled()))
            self.set_confirm.setChecked(bool(get_confirm_risky()))
            self.set_dark.setChecked(bool(get_dark_mode()))
            master = bool(get_si_master())
            self.set_si.setChecked(master)
            for btn, part in zip(self._si_subs, ("memory", "tools", "corrections", "routines", "packages")):
                btn.setChecked(bool(get_si_flag(part)))
                btn.setEnabled(master)                     # the parts only work while the master switch is on
            self.set_offline_sw.setChecked(bool(getattr(self.ctl, "offline_forced", False)))
        except Exception:
            pass

    def _focus_search(self, focus: bool = True) -> None:
        self._side_page(0)
        if focus:
            self.search.setFocus()

    def _escape(self) -> None:
        if self._overlay is not None and isinstance(self._overlay, ConfirmCard):
            return                                  # never dismiss a confirmation by accident
        self.ctl.interrupt()

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "THINKING":
            self.view.show_typing()
        else:
            self.view.hide_typing()
        text, color = {
            "LISTENING": ("Listening", P.GREEN), "THINKING": ("Thinking…", P.BLUE),
            "SPEAKING": ("Speaking", P.BLUE), "SLEEPING": ("Sleeping", P.DIM),
            "MUTED": ("Mic muted", P.AMBER),
        }.get(state, (state.title(), P.DIM))
        self.chip.setText(f"●  {text}")
        self.chip.setStyleSheet(f"color:{color};background:transparent;")

    def _style_mic(self) -> None:
        muted = self.ctl._muted
        self.mic.setText("🔇" if muted else "🎙")
        self.mic.setToolTip("Microphone muted — click to unmute (F4)" if muted else "Microphone on — click to mute (F4)")
        self.mic.setStyleSheet(
            f"QPushButton{{background:{P.WARN_BG if muted else P.SOFT};color:{P.INK};border:none;border-radius:18px;}}")

    def _style_mute_big(self) -> None:
        muted = self.ctl._muted
        self.mute_big.setText("🔇  Microphone muted" if muted else "🎙  Microphone active")
        self.mute_big.setStyleSheet(
            f"QPushButton{{background:{P.AMBER if muted else P.INK};color:{P.INK if muted else P.ON_INK};"
            f"border:none;border-radius:22px;}}QPushButton:hover{{opacity:0.9;}}")

    def refresh_mute(self) -> None:
        self._style_mic()
        self._style_mute_big()

    # chat flow
    def say(self, text: str) -> None:
        """Send as if typed."""
        self.ctl.submit_text(text)

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.ctl.submit_text(text)

    def new_chat(self, save_old: bool = True) -> None:
        if self.store.temporary:                                 # the + button always means a normal, saved chat
            self.ctl.set_temp_chat(False)
            return
        if save_old:
            self.store.flush()
        self.store.new()
        self.view.clear()
        self.view.show_welcome(self.ctl._assistant_name)
        self.title.setText("New chat")
        self.refresh_history()

    def set_temp_mode(self, on: bool) -> None:
        """Start (or leave) a temporary chat. Either way the screen starts clean."""
        self.store.temporary = bool(on)
        self.btn_temp.setChecked(bool(on))
        self.store.new()
        self.view.clear()
        self.view.show_welcome(self.ctl._assistant_name, temporary=bool(on))
        self.title.setText("Temporary chat · not saved" if on else "New chat")
        self.refresh_history()

    def open_chat(self, cid: int) -> None:
        if self.store.temporary:
            self.ctl.set_temp_chat(False)
        c = self.store.open(cid)
        if not c:
            return
        self.view.clear()
        self.title.setText(c["title"])
        for m in c["messages"]:
            self.view.add(MessageRow(m["role"], m["text"], m.get("ts", "")))

    def add_line(self, text: str) -> str:
        """Route one write_log() line into the conversation. Returns its role."""
        name = self.ctl._assistant_name.lower()
        low = text.lower()
        if low.startswith("you:"):
            body = text[4:].strip()
            self.view.add(MessageRow("user", body))
            self.store.add("user", body)
            if not self.store.temporary:
                self.title.setText(self.store.current["title"])
            self.refresh_history()
            return "user"
        for pfx in (f"{name}:", "jarvis:"):
            if low.startswith(pfx):
                body = text[len(pfx):].strip()
                if body:
                    self.view.add(MessageRow("assistant", body))
                    self.store.add("assistant", body)
                return "assistant"
        if low.startswith("image:") and Path(text[6:].strip()).is_file():
            self.view.add(ImageCard(text[6:].strip()))
        elif low.startswith("file:"):
            self.view.add(SystemNote("📎  " + text[5:].strip(), "file"))
        elif low.startswith("err") or " error" in low[:40]:
            self.view.add(SystemNote(text.split(":", 1)[-1].strip() if low.startswith("err") else text, "err"))
        else:
            self.view.add(SystemNote(text.split(":", 1)[-1].strip() if low.startswith("sys:") else text))
        return "system"

    def add_card(self, title: str, text: str) -> None:
        self.view.add(ContentCard(title, text))

    def refresh_history(self, *_a) -> None:
        while self.hist_lay.count() > 1:
            it = self.hist_lay.takeAt(0)
            if it.widget():
                it.widget().hide()                          # stop painting now; deleteLater frees it on the next loop turn
                it.widget().deleteLater()
        q = self.search.text() if hasattr(self, "search") else ""
        groups = self.store.grouped(q)
        if not groups:
            e = QLabel("No chats yet" if not q else "Nothing found")
            e.setFont(_font(9))
            e.setStyleSheet(f"color:{P.DIM};background:transparent;padding:6px 8px;")
            self.hist_lay.insertWidget(self.hist_lay.count() - 1, e)
            return
        for name, chats in groups:
            h = QLabel(name)
            h.setFont(_font(8, True))
            h.setStyleSheet(f"color:{P.DIM};background:transparent;padding:8px 8px 2px 8px;")
            self.hist_lay.insertWidget(self.hist_lay.count() - 1, h)
            for c in chats[:25]:
                b = QPushButton("💬  " + c["title"])
                b.setFont(_font(9))
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                current = self.store.current is c
                b.setStyleSheet(
                    f"QPushButton{{background:{P.HOVER2 if current else 'transparent'};"
                    f"color:{P.INK};border:none;text-align:left;padding:7px 8px;border-radius:12px;}}"
                    f"QPushButton:hover{{background:{P.HOVER};}}")
                fm = QFontMetrics(b.font())
                b.setText(fm.elidedText("💬  " + c["title"], Qt.TextElideMode.ElideRight, 205))
                b.setToolTip(c["title"])
                b.clicked.connect(lambda _=False, cid=c["id"]: self.open_chat(cid))
                b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda _p, cid=c["id"]: self._chat_menu(cid))
                self.hist_lay.insertWidget(self.hist_lay.count() - 1, b)

    def _chat_menu(self, cid: int) -> None:
        m = QMenu(self)
        act = m.addAction("Delete chat")
        if m.exec(QCursor.pos()) is act:
            self.store.delete(cid)
            if self.store.current is None:
                self.new_chat(save_old=False)
            self.refresh_history()

    # files
    def attach(self, path: str | None) -> None:
        self._attached = path
        self.doc.set_file(path)
        if path:
            self.attach_chip.setText(f"📎  {Path(path).name}   ✕")
            self.attach_chip.show()
            self._side_page(0)
            self.add_line(f"FILE: {Path(path).name} attached — ask me about it.")
            self.ctl.notify_file(path)
        else:
            self.attach_chip.hide()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload a document", str(Path.home()),
            "Documents and media (*.pdf *.doc *.docx *.txt *.md *.csv *.xlsx *.xls *.pptx *.json "
            "*.png *.jpg *.jpeg *.webp *.py *.js *.html *.zip *.mp3 *.wav *.mp4);;All files (*.*)")
        if path:
            self.attach(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.attach(u.toLocalFile())
                break

    # overlays (setup / confirmation / audio / memory / remote)
    def show_overlay(self, w: QWidget) -> None:
        self.clear_overlay()
        w.setParent(self.card)
        self._overlay = w
        w.adjustSize()
        w.move(max(0, (self.card.width() - w.width()) // 2), max(0, (self.card.height() - w.height()) // 2))
        w.show()
        w.raise_()

    def clear_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None

    # window chrome
    def hide_to_robot(self) -> None:
        self.hide()
        self.hidden_by_user.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 70:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_from is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, e):
        self._drag_from = None

    def closeEvent(self, e):
        e.ignore()
        self.hide_to_robot()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - self._grip.width() - 22, self.height() - self._grip.height() - 26)
        self._grip.raise_()
        if self._overlay is not None:
            self._overlay.move(max(0, (self.card.width() - self._overlay.width()) // 2),
                               max(0, (self.card.height() - self._overlay.height()) // 2))
        self.geometry_changed.emit()

    def moveEvent(self, e):
        super().moveEvent(e)
        self.geometry_changed.emit()


# ══════════════════════════════════════════════════════════════════════════════
#  Controller — the part ui.py's JarvisUI talks to
# ══════════════════════════════════════════════════════════════════════════════
class _HudShim:
    """Where JarvisUI sends audio level and mouth shapes (it used to be HudCanvas)."""

    def __init__(self, robot: RobotWidget):
        self._robot = robot
        self.muted = False
        self.state = "LISTENING"

    def set_audio_level(self, level: float) -> None:
        self._robot.level = max(0.0, min(1.0, float(level)))

    def push_visemes(self, frames, hop: float, at: float) -> None:
        self._robot.push_visemes(frames, hop, at)


class _DropShim:
    def __init__(self, ctl: "ChatController"):
        self._ctl = ctl

    def current_file(self) -> str | None:
        return self._ctl.chat._attached


class ChatController(QObject):
    # The same signals the old MainWindow exposed — JarvisUI emits these from any thread.
    _log_sig = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _content_sig = pyqtSignal(str, str)
    _reconfig_sig = pyqtSignal()
    _camera_sig = pyqtSignal(bytes)
    _cam_stream_sig = pyqtSignal(bool)
    _cam_frame_sig = pyqtSignal(bytes)
    _video_open_sig = pyqtSignal(str, str, bool, str)
    _video_close_sig = pyqtSignal()
    _video_mute_sig = pyqtSignal(bool)
    _confirm_sig = pyqtSignal(str, str)
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig = pyqtSignal(bool, str)
    _quiz_sig = pyqtSignal(str, object, object)
    _quiz_hide_sig = pyqtSignal()
    _offline_sig = pyqtSignal(bool)
    _review_sig = pyqtSignal(str, str, object, object)

    def __init__(self, face_path: str = ""):
        super().__init__()
        QApplication.setQuitOnLastWindowClosed(False)
        cfg = _read_config()
        self._assistant_name: str = (cfg.get("assistant_name") or "JARVIS").strip()

        self.on_text_command = None
        self.on_interrupt = None
        self.on_voice_change = None
        self.on_personality_change = None
        self.on_robot_event = None
        self.on_audio_device_change = None
        self.get_plugins = None
        self.get_plugin_settings = None
        self.on_wake_toggle = None
        self.on_wake_manual = None
        self.on_push_to_talk = None
        self.ptt_hold = None
        self.wake_get_state = None
        self._muted = False
        self._ready = self._check_config()
        self._video_on = False
        self._video_split = False
        self._video_auto_muted = False
        self._cam_stop = threading.Event()
        self._pending_confirm = False
        self._last_activity = time.monotonic()

        try:
            from memory.config_manager import get_dark_mode
            P.apply(get_dark_mode())
            apply_app_palette(P.DARK)
        except Exception:
            pass
        self.on_settings_change = None
        self.on_temp_chat_change = None
        self.on_offline_toggle = None
        self._offline = False             # the assistant is running on its offline brain
        self.offline_forced = False       # …because the user switched it on in Settings
        self.robot = RobotWidget()
        self.hud = _HudShim(self.robot)
        self.bubble = SpeechBubble()
        self.chat = ChatWindow(self)
        self._drop_zone = _DropShim(self)

        self.robot.clicked.connect(self.toggle_chat)
        self.robot.moved.connect(self._robot_moved)
        self.robot.shaken.connect(self._robot_shaken)
        self.robot.reacted.connect(self._robot_react)
        self.robot.menu_requested.connect(self._robot_menu)
        self.bubble.clicked.connect(self.open_chat)
        self._wire_chat()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_state)

        self._offline_sig.connect(self._on_offline)
        self._log_sig.connect(self._on_log)
        self._state_sig.connect(self._on_state)
        self._content_sig.connect(self._on_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._on_camera_snapshot)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._set_video_muted)
        self._confirm_sig.connect(self._show_confirm)
        self._confirm_hide_sig.connect(self._hide_confirm)
        self._wake_dl_sig.connect(self._on_wake_done)
        self._quiz_sig.connect(self._on_quiz)
        self._quiz_hide_sig.connect(lambda: None)
        self._review_sig.connect(self._on_review)

        # the robot must always be there: re-show it, re-pin it on top and pull it back on screen
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self.robot.keep_visible)
        self._watchdog.start(2000)
        app = QApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda *_: self.robot.keep_visible())
            app.screenRemoved.connect(lambda *_: self.robot.keep_visible())
            app.primaryScreenChanged.connect(lambda *_: self.robot.keep_visible())

        # idle nudge, like the screenshot's "Stuck on something?"
        self._idle = QTimer(self)
        self._idle.timeout.connect(self._maybe_nudge)
        self._idle.start(60_000)

        self._video_player = self._video_sound = self._video_audio = self._video_sound_out = None
        self._video_sync: QTimer | None = None

    def _wire_chat(self) -> None:
        """Everything that listens to the chat window. Run again whenever the window is rebuilt."""
        self.chat.hidden_by_user.connect(self._robot_moved)
        self.chat.geometry_changed.connect(self._robot_moved)
        try:
            self._cam_frame_sig.disconnect()
        except TypeError:
            pass
        self._cam_frame_sig.connect(self.chat.dock.set_frame)
        self.chat.dock.close_requested.connect(self._dock_closed)
        self.chat.dock.sound_toggled.connect(self._toggle_video_sound)

    def _on_offline(self, on: bool) -> None:
        """The assistant went offline (or came back). The window stays exactly as it was — only the status
        text and the Settings switch change."""
        self._offline = bool(on)
        self.chat.refresh_status()
        self.chat.refresh_settings()

    def toggle_offline(self) -> None:
        """The Settings switch: use the offline assistant now, or go back to the online one."""
        self.offline_forced = not self.offline_forced
        if callable(self.on_offline_toggle):
            self.on_offline_toggle(self.offline_forced)
        else:
            self.offline_forced = False
            self._on_log("SYS: Assistant is still starting up — try again in a moment.")
        self.chat.refresh_settings()

    def set_temp_chat(self, on: bool) -> None:
        """The rail's temporary-chat button: a conversation that is not saved to history or to memory."""
        self.chat.set_temp_mode(on)
        if callable(self.on_temp_chat_change):
            try:
                self.on_temp_chat_change(on)
            except Exception:
                pass

    # -- settings switches ---------------------------------------------------------
    def toggle_confirm(self) -> None:
        from memory.config_manager import get_confirm_risky, save_confirm_risky
        on = not get_confirm_risky()
        save_confirm_risky(on)
        self._on_log("SYS: I will ask you to confirm risky actions." if on else
                     "SYS: Confirmations are OFF — risky actions (restart, stopping programs, new tools…) now happen at once. "
                     "Quitting JARVIS still asks.")
        self.chat.refresh_settings()

    _SI_NOTES = {
        "memory": ("I will remember new facts about you again.", "I won't save anything new about you (what I already know stays)."),
        "tools": ("I may write a new tool if you ask for something I can't do (you confirm each install unless confirmations are off).",
                  "I won't write new tools."),
        "corrections": ("I will save a lesson when you correct me, and follow it from then on.", "I won't save new lessons (the ones I have still apply)."),
        "routines": ("I will notice what you do at the same time most days, and may offer it.", "I'm no longer noting your routines."),
        "packages": ("I may install an approved Python package a new tool needs — always after you confirm.", "I won't install packages."),
    }

    def _si_changed(self) -> None:
        self.chat.refresh_settings()
        if self.on_settings_change:                       # the instructions change, so the session is rebuilt (context kept)
            self.on_settings_change()

    def toggle_si_master(self) -> None:
        from memory.config_manager import get_si_master, save_si_master
        on = not get_si_master()
        save_si_master(on)
        self._on_log("SYS: Self-improvement is ON — the parts you have switched on are active." if on else
                     "SYS: Self-improvement is OFF — I won't add new facts, tools, lessons or routines. What I already know stays.")
        self._si_changed()

    def toggle_si(self, part: str) -> None:
        from memory.config_manager import get_si_flag, save_si
        on = not get_si_flag(part)
        save_si(part, on)
        self._on_log("SYS: " + self._SI_NOTES[part][0 if on else 1])
        self._si_changed()

    def toggle_self_improve(self) -> None:
        self.toggle_si("tools")

    def open_lessons(self) -> None:
        try:
            self.chat.show_overlay(LessonsCard(self.chat.card))
        except Exception as e:
            self._on_log(f"ERR: Could not open that list — {e}")

    def toggle_dark_mode(self) -> None:
        from memory.config_manager import get_dark_mode, save_dark_mode
        on = not get_dark_mode()
        save_dark_mode(on)
        QTimer.singleShot(0, lambda: self._set_theme(on))     # the button that was clicked is about to be rebuilt

    def _set_theme(self, dark: bool) -> None:
        """Recolour everything. Widgets take their colours when they are built, so the chat window is
        rebuilt in the same place, same size, with the same conversation open."""
        P.apply(dark)
        apply_app_palette(dark)
        old = self.chat
        geo, was_visible, page = old.geometry(), old.isVisible(), old.side_stack.currentIndex()
        cur = old.store.current
        temp = old.store.temporary
        cid = cur["id"] if cur and cur.get("messages") and not temp else None
        kept = list(cur["messages"]) if temp and cur else []
        old.store.flush()
        new = ChatWindow(self)
        self.chat = new
        new.resize(geo.size())
        new.move(geo.topLeft())
        new.setProperty("placed", True)
        if temp:                                                 # a temporary chat lives only in memory: carry it over
            new.set_temp_mode(True)
            new.view.clear()
            for m in kept:
                new.view.add(MessageRow(m["role"], m["text"], m.get("ts", "")))
            new.store.current["messages"] = kept
        elif cid is not None:
            new.open_chat(cid)
        new.set_state(old._state)
        self._wire_chat()
        old.hide()
        old.deleteLater()
        if was_visible:
            new.show()
            new.raise_()
            new.activateWindow()
        new._side_page(page)
        self._on_log("SYS: Dark mode on." if dark else "SYS: Light mode on.")

    # -- startup ---------------------------------------------------------------
    def show(self) -> None:
        state = _read_json(STATE_FILE, {})
        scr = _screen_rect()
        sc = state.get("robot_scale")
        if isinstance(sc, (int, float)):
            self.robot.set_scale(sc)
        x, y = state.get("robot_x"), state.get("robot_y")
        if not isinstance(x, int) or not isinstance(y, int):
            x, y = (scr.right() - 170, scr.bottom() - 190) if scr else (100, 100)
        self.robot.move(x, y)
        self.robot.show()
        self.robot.clamp_to_screen()

        # the chat window comes back where and how big the user left it
        cw, ch = state.get("chat_w"), state.get("chat_h")
        if isinstance(cw, int) and isinstance(ch, int) and scr:
            self.chat.resize(max(self.chat.minimumWidth(), min(cw, scr.width())),
                             max(self.chat.minimumHeight(), min(ch, scr.height())))
        cx, cy = state.get("chat_x"), state.get("chat_y")
        if isinstance(cx, int) and isinstance(cy, int) and scr:
            self.chat.move(min(max(scr.left(), cx), max(scr.left(), scr.right() - 200)),
                           min(max(scr.top(), cy), max(scr.top(), scr.bottom() - 120)))
            self.chat.setProperty("placed", True)
        self._ui_ready = True               # from here on, geometry changes are saved

        if not self._ready:
            self.open_chat()
            self._show_setup()

    def _robot_moved(self) -> None:
        """Remember where the robot and the chat window are, and how big (debounced)."""
        if getattr(self, "_ui_ready", False):
            self._save_timer.start(500)

    def _save_state(self) -> None:
        _write_json(STATE_FILE, {
            "robot_x": self.robot.x(), "robot_y": self.robot.y(),
            "robot_scale": round(self.robot.scale_factor, 2),
            "chat_x": self.chat.x(), "chat_y": self.chat.y(),
            "chat_w": self.chat.width(), "chat_h": self.chat.height(),
        })

    def _check_config(self) -> bool:
        d = _read_config()
        return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))

    # -- window show / hide ----------------------------------------------------
    def toggle_chat(self) -> None:
        if self.chat.isVisible():
            self.chat.hide_to_robot()
        else:
            self.open_chat()

    def open_chat(self) -> None:
        self.bubble.hide()
        if not self.chat.isVisible():
            scr = _screen_rect()
            if scr:
                w, h = self.chat.width(), self.chat.height()
                x = min(max(scr.left() + 10, self.robot.x() + self.robot.width() - w), scr.right() - w - 10)
                y = min(max(scr.top() + 10, self.robot.y() + self.robot.height() - h - 20), scr.bottom() - h - 10)
                if not self.chat.property("placed"):
                    self.chat.move(x, y)
                    self.chat.setProperty("placed", True)
            self.chat.setWindowOpacity(0.0)
            self.chat.show()
            anim = QPropertyAnimation(self.chat, b"windowOpacity", self)
            anim.setDuration(170)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self.chat.raise_()
        self.chat.activateWindow()
        self.chat.input.setFocus()

    def _robot_menu(self, pos: QPoint) -> None:
        m = QMenu()
        m.addAction("Open chat", self.open_chat)
        size = m.addMenu("Robot size")
        size.addAction("Bigger", lambda: self.robot.set_scale(self.robot.scale_factor + 0.2))
        size.addAction("Smaller", lambda: self.robot.set_scale(self.robot.scale_factor - 0.2))
        size.addAction("Reset", lambda: self.robot.set_scale(1.0))
        m.addAction("Unmute microphone" if self._muted else "Mute microphone", self.toggle_mute)
        m.addAction("Stop talking", self.interrupt)
        m.addSeparator()
        m.addAction("Quit", self.quit)
        m.exec(pos)

    def quit(self) -> None:
        self.chat.store.flush()
        self._save_state()
        try:                                              # leave the offline copy of the history up to date
            from core import offline_history
            offline_history.sync()
        except Exception:
            pass
        os._exit(0)

    def _maybe_nudge(self) -> None:
        idle = time.monotonic() - self._last_activity
        if self.chat.isVisible() or self._pending_confirm or idle < 8 * 60:
            return
        self._last_activity = time.monotonic()
        self.bubble.say(random.choice([
            "Stuck on something? Let me help — get a quick assist!",
            "Need a hand? Click me and just ask.",
            "Want a quick briefing on your day?",
        ]), self.robot, 8000)

    # What the robot says in its bubble when the assistant is not able to answer out loud
    # (asleep, offline, busy talking). When it can, the model speaks the reaction instead,
    # in the chosen voice and personality, and its words appear in the bubble.
    _ROBOT_LINES = {
        "shake1": ("Whoa… everything is spinning…", "Easy! I'm getting dizzy!", "Ooh, the room is moving…"),
        "shake2": ("Hey! Stop shaking me!", "I'm warning you!", "Put. Me. Down."),
        "shake3": ("THAT'S IT. I'M ANGRY NOW!", "Do that again and see what happens!", "Grrr! Quit it!"),
        "pickup": ("Wheee!", "Where are we going?", "Hey, careful with me!"),
        "drop": ("Ahh, nice spot.", "Right, this'll do.", "Thanks for the lift."),
        "resize_big": ("Look at me, I'm huge!", "Now we're talking!"),
        "resize_small": ("Aww, tiny me.", "Honey, I shrunk the robot."),
        "woke": ("Mmm… I'm up, I'm up!", "Five more minutes…", "Huh? What? I was resting my eyes."),
    }
    _QUIET_KINDS = ("pickup", "drop", "resize_big", "resize_small")     # bubble only now and then

    def _robot_shaken(self, count: int) -> None:
        """The robot was shaken: dizzy first, then cross — and it says so."""
        self._robot_react(f"shake{min(max(count, 1), 3)}")

    def _robot_react(self, kind: str) -> None:
        spoken = False
        if callable(self.on_robot_event):
            try:
                spoken = bool(self.on_robot_event(kind))
            except Exception:
                spoken = False
        if spoken or kind not in self._ROBOT_LINES:
            return
        if kind in self._QUIET_KINDS and random.random() > 0.35:
            return
        self.bubble.say(random.choice(self._ROBOT_LINES[kind]), self.robot, 2600, kind="answer")

    # -- logging / state -------------------------------------------------------
    def _on_log(self, text: str) -> None:
        self._last_activity = time.monotonic()
        role = self.chat.add_line(text)
        body = text.split(":", 1)[-1].strip()
        if role == "assistant":
            mood = mood_for_reply(body)
            if mood:
                self.robot.set_mood(*mood)
            if not self.chat.isVisible():                # the robot answers out loud AND in its bubble
                self.bubble.say(body, self.robot, kind="answer")
        elif role == "user":
            if not self.chat.isVisible():
                self.bubble.say(body, self.robot, ms=3500, kind="you")
        elif text.lower().startswith("image:"):
            self.robot.set_mood("HAPPY", 3.0)            # a picture is ready
        elif text.lower().startswith("err"):
            self.robot.set_mood("CONFUSED", 3.5)         # something went wrong: it looks puzzled

    def _on_state(self, state: str) -> None:
        self.hud.state = state
        self.robot.state = state
        self.chat.set_state("MUTED" if (self._muted and state == "LISTENING") else state)

    def _on_content(self, title: str, text: str) -> None:
        self.chat.add_card(title, text)

    def _on_quiz(self, topic: str, questions, grader) -> None:
        lines = []
        for i, q in enumerate(questions or [], 1):
            lines.append(f"{i}. {q.get('question') or q.get('prompt') or q}" if isinstance(q, dict) else f"{i}. {q}")
        self.chat.add_card(f"Quiz — {topic}" if topic else "Quiz", "\n".join(lines) or "(no questions)")

    def _on_review(self, title: str, summary: str, findings, unclear) -> None:
        parts = [summary] if summary else []
        for f in findings or []:
            if isinstance(f, dict):
                parts.append(f"• [{f.get('severity', 'note')}] {f.get('heading', '')}: {f.get('detail', '')}")
        self.chat.add_card(title or "Review", "\n".join(parts))

    # -- input -----------------------------------------------------------------
    def submit_text(self, text: str) -> None:
        self._last_activity = time.monotonic()
        self._on_log(f"You: {text}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(text,), daemon=True).start()
        else:
            self._on_log("SYS: Assistant is still starting up — try again in a moment.")

    def interrupt(self) -> None:
        if self.on_interrupt:
            self.on_interrupt()

    def notify_file(self, path: str) -> None:
        """Tell the assistant a document was attached. Without this it has no idea one
        exists — current_file is only a fallback for when it already calls file_processor."""
        note = (f"[A file was just attached by the user: {path}. When they ask about it, call "
                f"file_processor on it. Acknowledge in one very short sentence; do not read the "
                f"path aloud.]")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(note,), daemon=True).start()

    # -- microphone ------------------------------------------------------------
    def toggle_mute(self) -> None:
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _toggle_mute(self) -> None:             # name JarvisUI.muted.setter calls
        self.toggle_mute()

    def _set_muted(self, muted: bool, note: str = "") -> None:
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self.robot.muted = muted
        self.chat.refresh_mute()
        self.chat.set_state("MUTED" if muted else "LISTENING")
        self.robot.state = "LISTENING"
        self._on_log(("SYS: Microphone muted." if muted else "SYS: Microphone active.") + (f" {note}" if note else ""))

    # -- first run / API key ---------------------------------------------------
    def _show_setup(self) -> None:
        self.open_chat()
        card = SetupCard(self.chat.card)
        card.done.connect(self._setup_done)
        self.chat.show_overlay(card)

    def _setup_done(self, key: str, os_name: str) -> None:
        cfg = _read_config()
        cfg.update({"gemini_api_key": key, "os_system": os_name})
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        API_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
        self._ready = True
        self.chat.clear_overlay()
        self._assistant_name = (_read_config().get("assistant_name") or "JARVIS").strip()
        self._on_state("LISTENING")
        self._on_log(f"SYS: Ready. {self._assistant_name} is online.")

    # -- confirmation gate -----------------------------------------------------
    def _show_confirm(self, title: str, detail: str) -> None:
        self._pending_confirm = True
        self.robot.alert = True
        self.open_chat()                    # a confirmation must always be seen
        card = ConfirmCard(self.chat.card, title, detail)
        card.answered.connect(self._confirm_answered)
        self.chat.show_overlay(card)

    def _hide_confirm(self) -> None:
        self._pending_confirm = False
        self.robot.alert = False
        if isinstance(self.chat._overlay, ConfirmCard):
            self.chat.clear_overlay()

    def _confirm_answered(self, accepted: bool) -> None:
        self._hide_confirm()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._on_log(f"ERR: Confirmation failed — {e}")

    # -- settings actions ------------------------------------------------------
    def wake_state(self) -> dict:
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")), "enabled": bool(s.get("enabled")), "awake": bool(s.get("awake"))}
            except Exception:
                pass
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            return {"ready": is_ready(), "enabled": get_wake_word_enabled(), "awake": True}
        except Exception:
            return {"ready": False, "enabled": False, "awake": True}

    def toggle_wake_word(self) -> None:
        st = self.wake_state()
        if not st["ready"]:
            self._on_log("SYS: Downloading the wake-word model (one time)…")

            def work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=work, daemon=True).start()
            return
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self.chat.refresh_settings()

    def _on_wake_done(self, ok: bool, msg: str) -> None:
        self._on_log("SYS: Wake word ready." if ok else f"SYS: Wake word setup failed: {msg}")
        self.chat.refresh_settings()

    def toggle_ptt(self) -> None:
        try:
            from memory.config_manager import get_push_to_talk_enabled, save_push_to_talk_enabled
            want = not get_push_to_talk_enabled()
            save_push_to_talk_enabled(want)
            if self.on_push_to_talk:
                try:
                    self.on_push_to_talk(want)
                except Exception as e:
                    self._on_log(f"ERR: Push-to-talk failed — {e}")
                    save_push_to_talk_enabled(False)
        except Exception as e:
            self._on_log(f"ERR: Push-to-talk unavailable — {e}")
        self.chat.refresh_settings()

    def set_voice(self, voice: str) -> None:
        try:
            from memory.config_manager import save_voice
            save_voice(voice)
            self._on_log(f"SYS: Voice set to {voice}.")
            if self.on_voice_change:
                self.on_voice_change()
        except Exception as e:
            self._on_log(f"ERR: Could not change voice — {e}")

    def set_personality(self, key: str) -> None:
        try:
            from memory.config_manager import PERSONALITIES, save_personality
            key = save_personality(key)
            self._on_log(f"SYS: Personality set to {PERSONALITIES[key][0]}.")
            if self.on_personality_change:
                self.on_personality_change()
        except Exception as e:
            self._on_log(f"ERR: Could not change personality — {e}")

    def open_audio_devices(self) -> None:
        try:
            ov = AudioCard(self.chat.card)
        except Exception as e:
            self._on_log(f"ERR: Could not open the audio devices — {e}")
            return
        ov.picked.connect(lambda: (self._on_log("SYS: Audio devices updated."),
                                   self.on_audio_device_change() if self.on_audio_device_change else None))
        self.chat.show_overlay(ov)

    def open_memory(self) -> None:
        try:
            self.chat.show_overlay(MemoryCard(self.chat.card))
        except Exception as e:
            self._on_log(f"ERR: Could not open the memory list — {e}")

    # -- camera ----------------------------------------------------------------
    def _on_camera_snapshot(self, data: bytes) -> None:
        self.open_chat()
        self.chat.dock.show_camera("Camera")
        self.chat.dock.set_frame(data)

    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self.open_chat()
            self.chat.dock.show_camera("Live camera")
        elif not self._video_on:
            self.chat.dock.hide()

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream").start()

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    def _cam_loop(self) -> None:
        try:
            import cv2
            idx = 0
            try:
                idx = int(_read_config().get("camera_index", 0))
            except Exception:
                pass
            backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            cap = cv2.VideoCapture(idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.04) and cap.isOpened():
                ok, frame = cap.read()
                if ok and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def _dock_closed(self) -> None:
        self._cam_stop.set()
        self._on_video_close()
        self.chat.dock.hide()

    # -- video -----------------------------------------------------------------
    def _ensure_players(self) -> bool:
        if not HAVE_VIDEO:
            return False
        if self._video_player is None:
            self._video_player = QMediaPlayer()
            self._video_audio = QAudioOutput()
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.setVideoOutput(self.chat.dock.video)
            self._video_player.errorOccurred.connect(self._on_video_error)
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound.setAudioOutput(self._video_sound_out)
            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        return True

    def _on_video_open(self, source: str, title: str, muted: bool, audio_source: str = "") -> None:
        if not self._ensure_players():
            self._on_log("SYS: Video playback is not available in this Qt install.")
            return
        self._cam_stop.set()
        self.open_chat()
        self.chat.dock.show_video(title or "Video")
        self._video_split = bool(audio_source)
        self._video_on = True
        self._set_video_muted(bool(muted))
        url = QUrl.fromLocalFile(source) if Path(source).exists() else QUrl(source)
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._sync_mic_for_video()
        self.chat.dock.hide()

    def _video_out(self):
        return self._video_sound_out if self._video_split else self._video_audio

    def _set_video_muted(self, muted: bool) -> None:
        muted = bool(muted)
        for out in (self._video_audio, self._video_sound_out):
            if out:
                out.setMuted(muted)
        self.chat.dock.set_sound_label(muted)
        self._sync_mic_for_video()

    def _toggle_video_sound(self) -> None:
        out = self._video_out()
        if out:
            self._set_video_muted(not out.isMuted())

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while a video makes sound, so the assistant does not
        answer the film. Only if JARVIS closed it: a mic the user muted stays muted."""
        out = self._video_out()
        sound_on = bool(self._video_on and out and not out.isMuted())
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(True, "The video's sound is on — silence it, close it, or press F4 to talk.")
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if self._video_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            if abs(self._video_sound.position() - self._video_player.position()) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self._on_log(f"SYS: The video could not be played{(' — ' + err) if err else ''}.")
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)
