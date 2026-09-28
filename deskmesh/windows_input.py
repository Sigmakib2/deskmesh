"""Windows low-level input hooks and SendInput injection.

Hooks live on the main thread, which must pump Windows messages. Their callbacks
never perform network I/O. Returning from the process removes the hooks.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

LLKHF_EXTENDED = 0x01
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x204, 0x205
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_MOUSEWHEEL = 0x207, 0x208, 0x20A
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_WHEEL = 0x0020, 0x0040, 0x0800

KEY_NAMES = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23}


def parse_hotkey(value: str) -> frozenset[int]:
    try:
        result = frozenset(KEY_NAMES[part.strip().lower()] for part in value.split("+"))
    except KeyError as exc:
        raise ValueError(f"Unsupported hotkey key: {exc.args[0]}") from exc
    if len(result) < 2:
        raise ValueError("Hotkeys need at least two different keys")
    return result


def _send(inp: INPUT) -> None:
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "SendInput failed")


def inject_key(vk: int, pressed: bool, extended: bool = False) -> None:
    flags = (0 if pressed else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send(INPUT(1, INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, 0))))


def inject_move(dx: int, dy: int) -> None:
    _send(INPUT(0, INPUTUNION(mi=MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0))))


def inject_button(button: str, pressed: bool) -> None:
    flags = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP), "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP), "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}[button][not pressed]
    _send(INPUT(0, INPUTUNION(mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))))


def inject_wheel(delta: int) -> None:
    _send(INPUT(0, INPUTUNION(mi=MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, 0))))


class InputHooks:
    def __init__(self, on_event: Callable[[dict | None], bool], on_switch: Callable[[str], bool], hotkeys: dict[str, str]) -> None:
        self.on_event = on_event
        self.on_switch = on_switch
        self.hotkeys = {name: parse_hotkey(combo) for name, combo in hotkeys.items()}
        self.recovery_hotkey = parse_hotkey("ctrl+alt+home")
        if len(set(self.hotkeys.values())) != len(self.hotkeys):
            raise ValueError("Hotkeys must be unique")
        self.pressed: set[int] = set()
        self.anchor: POINT | None = None
        # Values indicate whether a key-up must reach the local PC. When
        # switching away, Ctrl/Alt were already pressed locally.
        self.hotkey_latch: dict[int, bool] = {}
        self.poll_latch: set[str] = set()
        self.reinstall_after_release = False
        self.keyboard_hook = None
        self.mouse_hook = None
        self._keyboard_callback = HOOKPROC(self._keyboard)
        self._mouse_callback = HOOKPROC(self._mouse)

    def install(self) -> None:
        module = kernel32.GetModuleHandleW(None)
        self.keyboard_hook = user32.SetWindowsHookExW(13, self._keyboard_callback, module, 0)
        self.mouse_hook = user32.SetWindowsHookExW(14, self._mouse_callback, module, 0)
        if not self.keyboard_hook or not self.mouse_hook:
            self.close()
            raise OSError(ctypes.get_last_error(), "Unable to install input hooks")

    def close(self) -> None:
        for hook in (self.keyboard_hook, self.mouse_hook):
            if hook:
                user32.UnhookWindowsHookEx(hook)
        self.keyboard_hook = self.mouse_hook = None

    def pump(self) -> None:
        msg = wintypes.MSG()
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == 0x0012:  # WM_QUIT
                    return
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            self._poll_hotkeys()
            time.sleep(0.02)  # Also lets Python handle Ctrl+C while idle.

    def _poll_hotkeys(self) -> None:
        choices = (("emergency", self.recovery_hotkey), ("emergency", self.hotkeys["emergency"]), ("primary", self.hotkeys["primary"]), ("secondary", self.hotkeys["secondary"]))
        active: set[str] = set()
        for action, combo in choices:
            if all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in combo):
                active.add(action)
                if action not in self.poll_latch:
                    self.poll_latch.add(action)
                    logging.warning("Switch hotkey detected by fallback (%s); checking input hooks", action)
                    self.on_switch(action)
                    if action == "secondary":
                        point = POINT()
                        if user32.GetCursorPos(ctypes.byref(point)):
                            self.anchor = point
                    else:
                        self.anchor = None
                    self.reinstall_after_release = True
        self.poll_latch = active
        if self.reinstall_after_release and not active:
            self.close()
            self.install()
            self.pressed.clear()
            self.hotkey_latch.clear()
            self.reinstall_after_release = False
            logging.info("Input hooks reinstalled")

    def _keyboard(self, code: int, message: int, param: int) -> int:
        if code < 0:
            return user32.CallNextHookEx(self.keyboard_hook, code, message, param)
        try:
            event = ctypes.cast(param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if event.flags & LLKHF_INJECTED:
                return user32.CallNextHookEx(self.keyboard_hook, code, message, param)
            down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
            up = message in (WM_KEYUP, WM_SYSKEYUP)
            if not (down or up):
                return user32.CallNextHookEx(self.keyboard_hook, code, message, param)
            vk = int(event.vkCode)
            if down:
                if vk in self.hotkey_latch:
                    return 1
                self.pressed.add(vk)
                # Generic modifier VKs and left/right variants both match.
                normalized = {0xA0: 0x10, 0xA1: 0x10, 0xA2: 0x11, 0xA3: 0x11, 0xA4: 0x12, 0xA5: 0x12, 0x5C: 0x5B}
                current = {normalized.get(key, key) for key in self.pressed}
                if vk in (0x25, 0x27, 0x24):
                    logging.debug("Hotkey candidate VK=%#x, pressed=%s", vk, sorted(current))
                choices = (("emergency", self.recovery_hotkey), ("emergency", self.hotkeys["emergency"]), ("primary", self.hotkeys["primary"]), ("secondary", self.hotkeys["secondary"]))
                for action, combo in choices:
                    if combo <= current and normalized.get(vk, vk) in combo:
                        logging.info("Switch hotkey detected: %s", action)
                        self.poll_latch.add(action)
                        was_local = self.on_switch(action)
                        for physical in self.pressed:
                            if normalized.get(physical, physical) in combo:
                                self.hotkey_latch[physical] = was_local and physical != vk
                        if action == "secondary":
                            point = POINT()
                            if user32.GetCursorPos(ctypes.byref(point)):
                                self.anchor = point
                        else:
                            self.anchor = None
                        return 1
            else:
                self.pressed.discard(vk)
                if vk in self.hotkey_latch:
                    passthrough = self.hotkey_latch.pop(vk)
                    return user32.CallNextHookEx(self.keyboard_hook, code, message, param) if passthrough else 1
            suppress = self.on_event({"type": "keyboard", "vk": vk, "pressed": down, "extended": bool(event.flags & LLKHF_EXTENDED)})
            return 1 if suppress else user32.CallNextHookEx(self.keyboard_hook, code, message, param)
        except Exception:
            logging.exception("Keyboard hook failed; restoring local input")
            self.on_switch("emergency")
            return user32.CallNextHookEx(self.keyboard_hook, code, message, param)

    def _mouse(self, code: int, message: int, param: int) -> int:
        if code < 0:
            return user32.CallNextHookEx(self.mouse_hook, code, message, param)
        try:
            event = ctypes.cast(param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if event.flags & LLMHF_INJECTED:
                return user32.CallNextHookEx(self.mouse_hook, code, message, param)
            payload = None
            if message == WM_MOUSEMOVE:
                cursor = self.anchor
                if cursor is None:
                    cursor = POINT()
                    user32.GetCursorPos(ctypes.byref(cursor))
                dx, dy = event.pt.x - cursor.x, event.pt.y - cursor.y
                if dx or dy:
                    payload = {"type": "mouse_move", "dx": max(-32768, min(32767, dx)), "dy": max(-32768, min(32767, dy))}
            elif message in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                button = "left" if message in (WM_LBUTTONDOWN, WM_LBUTTONUP) else "right" if message in (WM_RBUTTONDOWN, WM_RBUTTONUP) else "middle"
                payload = {"type": "mouse_button", "button": button, "pressed": message in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN)}
            elif message == WM_MOUSEWHEEL:
                delta = ctypes.c_int16(event.mouseData >> 16).value
                payload = {"type": "mouse_wheel", "delta": delta}
            suppress = self.on_event(payload) if payload else self.on_event(None)
            if suppress and message == WM_MOUSEMOVE and self.anchor is not None:
                actual = POINT()
                if user32.GetCursorPos(ctypes.byref(actual)) and (actual.x != self.anchor.x or actual.y != self.anchor.y):
                    user32.SetCursorPos(self.anchor.x, self.anchor.y)
            return 1 if suppress else user32.CallNextHookEx(self.mouse_hook, code, message, param)
        except Exception:
            logging.exception("Mouse hook failed; restoring local input")
            self.on_switch("emergency")
            return user32.CallNextHookEx(self.mouse_hook, code, message, param)
