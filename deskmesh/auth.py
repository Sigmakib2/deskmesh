"""Mutual challenge-response authentication for a shared secret."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import socket
from pathlib import Path

from .protocol import encode, receive


def generate_key(path: str) -> None:
    target = Path(path)
    with target.open("x", encoding="ascii") as file:
        file.write(secrets.token_hex(32) + "\n")


def load_key(path: str) -> bytes:
    try:
        key = bytes.fromhex(Path(path).read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read valid hex key from {path}; run generate-key and copy it to both PCs") from exc
    if len(key) != 32:
        raise ValueError("Key must contain 32 random bytes (64 hex characters)")
    return key


def tag(key: bytes, label: bytes, *parts: bytes) -> str:
    return hmac.new(key, label + b"\0" + b"\0".join(parts), hashlib.sha256).hexdigest()


def server_auth(sock: socket.socket, key: bytes) -> bytes:
    server_nonce = secrets.token_bytes(32)
    sock.sendall(encode({"type": "challenge", "nonce": server_nonce.hex()}))
    reply = receive(sock)
    if reply["type"] != "authenticate":
        raise ValueError("Authentication failed")
    try:
        client_nonce = bytes.fromhex(reply["nonce"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Authentication failed") from exc
    if len(client_nonce) != 32 or not hmac.compare_digest(reply.get("tag", ""), tag(key, b"client", server_nonce, client_nonce)):
        raise ValueError("Authentication failed")
    session = secrets.token_bytes(8)
    sock.sendall(encode({"type": "authenticated", "tag": tag(key, b"server", server_nonce, client_nonce, session), "session": session.hex()}))
    return session


def client_auth(sock: socket.socket, key: bytes) -> bytes:
    challenge = receive(sock)
    if challenge["type"] != "challenge":
        raise ValueError("Authentication failed")
    try:
        server_nonce = bytes.fromhex(challenge["nonce"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Authentication failed") from exc
    if len(server_nonce) != 32:
        raise ValueError("Authentication failed")
    client_nonce = secrets.token_bytes(32)
    sock.sendall(encode({"type": "authenticate", "nonce": client_nonce.hex(), "tag": tag(key, b"client", server_nonce, client_nonce)}))
    reply = receive(sock)
    if reply["type"] != "authenticated":
        raise ValueError("Authentication failed")
    try:
        session = bytes.fromhex(reply["session"])
    except (KeyError, ValueError) as exc:
        raise ValueError("Authentication failed") from exc
    if len(session) != 8 or not hmac.compare_digest(reply.get("tag", ""), tag(key, b"server", server_nonce, client_nonce, session)):
        raise ValueError("Authentication failed")
    return session
