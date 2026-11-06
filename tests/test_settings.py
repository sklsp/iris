"""iris.settings: the Settings tab's fields, validation and the config.toml writer (config.toml lives in tmp_path)."""

from __future__ import annotations

import json
import tomllib
import urllib.error
import urllib.request

import pytest

from iris import config, settings

START = """# my settings
name = "Sam"   # me
folders = [
  "C:/Windows",
]
builder = ["claude", "-p", "{prompt}"]

[extra]
x = 1
"""


def test_write_keeps_comments_tables_and_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(START, encoding="utf-8")
    settings.write({"city": "Rotterdam", "folders": [str(tmp_path)], "silence": 2.5, "overlay": True}, path)
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["city"] == "Rotterdam" and data["folders"] == [str(tmp_path)] and data["silence"] == 2.5
    assert data["overlay"] is True and data["builder"] == ["claude", "-p", "{prompt}"] and data["extra"] == {"x": 1}
    assert text.startswith("# my settings\nname = \"Sam\"   # me\n")
    assert text.index("overlay = true") < text.index("\n\n[extra]")  # new keys stay above the table
    settings.write({"city": None, "name": "Jay"}, path)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "city" not in data and data["name"] == "Jay"


def test_write_creates_the_file_and_quotes_strings(tmp_path):
    path = tmp_path / "new" / "config.toml"
    settings.write({"name": 'Ann "the boss" \\ Ünal', "plugins": ["lmstudio"]}, path)
    assert tomllib.loads(path.read_text(encoding="utf-8")) == {"name": 'Ann "the boss" \\ Ünal',
                                                                "plugins": ["lmstudio"]}


def test_write_refuses_a_file_it_could_not_read_back(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("name = \n", encoding="utf-8")  # already broken by hand
    with pytest.raises(tomllib.TOMLDecodeError):
        settings.write({"city": "x"}, path)
    assert path.read_text(encoding="utf-8") == "name = \n"


def test_write_refuses_a_value_that_does_not_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    with pytest.raises(ValueError, match="did not round-trip"):
        settings.write({"name": (1, 2)}, path)  # TOML has no tuples: [1, 2] comes back as a list
    assert not path.exists()  # the file is never written when the check fails


def test_validate_types_ranges_and_choices(tmp_path):
    clean, errors = settings.validate({
        "name": " Sam ", "silence": "2", "follow_up": 0, "whisper_gpu": 1, "overlay": True, "tts": "piper",
        "plugins": ["lmstudio"], "folders": f"{tmp_path}\n\n", "whisper_model": "", "llm_url": "http://x:1/v1"})
    assert errors == {}
    assert clean == {"name": "Sam", "silence": 2.0, "follow_up": 0.0, "whisper_gpu": 1, "overlay": True,
                     "tts": "piper", "plugins": ["lmstudio"], "folders": [str(tmp_path)], "whisper_model": None,
                     "llm_url": "http://x:1/v1"}
    _, errors = settings.validate({
        "silence": 99, "wake_threshold": "loud", "whisper_gpu": 1.5, "overlay": "yes", "tts": "espeak",
        "plugins": ["nope"], "folders": [str(tmp_path / "missing")], "llm_url": "localhost:1234",
        "name": "two\nlines", "builder": ["x"], "colour": "red", "browser_profile": str(tmp_path / "none")})
    assert set(errors) == {"silence", "wake_threshold", "whisper_gpu", "overlay", "tts", "plugins", "folders",
                           "llm_url", "name", "builder", "colour", "browser_profile"}
    assert errors["wake_threshold"] == "must be a number"
    assert errors["builder"] == "not a setting the app can change"


def test_validate_rejects_a_list_value_with_non_text_items(tmp_path):
    clean, errors = settings.validate({"folders": [str(tmp_path), 2]})
    assert errors == {"folders": "must be a list of text"}
    assert clean == {}


def test_a_hand_edited_choice_or_own_wake_model_may_stay(tmp_path):
    model = tmp_path / "hey_sam.onnx"
    model.write_bytes(b"onnx")
    assert settings.validate({"wakeword": str(model)}) == ({"wakeword": str(model)}, {})
    config.config_path().write_text('tts = "custom"\n', encoding="utf-8")
    assert settings.validate({"tts": "custom"}) == ({"tts": "custom"}, {})


def test_payload_marks_environment_overrides_and_read_only_keys(monkeypatch):
    config.config_path().write_text('silence = 3.0\nbuilder = ["claude"]\n', encoding="utf-8")
    monkeypatch.setenv("IRIS_SILENCE", "2")
    monkeypatch.setattr(settings, "_gpus", lambda: [{"index": 0, "name": "RTX"}])
    p = settings.payload()
    rows = {f["key"]: f for f in p["fields"]}
    assert rows["silence"]["value"] == 3.0 and rows["silence"]["set"] is True
    assert rows["silence"]["overridden_by"] == "IRIS_SILENCE=2"
    assert rows["city"]["set"] is False and "overridden_by" not in rows["city"]
    assert "lmstudio" in rows["plugins"]["choices"]
    assert p["read_only"] == {"builder": ["claude"]} and p["gpus"] == [{"index": 0, "name": "RTX"}]


def test_gpus_is_empty_without_nvidia(monkeypatch):
    import iris.tools

    monkeypatch.setattr(iris.tools, "system_data", lambda: (_ for _ in ()).throw(OSError("no nvidia-smi")))
    assert settings._gpus() == []


def test_a_wrong_typed_value_in_the_file_does_not_break_or_wipe_the_tab():
    config.config_path().write_text('folders = "C:/x"\nplugins = "lmstudio"\nsilence = "fast"\n', encoding="utf-8")
    rows = {f["key"]: f for f in settings.payload()["fields"]}
    assert rows["folders"]["value"] == ["C:/x"] and rows["plugins"]["value"] == ["lmstudio"]  # a line is one item
    assert rows["silence"]["value"] == 1.8 and rows["silence"]["invalid"] == "fast"


def test_a_correct_file_gives_rows_without_invalid():
    config.config_path().write_text('folders = ["C:/x"]\nsilence = 2.5\n', encoding="utf-8")
    rows = {f["key"]: f for f in settings.payload()["fields"]}
    assert rows["folders"]["value"] == ["C:/x"] and rows["silence"]["value"] == 2.5
    assert all("invalid" not in row for row in rows.values())


def test_save_writes_only_changes_and_names_the_ones_that_need_a_restart(monkeypatch):
    from iris import dashboard

    config.config_path().write_text('name = "Sam"\n', encoding="utf-8")
    r = settings.save({"name": "Sam", "city": "Utrecht", "tts": "piper", "llm_url": "http://127.0.0.1:11434/v1"})
    assert r == {"ok": True, "changed": ["city", "llm_url", "tts"], "restart": ["llm_url", "tts"]}
    assert dashboard.V1_MODELS_URL == "http://127.0.0.1:11434/v1/models"  # the model list follows at once
    assert config.load()["name"] == "Sam" and config.get("city") == "Utrecht"
    assert settings.save({"silence": -1})["errors"] == {"silence": "must be between 0.3 and 10"}
    assert settings.save({"llm_url": None})["changed"] == ["llm_url"]
    assert dashboard.V1_MODELS_URL == "http://127.0.0.1:1234/v1/models"
    dashboard.set_lm_base(dashboard._lm_base_from_settings())


def test_save_reports_a_file_it_could_not_write(monkeypatch):
    config.config_path().write_text("name = \n", encoding="utf-8")
    r = settings.save({"city": "x"})
    assert r["ok"] is False and r["errors"]["_file"].startswith("config.toml could not be written")


def test_config_setting_prefers_the_environment(monkeypatch):
    config.config_path().write_text('silence = 2.5\nflag = true\nname = "  "\n', encoding="utf-8")
    assert config.setting("IRIS_SILENCE", "silence", "1.8") == "2.5"
    monkeypatch.setenv("IRIS_SILENCE", " 3 ")
    assert config.setting("IRIS_SILENCE", "silence", "1.8") == "3"
    assert config.setting("IRIS_X", "flag", "d") == "d"   # a bool is not a scalar setting
    assert config.setting("IRIS_X", "name", "d") == "d"   # blank text means the default
    assert config.setting("IRIS_X", "missing") == ""


# --- the routes -----------------------------------------------------------------------------------------------------

def _post(base, route, body, token):
    req = urllib.request.Request(base + route, json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Iris-Token": token,
                                          "Origin": base})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def server():
    import threading

    from iris import dashboard

    s = dashboard.make_server(port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


def test_settings_routes_read_and_write(server):
    s, base = server
    with urllib.request.urlopen(base + "/api/settings", timeout=10) as r:
        assert json.loads(r.read())["ok"] is True
    assert _post(base, "/api/settings", {"values": {"city": "Delft"}}, s.token)[1]["ok"]
    assert config.get("city") == "Delft"
    assert _post(base, "/api/settings", {"values": {"city": "x"}}, "wrong")[0] == 403  # the token guards writes
    with urllib.request.urlopen(base + "/api/app", timeout=10) as r:
        assert json.loads(r.read()) == {"ok": True, "app": False}


def test_voice_restart_route_only_works_inside_the_app(server, monkeypatch):
    from iris import dashboard

    s, base = server
    assert _post(base, "/api/app/voice/restart", {}, s.token) == (
        409, {"error": "only the desktop app runs voice mode; restart `iris --voice` yourself"})
    monkeypatch.setitem(dashboard.APP_HOOKS, "restart_voice", lambda: "Iris is listening.")
    assert _post(base, "/api/app/voice/restart", {}, s.token) == (200, {"ok": True, "message": "Iris is listening."})


def _tts_only_in_the_environment(tmp_path, monkeypatch):
    """config.toml with just city; IRIS_TTS set and every other FIELDS env name unset."""
    (tmp_path / "iris-config.toml").write_text('city = "Rotterdam"\n', encoding="utf-8")
    monkeypatch.setenv("IRIS_TTS", "piper")
    for f in settings.FIELDS:
        if f.get("env") and f["env"] != "IRIS_TTS":
            monkeypatch.delenv(f["env"], raising=False)


def test_effective_reports_every_setting_with_its_source(tmp_path, monkeypatch):
    _tts_only_in_the_environment(tmp_path, monkeypatch)
    rows = settings.effective()
    assert len(rows) == len(settings.FIELDS)
    assert ("city", '"Rotterdam"', "config.toml") in rows
    assert ("tts", "piper", "environment IRIS_TTS") in rows
    assert ("name", '"the user"', "default") in rows


def test_config_main_prints_the_file_and_every_row(tmp_path, monkeypatch, capsys):
    _tts_only_in_the_environment(tmp_path, monkeypatch)
    assert settings.config_main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"config file: {tmp_path / 'iris-config.toml'}"
    assert 'city = "Rotterdam"  (config.toml)' in out


def test_extras_lists_the_keys_that_are_not_on_the_tab(tmp_path):
    (tmp_path / "iris-config.toml").write_text(
        'city = "Rotterdam"\ncline_cli = "W:/Cline/cline-cli.exe"\n', encoding="utf-8")

    assert settings.extras() == [("cline_cli", '"W:/Cline/cline-cli.exe"')]


def test_config_main_prints_the_extra_keys(tmp_path, capsys):
    (tmp_path / "iris-config.toml").write_text(
        'city = "Rotterdam"\ncline_cli = "W:/Cline/cline-cli.exe"\n', encoding="utf-8")

    assert settings.config_main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "extra keys in config.toml (not on the Settings tab):" in out
    assert 'cline_cli = "W:/Cline/cline-cli.exe"' in out


def test_config_main_prints_no_header_when_there_are_no_extras(tmp_path, capsys):
    (tmp_path / "iris-config.toml").write_text('city = "Rotterdam"\n', encoding="utf-8")

    assert settings.config_main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert not any("extra keys in config.toml" in line for line in out)
