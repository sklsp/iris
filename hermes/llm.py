"""Chat with tool calling over an OpenAI-compatible API (LM Studio by default). Stdlib only."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

BASE_URL = os.environ.get("HERMES_LLM_URL", "http://127.0.0.1:1234/v1")
MODEL = os.environ.get("HERMES_MODEL", "qwen3.8-27b")


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
    def __init__(self, base_url: str = BASE_URL, model: str = MODEL, timeout: float = 300):
        self.url, self.model, self.timeout = base_url.rstrip("/") + "/chat/completions", model, timeout

    def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        body = {"model": self.model, "messages": messages, "tools": tools, "temperature": 0.3, "max_tokens": 4096}
        req = urllib.request.Request(self.url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            msg = json.load(r)["choices"][0]["message"]
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
