"""Desktop actions for the user: clipboard, media keys, locking the screen, screenshots, closing windows and clock."""

import os
import subprocess
from ctypes import windll
from pathlib import Path

_MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}


def clipboard_read() -> str:
    """The text on the user's clipboard (at most 20000 characters), or 'The clipboard is empty.' when there is none."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                           capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "error: reading the clipboard timed out"
    except OSError:
        return "error: could not read the clipboard"
    text = r.stdout.rstrip("\r\n")
    if not text:
        return "The clipboard is empty."
    return text[:20000]


def clipboard_write(text: str) -> str:
    """Put text on the user's clipboard, piped to Set-Clipboard so it never appears in the command line."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                           input=text, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "error: writing to the clipboard timed out"
    except OSError:
        return "error: could not write the clipboard"
    if r.returncode != 0:
        return "error: could not write the clipboard"
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
    "$bmp.Save($args[0], [System.Drawing.Imaging.ImageFormat]::Png);"
    "$g.Dispose(); $bmp.Dispose()"
)


def screenshot(path: str = "") -> str:
    """Save a PNG of the whole screen; without a path it goes to ~/Pictures/Iris/screenshot-YYYYMMDD-HHMMSS.png."""
    from iris.tools import _wall_now  # local: tools imports this module at top level

    if not path:
        p = Path.home() / "Pictures" / "Iris" / f"screenshot-{_wall_now():%Y%m%d-%H%M%S}.png"
    else:
        p = Path(path).expanduser()
    os.makedirs(p.parent, exist_ok=True)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _SCREENSHOT_PS, str(p)],
                           capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return "error: could not save a screenshot"
    if r.returncode != 0:
        return "error: could not save a screenshot"
    return f"saved {p}"


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
