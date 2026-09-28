"""Configuration loading and validation."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Config:
    role: str = ""
    connect: str = ""
    name: str = socket.gethostname()
    bind: str = "0.0.0.0"
    control_port: int = 47660
    audio_port: int = 47661
    discovery_port: int = 47662
    key_file: str = "deskmesh.key"
    switch_to_secondary: str = "ctrl+alt+right"
    switch_to_primary: str = "ctrl+alt+left"
    emergency_return: str = "ctrl+alt+home"
    audio_send: bool = True
    audio_receive: bool = True
    capture_device: str = ""
    playback_device: str = ""
    remote_volume: float = 0.7
    buffer_ms: int = 100
    heartbeat_seconds: float = 1.0
    timeout_seconds: float = 4.0


def load_config(path: str | None, overrides: dict | None = None) -> Config:
    data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    if not isinstance(data, dict):
        raise ValueError("Configuration must be a JSON object")
    allowed = {field.name for field in fields(Config)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    data.update({key: value for key, value in (overrides or {}).items() if value is not None})
    config = Config(**data)
    if config.role not in ("", "primary", "secondary"):
        raise ValueError("role must be primary or secondary")
    if not config.name or len(config.name.encode("utf-8")) > 64:
        raise ValueError("name must be 1-64 UTF-8 bytes")
    if not 1 <= config.control_port <= 65535 or not 1 <= config.audio_port <= 65535 or not 1 <= config.discovery_port <= 65535:
        raise ValueError("Ports must be between 1 and 65535")
    if not 0 <= config.remote_volume <= 1:
        raise ValueError("remote_volume must be between 0 and 1")
    if not 40 <= config.buffer_ms <= 500:
        raise ValueError("buffer_ms must be between 40 and 500")
    if config.heartbeat_seconds <= 0 or config.timeout_seconds <= 2 * config.heartbeat_seconds:
        raise ValueError("timeout_seconds must exceed twice heartbeat_seconds")
    return config
