"""Tests for press_keys, focus_window, scroll and open_url with fakes only (no real desktop)."""

from pathlib import Path

import pytest

from iris import tools


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
    for var in ("IRIS_TTS", "IRIS_LLM_URL", "IRIS_WHISPER_DEVICE", "IRIS_TRASH_DIR", "IRIS_AGENTS_TOML"):
        assert var in readme



def test_trash_refuses_paths_outside_home_and_w_and_system_dirs():
    from pathlib import Path

    from iris.tools import CONFIRM, NEVER, TOOLS

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
    assert tools.lmstudio_models() == "LM Studio is not reachable."



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
    assert tools.followups() == "No leadscan CRM found."



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

