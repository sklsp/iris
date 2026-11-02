"""A small panel for voice mode, bottom-center above the taskbar: what you said and what Iris answers.

Opt-in: `overlay = true` in config.toml or IRIS_OVERLAY=1. It runs as its own process, `python -m iris.overlay`, and
reads one JSON event per line on stdin, so a crash or a Python without Tk never touches the voice loop. Iris types into
and looks at other windows, so the panel never takes focus, stays out of the taskbar, lets clicks through and is left
out of screenshots. Stdlib only: tkinter and ctypes.

Events: listening, heard (text), thinking, answer (text), idle, interrupted."""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import IO, Any

from iris import config

TITLE = "Iris overlay"  # list_windows and read_window skip the window with this title
HIDE_AFTER = 4.0  # seconds the last answer stays up after Iris stops talking
FADE = 0.4  # the last part of HIDE_AFTER, in which the panel fades out
ALPHA = 0.93
PROMPT_CHARS, ANSWER_CHARS, ANSWER_LINES = 160, 300, 6  # longer text keeps its end, which is what is being said
EVENTS = {"listening", "heard", "thinking", "answer", "idle", "interrupted"}
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
        return self.mode != "hidden"

    def apply(self, event: dict, now: float) -> None:
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
    process. When that process is missing, dead or stuck, events go nowhere and the voice loop never notices."""

    def __init__(self, proc: subprocess.Popen | None = None):
        self._proc = proc
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=64)
        if proc is not None:
            threading.Thread(target=self._write, args=(proc,), daemon=True, name="overlay").start()

    def send(self, event: str, text: str = "") -> None:
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


def launch() -> Overlay:
    """The overlay process when it is switched on, else an Overlay that sends nothing. Never raises."""
    if not enabled():
        return Overlay()
    log_path = config.config_path().parent / "overlay.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:  # the child keeps its own handle
            proc = subprocess.Popen([sys.executable, "-m", "iris.overlay"], stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=log, text=True, encoding="ascii",
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
    """What Tk cannot do on Windows: no focus, no taskbar button, click-through, no screenshots of it."""

    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x20, 0x80, 0x40000
    WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
    WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004 and later; older ones keep showing it in screenshots
    SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
    HWND_TOPMOST, SWP_FLAGS = -1, 0x1 | 0x2 | 0x10  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE

    def __init__(self, user32: Any, hwnd: int):
        self.user32, self.hwnd = user32, hwnd

    def no_focus(self) -> None:
        style = self.user32.GetWindowLongW(self.hwnd, self.GWL_EXSTYLE)
        style = (style & ~self.WS_EX_APPWINDOW) | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE | \
            self.WS_EX_TRANSPARENT | self.WS_EX_LAYERED
        self.user32.SetWindowLongW(self.hwnd, self.GWL_EXSTYLE, style)
        self.user32.SetWindowDisplayAffinity(self.hwnd, self.WDA_EXCLUDEFROMCAPTURE)

    def show(self) -> None:
        self.user32.ShowWindow(self.hwnd, self.SW_SHOWNOACTIVATE)
        self.user32.SetWindowPos(self.hwnd, self.HWND_TOPMOST, 0, 0, 0, 0, self.SWP_FLAGS)

    def hide(self) -> None:
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


BG, KEY, MUTED, TEXT = "#1c1c22", "#010203", "#9aa0ab", "#f2f2f5"  # KEY: the see-through colour round the corners
COLOURS = {"listening": "#4ea1ff", "thinking": "#b28dff", "speaking": "#5ad19a", "done": "#5c6070"}
FONT = "Segoe UI"


class Panel:
    """The Tk window. Thin on purpose: View decides what to show, Panel only draws it. `tk` is the tkinter module and
    `user32` ctypes' user32 (None off Windows), passed in so tests can hand it fakes and never open a window."""

    WIDTH, PAD, INDENT, RADIUS, POLL_MS = 540, 16, 52, 18, 40

    def __init__(self, tk: Any, events: queue.Queue, clock=time.monotonic, user32: Any = None):
        self.tk, self.events, self.clock, self.view = tk, events, clock, View()
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
        self.width = int(self.WIDTH * self.scale)
        self.canvas = tk.Canvas(root, width=self.width, height=60, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.height, self.shown, self._alpha = 60, False, 0.0
        self.win: Win32 | None = None
        if user32 is not None:
            # Tk makes the real top-level window only when it is first mapped, so map it once, see-through, give it
            # the styles that keep it out of the way, hide it, and hand the focus back to whoever had it
            before = user32.GetForegroundWindow()
            root.deiconify()
            root.update()
            self.win = Win32(user32, int(root.wm_frame(), 16))
            self.win.no_focus()
            self.win.hide()
            if before:
                user32.SetForegroundWindow(before)

    def run(self) -> None:
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
            self.draw_dots(now)
            self.set_alpha(self.view.alpha(now))
            if not self.shown:
                self._show()
        elif self.shown:
            self._hide()
        self.root.after(self.POLL_MS, self.step)

    def layout(self) -> None:
        """Write the two lines, size the panel to them and put it bottom-center above the taskbar."""
        c, s = self.canvas, self.scale
        c.delete("all")
        pad, x = int(self.PAD * s), int(self.INDENT * s)
        top, answer = self.view.lines()
        y = pad
        for text, colour, size in ((top, MUTED, 10), (answer, TEXT, 12)):
            if not text:
                continue
            item = self._text(x, y, text, colour, size)
            y = c.bbox(item)[3] + int(6 * s)
        self.height = max(int(48 * s), y - int(6 * s) + pad)
        self._round_rect(0, 0, self.width, self.height, int(self.RADIUS * s))
        c.tag_lower("panel")
        c.configure(height=self.height)
        area = self.win.work_area() if self.win else None
        if area is None:
            area = (0, 0, int(self.root.winfo_screenwidth()), int(self.root.winfo_screenheight()) - int(48 * s))
        left = area[0] + (area[2] - area[0] - self.width) // 2
        self.root.geometry(f"{self.width}x{self.height}+{left}+{area[3] - self.height - int(16 * s)}")

    def _text(self, x: int, y: int, text: str, colour: str, size: int) -> Any:
        width = self.width - x - int(self.PAD * self.scale)
        try:
            return self.canvas.create_text(x, y, text=text, anchor="nw", width=width, fill=colour, font=(FONT, size))
        except self.tk.TclError:  # an old Tk that cannot take a character outside the BMP, such as some emoji
            text = "".join(ch if ord(ch) <= 0xFFFF else "?" for ch in text)
            return self.canvas.create_text(x, y, text=text, anchor="nw", width=width, fill=colour, font=(FONT, size))

    def _round_rect(self, x0: int, y0: int, x1: int, y1: int, r: int) -> None:
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.canvas.create_polygon(pts, smooth=True, fill=BG, outline="", tags="panel")

    def draw_dots(self, now: float) -> None:
        c, s = self.canvas, self.scale
        c.delete("dots")
        cx, cy = (self.PAD + 14) * s, (self.PAD + 10) * s
        colour = COLOURS.get(self.view.mode, MUTED)
        for dx, dy, r in dots(self.view.mode, now):
            x, y = cx + dx * s, cy + dy * s
            if self.view.mode == "speaking":
                c.create_rectangle(x - 1.5 * s, y - r * s, x + 1.5 * s, y + r * s, fill=colour, width=0, tags="dots")
            else:
                c.create_oval(x - r * s, y - r * s, x + r * s, y + r * s, fill=colour, width=0, tags="dots")

    def set_alpha(self, alpha: float) -> None:
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
    Panel(tk, events, user32=user32).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
