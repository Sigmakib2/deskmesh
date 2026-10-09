"""Windows low-level input hooks and SendInput injection.

Hooks live on the main thread, which must pump Windows messages. Their callbacks
never perform network I/O or blocking logging. Returning from the process removes
the hooks.

Mouse movement is captured as a delta from a cursor anchored at the centre of the
primary's virtual desktop, so no direction can be clipped by a screen edge, and is
replayed on the secondary as an absolute virtual-desktop position so that the
secondary's pointer acceleration is not applied a second time.
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
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

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
MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
ABSOLUTE_RANGE = 65535

KEY_NAMES = {
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E, "escape": 0x1B, "esc": 0x1B,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "backspace": 0x08,
    "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C, "capslock": 0x14,
    # Letters, digits and function keys so custom hotkeys are not limited to modifiers.
    **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(digit): 0x30 + digit for digit in range(10)},
    **{f"f{index}": 0x6F + index for index in range(1, 25)},
}


def enable_dpi_awareness() -> None:
    """Make GetCursorPos, GetSystemMetrics and SendInput share one coordinate space.

    Without this a scaled display reports virtualised cursor coordinates while
    absolute SendInput still spans the real desktop, which offsets remote movement.
    """
    context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if context is not None:
        context.argtypes = [wintypes.HANDLE]
        context.restype = wintypes.BOOL
        try:
            if context(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                return
        except OSError:
            pass
    legacy = getattr(user32, "SetProcessDPIAware", None)
    if legacy is not None:
        try:
            legacy()
        except OSError:
            pass


enable_dpi_awareness()


def virtual_screen() -> tuple[int, int, int, int]:
    """Return the virtual desktop as (left, top, width, height)."""
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, max(1, width), max(1, height)


def cursor_position() -> POINT | None:
    point = POINT()
    return point if user32.GetCursorPos(ctypes.byref(point)) else None


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


class RemoteCursor:
    """Absolute pointer position driven by deltas from the primary.

    Accumulating the deltas here and injecting an absolute virtual-desktop
    coordinate bypasses this PC's pointer acceleration, so one unit of primary
    movement is always one unit of secondary movement.
    """

    def __init__(self) -> None:
        self.x: int | None = None
        self.y: int | None = None

    def reset(self) -> None:
        self.x = self.y = None

    def move(self, dx: int, dy: int) -> None:
        left, top, width, height = virtual_screen()
        if self.x is None or self.y is None:
            start = cursor_position()
            self.x = start.x if start else left + width // 2
            self.y = start.y if start else top + height // 2
        self.x = min(left + width - 1, max(left, self.x + dx))
        self.y = min(top + height - 1, max(top, self.y + dy))
        nx = round((self.x - left) * ABSOLUTE_RANGE / max(1, width - 1))
        ny = round((self.y - top) * ABSOLUTE_RANGE / max(1, height - 1))
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        _send(INPUT(0, INPUTUNION(mi=MOUSEINPUT(nx, ny, 0, flags, 0, 0))))


_remote_cursor = RemoteCursor()


def reset_cursor() -> None:
    """Forget the tracked position so the next move starts from the real cursor."""
    _remote_cursor.reset()


def inject_move(dx: int, dy: int) -> None:
    _remote_cursor.move(dx, dy)


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
        self.restore_cursor: POINT | None = None
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

    def _enter_remote(self) -> None:
        """Park the cursor mid-desktop so no direction is clipped by a screen edge."""
        if self.restore_cursor is None:
            # Switching to the secondary twice (the fallback poller can repeat it)
            # must not overwrite where the user actually left the cursor.
            self.restore_cursor = cursor_position()
        left, top, width, height = virtual_screen()
        centre = POINT(left + width // 2, top + height // 2)
        self.anchor = centre if user32.SetCursorPos(centre.x, centre.y) else cursor_position()

    def _exit_remote(self) -> None:
        self.anchor = None
        if self.restore_cursor is not None:
            user32.SetCursorPos(self.restore_cursor.x, self.restore_cursor.y)
            self.restore_cursor = None

    def _switch(self, action: str) -> bool:
        was_local = self.on_switch(action)
        if action == "secondary":
            self._enter_remote()
        else:
            self._exit_remote()
        return was_local

    def _poll_hotkeys(self) -> None:
        choices = (("emergency", self.recovery_hotkey), ("emergency", self.hotkeys["emergency"]), ("primary", self.hotkeys["primary"]), ("secondary", self.hotkeys["secondary"]))
        active: set[str] = set()
        for action, combo in choices:
            if all(user32.GetAsyncKeyState(vk) & 0x8000 for vk in combo):
                active.add(action)
                if action not in self.poll_latch:
                    self.poll_latch.add(action)
                    logging.warning("Switch hotkey detected by fallback (%s); checking input hooks", action)
                    self._switch(action)
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
                choices = (("emergency", self.recovery_hotkey), ("emergency", self.hotkeys["emergency"]), ("primary", self.hotkeys["primary"]), ("secondary", self.hotkeys["secondary"]))
                for action, combo in choices:
                    if combo <= current and normalized.get(vk, vk) in combo:
                        logging.info("Switch hotkey detected: %s", action)
                        self.poll_latch.add(action)
                        was_local = self._switch(action)
                        for physical in self.pressed:
                            if normalized.get(physical, physical) in combo:
                                self.hotkey_latch[physical] = was_local and physical != vk
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
            self._switch("emergency")
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
                    cursor = cursor_position()
                    if cursor is None:
                        return user32.CallNextHookEx(self.mouse_hook, code, message, param)
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
                actual = cursor_position()
                if actual is not None and (actual.x != self.anchor.x or actual.y != self.anchor.y):
                    user32.SetCursorPos(self.anchor.x, self.anchor.y)
            return 1 if suppress else user32.CallNextHookEx(self.mouse_hook, code, message, param)
        except Exception:
            logging.exception("Mouse hook failed; restoring local input")
            self._switch("emergency")
            return user32.CallNextHookEx(self.mouse_hook, code, message, param)
