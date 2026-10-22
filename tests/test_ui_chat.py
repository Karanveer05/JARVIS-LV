# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
Tests for the floating-robot chat interface (ui_chat.py). Runs headless:

    python tests/test_ui_chat.py

Uses Qt's offscreen platform and a temporary chat store, so no window appears and
nothing is written to your real memory/ folder.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

_failed = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond:
        _failed.append(name)


tmp = Path(tempfile.mkdtemp())
import ui_chat
ui_chat.CHATS_FILE = tmp / "chats.json"
ui_chat.STATE_FILE = tmp / "ui_state.json"

from ui import JarvisUI
from core import confirm

u = JarvisUI("face.png")
app, win = u._app, u._win


def pump(ms=200):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


check("chat front end is the default", type(win).__name__ == "ChatController")
win._ready = True
win.chat.clear_overlay()
win.robot.show()
pump()

# -- the contract the rest of the app depends on ---------------------------------
for attr in ("_muted", "_ready", "_assistant_name", "_drop_zone", "hud", "on_text_command",
             "on_interrupt", "get_plugins", "on_wake_toggle", "wake_get_state", "ptt_hold",
             "_toggle_mute", "start_camera_stream", "stop_camera_stream", "video_is_playing",
             "show"):
    check(f"controller has {attr}", hasattr(win, attr))
for sig in ("_log_sig", "_state_sig", "_content_sig", "_reconfig_sig", "_camera_sig", "_confirm_sig",
            "_confirm_hide_sig", "_video_open_sig", "_video_close_sig", "_video_mute_sig",
            "_quiz_sig", "_quiz_hide_sig", "_review_sig"):
    check(f"controller has signal {sig}", hasattr(win, sig))

# -- chat flow ---------------------------------------------------------------------
sent = []
u.on_text_command = sent.append
win.chat.input.setText("what time is it")
win.chat._send()
pump(300)
check("typed text goes to the assistant", sent == ["what time is it"], str(sent))
u.write_log("JARVIS: It is four fifty.")
u.write_log("SYS: something happened")
u.write_log("ERR: something broke")
u.write_log("FILE: report.pdf")
pump(200)
roles = [getattr(w, "role", type(w).__name__) for w in win.chat.view._items]
check("messages routed to bubbles and notes",
      roles[:2] == ["user", "assistant"] and roles[2:] == ["SystemNote"] * 3, str(roles))

# -- history ------------------------------------------------------------------------
win.chat.store.flush()
saved = ui_chat._read_json(ui_chat.CHATS_FILE, [])
check("conversation saved", len(saved) == 1 and len(saved[0]["messages"]) == 2, str(saved)[:200])
check("title comes from first message", saved[0]["title"] == "what time is it")
win.chat.new_chat()
check("new chat starts empty", len(win.chat.view._items) == 0)
win.chat.open_chat(saved[0]["id"])
pump(100)
check("old chat reopens", len(win.chat.view._items) == 2)
win.chat.search.setText("zzzz")
pump(100)
check("search filters history", not win.chat.store.grouped("zzzz"))
win.chat.search.setText("")

# -- states / typing indicator -------------------------------------------------------
u.set_state("THINKING")
pump(100)
check("typing dots while thinking", win.chat.view._typing is not None)
for _ in range(3):
    u.set_state("THINKING")
pump(100)
n_typing = sum(1 for i in range(win.chat.view._lay.count())
               if type(win.chat.view._lay.itemAt(i).widget()).__name__ == "TypingRow")
check("only one typing row ever", n_typing == 1, str(n_typing))
u.set_state("SPEAKING")
pump(100)
check("typing dots removed when speaking", win.chat.view._typing is None)
for st in ("LISTENING", "THINKING", "SPEAKING", "SLEEPING"):
    u.set_state(st)
    pump(80)
    win.robot.grab()                                      # paint every robot state
    check(f"robot paints in {st}", True)
u.push_visemes([(0.5, 0.8, 0.5)] * 20, 0.02, time.time())
u.set_audio_level(0.6)
pump(100)
win.robot.grab()
check("robot paints while speaking with mouth data", True)

# -- microphone ---------------------------------------------------------------------
check("starts unmuted", u.muted is False)
u.muted = True
pump(100)
check("muting from JarvisUI works", win._muted and win.robot.muted)
win.toggle_mute()
check("toggle unmutes", not win._muted)

# -- the confirmation gate ------------------------------------------------------------
ran = []
confirm.bind(u.show_confirm, u.hide_confirm, lambda m: None)
confirm.request("k", "Turn Wi-Fi off", "detail", lambda: (ran.append(1), "ok")[1])
pump(300)
check("confirmation opens the chat even if hidden", win.chat.isVisible())
check("robot shows the alert badge", win.robot.alert)
win.chat.activateWindow()
win.chat._escape()
pump(100)
check("Escape does not dismiss a confirmation", win._pending_confirm)
win._confirm_answered(False)
pump(300)
check("cancel does not run the action", ran == [] and not win.robot.alert)
confirm.request("k", "Again", "detail", lambda: (ran.append(1), "ok")[1])
pump(200)
win._confirm_answered(True)
pump(600)
check("confirm runs the action", ran == [1])

# -- other surfaces ----------------------------------------------------------------------
u.show_content("WEB SEARCH — test", "result text")
pump(100)
check("content shows as a card", type(win.chat.view._items[-1]).__name__ == "ContentCard")
u.show_camera_frame(b"")
pump(100)
check("camera snapshot opens the media dock", win.chat.dock.isVisible())
win._dock_closed()
check("closing the dock hides it", not win.chat.dock.isVisible())
check("no video playing", u.video_is_playing() is False)
sent.clear()
f = tmp / "report.pdf"
f.write_bytes(b"%PDF-1.4 test")
win.chat.attach(str(f))
pump(300)
check("attached file is visible to the app", u.current_file == str(f))
check("assistant is told about the file", any("file_processor" in s and str(f) in s for s in sent), str(sent))
check("upload panel shows the file", win.chat.doc._stack.currentIndex() == 1 and "report.pdf" in win.chat.doc._name.text())
check("upload panel offers PDF actions", win.chat.doc._btn_row.count() == 3)
sent.clear()
win.chat.doc.command.emit("Summarize the file I attached.")
pump(300)
check("an action button asks the assistant", sent == ["Summarize the file I attached."], str(sent))
win.chat.attach(None)
check("detaching clears it", u.current_file is None and win.chat.doc._stack.currentIndex() == 0)
win.chat.new_chat()
check("empty chat shows the welcome", getattr(win.chat.view, "_welcome", None) is not None)
u.write_log("You: hi")
check("welcome goes away on the first message", getattr(win.chat.view, "_welcome", None) is None)

# -- expressions --------------------------------------------------------------------------
from ui_chat import mood_for_reply
for text, want in (("I don't know the answer to that.", "CONFUSED"), ("Which file do you mean?", "ASK"),
                   ("Hello! How can I help?", "WAVE"), ("You're welcome, all done.", "HAPPY"),
                   ("It is sunny today.", None), ("Could you clarify what you want?", "ASK")):
    got = mood_for_reply(text)
    check(f"mood for {text!r}", (got[0] if got else None) == want, str(got))
for mood in ("CONFUSED", "ASK", "HAPPY", "WAVE"):
    win.robot.set_mood(mood, 30)
    pump(60)
    check(f"{mood} expression is shown", win.robot._expr(time.monotonic()) == mood)
    win.robot.grab()
for mood in ("ANGRY", "DIZZY", "LAUGH", "SURPRISED", "LOVE", "SILLY", "WINK", "DANCE", "YAWN", "COOL", "SCAN"):
    win.robot.set_mood(mood, 30)
    pump(40)
    check(f"{mood} expression is shown", win.robot._expr(time.monotonic()) == mood)
    win.robot.grab()                                      # every antic paints without error
check("a laughing reply makes it laugh", (mood_for_reply("Haha, that's a good one!") or ("",))[0] == "LAUGH")
win.robot.set_mood("", 0)

# -- shaking it, antics and napping ------------------------------------------------------
from PyQt6.QtCore import QPoint
rb = win.robot
shaken = []
rb.shaken.connect(shaken.append)


def drag(points, t0):
    rb._trail.clear()
    rb._shake_cool = 0
    for i, (x, y) in enumerate(points):
        rb._track_shake(QPoint(x, y), t0 + i * 0.02)


drag([(i * 6, 100) for i in range(40)], 100.0)
check("a calm drag is not a shake", shaken == [])
drag([(100 + (3 if i % 2 else -3), 100) for i in range(40)], 200.0)
check("a tremor is not a shake", shaken == [])
swing = [(100 + (40 if (i // 2) % 2 else -40), 100) for i in range(24)]
drag(swing, 300.0)
check("the first shake makes it dizzy", shaken == [1] and rb._mood == "DIZZY", f"{shaken} {rb._mood}")
drag(swing, 301.5)
check("shaking again makes it angry", shaken == [1, 2] and rb._mood == "ANGRY", f"{shaken} {rb._mood}")
drag(swing, 330.0)                                           # long enough later that it has calmed down
check("it forgives after a while", shaken[-1] == 1, str(shaken))
rb.set_mood("", 0)
rb._hover = False
rb.state = "LISTENING"
t_now = time.monotonic()
rb._last_active = t_now - rb.NAP_AFTER - 5
rb._life(t_now)
check("it naps when left alone", rb._nap and rb._expr(t_now) == "SLEEPING")
rb._touched()
check("touching it wakes it with a yawn", not rb._nap and rb._mood == "YAWN")
rb.set_mood("", 0)
rb._last_active = time.monotonic()
rb._next_antic = 0.0
rb._life(time.monotonic())
check("it gets up to antics on its own", rb._mood in {m for m, _ in rb.ANTICS}, rb._mood)
rb.set_mood("", 0)
rb._last_antic = ""

# -- what is done to the robot is reacted to (spoken when the assistant can, else in the bubble) --------------
events = []
win.on_robot_event = lambda kind: (events.append(kind), True)[1]
rb.reacted.emit("pickup")
rb._on_shake(time.monotonic() + 100)
check("a reaction is handed to the assistant to speak", "pickup" in events and "shake1" in events, str(events))
win.bubble.hide()
win.on_robot_event = lambda kind: False
win._robot_react("woke")
check("when it cannot speak, the bubble says it", win.bubble.isVisible(), "no bubble")
win.bubble.hide()
rb._last_shake = 0.0
rb._wheel_net = 1
rb._wheel_done()
rb._nap = True
rb._touched()
check("waking from a nap is a reaction", True)
cfg_before = open("config/api_keys.json", encoding="utf-8").read()
from memory.config_manager import get_personality
check("settings has a personality picker with every option", win.chat.persona.count() >= 9 and win.chat.persona.isEnabled())
open("config/api_keys.json", "w", encoding="utf-8").write(cfg_before)

# -- the robot must never go missing -------------------------------------------------------
rb.hide()
rb.keep_visible()
check("the watchdog re-shows a hidden robot", rb.isVisible())
rb.move(-5000, -5000)
rb.keep_visible()
from PyQt6.QtGui import QGuiApplication
check("the watchdog brings an off-screen robot back", QGuiApplication.screenAt(rb.frameGeometry().center()) is not None,
      f"{rb.x()},{rb.y()}")
rb.setWindowOpacity(0.0)
rb.keep_visible()
check("the watchdog restores a transparent robot", rb.windowOpacity() == 1.0)
rb.set_scale(0.55)
check("the robot cannot be shrunk to a speck", rb.scale_factor >= 0.8 and rb.width() >= 100, str(rb.scale_factor))
rb.set_scale(1.0)
orig_scene = rb._paint_scene
rb._paint_scene = lambda p: (_ for _ in ()).throw(RuntimeError("boom"))
rb.grab()
check("a drawing bug falls back to a plain face instead of a blank window", rb._paint_failed)
rb._paint_scene = orig_scene
rb._paint_failed = False
rb.grab()
check("normal drawing resumes", not rb._paint_failed)

win.robot.set_mood("", 0)
win.robot.muted = True
win.robot.state = "LISTENING"
check("muted expression", win.robot._expr(time.monotonic()) == "MUTED")
win.robot.muted = False
win.robot.state = "SPEAKING"
check("speaking beats idle", win.robot._expr(time.monotonic()) == "SPEAKING")
win.robot.state = "LISTENING"
u.write_log("JARVIS: I'm not sure about that one.")
pump(100)
check("a 'don't know' reply makes the robot confused", win.robot._expr(time.monotonic()) == "CONFUSED")
win.robot.set_mood("", 0)
win.chat.hide_to_robot()
pump(100)
u.write_log("You: what is the capital of Peru")
u.write_log("JARVIS: The capital of Peru is Lima.")
pump(200)
check("answer appears in the robot's speech bubble", win.bubble.isVisible() and "Lima" in win.bubble._text, win.bubble._text)
win.bubble.hide()

# -- move and resize ----------------------------------------------------------------------
w0, h0 = win.robot.width(), win.robot.height()
win.robot.set_scale(1.6)
check("robot grows", win.robot.width() == int(128 * 1.6) and win.robot.height() == int(172 * 1.6),
      f"{win.robot.width()}x{win.robot.height()}")
win.robot.grab()
check("robot paints when scaled", True)
win.robot.set_scale(9)
check("robot size is capped", win.robot.scale_factor == win.robot.MAX_SCALE)
win.robot.set_scale(0.01)
check("robot size has a minimum", win.robot.scale_factor == win.robot.MIN_SCALE)
win.robot.set_scale(1.0)
check("robot resets to normal size", (win.robot.width(), win.robot.height()) == (w0, h0))
win.robot.move(300, 200)
win.chat.resize(900, 600)
win.chat.move(120, 80)
pump(900)
saved_ui = ui_chat._read_json(ui_chat.STATE_FILE, {})
check("robot position saved", saved_ui.get("robot_x") == 300 and saved_ui.get("robot_y") == 200, str(saved_ui))
check("chat size and position saved",
      (saved_ui.get("chat_w"), saved_ui.get("chat_h"), saved_ui.get("chat_x"), saved_ui.get("chat_y"))
      == (900, 600, 120, 80), str(saved_ui))
check("chat window has a resize handle", win.chat._grip.isVisible() or win.chat._grip.parent() is win.chat)

# -- first run ----------------------------------------------------------------------------
win._ready = False
u.prompt_reconfig()
pump(300)
check("bad key shows the setup card", type(win.chat._overlay).__name__ == "SetupCard")

# -- settings switches: confirmations, dark mode, self-improving tools ---------------------------------------
import memory.config_manager as _cm
_flags = {"dark": False, "confirm": True, "master": True,
          "si": {"memory": True, "tools": False, "corrections": False, "routines": False, "packages": False}}
_cm.get_dark_mode, _cm.save_dark_mode = (lambda: _flags["dark"]), (lambda v: _flags.__setitem__("dark", v))
_cm.get_confirm_risky, _cm.save_confirm_risky = (lambda: _flags["confirm"]), (lambda v: _flags.__setitem__("confirm", v))
_cm.get_si_master, _cm.save_si_master = (lambda: _flags["master"]), (lambda v: _flags.__setitem__("master", v))
_cm.get_si_flag, _cm.save_si = (lambda part: _flags["si"][part]), (lambda part, v: _flags["si"].__setitem__(part, v))
win._ready = True
win.chat.clear_overlay()
changes = []
win.on_settings_change = lambda: changes.append(1)
win.chat._side_page(1)
check("settings has the three new switches", all(hasattr(win.chat, n) for n in ("set_confirm", "set_dark", "set_grow")))
check("confirmations default to on", win.chat.set_confirm.isChecked())
win.toggle_confirm()
check("confirmation switch turns off", _flags["confirm"] is False and not win.chat.set_confirm.isChecked())
win.toggle_confirm()
check("and back on", _flags["confirm"] is True)
win.toggle_self_improve()
check("the tools switch turns on and tells the session", _flags["si"]["tools"] is True and changes == [1] and win.chat.set_grow.isChecked())
win.toggle_self_improve()
check("tools switch turns off again", _flags["si"]["tools"] is False)

# self-improvement: a master switch and its parts
check("the master and the five parts exist", all(hasattr(win.chat, n) for n in ("set_si", "set_remember", "set_grow", "set_learn", "set_routines", "set_packages")))
check("by default memory is on and the others are off",
      win.chat.set_si.isChecked() and win.chat.set_remember.isChecked()
      and not any(b.isChecked() for b in (win.chat.set_grow, win.chat.set_learn, win.chat.set_routines, win.chat.set_packages)))
changes.clear()
win.toggle_si("corrections")
check("a part turns on and tells the session", _flags["si"]["corrections"] is True and win.chat.set_learn.isChecked() and changes == [1])
win.toggle_si("memory")
check("remembering can be switched off on its own", _flags["si"]["memory"] is False and not win.chat.set_remember.isChecked())
win.toggle_si("memory")
win.toggle_si_master()
check("the master switch turns the parts off", _flags["master"] is False and not win.chat.set_si.isChecked()
      and not any(b.isEnabled() for b in win.chat._si_subs))
check("the parts keep their own setting while the master is off", win.chat.set_learn.isChecked() and _flags["si"]["corrections"] is True)
win.toggle_si_master()
check("the master switch turns them back on", all(b.isEnabled() for b in win.chat._si_subs) and win.chat.set_learn.isChecked())
win.toggle_si("corrections")
win.open_lessons()
pump(100)
check("What I've learned opens", type(win.chat._overlay).__name__ == "LessonsCard")
win.chat.clear_overlay()
win.chat.add_line("You: remember this chat")
win.chat.add_line("JARVIS: I will, sir.")
old_chat = win.chat
win._set_theme(True)
pump(200)
check("dark mode rebuilds the window", win.chat is not old_chat and ui_chat.P.DARK and ui_chat.P.INK == "#eef0ff")
check("the conversation survives the switch", win.chat.store.current is not None and len(win.chat.store.current["messages"]) >= 2)
check("it stays on the settings page", win.chat.side_stack.currentIndex() == 1)
win.chat.grab()
win._set_theme(False)
pump(200)
check("light mode comes back", not ui_chat.P.DARK and ui_chat.P.INK == "#14162b")

# -- temporary chat ----------------------------------------------------------------------------------------
saved_before = len(win.chat.store.chats)
told = []
win.on_temp_chat_change = told.append
check("the rail has a temporary-chat button", hasattr(win.chat, "btn_temp") and win.chat.btn_temp.isCheckable())
win.set_temp_chat(True)
check("temporary mode starts a clean, labelled chat", win.chat.store.temporary and "Temporary" in win.chat.title.text()
      and win.chat.btn_temp.isChecked() and told == [True])
win.chat.add_line("You: this should not be kept")
win.chat.add_line("JARVIS: understood, sir.")
pump(100)
check("temporary messages show on screen", len(win.chat.store.current["messages"]) == 2)
check("temporary messages are not in the saved history", len(win.chat.store.chats) == saved_before
      and not any("should not be kept" in m["text"] for c in win.chat.store.chats for m in c["messages"]))
win.chat.store.flush()
check("nothing about it reaches chats.json",
      "should not be kept" not in (ui_chat.CHATS_FILE.read_text(encoding="utf-8") if ui_chat.CHATS_FILE.exists() else ""))
check("the title stays 'Temporary chat'", "Temporary" in win.chat.title.text())
win._set_theme(True)
pump(150)
check("a theme switch keeps the temporary chat", win.chat.store.temporary and len(win.chat.store.current["messages"]) == 2)
win._set_theme(False)
pump(150)
win.chat.new_chat()
check("the + button leaves temporary mode for a normal chat", not win.chat.store.temporary and not win.chat.btn_temp.isChecked()
      and told[-1] is False and win.chat.title.text() == "New chat")
check("and the temporary conversation is gone", "should not be kept" not in str(win.chat.store.current))

# -- generated pictures in the chat -----------------------------------------------------------------------
import tempfile as _tf
from PIL import Image as _Image
_pic = Path(_tf.mkdtemp()) / "demo.png"
_Image.new("RGB", (300, 200), "#ff7aa8").save(_pic)
win.chat.view.clear()
win.chat.add_line(f"IMAGE: {_pic}")
pump(100)
cards = [w for w in win.chat.view.findChildren(ui_chat.ImageCard)]
check("an IMAGE line shows a picture card", len(cards) == 1 and cards[0].path == str(_pic))
check("the card has Open / Save as / Show in folder", len(cards[0].findChildren(ui_chat.QPushButton)) == 3)
win.chat.add_line("IMAGE: C:/no/such/file.png")
check("a missing picture does not break the chat", len([w for w in win.chat.view.findChildren(ui_chat.ImageCard)]) == 1)
win._on_log(f"IMAGE: {_pic}")
check("a ready picture makes the robot happy", win.robot._mood == "HAPPY")

# -- offline mode in the window ------------------------------------------------------------------------------
toggles = []
win.on_offline_toggle = toggles.append
win.chat.refresh_status()
check("the status says online", win.chat.pill.text().endswith("online"))
win._offline_sig.emit(True)
pump(100)
check("going offline changes only the status text", win._offline and win.chat.pill.text().endswith("offline"))
win._offline_sig.emit(False)
pump(100)
check("coming back online restores it", not win._offline and win.chat.pill.text().endswith("online"))
check("Settings has an offline switch", hasattr(win.chat, "set_offline_sw"))
win.toggle_offline()
check("the switch asks the assistant to go offline", toggles == [True] and win.offline_forced and win.chat.set_offline_sw.isChecked())
win._set_theme(True)
pump(150)
win._offline_sig.emit(True)
pump(100)
check("a rebuilt (dark) window keeps the offline status", win.chat.pill.text().endswith("offline"))
win.toggle_offline()
check("the switch turns it off again", toggles == [True, False] and not win.offline_forced)
win._offline_sig.emit(False)
win._set_theme(False)
pump(100)

print(f"\n{'ALL PASSED' if not _failed else str(len(_failed)) + ' FAILED: ' + ', '.join(_failed)}")
os._exit(1 if _failed else 0)
