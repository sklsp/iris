"""Behavioural tests for iris/llm.py: the OpenAI-compatible chat client.

The network is faked by replacing urllib.request.urlopen with a recorder that
returns canned JSON payloads, so every test asserts on the exact request body
sent and the exact Reply parsed back. No server, no model, no internet.
"""
import io
import json

from iris.llm import LMStudio, Reply, ToolCall, effective_model


class FakeResponse:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self):
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_urlopen(monkeypatch, payload):
    """Replace urllib.request.urlopen; record (request, timeout) and serve one canned payload."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def test_init_builds_chat_completions_url_and_stores_model_and_timeout():
    lm = LMStudio(base_url="http://host:1234/v1/", model="m-1", timeout=5)
    assert lm.url == "http://host:1234/v1/chat/completions"
    assert lm.model == "m-1"
    assert lm.timeout == 5

    default = LMStudio()
    assert default.url.endswith("/chat/completions")
    assert default.model is None
    assert default.timeout == 300


def test_model_none_resolves_effective_model_but_explicit_wins(monkeypatch):
    monkeypatch.setenv("IRIS_MODEL", "env-model")
    assert LMStudio()._model() == "env-model"
    assert LMStudio(model="fixed")._model() == "fixed"


def test_effective_model_returns_the_resolved_id(monkeypatch):
    monkeypatch.setenv("IRIS_MODEL", "abc-123")
    assert effective_model() == "abc-123"


def test_chat_sends_full_body_and_parses_plain_text_reply(monkeypatch):
    seen = _fake_urlopen(
        monkeypatch, {"choices": [{"message": {"content": "hello there"}}]}
    )
    lm = LMStudio(base_url="http://h:1/v1", model="fixed-model", timeout=5)

    reply = lm.chat([{"role": "user", "content": "hi"}], [])

    assert isinstance(reply, Reply)
    assert reply.text == "hello there"
    assert reply.calls == []
    assert reply.raw == {"role": "assistant", "content": "hello there"}

    req = seen["request"]
    assert req.full_url == "http://h:1/v1/chat/completions"
    assert dict(req.header_items()) == {"Content-type": "application/json"}
    assert json.loads(req.data.decode("utf-8")) == {
        "model": "fixed-model",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    assert seen["timeout"] == 5


def test_chat_parses_tool_calls_with_json_arguments(monkeypatch):
    tool_call = {
        "id": "c1",
        "function": {"name": "open_app", "arguments": "{\"app\": \"notepad\"}"},
    }
    seen = _fake_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": None, "tool_calls": [tool_call]}}]},
    )
    lm = LMStudio(base_url="http://h:1/v1", model="m")

    reply = lm.chat([], [])

    assert reply.text == ""  # content was null
    assert reply.calls == [ToolCall(id="c1", name="open_app", args={"app": "notepad"})]
    assert reply.raw["tool_calls"] == [tool_call]
    assert reply.raw["content"] == ""


def test_chat_keeps_unparsed_arguments_when_json_is_broken(monkeypatch):
    tool_call = {
        "id": "c2",
        "function": {"name": "trash_file", "arguments": "{not json"},
    }
    _fake_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": None, "tool_calls": [tool_call]}}]},
    )
    lm = LMStudio(base_url="http://h:1/v1", model="m")

    reply = lm.chat([], [])

    assert reply.calls == [ToolCall(id="c2", name="trash_file", args={"_unparsed": "{not json"})]


def test_chat_defaults_missing_arguments_to_empty_dict(monkeypatch):
    tool_call = {"id": "c3", "function": {"name": "say"}}  # no arguments key at all
    _fake_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": None, "tool_calls": [tool_call]}}]},
    )
    lm = LMStudio(base_url="http://h:1/v1", model="m")

    reply = lm.chat([], [])

    assert reply.calls == [ToolCall(id="c3", name="say", args={})]
