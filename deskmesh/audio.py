"""Authenticated UDP PCM audio and bounded reorder buffer."""

from __future__ import annotations

import hashlib
import hmac
import logging
import socket
import struct
import threading
import time

RATE = 48000
CHANNELS = 2
FRAMES = 240  # 5 ms, 960 PCM bytes, below a normal Ethernet MTU.
PCM_BYTES = FRAMES * CHANNELS * 2
HEADER = struct.Struct("!4s8sI")
MAGIC = b"DMA1"
TAG_SIZE = 16
SILENCE = bytes(PCM_BYTES)


class AudioPacer:
    """Keep capture packets at the PCM sample clock even if a backend returns early."""

    def __init__(self, frames: int = FRAMES, rate: int = RATE) -> None:
        self.period = frames / rate
        self.deadline = time.monotonic()

    def wait(self, stop: threading.Event) -> None:
        self.deadline += self.period
        now = time.monotonic()
        if self.deadline < now - 0.1:
            self.deadline = now
        if self.deadline > now:
            stop.wait(self.deadline - now)


def audio_key(shared_key: bytes, session: bytes) -> bytes:
    return hmac.new(shared_key, b"deskmesh-audio\0" + session, hashlib.sha256).digest()


def pack_audio(key: bytes, session: bytes, sequence: int, pcm: bytes) -> bytes:
    if len(session) != 8 or len(pcm) != PCM_BYTES:
        raise ValueError("Invalid audio packet format")
    body = HEADER.pack(MAGIC, session, sequence & 0xFFFFFFFF) + pcm
    return body + hmac.new(key, body, hashlib.sha256).digest()[:TAG_SIZE]


def unpack_audio(key: bytes, session: bytes, packet: bytes) -> tuple[int, bytes]:
    if len(packet) != HEADER.size + PCM_BYTES + TAG_SIZE:
        raise ValueError("Invalid audio packet size")
    magic, received_session, sequence = HEADER.unpack_from(packet)
    if magic != MAGIC or received_session != session:
        raise ValueError("Invalid audio session")
    if not hmac.compare_digest(packet[-TAG_SIZE:], hmac.new(key, packet[:-TAG_SIZE], hashlib.sha256).digest()[:TAG_SIZE]):
        raise ValueError("Invalid audio authentication")
    return sequence, packet[HEADER.size:-TAG_SIZE]


class JitterBuffer:
    def __init__(self, buffer_ms: int) -> None:
        self.target = max(1, buffer_ms // 5)
        self.limit = max(self.target * 2, self.target + 10)
        self.packets: dict[int, bytes] = {}
        self.next_sequence: int | None = None
        self.started = False
        self.underruns = 0
        self.late_packets = 0
        self.trimmed_packets = 0
        self._lock = threading.Lock()

    def push(self, sequence: int, pcm: bytes) -> None:
        with self._lock:
            if self.next_sequence is not None and sequence < self.next_sequence:
                self.late_packets += 1
                return
            self.packets[sequence] = pcm
            if len(self.packets) > self.limit:
                before = len(self.packets)
                newest = max(self.packets)
                self.next_sequence = newest - self.target + 1
                self.packets = {seq: data for seq, data in self.packets.items() if seq >= self.next_sequence}
                self.trimmed_packets += before - len(self.packets)
                self.started = True

    def pop(self) -> bytes:
        with self._lock:
            if not self.started:
                if len(self.packets) < self.target:
                    return SILENCE
                self.next_sequence = min(self.packets)
                self.started = True
            assert self.next_sequence is not None
            result = self.packets.pop(self.next_sequence, None)
            if result is None:
                self.underruns += 1
                if not self.packets:
                    self.started = False
                    self.next_sequence = None
                    return SILENCE
            self.next_sequence += 1
            return result if result is not None else SILENCE

    def stats(self) -> tuple[int, int, int, int]:
        with self._lock:
            return len(self.packets), self.underruns, self.late_packets, self.trimmed_packets


def _speaker(name: str):
    import soundcard as sc

    devices = sc.all_speakers()
    if not name:
        return sc.default_speaker()
    matches = [device for device in devices if name.lower() in device.name.lower()]
    if len(matches) != 1:
        raise ValueError(f"Playback device '{name}' matched {len(matches)} devices; run devices and use a unique name")
    return matches[0]


def _loopback(name: str):
    import soundcard as sc

    devices = [device for device in sc.all_microphones(include_loopback=True) if device.isloopback]
    if name:
        matches = [device for device in devices if name.lower() in device.name.lower()]
    else:
        speaker = sc.default_speaker()
        # An exact id match is unambiguous; only fall back to names when the
        # loopback endpoint is not reported under the speaker's own id.
        matches = [device for device in devices if device.id == speaker.id]
        if not matches:
            matches = [device for device in devices if speaker.name.lower() in device.name.lower()]
    if len(matches) != 1:
        raise ValueError(f"Loopback device '{name or 'default speaker'}' matched {len(matches)} devices; run devices and set capture_device")
    return matches[0]


def list_devices() -> None:
    import soundcard as sc

    print("Playback devices:")
    for device in sc.all_speakers():
        print(f"  {device.name} [{device.id}]")
    print("System-audio loopback devices:")
    for device in sc.all_microphones(include_loopback=True):
        if device.isloopback:
            print(f"  {device.name} [{device.id}]")


def send_audio(stop: threading.Event, host: str, port: int, shared_key: bytes, session: bytes, capture_device: str) -> None:
    import numpy as np

    source = _loopback(capture_device)
    key = audio_key(shared_key, session)
    sequence = 0
    pacer = AudioPacer()
    report_at = time.monotonic() + 10
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, source.recorder(samplerate=RATE, channels=CHANNELS, blocksize=FRAMES * 4) as recorder:
        logging.info("Audio capture active: %s", source.name)
        while not stop.is_set():
            frames = recorder.record(numframes=FRAMES)
            pcm = (np.clip(frames, -1, 1) * 32767).astype("<i2").tobytes()
            if len(pcm) == PCM_BYTES:
                sock.sendto(pack_audio(key, session, sequence, pcm), (host, port))
                sequence = (sequence + 1) & 0xFFFFFFFF
                sent += 1
            pacer.wait(stop)
            now = time.monotonic()
            if now >= report_at:
                logging.debug("Audio sender: %d packets in last 10s", sent)
                sent = 0
                report_at = now + 10


def receive_audio(stop: threading.Event, bind: str, port: int, peer_ip: str, shared_key: bytes, session: bytes, playback_device: str, buffer_ms: int, volume: float) -> None:
    import numpy as np

    speaker = _speaker(playback_device)
    key = audio_key(shared_key, session)
    buffer = JitterBuffer(buffer_ms)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # A previous session's receiver can still be tearing down its WASAPI
        # stream and holding this port; without this, reconnecting loses audio
        # until DeskMesh is restarted.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind, port))
        sock.settimeout(0.2)

        def receive_loop() -> None:
            while not stop.is_set():
                try:
                    packet, address = sock.recvfrom(2048)
                    if address[0] == peer_ip:
                        sequence, pcm = unpack_audio(key, session, packet)
                        buffer.push(sequence, pcm)
                except socket.timeout:
                    continue
                except ValueError:
                    continue
                except OSError:
                    if not stop.is_set():
                        logging.exception("Audio receive failed")
                    return

        reader = threading.Thread(target=receive_loop, daemon=True, name="audio-udp")
        reader.start()
        logging.info("Audio playback active: %s", speaker.name)
        report_at = time.monotonic() + 10
        last_underruns = last_trimmed = 0
        with speaker.player(samplerate=RATE, channels=CHANNELS, blocksize=FRAMES * 4) as player:
            while not stop.is_set():
                pcm = buffer.pop()
                frames = np.frombuffer(pcm, dtype="<i2").reshape(FRAMES, CHANNELS).astype("float32") / 32768.0
                player.play(frames * volume)
                now = time.monotonic()
                if now >= report_at:
                    queued, underruns, late, trimmed = buffer.stats()
                    log = logging.warning if underruns > last_underruns + 20 or trimmed > last_trimmed else logging.debug
                    log("Audio buffer: %d packets queued, %d underruns, %d late, %d trimmed", queued, underruns, late, trimmed)
                    last_underruns, last_trimmed = underruns, trimmed
                    report_at = now + 10
        logging.info("Audio underruns: %d", buffer.underruns)
        reader.join(timeout=1)
