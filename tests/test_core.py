import socket
import threading
import json
import logging
import logging.handlers
import tempfile
import time
import unittest
import ctypes
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from deskmesh.app import Primary, Session, configure_logging
from deskmesh.audio import AudioPacer, JitterBuffer, PCM_BYTES, SILENCE, audio_key, pack_audio, unpack_audio
from deskmesh.auth import client_auth, server_auth
from deskmesh.config import Config
from deskmesh.auth import load_key
from deskmesh.config import load_config
from deskmesh.discovery import REQUEST, RESPONSE, respond
from deskmesh.pairing import Exchange, MODP_2048, short_code, unwrap_key, wrap_key
from deskmesh.setup import code_from_key, key_from_code, new_pairing, pair_with_primary, setup_interactive
from deskmesh.protocol import MAX_MESSAGE, encode, receive
from deskmesh.state import ActiveState, PressedState


@contextmanager
def frozen_cursor(module, position=(960, 540), screen=(0, 0, 1920, 1080)):
    """Keep tests off the real mouse while letting cursor logic run."""
    moves = []

    def fake_get(pointer):
        # byref() yields a CArgObject wrapping the struct; a real POINTER has .contents.
        target = getattr(pointer, "_obj", None) or pointer.contents
        target.x, target.y = position
        return 1

    def fake_set(x, y):
        moves.append((x, y))
        return 1

    with ExitStack() as stack:
        stack.enter_context(patch.object(module.user32, "GetCursorPos", fake_get))
        stack.enter_context(patch.object(module.user32, "SetCursorPos", fake_set))
        stack.enter_context(patch.object(module, "virtual_screen", lambda: screen))
        yield moves


class ProtocolTests(unittest.TestCase):
    def test_partial_frames_and_multiple_messages(self):
        a, b = socket.socketpair()
        try:
            frames = encode({"type": "heartbeat"}) + encode({"type": "mouse_wheel", "delta": -120})
            def writer():
                for piece in (frames[:2], frames[2:8], frames[8:]):
                    a.sendall(piece)
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertEqual(receive(b)["type"], "heartbeat")
            self.assertEqual(receive(b)["delta"], -120)
            thread.join()
        finally:
            a.close()
            b.close()

    def test_bad_messages(self):
        with self.assertRaises(ValueError):
            encode({"type": "run_code", "code": "x"})
        with self.assertRaises(ValueError):
            encode({"type": "keyboard", "vk": 0, "pressed": True, "extended": False})
        a, b = socket.socketpair()
        try:
            a.sendall((MAX_MESSAGE + 1).to_bytes(4, "big"))
            with self.assertRaises(ValueError):
                receive(b)
        finally:
            a.close()
            b.close()


class AuthTests(unittest.TestCase):
    def test_mutual_auth(self):
        key = bytes(range(32))
        a, b = socket.socketpair()
        result = []
        thread = threading.Thread(target=lambda: result.append(server_auth(a, key)))
        thread.start()
        client_session = client_auth(b, key)
        thread.join()
        self.assertEqual(result, [client_session])
        a.close()
        b.close()

    def test_bad_key_rejected(self):
        key = bytes(range(32))
        a, b = socket.socketpair()
        errors = []
        thread = threading.Thread(target=lambda: self._server_error(a, key, errors))
        thread.start()
        with self.assertRaises((ValueError, ConnectionError)):
            client_auth(b, bytes(reversed(key)))
        thread.join()
        self.assertEqual(len(errors), 1)
        a.close()
        b.close()

    @staticmethod
    def _server_error(sock, key, errors):
        try:
            server_auth(sock, key)
        except ValueError as exc:
            errors.append(exc)
            sock.close()


class ConnectionTests(unittest.TestCase):
    def test_primary_restores_local_on_disconnect(self):
        key = bytes(range(32))
        primary = Primary(Config(audio_receive=False), key)
        a, b = socket.socketpair()
        thread = threading.Thread(target=primary.serve_peer, args=(a, ("127.0.0.1", 1234)))
        thread.start()
        b.sendall(encode({"type": "connect"}))
        client_auth(b, key)
        b.sendall(encode({"type": "hello", "name": "test-secondary"}))
        self.assertEqual(receive(b)["type"], "hello")
        primary.on_switch("secondary")
        self.assertTrue(primary.state.remote)
        b.sendall(encode({"type": "disconnect"}))
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(primary.state.remote)
        b.close()

    def test_heartbeat_watchdog_drops_a_silent_peer(self):
        """A peer that stops answering must not hold the session open forever."""
        config = Config(heartbeat_seconds=0.01, timeout_seconds=0.05)
        a, b = socket.socketpair()
        try:
            session = Session(a, config, bytes(range(32)), b"session1")
            session.last_heartbeat = time.monotonic() - 10
            session.start_heartbeat()
            self.assertTrue(session.stop.wait(timeout=2), "watchdog did not fire")
        finally:
            a.close()
            b.close()

    def test_heartbeat_watchdog_keeps_a_responsive_peer(self):
        config = Config(heartbeat_seconds=0.01, timeout_seconds=0.05)
        a, b = socket.socketpair()
        try:
            session = Session(a, config, bytes(range(32)), b"session1")
            stop_refreshing = threading.Event()

            def refresh():
                while not stop_refreshing.wait(0.01):
                    session.last_heartbeat = time.monotonic()

            keeper = threading.Thread(target=refresh, daemon=True)
            keeper.start()
            session.start_heartbeat()
            self.assertFalse(session.stop.wait(timeout=0.3), "watchdog fired on a live peer")
            stop_refreshing.set()
            keeper.join(timeout=1)
            session.stop.set()
        finally:
            a.close()
            b.close()


class InjectionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows input injection")
    def test_apply_input_dispatches_and_releases_held_inputs(self):
        from deskmesh import windows_input as win
        from deskmesh.app import apply_input, release_remote

        calls = []
        held = PressedState()
        patches = {
            "inject_key": lambda vk, pressed, extended: calls.append(("key", vk, pressed, extended)),
            "inject_move": lambda dx, dy: calls.append(("move", dx, dy)),
            "inject_button": lambda button, pressed: calls.append(("button", button, pressed)),
            "inject_wheel": lambda delta: calls.append(("wheel", delta)),
        }
        with ExitStack() as stack:
            for name, fake in patches.items():
                stack.enter_context(patch.object(win, name, fake))
            apply_input({"type": "keyboard", "vk": 0x41, "pressed": True, "extended": False}, held)
            apply_input({"type": "mouse_move", "dx": 7, "dy": -3}, held)
            apply_input({"type": "mouse_button", "button": "left", "pressed": True}, held)
            apply_input({"type": "mouse_wheel", "delta": -120}, held)
            # The primary holds A and left-click; release_all must undo exactly those.
            self.assertEqual(held.keys, {0x41: False})
            self.assertEqual(held.buttons, {"left"})
            apply_input({"type": "release_all"}, held)
            self.assertEqual(held.keys, {})
            self.assertEqual(held.buttons, set())
            with self.assertRaises(ValueError):
                apply_input({"type": "hello", "name": "x"}, held)
        self.assertEqual(calls, [
            ("key", 0x41, True, False),
            ("move", 7, -3),
            ("button", "left", True),
            ("wheel", -120),
            ("key", 0x41, False, False),
            ("button", "left", False),
        ])

    @unittest.skipUnless(sys.platform == "win32", "Windows input injection")
    def test_release_remote_is_safe_when_nothing_is_held(self):
        from deskmesh.app import release_remote

        release_remote(PressedState())


class LoggingTests(unittest.TestCase):
    def test_logging_never_blocks_the_caller(self):
        """Input hook callbacks log; a blocking console write unhooks them."""
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        try:
            configure_logging(debug=False)
            self.assertEqual(len(root.handlers), 1)
            self.assertIsInstance(root.handlers[0], logging.handlers.QueueHandler)
        finally:
            for handler in root.handlers[:]:
                root.removeHandler(handler)
            for handler in saved_handlers:
                root.addHandler(handler)
            root.setLevel(saved_level)


class ConfirmTests(unittest.TestCase):
    def test_enter_takes_the_default_so_setup_is_two_keystrokes(self):
        from deskmesh.setup import _confirm

        for answers, default, expected in (
            ([""], True, True),
            ([""], False, False),
            (["y"], False, True),
            (["YES"], False, True),
            (["n"], True, False),
            (["no"], True, False),
            (["  "], True, True),
            (["maybe", "y"], False, True),  # Re-asks until the answer parses.
        ):
            with patch("builtins.input", side_effect=answers), patch("builtins.print"):
                self.assertIs(_confirm("q?", default=default), expected, f"answers={answers} default={default}")


class PairingTests(unittest.TestCase):
    def test_both_sides_agree_on_secret_and_code(self):
        secondary, primary = Exchange(), Exchange()
        shared_secondary = secondary.shared(primary.public_hex)
        shared_primary = primary.shared(secondary.public_hex)
        self.assertEqual(shared_secondary, shared_primary)
        code_a = short_code(shared_secondary, secondary.public_hex, primary.public_hex)
        code_b = short_code(shared_primary, secondary.public_hex, primary.public_hex)
        self.assertEqual(code_a, code_b)
        self.assertRegex(code_a, r"^\d{4}$")

    def test_man_in_the_middle_produces_different_codes(self):
        """The attacker holds two separate secrets, so the two screens disagree."""
        secondary, primary, attacker = Exchange(), Exchange(), Exchange()
        # Each honest side believes it is talking to the attacker's public value.
        secondary_side = secondary.shared(attacker.public_hex)
        primary_side = primary.shared(attacker.public_hex)
        self.assertNotEqual(secondary_side, primary_side)
        shown_on_secondary = short_code(secondary_side, secondary.public_hex, attacker.public_hex)
        shown_on_primary = short_code(primary_side, attacker.public_hex, primary.public_hex)
        self.assertNotEqual(shown_on_secondary, shown_on_primary)

    def test_degenerate_public_values_are_rejected(self):
        exchange = Exchange()
        for bad in (0, 1, MODP_2048 - 1, MODP_2048):
            with self.assertRaises(ValueError):
                exchange.shared(bad.to_bytes(256, "big").hex())
        with self.assertRaises(ValueError):
            exchange.shared("not-hex" * 10)
        with self.assertRaises(ValueError):
            exchange.shared("ab")  # Wrong width.

    def test_key_wrap_round_trip_and_tamper_detection(self):
        shared = Exchange().shared(Exchange().public_hex)
        key = bytes(range(32))
        wrapped, tag = wrap_key(shared, key)
        self.assertNotIn(key.hex(), wrapped)  # Never travels in the clear.
        self.assertEqual(unwrap_key(shared, wrapped, tag), key)
        flipped = bytearray(bytes.fromhex(wrapped))
        flipped[0] ^= 1
        with self.assertRaises(ValueError):
            unwrap_key(shared, bytes(flipped).hex(), tag)
        other = Exchange().shared(Exchange().public_hex)
        with self.assertRaises(ValueError):
            unwrap_key(other, wrapped, tag)

    def _pairing_server(self, port, key, answer, store, already_paired=False):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        primary = Primary(Config(control_port=port, audio_receive=False), key)
        primary.paired = already_paired  # Set before serving, never racing the accept.
        store["primary"] = primary

        def serve():
            with listener:
                sock, address = listener.accept()
                with patch("builtins.input", return_value=answer), patch("builtins.print"):
                    primary.serve_peer(sock, address)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        return thread

    def test_end_to_end_pairing_transfers_the_key(self):
        key = bytes(range(32))
        port = 47731
        store = {}
        thread = self._pairing_server(port, key, "y", store)
        with patch("builtins.print"):
            received, peer_name = pair_with_primary("127.0.0.1", port, "other-pc")
        thread.join(timeout=5)
        self.assertEqual(received, key)
        self.assertEqual(peer_name, Config().name)
        self.assertTrue(store["primary"].paired, "pairing window should close after success")

    def test_declining_at_the_main_pc_refuses_the_key(self):
        port = 47732
        store = {}
        thread = self._pairing_server(port, bytes(range(32)), "n", store)
        with patch("builtins.print"), self.assertRaises(ValueError) as caught:
            pair_with_primary("127.0.0.1", port, "other-pc")
        thread.join(timeout=5)
        self.assertIn("declined", str(caught.exception))
        self.assertFalse(store["primary"].paired)

    def test_a_second_pc_is_refused_while_one_is_busy(self):
        """An extra PC must be told why, not left hanging in the listen backlog."""
        port = 47734
        store = {}
        thread = self._pairing_server(port, bytes(range(32)), None, store)
        primary = store["primary"]
        primary._peer_gate.acquire()  # Stand in for a live session.
        try:
            with patch("builtins.print"), self.assertRaises(ValueError) as caught:
                pair_with_primary("127.0.0.1", port, "intruder-pc")
        finally:
            primary._peer_gate.release()
        thread.join(timeout=5)
        self.assertIn("busy", str(caught.exception))

    def test_an_already_paired_primary_refuses_without_prompting(self):
        port = 47733
        store = {}
        # input() would raise StopIteration if the handler wrongly prompted.
        thread = self._pairing_server(port, bytes(range(32)), None, store, already_paired=True)
        with patch("builtins.print"), self.assertRaises(ValueError) as caught:
            pair_with_primary("127.0.0.1", port, "other-pc")
        thread.join(timeout=5)
        self.assertIn("Already paired", str(caught.exception))


class SetupTests(unittest.TestCase):
    def test_pairing_code_round_trip(self):
        code, key = new_pairing()
        self.assertEqual(key_from_code(code.lower().replace("-", " ")), key)
        self.assertEqual(code_from_key(key), code)
        with self.assertRaises(ValueError):
            key_from_code("short")

    def test_both_roles_save_matching_keys(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            main_config = root / "main.json"
            other_config = root / "other.json"
            main_key = root / "main.key"
            other_key = root / "other.key"
            main_config.write_text(json.dumps({"key_file": str(main_key)}), encoding="utf-8")
            other_config.write_text(json.dumps({"key_file": str(other_key)}), encoding="utf-8")
            # Nothing on the network, so this PC offers to be the main one.
            with patch("deskmesh.setup.discover", return_value=[]), patch("builtins.input", side_effect=["y"]), patch("deskmesh.setup.local_ipv4_addresses", return_value=["192.168.1.10"]), patch("builtins.print"):
                setup_interactive(str(main_config))
            main_key_bytes = load_key(str(main_key))
            # The main PC is now discoverable, so this one becomes the secondary.
            discovered = [{"name": "MAIN-PC", "host": "192.168.1.10", "control_port": 47660, "audio_port": 47661}]
            with patch("deskmesh.setup.discover", return_value=discovered), patch("builtins.input", side_effect=["y"]), patch("deskmesh.setup.pair_with_primary", return_value=(main_key_bytes, "MAIN-PC")), patch("builtins.print"):
                setup_interactive(str(other_config))
            self.assertEqual(load_key(str(main_key)), load_key(str(other_key)))
            self.assertEqual(load_config(str(main_config)).role, "primary")
            self.assertFalse(load_config(str(main_config)).paired, "the window must be open for the other PC")
            self.assertEqual(load_config(str(other_config)).role, "secondary")
            self.assertEqual(load_config(str(other_config)).connect, "192.168.1.10")

    def test_backup_code_still_works_when_pairing_cannot_run(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            main_config, other_config = root / "main.json", root / "other.json"
            main_key, other_key = root / "main.key", root / "other.key"
            main_config.write_text(json.dumps({"key_file": str(main_key)}), encoding="utf-8")
            other_config.write_text(json.dumps({"key_file": str(other_key)}), encoding="utf-8")
            with patch("deskmesh.setup.discover", return_value=[]), patch("builtins.input", side_effect=["y"]), patch("deskmesh.setup.local_ipv4_addresses", return_value=[]), patch("builtins.print"):
                setup_interactive(str(main_config))
            code = code_from_key(load_key(str(main_key)))
            unreachable = OSError("no route to host")
            # Confirm secondary role, accept the fallback, then type the code.
            with patch("deskmesh.setup.discover", return_value=[]), patch("deskmesh.setup.pair_with_primary", side_effect=unreachable), patch("builtins.input", side_effect=["n", "192.168.1.10", "y", code]), patch("builtins.print"):
                setup_interactive(str(other_config))
            self.assertEqual(load_key(str(main_key)), load_key(str(other_key)))
            self.assertEqual(load_config(str(other_config)).role, "secondary")
            self.assertEqual(load_config(str(other_config)).connect, "192.168.1.10")

    def test_discovery_response(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        stop = threading.Event()
        server = threading.Thread(target=respond, args=(stop, "127.0.0.1", port, "main", 47660, 47661))
        server.start()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(1)
                nonce = b"12345678"
                for _ in range(3):
                    client.sendto(REQUEST + nonce, ("127.0.0.1", port))
                    try:
                        reply, _ = client.recvfrom(512)
                        break
                    except (socket.timeout, ConnectionResetError):
                        continue
                else:
                    self.fail("No discovery response")
                self.assertTrue(reply.startswith(RESPONSE + nonce))
        finally:
            stop.set()
            server.join(timeout=2)


class StateTests(unittest.TestCase):
    def test_switch_and_disconnect(self):
        state = ActiveState()
        self.assertFalse(state.switch(True))
        state.connect()
        self.assertTrue(state.switch(True))
        state.switch(False)
        self.assertFalse(state.remote)
        state.switch(True)
        state.disconnect()
        self.assertFalse(state.remote)

    def test_held_inputs_cleanup(self):
        state = PressedState()
        state.key(17, False, True)
        state.button("left", True)
        self.assertEqual(state.drain(), ([(17, False)], ["left"]))
        self.assertEqual(state.drain(), ([], []))

    def test_repeated_switch_releases_remote_modifiers(self):
        primary = Primary(Config(audio_receive=False), bytes(range(32)))
        primary.state.connect()
        self.assertTrue(primary.on_switch("secondary"))
        self.assertFalse(primary.on_switch("secondary"))
        self.assertEqual(primary.outbound.get_nowait(), {"type": "release_all"})
        self.assertFalse(primary.on_switch("emergency"))
        self.assertEqual(primary.outbound.get_nowait(), {"type": "release_all"})

    @unittest.skipUnless(sys.platform == "win32", "Windows input hooks")
    def test_hotkey_hook_switches_to_secondary(self):
        from deskmesh import windows_input as win

        primary = Primary(Config(audio_receive=False), bytes(range(32)))
        primary.state.connect()
        hooks = win.InputHooks(primary.on_event, primary.on_switch, {"secondary": "ctrl+alt+right", "primary": "ctrl+alt+left", "emergency": "ctrl+alt+home"})
        with frozen_cursor(win):
            for vk in (0xA2, 0xA4, 0x27):
                event = win.KBDLLHOOKSTRUCT(vk, 0, 0, 0, 0)
                hooks._keyboard(0, win.WM_KEYDOWN, ctypes.addressof(event))
        self.assertTrue(primary.state.remote)

    @unittest.skipUnless(sys.platform == "win32", "Windows input hooks")
    def test_poll_fallback_switches_and_reinstalls(self):
        from deskmesh import windows_input as win

        primary = Primary(Config(audio_receive=False), bytes(range(32)))
        primary.state.connect()
        hooks = win.InputHooks(primary.on_event, primary.on_switch, {"secondary": "ctrl+alt+right", "primary": "ctrl+alt+left", "emergency": "ctrl+alt+home"})
        pressed = {0x11, 0x12, 0x27}
        with frozen_cursor(win), patch.object(win.user32, "GetAsyncKeyState", side_effect=lambda vk: 0x8000 if vk in pressed else 0), patch.object(hooks, "close"), patch.object(hooks, "install") as install:
            hooks._poll_hotkeys()
            self.assertTrue(primary.state.remote)
            pressed.clear()
            hooks._poll_hotkeys()
            install.assert_called_once()

    @unittest.skipUnless(sys.platform == "win32", "Windows input hooks")
    def test_switch_centres_cursor_so_no_direction_is_clipped(self):
        """A cursor left against a screen edge would clamp deltas to zero."""
        from deskmesh import windows_input as win

        primary = Primary(Config(audio_receive=False), bytes(range(32)))
        primary.state.connect()
        hooks = win.InputHooks(primary.on_event, primary.on_switch, {"secondary": "ctrl+alt+right", "primary": "ctrl+alt+left", "emergency": "ctrl+alt+home"})
        with frozen_cursor(win, position=(1919, 1079)) as moves:
            hooks._switch("secondary")
            self.assertEqual((hooks.anchor.x, hooks.anchor.y), (960, 540))
            self.assertEqual(moves[-1], (960, 540))
            self.assertEqual((hooks.restore_cursor.x, hooks.restore_cursor.y), (1919, 1079))
            hooks._switch("secondary")  # A repeat must not forget the real origin.
            self.assertEqual((hooks.restore_cursor.x, hooks.restore_cursor.y), (1919, 1079))
            hooks._switch("primary")
            self.assertIsNone(hooks.anchor)
            self.assertIsNone(hooks.restore_cursor)
            self.assertEqual(moves[-1], (1919, 1079))  # Returned where the user left it.

    @unittest.skipUnless(sys.platform == "win32", "Windows input hooks")
    def test_remote_cursor_is_absolute_and_bounded(self):
        """Absolute injection avoids applying the secondary's acceleration twice."""
        from deskmesh import windows_input as win

        sent = []
        with frozen_cursor(win, position=(100, 100)), patch.object(win, "_send", lambda inp: sent.append((inp.u.mi.dx, inp.u.mi.dy, inp.u.mi.dwFlags))):
            win.reset_cursor()
            win.inject_move(10, 20)
            self.assertEqual(win._remote_cursor.x, 110)
            self.assertEqual(win._remote_cursor.y, 120)
            win.inject_move(5, 5)
            self.assertEqual((win._remote_cursor.x, win._remote_cursor.y), (115, 125))
            win.inject_move(99999, 99999)  # Clamped to the virtual desktop.
            self.assertEqual((win._remote_cursor.x, win._remote_cursor.y), (1919, 1079))
            win.reset_cursor()
        expected = win.MOUSEEVENTF_MOVE | win.MOUSEEVENTF_ABSOLUTE | win.MOUSEEVENTF_VIRTUALDESK
        self.assertTrue(all(flags == expected for _, _, flags in sent))
        self.assertEqual(sent[-1][:2], (win.ABSOLUTE_RANGE, win.ABSOLUTE_RANGE))

    @unittest.skipUnless(sys.platform == "win32", "Windows input hooks")
    def test_hotkeys_accept_letters_and_function_keys(self):
        from deskmesh.windows_input import parse_hotkey

        self.assertEqual(parse_hotkey("ctrl+alt+a"), frozenset({0x11, 0x12, 0x41}))
        self.assertEqual(parse_hotkey("ctrl+shift+f1"), frozenset({0x11, 0x10, 0x70}))
        self.assertEqual(parse_hotkey("win+3"), frozenset({0x5B, 0x33}))
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+nope")
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+ctrl")


class AudioTests(unittest.TestCase):
    def test_capture_pacer_waits_when_backend_returns_early(self):
        class Stop:
            waits = []

            def wait(self, seconds):
                self.waits.append(seconds)

        with patch("deskmesh.audio.time.monotonic", return_value=100.0):
            pacer = AudioPacer()
            stop = Stop()
            pacer.wait(stop)
            self.assertAlmostEqual(stop.waits[0], 0.005)

    def test_packet_auth_and_session(self):
        key = audio_key(bytes(range(32)), b"session1")
        packet = pack_audio(key, b"session1", 7, bytes(PCM_BYTES))
        self.assertEqual(unpack_audio(key, b"session1", packet), (7, bytes(PCM_BYTES)))
        tampered = bytearray(packet)
        tampered[-20] ^= 1
        with self.assertRaises(ValueError):
            unpack_audio(key, b"session1", tampered)
        with self.assertRaises(ValueError):
            unpack_audio(key, b"session2", packet)

    def test_reorder_gap_and_refill(self):
        buffer = JitterBuffer(10)
        first, third = bytes([1]) * PCM_BYTES, bytes([3]) * PCM_BYTES
        buffer.push(3, third)
        self.assertEqual(buffer.pop(), SILENCE)
        buffer.push(1, first)
        self.assertEqual(buffer.pop(), first)
        self.assertEqual(buffer.pop(), SILENCE)
        self.assertEqual(buffer.pop(), third)
        self.assertEqual(buffer.pop(), SILENCE)
        self.assertEqual(buffer.underruns, 2)

    def test_backlog_bounded(self):
        buffer = JitterBuffer(50)
        for seq in range(100):
            buffer.push(seq, bytes(PCM_BYTES))
        self.assertLessEqual(len(buffer.packets), buffer.limit)

    def test_silent_packet_is_not_underrun(self):
        buffer = JitterBuffer(5)
        buffer.push(1, SILENCE)
        self.assertEqual(buffer.pop(), SILENCE)
        self.assertEqual(buffer.underruns, 0)


if __name__ == "__main__":
    unittest.main()
