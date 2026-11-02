"""WiFi for the robot: the brain's side of it.

Finding the brain: on a network with no address in its firmware (a phone's hotspot, a friend's WiFi) the robot
broadcasts ASK on PORT, and every brain that hears it answers with the port of its WebSocket. The robot still has to
give the token before the brain will talk to it.

Which networks the robot knows is kept on the robot; the live view's Settings tab edits them through wifi_message.
A network this computer has joined can be added with the password it saved (saved_message), which goes from the
computer to the robot and nowhere else: not to the page, not to the log.
"""

import re
import socket
import subprocess
import sys
import threading
import time

PORT = 8767
ASK = b"rockybrain?"
ANSWER = b"rockybrain! %d"


def answer(packet: bytes, ws_port: int) -> bytes | None:
    """What to say back to a packet; None for anything but the robot's question."""
    return ANSWER % ws_port if packet.strip() == ASK else None


def join_failure(ssid: str, reason: object, has_password: bool = True) -> str:
    """Why the robot couldn't join a network, from the WiFi driver's reason code."""
    if not has_password:
        why = "it has no password saved; forget it and add it again"
    elif reason in (2, 15, 202, 204):
        why = "the password is wrong, or the router refused it"
    elif reason in (201, 210, 211, 212):
        why = "the router's 2.4 GHz settings or the saved password don't suit it"
    elif reason in (203, 205):
        why = "the router wouldn't let it connect"
    else:
        why = "no reason given; check the password"
    return f"Couldn't join {ssid}: {why} (reason {reason})"


def wifi_message(payload: dict) -> dict:
    """The live view's request as a message for the robot. Raises ValueError for one it shouldn't send.
    The password goes only to the robot: nothing here logs or keeps it."""
    op = payload.get("op")
    if op == "scan":
        return {"type": "wifi_scan"}
    ssid = payload.get("ssid")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
        raise ValueError("a network's name is 1 to 32 characters")
    if op in ("use", "forget"):
        return {"type": f"wifi_{op}", "ssid": ssid}
    if op != "add":
        raise ValueError("op: scan, add, use or forget")
    password = payload.get("password", "")
    if not isinstance(password, str) or (password and not 8 <= len(password.encode()) <= 63):
        raise ValueError("a WiFi password is 8 to 63 characters (empty for an open network)")
    return {"type": "wifi_add", "ssid": ssid, "password": password}


def _run(args: list[str]) -> str:
    """A system tool's output, "" if it's missing or fails. Its output can hold a password: never print it."""
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        return subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=10,
                              creationflags=flags).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _nm_connections() -> dict[str, str]:
    """NetworkManager's WiFi connections: network name -> connection name (they can differ)."""
    found = {}
    for line in _run(["nmcli", "-t", "-e", "no", "-f", "NAME,TYPE", "connection", "show"]).splitlines():
        name, _, kind = line.rpartition(":")
        if kind == "802-11-wireless" and name:
            ssid = _run(["nmcli", "-e", "no", "-g", "802-11-wireless.ssid", "connection", "show", "id", name]).strip()
            found[ssid or name] = name
    return found


def _read_saved_networks() -> set[str]:
    if sys.platform == "win32":
        return {m.strip() for m in re.findall(r"All User Profile\s*:\s*(.+)", _run(["netsh", "wlan", "show", "profiles"]))}
    if sys.platform.startswith("linux"):
        return set(_nm_connections())
    return set()


SAVED_CACHE_S = 60
_saved: tuple[float, set[str]] | None = None


def saved_networks() -> set[str]:
    """The networks this computer has saved, names only (for /status, so not asked every poll)."""
    global _saved
    if _saved is None or time.time() - _saved[0] > SAVED_CACHE_S:
        _saved = (time.time(), _read_saved_networks())
    return _saved[1]


def saved_password(ssid: str) -> str | None:
    """The password this computer saved for a network: "" for an open one, None if it has none it can read."""
    if sys.platform == "win32":
        out = _run(["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"])
        key = re.search(r"Key Content\s*:\s*(.*)", out)
        if key:
            return key.group(1).strip()
        return "" if re.search(r"Security key\s*:\s*Absent", out) else None
    if sys.platform.startswith("linux"):
        name = _nm_connections().get(ssid)
        if name is None:
            return None
        psk = _run(["nmcli", "-s", "-e", "no", "-g", "802-11-wireless-security.psk", "connection", "show", "id", name])
        if psk.strip():
            return psk.rstrip("\n")
        mgmt = _run(["nmcli", "-e", "no", "-g", "802-11-wireless-security.key-mgmt", "connection", "show", "id", name])
        return "" if not mgmt.strip() else None  # secured, but it wouldn't say: a permission, or a keyring
    return None


def saved_message(ssid: object) -> dict:
    """wifi_message's "add", with the password this computer saved. Runs system tools: not on the event loop."""
    if not isinstance(ssid, str) or not ssid:
        raise ValueError("a network's name is 1 to 32 characters")
    password = saved_password(ssid)
    if password is None:
        raise ValueError(f"this computer has no saved password it can read for {ssid[:32]}")
    return wifi_message({"op": "add", "ssid": ssid, "password": password})


def serve(ws_port: int) -> None:
    """Answer the robot's question from now on (a background thread)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", PORT))
    except OSError as e:
        print(f"(the robot can't search for this brain: port {PORT}: {e})")
        return

    def run() -> None:
        while True:
            try:
                packet, sender = sock.recvfrom(64)
                reply = answer(packet, ws_port)
                if reply is not None:
                    sock.sendto(reply, sender)
            except OSError:
                pass  # a network going away; keep listening

    threading.Thread(target=run, name="finder", daemon=True).start()
