import pytest

collect_ignore_glob = ["fixtures/*"]  # data in old formats, such as a skill with its own test_skill.py


@pytest.fixture(autouse=True)
def _journal_in_tmp(tmp_path, monkeypatch):
    """Tests never touch the real ~/.iris: the journal, config.toml and the skills folder all point into tmp_path.
    A test that needs its own sets the variable again."""
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "iris-journal.jsonl"))
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "iris-config.toml"))
    monkeypatch.setenv("IRIS_SKILLS", str(tmp_path / "iris-skills"))
    monkeypatch.setenv("IRIS_START_MENU", str(tmp_path / "start-menu"))
    import iris.voice  # tests never depend on this PC's GPU: Whisper is built for the CPU unless a test says otherwise

    monkeypatch.setattr(iris.voice, "_cuda_available", lambda: False)
    monkeypatch.setenv("IRIS_CHIME", "0")  # no beeps from the test run
    monkeypatch.delenv("IRIS_OVERLAY", raising=False)  # no overlay window from the test run, even where it is on
    # nor the real browser: with no DevToolsActivePort in an empty profile, browser tools say it is not allowed
    monkeypatch.setenv("IRIS_BROWSER_PROFILE", str(tmp_path / "no-browser"))
    import iris.browser

    monkeypatch.setattr(iris.browser, "_conn", None)
    import threading

    monkeypatch.setattr(threading, "excepthook", threading.excepthook)  # main() installs its own; put it back after
    monkeypatch.setattr(iris.browser, "_watching", threading.Event())  # a voice-mode test may have started watch()
    import iris.web  # nor DNS: every name resolves to a public documentation address unless a test says otherwise

    monkeypatch.setattr(iris.web, "_resolve", lambda host: ["93.184.215.14"])
    import iris.dashboard  # nor a probe of this PC's ports: tests that fake urlopen expect it to be asked

    monkeypatch.setattr(iris.dashboard, "_nothing_listens", lambda url, timeout=0.5: False)
    import iris.update

    def offline():
        raise OSError("tests never ask GitHub for releases")

    monkeypatch.setattr(iris.update, "_fetch", offline)
