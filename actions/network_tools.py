"""
network_tools — what is my connection doing: addresses, Wi-Fi details, ping, speed.

Everything here only reads or measures; nothing changes network settings
(use system_toggles for Wi-Fi / Bluetooth switches).
"""
import json
import platform
import re
import socket
import subprocess
import time
import urllib.request

_OS = platform.system()
_HIDE = {"creationflags": subprocess.CREATE_NO_WINDOW} if _OS == "Windows" else {}
_HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **_HIDE)
        return r.stdout.decode(errors="replace")
    except Exception:
        return ""


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))          # no packet is sent; this just picks the route
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def _public_info() -> dict:
    try:
        req = urllib.request.Request("https://ipwho.is/", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d if d.get("success") else {}
    except Exception:
        return {}


def _wifi_details() -> str:
    if _OS == "Windows":
        out = _run(["netsh", "wlan", "show", "interfaces"])
        def f(label):
            m = re.search(rf"^\s*{label}\s*:\s*(.+)$", out, re.M)
            return m.group(1).strip() if m else ""
        ssid, sig = f("SSID"), f("Signal")
        if not ssid:
            return "Not connected to Wi-Fi."
        extra = []
        if f("Radio type"):
            extra.append(f("Radio type"))
        rate = f("Receive rate \\(Mbps\\)")
        if rate:
            extra.append(f"{rate} Mbps link")
        return f"Wi-Fi network {ssid}, signal {sig or 'unknown'}" + (f" ({', '.join(extra)})" if extra else "") + "."
    if _OS == "Linux":
        ssid = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
        m = re.search(r"yes:(.+)", ssid)
        return f"Wi-Fi network {m.group(1).strip()}." if m else "Not connected to Wi-Fi."
    return "Wi-Fi details are not available on this system."


def _info() -> str:
    parts = [f"Local IP {_local_ip() or 'unknown'}."]
    pub = _public_info()
    if pub:
        parts.append(f"Public IP {pub.get('ip')} ({pub.get('city', '?')}, {pub.get('country', '?')}, "
                     f"provider {(pub.get('connection') or {}).get('isp', 'unknown')}).")
    else:
        parts.append("No internet reply, so no public IP.")
    parts.append(_wifi_details())
    return " ".join(parts)


def _ping(host: str) -> str:
    host = host.strip()
    if not host or not _HOST_RE.match(host):
        return "Give a valid host name or IP address to ping."
    flag = "-n" if _OS == "Windows" else "-c"
    out = _run(["ping", flag, "4", host], timeout=25)
    if _OS == "Windows":
        m = re.search(r"Average = (\d+)ms", out)
        loss = re.search(r"\((\d+)% loss\)", out)
    else:
        m = re.search(r"= [\d.]+/([\d.]+)/", out)
        loss = re.search(r"([\d.]+)% packet loss", out)
    if m:
        return f"{host} replied: average {m.group(1)} ms, {loss.group(1) if loss else '0'}% lost."
    if loss and loss.group(1) == "100":
        return f"{host} did not reply (100% lost)."
    return f"I could not ping {host}."


def _speed() -> str:
    """Download speed from Cloudflare's test endpoint, 25 MB, capped at 12 s.
    (The endpoint rejected a 12 MB request with HTTP 403 while accepting 2 MB and
    25 MB, so the size is one that is known to work.)"""
    import requests
    got, start = 0, time.perf_counter()
    try:
        with requests.get("https://speed.cloudflare.com/__down?bytes=25000000",
                          stream=True, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code != 200:
                return f"The speed test server refused the request (HTTP {r.status_code})."
            for chunk in r.iter_content(65536):
                got += len(chunk)
                if time.perf_counter() - start > 12:
                    break
    except Exception as e:
        return f"The speed test failed: {e}"
    secs = time.perf_counter() - start
    if got < 100_000 or secs <= 0:
        return "The speed test did not get enough data."
    t0 = time.perf_counter()
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=4).close()
        lat = f", latency about {round((time.perf_counter() - t0) * 1000)} ms"
    except Exception:
        lat = ""
    return f"Download speed about {got * 8 / secs / 1e6:.1f} Mbps{lat}."


def _wifi_list() -> str:
    if _OS != "Windows":
        return "Listing nearby networks is only supported on Windows here."
    out = _run(["netsh", "wlan", "show", "networks", "mode=bssid"])
    nets = re.findall(r"^SSID \d+ : (.*)$|^\s+Signal\s+:\s+(\d+)%", out, re.M)
    results, cur = {}, None
    for ssid, sig in nets:
        if ssid is not None and ssid != "":
            cur = ssid.strip()
        elif sig and cur:
            results[cur] = max(results.get(cur, 0), int(sig))
    if not results:
        return "No Wi-Fi networks found."
    top = sorted(results.items(), key=lambda kv: -kv[1])[:8]
    return "Nearby networks: " + ", ".join(f"{n} ({s}%)" for n, s in top) + "."


def _dns_check() -> str:
    t0 = time.perf_counter()
    try:
        socket.getaddrinfo("example.com", 80)
        return f"DNS is working ({round((time.perf_counter() - t0) * 1000)} ms)."
    except Exception:
        return "DNS lookup failed — the internet may be down or DNS is misconfigured."


def network_tools(parameters: dict, player=None) -> str:
    action = str(parameters.get("action", "info")).strip().lower()
    if action in ("info", "ip", "status", "wifi_info"):
        return _info()
    if action == "ping":
        return _ping(str(parameters.get("host", "")))
    if action in ("speed", "speedtest", "speed_test"):
        return _speed()
    if action in ("wifi_list", "networks", "scan"):
        return _wifi_list()
    if action in ("dns", "dns_check"):
        return _dns_check()
    if action in ("check", "internet", "online"):
        dns = _dns_check()
        return f"Internet is reachable. {dns}" if dns.startswith("DNS is working") else dns
    return "Network actions are: info, ping, speed, wifi_list, dns, check."


TOOL = {
    "name": "network_tools",
    "description": (
        "Network information and tests. Use for 'what is my IP', 'what Wi-Fi am I on / "
        "how strong is it', 'ping google.com', 'test my internet speed', 'which "
        "Wi-Fi networks are nearby', 'is my internet working'. Read-only — to switch "
        "Wi-Fi on or off use system_toggles."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING",
                       "description": "info | ping | speed | wifi_list | dns | check (default info)"},
            "host": {"type": "STRING", "description": "For ping: host name or IP, e.g. google.com"},
        },
        "required": [],
    },
    "handler": network_tools,
}
