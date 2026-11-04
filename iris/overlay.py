"""A small panel for voice mode, bottom-center above the taskbar: what you said and what Iris answers.

Opt-in: `overlay = true` in config.toml or IRIS_OVERLAY=1. It runs as its own process, `python -m iris.overlay`, and
reads one JSON event per line on stdin, so a crash or a Python without Tk never touches the voice loop. Iris types into
and looks at other windows, so the panel never takes focus, stays out of the taskbar and is left out of screenshots.
While Iris listens, thinks or talks it shows a round cancel button; a click writes one JSON line back on stdout.
Stdlib only: tkinter and ctypes.

Events in: listening, heard (text), thinking, answer (text), idle, interrupted. Events out: cancel."""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any

from iris import config

TITLE = "Iris overlay"  # list_windows and read_window skip the window with this title
HIDE_AFTER = 4.0  # seconds the last answer stays up after Iris stops talking
FADE = 0.4  # the last part of HIDE_AFTER, in which the panel fades out
ALPHA = 0.93
PROMPT_CHARS, ANSWER_CHARS, ANSWER_LINES = 160, 300, 6  # longer text keeps its end, which is what is being said
EVENTS = {"listening", "heard", "thinking", "answer", "idle", "interrupted"}
ACTIVE = {"listening", "thinking", "speaking"}  # the panel modes in which there is a request to cancel
CANCEL = json.dumps({"event": "cancel"}) + "\n"  # the line the overlay writes when its cancel button is clicked
_ON = {"1", "true", "on", "yes"}


def enabled() -> bool:
    """IRIS_OVERLAY when set (1 or 0), else `overlay = true` in config.toml. Off by default."""
    env = os.environ.get("IRIS_OVERLAY", "").strip().lower()
    if env:
        return env in _ON
    value = config.raw().get("overlay")
    return value is True or (isinstance(value, str) and value.strip().lower() in _ON)


def tail(text: str, chars: int, lines: int = 3) -> str:
    """Text that fits the panel: blank lines and runs of spaces go, and a long text keeps its end behind an ellipsis."""
    rows = [" ".join(r.split()) for r in text.splitlines()]
    rows = [r for r in rows if r]
    cut = len(rows) > lines
    out = "\n".join(rows[-lines:])
    if len(out) > chars:
        out, cut = out[-(chars - 1):].lstrip(), True
    return "…" + out if cut else out


def parse(line: str) -> dict | None:
    """One event from one line, or None for anything that is not a known event."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("event") not in EVENTS:
        return None
    return event


@dataclass
class View:
    """What the panel shows. Plain data driven by events and a clock, so it is tested without a display."""

    mode: str = "hidden"  # hidden, listening, thinking, speaking or done
    prompt: str = ""
    answer: str = ""
    hide_at: float | None = None

    @property
    def visible(self) -> bool:
        """Whether the panel is up at all."""
        return self.mode != "hidden"

    def apply(self, event: dict, now: float) -> None:
        """Fold one event into the view: a new request clears the old one, and idle starts the fade-out clock."""
        kind, text = event.get("event"), str(event.get("text") or "")
        if kind in ("listening", "interrupted"):  # a new request starts: the old one goes
            self.mode, self.prompt, self.answer = "listening", "", ""
        elif kind == "heard":
            self.mode, self.prompt, self.answer = "thinking", text or "(nothing heard)", ""
        elif kind == "thinking":
            self.mode = "thinking"
        elif kind == "answer":
            self.mode, self.answer = "speaking", text
        elif kind == "idle":
            if self.visible:
                self.mode, self.hide_at = "done", now + HIDE_AFTER
            return
        else:
            return
        self.hide_at = None

    def tick(self, now: float) -> None:
        """A done panel hides itself once its clock runs out."""
        if self.hide_at is not None and now >= self.hide_at:
            self.mode, self.prompt, self.answer, self.hide_at = "hidden", "", "", None

    def alpha(self, now: float) -> float:
        """Full opacity, fading to nothing over the last FADE seconds before it hides."""
        if self.hide_at is None:
            return ALPHA
        return ALPHA * min(1.0, max(0.0, (self.hide_at - now) / FADE))

    def lines(self) -> tuple[str, str]:
        """(the muted line about the user, Iris's line), as the panel writes them."""
        top = f"You: {tail(self.prompt, PROMPT_CHARS)}" if self.prompt else ""
        if self.mode == "listening":
            return top or "Listening...", ""
        if self.answer:
            return top, tail(self.answer, ANSWER_CHARS, ANSWER_LINES)
        return top, "Thinking..." if self.mode == "thinking" else ""


def dots(mode: str, t: float) -> list[tuple[float, float, float]]:
    """The indicator at time t: (x, y, radius) per dot, in panel units around its centre.
    One pulsing dot while listening, three bouncing ones while thinking, three bars of sound while speaking."""
    if mode == "listening":
        return [(0.0, 0.0, 5 + 2 * math.sin(t * 6))]
    if mode == "thinking":
        return [((i - 1) * 11.0, -3 * max(0.0, math.sin(t * 7 - i * 0.9)), 3.5) for i in range(3)]
    if mode == "speaking":  # drawn as bars: the radius is half the height
        return [((i - 1) * 9.0, 0.0, 3 + 4 * abs(math.sin(t * 9 + i * 1.7))) for i in range(3)]
    return [((i - 1) * 11.0, 0.0, 3.0) for i in range(3)]


class Overlay:
    """The voice loop's side. send() queues an event and returns at once; a daemon thread writes it to the overlay
    process. When that process is missing, dead or stuck, events go nowhere and the voice loop never notices.
    Another daemon thread reads what the overlay writes back: a click on its cancel button calls on_cancel."""

    def __init__(self, proc: subprocess.Popen | None = None, on_cancel: Callable[[], None] | None = None):
        self._proc = proc
        self.on_cancel = on_cancel
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=64)
        if proc is not None:
            threading.Thread(target=self._write, args=(proc,), daemon=True, name="overlay").start()
            if getattr(proc, "stdout", None) is not None:
                threading.Thread(target=self._read, args=(proc.stdout,), daemon=True, name="overlay-read").start()

    def send(self, event: str, text: str = "") -> None:
        """Queue an event for the overlay process; it returns at once, so a slow or dead panel never blocks Iris."""
        if self._proc is None:
            return
        try:  # JSON's ASCII escapes: no console code page can garble Dutch or an emoji on the way
            self._queue.put_nowait(json.dumps({"event": event, "text": text}) + "\n")
        except queue.Full:
            pass  # the overlay stopped reading; drop events rather than wait for it

    def _write(self, proc: subprocess.Popen) -> None:
        pipe = proc.stdin
        while pipe is not None and (line := self._queue.get()) is not None:
            try:
                pipe.write(line)
                pipe.flush()
            except (OSError, ValueError):  # a broken pipe, or a pipe close() already shut
                self._proc = None
                _close_quietly(pipe)
                return

    def _read(self, pipe: IO[str]) -> None:
        """Every cancel line from the overlay calls on_cancel; ends quietly when the overlay quits or dies."""
        try:
            for line in pipe:
                if is_cancel(line) and self.on_cancel is not None:
                    try:
                        self.on_cancel()
                    except Exception as exc:  # noqa: BLE001 - a failing cancel must not stop the next one
                        print(f"(cancelling did not work: {exc!r})")
        except (OSError, ValueError):  # the pipe broke or was closed
            pass

    def close(self) -> None:
        """Let the overlay go: end of input makes it quit; one that hangs is killed after two seconds."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        # in a thread: a writer stuck on a full pipe holds its lock, and close() would wait for it forever
        threading.Thread(target=_close_quietly, args=(proc.stdin,), daemon=True).start()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()  # which also frees a stuck writer: its pipe breaks
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass


def _close_quietly(pipe: IO[str] | None) -> None:
    try:
        if pipe is not None:
            pipe.close()
    except (OSError, ValueError):  # the overlay is gone already
        pass


def is_cancel(line: str) -> bool:
    """True for the line the overlay writes when its cancel button is clicked."""
    try:
        event = json.loads(line)
    except ValueError:
        return False
    return isinstance(event, dict) and event.get("event") == "cancel"


def launch() -> Overlay:
    """The overlay process when it is switched on, else an Overlay that sends nothing. Never raises.
    Its stdout is a pipe back to Iris, for the cancel button; set on_cancel on the result to hear it."""
    if not enabled():
        return Overlay()
    log_path = config.config_path().parent / "overlay.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:  # the child keeps its own handle
            proc = subprocess.Popen([sys.executable, "-m", "iris.overlay"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=log, text=True, encoding="ascii",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as exc:
        print(f"(the overlay did not start: {exc}; carrying on without it)")
        return Overlay()
    return Overlay(proc)


# --- the overlay process ---------------------------------------------------------------------------------------------
def read_events(stream: IO[str], out: queue.Queue) -> None:
    """Every known event from stream into out, then None once Iris closes the pipe (or dies)."""
    try:
        for line in stream:
            event = parse(line)
            if event is not None:
                out.put(event)
    except (OSError, ValueError):
        pass
    out.put(None)


class Win32:
    """What Tk cannot do on Windows: no focus, also not on a click, no taskbar button, no screenshots of it."""

    GWL_EXSTYLE, GWLP_WNDPROC = -20, -4
    WM_MOUSEACTIVATE, MA_NOACTIVATE = 0x21, 3
    WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x20, 0x80, 0x40000
    WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
    WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004 and later; older ones keep showing it in screenshots
    SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
    HWND_TOPMOST, SWP_FLAGS = -1, 0x1 | 0x2 | 0x10  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE

    def __init__(self, user32: Any, hwnd: int):
        self.user32, self.hwnd = user32, hwnd
        self._wndproc: Any = None  # the ctypes callback; kept here, or Python frees it while Windows still calls it

    def no_focus(self) -> None:
        """Never the active window, no taskbar button, out of screenshots. Not click-through (WS_EX_TRANSPARENT): the
        cancel button needs the click. The see-through KEY round the panel still lets clicks through by itself."""
        style = self.user32.GetWindowLongW(self.hwnd, self.GWL_EXSTYLE)
        style = (style & ~(self.WS_EX_APPWINDOW | self.WS_EX_TRANSPARENT)) | self.WS_EX_TOOLWINDOW | \
            self.WS_EX_NOACTIVATE | self.WS_EX_LAYERED
        self.user32.SetWindowLongW(self.hwnd, self.GWL_EXSTYLE, style)
        self.user32.SetWindowDisplayAffinity(self.hwnd, self.WDA_EXCLUDEFROMCAPTURE)

    def no_activate_on_click(self) -> None:
        """Answer WM_MOUSEACTIVATE with MA_NOACTIVATE: a click lands on the panel, but the window Iris types into keeps
        the keyboard. WS_EX_NOACTIVATE alone should do that; this also covers Tk's own handling of the message.
        Done by subclassing the window: a small window procedure in front of Tk's that passes on everything else."""
        import ctypes

        functype = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)  # CFUNCTYPE off Windows, for the tests
        lresult, wparam = ctypes.c_ssize_t, ctypes.c_size_t
        wndproc = functype(lresult, ctypes.c_void_p, ctypes.c_uint, wparam, lresult)
        set_proc = getattr(self.user32, "SetWindowLongPtrW", None) or self.user32.SetWindowLongW  # 32-bit Python
        set_proc.argtypes, set_proc.restype = [ctypes.c_void_p, ctypes.c_int, lresult], lresult
        call = self.user32.CallWindowProcW
        call.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, wparam, lresult]
        call.restype = lresult
        previous: int | None = None

        def on_message(hwnd: int | None, msg: int, w: int, lp: int) -> int:
            if msg == self.WM_MOUSEACTIVATE:
                return self.MA_NOACTIVATE
            return call(previous, hwnd, msg, w, lp)

        self._wndproc = wndproc(on_message)
        previous = set_proc(self.hwnd, self.GWLP_WNDPROC, ctypes.cast(self._wndproc, ctypes.c_void_p).value)

    def show(self) -> None:
        """Show the layered window and let it draw again."""
        self.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
        self.user32.SetWindowPos(self.hwnd, self.HWND_TOPMOST, 0, 0, 0, 0, self.SWP_FLAGS)

    def hide(self) -> None:
        """Hide the layered window without destroying it."""
        self.user32.ShowWindow(self.hwnd, self.SW_HIDE)

    def work_area(self) -> tuple[int, int, int, int] | None:
        """(left, top, right, bottom) of the primary screen without the taskbar."""
        import ctypes

        rect = (ctypes.c_long * 4)()
        if not self.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return None
        return rect[0], rect[1], rect[2], rect[3]


def _user32() -> Any:
    try:
        import ctypes

        return ctypes.windll.user32  # type: ignore[attr-defined]
    except (ImportError, AttributeError):  # not Windows
        return None


BG, KEY, MUTED, TEXT = "#15151b", "#010203", "#8f94a3", "#f4f4f7"  # KEY: the see-through colour round the panel
COLOURS = {"listening": "#4ea1ff", "thinking": "#b28dff", "speaking": "#5ad19a", "done": "#5c6070"}
FONT = "Segoe UI"
WHITE = "#ffffff"
COLOUR_FADE = 0.35  # seconds the outline takes to go from one state's colour to the next
# The glow, faked with bands of colour mixed into the panel's, outermost first, each (width or depth in px, strength).
# OUTER: strokes along the edge drawn under the panel, so only their outer half shows, in the see-through margin.
# INNER: bands from the edge inwards, over the panel, fading into it; the part that reads as a glow on any wallpaper.
OUTER = ((12, 0.26), (7, 0.5))
INNER = ((18, 0.05), (13, 0.10), (9, 0.17), (6, 0.26), (3, 0.38))
EDGE_WIDTH, EDGE_PIECES = 2.5, 48  # the crisp outline, cut into pieces so a light can travel along it
BUTTON_RADIUS, BUTTON_CROSS = 11, 4  # the cancel button: a circle with an X, its arms this far from the centre


def blend(colour: str, other: str, t: float) -> str:
    """`colour` moved a fraction t of the way to `other`, both "#rrggbb". Tk has no alpha per stroke, so a soft glow
    is a stroke whose colour is mixed with the panel's."""
    t = min(1.0, max(0.0, t))
    a = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    b = (int(other[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b, strict=True))


def ease(t: float) -> float:
    """0 to 1 with a soft start and end."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


@dataclass
class Tint:
    """The outline's colour, which fades from one state's colour to the next over COLOUR_FADE instead of jumping.
    It starts neutral, so a panel that appears grows into its colour."""

    start: str = COLOURS["done"]
    target: str = COLOURS["done"]
    since: float = 0.0

    def toward(self, colour: str, now: float) -> None:
        """Aim the fade at a new colour, restarting it from wherever the colour is now."""
        if colour != self.target:  # from wherever it is now: a change in the middle of a fade does not jump either
            self.start, self.target, self.since = self.at(now), colour, now

    def at(self, now: float) -> str:
        """The colour at a moment: the old one, eased toward the new by how far along the fade is."""
        return blend(self.start, self.target, ease((now - self.since) / COLOUR_FADE))


def pulse(mode: str, t: float) -> float:
    """How bright the outline is at time t, 0 to 1: breathing while listening, a slow shimmer while thinking, with the
    sound bars while speaking, and calm once Iris is done."""
    if mode == "listening":
        return 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * math.tau / 1.6))
    if mode == "thinking":
        return 0.7 + 0.15 * math.sin(t * 2.3)
    if mode == "speaking":  # the bars' height, 3 to 7: the outline beats with them
        bars = dots("speaking", t)
        return 0.45 + 0.55 * sum(r - 3 for _, _, r in bars) / (4 * len(bars))
    return 0.35


def glow(base: str, mode: str, t: float) -> tuple[list[tuple[str, float]], list[str]]:
    """The glow at time t: (colour, width in px) of each OUTER stroke, a little wider as the outline pulses but never
    wider than OUTER says, and the colour of each INNER band. All `base` mixed into the panel colour, brighter as
    the outline pulses."""
    p = 0.4 + 0.6 * pulse(mode, t)
    outer = [(blend(BG, base, strength * p), width * (0.75 + 0.25 * p)) for width, strength in OUTER]
    return outer, [blend(BG, base, strength * p) for _, strength in INNER]


def edge(base: str, mode: str, t: float, pieces: int = EDGE_PIECES) -> list[str]:
    """The colour of each piece of the crisp outline at time t. One colour all round, except while thinking: then two
    soft lights travel round the panel, slowly, opposite each other."""
    p = pulse(mode, t)
    if mode != "thinking":
        return [blend(BG, base, 0.5 + 0.5 * p)] * pieces
    out = []
    for i in range(pieces):
        u = (i + 0.5) / pieces - t * 0.12  # one lap in about 8 s
        d = min(abs(u - round(u)), abs(u + 0.5 - round(u + 0.5)))  # distance to the nearest of the two lights
        light = max(0.0, 1 - d / 0.16) ** 2
        out.append(blend(blend(BG, base, 0.4 + 0.6 * max(light, p - 0.3)), WHITE, 0.35 * light))
    return out


def rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 8) -> list[tuple[float, float]]:
    """A rounded rectangle as a polygon, clockwise from the top edge: each corner a real arc of `steps` chords, so
    the panel, its outline and its glow all follow exactly the same edge."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = []
    for cx, cy, start in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(steps + 1):
            a = math.radians(start + 90 * k / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def split_ring(points: list[tuple[float, float]], pieces: int, chords: int = 6) -> list[list[tuple[float, float]]]:
    """A closed polygon cut into `pieces` of equal length, each a line of `chords` short chords, in order round it:
    piece i ends where piece i + 1 starts and the last ends where the first starts."""
    loop = points + points[:1]
    lengths = [math.dist(a, b) for a, b in zip(loop, loop[1:], strict=False)]
    total, n = sum(lengths), pieces * chords
    samples, i, walked = [], 0, 0.0
    for j in range(n):
        want = total * j / n
        while i < len(lengths) - 1 and walked + lengths[i] < want:
            walked += lengths[i]
            i += 1
        k = (want - walked) / lengths[i] if lengths[i] else 0.0
        (ax, ay), (bx, by) = loop[i], loop[i + 1]
        samples.append((ax + (bx - ax) * k, ay + (by - ay) * k))
    samples.append(samples[0])
    return [samples[p * chords:(p + 1) * chords + 1] for p in range(pieces)]


class Panel:
    """The Tk window. Thin on purpose: View decides what to show, Panel only draws it. `tk` is the tkinter module and
    `user32` ctypes' user32 (None off Windows), passed in so tests can hand it fakes and never open a window. `out` is
    where a click on the cancel button is written: stdout, the pipe back to Iris.

    The window is the panel plus a MARGIN of see-through KEY all round, where the glow goes. Text is written once per
    change; each frame only the indicator, the glow and the outline are touched, and those only when they change."""

    WIDTH, MARGIN, PAD, INDENT, RADIUS, POLL_MS = 560, 10, 18, 54, 20, 40  # WIDTH: the window, glow margin included

    def __init__(self, tk: Any, events: queue.Queue, clock=time.monotonic, user32: Any = None,
                 out: IO[str] | None = None):
        self.tk, self.events, self.clock, self.view, self.tint = tk, events, clock, View(), Tint()
        self.out, self.hover = out, False
        if user32 is not None:
            try:
                user32.SetProcessDPIAware()  # sharp text; Tk then scales its point sizes to the real DPI
            except Exception:  # noqa: BLE001 - blurry is better than no panel
                pass
        root = self.root = tk.Tk()
        root.withdraw()
        root.title(TITLE)
        root.overrideredirect(True)
        root.configure(bg=KEY)
        root.attributes("-topmost", True)
        try:
            root.attributes("-transparentcolor", KEY)  # Windows only: the panel gets round corners
        except tk.TclError:
            pass
        root.attributes("-alpha", 0.0)
        self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96)
        self.width, self.margin = int(self.WIDTH * self.scale), int(self.MARGIN * self.scale)
        self.canvas = tk.Canvas(root, width=self.width, height=60, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        # bound to the tag, not to items: the button is drawn anew with every layout and keeps these
        self.canvas.tag_bind("cancel", "<Button-1>", self.cancel)
        self.canvas.tag_bind("cancel", "<Enter>", lambda _e: self.set_hover(True))
        self.canvas.tag_bind("cancel", "<Leave>", lambda _e: self.set_hover(False))
        self.height, self.shown, self._alpha = 60, False, 0.0
        self.anchor = (0.0, 0.0)  # the indicator's centre: level with the first line of text
        self._outer: list[Any] = []
        self._inner: list[Any] = []
        self._edge: list[Any] = []
        self._drawn: dict[tuple[str, int], tuple] = {}  # what each outline item shows now, so a frame skips the rest
        self.win: Win32 | None = None
        if user32 is not None:
            # Tk makes the real top-level window only when it is first mapped, so map it once, see-through, give it
            # the styles that keep it out of the way, hide it, and hand the focus back to whoever had it
            before = user32.GetForegroundWindow()
            root.deiconify()
            root.update()
            self.win = Win32(user32, int(root.wm_frame(), 16))
            self.win.no_focus()
            try:
                self.win.no_activate_on_click()
            except Exception as exc:  # noqa: BLE001 - WS_EX_NOACTIVATE still keeps the focus where it is
                print(f"no WM_MOUSEACTIVATE hook: {exc!r}", file=sys.stderr)
            self.win.hide()
            if before:
                user32.SetForegroundWindow(before)

    def run(self) -> None:
        """The panel's loop: one frame per tick, until Iris closes the pipe or the window is closed."""
        self.root.after(self.POLL_MS, self.step)
        self.root.mainloop()

    def step(self) -> None:
        """One frame: take the new events, draw, fade or hide. Stops the process when Iris closes the pipe."""
        now, changed = self.clock(), False
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event is None:
                self.root.destroy()
                return
            self.view.apply(event, now)
            changed = True
        self.view.tick(now)
        if self.view.visible:
            if changed or not self.shown:
                self.layout()
            self.draw_outline(now)
            self.draw_dots(now)
            self.set_alpha(self.view.alpha(now))
            if not self.shown:
                self._show()
        elif self.shown:
            self._hide()
        self.root.after(self.POLL_MS, self.step)

    def layout(self) -> None:
        """Write the two lines, size the panel to them, lay its glow and outline round it and put it bottom-center
        above the taskbar."""
        c, s, m = self.canvas, self.scale, self.margin
        c.delete("all")
        self._drawn.clear()
        pad, x, gap = int(self.PAD * s), m + int(self.INDENT * s), int(8 * s)
        top, answer = self.view.lines()
        y, first = m + pad, None  # first: the box round the first line
        for text, colour, size in ((top, MUTED, 10), (answer, TEXT, 12)):
            if not text:
                continue
            box = c.bbox(self._text(x, y, text, colour, size))
            first = first or box
            y = box[3] + gap
        panel = max(int(52 * s), y - gap + pad - m)  # the panel's height; the window adds the margin twice
        self.height = panel + 2 * m
        left_x = m + (self.INDENT - 24) * s  # the indicator, centred in the column left of the text
        self.anchor = (left_x, (first[1] + first[3]) / 2 if first else m + panel / 2)
        ring = rounded_rect(m, m, self.width - m, self.height - m, self.RADIUS * s)
        flat = [v for pt in ring for v in pt]
        self._outer = [c.create_polygon(flat, fill="", outline=BG, width=1, joinstyle="round", tags="glow")
                       for _ in OUTER]
        c.create_polygon(flat, fill=BG, outline="", tags="panel")
        self._inner = []
        for depth, _ in INNER:  # a stroke as wide as the band, along the edge moved in by half of it
            d = depth * s / 2
            inset = rounded_rect(m + d, m + d, self.width - m - d, self.height - m - d, self.RADIUS * s - d)
            self._inner.append(c.create_polygon([v for pt in inset for v in pt], fill="", outline=BG,
                                                width=depth * s, joinstyle="round", tags="glow"))
        self._edge = [c.create_line([v for pt in piece for v in pt], fill=BG, width=EDGE_WIDTH * s, capstyle="round",
                                    joinstyle="round", tags="edge") for piece in split_ring(ring, EDGE_PIECES)]
        c.tag_raise("text")  # drawn first to measure it; the shapes stack in the order they were made, under it
        if self.view.mode in ACTIVE:
            self.draw_button()
        else:
            self.set_hover(False)  # the button is gone, and with it the hand cursor
        c.configure(height=self.height)
        area = self.win.work_area() if self.win else None
        if area is None:
            area = (0, 0, int(self.root.winfo_screenwidth()), int(self.root.winfo_screenheight()) - int(48 * s))
        left = area[0] + (area[2] - area[0] - self.width) // 2
        bottom = area[3] - int(16 * s) + m  # the panel 16 px above the taskbar; its glow sits in the margin
        self.root.geometry(f"{self.width}x{self.height}+{left}+{bottom - self.height}")

    def _text(self, x: int, y: int, text: str, colour: str, size: int) -> Any:
        # the text column stops as far from the right edge as it starts from the left: the cancel button goes there
        look = {"anchor": "nw", "width": self.width - x - int(self.INDENT * self.scale) - self.margin, "fill": colour,
                "font": (FONT, size), "tags": "text"}
        try:
            return self.canvas.create_text(x, y, text=text, **look)
        except self.tk.TclError:  # an old Tk that cannot take a character outside the BMP, such as some emoji
            text = "".join(ch if ord(ch) <= 0xFFFF else "?" for ch in text)
            return self.canvas.create_text(x, y, text=text, **look)

    def button_centre(self) -> tuple[float, float]:
        """The cancel button mirrors the indicator: as far from the right edge, level with the first line."""
        return self.width - self.anchor[0], self.anchor[1]

    def button_look(self) -> tuple[str, str, str]:
        """(fill, ring, cross) of the cancel button: quiet on the panel, in the state's colour under the mouse."""
        if self.hover:
            tint = self.tint.at(self.clock())
            return blend(BG, tint, 0.3), tint, TEXT
        return blend(BG, WHITE, 0.07), blend(BG, WHITE, 0.2), MUTED

    def draw_button(self) -> None:
        """Draw the round cancel button with its X, mirroring the indicator on the other side."""
        c, s = self.canvas, self.scale
        (cx, cy), r, a = self.button_centre(), BUTTON_RADIUS * s, BUTTON_CROSS * s
        fill, ring, cross = self.button_look()
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline=ring, width=1.5 * s,
                      tags=("cancel", "cancel_ring"))
        for dx in (a, -a):  # the X; its lines are part of the button, so a click on them counts too
            c.create_line(cx - dx, cy - a, cx + dx, cy + a, fill=cross, width=1.8 * s, capstyle="round",
                          tags=("cancel", "cancel_x"))

    def set_hover(self, on: bool) -> None:
        """Whether the mouse is over the cancel button: recolour it and swap in the hand cursor."""
        if on == self.hover:
            return
        self.hover = on
        fill, ring, cross = self.button_look()
        self.canvas.itemconfigure("cancel_ring", fill=fill, outline=ring)
        self.canvas.itemconfigure("cancel_x", fill=cross)
        self.canvas.configure(cursor="hand2" if on else "")

    def cancel(self, _event: Any = None) -> None:
        """The cancel button was clicked: tell Iris, and let the panel go at once instead of waiting for Iris's idle."""
        if self.view.mode not in ACTIVE:  # a click that came in just after the request ended
            return
        try:
            if self.out is not None:
                self.out.write(CANCEL)
                self.out.flush()
        except (OSError, ValueError):  # Iris is gone or closed the pipe; the panel still goes
            pass
        self.view.apply({"event": "idle"}, self.clock())
        self.layout()

    def draw_outline(self, now: float) -> None:
        """Colour the glow and the outline for this frame. Only items whose look changed are touched."""
        mode, s = self.view.mode, self.scale
        self.tint.toward(COLOURS.get(mode, COLOURS["done"]), now)
        base = self.tint.at(now)
        outer, inner = glow(base, mode, now)
        for i, (item, (colour, width)) in enumerate(zip(self._outer, outer, strict=True)):
            self._set(("outer", i), item, outline=colour, width=round(width * s * 2) / 2)
        for i, (item, colour) in enumerate(zip(self._inner, inner, strict=True)):
            self._set(("inner", i), item, outline=colour)
        colours = edge(base, mode, now, len(self._edge))
        if len(set(colours)) == 1:  # one colour all round: one call for the lot
            if self._drawn.get(("edge", -1)) != (colours[0],):
                self.canvas.itemconfigure("edge", fill=colours[0])
                self._drawn = {k: v for k, v in self._drawn.items() if k[0] != "edge"}
                self._drawn[("edge", -1)] = (colours[0],)
            return
        self._drawn.pop(("edge", -1), None)
        for i, (item, colour) in enumerate(zip(self._edge, colours, strict=True)):
            self._set(("edge", i), item, fill=colour)

    def _set(self, key: tuple[str, int], item: Any, **look: Any) -> None:
        values = tuple(look.values())
        if self._drawn.get(key) != values:
            self._drawn[key] = values
            self.canvas.itemconfigure(item, **look)

    def draw_dots(self, now: float) -> None:
        """Redraw the indicator dots for this frame: bars while speaking, circles otherwise."""
        c, s = self.canvas, self.scale
        c.delete("dots")
        cx, cy = self.anchor
        colour = self.tint.at(now)
        for dx, dy, r in dots(self.view.mode, now):
            x, y = cx + dx * s, cy + dy * s
            if self.view.mode == "speaking":
                c.create_rectangle(x - 1.5 * s, y - r * s, x + 1.5 * s, y + r * s, fill=colour, width=0, tags="dots")
            else:
                c.create_oval(x - r * s, y - r * s, x + r * s, y + r * s, fill=colour, width=0, tags="dots")

    def set_alpha(self, alpha: float) -> None:
        """Set the window's transparency, only when it changes: every call repaints the layered window."""
        alpha = round(alpha, 2)
        if alpha != self._alpha:  # only on a change: every call repaints the layered window
            self._alpha = alpha
            self.root.attributes("-alpha", alpha)

    def _show(self) -> None:
        self.shown = True
        if self.win:
            self.win.show()
        else:
            self.root.deiconify()

    def _hide(self) -> None:
        self.shown = False
        if self.win:
            self.win.hide()
        else:
            self.root.withdraw()


def main(stdin: IO[str] | None = None, tk: Any = None, user32: Any = None) -> int:
    """`python -m iris.overlay`: draw what comes in on stdin until Iris closes it. Tests pass a fake tk and user32."""
    if tk is None:
        try:
            import tkinter as tk
        except ImportError:
            print("no tkinter in this Python; the overlay needs it", file=sys.stderr)
            return 1
        user32 = _user32()
    events: queue.Queue = queue.Queue()
    threading.Thread(target=read_events, args=(stdin or sys.stdin, events), daemon=True).start()
    Panel(tk, events, user32=user32, out=sys.stdout).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
