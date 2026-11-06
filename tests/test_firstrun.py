"""iris.firstrun (the Setup tab) and iris.update (the notify-only update check). No network, mic or speaker: every
probe, recording, voice and GitHub answer is a fake."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from iris import __version__, config, firstrun, update


@pytest.fixture(autouse=True)
def _model_choice_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_SETTINGS", str(tmp_path / "settings.json"))  # never the real model choice


# --- first run ------------------------------------------------------------------------------------------------------

def test_done_marker():
    assert not firstrun.done()
    firstrun.mark_done()
    assert firstrun.done()
    assert json.loads(firstrun.marker_path().read_text(encoding="utf-8"))["version"] == __version__


def test_servers_lists_the_configured_one_first_without_duplicates():
    config.config_path().write_text('llm_url = "http://127.0.0.1:11434/v1"\n', encoding="utf-8")
    answers = {"http://127.0.0.1:1234/v1": ["qwen"], "http://127.0.0.1:11434/v1": None}
    rows = firstrun.servers(lambda url: answers.get(url))
    assert [(r["name"], r["reachable"], r["current"]) for r in rows] == [
        ("Configured", False, True), ("LM Studio", True, False), ("llama.cpp", False, False)]
    assert rows[1]["models"] == ["qwen"]


def test_probe_reads_an_openai_style_model_list(monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps({"data": [{"id": "a"}, {"id": ""}, {"id": "b"}]}).encode()

    assert firstrun.probe("http://127.0.0.1:9/v1", timeout=0.2) is None  # nothing listens on port 9
    seen = []
    monkeypatch.setattr(firstrun.urllib.request, "urlopen", lambda url, timeout: seen.append(url) or Resp())
    assert firstrun.probe("http://x/v1/") == ["a", "b"] and seen == ["http://x/v1/models"]


def test_use_server_saves_the_url_and_the_model():
    from iris.llm import read_settings

    probe = {"http://127.0.0.1:11434/v1": ["llama3"], firstrun.DEFAULT_URL: ["qwen"]}.get
    assert firstrun.use_server({"url": "http://127.0.0.1:11434/v1", "model": "llama3"}, probe)["ok"]
    assert config.get("llm_url") == "http://127.0.0.1:11434/v1" and read_settings()["model"] == "llama3"
    assert firstrun.use_server({"url": firstrun.DEFAULT_URL, "model": "qwen"}, probe)["ok"]
    assert "llm_url" not in config.raw()  # the default needs no line in the file
    from iris import dashboard

    dashboard.set_lm_base(dashboard._lm_base_from_settings())


@pytest.mark.parametrize(("body", "models", "error"), [
    ({"url": "http://127.0.0.1:9/v1", "model": "x"}, None, "no OpenAI-compatible server answers"),
    ({"url": "", "model": "x"}, ["x"], "no OpenAI-compatible server answers at (empty)"),
    ({"url": "http://a/v1", "model": "x"}, ["y"], "pick one of the models"),
    ({"url": "http://a/v1", "model": "x"}, [], "the server lists no models"),
    ({"url": "ftp://a/v1", "model": "x"}, ["x"], "must start with http"),
])
def test_use_server_refuses(body, models, error):
    r = firstrun.use_server(body, lambda url: models)
    assert r["ok"] is False and error in r["error"]


def test_use_server_with_a_custom_url_uses_the_first_listed_model():
    """A custom URL has no model list in the drop-down, so an empty model means "the first one it lists"."""
    from iris.llm import read_settings

    r = firstrun.use_server({"url": "http://127.0.0.1:5000/v1", "model": ""}, lambda url: ["m1", "m2"])
    assert r == {"ok": True, "url": "http://127.0.0.1:5000/v1", "model": "m1"}
    assert config.get("llm_url") == "http://127.0.0.1:5000/v1" and read_settings()["model"] == "m1"


def test_use_server_still_saves_a_named_model_and_refuses_one_not_listed():
    r = firstrun.use_server({"url": "http://127.0.0.1:5000/v1", "model": "m2"}, lambda url: ["m1", "m2"])
    assert r["ok"] is True and r["model"] == "m2"
    r = firstrun.use_server({"url": "http://127.0.0.1:5000/v1", "model": "zzz"}, lambda url: ["m1", "m2"])
    assert r["ok"] is False and "pick one of the models" in r["error"]


def test_the_setup_tab_sends_no_model_for_a_custom_url():
    html = (Path(firstrun.__file__).parent / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert 'custom.value.trim() ? "" : models.value' in html


def test_mic_test_measures_the_level():
    from iris.voice import VAD_THRESHOLD

    loud = firstrun.mic_test(lambda s: np.full(16000, VAD_THRESHOLD * 5, dtype="float32"))
    assert loud == {"ok": True, "level": 50, "message": "Iris heard you clearly."}
    soft = firstrun.mic_test(lambda s: np.full(16000, VAD_THRESHOLD, dtype="float32"))
    assert soft["ok"] and "softly" in soft["message"]
    silent = firstrun.mic_test(lambda s: np.zeros(16000, dtype="float32"))
    assert silent["ok"] is False and silent["level"] == 0 and "Sound settings" in silent["message"]
    assert firstrun.mic_test(lambda s: np.zeros(0, dtype="float32"))["ok"] is False


def test_mic_test_reports_a_device_error_and_missing_voice_parts(monkeypatch):
    def broken(seconds):
        raise OSError("device busy")

    assert firstrun.mic_test(broken)["message"] == "No microphone could be opened: device busy"
    import sys

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    assert "not installed" in firstrun.mic_test()["message"]


def test_mic_test_uses_sounddevice_when_no_recorder_is_given(monkeypatch):
    """The default recorder records MIC_SECONDS of mono float32 at 16 kHz through sounddevice."""
    import sys

    asked = {}

    class _FakeSD:
        def rec(self, frames, samplerate, channels, dtype):
            asked.update(frames=frames, samplerate=samplerate, channels=channels, dtype=dtype)
            return np.zeros(frames, dtype="float32")

        def wait(self):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSD())
    r = firstrun.mic_test()
    assert asked == {"frames": int(firstrun.MIC_SECONDS * 16000), "samplerate": 16000,
                     "channels": 1, "dtype": "float32"}
    assert r == {"ok": False, "level": 0, "message": "Almost nothing came in. Check the microphone in Windows "
                                                     "Sound settings (the default input device and its level)."}


def test_speaker_test():
    said = []
    assert firstrun.speaker_test(said.append)["ok"] and said == [firstrun.SPEAKER_TEXT]

    def broken(text):
        raise RuntimeError("no audio device")

    assert firstrun.speaker_test(broken) == {"ok": False, "message": "Could not play sound: no audio device"}


def test_speaker_test_uses_the_windows_voice_when_no_say_is_given(monkeypatch):
    """The default speaker speaks SPEAKER_TEXT through the sapi engine."""
    from iris import voice

    calls = []

    class _FakeSpeaker:
        def __init__(self, engine):
            self.engine = engine

        def speak(self, text):
            calls.append((self.engine, text))

    monkeypatch.setattr(voice, "Speaker", _FakeSpeaker)
    r = firstrun.speaker_test()
    assert calls == [("sapi", firstrun.SPEAKER_TEXT)]
    assert r == {"ok": True, "message": "Played. If you heard nothing, check the default output device in Windows."}


def test_payload_and_checks(monkeypatch):
    from iris import dashboard, doctor

    monkeypatch.setattr(firstrun, "servers", lambda: [])
    monkeypatch.setattr(dashboard, "lmstudio_payload", lambda: {"reachable": False})
    monkeypatch.setattr(doctor, "checks", lambda payload: [(True, "voice extras installed"), (False, "browser: x"),
                                                           (False, "model server not reachable")])
    p = firstrun.payload()
    assert p["done"] is False and p["update_check"] is True and p["servers"] == []
    assert p["checks"] == [{"ok": True, "line": "voice extras installed", "optional": True},
                           {"ok": False, "line": "browser: x", "optional": True},
                           {"ok": False, "line": "model server not reachable", "optional": False}]


def test_finish_stores_the_update_choice_and_starts_listening_in_the_app(monkeypatch):
    from iris import dashboard

    r = firstrun.finish({"update_check": False})
    assert r["ok"] and "iris --voice" in r["message"] and firstrun.done()
    assert config.raw()["update_check"] is False and update.enabled() is False
    monkeypatch.setitem(dashboard.APP_HOOKS, "restart_voice", lambda: "Iris is listening.")
    assert firstrun.finish({"update_check": True}) == {"ok": True, "message": "Iris is listening."}


def test_finish_reports_a_broken_config():
    config.config_path().write_text("name = \n", encoding="utf-8")
    r = firstrun.finish({})
    assert r["ok"] is False and "could not be written" in r["error"] and not firstrun.done()


# --- update check ---------------------------------------------------------------------------------------------------

def test_version_tuple():
    assert update.version_tuple("v0.10.1") == (0, 10, 1)
    assert update.version_tuple("0.6.0-rc1") == (0, 6, 0)
    assert update.version_tuple("nightly") == ()


def test_check_asks_at_most_once_a_day():
    calls = []

    def fetch():
        calls.append(1)
        return {"tag_name": "v99.0.0", "html_url": "https://github.com/sklsp/iris/releases/tag/v99.0.0"}

    st = update.check(now=1000.0, fetch=fetch)
    assert st["newer"] and st["latest"] == "v99.0.0" and st["url"].endswith("v99.0.0") and st["checked_at"] == 1000.0
    update.check(now=1000.0 + 3600, fetch=fetch)
    assert len(calls) == 1
    update.check(now=1000.0 + update.CHECK_EVERY_SECONDS + 1, fetch=fetch)
    assert len(calls) == 2


def test_check_respects_the_setting_and_survives_being_offline():
    def offline():
        raise OSError("no network")

    assert update.check(now=5.0, fetch=offline)["latest"] is None
    assert not update.state_path().exists()
    config.config_path().write_text("update_check = false\n", encoding="utf-8")
    assert update.check(fetch=lambda: pytest.fail("must not go online"))["enabled"] is False


def test_same_or_older_release_is_not_newer():
    assert update.check(now=1.0, fetch=lambda: {"tag_name": f"v{__version__}"})["newer"] is False
    update.state_path().write_text("[]", encoding="utf-8")  # a broken file is no state
    assert update.status()["latest"] is None


def test_tell_once_says_it_once_per_version(monkeypatch):
    told = []
    monkeypatch.setattr(update, "_fetch", lambda: {"tag_name": "v99.0.0", "html_url": "u"})
    update.tell_once(told.append)
    update.tell_once(told.append)
    assert len(told) == 1 and "v99.0.0" in told[0]


def test_update_route_never_goes_online(monkeypatch):
    import threading
    import urllib.request

    from iris import dashboard

    monkeypatch.setattr(update, "_fetch", lambda: pytest.fail("the dashboard must not check"))
    s = dashboard.make_server(port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{s.server_address[1]}/api/update", timeout=10) as r:
            assert json.loads(r.read())["current"] == __version__
    finally:
        s.shutdown()
        s.server_close()


def test_setup_routes_dispatch_each_step(monkeypatch):
    import threading
    import urllib.request

    from iris import dashboard

    monkeypatch.setattr(firstrun, "payload", lambda: {"ok": True, "step": "get"})
    monkeypatch.setattr(firstrun, "use_server", lambda body: {"ok": True, "step": "server", "body": body})
    monkeypatch.setattr(firstrun, "mic_test", lambda: {"ok": True, "step": "mic"})
    monkeypatch.setattr(firstrun, "speaker_test", lambda: {"ok": True, "step": "speaker"})
    monkeypatch.setattr(firstrun, "finish", lambda body: {"ok": True, "step": "done"})
    s = dashboard.make_server(port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/setup", timeout=10) as r:
            assert json.loads(r.read())["step"] == "get"
        for step in ("server", "mic", "speaker", "done"):
            req = urllib.request.Request(base + "/api/setup/" + step, b'{"url": "u"}', method="POST", headers={
                "Content-Type": "application/json", "X-Iris-Token": s.token, "Origin": base})
            with urllib.request.urlopen(req, timeout=10) as r:
                assert json.loads(r.read())["step"] == step
    finally:
        s.shutdown()
        s.server_close()


@pytest.mark.parametrize("latest,current,expected", [
    ("v1.0.0", "0.6.1", True), ("1.0.0", "1.0.0rc1", True), ("1.0.0rc1", "0.6.1", True),
    ("1.0.0rc2", "1.0.0rc1", True), ("1.0.0", "1.0.0", False), ("1.0.0rc1", "1.0.0", False),
    ("0.7", "0.7.0", False), ("0.7.1", "0.7", True), ("1.0.0.post1", "1.0.0", True), ("1.0.0", "1.0.0.dev3", True),
    ("v1.0.0-beta.2", "1.0.0b1", True), ("nightly", "0.6.1", False), ("0.6.0", "0.6.1", False)])
def test_newer_orders_versions_the_way_releases_go(latest, current, expected):
    assert update.newer(latest, current) is expected


@pytest.mark.parametrize("answer", [[], "not json", 42])
def test_a_release_answer_that_is_not_an_object_changes_nothing(tmp_path, monkeypatch, answer):
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    assert update.check(now=1000.0, fetch=lambda: answer)["latest"] is None


@pytest.mark.parametrize("exc", [__import__("http.client").client.IncompleteRead(b"{", 50),
                                 __import__("urllib.error").error.HTTPError(update.LATEST_URL, 403, "rate limited",
                                                                             None, None)])
def test_a_cut_off_or_rate_limited_answer_is_like_offline(tmp_path, monkeypatch, exc):
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))

    def fail():
        raise exc

    assert update.check(now=1000.0, fetch=fail)["latest"] is None


def test_a_broken_update_json_is_asked_again(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_CONFIG", str(tmp_path / "config.toml"))
    update.state_path().parent.mkdir(parents=True, exist_ok=True)
    update.state_path().write_text('{"checked_at": "yesterday"}', encoding="utf-8")
    st = update.check(now=1000.0, fetch=lambda: {"tag_name": "v9.0.0", "html_url": "https://x"})
    assert st["latest"] == "v9.0.0" and st["newer"] is True
