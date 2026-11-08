"""Tests for press_keys, focus_window, scroll and open_url with fakes only (no real desktop)."""

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from iris import tools
from tests.test_timers import make_timers


class FakeWindow:
    def __init__(self, title="Notepad"):
        self.title = title
        self.focus_calls = 0
        self.keys = []

    def window_text(self):
        return self.title

    def set_focus(self):
        self.focus_calls += 1

    def type_keys(self, keys, **kw):
        self.keys.append(keys)

    def rectangle(self):
        class R:
            left, top, right, bottom = 0, 0, 800, 600

        return R()


@pytest.fixture
def fake_window(monkeypatch):
    win = FakeWindow()
    monkeypatch.setattr(tools, "_window", lambda title: win)
    return win


# --- press_keys ---------------------------------------------------------------
def test_press_keys_tier_is_confirm(fake_window):
    assert tools.press_keys("Notepad", "^s") == "pressed '^s' in 'Notepad'"
    assert fake_window.focus_calls == 1 and fake_window.keys == ["^s"]


@pytest.mark.parametrize("app", ["Outlook - Inbox", "Gmail - Chat", "WhatsApp", "Discord", "Microsoft Teams", "Slack"])
def test_enter_in_mail_chat_is_never(app):
    assert tools._press_keys_tier({"window": app, "keys": "{ENTER}"}) == tools.NEVER


@pytest.mark.parametrize("app", ["Notepad", "VS Code", "Chrome - Docs"])
def test_enter_elsewhere_stays_confirm(app):
    assert tools._press_keys_tier({"window": app, "keys": "{ENTER}"}) == tools.CONFIRM


def test_non_enter_in_mail_chat_is_only_confirm():
    # Ctrl+S in Outlook is fine; only the send combination (Enter) is refused.
    assert tools._press_keys_tier({"window": "Outlook", "keys": "^s"}) == tools.CONFIRM


# --- focus_window -------------------------------------------------------------
def test_focus_window(fake_window):
    assert tools.focus_window("Notepad") == "focused 'Notepad'"
    assert fake_window.focus_calls == 1


def test_missing_window_error_tells_the_model_to_stop_retrying(monkeypatch):
    class EmptyDesktop:
        def windows(self):
            return []

    monkeypatch.setattr(tools, "_desktop", lambda: EmptyDesktop())
    with pytest.raises(LookupError) as exc:
        tools.focus_window("Spotify")
    assert str(exc.value) == (
        "no window whose title contains 'Spotify'; it is not open now. Call list_windows once to check, "
        "then tell the user instead of retrying.")


def test_focus_window_description_says_not_open_is_not_retried():
    desc = tools.TOOLS["focus_window"].description
    phrase = ("When it answers that the window is not open, check list_windows at most once and tell"
              " the user; do not retry or open the app unless they asked to open it.")
    assert phrase in desc


# --- scroll -------------------------------------------------------------------
def test_scroll_down_sends_negative_delta(monkeypatch, fake_window):
    calls = []
    monkeypatch.setattr(tools, "_scroll_at", lambda x, y, d: calls.append((x, y, d)))
    assert tools.scroll("Notepad", "down", 2) == "scrolled down by 2 in 'Notepad'"
    assert calls == [(400, 300, -240)]


def test_scroll_up_sends_positive_delta(monkeypatch, fake_window):
    calls = []
    monkeypatch.setattr(tools, "_scroll_at", lambda x, y, d: calls.append((x, y, d)))
    tools.scroll("Notepad", "up")  # default amount 3
    assert calls == [(400, 300, 360)]


def test_scroll_rejects_bad_direction(fake_window):
    with pytest.raises(ValueError):
        tools.scroll("Notepad", "left")


# --- open_url -----------------------------------------------------------------
@pytest.mark.parametrize("url", ["file:///C:/secret.txt", "javascript:alert(1)", "data:text/html,<b>x</b>"])
def test_open_url_refuses_non_http(url, monkeypatch):
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda u: started.append(u))
    with pytest.raises(ValueError):
        tools.open_url(url)
    assert started == []


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost:8080"])
def test_open_url_allows_http_and_https(url, monkeypatch):
    started = []
    monkeypatch.setattr(tools.os, "startfile", lambda u: started.append(u))
    assert tools.open_url(url) == f"opened {url}"
    assert started == [url]


def test_open_url_description_says_answer_from_the_search_result():
    desc = tools.TOOLS["open_url"].description
    assert "After a search URL the result already holds the results:" in desc
    assert "do not read, scroll or search the page again" in desc


# --- files (trash + undo) -----------------------------------------------------
@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_TRASH_DIR", str(tmp_path / "trash"))
    return tmp_path


def test_trash_then_restore_roundtrip(home):
    f = home / "report.txt"
    f.write_text("hello")
    tools.trash_file(str(f))
    assert not f.exists()
    trashed = list((home / "trash").iterdir())
    assert len(trashed) == 2  # the file + its .json sidecar

    out = tools.restore_trash()
    assert f.exists() and f.read_text() == "hello"
    assert out.startswith("restored")
    assert list((home / "trash").iterdir()) == []


def test_a_trashed_json_file_can_be_restored(home):
    f = home / "data.json"
    f.write_text('{"a": 1}')
    tools.trash_file(str(f))
    assert tools.restore_trash("data").startswith("restored") and f.read_text() == '{"a": 1}'
    assert list((home / "trash").iterdir()) == []


def test_an_item_never_lands_in_the_trash_without_its_record(home, monkeypatch):
    f = home / "report.txt"
    f.write_text("hello")
    real = Path.write_text

    def full_disk(self, *a, **kw):
        if self.name.endswith(".json"):
            raise OSError(28, "No space left on device")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", full_disk)
    with pytest.raises(OSError):
        tools.trash_file(str(f))
    assert f.read_text() == "hello"  # still in place: no trash item that restore_trash could not put back


def test_a_failed_move_leaves_no_record_behind(home, monkeypatch):
    f = home / "report.txt"
    f.write_text("hello")

    def stuck(src, dst):
        raise PermissionError("the file is open in another program")

    monkeypatch.setattr(tools.shutil, "move", stuck)
    with pytest.raises(PermissionError):
        tools.trash_file(str(f))
    assert f.exists() and list((home / "trash").iterdir()) == []


def test_restore_by_name_filter(home):
    (home / "a.txt").write_text("a")
    (home / "b.txt").write_text("b")
    tools.trash_file(str(home / "a.txt"))
    tools.trash_file(str(home / "b.txt"))
    tools.restore_trash(name="b.txt")
    assert (home / "b.txt").exists() and not (home / "a.txt").exists()


def test_restore_unknown_name_raises(home):
    with pytest.raises(LookupError):
        tools.restore_trash(name="nope")


def test_trash_missing_file_raises(home):
    with pytest.raises(LookupError):
        tools.trash_file(str(home / "ghost.txt"))


def test_restore_refuses_to_overwrite_existing(home):
    f = home / "x.txt"
    f.write_text("old")
    tools.trash_file(str(f))
    f.write_text("new")  # Jay made a new file at the same path meanwhile
    with pytest.raises(FileExistsError):
        tools.restore_trash()


def test_tiers():
    t = tools.TOOLS
    assert t["press_keys"].tier_of({"window": "Notepad", "keys": "^s"}) == tools.CONFIRM
    assert t["focus_window"].tier == tools.SAFE
    assert t["scroll"].tier == tools.SAFE
    assert t["open_url"].tier == tools.SAFE
    assert t["trash_file"].tier == tools.CONFIRM
    assert t["restore_trash"].tier == tools.SAFE
    assert t["agents_status"].tier == tools.SAFE  # the agent manager only ever reads


def test_agents_status_tool_reads_the_real_config(tmp_path, monkeypatch):
    """The registered tool must reach iris.agents.agents_status, so the voice loop can call it."""
    import iris.agents

    monkeypatch.setattr(iris.agents, "agents_status", lambda *a, **k: "all quiet")
    assert tools.agents_status() == "all quiet"


# --- the README cannot drift from the code --------------------------------------
def test_readme_documents_every_tool_with_its_tier():
    """Every tool in the registry must appear in README.md on a row with the right tier."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    rows = {}
    for line in readme.splitlines():
        if line.startswith("| ") and "|" in line[2:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows[cells[0]] = cells[1]
    for tool in tools.TOOLS.values():
        assert tool.name in rows, f"{tool.name} is missing from the README table"
        assert rows[tool.name] == tool.tier, f"{tool.name} is {rows[tool.name]} in the README, {tool.tier} in code"


def test_readme_documents_the_wake_word_and_how_to_run():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "hey_jarvis" in readme and "IRIS_WAKEWORD" in readme  # the wake word and how to change it
    for cmd in ("python -m iris", "--voice", "--push-to-talk"):
        assert cmd in readme
    for var in ("IRIS_TTS", "IRIS_LLM_URL", "IRIS_WHISPER_DEVICE", "IRIS_TRASH_DIR", "IRIS_AGENTS_TOML",
                "IRIS_REPOS", "IRIS_TRASH_ROOTS"):
        assert var in readme



def test_trash_refuses_paths_outside_home_config_folders_and_system_dirs(tmp_path, monkeypatch):
    from pathlib import Path

    from iris.tools import CONFIRM, NEVER, TOOLS

    cfg = tmp_path / "config.toml"
    cfg.write_text('folders = ["W:/"]', encoding="utf-8")  # W:/ is a root only because the config says so
    monkeypatch.setenv("IRIS_CONFIG", str(cfg))
    t = TOOLS["trash_file"]
    for bad in ("C:/Windows/System32/drivers", "C:/Program Files/LM Studio", "C:/ProgramData",
                str(Path.home()), "W:/", "D:/anything.txt", str(Path.home() / ".iris" / "trash" / "x")):
        assert t.tier_of({"path": bad}) == NEVER, bad
    assert t.tier_of({"path": str(Path.home() / "Documents" / "notes.txt")}) == CONFIRM
    assert t.tier_of({"path": "W:/Iris/scratch.txt"}) == CONFIRM


# --- gpu_guard -----------------------------------------------------------------
def test_gpu_guard_flags_low_free_vram():
    data = {"gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 3090", "load_percent": 97.0,
                      "vram_used_mib": 23100.0, "vram_total_mib": 24576.0, "temp_c": 71.0}]}
    [g] = tools.gpu_guard(data)
    assert g["free_mib"] == 1476 and g["ok"] is False
    assert g["reason"] == "only 1476 MiB free"


def test_gpu_guard_passes_a_cool_gpu_with_room_to_spare():
    data = {"gpus": [{"index": 1, "name": "NVIDIA GeForce RTX 3060", "load_percent": 3.0,
                      "vram_used_mib": 400.0, "vram_total_mib": 12288.0, "temp_c": 41.0}]}
    [g] = tools.gpu_guard(data)
    assert g["free_mib"] == 11888 and g["ok"] is True and g["reason"] == ""


def test_gpu_guard_flags_a_hot_gpu():
    data = {"gpus": [{"index": 2, "name": "NVIDIA GeForce RTX 3090", "load_percent": 50.0,
                      "vram_used_mib": 1000.0, "vram_total_mib": 24576.0, "temp_c": 84.0}]}
    [g] = tools.gpu_guard(data)
    assert g["ok"] is False and g["reason"] == "84 C, too hot"


def test_gpu_guard_lists_both_reasons_in_order():
    data = {"gpus": [{"index": 3, "name": "NVIDIA GeForce RTX 3090", "load_percent": 50.0,
                      "vram_used_mib": 20000.0, "vram_total_mib": 24576.0, "temp_c": 85.0}]}
    [g] = tools.gpu_guard(data)
    assert g["ok"] is False and g["reason"] == "only 4576 MiB free; 85 C, too hot"


def test_gpu_guard_with_no_gpus():
    assert tools.gpu_guard({"gpus": []}) == []



# --- gpu_check -----------------------------------------------------------------
def _two_gpus():
    return {"cpu_percent": 12.0, "ram_used_gb": 8.5, "ram_total_gb": 32.0,
            "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 3090", "load_percent": 97.0,
                      "vram_used_mib": 23100.0, "vram_total_mib": 24576.0, "temp_c": 71.0},
                     {"index": 1, "name": "NVIDIA GeForce RTX 3060", "load_percent": 3.0,
                      "vram_used_mib": 400.0, "vram_total_mib": 12288.0, "temp_c": 41.0}]}


def test_gpu_check_reports_busy_and_ok_gpus(monkeypatch):
    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())
    assert tools.gpu_check() == ("GPU0 NVIDIA GeForce RTX 3090: busy, only 1476 MiB free\n"
                                 "GPU1 NVIDIA GeForce RTX 3060: ok, 11888 MiB free")


def test_gpu_check_with_a_lower_threshold(monkeypatch):
    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())
    out = tools.gpu_check(min_free_mib=1000)
    assert out.splitlines()[0] == "GPU0 NVIDIA GeForce RTX 3090: ok, 1476 MiB free"


def test_gpu_check_without_gpus(monkeypatch):
    monkeypatch.setattr(tools, "system_data", lambda: {"cpu_percent": 5.0, "ram_used_gb": 8.0,
                                                       "ram_total_gb": 32.0, "gpus": []})
    assert tools.gpu_check() == "GPU: nvidia-smi not available"



# --- comfy_status --------------------------------------------------------------
def test_comfy_status_reports_version_and_queue(monkeypatch):
    def fake(url):
        if url.endswith("/system_stats"):
            return {"system": {"comfyui_version": "0.37.0"}}
        return {"queue_running": [[1]], "queue_pending": [[2], [3]]}

    monkeypatch.setattr(tools, "_get_json", fake)
    assert tools.comfy_status() == "ComfyUI 0.37.0 is running. Queue: 1 running, 2 waiting."


def test_comfy_status_when_not_running(monkeypatch):
    import urllib.error

    def fake(url):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(tools, "_get_json", fake)
    assert tools.comfy_status() == "ComfyUI is not running."


def test_comfy_status_default_urls(monkeypatch):
    urls = []

    def fake(url):
        urls.append(url)
        return {}

    monkeypatch.setattr(tools, "_get_json", fake)
    monkeypatch.delenv("IRIS_COMFY_URL", raising=False)
    tools.comfy_status()
    assert urls == ["http://127.0.0.1:8188/system_stats", "http://127.0.0.1:8188/queue"]


def test_comfy_status_honors_iris_comfy_url(monkeypatch):
    urls = []

    def fake(url):
        urls.append(url)
        return {}

    monkeypatch.setattr(tools, "_get_json", fake)
    monkeypatch.setenv("IRIS_COMFY_URL", "http://10.0.0.5:8188/")
    tools.comfy_status()
    assert urls[1] == "http://10.0.0.5:8188/queue"


# --- comfy_workflows -------------------------------------------------------------
def _workflows_folder(tmp_path, names):
    folder = tmp_path / "wf"
    folder.mkdir()
    for n in names:
        (folder / n).write_text("{}", encoding="utf-8")
    return folder


def test_comfy_workflows_lists_names_without_json_sorted(tmp_path, monkeypatch):
    folder = _workflows_folder(tmp_path, ["zeta.json", "alpha.json", "notes.txt"])
    (folder / "nested").mkdir()  # a folder is not a workflow
    monkeypatch.setenv("IRIS_COMFY_WORKFLOWS", str(folder))
    assert tools.comfy_workflows() == "alpha\nzeta"


def test_comfy_workflows_empty_folder(tmp_path, monkeypatch):
    folder = _workflows_folder(tmp_path, [])
    monkeypatch.setenv("IRIS_COMFY_WORKFLOWS", str(folder))
    assert tools.comfy_workflows() == f"No saved workflows in {folder}."


def test_comfy_workflows_missing_folder(tmp_path, monkeypatch):
    folder = tmp_path / "nope"
    monkeypatch.setenv("IRIS_COMFY_WORKFLOWS", str(folder))
    assert tools.comfy_workflows() == f"No saved workflows in {folder}."


def test_comfy_workflows_defaults_to_home_iris(tmp_path, monkeypatch):
    folder = tmp_path / ".iris" / "workflows"
    folder.mkdir(parents=True)
    (folder / "anime.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("IRIS_COMFY_WORKFLOWS", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # where Path.home() looks on Windows
    assert tools.comfy_workflows() == "anime"


def test_comfy_workflows_tier_is_safe():
    assert tools.TOOLS["comfy_workflows"].tier == tools.SAFE


# --- comfy_queue ---------------------------------------------------------------
def _queue_folder(tmp_path, monkeypatch):
    folder = tmp_path / "wf"
    folder.mkdir()
    (folder / "anime.json").write_text('{"3": {"class_type": "KSampler"}}', encoding="utf-8")
    monkeypatch.setenv("IRIS_COMFY_WORKFLOWS", str(folder))
    return folder


def test_comfy_queue_posts_the_workflow_and_returns_prompt_id(tmp_path, monkeypatch):
    import urllib.request

    _queue_folder(tmp_path, monkeypatch)
    monkeypatch.delenv("IRIS_COMFY_URL", raising=False)
    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())  # GPU1 has 11888 MiB free and is cool
    seen = {}

    def fake(req, timeout=None):
        seen["req"] = req
        seen["timeout"] = timeout
        return io.BytesIO(json.dumps({"prompt_id": "abc-123"}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tools.comfy_queue("anime") == "queued anime as abc-123"
    req = seen["req"]
    assert req.full_url == "http://127.0.0.1:8188/prompt"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"prompt": {"3": {"class_type": "KSampler"}}}
    assert seen["timeout"] == 10


def test_comfy_queue_bad_name_never_touches_disk_or_network(tmp_path, monkeypatch):
    import urllib.request

    def fake(*a, **k):
        raise AssertionError("network must not be touched")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    # no workflows folder exists at all: a bad name must fail before any disk access
    for name in ("../evil", "a b", "a/b", "a.b", ""):
        assert tools.comfy_queue(name) == ("error: a workflow name has only letters, digits, - and _; "
                                           "comfy_workflows lists them")


def test_comfy_queue_missing_workflow(tmp_path, monkeypatch):
    _queue_folder(tmp_path, monkeypatch)
    assert tools.comfy_queue("nope") == "error: no saved workflow named nope; comfy_workflows lists them"


def test_comfy_queue_when_not_running(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    _queue_folder(tmp_path, monkeypatch)
    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())  # GPU1 has room and is cool

    def fake(req, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tools.comfy_queue("anime") == "error: ComfyUI is not running; start ComfyUI, then ask again"


def test_comfy_queue_without_gpu_room_does_not_post(tmp_path, monkeypatch):
    import urllib.request

    _queue_folder(tmp_path, monkeypatch)
    tight = {"cpu_percent": 12.0, "ram_used_gb": 8.5, "ram_total_gb": 32.0,
             "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 3090", "load_percent": 97.0,
                       "vram_used_mib": 21000.0, "vram_total_mib": 24576.0, "temp_c": 71.0},   # 3576 MiB free
                      {"index": 1, "name": "NVIDIA GeForce RTX 3060", "load_percent": 3.0,
                       "vram_used_mib": 400.0, "vram_total_mib": 12288.0, "temp_c": 84.0}]}     # hot
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(tools, "system_data", lambda: tight)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tools.comfy_queue("anime") == ("error: no GPU is free for it (only 3576 MiB free; 84 C, too hot); "
                                          "try again later")
    assert calls == []


def test_comfy_queue_without_any_gpu_does_not_post(tmp_path, monkeypatch):
    import urllib.request

    _queue_folder(tmp_path, monkeypatch)
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(tools, "system_data", lambda: {"cpu_percent": 5.0, "ram_used_gb": 8.0,
                                                       "ram_total_gb": 32.0, "gpus": []})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert tools.comfy_queue("anime") == ("error: no NVIDIA GPU found (nvidia-smi listed none); ComfyUI workflows "
                                          "need one")
    assert calls == []


def test_comfy_queue_tier_and_description():
    t = tools.TOOLS["comfy_queue"]
    assert t.tier == tools.CONFIRM
    assert t.description == ("Queue a saved ComfyUI workflow (API format) by name. "
                             "Ask first: it uses the GPU for a while.")


# --- lmstudio_models -----------------------------------------------------------
def test_lmstudio_models_loaded(monkeypatch):
    monkeypatch.setattr("iris.dashboard.lmstudio_payload", lambda: {
        "reachable": True, "states_known": True,
        "models": [{"id": "qwen3.8-27b", "state": "loaded", "type": "llm"},
                   {"id": "text-embedding-nomic", "state": "not-loaded", "type": "embeddings"}]})
    assert tools.lmstudio_models() == "Loaded: qwen3.8-27b. Available: 2 models."


def test_lmstudio_models_none_loaded(monkeypatch):
    monkeypatch.setattr("iris.dashboard.lmstudio_payload", lambda: {
        "reachable": True, "states_known": True,
        "models": [{"id": "qwen3.8-27b", "state": "not-loaded", "type": "llm"},
                   {"id": "text-embedding-nomic", "state": "not-loaded", "type": "embeddings"}]})
    assert tools.lmstudio_models() == "No model is loaded. Available: 2 models."


def test_lmstudio_models_states_unknown(monkeypatch):
    monkeypatch.setattr("iris.dashboard.lmstudio_payload", lambda: {
        "reachable": True, "states_known": False,
        "models": [{"id": "a", "state": None, "type": None}]})
    assert tools.lmstudio_models() == "LM Studio is running with 1 models; it does not say which one is loaded."


def test_lmstudio_models_not_reachable(monkeypatch):
    monkeypatch.setattr("iris.dashboard.lmstudio_payload", lambda: {"reachable": False})
    assert tools.lmstudio_models() == ("LM Studio is not reachable; start LM Studio and its local server, then ask "
                                       "again.")


# --- lmstudio_load -------------------------------------------------------------
def test_lmstudio_load_returns_loaded_text(monkeypatch):
    calls = []

    def fake(action, body):
        calls.append((action, body))
        return {"action": action}

    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())  # GPU1 has 11888 MiB free
    monkeypatch.setattr("iris.dashboard.model_action", fake)
    assert tools.lmstudio_load("qwen3.8-27b") == "loaded qwen3.8-27b"
    assert calls == [("load", {"model": "qwen3.8-27b"})]


def test_lmstudio_load_error_is_returned_not_raised(monkeypatch):
    from iris.dashboard import ModelActionError

    def fake(action, body):
        raise ModelActionError(400, "unknown model: nope")

    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())
    monkeypatch.setattr("iris.dashboard.model_action", fake)
    assert tools.lmstudio_load("nope") == "error: unknown model: nope"


def test_load_without_room_returns_the_literal_error(monkeypatch):
    tight = {"cpu_percent": 12.0, "ram_used_gb": 8.5, "ram_total_gb": 32.0,
             "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 3090", "load_percent": 97.0,
                       "vram_used_mib": 21000.0, "vram_total_mib": 24576.0, "temp_c": 71.0},   # 3576 MiB free
                      {"index": 1, "name": "NVIDIA GeForce RTX 3060", "load_percent": 3.0,
                       "vram_used_mib": 6288.0, "vram_total_mib": 12288.0, "temp_c": 41.0}]}    # 6000 MiB free
    calls = []

    def fake(action, body):
        calls.append((action, body))
        return {"action": action}

    monkeypatch.setattr(tools, "system_data", lambda: tight)
    monkeypatch.setattr("iris.dashboard.model_action", fake)
    assert tools.lmstudio_load("qwen3.8-27b") == "error: no GPU has 8000 MiB free; unload a model first"
    assert calls == []


def test_load_without_any_gpu_returns_the_literal_error(monkeypatch):
    calls = []

    def fake(action, body):
        calls.append((action, body))
        return {"action": action}

    monkeypatch.setattr(tools, "system_data", lambda: {"cpu_percent": 5.0, "ram_used_gb": 8.0,
                                                       "ram_total_gb": 32.0, "gpus": []})
    monkeypatch.setattr("iris.dashboard.model_action", fake)
    assert tools.lmstudio_load("qwen3.8-27b") == "error: no GPU has 8000 MiB free; unload a model first"
    assert calls == []


def test_lmstudio_load_tier_is_confirm():
    assert tools.TOOLS["lmstudio_load"].tier == tools.CONFIRM


# --- lmstudio_unload -------------------------------------------------------------
UNLOAD_TIER = [
    ({"model": "all"}, tools.NEVER),  # unloading everything takes the model Iris runs on too
    ({"model": "qwen3.8-27b"}, tools.NEVER),  # the model Iris talks to right now
    ({"model": "llama-3.1-8b"}, tools.CONFIRM),  # a different model: just asks first
]


@pytest.mark.parametrize("args, expected", UNLOAD_TIER, ids=[a["model"] for a, _ in UNLOAD_TIER])
def test_unload_escalates_to_never_for_all_or_the_running_model(monkeypatch, args, expected):
    monkeypatch.setattr("iris.llm.effective_model", lambda: "qwen3.8-27b")
    assert tools._unload_tier(args) == expected


def test_lmstudio_unload_returns_unloaded_text(monkeypatch):
    calls = []

    def fake(action, body):
        calls.append((action, body))
        return {"action": action}

    monkeypatch.setattr("iris.dashboard.model_action", fake)
    assert tools.lmstudio_unload("llama-3.1-8b") == "unloaded llama-3.1-8b"
    assert calls == [("unload", {"model": "llama-3.1-8b"})]


def test_lmstudio_unload_error_is_returned_not_raised(monkeypatch):
    from iris.dashboard import ModelActionError

    def fake(action, body):
        raise ModelActionError(400, "unknown model: nope")

    monkeypatch.setattr("iris.dashboard.model_action", fake)
    assert tools.lmstudio_unload("nope") == "error: unknown model: nope"


def test_lmstudio_unload_tier_is_confirm():
    assert tools.TOOLS["lmstudio_unload"].tier == tools.CONFIRM


def test_lmstudio_load_and_unload_descriptions_cover_the_dutch_phrasings():
    load = tools.TOOLS["lmstudio_load"].description
    unload = tools.TOOLS["lmstudio_unload"].description
    assert "laad ... uit" in load and "uitladen" in load
    assert "laad ... uit" in unload and "uitladen" in unload


# --- followups -----------------------------------------------------------------
PIPELINE_CSV = """osm,name,status,note,next,tier,since,demo,updated
node/1,Bakkerij Zon,interesse,eigenaar vond demo goed,2026-10-17,,,https://bakkerij-zon.example.com,2026-10-12
node/2,Bakkerij Aart,bezocht,,2026-10-15,,,,2026-10-11
node/3,Kapper Sem,klant,,2026-10-01,etalage,2026-10-01,,2026-10-01
node/4,Cafe Noord,interesse,later,2026-10-20,,,,2026-10-12
"""


@pytest.fixture
def leadscan(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "pipeline.csv").write_text(PIPELINE_CSV, encoding="utf-8")
    monkeypatch.setenv("IRIS_LEADSCAN_DIR", str(tmp_path))
    return tmp_path


def test_followups_lists_due_leads_sorted_by_date_and_name(leadscan):
    assert tools.followups("2026-10-17") == (
        "2026-10-15 Bakkerij Aart (bezocht)\n"
        "2026-10-17 Bakkerij Zon (interesse): eigenaar vond demo goed")


def test_followups_none_due(leadscan):
    assert tools.followups("2026-10-14") == "No follow-ups due."


def test_followups_without_crm(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_LEADSCAN_DIR", str(tmp_path))
    assert tools.followups() == (f"No leadscan CRM found at {tmp_path / 'out' / 'pipeline.csv'}; set leadscan_dir "
                                 "in config.toml to its folder.")



# --- journal_today -------------------------------------------------------------
def _write_journal(tmp_path, monkeypatch):
    import json

    lines = [
        {"event": "request", "text": "open notepad", "t": "2026-10-17T09:00:00"},
        {"event": "tool", "tool": "focus_window", "args": {}, "tier": "safe",
         "outcome": "ok", "result": "focused 'Notepad'", "t": "2026-10-17T09:00:05"},
        {"event": "tool", "tool": "press_keys", "args": {}, "tier": "never",
         "outcome": "refused", "t": "2026-10-17T09:01:00"},
        {"event": "request", "text": "check the gpu", "t": "2026-10-17T10:00:00"},
        {"event": "tool", "tool": "click", "args": {}, "tier": "confirm",
         "outcome": "declined", "t": "2026-10-17T10:00:30"},
        {"event": "tool", "tool": "open_url", "args": {}, "tier": "safe",
         "outcome": "error", "error": "boom", "t": "2026-10-17T10:01:00"},
    ]
    text = "\n".join(json.dumps(e) for e in lines) + "\nnot json\n"
    text += json.dumps({"event": "request", "text": "yesterday", "t": "2026-10-16T20:00:00"}) + "\n"
    p = tmp_path / "journal.jsonl"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(p))
    return p


def test_journal_today_counts_by_outcome(tmp_path, monkeypatch):
    _write_journal(tmp_path, monkeypatch)
    assert tools.journal_today("2026-10-17") == (
        "Today: 2 requests, 4 tool calls (1 ok, 1 refused, 1 declined, 1 errors).")


def test_journal_today_empty_file(tmp_path, monkeypatch):
    p = tmp_path / "journal.jsonl"
    p.write_text("", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(p))
    assert tools.journal_today() == (
        "Today: 0 requests, 0 tool calls (0 ok, 0 refused, 0 declined, 0 errors).")


def test_journal_today_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_JOURNAL", str(tmp_path / "nope.jsonl"))
    assert tools.journal_today() == "Nothing in the journal yet."


def test_journal_today_counts_a_returned_error(tmp_path, monkeypatch):
    from iris.agent import Agent, Journal
    from iris.llm import Reply, ToolCall

    p = tmp_path / "journal.jsonl"
    monkeypatch.setenv("IRIS_JOURNAL", str(p))
    boom = tools.Tool("boom", "", {}, [], tools.SAFE, lambda **kw: "error: x")
    replies = [
        Reply("", [ToolCall("id-boom", "boom", {})], {"role": "assistant", "content": "", "tool_calls": [
            {"id": "id-boom", "type": "function", "function": {"name": "boom", "arguments": "{}"}}]}),
        Reply("Klaar.", [], {"role": "assistant", "content": "Klaar."}),
    ]

    class LLM:
        def chat(self, messages, tools):
            return replies.pop(0)

    Agent(LLM(), lambda q: True, Journal(p), {"boom": boom}).handle("do it")
    day = json.loads(p.read_text(encoding="utf-8").splitlines()[0])["t"][:10]
    assert tools.journal_today(today=day) == (
        "Today: 1 requests, 1 tool calls (0 ok, 0 refused, 0 declined, 1 errors).")


def test_journal_today_counts_model_errors(tmp_path, monkeypatch):
    lines = [
        {"event": "request", "text": "open notepad", "t": "2026-10-17T09:00:00"},
        {"event": "llm_error", "error": "URLError('connection refused')", "t": "2026-10-17T09:05:00"},
        {"event": "request", "text": "check the gpu", "t": "2026-10-17T10:00:00"},
        {"event": "llm_error", "error": "TimeoutError()", "t": "2026-10-17T10:05:00"},
    ]
    p = tmp_path / "journal.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(p))
    assert tools.journal_today("2026-10-17") == (
        "Today: 2 requests, 0 tool calls (0 ok, 0 refused, 0 declined, 0 errors). 2 model errors.")


def test_journal_today_ignores_model_errors_from_other_days(tmp_path, monkeypatch):
    lines = [
        {"event": "request", "text": "open notepad", "t": "2026-10-17T09:00:00"},
        {"event": "llm_error", "error": "URLError('connection refused')", "t": "2026-10-16T09:05:00"},
    ]
    p = tmp_path / "journal.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("IRIS_JOURNAL", str(p))
    assert tools.journal_today("2026-10-17") == (
        "Today: 1 requests, 0 tool calls (0 ok, 0 refused, 0 declined, 0 errors).")



# --- concept_sites ---------------------------------------------------------------
def test_concept_sites_without_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_CONCEPTKIT_DIR", str(tmp_path))
    assert tools.concept_sites() == f"conceptkit not found at {tmp_path}"


def test_concept_sites_lists_and_checks(tmp_path, monkeypatch):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"")

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            pass

        r = R()
        r.stdout = ("lotus: Restaurant Lotus, poort 5191\n" if cmd[-1] == "list"
                    else "Geen problemen gevonden.\n")
        return r

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    monkeypatch.setenv("IRIS_CONCEPTKIT_DIR", str(tmp_path))
    assert tools.concept_sites() == (
        "Sites:\nlotus: Restaurant Lotus, poort 5191\n\nChecks:\nGeen problemen gevonden.")
    assert calls[0][-3:] == ["-m", "conceptkit", "list"]
    assert calls[1][-4:] == ["-m", "conceptkit", "check", "--all"]
    assert tools.TOOLS["concept_sites"].tier == tools.SAFE


# --- queue_remove ---------------------------------------------------------------
def test_queue_remove_removes_the_prompt(tmp_path, monkeypatch):
    from iris.prompt_queue import add as _add
    from iris.prompt_queue import load

    path = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    pid = _add("leadscan", "Run de tests en vertel me het resultaat.")
    assert tools.queue_remove(pid) == (
        f"Removed prompt {pid} for leadscan: Run de tests en vertel me het resultaat.")
    assert load(path)["prompts"] == []


def test_queue_remove_cuts_long_text_to_80_chars(tmp_path, monkeypatch):
    from iris.prompt_queue import add as _add

    path = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    pid = _add("Iris", "a" * 90)
    assert tools.queue_remove(pid) == f"Removed prompt {pid} for Iris: {'a' * 77}..."


def test_queue_remove_unknown_id_gives_error_text(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_QUEUE", str(tmp_path / "q.json"))
    assert tools.queue_remove(99) == "error: no prompt with id 99; queue_list shows the ids"


def test_queue_remove_is_registered_with_confirm():
    t = tools.TOOLS["queue_remove"]
    assert t.tier == tools.CONFIRM
    assert t.required == ["id"]
    assert t.params["id"]["type"] == "integer"


# --- queue_move ---------------------------------------------------------------
def test_queue_move_up_swaps_with_the_previous_prompt(tmp_path, monkeypatch):
    from iris.prompt_queue import add as _add
    from iris.prompt_queue import load

    path = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    first = _add("leadscan", "Eerste prompt.")
    second = _add("Iris", "Tweede prompt.")
    result = tools.queue_move(second, "up")
    assert [p["id"] for p in load(path)["prompts"]] == [second, first]
    assert result == (f"Moved prompt {second} up.\n2 prompts waiting:\n{second}. Iris: Tweede prompt.\n"
                      f"{first}. leadscan: Eerste prompt.")


def test_queue_move_down_ignores_case_and_spaces(tmp_path, monkeypatch):
    from iris.prompt_queue import add as _add
    from iris.prompt_queue import load

    path = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    first = _add("leadscan", "Eerste prompt.")
    second = _add("Iris", "Tweede prompt.")
    result = tools.queue_move(first, " DOWN ")
    assert [p["id"] for p in load(path)["prompts"]] == [second, first]
    assert result.startswith(f"Moved prompt {first} down.\n")


def test_queue_move_bad_direction_gives_error_text(tmp_path, monkeypatch):
    from iris.prompt_queue import add as _add

    path = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    _add("Iris", "Een prompt.")
    assert tools.queue_move(1, "sideways") == 'error: direction must be "up" or "down"'


def test_queue_move_unknown_id_gives_error_text(tmp_path, monkeypatch):
    from iris.prompt_queue import add as _add

    path = tmp_path / "q.json"
    monkeypatch.setenv("IRIS_QUEUE", str(path))
    _add("Iris", "Een prompt.")
    assert tools.queue_move(99, "up") == "error: no prompt with id 99; queue_list shows the ids"


def test_queue_move_is_registered_with_confirm():
    t = tools.TOOLS["queue_move"]
    assert t.tier == tools.CONFIRM
    assert t.required == ["id", "direction"]
    assert t.params["id"]["type"] == "integer"


# --- queue_take and queue_remove descriptions -----------------------------------
def test_queue_take_and_remove_descriptions_point_to_the_right_tool():
    take = tools.TOOLS["queue_take"].description
    remove = tools.TOOLS["queue_remove"].description
    assert "never to delete one" in take
    assert "call queue_list first" in remove


def test_queue_take_points_numbered_removals_to_queue_remove():
    take = tools.TOOLS["queue_take"].description
    remove = tools.TOOLS["queue_remove"].description
    assert "A request to take a prompt out of the queue by its number is queue_remove." in take
    assert "take it out of the queue" in remove
    assert "haal uit de wachtrij" in remove


# --- make_presentation and make_spreadsheet descriptions -------------------------
def test_make_office_descriptions_do_not_ask_what_to_put_in():
    pres = tools.TOOLS["make_presentation"].description
    sheet = tools.TOOLS["make_spreadsheet"].description
    phrase = ("When the request gives no details and one quick look finds none,"
              " make it anyway with sensible general content and say so in the answer; do not ask first.")
    assert phrase in pres
    assert phrase in sheet


# --- request_capability description ---------------------------------------------
def test_request_capability_description_covers_smart_devices_and_online_services():
    desc = tools.TOOLS["request_capability"].description
    phrase = ("A smart device or online service with no tool here (lamps, a thermostat, "
              "a streaming account) is such a request: call this straight away, do not look"
              " for an app or a window first.")
    assert phrase in desc


# --- weather description --------------------------------------------------------
def test_weather_description_says_one_call_holds_tomorrow_too():
    desc = tools.TOOLS["weather"].description
    phrase = ("One call holds today and the coming days, and tomorrow is the first date after"
              " today in it: answer from that result, do not call it again or search the web.")
    assert phrase in desc


# --- close_window description ---------------------------------------------------
def test_close_window_description_says_call_it_directly():
    desc = tools.TOOLS["close_window"].description
    assert "no list_windows or focus_window first" in desc


# --- find_files description -----------------------------------------------------
def test_find_files_description_says_one_search_covers_variants():
    desc = tools.TOOLS["find_files"].description
    assert "do not repeat with variants" in desc


# --- registry guards -----------------------------------------------------------
def test_every_tool_is_documented():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for name in tools.TOOLS:
        assert f"| {name} |" in readme, f"{name} has no row in the README tools table"


def test_every_tool_has_a_valid_tier():
    for tool in tools.TOOLS.values():
        assert tool.tier in (tools.SAFE, tools.CONFIRM, tools.NEVER), tool.name
        assert tool.description.strip(), f"{tool.name} has an empty description"


def test_read_only_tools_are_safe():
    for name in ("system_status", "gpu_check", "comfy_status", "lmstudio_models",
                 "followups", "journal_today", "agents_status", "queue_list", "concept_sites"):
        assert tools.TOOLS[name].tier == tools.SAFE, name


# --- calculate ---------------------------------------------------------------
def test_calculate_never_shows_a_rounded_or_complex_result_as_exact():
    assert tools.calculate("(-8)**(1/3)") == "error: no real answer (a negative number to a fractional power)"
    assert tools.calculate("(-1)**0.5").startswith("error: no real answer")
    assert tools.calculate("10**100/3") == "10**100/3 = 3.33333333333e+99"
    assert tools.calculate("4e15/2") == "4e15/2 = 2e+15"
    assert tools.calculate("10/2") == "10/2 = 5"


def test_calculate_too_many_digits_to_print_is_an_error():
    assert tools.calculate("*".join(["99**100"] * 23)) == "error: number too large"


def test_calculate_multiplication():
    assert tools.calculate("17*23") == "17*23 = 391"


def test_calculate_float_sum_rounds_to_twelve_significant_digits():
    assert tools.calculate("0.1+0.2") == "0.1+0.2 = 0.3"


def test_calculate_comma_is_a_decimal_separator():
    assert tools.calculate("2,5 * 4") == "2,5 * 4 = 10"


def test_calculate_division_gives_a_float():
    assert tools.calculate("7/2") == "7/2 = 3.5"


def test_calculate_negative_base_power():
    assert tools.calculate("(-3)**2") == "(-3)**2 = 9"


def test_calculate_subtraction_floor_division_modulo_and_unary_plus():
    assert tools.calculate("5-2") == "5-2 = 3"
    assert tools.calculate("7//2") == "7//2 = 3"
    assert tools.calculate("7%2") == "7%2 = 1"
    assert tools.calculate("+5") == "+5 = 5"


def test_calculate_negative_exponent_gives_a_float():
    assert tools.calculate("2**-2") == "2**-2 = 0.25"


def test_calculate_allows_an_exponent_of_100():
    assert tools.calculate("2**100") == "2**100 = 1267650600228229401496703205376"


def test_calculate_refuses_an_exponent_above_100():
    assert tools.calculate("2**101") == "error: exponent too large"


def test_calculate_refuses_nested_powers_that_grow_without_bound():
    assert tools.calculate("((9**99)**99)**99") == "error: number too large"
    assert tools.calculate("(9**99)**9") == "(9**99)**9 = " + str(9 ** 891)


def test_calculate_refuses_division_by_zero_in_all_three_forms():
    assert tools.calculate("1/0") == "error: division by zero"
    assert tools.calculate("5//0") == "error: division by zero"
    assert tools.calculate("5%0") == "error: division by zero"


def test_calculate_refuses_names_calls_strings_comparisons_and_subscripts():
    for expr in ("__import__('os')", "x + 1", "'abc'", "1 < 2", "x[0]"):
        assert tools.calculate(expr) == "error: only numbers and + - * / // % ** are allowed"


def test_calculate_refuses_boolean_constants():
    assert tools.calculate("True + 1") == "error: only numbers and + - * / // % ** are allowed"


def test_calculate_refuses_a_malformed_expression():
    assert tools.calculate("1 +") == "error: only numbers and + - * / // % ** are allowed"


def test_calculate_refuses_an_expression_longer_than_200_chars():
    assert tools.calculate("9" * 201) == "error: expression too long"


def test_calculate_refuses_a_number_too_large():
    assert tools.calculate("(1e308)**2") == "error: number too large"
    assert tools.calculate("1e308*1e308") == "error: number too large"


def test_calculate_formats_repeating_decimals_with_twelve_digits():
    assert tools.calculate("1/3") == "1/3 = 0.333333333333"


def test_calculate_is_registered_safe():
    t = tools.TOOLS["calculate"]
    assert t.tier == tools.SAFE
    assert t.required == ["expression"]
    assert "instead of working it out yourself" in t.description


# --- timers --------------------------------------------------------------------
@pytest.fixture
def fake_timers(monkeypatch):
    """A real Timers on a hand-advanced clock and recording scheduler, installed as tools.TIMERS."""
    timers, start, clock, announced = make_timers()
    monkeypatch.setattr(tools, "TIMERS", timers)
    return timers, start, clock, announced


def test_timer_set_whole_minutes(fake_timers):
    assert tools.timer_set(10, "pasta") == "Timer 1 set for 10 minutes: pasta."


def test_timer_set_one_minute_is_singular(fake_timers):
    assert tools.timer_set(1) == "Timer 1 set for 1 minute."


def test_timer_set_fractional_minutes_keep_the_decimal(fake_timers):
    assert tools.timer_set(2.5, "tea") == "Timer 1 set for 2.5 minutes: tea."


def test_timer_set_under_a_minute_says_seconds(fake_timers):
    assert tools.timer_set(0.5) == "Timer 1 set for 30 seconds."


def test_timer_set_refuses_zero_and_too_long(fake_timers):
    assert tools.timer_set(0) == "error: seconds must be greater than zero"
    assert tools.timer_set(-2, "pasta") == "error: seconds must be greater than zero"
    assert tools.timer_set(1441) == "error: seconds must be at most 86400 (24 hours)"


def test_timer_set_passes_the_message_through_to_the_announce(fake_timers):
    _, start, _, announced = fake_timers
    tools.timer_set(10, "pasta", "De pasta is klaar.")
    start.calls[0][1]()  # fire by hand
    assert announced == ["De pasta is klaar."]


def test_timer_set_refuses_a_message_longer_than_200_characters(fake_timers):
    assert tools.timer_set(10, "pasta", "x" * 201) == "error: message too long"


def test_timer_set_at_is_the_minutes_until_that_clock_time(fake_timers, monkeypatch):
    _, start, _, _ = fake_timers
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 10, 30, 14, 30))
    assert tools.timer_set(at="15:00") == "Timer 1 set for 15:00 (in 30 minutes)."
    assert [c[0] for c in start.calls] == [1800.0]


def test_timer_set_at_in_the_past_is_tomorrow(fake_timers, monkeypatch):
    _, start, _, _ = fake_timers
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 10, 30, 15, 30))
    assert tools.timer_set(at="15:00") == "Timer 1 set for 15:00 (in 23 h 30 min)."
    assert [c[0] for c in start.calls] == [84600.0]


def test_timer_set_at_equal_to_now_is_24_hours(fake_timers, monkeypatch):
    _, start, _, _ = fake_timers
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 10, 30, 15, 0))
    assert tools.timer_set(at="15:00") == "Timer 1 set for 15:00 (in 24 h)."
    assert [c[0] for c in start.calls] == [86400.0]


def test_timer_set_at_with_a_label(fake_timers, monkeypatch):
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 10, 30, 14, 30))
    assert tools.timer_set(at="15:00", label="pasta") == "Timer 1 set for 15:00 (in 30 minutes): pasta."


def test_timer_set_refuses_bad_at_formats(fake_timers, monkeypatch):
    monkeypatch.setattr(tools, "_wall_now", lambda: datetime(2026, 10, 30, 14, 30))
    for bad in ("25:00", "3 uur", "15:7"):
        assert tools.timer_set(at=bad) == "error: at must look like 15:00"


def test_timer_set_refuses_both_minutes_and_at(fake_timers):
    assert tools.timer_set(10, at="15:00") == "error: give either minutes (such as 10) or at (such as 15:30), not both"


def test_timer_set_refuses_neither_minutes_nor_at(fake_timers):
    assert tools.timer_set() == "error: say how long (minutes, such as 10) or until when (at, such as 15:30)"


def test_timer_set_registers_a_scheduled_thread(fake_timers):
    _, start, _, _ = fake_timers
    tools.timer_set(10, "pasta")
    assert [c[0] for c in start.calls] == [600.0]  # minutes became seconds


def test_timer_list_empty(fake_timers):
    assert tools.timer_list() == "No timers running."


def test_timer_list_shows_each_timer_soonest_first(fake_timers):
    _, _, clock, _ = fake_timers
    tools.timer_set(10, "pasta")  # id 1, due at 600 s
    tools.timer_set(2, "tea")     # id 2, due at 120 s
    clock.advance(30)             # now = 30: tea has 90 s left, pasta 570 s
    assert tools.timer_list() == (
        "2. tea: 1 min 30 s left\n"
        "1. pasta: 9 min 30 s left")


def test_timer_cancel_one(fake_timers):
    _, start, _, _ = fake_timers
    tools.timer_set(5, "pasta")
    assert tools.timer_cancel(1) == "Cancelled timer 1."
    assert start.calls[0][2].cancelled is True  # the scheduled thread was cancelled too
    assert tools.timer_list() == "No timers running."


def test_timer_cancel_unknown_id(fake_timers):
    assert tools.timer_cancel(7) == "error: no timer 7; timer_list shows the running ones"


def test_timer_cancel_zero_cancels_all(fake_timers):
    tools.timer_set(5, "a")
    tools.timer_set(6, "b")
    assert tools.timer_cancel() == "Cancelled 2 timers."
    assert tools.timer_list() == "No timers running."


def test_timer_tools_refuse_to_run_without_a_timers(monkeypatch):
    monkeypatch.setattr(tools, "TIMERS", None)
    msg = "error: timers only work while Iris runs in voice or text mode (python -m iris or python -m iris --voice)"
    assert tools.timer_set(5) == msg
    assert tools.timer_list() == msg
    assert tools.timer_cancel() == msg


def test_timer_tools_are_registered_safe():
    t = tools.TOOLS["timer_set"]
    expected = ("Set a countdown timer; Iris says `message` out loud when it is done. "
                "Always pass message in the user's language.")
    assert t.tier == tools.SAFE and t.required == []  # minutes or at, exactly one, so neither is required
    assert t.description == expected
    assert "A clock time such as 15:00 instead of minutes" in t.params["at"]["description"]
    assert tools.TOOLS["timer_list"].tier == tools.SAFE
    assert tools.TOOLS["timer_cancel"].required == []


def test_timer_set_schema_describes_the_message_param():
    m = tools.TOOLS["timer_set"].params["message"]
    assert m["type"] == "string"
    assert "in the language the user spoke" in m["description"]
    assert "'De pasta is klaar.'" in m["description"]


def test_set_announcer_installs_the_shared_timers(monkeypatch):
    from iris.timers import Timers

    monkeypatch.setattr(tools, "TIMERS", None)
    announced: list[str] = []
    tools.set_announcer(announced.append)
    assert isinstance(tools.TIMERS, Timers)



def test_duration_text_never_reads_out_a_long_fraction():
    assert tools._duration_text(549.9862859333333) == "9 h 10 min"
    assert tools._duration_text(12.7) == "13 minutes"
    assert tools._duration_text(1.5) == "1.5 minutes"
    assert tools._duration_text(60) == "1 h"
    assert tools._duration_text(10) == "10 minutes"


def test_restore_brings_back_the_last_trashed_not_the_newest_file(home):
    import os

    new, old = home / "edited-yesterday.txt", home / "edited-last-week.txt"
    new.write_text("new")
    old.write_text("old")
    os.utime(old, (1_700_000_000, 1_700_000_000))  # the file itself is older...
    tools.trash_file(str(new))
    tools.trash_file(str(old))  # ...but it went to the trash last
    for f in (home / "trash").glob("*.json"):  # make the order unambiguous even within one second
        os.utime(f, (2_000_000_000 if "last-week" in f.name else 1_900_000_000,) * 2)
    tools.restore_trash()
    assert old.exists() and not new.exists()


def test_every_tool_accepts_the_parameters_its_schema_offers():
    """The model calls a tool with the names in its schema; a function whose parameter is named differently (rid
    for id) fails on every real call, which the eval's dry tools never see."""
    import inspect

    for tool in tools.TOOLS.values():
        sig = inspect.signature(tool.fn)
        sample = {p: 1 for p in tool.params}
        try:
            sig.bind(**{p: sample[p] for p in tool.required})
            sig.bind(**sample)
        except TypeError as exc:
            raise AssertionError(f"{tool.name}: {exc}") from None


def test_comfy_queue_tells_a_refused_workflow_from_a_stopped_comfyui(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    _queue_folder(tmp_path, monkeypatch)
    monkeypatch.setattr(tools, "system_data", lambda: _two_gpus())

    def refused(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    assert tools.comfy_queue("anime") == "error: ComfyUI refused anime (HTTP 400); open it in ComfyUI to see why"
