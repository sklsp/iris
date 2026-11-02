# Contributing

Thanks for helping. Iris drives a real PC, so every change keeps one rule: **the risk tier of an action is decided in
code, never by the model.**

## Setup

```bash
git clone https://github.com/sklsp/Iris && cd Iris
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,office]"
```

## The gates (CI runs the same three)

```bash
.venv\Scripts\python -m ruff check iris tests
.venv\Scripts\python -m mypy
.venv\Scripts\python -m pytest -q --cov=iris
```

Tests never touch the real desktop, network, clipboard, microphone, LM Studio or files outside `tmp_path`: fake them
with `monkeypatch`. `tests/conftest.py` already points the journal, `config.toml` and the skills folder into `tmp_path`.

## Adding a tool

1. A function that returns a short text (errors start with `error:`), in the module that fits.
2. A `Tool(...)` entry in `iris/tools.py` with its tier. Anything that changes something is `confirm`. Sending,
   paying, passwords and permanent deletes are `never`. When the arguments can make it riskier, add a `tier_for`
   function in `iris/policy.py`.
3. A line in `iris/tier_lock.json`, a test that pins every tier literally, and a `_describe` case in `iris/agent.py`
   for a confirm tool (English and Dutch), so the spoken question is plain.
4. Two eval tasks in `eval/tasks.toml` (one Dutch, one English) and a row in the README's risk tier table. Give every
   tool the model may call first a fixture too: without one it gets a dry-run placeholder, not what the real tool
   says. Fixtures are written for the eval's fixed date, Sunday 1 November 2026.

A tool that only makes sense on your own setup belongs in a plugin group (`iris/registry.py`), off by default.

## Measuring

Changes to prompts or tool descriptions are measured, not guessed: run `python -m iris eval --repeat 3` with LM Studio
running (it touches nothing on the PC) and compare with `python -m iris eval-compare old.json new.json`. A run with
`--only ID` checks a few tasks quickly; only a full run is published with `python -m iris eval publish`.

## Style

Short functions with a docstring that says what and why. Standard library first. No em dashes or en dashes in text.
