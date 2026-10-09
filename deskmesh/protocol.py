"""Bounded length-prefixed JSON control protocol."""

from __future__ import annotations

import json
import socket
import struct

MAX_MESSAGE = 4096
PUBLIC_HEX_LENGTH = 512  # A 2048-bit Diffie-Hellman public value.
TYPES = {"challenge", "authenticate", "authenticated", "hello", "heartbeat", "keyboard", "mouse_move", "mouse_button", "mouse_wheel", "release_all", "disconnect", "connect", "pair_request", "pair_offer", "pair_accept", "pair_reject"}


def validate(message: object) -> dict:
    if not isinstance(message, dict) or message.get("type") not in TYPES:
        raise ValueError("Unsupported control message")
    kind = message["type"]
    if kind == "keyboard":
        if type(message.get("vk")) is not int or not 1 <= message["vk"] <= 255 or type(message.get("pressed")) is not bool or type(message.get("extended")) is not bool:
            raise ValueError("Invalid keyboard event")
    elif kind == "mouse_move":
        if any(type(message.get(k)) is not int or not -32768 <= message[k] <= 32767 for k in ("dx", "dy")):
            raise ValueError("Invalid mouse movement")
    elif kind == "mouse_button":
        if message.get("button") not in ("left", "right", "middle") or type(message.get("pressed")) is not bool:
            raise ValueError("Invalid mouse button")
    elif kind == "mouse_wheel":
        if type(message.get("delta")) is not int or not -32768 <= message["delta"] <= 32767:
            raise ValueError("Invalid mouse wheel")
    elif kind in ("challenge", "authenticate", "authenticated"):
        for value in message.values():
            if not isinstance(value, str) or len(value) > 128:
                raise ValueError("Invalid authentication message")
    elif kind == "hello":
        if not isinstance(message.get("name"), str) or not 1 <= len(message["name"].encode("utf-8")) <= 64:
            raise ValueError("Invalid peer name")
    elif kind in ("pair_request", "pair_offer"):
        if not _is_hex(message.get("public"), PUBLIC_HEX_LENGTH):
            raise ValueError("Invalid pairing public value")
        if not isinstance(message.get("name"), str) or not 1 <= len(message["name"].encode("utf-8")) <= 64:
            raise ValueError("Invalid peer name")
    elif kind == "pair_accept":
        if not _is_hex(message.get("key"), 64) or not _is_hex(message.get("tag"), 64):
            raise ValueError("Invalid pairing acceptance")
    elif kind == "pair_reject":
        reason = message.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 200:
            raise ValueError("Invalid pairing rejection")
    return message


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(character in "0123456789abcdefABCDEF" for character in value)


def encode(message: dict) -> bytes:
    validate(message)
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_MESSAGE:
        raise ValueError("Control message too large")
    return struct.pack("!I", len(body)) + body


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("Peer disconnected")
        chunks.extend(chunk)
    return bytes(chunks)


def receive(sock: socket.socket) -> dict:
    size = struct.unpack("!I", read_exact(sock, 4))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("Invalid control frame length")
    try:
        return validate(json.loads(read_exact(sock, size).decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid control JSON") from exc
