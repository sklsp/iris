"""Desktop actions for the user: reading and writing the clipboard, pressing media keys, locking the screen."""

import subprocess
from ctypes import windll

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
