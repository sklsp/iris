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

