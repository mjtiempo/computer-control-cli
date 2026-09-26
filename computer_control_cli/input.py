"""Keyboard and pointer input.

Two routes, and the choice between them is part of the contract:

* ``wtype`` — real-keyboard semantics: Chromium opens a TAB on a plain
  ``ctrl+t``, while ydotool's virtual device opens a window. Plain
  ctrl/alt/shift combos and typing take this route.
* ``ydotool`` (uinput) — super chords, which wtype cannot press, and the keys
  wtype has no name for. ``force_ydotool`` asks for it explicitly.

The pointer's one provable effect is the warp: ``move`` reads the cursor back
and refuses a mapping that lands somewhere else. Button and key events are
honestly ``unverifiable``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.outcome import Outcome  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

ABS_X_MAX = 960  # ydotool's virtual device ABS range (one anchor monitor)
ABS_Y_MAX = 540
POINTER_TOLERANCE_PX = 2
REL_TRIES = 16
SETTLE_TRIES = 8
SETTLE_DELAY = 0.05
WARP_DELAY = 0.02


@dataclass(frozen=True)
class Combo:
    """A parsed key chord: modifiers first, then the key."""

    mods: tuple[str, ...]
    key: str


class KeyMap:
    """Key names to the codes each route needs. Tables, not arithmetic."""

    MODS = {"ctrl": 29, "control": 29, "alt": 56, "super": 125, "win": 125,
            "mod": 125, "shift": 42}
    NAMES = {
        "return": 28, "enter": 28, "space": 57, "tab": 15, "escape": 1,
        "esc": 1, "backspace": 14, "delete": 111, "del": 111, "insert": 110,
        "up": 103, "down": 108, "left": 105, "right": 106,
        "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
        "print": 99, "pause": 119, "capslock": 58, "menu": 127,
        "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
        "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
        "kp_enter": 96, "kp_add": 78, "kp_subtract": 74,
        "minus": 12, "equal": 13, "bracketleft": 26, "bracketright": 27,
        "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
        "comma": 51, "period": 52, "slash": 53,
    }
    LETTERS = {
        "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34,
        "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49,
        "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22,
        "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
    }
    XKB = {
        "return": "Return", "enter": "Return", "space": "space", "tab": "Tab",
        "escape": "Escape", "esc": "Escape", "backspace": "BackSpace",
        "delete": "Delete", "del": "Delete", "insert": "Insert",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "home": "Home", "end": "End", "pageup": "Page_Up",
        "pagedown": "Page_Down", "print": "Print", "pause": "Pause",
        "capslock": "Caps_Lock", "menu": "Menu", "minus": "minus",
        "equal": "equal", "period": "period", "comma": "comma",
        "slash": "slash", "backslash": "backslash", "semicolon": "semicolon",
        "apostrophe": "apostrophe", "grave": "grave",
        "bracketleft": "bracketleft", "bracketright": "bracketright",
        "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5",
        "f6": "F6", "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10",
        "f11": "F11", "f12": "F12", "kp_enter": "KP_Enter",
        "kp_add": "KP_Add", "kp_subtract": "KP_Subtract",
    }
    BUTTONS = {"L": "0xC0", "R": "0xC1", "M": "0xC2"}

    @classmethod
    def evdev_code(cls, name: str) -> int:
        lowered = str(name).lower()
        if lowered in cls.NAMES:
            return cls.NAMES[lowered]
        if len(lowered) == 1:
            if lowered in cls.LETTERS:
                return cls.LETTERS[lowered]
            if "0" <= lowered <= "9":
                return 11 if lowered == "0" else ord(lowered) - 47
            if lowered == "-":
                return 12
        raise ControlError("key-unknown", f"key {name!r}: unknown key name")

    @classmethod
    def wtype_name(cls, key: str) -> Optional[str]:
        lowered = str(key).lower()
        if lowered in cls.XKB:
            return cls.XKB[lowered]
        if len(lowered) == 1 and lowered.isalnum():
            return lowered
        return None

    @classmethod
    def parse(cls, combo: str) -> Combo:
        parts = [part.strip().lower() for part in str(combo).split("+")
                 if part.strip()]
        mods: list[str] = []
        key: Optional[str] = None
        for part in parts:
            if part in cls.MODS:
                mods.append(part)
            elif key is None:
                key = part
            else:
                raise ControlError("bad-combo",
                                   f"key {combo!r}: multiple key parts")
        if key is None:
            raise ControlError("bad-combo", f"key {combo!r}: empty")
        cls.evdev_code(key)
        return Combo(tuple(mods), key)

    @classmethod
    def codes(cls, combo: Combo) -> list[int]:
        return [cls.MODS[mod] for mod in combo.mods] \
            + [cls.evdev_code(combo.key)]


class Pointer:
    """ydotool's virtual pointer: warp, click, wheel."""

    def __init__(self, session: Session):
        self.session = session

    def position(self) -> tuple[int, int]:
        return self.session.cursor()

    def scale(self) -> tuple[float, float]:
        anchor = self.session.anchor_monitor()
        if anchor is None or anchor.w <= 0 or anchor.h <= 0:
            raise ControlError(
                "move-not-verified",
                "pointer: the compositor reports no monitor with a size — "
                "there is nothing to warp on")
        return ABS_X_MAX / anchor.w, ABS_Y_MAX / anchor.h

    def move_raw(self, raw_x: int, raw_y: int) -> None:
        proc = self.session.run(
            ["ydotool", "mousemove", "--absolute",
             "-x", str(int(raw_x)), "-y", str(int(raw_y))])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"ydotool mousemove failed: "
                               f"{(proc.stderr or '').strip()}")

    def move_logical(self, x: int, y: int) -> None:
        scale_x, scale_y = self.scale()
        raw_x = min(max(int(round(x * scale_x)), 0), ABS_X_MAX)
        raw_y = min(max(int(round(y * scale_y)), 0), ABS_Y_MAX)
        self.move_raw(raw_x, raw_y)

    def move_rel(self, dx: float, dy: float) -> None:
        scale_x, scale_y = self.scale()
        raw_x, raw_y = int(round(dx * scale_x)), int(round(dy * scale_y))
        if raw_x == 0 and raw_y == 0:
            return
        proc = self.session.run(["ydotool", "mousemove",
                                 "-x", str(raw_x), "-y", str(raw_y)])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"ydotool relative move failed: "
                               f"{(proc.stderr or '').strip()}")

    def button(self, code: str) -> None:
        proc = self.session.run(["ydotool", "click", code])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"ydotool click failed: "
                               f"{(proc.stderr or '').strip()}")

    BUTTON_EVDEV = {"0xC0": 272, "0xC1": 273, "0xC2": 274}

    def press(self, code: str) -> None:
        self._button_state(code, 1)

    def release(self, code: str) -> None:
        self._button_state(code, 0)

    def _button_state(self, code: str, value: int) -> None:
        """Hold or release a button through a one-shot evdev device.

        ``ydotool click`` is a full press-and-release, so a drag needs the
        two states separately; uinput is the only route that exposes them.
        """
        button = self.BUTTON_EVDEV.get(code)
        if button is None:
            raise ControlError("bad-args", f"bad button code {code!r}")
        script = (
            "from evdev import UInput, ecodes\n"
            "from time import sleep\n"
            f"ui = UInput({{1: [{button}]}}, name='ccc-button')\n"
            f"ui.write(ecodes.EV_KEY, {button}, {value}); ui.syn()\n"
            "sleep(0.02); ui.close()\n"
        )
        proc = self.session.run(["python3", "-c", script])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"button state failed: "
                               f"{(proc.stderr or '').strip()}")

    def wheel(self, direction: str, amount: int) -> None:
        click, axis = {"up": (1, 8), "down": (-1, 8),
                       "left": (-1, 6), "right": (1, 6)}[direction]
        script = (
            "from evdev import UInput, ecodes\n"
            "from time import sleep\n"
            f"ui = UInput({{2: [{axis}]}}, name='ccc-wheel')\n"
            f"for _ in range({amount}):\n"
            f"    ui.write(ecodes.EV_REL, "
            f"ecodes.REL_WHEEL if {axis} == 8 else ecodes.REL_HWHEEL, "
            f"{click}); ui.syn()\n"
            "sleep(0.05); ui.close()\n"
        )
        proc = self.session.run(["python3", "-c", script])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"scroll failed: {(proc.stderr or '').strip()}")


class Keyboard:
    """wtype and ydotool key events."""

    # Both sides of every modifier, released by `--clearmodifiers`. Releasing
    # a key that is not held is harmless; a modifier left held colours the
    # next action.
    MODIFIER_RELEASE_CODES = (29, 97, 56, 100, 42, 54, 125, 126)

    def __init__(self, session: Session):
        self.session = session

    def clear_modifiers(self) -> None:
        """Release every modifier. ydotool only: its state persists."""
        sequence = [f"{code}:0" for code in self.MODIFIER_RELEASE_CODES]
        proc = self.session.run(["ydotool", "key", *sequence])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"ydotool key failed: "
                               f"{(proc.stderr or '').strip()}")

    def chord(self, codes: list[int]) -> None:
        sequence = [f"{code}:1" for code in codes] \
            + [f"{code}:0" for code in reversed(codes)]
        proc = self.session.run(["ydotool", "key", *sequence])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"ydotool key failed: "
                               f"{(proc.stderr or '').strip()}")

    def wtype_chord(self, mods: tuple, keysym: str) -> None:
        argv = (["wtype"]
                + sum((["-M", mod] for mod in mods), [])
                + ["-k", keysym]
                + sum((["-m", mod] for mod in reversed(mods)), []))
        proc = self.session.run(argv)
        if proc.returncode != 0:
            raise ControlError("type",
                               f"wtype failed: {(proc.stderr or '').strip()}")

    def type_text(self, text: str, delay_ms: int = 0) -> None:
        for argv in self.type_plan(text, delay_ms):
            proc = self.session.run(argv)
            if proc.returncode != 0:
                raise ControlError("type",
                                   f"wtype failed: "
                                   f"{(proc.stderr or '').strip()}")

    def key_state(self, combo: Combo, down: bool) -> None:
        """Press (``down``) or release one chord's keys and LEAVE them so.

        Always ydotool: its virtual keyboard's state persists across calls,
        while wtype releases everything it pressed when the process exits —
        a wtype "hold" would not outlive the call that made it.
        """
        sequence = [f"{code}:{1 if down else 0}"
                    for code in KeyMap.codes(combo)]
        proc = self.session.run(["ydotool", "key", *sequence])
        if proc.returncode != 0:
            raise ControlError("ydotool",
                               f"ydotool key failed: "
                               f"{(proc.stderr or '').strip()}")

    @staticmethod
    def type_plan(text: str, delay_ms: int = 0) -> list[list[str]]:
        """The wtype invocations that type ``text`` (newlines become Return).

        A line starting with ``-`` gets its own ``wtype --`` call, because
        wtype parses it as an option otherwise. ``delay_ms`` becomes wtype's
        own per-keystroke delay.
        """
        prefix = ["-d", str(int(delay_ms))] if delay_ms else []
        segments = text.split("\n")
        if any(segment.startswith("-") for segment in segments):
            plan: list[list[str]] = []
            for index, segment in enumerate(segments):
                if index > 0:
                    plan.append(["wtype", *prefix, "-k", "Return"])
                if segment:
                    plan.append(["wtype", *prefix, "--", segment])
            return plan
        argv = ["wtype", *prefix]
        for index, segment in enumerate(segments):
            if index > 0:
                argv += ["-k", "Return"]
            if segment:
                argv += [segment]
        return [argv]


class Input:
    """Pointer and keyboard actions, verified where an effect can be read."""

    def __init__(self, session: Session):
        self.session = session
        self.pointer = Pointer(session)
        self.keyboard = Keyboard(session)

    # --------------------------------------------------------------- reads
    def pos(self) -> dict:
        x, y = self.pointer.position()
        return {"x": x, "y": y}

    # -------------------------------------------------------------- motion
    def move(self, x: int, y: int, clearmodifiers: bool = False) -> Outcome:
        """Move the pointer and PROVE the warp landed, or refuse."""
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        want = (int(x), int(y))
        self.warp(want[0], want[1])
        return Outcome.confirmed("px", x=want[0], y=want[1])

    def warp(self, x: int, y: int) -> None:
        want = (int(x), int(y))
        box = self.session.desktop_bbox()
        if not self._inside(box, want):
            raise ControlError(
                "move-not-verified",
                f"pointer: {want} is outside the desktop {box} — the cursor "
                "is clamped to the monitors' bounding box")
        monitors = self.session.monitors()
        if monitors and not any(monitor.contains(*want) for monitor in monitors):
            raise ControlError(
                "move-not-verified",
                f"pointer: {want} is on no monitor — the box between them is "
                "not desktop")
        reach = self.session.pointer_reach()
        absolute = self._inside(reach, want)
        if absolute:
            self.pointer.move_logical(*want)
        else:
            self._nudge(want)
        time.sleep(WARP_DELAY)
        got = self.pointer.position()
        if self._offset(got, want) > POINTER_TOLERANCE_PX:
            got = self._settle()
        if absolute and self._offset(got, want) > POINTER_TOLERANCE_PX:
            self._nudge(want)
            got = self._settle()
        if self._offset(got, want) > POINTER_TOLERANCE_PX:
            raise ControlError(
                "move-not-verified",
                f"pointer: asked for {want} and the cursor settled at {got} "
                f"({self._offset(got, want)}px off, tolerance "
                f"{POINTER_TOLERANCE_PX})")

    def _nudge(self, want: tuple[int, int]) -> None:
        for _ in range(REL_TRIES):
            now = self.pointer.position()
            if self._offset(now, want) <= POINTER_TOLERANCE_PX:
                return
            self.pointer.move_rel(want[0] - now[0], want[1] - now[1])
            time.sleep(WARP_DELAY)

    def _settle(self) -> tuple[int, int]:
        last = self.pointer.position()
        for _ in range(SETTLE_TRIES):
            time.sleep(SETTLE_DELAY)
            now = self.pointer.position()
            if now == last:
                return now
            last = now
        return last

    def click(self, x: Optional[int] = None, y: Optional[int] = None,
              button: str = "L", double: bool = False,
              clearmodifiers: bool = False) -> Outcome:
        """Click at a point, or at the pointer when no coordinates are given."""
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        button = str(button).upper()
        code = KeyMap.BUTTONS.get(button)
        if code is None:
            raise ControlError("bad-args",
                               f"click: bad button {button!r} "
                               "(expected L, R or M)")
        if (x is None) != (y is None):
            raise ControlError("bad-args",
                               "click: coordinates are X Y (got one only)")
        if x is not None and y is not None:
            self.warp(x, y)
        for _ in range(2 if double else 1):
            self.pointer.button(code)
            if double:
                time.sleep(0.05)
        return Outcome.unverifiable("px", button=button, double=double,
                                    x=x, y=y)

    def scroll(self, direction: str, amount: int,
               x: Optional[int] = None, y: Optional[int] = None,
               clearmodifiers: bool = False) -> Outcome:
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        if direction not in ("up", "down", "left", "right"):
            raise ControlError("bad-args",
                               f"scroll: bad direction {direction!r}")
        if (x is None) != (y is None):
            raise ControlError("bad-args",
                               "scroll: coordinates are X Y (got one only)")
        amount = int(amount)
        if amount < 1 or amount > 10_000:
            raise ControlError("bad-args",
                               f"scroll: AMOUNT must be 1..10000, got {amount}")
        if x is not None and y is not None:
            self.warp(x, y)
        self.pointer.wheel(direction, amount)
        return Outcome.unverifiable("pt", direction=direction, amount=amount)

    # ------------------------------------------------------------ keyboard
    def key(self, combo: str, force_ydotool: bool = False,
            clearmodifiers: bool = False) -> Outcome:
        """Press a chord through the route that fits it."""
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        parsed = KeyMap.parse(combo)
        keysym = KeyMap.wtype_name(parsed.key)
        if (not force_ydotool and keysym
                and all(mod in ("ctrl", "control", "alt", "shift")
                        for mod in parsed.mods)):
            self.keyboard.wtype_chord(parsed.mods, keysym)
            route = "wtype"
        else:
            self.keyboard.chord(KeyMap.codes(parsed))
            route = "ydotool"
        return Outcome.unverifiable("input", combo=combo, route=route)

    def type_text(self, text: str, delay_ms: int = 0,
                  clearmodifiers: bool = False) -> Outcome:
        """Type text; both a literal ``\\n`` and a real newline mean Return."""
        replaced = str(text).replace("\\n", "\n")
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        self.keyboard.type_text(replaced, delay_ms)
        return Outcome.unverifiable("input", chars=len(str(text)),
                                    delay_ms=delay_ms)

    def key_down(self, combo: str, clearmodifiers: bool = False) -> Outcome:
        """Hold a chord's keys down (a drag for the keyboard)."""
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        self.keyboard.key_state(KeyMap.parse(combo), down=True)
        return Outcome.unverifiable("input", combo=combo, state="down")

    def key_up(self, combo: str, clearmodifiers: bool = False) -> Outcome:
        """Release a chord's keys."""
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        self.keyboard.key_state(KeyMap.parse(combo), down=False)
        return Outcome.unverifiable("input", combo=combo, state="up")

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "L",
             duration_ms: int = 300, steps: int = 12,
             clearmodifiers: bool = False) -> Outcome:
        """Press at the start, move to the end, release — release ALWAYS.

        The release is in a ``finally``: a failed movement must not leave the
        user's mouse button held down.
        """
        upper = str(button).upper()
        code = KeyMap.BUTTONS.get(upper)
        if code is None:
            raise ControlError("bad-args",
                               f"drag: bad button {button!r} "
                               "(expected L, R or M)")
        if clearmodifiers:
            self.keyboard.clear_modifiers()
        start_x, start_y = int(x1), int(y1)
        end_x, end_y = int(x2), int(y2)
        steps = max(1, int(steps))
        self.warp(start_x, start_y)
        self.pointer.press(code)
        try:
            for index in range(1, steps + 1):
                fraction = index / steps
                self.pointer.move_logical(
                    int(round(start_x + (end_x - start_x) * fraction)),
                    int(round(start_y + (end_y - start_y) * fraction)))
                if duration_ms:
                    time.sleep(max(0.0, float(duration_ms)) / 1000.0 / steps)
        finally:
            self.pointer.release(code)
        return Outcome.unverifiable("px", button=upper, x1=start_x, y1=start_y,
                                    x2=end_x, y2=end_y)

    # ------------------------------------------------------------ internals
    @staticmethod
    def _inside(rect: tuple, point: tuple) -> bool:
        x, y, w, h = rect
        return w > 0 and h > 0 and x <= point[0] < x + w and y <= point[1] < y + h

    @staticmethod
    def _offset(got: tuple, want: tuple) -> int:
        return max(abs(got[0] - want[0]), abs(got[1] - want[1]))
