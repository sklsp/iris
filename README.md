# Iris

A local voice agent that operates this Windows PC. You say what you want; Iris reads the screen through
Windows UI Automation, picks tools with a local LLM (Qwen in LM Studio), asks before it changes anything,
and answers out loud. No cloud, no cost per command. See [PLAN.md](PLAN.md) for where it is going.

## Run

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[voice,dev]"
.venv\Scripts\python -m Iris            # type requests
.venv\Scripts\python -m Iris --voice    # speak (press Enter to stop recording), hear the answer
```

LM Studio must be serving on `http://127.0.0.1:1234/v1` with a tool-calling model. Override it with
`IRIS_LLM_URL` and `IRIS_MODEL`. Speech recognition runs on the CPU by default
(`IRIS_WHISPER_DEVICE=cuda` moves it to a GPU).

## Tools and risk tiers

| Tool | Tier | What it does |
|---|---|---|
| system_status | safe | CPU, RAM, and per-GPU load, VRAM and temperature |
| list_windows | safe | titles of the open windows |
| read_window | safe | the named controls in a window, with the contents of text fields |
| open_app | safe | start an app, an `ms-settings:` page or a file |
| click | confirm | click a control by its name |
| type_text | confirm | type into a window or a named field, optionally replacing what is there |

The tiers are enforced in code, not in the prompt. A click or a field whose name means sending, buying,
paying, deleting or a password (English and Dutch) is refused even if you say yes. Screen contents reach
the model wrapped as untrusted data. Every request, tool call, confirmation and result is appended to
`~/.Iris/journal.jsonl`.

## Status

Core loop works end to end against Qwen 3.8 27B. On this PC, a Dutch status question took 9 s, reading
Notepad took 5 s, and an edit request stopped at the confirmation. 8 tests cover the loop with a scripted
model: tiers, refusals, declines, the step limit, and unknown tools.
