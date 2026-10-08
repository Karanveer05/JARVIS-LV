#system_toggles.py
"""
On / off / status for the basic system switches: Wi-Fi, Bluetooth, airplane
mode, the microphone, and the Caps / Num / Scroll Lock keys — plus battery and
a one-shot "how is everything set" readout.

Why this is a separate tool from computer_settings
    computer_settings only FLIPS things ("toggle_wifi"), so "turn Bluetooth on"
    would switch it off when it was already on, and nothing can be asked what
    state a switch is in. Every action here takes an explicit state — on, off,
    toggle or status — reads the current value first, and reports what it found.

How the radios are driven (Windows)
    Windows.Devices.Radios — the same API the Settings quick toggles use. It
    needs no administrator rights, unlike Disable-NetAdapter, and it switches
    the radio rather than the network adapter, so Wi-Fi comes back cleanly.
"""
import ctypes
import os
import platform
import subprocess
import sys

from core import confirm
from core.undo import push_undo

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"
_WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW} if _OS == "Windows" else {}

_ON_WORDS  = ("on", "enable", "enabled", "true", "yes", "start", "1")
_OFF_WORDS = ("off", "disable", "disabled", "false", "no", "stop", "0")


# ── Windows radios (Wi-Fi, Bluetooth) ────────────────────────────────────────
# Kind and state come in through environment variables, never spliced into the
# script text, so nothing the model says can become PowerShell.
_PS_RADIO = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
if ($access -ne 'Allowed') { "DENIED|$access"; exit 0 }
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$kind  = $env:JV_KIND
$state = $env:JV_STATE
$found = 0
foreach ($r in $radios) {
    if ($kind -ne 'All' -and [string]$r.Kind -ne $kind) { continue }
    $found++
    if ($state -eq 'On' -or $state -eq 'Off') {
        $res = Await ($r.SetStateAsync([Windows.Devices.Radios.RadioState]$state)) ([Windows.Devices.Radios.RadioAccessStatus])
        if ($res -ne 'Allowed') { "DENIED|$res"; exit 0 }
    }
    "RADIO|$($r.Kind)|$($r.State)"
}
if ($found -eq 0) { "NONE" }
"""


def _radios(kind: str, state: str = "") -> tuple[list[tuple[str, str]], str]:
    """Run the radio script. Returns ([(kind, state)], problem). `problem` is ''
    on success. `state` '' only reads; 'On'/'Off' sets and returns the new state."""
    env = dict(os.environ, JV_KIND=kind, JV_STATE=state)
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_RADIO],
            capture_output=True, text=True, timeout=25, env=env, **_WIN_HIDE,
        )
    except subprocess.TimeoutExpired:
        return [], "Windows did not answer in time."
    except Exception as e:
        return [], f"could not run PowerShell: {e}"

    found, problem = [], ""
    for line in out.stdout.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "RADIO" and len(parts) == 3:
            found.append((parts[1], parts[2]))
        elif parts[0] == "DENIED":
            problem = f"Windows refused ({parts[1] if len(parts) > 1 else 'access denied'})."
        elif parts[0] == "NONE":
            problem = f"this computer has no {kind} radio."
    if not found and not problem:
        problem = (out.stderr.strip().splitlines() or ["no radio information returned"])[-1]
    return found, problem


# ── Non-Windows fallbacks for the two radios ─────────────────────────────────
def _other_os_get(kind: str) -> bool | None:
    try:
        if kind == "WiFi":
            if _OS == "Linux":
                return "enabled" in subprocess.run(["nmcli", "radio", "wifi"],
                                                   capture_output=True, text=True).stdout
            if _OS == "Darwin":
                from actions.computer_settings import _get_macos_wifi_interface
                r = subprocess.run(["networksetup", "-getairportpower", _get_macos_wifi_interface()],
                                   capture_output=True, text=True)
                return "On" in r.stdout
        if kind == "Bluetooth":
            if _OS == "Linux":
                r = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True)
                return "Powered: yes" in r.stdout
            if _OS == "Darwin":
                r = subprocess.run(["blueutil", "--power"], capture_output=True, text=True)
                return r.stdout.strip() == "1"
    except Exception:
        pass
    return None


def _other_os_set(kind: str, on: bool) -> bool:
    try:
        if kind == "WiFi":
            if _OS == "Linux":
                return subprocess.run(["nmcli", "radio", "wifi", "on" if on else "off"],
                                      capture_output=True).returncode == 0
            if _OS == "Darwin":
                from actions.computer_settings import _get_macos_wifi_interface
                return subprocess.run(["networksetup", "-setairportpower",
                                       _get_macos_wifi_interface(), "on" if on else "off"],
                                      capture_output=True).returncode == 0
        if kind == "Bluetooth":
            if _OS == "Linux":
                return subprocess.run(["bluetoothctl", "power", "on" if on else "off"],
                                      capture_output=True).returncode == 0
            if _OS == "Darwin":
                return subprocess.run(["blueutil", "--power", "1" if on else "0"],
                                      capture_output=True).returncode == 0
    except Exception:
        pass
    return False


def _radio_is_on(kind: str) -> tuple[bool | None, str]:
    """(is_on, problem). is_on is None when it cannot be read."""
    if _OS == "Windows":
        found, problem = _radios(kind)
        if problem:
            return None, problem
        return any(s == "On" for _, s in found), ""
    state = _other_os_get(kind)
    if state is None:
        return None, f"I cannot read {kind} on this system."
    return state, ""


def _radio_set(kind: str, on: bool) -> str:
    """Set a radio and verify. Returns '' on success, else the problem."""
    if _OS == "Windows":
        found, problem = _radios(kind, "On" if on else "Off")
        if problem:
            return problem
        if not all(s == ("On" if on else "Off") for _, s in found):
            return "the switch did not change."
        return ""
    return "" if _other_os_set(kind, on) else f"I cannot change {kind} on this system."


_NAMES = {"WiFi": "Wi-Fi", "Bluetooth": "Bluetooth"}


def _do_radio(kind: str, want: str) -> str:
    """want: on | off | toggle | status."""
    name = _NAMES[kind]
    is_on, problem = _radio_is_on(kind)
    if is_on is None:
        return f"I could not read {name}: {problem}"
    if want == "status":
        return f"{name} is {'on' if is_on else 'off'}."
    target = (not is_on) if want == "toggle" else (want == "on")
    if target == is_on:
        return f"{name} is already {'on' if is_on else 'off'}."

    # Switching Wi-Fi off cuts the connection this assistant is talking over, so
    # it cannot be undone by voice afterwards. That one goes through the HUD
    # gate; everything else is done at once and can be taken back with undo.
    if kind == "WiFi" and not target:
        if confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return confirm.request(
            key="wifi_off", title="Turn Wi-Fi off",
            detail="This will disconnect JARVIS from the internet until Wi-Fi is turned back on.",
            run=lambda: _set_and_report(kind, False, undoable=False),
        )
    return _set_and_report(kind, target, undoable=True)


def _set_and_report(kind: str, on: bool, undoable: bool) -> str:
    name = _NAMES[kind]
    problem = _radio_set(kind, on)
    if problem:
        return f"I could not turn {name} {'on' if on else 'off'}: {problem}"
    if undoable:
        push_undo(f"{name} turned {'on' if on else 'off'}",
                  lambda: _radio_set(kind, not on) or f"{name} back {'off' if on else 'on'}.")
    return f"{name} is now {'on' if on else 'off'}."


def _do_airplane(want: str) -> str:
    if _OS != "Windows":
        return "Airplane mode is only supported on Windows here."
    found, problem = _radios("All")
    if problem:
        return f"I could not read the radios: {problem}"
    any_on = any(s == "On" for _, s in found)
    if want == "status":
        return ("Airplane mode is off — at least one radio is on."
                if any_on else "Airplane mode is on — every radio is off.")
    target_airplane = (any_on) if want == "toggle" else (want == "on")
    if target_airplane == (not any_on):
        return f"Airplane mode is already {'on' if target_airplane else 'off'}."

    if target_airplane:
        if confirm.pending_title():
            return ("There is already a confirmation waiting on screen. "
                    "Ask the user to answer that one first.")
        return confirm.request(
            key="airplane_on", title="Turn airplane mode on",
            detail="This switches off Wi-Fi and Bluetooth, which disconnects JARVIS from the internet.",
            run=lambda: _airplane_set(True),
        )
    return _airplane_set(False)


def _airplane_set(airplane: bool) -> str:
    found, problem = _radios("All", "Off" if airplane else "On")
    if problem:
        return f"I could not change airplane mode: {problem}"
    return f"Airplane mode is now {'on' if airplane else 'off'}."


# ── Lock keys ────────────────────────────────────────────────────────────────
_LOCK_KEYS = {"caps_lock": (0x14, "Caps Lock"),
              "num_lock": (0x90, "Num Lock"),
              "scroll_lock": (0x91, "Scroll Lock")}


def _lock_state(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetKeyState(vk) & 1)


def _do_lock_key(target: str, want: str) -> str:
    if _OS != "Windows":
        return "Lock-key control is only supported on Windows here."
    vk, name = _LOCK_KEYS[target]
    current = _lock_state(vk)
    if want == "status":
        return f"{name} is {'on' if current else 'off'}."
    desired = (not current) if want == "toggle" else (want == "on")
    if desired == current:
        return f"{name} is already {'on' if current else 'off'}."
    KEYEVENTF_KEYUP = 0x0002
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    if _lock_state(vk) != desired:
        return f"I pressed {name} but it did not change."
    push_undo(f"{name} turned {'on' if desired else 'off'}",
              lambda: (_do_lock_key(target, "off" if desired else "on"), f"{name} back.")[1])
    return f"{name} is now {'on' if desired else 'off'}."


# ── Microphone ───────────────────────────────────────────────────────────────
def _mic_endpoint():
    # QueryInterface, not ctypes.cast: cast() makes a second pointer without a
    # matching AddRef, so both get Released and the heap is corrupted. The crash
    # then shows up much later, in unrelated code (a network call segfaulted).
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetMicrophone()
    if dev is None:
        raise RuntimeError("no microphone found")
    unknown = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return unknown.QueryInterface(IAudioEndpointVolume)


def _do_mic(want: str) -> str:
    if _OS != "Windows":
        return "Microphone mute is only supported on Windows here."
    try:
        ep = _mic_endpoint()
        muted = bool(ep.GetMute())
        if want == "status":
            return f"The microphone is {'muted' if muted else 'live'}."
        target = (not muted) if want == "toggle" else (want == "off")  # "off" = muted
        if target == muted:
            return f"The microphone is already {'muted' if muted else 'live'}."
        ep.SetMute(1 if target else 0, None)
        push_undo(f"microphone {'muted' if target else 'unmuted'}",
                  lambda: (_mic_endpoint().SetMute(0 if target else 1, None),
                           f"Microphone {'live' if target else 'muted'} again.")[1])
        return f"The microphone is now {'muted' if target else 'live'}."
    except Exception as e:
        return f"I could not change the microphone: {e}"


# ── Battery ──────────────────────────────────────────────────────────────────
def _battery() -> str:
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return "This computer has no battery — it is a desktop or the battery cannot be read."
    state = "charging" if b.power_plugged else "on battery"
    left = ""
    if not b.power_plugged and b.secsleft and b.secsleft > 0:
        h, m = divmod(b.secsleft // 60, 60)
        left = f", about {h} hours {m} minutes left" if h else f", about {m} minutes left"
    return f"Battery is at {round(b.percent)}%, {state}{left}."


# ── Everything at once ───────────────────────────────────────────────────────
def _all_status() -> str:
    parts = []
    for kind in ("WiFi", "Bluetooth"):
        parts.append(_do_radio(kind, "status"))
    if _OS == "Windows":
        parts.append(_do_mic("status"))
        locks = [f"{n} {'on' if _lock_state(vk) else 'off'}" for vk, n in _LOCK_KEYS.values()]
        parts.append("Keys: " + ", ".join(locks) + ".")
    parts.append(_battery())
    return " ".join(parts)


# ── Dispatch ─────────────────────────────────────────────────────────────────
_TARGETS = {
    "wifi": "WiFi", "wi-fi": "WiFi", "wi_fi": "WiFi", "wlan": "WiFi", "internet": "WiFi",
    "bluetooth": "Bluetooth", "bt": "Bluetooth",
    "airplane_mode": "airplane", "airplane": "airplane", "flight_mode": "airplane",
    "aeroplane_mode": "airplane",
    "microphone": "mic", "mic": "mic", "mic_mute": "mic",
    "caps_lock": "caps_lock", "capslock": "caps_lock", "caps": "caps_lock",
    "num_lock": "num_lock", "numlock": "num_lock",
    "scroll_lock": "scroll_lock", "scrolllock": "scroll_lock",
    "battery": "battery", "all": "all", "everything": "all", "system": "all",
}


def _norm_state(raw) -> str:
    s = str(raw or "").strip().lower()
    if s in _ON_WORDS:
        return "on"
    if s in _OFF_WORDS:
        return "off"
    if s in ("toggle", "flip", "switch"):
        return "toggle"
    return "status" if s in ("", "status", "check", "state", "is it on") else ""


def system_toggles(parameters: dict, player=None) -> str:
    target_raw = str(parameters.get("target", "")).strip().lower().replace(" ", "_")
    target = _TARGETS.get(target_raw)
    if target is None:
        return (f"I don't have a switch called '{target_raw}'. I can control: "
                "wifi, bluetooth, airplane_mode, microphone, caps_lock, num_lock, "
                "scroll_lock, and read battery or everything.")
    want = _norm_state(parameters.get("state", "status"))
    if not want:
        return "State must be on, off, toggle or status."
    if player:
        try:
            player.write_log(f"[Toggle] {target} → {want}")
        except Exception:
            pass

    if target in ("WiFi", "Bluetooth"):
        return _do_radio(target, want)
    if target == "airplane":
        return _do_airplane(want)
    if target == "mic":
        # For the microphone, "off" means muted, "on" means live.
        return _do_mic(want)
    if target in _LOCK_KEYS:
        return _do_lock_key(target, want)
    if target == "battery":
        return _battery()
    return _all_status()


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "system_toggles",
    "description": (
        "Turn a system switch ON or OFF, flip it, or read its state. Use for: "
        "Wi-Fi, Bluetooth, airplane mode, the microphone (off = muted), and the "
        "Caps Lock / Num Lock / Scroll Lock keys; also reports battery and, with "
        "target='all', the state of everything. Prefer this over computer_settings "
        "for Wi-Fi — it knows whether Wi-Fi is already on, so 'turn Bluetooth on' "
        "never turns it off. Turning Wi-Fi off or airplane mode on puts a "
        "confirmation on the user's screen and does NOT happen until they press "
        "it — never claim those are done. Everything else is immediate and can "
        "be reversed with the `undo` tool."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {
                "type": "STRING",
                "description": ("wifi | bluetooth | airplane_mode | microphone | "
                                "caps_lock | num_lock | scroll_lock | battery | all"),
            },
            "state": {
                "type": "STRING",
                "description": "on | off | toggle | status (default status)",
            },
        },
        "required": ["target"],
    },
    "handler": system_toggles,
}
