"""Explicit active-machine and remotely held input state."""

from __future__ import annotations

import threading


class ActiveState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connected = False
        self._remote = False

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def switch(self, remote: bool) -> bool:
        with self._lock:
            self._remote = remote and self._connected
            return self._remote

    def disconnect(self) -> None:
        with self._lock:
            self._remote = False
            self._connected = False

    @property
    def remote(self) -> bool:
        with self._lock:
            return self._remote


class PressedState:
    def __init__(self) -> None:
        self.keys: dict[int, bool] = {}
        self.buttons: set[str] = set()

    def key(self, vk: int, extended: bool, pressed: bool) -> None:
        if pressed:
            self.keys[vk] = extended
        else:
            self.keys.pop(vk, None)

    def button(self, name: str, pressed: bool) -> None:
        if pressed:
            self.buttons.add(name)
        else:
            self.buttons.discard(name)

    def drain(self) -> tuple[list[tuple[int, bool]], list[str]]:
        keys, buttons = list(self.keys.items()), list(self.buttons)
        self.keys.clear()
        self.buttons.clear()
        return keys, buttons
