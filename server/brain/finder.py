"""WiFi for the robot: the brain's side of it.

Finding the brain: on a network with no address in its firmware (a phone's hotspot, a friend's WiFi) the robot
broadcasts ASK on PORT, and every brain that hears it answers with the port of its WebSocket. The robot still has to
give the token before the brain will talk to it.

Which networks the robot knows is kept on the robot; the live view's Settings tab edits them through wifi_message.
"""

import socket
import threading

PORT = 8767
ASK = b"rockybrain?"
ANSWER = b"rockybrain! %d"


def answer(packet: bytes, ws_port: int) -> bytes | None:
    """What to say back to a packet; None for anything but the robot's question."""
    return ANSWER % ws_port if packet.strip() == ASK else None


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
