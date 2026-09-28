"""Optional LAN discovery; authentication still happens on the TCP channel."""

from __future__ import annotations

import json
import logging
import secrets
import socket
import threading
import time

REQUEST = b"DESKMESH_DISCOVER_V1\0"
RESPONSE = b"DESKMESH_HERE_V1\0"


def respond(stop: threading.Event, bind: str, port: int, name: str, control_port: int, audio_port: int) -> None:
    payload = json.dumps({"name": name, "control_port": control_port, "audio_port": audio_port}, separators=(",", ":")).encode("utf-8")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((bind, port))
            sock.settimeout(0.5)
            while not stop.is_set():
                try:
                    message, address = sock.recvfrom(128)
                except socket.timeout:
                    continue
                if len(message) == len(REQUEST) + 8 and message.startswith(REQUEST):
                    sock.sendto(RESPONSE + message[-8:] + payload, address)
    except OSError as exc:
        logging.warning("LAN discovery unavailable: %s; enter the main PC address manually", exc)


def discover(port: int, timeout: float = 2.0) -> list[dict]:
    nonce = secrets.token_bytes(8)
    found: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(0.25)
        deadline = time.monotonic() + timeout
        last_send = 0.0
        while time.monotonic() < deadline:
            if time.monotonic() - last_send >= 0.6:
                try:
                    sock.sendto(REQUEST + nonce, ("255.255.255.255", port))
                except OSError:
                    return []
                last_send = time.monotonic()
            try:
                message, address = sock.recvfrom(512)
            except socket.timeout:
                continue
            if not message.startswith(RESPONSE + nonce):
                continue
            try:
                data = json.loads(message[len(RESPONSE) + 8:].decode("utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not 1 <= len(data["name"].encode("utf-8")) <= 64:
                    continue
                if any(type(data.get(field)) is not int or not 1 <= data[field] <= 65535 for field in ("control_port", "audio_port")):
                    continue
                data["host"] = address[0]
                found[address[0]] = data
            except (UnicodeError, json.JSONDecodeError):
                continue
    return sorted(found.values(), key=lambda item: item["host"])
