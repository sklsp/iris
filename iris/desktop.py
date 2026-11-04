"""Desktop actions for the user: clipboard, media keys, locking the screen, screenshots, closing windows and clock,
plus opening email drafts and looking at the screen."""

import os
import re
import subprocess
import tempfile
import urllib.error
from collections.abc import Callable
from ctypes import windll
from pathlib import Path
from urllib.parse import quote

from iris import files, llm

_MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}


# PowerShell reads stdin and writes stdout in the console code page, which breaks é and ë; UTF-8 both ways.
_UTF8 = "[Console]::InputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "


def clipboard_read() -> str:
    """The text on the user's clipboard (at most 20000 characters), or 'The clipboard is empty.' when there is none."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _UTF8 + "Get-Clipboard -Raw"],
                           capture_output=True, text=True, encoding="utf-8", timeout=10)
    except subprocess.TimeoutExpired:
        return "error: reading the clipboard timed out; another program may be holding it, try again"
    except OSError:
        return "error: could not read the clipboard; another program may be holding it, try again"
    text = r.stdout.rstrip("\r\n")
    if not text:
        return "The clipboard is empty."
    return text[:20000]


def clipboard_write(text: str) -> str:
    """Put text on the user's clipboard; it goes in through the environment, never into the command line."""
    if len(text) > 32000:  # an environment variable holds at most 32767 characters
        return "error: at most 32000 characters fit on the clipboard this way"
    try:
        # through the environment, which Windows passes as Unicode: stdin goes through the console code page
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $env:IRIS_CLIP"],
                           env={**os.environ, "IRIS_CLIP": text}, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "error: writing to the clipboard timed out; another program may be holding it, try again"
    except OSError:
        return "error: could not write the clipboard; another program may be holding it, try again"
    if r.returncode != 0:
        return "error: could not write the clipboard; another program may be holding it, try again"
    return f"put {len(text)} characters on the clipboard"


def media_key(key: str, times: int = 1) -> str:
    """Press a media key (play_pause, next, previous, volume_up, volume_down or mute), repeated `times` times."""
    vk = _MEDIA_KEYS.get(key)
    if vk is None:
        return "error: unknown media key"
    if not 1 <= times <= 10:
        return "error: times must be between 1 and 10"
    for _ in range(times):
        windll.user32.keybd_event(vk, 0, 0, 0)   # down
        windll.user32.keybd_event(vk, 0, 2, 0)   # up (KEYEVENTF_KEYUP in dwFlags)
    return f"pressed {key} {times} times"


def lock_screen() -> str:
    """Lock the user's Windows session."""
    windll.user32.LockWorkStation()
    return "the screen is locked"


_SCREENSHOT_PS = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing;"
    "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height;"
    "$g = [System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $bmp.Size);"
    "$bmp.Save($env:IRIS_SHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png);"
    "$g.Dispose(); $bmp.Dispose()"
)


def screenshot(path: str = "") -> str:
    """Save a PNG of the whole screen; without a path it goes to ~/Pictures/Iris/screenshot-YYYYMMDD-HHMMSS.png."""
    from iris.tools import _wall_now  # local: tools imports this module at top level

    if not path:
        p = Path.home() / "Pictures" / "Iris" / f"screenshot-{_wall_now():%Y%m%d-%H%M%S}.png"
    else:
        p = files.resolve_target(path, ".png")
    os.makedirs(p.parent, exist_ok=True)
    # the path goes in through the environment, never into the script text, so it can not run as PowerShell
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _SCREENSHOT_PS],
                           env={**os.environ, "IRIS_SHOT_PATH": str(p)}, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return "error: could not save a screenshot"
    if r.returncode != 0:
        return "error: could not save a screenshot"
    return f"saved {p}"


# The same capture at physical pixels, of the rectangle in IRIS_SHOT_RECT ("left,top,width,height"), scaled down to at
# most 1600 pixels on the longest side so a look stays a few thousand tokens.
_LOOK_PS = (
    "Add-Type -AssemblyName System.Drawing;"
    "Add-Type -Name Dpi -Namespace Iris -MemberDefinition "
    "'[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware();';"
    "[Iris.Dpi]::SetProcessDPIAware() | Out-Null;"
    "$r = $env:IRIS_SHOT_RECT.Split(',');"
    "$b = New-Object System.Drawing.Rectangle ([int]$r[0]), ([int]$r[1]), ([int]$r[2]), ([int]$r[3]);"
    "$full = New-Object System.Drawing.Bitmap $b.Width, $b.Height;"
    "$g = [System.Drawing.Graphics]::FromImage($full);"
    "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $full.Size);"
    "$s = [Math]::Min(1.0, 1600 / [Math]::Max($b.Width, $b.Height));"
    "$out = New-Object System.Drawing.Bitmap ([int]($b.Width * $s)), ([int]($b.Height * $s));"
    "$g2 = [System.Drawing.Graphics]::FromImage($out); $g2.InterpolationMode = 'HighQualityBicubic';"
    "$g2.DrawImage($full, 0, 0, $out.Width, $out.Height);"
    "$out.Save($env:IRIS_SHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png);"
    "$g.Dispose(); $g2.Dispose(); $full.Dispose(); $out.Dispose()"
)


def _area(window: str) -> tuple[int, int, int, int]:
    """(left, top, width, height) in physical pixels: one window brought to the front, else every screen."""
    windll.user32.SetProcessDPIAware()  # physical pixels, the same ones PowerShell captures and the mouse moves in
    if not window:
        m = windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)  # the virtual screen: every monitor
    from iris.tools import _window  # local: tools imports this module at top level

    w = _window(window)
    w.set_focus()
    r = w.rectangle()
    if r.width() <= 0 or r.height() <= 0:
        raise LookupError(f"{w.window_text()!r} is minimized; there is nothing to see")
    return r.left, r.top, r.width(), r.height()


def _capture(area: tuple[int, int, int, int]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        shot = Path(tmp) / "look.png"
        env = {**os.environ, "IRIS_SHOT_PATH": str(shot), "IRIS_SHOT_RECT": ",".join(map(str, area))}
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-Command", _LOOK_PS], env=env,
                               capture_output=True, text=True, timeout=30)
            return shot.read_bytes() if p.returncode == 0 else b""
        except (subprocess.TimeoutExpired, OSError):
            return b""


def _ask(png: bytes, ask: Callable[[bytes, str], str], text: str) -> str:
    if not png:
        return "error: could not capture the screen"
    try:
        return ask(png, text)
    except urllib.error.HTTPError as exc:
        return f"error: the model could not look at the image ({exc.code}); it may not read images"
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return "error: could not reach the model to look at the screen; is the model server running?"


def look_at_screen(question: str, window: str = "") -> str:
    """Answer a question about what is on the screen, or in one window brought to the front, with the model's eyes:
    the fallback for apps whose controls read_window can not see."""
    try:
        area = _area(window)
    except LookupError as exc:
        return f"error: {exc}"
    return _ask(_capture(area), llm.see, question) or "error: the model saw nothing it could describe"


def click_on_screen(target: str, window: str = "") -> str:
    """Find `target` (in words) on the screen or in one window with the model's eyes, and left-click its center:
    the fallback for buttons click can not find by name."""
    try:
        area = _area(window)
    except LookupError as exc:
        return f"error: {exc}"
    found = _ask(_capture(area), llm.locate, target)
    m = re.fullmatch(r"(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)", found)
    if not m:
        return found if found.startswith("error:") else f"error: could not find {target!r} on the screen"
    left, top, width, height = area
    x = left + round(float(m[1]) / 1000 * width)
    y = top + round(float(m[2]) / 1000 * height)
    windll.user32.SetCursorPos(x, y)
    windll.user32.mouse_event(2, 0, 0, 0, 0)   # left down
    windll.user32.mouse_event(4, 0, 0, 0, 0)   # left up
    return f"clicked {target!r} at ({x}, {y}); look again if you need to check what happened"


def close_window(title: str) -> str:
    """Close the first window whose title contains `title`; WM_CLOSE, so the app can still ask to save."""
    from iris.tools import _window  # local: tools imports this module at top level

    w = _window(title)
    w.close()
    return f"closed {w.window_text()!r}"


def clock() -> str:
    """The local date and time, weekday and ISO week, e.g. 'Friday 31 October 2026, 14:05, week 44'."""
    from iris.tools import _wall_now  # local: tools imports this module at top level

    now = _wall_now()
    return f"{now:%A} {now.day} {now:%B} {now.year}, {now:%H:%M}, week {now.isocalendar()[1]}"


# --- opening apps ------------------------------------------------------------------

def _start_menu_folders() -> list[Path]:
    """Start menu folders from IRIS_START_MENU (';' separated); the two real Windows ones otherwise."""
    raw = os.environ.get("IRIS_START_MENU")
    if not raw:
        sub = Path("Microsoft/Windows/Start Menu/Programs")
        return [Path(os.environ.get("ProgramData", "C:/ProgramData")) / sub,
                Path(os.environ.get("AppData", str(Path.home() / "AppData" / "Roaming"))) / sub]
    return [Path(s.strip()) for s in raw.split(";") if s.strip()]


def start_menu_match(name: str, folders: list[Path]) -> Path | None:
    """The .lnk under `folders` whose stem equals `name` (case-insensitive), else starts with it, else contains it;
    the shortest stem wins. Names that are paths or URIs skip the search."""
    if not name or ":" in name or "/" in name or "\\" in name or name.lower().endswith(".exe"):
        return None
    needle = name.lower()
    best: tuple[int, int, str] | None = None
    hit: Path | None = None
    for folder in folders:
        if not folder.is_dir():
            continue
        for lnk in folder.rglob("*.lnk"):
            stem = lnk.stem.lower()
            if stem == needle:
                rank = 0
            elif stem.startswith(needle):
                rank = 1
            elif needle in stem:
                rank = 2
            else:
                continue
            key = (rank, len(stem), str(lnk))
            if best is None or key < best:
                best, hit = key, lnk
    return hit


def email_draft(to: str = "", subject: str = "", body: str = "") -> str:
    """Open a new message in the default mail app with a mailto: URL; it never sends, the user presses send."""
    if not to.strip():
        return "error: say who the email is for"
    text = body[:1500] + ("..." if len(body) > 1500 else "")
    url = f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(text)}"
    os.startfile(url)
    return f"opened a draft to {to}; check it and press send yourself"
