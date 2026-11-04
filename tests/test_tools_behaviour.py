"""Behavioural tests for iris/tools.py with fakes only (no desktop, no GPU, no network).

Covers the paths the existing suite bypasses by patching tools._window /
tools._scroll_at / tools._get_json directly: the real _desktop/_window plumbing,
read_window/_value/_control/click/type_text against an injected fake pywinauto,
system_data/system_status with a fake psutil and nvidia-smi, _get_json over a
fake urlopen, open_app, the ctypes wheel helper, the trash name-collision loop
and concept_sites timeouts.
"""

import ctypes
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from iris import tools


# --- system_data / system_status (fake psutil + nvidia-smi) -------------------
class _FakeVM:
    used = 8 * 2**30
    total = 16 * 2**30


def _install_fake_psutil(monkeypatch, cpu=43.0):
    calls = []

    mod = types.ModuleType("psutil")
    mod.virtual_memory = lambda: _FakeVM()
    mod.cpu_percent = lambda interval=None: calls.append(interval) or cpu
    monkeypatch.setitem(sys.modules, "psutil", mod)
    return calls


def _fake_nvidia(monkeypatch, stdout):
    seen = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw))

        class R:
            pass

        r = R()
        r.stdout = stdout
        return r

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    return seen


def test_system_data_reports_cpu_ram_and_gpus(monkeypatch):
    cpu_calls = _install_fake_psutil(monkeypatch)
    seen = _fake_nvidia(monkeypatch, "0, NVIDIA GeForce RTX 3090, 75, 10240, 24576, 68\n")

    d = tools.system_data()

    assert d["cpu_percent"] == 43.0 and cpu_calls == [0.3]
    assert d["ram_used_gb"] == pytest.approx(8.0) and d["ram_total_gb"] == pytest.approx(16.0)
    assert d["gpus"] == [{"index": 0, "name": "NVIDIA GeForce RTX 3090", "load_percent": 75.0,
                          "vram_used_mib": 10240.0, "vram_total_mib": 24576.0, "temp_c": 68.0}]
    cmd, kw = seen[0]
    assert cmd[:1] == ["nvidia-smi"] and kw["timeout"] == 10 and kw["capture_output"] is True


def test_system_data_reports_no_gpus_when_nvidia_smi_is_missing(monkeypatch):
    _install_fake_psutil(monkeypatch)

    def boom(cmd, **kw):
        raise OSError("nvidia-smi not found")

    monkeypatch.setattr(tools.subprocess, "run", boom)
    assert tools.system_data()["gpus"] == []


def test_system_status_lists_each_gpu(monkeypatch):
    _install_fake_psutil(monkeypatch)
    _fake_nvidia(monkeypatch, "0, RTX 3090, 75, 10240, 24576, 68\n")
    assert tools.system_status() == (
        "CPU 43%, RAM 8.0/16.0 GB\n"
        "GPU0 RTX 3090: 75% load, VRAM 10240/24576 MiB, 68 C")


def test_system_status_says_nvidia_unavailable(monkeypatch):
    _install_fake_psutil(monkeypatch)

    def boom(cmd, **kw):
        raise OSError("no nvidia-smi")

    monkeypatch.setattr(tools.subprocess, "run", boom)
    assert tools.system_status() == "CPU 43%, RAM 8.0/16.0 GB\nGPU: nvidia-smi not available"


# --- _get_json over a fake urlopen ---------------------------------------------
class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_get_json_fetches_comfy_stats_and_queue(monkeypatch):
    payloads = {
        "http://127.0.0.1:8188/system_stats": {"system": {"comfyui_version": "3.2"}},
        "http://127.0.0.1:8188/queue": {"queue_running": [1, 2], "queue_pending": [3]},
    }
    seen = []

    def fake_urlopen(url, timeout=None):
        seen.append((url, timeout))
        return _Resp(payloads[url])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.delenv("IRIS_COMFY_URL", raising=False)

    assert tools.comfy_status() == "ComfyUI 3.2 is running. Queue: 2 running, 1 waiting."
    assert [u for u, _ in seen] == ["http://127.0.0.1:8188/system_stats",
                                    "http://127.0.0.1:8188/queue"]
    assert all(t == 3 for _, t in seen)


# --- journal_today skips blank lines --------------------------------------------
def test_journal_today_skips_blank_lines(tmp_path, monkeypatch):
    p = tmp_path / "journal.jsonl"
    lines = [json.dumps({"event": "request", "t": "2026-10-17T09:00:00"}), "",
             json.dumps({"event": "tool", "outcome": "ok", "t": "2026-10-17T09:00:05"})]
    p.write_text("\n".join(lines) + "\n\n", encoding="utf-8")  # blank line inside and at the end
    monkeypatch.setenv("IRIS_JOURNAL", str(p))

    assert tools.journal_today("2026-10-17") == (
        "Today: 1 requests, 1 tool calls (1 ok, 0 refused, 0 declined, 0 errors).")


# --- the UI layer (fake pywinauto injected into sys.modules) -------------------
class FakeControl:
    def __init__(self, text="", kind="Button", value=None, iface_raises=False,
                 text_raises=False, invoke_fails=False):
        self.text = text
        self.kind = kind
        self._value = value
        self.iface_raises = iface_raises
        self.text_raises = text_raises
        self.invoke_fails = invoke_fails
        self.invoke_calls = 0
        self.click_input_calls = 0
        self.keys = []

    def window_text(self):
        if self.text_raises:
            raise RuntimeError("control vanished")
        return self.text

    @property
    def element_info(self):
        class EI:
            pass

        ei = EI()
        ei.control_type = self.kind
        return ei

    @property
    def iface_value(self):
        if self.iface_raises:
            raise RuntimeError("no Value pattern")

        class IV:
            pass

        iv = IV()
        iv.CurrentValue = self._value
        return iv

    def invoke(self):
        self.invoke_calls += 1
        if self.invoke_fails:
            raise RuntimeError("Invoke not supported")

    def click_input(self):
        self.click_input_calls += 1

    def type_keys(self, keys, **kw):
        self.keys.append((keys, kw))


class FakeWindow:
    def __init__(self, title="Notepad", controls=()):
        self.title = title
        self.controls = list(controls)
        self.focus_calls = 0
        self.keys = []

    def window_text(self):
        return self.title

    def set_focus(self):
        self.focus_calls += 1

    def descendants(self):
        return list(self.controls)

    def rectangle(self):
        class R:
            left, top, right, bottom = 0, 0, 800, 600

        return R()

    def type_keys(self, keys, **kw):
        self.keys.append((keys, kw))


class FakeDesktop:
    def __init__(self, windows=()):
        self._windows = list(windows)

    def windows(self):
        return self._windows


def _install_desktop(monkeypatch, desktop):
    seen = {}

    mod = types.ModuleType("pywinauto")

    def Desktop(backend=None):
        seen["backend"] = backend
        return desktop

    mod.Desktop = Desktop
    monkeypatch.setitem(sys.modules, "pywinauto", mod)
    return seen


def test_list_windows_skips_blank_titles_and_uses_uia_backend(monkeypatch):
    seen = _install_desktop(monkeypatch, FakeDesktop(
        [FakeWindow("Notepad"), FakeWindow("   "), FakeWindow("Chrome - Docs")]))

    assert tools.list_windows() == "Notepad\nChrome - Docs"
    assert seen["backend"] == "uia"


def test_list_windows_reports_when_nothing_is_open(monkeypatch):
    _install_desktop(monkeypatch, FakeDesktop())
    assert tools.list_windows() == "(no windows)"


def test_window_lookup_matches_case_insensitive_substring_and_raises_when_absent(monkeypatch):
    win = FakeWindow("Notepad")
    _install_desktop(monkeypatch, FakeDesktop([win]))

    assert tools._window("notepad") is win
    with pytest.raises(LookupError):
        tools._window("paint")


def test_read_window_shows_values_empty_fields_and_named_controls(monkeypatch):
    edit = FakeControl(text="Title", kind="Edit", value="hello world")
    empty_edit = FakeControl(text="Some field", kind="Edit", value=None)
    button = FakeControl(text="OK", kind="Button")
    pane = FakeControl(text="", kind="Pane")  # unnamed non-edit control: skipped
    win = FakeWindow("Notepad", [edit, empty_edit, button, pane])
    _install_desktop(monkeypatch, FakeDesktop([win]))

    assert tools.read_window("notepad") == (
        "Window: Notepad\n"
        "Edit: Title = 'hello world'\n"
        "Edit: Some field (empty)\n"
        "Button: OK")


def test_read_window_truncates_at_max_controls(monkeypatch):
    controls = [FakeControl(text=f"B{i}", kind="Button") for i in range(tools.MAX_CONTROLS + 5)]
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Big", controls)]))

    lines = tools.read_window("big").splitlines()

    assert lines[0] == "Window: Big"
    assert lines[-1] == "... (truncated)"
    assert sum(1 for line in lines if line.startswith("Button:")) == tools.MAX_CONTROLS


def test_value_prefers_iface_then_window_text_then_empty():
    ok = FakeControl(text="t", value="abc")
    no_iface = FakeControl(text="fallback text", iface_raises=True)
    dead = FakeControl(text="", iface_raises=True, text_raises=True)

    assert tools._value(ok) == "abc"
    assert tools._value(no_iface) == "fallback text"
    assert tools._value(dead) == ""


def test_control_prefers_exact_match_and_raises_when_absent(monkeypatch):
    save = FakeControl(text="Save", kind="Button")
    save_as = FakeControl(text="Save As", kind="MenuItem")
    win = FakeWindow("Notepad", [save, save_as])
    _install_desktop(monkeypatch, FakeDesktop([win]))

    assert tools._control(win, "SAVE") is save  # exact match wins over substring
    with pytest.raises(LookupError):
        tools._control(win, "open")


def test_click_invokes_when_supported(monkeypatch):
    ok = FakeControl(text="OK", kind="Button")
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Notepad", [ok])]))

    assert tools.click("notepad", "ok") == "clicked Button 'OK'"
    assert ok.invoke_calls == 1 and ok.click_input_calls == 0


def test_click_falls_back_to_real_click_when_invoke_fails(monkeypatch):
    stubborn = FakeControl(text="Go", kind="Button", invoke_fails=True)
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Notepad", [stubborn])]))

    assert tools.click("notepad", "go") == "clicked Button 'Go'"
    assert stubborn.invoke_calls == 1 and stubborn.click_input_calls == 1


def test_a_partial_name_never_clicks_or_types_into_a_send_pay_or_password_control(monkeypatch):
    send = FakeControl(text="Send", kind="Button")
    field = FakeControl(text="Wachtwoord", kind="Edit")
    _install_desktop(monkeypatch, FakeDesktop([FakeWindow("Mail", [send, field])]))
    with pytest.raises(LookupError, match="'Sen' matches the control 'Send'; use its full name"):
        tools.click("mail", "Sen")  # judged as a confirm click on "Sen", but it would press Send
    with pytest.raises(LookupError, match="use its full name"):
        tools.type_text("mail", "x", into="Wacht")
    assert send.invoke_calls == 0 and send.click_input_calls == 0 and field.keys == []
    assert tools.click("mail", "Send") == "clicked Button 'Send'"  # named in full, the tier saw it (and said NEVER)


def test_type_text_escapes_and_targets_named_field(monkeypatch):
    field = FakeControl(text="Name", kind="Edit")
    win = FakeWindow("Notepad", [field])
    _install_desktop(monkeypatch, FakeDesktop([win]))

    out = tools.type_text("notepad", "hi {x}", into="name", replace=True)

    assert out == "typed 6 characters into 'name'"
    assert win.focus_calls == 1
    assert field.keys == [("^a{BACKSPACE}", {}),
                          ("hi {{}x{}}", {"with_spaces": True, "with_newlines": True, "pause": 0.01})]


def test_type_text_without_into_types_into_the_window(monkeypatch):
    win = FakeWindow("Notepad")
    _install_desktop(monkeypatch, FakeDesktop([win]))

    out = tools.type_text("notepad", "plain")

    assert out == "typed 5 characters into 'Notepad'"
    assert win.keys == [("plain", {"with_spaces": True, "with_newlines": True, "pause": 0.01})]


def test_escape_keys_wraps_every_special_character():
    # re.sub wraps each special char: { -> {{}, } -> {}} (pywinauto literal-brace form)
    assert tools._escape_keys("a{b}+c^d%e~f(g)h") == "a{{}b{}}{+}c{^}d{%}e{~}f{(}g{)}h"


# --- open_app -------------------------------------------------------------------
def test_open_app_uses_shell_execute(monkeypatch):
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda name: started.append(name))

    assert tools.open_app("notepad") == "started 'notepad'"
    assert started == ["notepad"]


# --- _scroll_at (ctypes stubbed so the real mouse is never touched) -------------
def test_scroll_at_moves_cursor_and_spins_the_wheel(monkeypatch):
    class User32:
        def __init__(self):
            self.pos = []
            self.wheel = []

        def SetCursorPos(self, x, y):
            self.pos.append((x, y))

        def mouse_event(self, flags, dx, dy, data):
            self.wheel.append((flags, dx, dy, data))

    class Windll:
        user32 = User32()

    monkeypatch.setattr(ctypes, "windll", Windll())
    tools._scroll_at(10, 20, -240)

    assert ctypes.windll.user32.pos == [(10, 20)]
    assert ctypes.windll.user32.wheel == [(0x0800, 0, -240, 0)]


# --- _under ---------------------------------------------------------------------
def test_under_handles_unresolvable_roots():
    class BadRoot:
        def resolve(self):
            raise OSError("cannot resolve")

    assert tools._under(Path.home() / "a", Path.home()) is True
    assert tools._under(Path("C:/x"), BadRoot()) is False


# --- trash_file name collision ----------------------------------------------------
def test_trash_file_renames_on_collision(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(tools.time, "strftime", lambda fmt: "20261023-120000")

    f = tmp_path / "report.txt"
    f.write_text("data")
    first = Path(tmp_path) / "trash" / "20261023-120000_report.txt"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("older trashed file")

    tools.trash_file(str(f))

    assert not f.exists()
    second = Path(tmp_path) / "trash" / "20261023-120000_1_report.txt"
    assert second.read_text(encoding="utf-8") == "data"
    meta = json.loads((second.parent / (second.name + ".json")).read_text(encoding="utf-8"))
    assert meta["original"] == str(f.resolve())


# --- concept_sites timeout ---------------------------------------------------------
def test_concept_sites_reports_timeout(tmp_path, monkeypatch):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 120)

    monkeypatch.setattr(tools.subprocess, "run", boom)
    monkeypatch.setenv("IRIS_CONCEPTKIT_DIR", str(tmp_path))
    assert tools.concept_sites() == "conceptkit took too long"
