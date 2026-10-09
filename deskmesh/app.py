"""DeskMesh node lifecycle and command-line interface."""

from __future__ import annotations

import argparse
import atexit
import logging
import logging.handlers
import platform
import queue
import socket
import sys
import threading
import time
from pathlib import Path

from .audio import list_devices, receive_audio, send_audio
from .auth import client_auth, generate_key, load_key, server_auth
from .config import Config, load_config, update_config_file
from .discovery import respond
from .pairing import Exchange, short_code, wrap_key
from .protocol import encode, receive
from .setup import code_from_key, setup_interactive
from .state import ActiveState, PressedState


def require_windows() -> None:
    if platform.system() != "Windows":
        raise RuntimeError("Input and system audio require Windows 10/11")


def configure_logging(debug: bool) -> None:
    """Send every log record through a queue so no caller blocks on the console.

    Low-level input hook callbacks must return within the Windows
    LowLevelHooksTimeout (300 ms by default) or Windows silently removes the
    hook. A console write can exceed that, so the callbacks only enqueue and a
    listener thread does the writing.
    """
    records: queue.SimpleQueue = queue.SimpleQueue()
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    listener = logging.handlers.QueueListener(records, console, respect_handler_level=True)
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root.addHandler(logging.handlers.QueueHandler(records))
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    listener.start()
    atexit.register(listener.stop)


class Session:
    def __init__(self, sock: socket.socket, config: Config, key: bytes, session_id: bytes) -> None:
        self.sock = sock
        if sock.family == socket.AF_INET:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.config = config
        self.key = key
        self.session_id = session_id
        self.stop = threading.Event()
        self.send_lock = threading.Lock()
        self.last_heartbeat = time.monotonic()
        self.audio_thread: threading.Thread | None = None

    def send(self, message: dict) -> None:
        with self.send_lock:
            self.sock.sendall(encode(message))

    def heartbeat(self) -> None:
        while not self.stop.wait(self.config.heartbeat_seconds):
            if time.monotonic() - self.last_heartbeat > self.config.timeout_seconds:
                logging.warning("No heartbeat from peer for %.1fs; dropping the session", self.config.timeout_seconds)
                self.stop.set()
                # Unblock the reader, which is parked in a blocking receive().
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            try:
                self.send({"type": "heartbeat"})
            except OSError:
                self.stop.set()
                return

    def start_heartbeat(self) -> None:
        threading.Thread(target=self.heartbeat, daemon=True, name="heartbeat").start()

    def start_audio(self, target, *args) -> None:
        def run() -> None:
            try:
                target(self.stop, *args)
            except OSError as exc:
                logging.error("Audio unavailable: %s. Input still works; restart DeskMesh to retry audio", exc)
            except Exception:
                logging.exception("Audio stopped; input connection remains available. Check devices with 'devices'")

        self.audio_thread = threading.Thread(target=run, daemon=True, name="audio")
        self.audio_thread.start()

    def close(self) -> None:
        self.stop.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        if self.audio_thread:
            # Tearing down a WASAPI stream can take longer than a second; a thread
            # that outlives this join keeps the audio port bound and breaks the
            # next connection's bind.
            self.audio_thread.join(timeout=5)
            if self.audio_thread.is_alive():
                logging.warning("Audio thread did not stop; the audio port may stay busy briefly")


class Primary:
    def __init__(self, config: Config, key: bytes, config_path: str | None = None) -> None:
        self.config = config
        self.key = key
        self.config_path = config_path
        self.paired = config.paired
        self.state = ActiveState()
        self.current: Session | None = None
        self.outbound: queue.Queue[dict] = queue.Queue(maxsize=4096)
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._peer_gate = threading.Lock()

    def handle_pairing(self, sock: socket.socket, address: tuple[str, int], request: dict) -> None:
        """Hand this PC's key to a new secondary once a person confirms the code."""
        if self.paired:
            logging.warning("Refused a pairing request from %s: this PC is already paired. Run 'deskmesh -Reconfigure' to pair a different PC", address[0])
            sock.sendall(encode({"type": "pair_reject", "reason": "Already paired; run 'deskmesh -Reconfigure' on the main PC to pair another PC"}))
            return
        exchange = Exchange()
        shared = exchange.shared(request["public"])
        sock.sendall(encode({"type": "pair_offer", "public": exchange.public_hex, "name": self.config.name}))
        code = short_code(shared, request["public"], exchange.public_hex)
        logging.info("Pairing request from %s (%s)", request["name"], address[0])
        print(f"\n  {request['name']} at {address[0]} wants to pair.")
        print(f"  Confirm this code matches the one on that PC:  {code}\n")
        answer = input("  Pair with this PC? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            sock.sendall(encode({"type": "pair_reject", "reason": "The person at the main PC declined"}))
            logging.info("Pairing declined")
            return
        wrapped, tag = wrap_key(shared, self.key)
        # Close the window before handing the key over: if the send or the config
        # write then fails, the window stays shut rather than open to the network.
        self.paired = True
        sock.sendall(encode({"type": "pair_accept", "key": wrapped, "tag": tag}))
        if self.config_path:
            try:
                update_config_file(self.config_path, {"paired": True})
            except OSError as exc:
                logging.warning("Could not record the pairing in %s: %s", self.config_path, exc)
        logging.info("Paired with %s. %s will reconnect to start sharing", request["name"], request["name"])

    def on_switch(self, action: str) -> bool:
        was_local = not self.state.remote
        if action == "secondary":
            if not was_local:
                self.enqueue({"type": "release_all"})
            if self.state.switch(True):
                logging.info("[ACTIVE] Secondary")
            else:
                logging.warning("Switch requested, but no secondary is connected")
            return was_local
        was_remote = self.state.remote
        self.state.switch(False)
        if was_remote:
            self.enqueue({"type": "release_all"})
            logging.info("[ACTIVE] Primary%s", " (emergency)" if action == "emergency" else "")
        return was_local

    def enqueue(self, message: dict) -> bool:
        try:
            self.outbound.put_nowait(message)
            return True
        except queue.Full:
            logging.error("Input queue full; restoring local control")
            self.state.disconnect()
            with self._lock:
                if self.current:
                    self.current.stop.set()
                    try:
                        self.current.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            return False

    def on_event(self, message: dict | None) -> bool:
        if not self.state.remote:
            return False
        if message is None:
            return True
        return self.enqueue(message)

    def sender(self, session: Session) -> None:
        while not session.stop.is_set():
            try:
                message = self.outbound.get(timeout=0.2)
                session.send(message)
            except queue.Empty:
                continue
            except OSError:
                session.stop.set()
                return

    def serve_peer(self, sock: socket.socket, address: tuple[str, int]) -> None:
        """Dispatch one inbound connection to either pairing or a control session."""
        try:
            sock.settimeout(self.config.timeout_seconds)
            # The client states its intent first: an unpaired PC asks to pair,
            # a paired one goes straight into authentication.
            intent = receive(sock)
        except (OSError, ConnectionError, ValueError) as exc:
            logging.warning("Ignored a connection from %s: %s", address[0], exc)
            sock.close()
            return
        if intent["type"] not in ("pair_request", "connect"):
            logging.warning("Ignored a connection from %s: expected connect or pair_request", address[0])
            sock.close()
            return
        # DeskMesh serves one PC at a time. Claim that slot without waiting so an
        # extra PC is told why instead of being left hanging in the backlog.
        if not self._peer_gate.acquire(blocking=False):
            logging.warning("Refused a connection from %s: already busy with another PC", address[0])
            if intent["type"] == "pair_request":
                try:
                    sock.sendall(encode({"type": "pair_reject", "reason": "The main PC is already busy with another PC"}))
                except OSError:
                    pass
            sock.close()
            return
        try:
            if intent["type"] == "pair_request":
                try:
                    self.handle_pairing(sock, address, intent)
                except (OSError, ConnectionError, ValueError) as exc:
                    logging.warning("Pairing with %s failed: %s", address[0], exc)
                finally:
                    sock.close()
            else:
                self.serve_session(sock, address)
        finally:
            self._peer_gate.release()

    def serve_session(self, sock: socket.socket, address: tuple[str, int]) -> None:
        session: Session | None = None
        try:
            session_id = server_auth(sock, self.key)
            peer = receive(sock)
            if peer["type"] != "hello":
                raise ValueError("Expected peer hello")
            session = Session(sock, self.config, self.key, session_id)
            session.send({"type": "hello", "name": self.config.name})
            with self._lock:
                self.current = session
                self.outbound = queue.Queue(maxsize=4096)
                self.state.connect()
            logging.info("Connected: %s (%s)", peer["name"], address[0])
            session.start_heartbeat()
            threading.Thread(target=self.sender, args=(session,), daemon=True, name="input-sender").start()
            if self.config.audio_receive:
                session.start_audio(receive_audio, self.config.bind, self.config.audio_port, address[0], self.key, session_id, self.config.playback_device, self.config.buffer_ms, self.config.remote_volume)
            while not session.stop.is_set():
                message = receive(sock)
                if message["type"] == "heartbeat":
                    session.last_heartbeat = time.monotonic()
                elif message["type"] == "disconnect":
                    break
                else:
                    raise ValueError("Unexpected message from secondary")
        except (OSError, ConnectionError, ValueError) as exc:
            logging.warning("Secondary disconnected: %s", exc)
        finally:
            with self._lock:
                self.state.disconnect()
                self.current = None
            logging.info("Restored local control")
            if session:
                session.close()
            else:
                sock.close()

    def accept_loop(self, listener: socket.socket) -> None:
        with listener:
            listener.settimeout(0.5)
            logging.info("Listening on %s:%d; waiting for secondary", self.config.bind, self.config.control_port)
            while not self.stop.is_set():
                try:
                    sock, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop.is_set():
                        logging.exception("Stopped accepting connections")
                    return
                # Handled off this thread so a live session does not stall the
                # listener; serve_peer refuses anything beyond the first PC.
                threading.Thread(target=self.serve_peer, args=(sock, address), daemon=True, name="peer").start()

    def run(self) -> None:
        from .windows_input import InputHooks

        hooks = InputHooks(self.on_event, self.on_switch, {"secondary": self.config.switch_to_secondary, "primary": self.config.switch_to_primary, "emergency": self.config.emergency_return})
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.config.bind, self.config.control_port))
            sock.listen(1)
            sock.settimeout(0.5)
            hooks.install()
        except Exception:
            sock.close()
            raise
        listener = threading.Thread(target=self.accept_loop, args=(sock,), daemon=True, name="control-listener")
        listener.start()
        discovery = threading.Thread(target=respond, args=(self.stop, self.config.bind, self.config.discovery_port, self.config.name, self.config.control_port, self.config.audio_port), daemon=True, name="discovery")
        discovery.start()
        logging.info("Input hooks active; Ctrl+Alt+Home returns to primary")
        try:
            hooks.pump()
        finally:
            self.stop.set()
            self.state.disconnect()
            hooks.close()
            with self._lock:
                if self.current:
                    self.current.close()


def release_remote(held: PressedState) -> None:
    from .windows_input import inject_button, inject_key

    keys, buttons = held.drain()
    for vk, extended in keys:
        inject_key(vk, False, extended)
    for button in buttons:
        inject_button(button, False)


def apply_input(message: dict, held: PressedState) -> None:
    from .windows_input import inject_button, inject_key, inject_move, inject_wheel

    kind = message["type"]
    if kind == "keyboard":
        inject_key(message["vk"], message["pressed"], message["extended"])
        held.key(message["vk"], message["extended"], message["pressed"])
    elif kind == "mouse_move":
        inject_move(message["dx"], message["dy"])
    elif kind == "mouse_button":
        inject_button(message["button"], message["pressed"])
        held.button(message["button"], message["pressed"])
    elif kind == "mouse_wheel":
        inject_wheel(message["delta"])
    elif kind == "release_all":
        release_remote(held)
    else:
        raise ValueError("Unexpected message from primary")


def run_secondary(config: Config, key: bytes, host: str) -> None:
    from .windows_input import reset_cursor

    while True:
        session: Session | None = None
        held = PressedState()
        retry_seconds = 3
        reset_cursor()  # Track from this PC's real cursor again, not the last session's.
        try:
            with socket.create_connection((host, config.control_port), timeout=config.timeout_seconds) as sock:
                sock.settimeout(config.timeout_seconds)
                sock.sendall(encode({"type": "connect"}))
                session_id = client_auth(sock, key)
                session = Session(sock, config, key, session_id)
                session.send({"type": "hello", "name": config.name})
                peer = receive(sock)
                if peer["type"] != "hello":
                    raise ValueError("Expected primary hello")
                logging.info("Connected and authenticated to %s", peer["name"])
                session.start_heartbeat()
                if config.audio_send:
                    session.start_audio(send_audio, host, config.audio_port, key, session_id, config.capture_device)
                while not session.stop.is_set():
                    message = receive(sock)
                    if message["type"] == "heartbeat":
                        session.last_heartbeat = time.monotonic()
                    elif message["type"] == "disconnect":
                        break
                    else:
                        apply_input(message, held)
        except (OSError, ConnectionError, ValueError) as exc:
            if "Authentication failed" in str(exc):
                # Retrying cannot help until the key changes; slow down and say so.
                retry_seconds = 15
                logging.error("Authentication failed: this PC's pairing key does not match the main PC. Run 'deskmesh -Reconfigure' on the main PC, then here, and pair again")
            else:
                logging.warning("Primary unavailable: %s", exc)
        finally:
            try:
                release_remote(held)
            except OSError:
                logging.exception("Could not release a remote input")
            if session:
                session.close()
        logging.info("Reconnecting in %d seconds; press Ctrl+C to exit", retry_seconds)
        time.sleep(retry_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Share keyboard, mouse and system audio across two Windows PCs")
    parser.add_argument("--config", help="JSON config file (default: deskmesh.json if present)")
    parser.add_argument("--debug", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="Start the saved role, guiding first-time setup if needed")
    setup = sub.add_parser("setup", help="Configure or reconfigure this PC")
    setup.add_argument("--reset", action="store_true", help="Choose the role and pairing key again")
    sub.add_parser("pairing-code", help="Show the existing main PC pairing code")
    primary = sub.add_parser("primary", help="Run on the PC with keyboard, mouse and headset")
    secondary = sub.add_parser("secondary", help="Run on the other PC")
    secondary.add_argument("--connect", required=True, help="Primary PC LAN IP address")
    sub.add_parser("devices", help="List output and loopback audio devices")
    generate = sub.add_parser("generate-key", help="Create a shared key file")
    generate.add_argument("--key-file", default="deskmesh.key")
    for command in (primary, secondary):
        command.add_argument("--key-file")
        command.add_argument("--port", type=int, dest="control_port")
        command.add_argument("--audio-port", type=int, dest="audio_port")
        command.add_argument("--no-audio", action="store_true")
    args = parser.parse_args(argv)
    command = args.command or "start"
    configure_logging(args.debug)
    try:
        if command == "generate-key":
            generate_key(args.key_file)
            print(f"Created {args.key_file}; copy it securely to the other PC")
            return 0
        require_windows()
        if command == "devices":
            list_devices()
            return 0
        config_path = args.config or ("deskmesh.json" if Path("deskmesh.json").exists() else None)
        if command == "setup":
            setup_interactive(args.config or "deskmesh.json", reset=args.reset)
            return 0
        if command == "start":
            # start.ps1 already ran setup to learn the role for firewall rules, so
            # stay quiet here when this PC is configured and nothing needs asking.
            setup_interactive(args.config or "deskmesh.json", quiet=True)
            config_path = args.config or "deskmesh.json"
        if command == "pairing-code":
            config = load_config(config_path)
            if config.role != "primary":
                raise ValueError("Pairing codes are shown on the main PC")
            print(code_from_key(load_key(config.key_file)))
            return 0
        overrides = {}
        if command in ("primary", "secondary"):
            overrides = {"key_file": args.key_file, "control_port": args.control_port, "audio_port": args.audio_port}
            if args.no_audio:
                overrides["audio_receive" if command == "primary" else "audio_send"] = False
        config = load_config(config_path, overrides)
        key = load_key(config.key_file)
        role = config.role if command == "start" else command
        if role == "primary":
            Primary(config, key, config_path).run()
        else:
            host = config.connect if command == "start" else args.connect
            if not host:
                raise ValueError("Other PC needs the main PC address; run setup --reset")
            run_secondary(config, key, host)
        return 0
    except KeyboardInterrupt:
        logging.info("Stopped")
        return 0
    except ImportError as exc:
        logging.error("Missing audio dependency: %s. Run: .venv\\Scripts\\python.exe -m pip install -r requirements.txt", exc)
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1
