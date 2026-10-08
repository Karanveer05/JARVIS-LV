"""
core/offline_voice.py — speaking to JARVIS and hearing it back with no internet. English only.

Hearing
    1. The microphone is read continuously and cut into sentences by a small voice detector
       (UtteranceDetector): speech starts when the level rises well above the room's noise, and a
       sentence ends after a short silence.
    2. Each sentence is turned into text on this PC by Whisper (the English-only "small.en" model, else
       "base.en"; both are already in the Hugging Face cache if the app has used them), falling back to
       the small Vosk English model in models/vosk. Whisper is much better with accents; Vosk is a
       lighter safety net.
    3. The text is handled like typed text. Clear commands ("open Chrome", "close Notepad") work
       without the name; anything else — questions about the history — needs "Jarvis" first, or the bare
       name followed by the request, so that room chatter cannot trigger answers.

Speaking
    Windows' own voices (SAPI). The microphone is ignored while it talks, so it cannot obey its own voice.

It only starts in offline mode, and only while the online session is NOT running, so it never fights the
online assistant for the microphone. It follows the app's mute: muted means not listening.
"""
from __future__ import annotations

import difflib
import json
import queue
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "vosk"
RATE = 16000
BLOCK = 800                                   # 50 ms of audio
_WAKE_WORDS = ("jarvis", "jarvas", "jarvice", "jarves", "jarvus", "garvis", "travis", "jarvi")
_ARMED_FOR = 7.0
_WHISPER_MODELS = ("small.en", "base.en")
_PROMPT = "Jarvis. Open Chrome. Close Notepad. Open Spotify. Open WhatsApp. Close Calculator. Open Visual Studio Code."
# What Whisper tends to "hear" in silence or noise. Never a real command.
_HALLUCINATIONS = {"you", "thank you", "thanks", "thanks for watching", "thank you for watching", "bye", "okay", "ok", "so",
                   "oh", "uh", "um", "hmm", "yeah", "the", "i", "and", "subtitles by the amara.org community"}


# ── what is installed ──────────────────────────────────────────────────────────────────────

def find_vosk_model() -> Path | None:
    try:
        for d in sorted(MODEL_DIR.iterdir()):
            if d.is_dir() and (d / "am").exists():
                return d
    except Exception:
        pass
    return None


find_model = find_vosk_model                   # older name


def _whisper_ready() -> str:
    """The Whisper model name that can be loaded without the internet, or ''."""
    try:
        import faster_whisper  # noqa: F401
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return ""
    for name in _WHISPER_MODELS:
        try:
            if isinstance(try_to_load_from_cache(f"Systran/faster-whisper-{name}", "model.bin"), str):
                return name
        except Exception:
            continue
    return ""


def available() -> tuple[bool, str]:
    try:
        import sounddevice  # noqa: F401
    except Exception:
        return False, "the audio package (sounddevice) is missing"
    if _whisper_ready():
        return True, ""
    try:
        import vosk  # noqa: F401
        if find_vosk_model() is not None:
            return True, ""
    except Exception:
        pass
    return False, "no offline speech model is installed (Whisper small.en or models/vosk)"


# ── understanding what was heard ──────────────────────────────────────────────────────────────

def strip_wake(text: str) -> tuple[bool, str]:
    """(addressed?, the rest). 'hey jarvis open chrome' -> (True, 'open chrome'). Tolerates near-misses
    of the name ('jarvas', 'travis')."""
    words = re.sub(r"[^\w\s']", " ", (text or "").lower()).split()
    for i in range(min(2, len(words))):
        w = words[i]
        if w in _WAKE_WORDS or difflib.SequenceMatcher(None, w, "jarvis").ratio() >= 0.8:
            if i == 1 and words[0] not in ("hey", "hi", "ok", "okay", "hello", "yo"):
                continue
            return True, " ".join(words[i + 1:])
    return False, " ".join(words)


def is_noise(text: str) -> bool:
    t = re.sub(r"[^\w\s']", "", (text or "").lower()).strip()
    return not t or t in _HALLUCINATIONS or len(t) < 3


# ── cutting the microphone stream into sentences ─────────────────────────────────────────────────

class UtteranceDetector:
    """Feed it 16 kHz int16 blocks; it hands back finished sentences as int16 arrays.

    The speech threshold follows the room: it is a few times the noise floor (learned from the quiet
    blocks), never below an absolute minimum, so a noisy fan does not count as speech and a quiet
    Bluetooth microphone still does."""

    MIN_LEVEL = 150          # rms below this is never speech
    FLOOR_MULT = 3.0
    START_BLOCKS = 3         # 150 ms over the threshold to start
    END_SILENCE = 0.8        # seconds of quiet that end a sentence
    MIN_SPEECH = 0.35
    MAX_SPEECH = 12.0
    PREROLL = 6              # blocks kept from before the start, so the first word is not clipped

    def __init__(self, block: int = BLOCK, rate: int = RATE):
        self.block, self.rate = block, rate
        self.floor = 60.0
        self._hot = 0
        self._pre: list[np.ndarray] = []
        self._buf: list[np.ndarray] = []
        self._quiet = 0
        self.speaking = False

    @property
    def threshold(self) -> float:
        return max(self.MIN_LEVEL, self.floor * self.FLOOR_MULT)

    def feed(self, blk: np.ndarray) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        rms = float(np.sqrt(np.mean(blk.astype(np.float32) ** 2))) if blk.size else 0.0
        loud = rms > self.threshold
        if not self.speaking:
            if not loud:
                self.floor = 0.95 * self.floor + 0.05 * max(rms, 20.0)          # learn the room
                self._hot = 0
            else:
                self._hot += 1
            self._pre = (self._pre + [blk])[-self.PREROLL:]
            if self._hot >= self.START_BLOCKS:
                self.speaking, self._buf, self._quiet = True, list(self._pre), 0
            return out
        self._buf.append(blk)
        self._quiet = 0 if loud else self._quiet + 1
        length = len(self._buf) * self.block / self.rate
        if self._quiet * self.block / self.rate >= self.END_SILENCE or length >= self.MAX_SPEECH:
            voiced = length - self._quiet * self.block / self.rate
            if voiced >= self.MIN_SPEECH:
                out.append(np.concatenate(self._buf))
            self.speaking, self._buf, self._hot, self._pre, self._quiet = False, [], 0, [], 0
        return out


def _resample(x: np.ndarray, src: int, dst: int = RATE) -> np.ndarray:
    if src == dst:
        return x
    n = int(len(x) * dst / src)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


# ── turning a sentence into text ───────────────────────────────────────────────────────────────────

class WhisperTranscriber:
    name = "Whisper"

    def __init__(self, model_name: str):
        from faster_whisper import WhisperModel
        self.model_name = model_name
        self._m = WhisperModel(model_name, device="cpu", compute_type="int8", local_files_only=True)

    def transcribe(self, audio: np.ndarray) -> str:
        peak = float(np.max(np.abs(audio))) or 1.0
        x = audio.astype(np.float32) / 32768.0 * min(8.0, 0.7 * 32768.0 / peak)      # lift a quiet microphone
        segs, _ = self._m.transcribe(x, language="en", beam_size=1, temperature=0.0, condition_on_previous_text=False,
                                     initial_prompt=_PROMPT, no_speech_threshold=0.6, vad_filter=False)
        kept = [s.text.strip() for s in segs if s.no_speech_prob < 0.6 and s.avg_logprob > -1.3]
        return " ".join(kept).strip()


class VoskTranscriber:
    name = "Vosk"

    def __init__(self):
        import vosk
        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(find_vosk_model()))

    def transcribe(self, audio: np.ndarray) -> str:
        import vosk
        rec = vosk.KaldiRecognizer(self._model, RATE)
        rec.AcceptWaveform(audio.tobytes())
        return json.loads(rec.FinalResult()).get("text", "").strip()


def load_transcriber(log: Callable[[str], None] = print):
    name = _whisper_ready()
    if name:
        try:
            return WhisperTranscriber(name)
        except Exception as e:
            log(f"SYS: Whisper could not start ({type(e).__name__}); using the lighter recogniser.")
    return VoskTranscriber()


# ── the voice ────────────────────────────────────────────────────────────────────────────────────────

class OfflineVoice:
    def __init__(self, on_text: Callable[[str], str | None], device=None, log: Callable[[str], None] = print,
                 is_muted: Callable[[], bool] | None = None, is_command: Callable[[str], bool] | None = None):
        self._on_text = on_text
        self._device = device
        self._log = log
        self._muted = is_muted or (lambda: False)
        self._is_command = is_command or (lambda t: False)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._deaf = threading.Event()          # set while we are talking
        self._armed_until = 0.0
        self._last_hint = 0.0
        self.running = False

    # -- speaking ---------------------------------------------------------------
    def speak(self, text: str) -> None:
        """Say `text` with the Windows voice. Returns when finished (the mic is deaf meanwhile)."""
        if not text or sys.platform != "win32":
            return
        self._deaf.set()
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                voice.Rate = 1
                voice.Speak(re.sub(r"\s+", " ", text)[:600], 0)
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            self._log(f"SYS: Offline voice could not speak: {e}")
        finally:
            time.sleep(0.45)                    # let the room go quiet before listening again
            self._deaf.clear()

    # -- listening --------------------------------------------------------------
    def start(self) -> bool:
        ok, why = available()
        if not ok:
            self._log(f"SYS: Offline voice is off — {why}. Typing still works.")
            return False
        if self.running or (self._thread and self._thread.is_alive()):
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, name="offline-voice", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=4)
        self._thread = None
        self.running = False

    def _open_stream(self, q: queue.Queue):
        """Open the microphone: the chosen device at 16 kHz, then at its own rate (resampled), then the
        system default. Returns (stream, source_rate)."""
        import sounddevice as sd
        tries = []
        for dev in ((self._device, None) if self._device is not None else (None,)):
            tries.append((dev, RATE))
            try:
                tries.append((dev, int(sd.query_devices(dev, "input")["default_samplerate"])))
            except Exception:
                pass
        last = None
        for dev, rate in tries:
            blocksize = int(BLOCK * rate / RATE)

            def cb(indata, frames, t, status, rate=rate):
                if self._deaf.is_set() or self._muted():
                    return
                try:
                    q.put_nowait((np.frombuffer(bytes(indata), dtype=np.int16).copy(), rate))
                except queue.Full:
                    pass
            try:
                stream = sd.RawInputStream(samplerate=rate, blocksize=blocksize, dtype="int16", channels=1, device=dev, callback=cb)
                stream.start()
                return stream
            except Exception as e:
                last = e
        raise last or RuntimeError("no microphone could be opened")

    def _listen(self) -> None:
        try:
            self._log("SYS: Offline voice is getting ready…")
            recogniser = load_transcriber(self._log)
            q: queue.Queue = queue.Queue(maxsize=200)
            work: queue.Queue = queue.Queue(maxsize=4)
            stream = self._open_stream(q)
            det = UtteranceDetector()
            self.running = True
            self._log(f"SYS: Offline voice is listening ({recogniser.name}, English). Say “open Chrome”, or “Jarvis, what is my name?”.")

            finished = threading.Event()

            def transcriber():
                while not self._stop.is_set() and not finished.is_set():
                    try:
                        audio = work.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    try:
                        text = recogniser.transcribe(audio)
                    except Exception as e:
                        self._log(f"SYS: Offline voice could not read that: {type(e).__name__}")
                        continue
                    if not is_noise(text):
                        self._heard(text)

            threading.Thread(target=transcriber, name="offline-stt", daemon=True).start()
            try:
                while not self._stop.is_set():
                    try:
                        blk, rate = q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if self._deaf.is_set() or self._muted():
                        det.speaking, det._buf, det._hot = False, [], 0
                        continue
                    for utt in det.feed(_resample(blk, rate)):
                        try:
                            work.put_nowait(utt)
                        except queue.Full:
                            pass
            finally:
                finished.set()
                stream.stop()
                stream.close()
        except Exception as e:
            self._log(f"SYS: Offline voice stopped: {type(e).__name__}: {str(e)[:120]}")
        finally:
            self.running = False

    # -- what to do with a sentence --------------------------------------------------------------------
    def _heard(self, text: str) -> None:
        addressed, rest = strip_wake(text)
        now = time.monotonic()
        self._log(f"SYS: Heard: “{text.strip()}”")
        if addressed and not rest:
            self._armed_until = now + _ARMED_FOR
            threading.Thread(target=self.speak, args=("Yes?",), daemon=True).start()
            return
        if addressed:
            request = rest
        elif now <= self._armed_until:
            request = rest
        elif self._is_command(text):
            request = text                       # "open chrome" needs no name
        else:
            if now - self._last_hint > 12:
                self._last_hint = now
                self._log("SYS: (I only act on “open …” / “close …”, or on a question that starts with “Jarvis”.)")
            return
        self._armed_until = 0.0
        threading.Thread(target=self._respond, args=(request,), daemon=True).start()

    def _respond(self, text: str) -> None:
        try:
            reply = self._on_text(text)
        except Exception as e:
            reply = f"Something went wrong: {e}"
        if reply:
            self.speak(". ".join(ln.strip(" -") for ln in reply.splitlines() if ln.strip())[:320])
