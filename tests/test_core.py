import socket
import threading
import json
import tempfile
import unittest
import ctypes
import sys
from pathlib import Path
from unittest.mock import patch

from deskmesh.app import Primary
from deskmesh.audio import AudioPacer, JitterBuffer, PCM_BYTES, SILENCE, audio_key, pack_audio, unpack_audio
from deskmesh.auth import client_auth, server_auth
from deskmesh.config import Config
from deskmesh.auth import load_key
from deskmesh.config import load_config
from deskmesh.discovery import REQUEST, RESPONSE, respond
from deskmesh.setup import code_from_key, key_from_code, new_pairing, setup_interactive
from deskmesh.protocol import MAX_MESSAGE, encode, receive
from deskmesh.state import ActiveState, PressedState


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
            with patch("builtins.input", side_effect=["1"]), patch("deskmesh.setup.local_ipv4_addresses", return_value=["192.168.1.10"]), patch("builtins.print"):
                setup_interactive(str(main_config))
            code = code_from_key(load_key(str(main_key)))
            with patch("builtins.input", side_effect=["2", code]), patch("deskmesh.setup._choose_main", return_value={"host": "192.168.1.10"}), patch("builtins.print"):
                setup_interactive(str(other_config))
            self.assertEqual(load_key(str(main_key)), load_key(str(other_key)))
            self.assertEqual(load_config(str(main_config)).role, "primary")
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
        from deskmesh.windows_input import InputHooks, KBDLLHOOKSTRUCT, WM_KEYDOWN

        primary = Primary(Config(audio_receive=False), bytes(range(32)))
        primary.state.connect()
        hooks = InputHooks(primary.on_event, primary.on_switch, {"secondary": "ctrl+alt+right", "primary": "ctrl+alt+left", "emergency": "ctrl+alt+home"})
        for vk in (0xA2, 0xA4, 0x27):
            event = KBDLLHOOKSTRUCT(vk, 0, 0, 0, 0)
            hooks._keyboard(0, WM_KEYDOWN, ctypes.addressof(event))
        self.assertTrue(primary.state.remote)

    @unittest.skipUnless(sys.platform == "win32", "Windows input hooks")
    def test_poll_fallback_switches_and_reinstalls(self):
        from deskmesh import windows_input as win

        primary = Primary(Config(audio_receive=False), bytes(range(32)))
        primary.state.connect()
        hooks = win.InputHooks(primary.on_event, primary.on_switch, {"secondary": "ctrl+alt+right", "primary": "ctrl+alt+left", "emergency": "ctrl+alt+home"})
        pressed = {0x11, 0x12, 0x27}
        with patch.object(win.user32, "GetAsyncKeyState", side_effect=lambda vk: 0x8000 if vk in pressed else 0), patch.object(hooks, "close"), patch.object(hooks, "install") as install:
            hooks._poll_hotkeys()
            self.assertTrue(primary.state.remote)
            pressed.clear()
            hooks._poll_hotkeys()
            install.assert_called_once()


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
