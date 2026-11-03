"""Tests for iris/doctor.py: the overlay check in checks() and main() marking a missing tkinter optional.

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
