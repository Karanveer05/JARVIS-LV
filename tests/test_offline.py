# Copyright (c) 2026 Karanveer Singh (https://github.com/Karanveer05)
# Part of JARVIS-LV, licensed under CC BY-NC 4.0. See LICENSE and NOTICE.
"""
Tests for the offline assistant. Run from the project root:

    python tests/test_offline.py

Nothing here opens or closes a real application, touches your real history files, or uses the
microphone: those are replaced with stand-ins.
"""
import json
import sys
import tempfile
import threading
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

_failed = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else f"   -> {detail}"))
    if not cond:
        _failed.append(name)


from core import offline_assistant as oa
from core import offline_history as oh
from core import offline_voice as ov


# ── understanding commands ───────────────────────────────────────────────────────────────
def test_parse() -> None:
    check("open", oa.parse("open chrome") == ("open", ["chrome"]))
    check("polite, with a name and two apps",
          oa.parse("Hey Jarvis, can you please open Notepad and Calculator for me") == ("open", ["Notepad", "Calculator"]))
    check("close with 'I want to'", oa.parse("I want to quit whatsapp") == ("close", ["whatsapp"]))
    check("filler words dropped", oa.parse("could you launch the task manager app") == ("open", ["task manager"]))
    check("a how-to question is not a command", oa.parse("how do I open chrome")[0] == "other")
    check("shutting down the PC is not 'close an app'", oa.parse("shut down the pc")[0] == "other")
    check("no app named", oa.parse("open") == ("open", []))


# ── the assistant, with the real open/close replaced ─────────────────────────────────────────
def test_assistant() -> None:
    log = []
    fake = types.SimpleNamespace(open_item=lambda n: (log.append(("open", n)), f"Opened {n.title()}")[1],
                                 close_app=lambda n: (log.append(("close", n)), f"Closed {n.title()}")[1])
    a = oa.OfflineAssistant("JARVIS")
    a._oca = staticmethod(lambda: fake)
    check("opens an app", a.handle("open notepad") == "Opened Notepad." and log[-1] == ("open", "notepad"))
    check("opens several", a.handle("open notepad and calculator").count("Opened") == 2)
    check("closes an app", a.handle("close spotify") == "Closed Spotify." and log[-1] == ("close", "spotify"))
    n = len(log)
    for risky in ("close python", "close explorer", "close jarvis", "close everything", "close it", "close"):
        a.handle(risky)
    check("protected and vague closes never reach the closer", len(log) == n, str(log[n:]))
    fake.open_item = lambda n: f"Could not find '{n}'"
    check("not-found is phrased kindly", a.handle("open zzz").startswith("I couldn't find an app called zzz"))
    check("things that need the internet are refused", "needs the internet" in a.handle("play some music"))
    check("it says what it is", "offline mode" in a.handle("what can you do"))


# ── the history copy and the search ────────────────────────────────────────────────────────────
def _history_fixture() -> Path:
    tmp = Path(tempfile.mkdtemp())
    oh.STORE_FILE = tmp / "offline_history.json"
    src = tmp / "src"
    src.mkdir()
    oh._SRC_CHATS, oh._SRC_MEMORY, oh._SRC_NOTES = src / "chats.json", src / "long_term.json", src / "notes.json"
    today = datetime.now().strftime("%Y-%m-%d")
    json.dump([{"id": 1, "title": "Spotify", "created": today + "T10:00:00", "messages": [
        {"role": "user", "text": "Play Arjan Dhillon's new song", "ts": "10:00 AM"},
        {"role": "assistant", "text": "Playing Arjan Dhillon - No Shortcut on Spotify.", "ts": "10:00 AM"},
        {"role": "user", "text": "What's the weather in Delhi?", "ts": "10:05 AM"},
        {"role": "assistant", "text": "It is 30 degrees and partly cloudy in Delhi.", "ts": "10:05 AM"}]}], open(oh._SRC_CHATS, "w"))
    json.dump({"identity": {"name": {"value": "Karan", "updated": today}, "city": {"value": "Chandigarh", "updated": today}},
               "sessions": [{"date": today, "summary": "The user asked for a song and the weather."}]}, open(oh._SRC_MEMORY, "w"))
    json.dump([{"id": 1, "kind": "todo", "text": "Buy a phone charger", "done": False, "created": today + "T09:00"}], open(oh._SRC_NOTES, "w"))
    return tmp


def test_history() -> None:
    tmp = _history_fixture()
    counts = oh.sync()
    check("sync copies chats, summaries, facts and notes", counts == {"chats": 1, "summaries": 1, "facts": 2, "notes": 1}, str(counts))
    a = oa.OfflineAssistant("JARVIS")
    check("a fact: name", a.handle("What is my name?") == "Your name is Karan.")
    check("a fact by another word: where do I live", "Chandigarh" in a.handle("where do I live"))
    check("a past answer is found", "30 degrees" in a.handle("what was the weather in Delhi"))
    check("a past request is found", "No Shortcut" in a.handle("which song did I ask for"))
    from actions import notes as _notes_tool
    _notes_tool.STORE = oh._SRC_NOTES                      # the live notes are what the to-do list shows
    check("the to-do list", "phone charger" in a.handle("what's on my to-do list"))
    check("a recap of today", "song" in a.handle("what did we talk about today").lower())
    check("nothing found says so", "found nothing" in a.handle("what did I say about volcanoes"))
    check("an unanswerable current-events question points to the internet", "needs the internet" in a.handle("who won the cricket world cup"))
    # deleting a chat in the app removes it from the copy; chats that only aged out are kept
    json.dump([], open(oh._SRC_CHATS, "w"))
    oh.sync()
    check("a deleted chat leaves the copy", oh.load()["chats"] == [])
    old = [{"id": i, "title": f"c{i}", "created": "2026-01-01T00:00:00", "messages": []} for i in range(100, 100 + 60)]
    store = oh.load()
    store["chats"] = [{"id": 5, "title": "ancient", "created": "2025-01-01T00:00:00", "messages": []}]
    oh._write(oh.STORE_FILE, store)
    json.dump(old, open(oh._SRC_CHATS, "w"))
    oh.sync()
    ids = {c["id"] for c in oh.load()["chats"]}
    check("a chat that aged out of the live limit is kept", 5 in ids and len(ids) == 61, str(len(ids)))
    check("forgetting a fact removes it from the copy",
          (json.dump({"sessions": []}, open(oh._SRC_MEMORY, "w")), oh.sync(), oh.load()["facts"] == [])[2])
    check("session summaries accumulate", len(oh.load()["summaries"]) == 1)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ── voice ──────────────────────────────────────────────────────────────────────────────────────
def test_voice() -> None:
    check("wake word with a command", ov.strip_wake("jarvis open notepad") == (True, "open notepad"))
    check("hey + wake word", ov.strip_wake("hey jarvis close chrome") == (True, "close chrome"))
    check("the recogniser's near-miss is accepted", ov.strip_wake("jarvas open notepad")[0])
    check("chatter without the name is ignored", ov.strip_wake("open the window it is hot") == (False, "open the window it is hot"))
    check("the bare name", ov.strip_wake("jarvis") == (True, ""))
    heard = []
    v = ov.OfflineVoice(lambda t: (heard.append(t), "ok")[1])
    v.speak = lambda text: None
    done = threading.Event()
    v._respond = lambda t: (heard.append(t), done.set())
    v._heard("what is the time")
    check("unaddressed speech does nothing", heard == [])
    v._heard("jarvis open notepad")
    done.wait(1)
    check("addressed speech is handled without the name", heard == ["open notepad"], str(heard))
    heard.clear()
    done.clear()
    v._heard("jarvis")
    v._heard("close chrome")
    done.wait(1)
    check("after the bare name the next sentence needs no name", heard == ["close chrome"], str(heard))
    ok, why = ov.available()
    check("an offline speech model is installed", ok, why)
    check("Whisper English model is found for offline use", ov._whisper_ready() in ("small.en", "base.en") or ov.find_vosk_model() is not None)

    # clear commands work without the name; questions do not
    import numpy as np
    spoken = []
    v2 = ov.OfflineVoice(lambda t: None, log=lambda m: None,
                         is_command=lambda t: oa.parse(t)[0] in ("open", "close") and bool(oa.parse(t)[1]))
    v2._respond = lambda t: spoken.append(t)
    v2._heard("Open Chrome.")
    v2._heard("Close notepad")
    v2._heard("what is my name")
    v2._heard("I think we should go now")
    import time as _t
    _t.sleep(0.1)
    check("'open chrome' needs no name", spoken == ["Open Chrome.", "Close notepad"], str(spoken))
    check("a question without the name is ignored", "what is my name" not in spoken)

    # the hearing side: cutting a microphone stream into sentences
    rng = np.random.default_rng(0)

    def quiet(sec, level=40):
        return rng.normal(0, level, int(16000 * sec)).astype(np.int16)

    def speech(sec, amp=600):
        t = np.arange(int(16000 * sec)) / 16000
        return (amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) + rng.normal(0, 30, t.size)).astype(np.int16)

    def run(signal):
        det, out = ov.UtteranceDetector(), []
        for i in range(0, len(signal) - ov.BLOCK + 1, ov.BLOCK):
            out += det.feed(signal[i:i + ov.BLOCK])
        return out

    check("silence produces nothing", run(quiet(6)) == [])
    check("a sentence is cut out with its surroundings", len(run(np.concatenate([quiet(2), speech(1.6), quiet(1.5)]))) == 1)
    two = run(np.concatenate([quiet(2), speech(1.2), quiet(1.4), speech(1.5), quiet(1.4)]))
    check("two sentences are two utterances", len(two) == 2, str(len(two)))
    check("a click is not a sentence", run(np.concatenate([quiet(2), speech(0.1), quiet(2)])) == [])
    check("a steady fan is not speech", run(quiet(6, level=120)) == [])
    check("a quiet Bluetooth-level voice is still heard", len(run(np.concatenate([quiet(2), speech(1.6, amp=380), quiet(1.5)]))) == 1)
    check("a sentence is not cut short", sum(len(u) for u in two) / 16000 > 2.0)
    check("whisper's silence hallucinations are dropped", all(ov.is_noise(x) for x in ("you", "Thank you.", "", "Thanks for watching", "uh")))
    check("real sentences are kept", not ov.is_noise("Open Chrome") and not ov.is_noise("What is my name?"))
    check("the resampler keeps duration", len(ov._resample(np.zeros(4410, dtype=np.int16), 44100)) == 1600)


# ── the extra skills ────────────────────────────────────────────────────────────────────────────────
def _skills_assistant():
    """An assistant whose system-changing tools are stand-ins; notes go to a temporary file."""
    from actions import notes as notes_tool
    notes_tool.STORE = Path(tempfile.mkdtemp()) / "notes.json"
    fired, calls = [], []
    a = oa.OfflineAssistant("JARVIS", notify=fired.append)
    real = a.skills._tool

    def tool(module, func, params, **kw):
        if module in ("computer_settings", "spotify", "screenshot"):
            calls.append((module, params))
            return f"done:{module}"
        return real(module, func, params, **kw)
    a.skills._tool = tool
    return a, fired, calls


def test_skills() -> None:
    a, fired, calls = _skills_assistant()
    noon = datetime(2026, 10, 7, 21, 5)
    s = a.skills
    check("the time", s.try_handle("what time is it", noon) == "It's 9:05 PM.")
    check("today's date", s.try_handle("what's the date today", noon) == "Today is Wednesday, 07 October 2026.")
    check("tomorrow", s.try_handle("what day will it be tomorrow", noon) == "That is Thursday, 08 October 2026.")
    check("in three days", "Saturday, 10 October" in s.try_handle("what day is it in 3 days", noon))
    check("days until christmas", s.try_handle("how many days until christmas", noon) == "79 days until christmas.")
    check("maths: percent", a.handle("what is 15% of 240") == "15% of 240 = 36")
    check("maths: words", a.handle("what is 12 times 8") == "12 times 8 = 96")
    check("maths: square root", a.handle("what's the square root of 144").endswith("= 12"))
    check("maths: squared, shown as asked", a.handle("5 squared") == "5 squared = 25")
    check("maths: modulo", a.handle("17 mod 5") == "17 mod 5 = 2")
    check("units", "6.2137" in a.handle("convert 10 km to miles"))
    check("money needs the internet", "internet" in a.handle("convert 50 usd to inr"))
    check("not maths: a sentence with a number", s.try_handle("I have 3 cats and 2 dogs", noon) is None)
    # timers
    r = a.handle("set a timer for 2 seconds")
    check("a timer is set", r.startswith("Timer") and "2 seconds" in r, r)
    check("timers can be listed", "left" in a.handle("how long is left on my timer"))
    check("and cancelled", "Cancelled" in a.handle("cancel the timer"))
    r = a.handle("remind me in 5 minutes to call mom")
    check("a reminder with a label", "5 minutes" in r and "call mom" in r, r)
    a.handle("cancel the timers")
    a.handle("set a timer for 1 second")
    import time as _t
    _t.sleep(1.6)
    check("a finished timer reaches the notifier", fired and "finished" in fired[0].lower(), str(fired))
    check("a timer without a time asks for one", "For how long" in a.handle("set a timer"))
    # notes
    check("a note", a.handle("note down Buy a new Phone charger").startswith("Saved note"))
    check("the user's own capitals are kept", "Buy a new Phone charger" in a.handle("show my notes"))
    check("a to-do", a.handle("add eggs to my to-do list").startswith("Saved to-do"))
    check("the list", "eggs" in a.handle("what's on my to-do list"))
    check("marking done", a.handle("mark eggs done").startswith("Marked done"))
    check("removing", a.handle("remove eggs from my list").startswith("Deleted"))
    # things that change the system go to the right tool
    for phrase, module, action in (("volume up", "computer_settings", "volume_up"), ("set volume to 30", "computer_settings", "volume_set"),
                                   ("mute", "computer_settings", "mute"), ("brightness down", "computer_settings", "brightness_down"),
                                   ("lock the screen", "computer_settings", "lock_screen"), ("show desktop", "computer_settings", "show_desktop"),
                                   ("pause the music", "spotify", "pause"), ("next song", "spotify", "next"),
                                   ("previous song", "spotify", "previous"), ("resume the music", "spotify", "resume"),
                                   ("take a screenshot", "screenshot", "take")):
        calls.clear()
        a.handle(phrase)
        check(f"“{phrase}” -> {module}.{action}", calls and calls[-1][0] == module and calls[-1][1].get("action") == action, str(calls))
    calls.clear()
    a.handle("set volume to 300")
    check("volume is capped at 100", calls and calls[-1][1].get("value") == 100)
    calls.clear()
    check("'play' alone is not a music command", "internet" in a.handle("play some arjan dhillon") and calls == [])
    # this computer
    check("battery", "Battery" in a.handle("how much battery do I have") or "no battery" in a.handle("how much battery do I have"))
    check("memory", "GB" in a.handle("how much ram am I using"))
    check("disk", "GB free" in a.handle("how much disk space do I have"))
    check("uptime", "has been on" in a.handle("how long has my computer been on"))
    check("cpu", "CPU is at" in a.handle("how's my cpu"))
    check("system summary", a.handle("how is my computer").startswith("CPU"))
    check("ip address", "address on your network" in a.handle("what's my ip address"))
    check("online check answers", "internet" in a.handle("am I online"))
    # small things
    check("a coin", a.handle("flip a coin") in ("Heads.", "Tails."))
    check("dice", a.handle("roll 2d6").startswith("Rolled 2d6"))
    check("a number in a range", a.handle("pick a number from 5 to 5") == "5.")
    check("a joke", len(a.handle("tell me a joke")) > 20)
    check("thanks", a.handle("thanks") == "You're welcome.")
    # honesty about what needs the internet
    for q in ("what's the weather today", "send a message to mom", "draw a cat", "who is the prime minister of india", "translate hello to french"):
        check(f"needs internet: {q}", "needs the internet" in a.handle(q), a.handle(q)[:60])
    check("but a question about the past still searches history", "needs the internet" not in a.handle("what did I ask about the weather"))
    check("the help text lists the skills", all(w in a.handle("what can you do") for w in ("timers", "notes", "maths", "battery")))
    acts = ["set a timer for 5 minutes", "volume up", "take a screenshot", "note down buy milk", "pause the music", "lock the screen", "mute"]
    check("clear actions can be spoken without the name", all(a.skills.is_action(x) for x in acts))
    check("questions still need the name", not any(a.skills.is_action(x) for x in ("what time is it", "what is my name", "how much battery do I have", "what's 12 times 8")))
    a.skills._time_date = lambda *x: (_ for _ in ()).throw(RuntimeError("boom"))
    check("a broken skill is reported, not raised", "something went wrong" in a.skills.try_handle("what time is it", noon))


def test_history_questions() -> None:
    tmp = _history_fixture()
    oh.sync()
    a = oa.OfflineAssistant("JARVIS")
    r = a.handle("what was my last question")
    check("the last question", "weather in Delhi" in r, r)
    r = a.handle("when did I ask about the weather")
    check("when something came up", "weather" in r.lower() and "today" in r.lower(), r)
    r = a.handle("how many times did I ask about the weather")
    check("how many times", "1 time" in r, r)
    r = a.handle("how many times did I ask about volcanoes")
    check("how many times: never", "can't find" in r, r)
    long_answer = ("The capital of Punjab is Chandigarh. " * 2 + "Chandigarh was designed by Le Corbusier and is shared with Haryana. "
                   + "Weather is usually pleasant in winter. " * 3)
    check("a long answer is cut to the relevant sentence", "Le Corbusier" in oh._best_sentence(long_answer, oh.tokens("who designed chandigarh")))
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ── the switching logic in main.py, with stand-ins for the UI and the microphone ───────────────────────────
def test_switching() -> None:
    import main
    j = main.JarvisLive.__new__(main.JarvisLive)
    logs, states = [], []
    j.ui = types.SimpleNamespace(set_offline=lambda on: states.append(("offline", on)),
                                 write_log=logs.append, set_state=lambda s: states.append(s))
    j._offline, j._force_offline, j._net_fails = False, False, 0
    j._offline_engine = oa.OfflineAssistant("JARVIS")
    j._offline_voice, j._last_offline_sync = None, 0.0
    j._asst_name = "JARVIS"
    started = []
    j._start_offline_voice = lambda: started.append("voice")
    j._stop_offline_voice = lambda: started.append("stop")
    j._sync_offline_history = lambda: started.append("sync")
    j.session = None
    j._loop = None
    asked = []
    j._offline_engine.handle = lambda t: (asked.append(t), "reply to " + t)[1]
    j._last_offline_sync = 1e18                        # skip the pre-answer sync

    j._enter_offline("no internet connection")
    check("entering offline mode flips the UI and starts offline voice",
          j._offline and ("offline", True) in states and "voice" in started and any("Offline mode" in m for m in logs))
    check("the reason is remembered for the assistant", "no internet" in j._offline_engine.reason)
    n = len(logs)
    j._enter_offline("again")
    check("entering twice does not repeat itself", len(logs) == n)
    j._on_text_command("open notepad")
    check("typed text goes to the offline assistant", asked == ["open notepad"] and logs[-1].endswith("reply to open notepad"))
    j._leave_offline()
    check("going back online flips everything back", not j._offline and ("offline", False) in states and "stop" in started
          and logs[-1] == "SYS: Back online.")
    j._on_text_command("open notepad")
    check("online text is no longer taken offline", asked == ["open notepad"])

    j._offline = False
    j.request_reconnect = lambda **kw: asked.append(("reconnect", kw.get("reason")))
    j._set_force_offline(True)
    check("the Settings switch forces offline and drops the session", j._offline and j._force_offline and ("reconnect", "offline mode") in asked)
    j._leave_offline()
    check("automatic recovery does not override the switch", j._offline)
    j._set_force_offline(False)
    check("turning the switch off returns online", not j._offline and not j._force_offline)


if __name__ == "__main__":
    for fn in (test_parse, test_assistant, test_history, test_skills, test_history_questions, test_voice, test_switching):
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(fn.__name__, False, f"crashed: {e!r}")
    print(f"\n{'ALL PASSED' if not _failed else str(len(_failed)) + ' FAILED: ' + ', '.join(_failed)}")
    sys.exit(1 if _failed else 0)
