"""DeskMesh node lifecycle and command-line interface."""

from __future__ import annotations

import argparse
import logging
import platform
import queue
import socket
import sys
import threading
import time
from pathlib import Path

from .audio import list_devices, receive_audio, send_audio
from .auth import client_auth, generate_key, load_key, server_auth
from .config import Config, load_config
from .discovery import respond
from .protocol import encode, receive
from .setup import code_from_key, setup_interactive
from .state import ActiveState, PressedState


def require_windows() -> None:
    if platform.system() != "Windows":
        raise RuntimeError("Input and system audio require Windows 10/11")


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
            self.audio_thread.join(timeout=1)


class Primary:
    def __init__(self, config: Config, key: bytes) -> None:
        self.config = config
        self.key = key
        self.state = ActiveState()
        self.current: Session | None = None
        self.outbound: queue.Queue[dict] = queue.Queue(maxsize=4096)
        self.stop = threading.Event()
        self._lock = threading.Lock()

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
        session: Session | None = None
        try:
            sock.settimeout(self.config.timeout_seconds)
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
                self.serve_peer(sock, address)

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
    while True:
        session: Session | None = None
        held = PressedState()
        try:
            with socket.create_connection((host, config.control_port), timeout=config.timeout_seconds) as sock:
                sock.settimeout(config.timeout_seconds)
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
            logging.warning("Primary unavailable: %s", exc)
        finally:
            try:
                release_remote(held)
            except OSError:
                logging.exception("Could not release a remote input")
            if session:
                session.close()
        logging.info("Reconnecting in 3 seconds; press Ctrl+C to exit")
        time.sleep(3)


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
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="[%(levelname)s] %(message)s")
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
            setup_interactive(args.config or "deskmesh.json")
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
            Primary(config, key).run()
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
