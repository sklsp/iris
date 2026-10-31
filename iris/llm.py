"""Chat with tool calling over an OpenAI-compatible API (LM Studio by default). Stdlib only."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BASE_URL = os.environ.get("IRIS_LLM_URL", "http://127.0.0.1:1234/v1")
DEFAULT_MODEL = "qwen3.8-27b"


def settings_path() -> Path:
    """Where the user's model choice lives; IRIS_SETTINGS overrides it (tests)."""
    return Path(os.environ.get("IRIS_SETTINGS") or (Path.home() / ".iris" / "settings.json"))


def read_settings() -> dict:
    """The contents of settings.json, re-read on every call so edits apply at once.

    A missing file, a broken one and a non-object top level all mean "no user choice"."""
    try:
        with settings_path().open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_model() -> tuple[str, str]:
    """(model, source); the source is "env", "settings" or "default".

    The IRIS_MODEL environment variable wins; then the "model" key in settings.json;
    otherwise the default. Re-resolved on every call so a change applies at once."""
    env = os.environ.get("IRIS_MODEL") or ""
    if env:
        return env, "env"
    model = read_settings().get("model")
    if isinstance(model, str) and model:
        return model, "settings"
    return DEFAULT_MODEL, "default"


def effective_model() -> str:
    """The model Iris talks to right now."""
    return resolve_model()[0]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # the assistant message, to append to the history as-is


class LMStudio:
    def __init__(self, base_url: str = BASE_URL, model: str | None = None, timeout: float = 300):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model   # an explicit id wins; None resolves on every request
        self.timeout = timeout

    def _model(self) -> str:
        return self.model if self.model is not None else effective_model()

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        # Qwen reasons before it acts: 1500 to 2500 tokens on an open request, so 4096 cut some replies off.
        body = {"model": self._model(), "messages": messages, "tools": tools, "temperature": 0.3, "max_tokens": 8192}
        req = urllib.request.Request(self.url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            choice = json.load(r)["choices"][0]
        msg = choice["message"]
        if choice.get("finish_reason") == "length" and not msg.get("content") and not msg.get("tool_calls"):
            # an empty reply would make Iris say nothing; as a ValueError the agent reports a model error instead
            raise ValueError("the model ran out of tokens while thinking")
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": c["function"].get("arguments")}
            calls.append(ToolCall(c.get("id", ""), c["function"]["name"], args))
        raw = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            raw["tool_calls"] = msg["tool_calls"]
        return Reply(msg.get("content") or "", calls, raw)
