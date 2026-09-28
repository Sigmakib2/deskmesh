"""One-time terminal setup and human-readable pairing code."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import socket
from pathlib import Path

from .auth import load_key
from .config import load_config
from .discovery import discover


def key_from_code(code: str) -> bytes:
    clean = "".join(character for character in code.upper() if character not in " -")
    try:
        seed = base64.b32decode(clean + "=" * ((-len(clean)) % 8), casefold=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Pairing code has invalid characters") from exc
    if len(seed) != 16 or base64.b32encode(seed).decode("ascii").rstrip("=") != clean:
        raise ValueError("Pairing code must have 26 letters/digits")
    return seed + hashlib.sha256(b"deskmesh-pairing-v1\0" + seed).digest()[:16]


def code_from_key(key: bytes) -> str:
    if len(key) != 32 or key_from_code(base64.b32encode(key[:16]).decode("ascii").rstrip("=")) != key:
        raise ValueError("Existing key was not created by the setup wizard")
    compact = base64.b32encode(key[:16]).decode("ascii").rstrip("=")
    return "-".join(compact[index:index + 4] for index in range(0, len(compact), 4))


def new_pairing() -> tuple[str, bytes]:
    seed = secrets.token_bytes(16)
    key = key_from_code(base64.b32encode(seed).decode("ascii").rstrip("="))
    return code_from_key(key), key


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = result[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                addresses.add(ip)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))  # Select route; no packet is sent.
            ip = sock.getsockname()[0]
            if not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def _ask_role() -> str:
    while True:
        answer = input("This PC is [1] main/server or [2] other device? ").strip().lower()
        if answer in ("1", "main", "server", "primary"):
            return "primary"
        if answer in ("2", "other", "secondary", "client"):
            return "secondary"
        print("Enter 1 for the main PC or 2 for the other PC.")


def _ask_host() -> str:
    while True:
        answer = input("Main PC's LAN IPv4 address: ").strip()
        try:
            address = ipaddress.IPv4Address(answer)
            if address.is_unspecified or address.is_multicast or address.is_loopback:
                raise ValueError
            return str(address)
        except ValueError:
            print("Enter an IPv4 address such as 192.168.1.10, shown on the main PC.")


def _choose_main(port: int) -> dict:
    print("Looking for the main PC on the local network...")
    try:
        found = discover(port)
    except OSError:
        found = []
    if len(found) == 1:
        print(f"Found {found[0]['name']} at {found[0]['host']}.")
        return found[0]
    if found:
        for index, item in enumerate(found, 1):
            print(f"  [{index}] {item['name']} at {item['host']}")
        while True:
            answer = input("Choose a main PC number, or M to enter an address: ").strip().lower()
            if answer == "m":
                break
            if answer.isdigit() and 1 <= int(answer) <= len(found):
                return found[int(answer) - 1]
            print("Enter a listed number or M.")
    else:
        print("No main PC found automatically. You can enter its displayed address.")
    return {"host": _ask_host()}


def _write_key(path: Path, key: bytes) -> None:
    if path.exists():
        answer = input(f"{path} already exists. Replace it with the new pairing key? [y/N] ").strip().lower()
        if answer != "y":
            raise ValueError("Setup cancelled; existing key was left unchanged")
    path.write_text(key.hex() + "\n", encoding="ascii")


def setup_interactive(path: str = "deskmesh.json", *, reset: bool = False) -> None:
    target = Path(path)
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    existing = load_config(path if target.exists() else None)
    if existing.role and not reset:
        load_key(existing.key_file)
        print(f"Already configured as {existing.role}. Run the same command to start.")
        return
    print("\nDeskMesh first-time setup")
    role = _ask_role()
    key_path = Path(data.get("key_file", "deskmesh.key"))
    if role == "primary":
        code, key = new_pairing()
        _write_key(key_path, key)
        data.pop("connect", None)
        print("\nOn the other PC, enter this pairing code:")
        print(f"  {code}")
        addresses = local_ipv4_addresses()
        print("Main PC LAN address(es): " + (", ".join(addresses) if addresses else "run ipconfig to find IPv4 address"))
        print("Keep the pairing code private. The other PC will ask for it once.")
    else:
        main = _choose_main(int(data.get("discovery_port", 47662)))
        data["connect"] = main["host"]
        for field in ("control_port", "audio_port"):
            if field in main:
                data[field] = main[field]
        while True:
            try:
                key = key_from_code(input("Pairing code shown on main PC: ").strip())
                break
            except ValueError as exc:
                print(exc)
        _write_key(key_path, key)
    data["role"] = role
    data.setdefault("name", socket.gethostname())
    data.setdefault("key_file", str(key_path))
    # Verify before persisting. Existing advanced settings remain intact.
    temp = target.with_suffix(target.suffix + ".new")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        load_config(str(temp))
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Saved {target}. Next runs will use this role automatically.\n")
