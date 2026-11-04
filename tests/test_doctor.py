"""Tests for iris/doctor.py: the overlay and Piper speech checks, and main() marking a missing tkinter as optional.

The autouse conftest fixture points IRIS_CONFIG at a tmp config, and payload={"reachable": False} keeps
checks() from asking any model server."""
import importlib.util

from iris import doctor


def _rows(monkeypatch, overlay_env):
    monkeypatch.setenv("IRIS_OVERLAY", overlay_env)
    return doctor.checks(payload={"reachable": False})


def test_overlay_on_without_tkinter_reports_the_missing_line(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def no_tkinter(name, *args, **kwargs):
        if name == "tkinter":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", no_tkinter)
    rows = _rows(monkeypatch, "1")
    assert (False, "overlay: on, but tkinter is missing: reinstall Python with the tcl/tk option ticked,"
                   " or set overlay = false") in rows


def test_overlay_on_with_tkinter_reports_found(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def has_tkinter(name, *args, **kwargs):
        if name == "tkinter":
            return object()
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", has_tkinter)
    rows = _rows(monkeypatch, "1")
    assert (True, "overlay: on, tkinter found") in rows


def test_overlay_off_adds_no_overlay_line(monkeypatch):
    rows = _rows(monkeypatch, "0")
    assert not any(line.startswith("overlay") for _, line in rows)


def test_main_marks_a_missing_tkinter_optional(capsys, monkeypatch):
    missing = ("overlay: on, but tkinter is missing: reinstall Python with the tcl/tk option ticked,"
               " or set overlay = false")
    monkeypatch.setattr(doctor, "checks", lambda: [(False, missing)])
    assert doctor.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and out[0].startswith("-- ") and out[0].removeprefix("--").strip() == missing


def test_shortcut_missing_reports_the_none_line(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_DESKTOP_DIR", str(tmp_path))
    rows = doctor.checks(payload={"reachable": False})
    assert (False, "shortcut: none on the desktop; iris shortcut puts one there (optional)") in rows


def test_shortcut_present_reports_found(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_DESKTOP_DIR", str(tmp_path))
    (tmp_path / "Iris.cmd").touch()
    rows = doctor.checks(payload={"reachable": False})
    assert (True, "shortcut: Iris.cmd on the desktop") in rows


def test_the_installed_app_has_no_shortcut_check(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_DESKTOP_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "bundled", lambda: True)
    rows = doctor.checks(payload={"reachable": False})
    assert not any(line.startswith("shortcut") for _, line in rows)

def _voice_extras(monkeypatch, missing=()):
    """Report the voice extras as installed (CI has none), so doctor reaches its speech check; `missing` stay absent."""
    import iris.voice as v

    real_find_spec = importlib.util.find_spec
    extras = {"faster_whisper", "sounddevice", "openwakeword"}

    def find_spec(name, *args, **kwargs):
        if name in missing:
            return None
        return object() if name in extras else real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(v, "_cuda_available", lambda: False)


def test_piper_with_all_its_voices_lists_them(tmp_path, monkeypatch):
    import iris.voice as v

    voices = {"nl": "nl_test-a", "en": "en_test-b"}
    pdir = tmp_path / "voices"
    pdir.mkdir()
    for name in voices.values():
        (pdir / f"{name}.onnx").touch()
    runtime = tmp_path / "runtime"
    (runtime / "piper").mkdir(parents=True)  # the app's Piper pack: no pip install needed
    monkeypatch.setattr(v, "PIPER_VOICES", voices)
    monkeypatch.setattr(v, "PIPER_DIR", pdir)
    monkeypatch.setattr(v, "piper_runtime", lambda: runtime)
    _voice_extras(monkeypatch)
    monkeypatch.setenv("IRIS_TTS", "piper")
    rows = doctor.checks(payload={"reachable": False})
    assert (True, "speech: Piper nl_test-a, en_test-b") in rows


def test_piper_not_installed_says_what_to_install(tmp_path, monkeypatch):
    import iris.voice as v

    _voice_extras(monkeypatch, missing={"piper"})
    monkeypatch.setattr(v, "PIPER_DIR", tmp_path / "voices")  # no .onnx files there; that is not this line
    monkeypatch.setattr(v, "piper_runtime", lambda: tmp_path / "nope")  # no app Piper pack either
    monkeypatch.setattr(doctor, "bundled", lambda: False)  # not the installed app
    monkeypatch.setenv("IRIS_TTS", "piper")
    rows = doctor.checks(payload={"reachable": False})
    assert (False, "speech: Piper not installed: pip install -e .[piper]") in rows
