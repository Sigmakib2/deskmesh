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
from .pairing import Exchange, short_code, unwrap_key
from .protocol import encode, receive

PAIRING_WAIT_SECONDS = 180.0  # The person at the main PC has to read and confirm a code.


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


def _confirm(question: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Answer y or n.")


def pair_with_primary(host: str, control_port: int, name: str) -> tuple[bytes, str]:
    """Obtain the shared key from a running main PC without typing anything.

    Both PCs print the same four digits. A man in the middle cannot make them
    agree, so a mismatch means the person pairing should decline.
    """
    with socket.create_connection((host, control_port), timeout=10.0) as sock:
        sock.settimeout(10.0)
        exchange = Exchange()
        sock.sendall(encode({"type": "pair_request", "public": exchange.public_hex, "name": name}))
        offer = receive(sock)
        if offer["type"] == "pair_reject":
            raise ValueError(offer.get("reason") or "The main PC refused to pair")
        if offer["type"] != "pair_offer":
            raise ValueError("The main PC did not offer to pair")
        shared = exchange.shared(offer["public"])
        code = short_code(shared, exchange.public_hex, offer["public"])
        print(f"\n  Pairing with {offer['name']}.")
        print(f"  This code must match the one shown on {offer['name']}:  {code}")
        print(f"  Confirm it there to continue. Waiting up to {int(PAIRING_WAIT_SECONDS / 60)} minutes...\n")
        sock.settimeout(PAIRING_WAIT_SECONDS)
        reply = receive(sock)
        if reply["type"] == "pair_reject":
            raise ValueError(reply.get("reason") or "The main PC declined")
        if reply["type"] != "pair_accept":
            raise ValueError("The main PC sent an unexpected pairing reply")
        return unwrap_key(shared, reply["key"], reply["tag"]), offer["name"]


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


def _detect_role(discovery_port: int) -> tuple[str, dict | None]:
    """Decide the role from what is already on the network, then confirm it.

    Discovery uses broadcasts, which some networks drop, so a wrong guess has to
    be correctable: the answer decides which PC's keyboard everyone ends up
    using, and that is worth one keystroke.
    """
    print("Looking for another DeskMesh on this network...")
    try:
        found = discover(discovery_port)
    except OSError:
        found = []
    if found:
        main = found[0] if len(found) == 1 else None
        if main is None:
            for index, item in enumerate(found, 1):
                print(f"  [{index}] {item['name']} at {item['host']}")
            while True:
                answer = input("Which PC should control this one? Number, or N for none: ").strip().lower()
                if answer == "n":
                    break
                if answer.isdigit() and 1 <= int(answer) <= len(found):
                    main = found[int(answer) - 1]
                    break
                print("Enter a listed number or N.")
        if main is not None:
            print(f"Found {main['name']} at {main['host']}.")
            if _confirm(f"Let {main['name']}'s keyboard and mouse control this PC?"):
                return "secondary", main
            return "primary", None
    else:
        print("No other DeskMesh is running here yet.")
    if _confirm("Share this PC's keyboard, mouse and headset with another PC?"):
        return "primary", None
    return "secondary", None


def _pair_or_ask_for_code(host: str, control_port: int, name: str) -> bytes:
    """Pair automatically, falling back to the typed code if that cannot work."""
    try:
        key, peer_name = pair_with_primary(host, control_port, name)
        print(f"Paired with {peer_name}.")
        return key
    except (OSError, ConnectionError, ValueError) as exc:
        print(f"\nAutomatic pairing did not complete: {exc}")
    if not _confirm("Enter the backup pairing code from the main PC instead?"):
        raise ValueError("Pairing was not completed; run setup again when the main PC is running")
    while True:
        try:
            return key_from_code(input("Backup pairing code from the main PC: ").strip())
        except ValueError as exc:
            print(exc)


def _write_key(path: Path, key: bytes) -> None:
    if path.exists():
        answer = input(f"{path} already exists. Replace it with the new pairing key? [y/N] ").strip().lower()
        if answer != "y":
            raise ValueError("Setup cancelled; existing key was left unchanged")
    path.write_text(key.hex() + "\n", encoding="ascii")


def setup_interactive(path: str = "deskmesh.json", *, reset: bool = False, quiet: bool = False) -> None:
    target = Path(path)
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    existing = load_config(path if target.exists() else None)
    if existing.role and not reset:
        load_key(existing.key_file)
        if not quiet:
            print(f"Already configured as {existing.role}. Run the same command to start.")
        return
    print("\nDeskMesh setup")
    name = data.get("name") or socket.gethostname()
    role, main = _detect_role(int(data.get("discovery_port", 47662)))
    key_path = Path(data.get("key_file", "deskmesh.key"))
    if role == "primary":
        code, key = new_pairing()
        _write_key(key_path, key)
        data.pop("connect", None)
        data["paired"] = False  # Open the pairing window for the other PC.
        addresses = local_ipv4_addresses()
        print("\nThis PC will share its keyboard, mouse and headset.")
        print("  LAN address: " + (", ".join(addresses) if addresses else "run ipconfig to find the IPv4 address"))
        print("  Run DeskMesh on the other PC now; it will find this one and ask to pair.")
        print(f"  Backup pairing code, only needed if that fails:  {code}")
    else:
        if main is None:
            main = {"host": _ask_host()}
        data["connect"] = main["host"]
        for field in ("control_port", "audio_port"):
            if field in main:
                data[field] = main[field]
        control_port = int(data.get("control_port", 47660))
        key = _pair_or_ask_for_code(main["host"], control_port, name)
        _write_key(key_path, key)
    data["role"] = role
    data.setdefault("name", name)
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
