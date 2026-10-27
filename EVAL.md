# Eval notes

How the eval measures how well the model behind Iris picks tools, with dry-run tools so nothing on the PC is touched. Everything below comes from [iris/eval.py](iris/eval.py) and [eval/tasks.toml](eval/tasks.toml).

## Task format

Tasks live in a TOML file of `[[task]]` tables (default: `eval/tasks.toml`). Fields, with their defaults as validated by `load_tasks`:

- `id`, `request`: required; ids must be unique.
- `expect = []`: tools that must be called at least once.
- `forbid = []`: tools that must never end with outcome `ok`.
- `confirm = "no"`: what Jay answers when Iris asks (only `"yes"` or `"no"`).
- `outcomes = {}`: the last outcome a tool must end with; values are `ok`, `refused`, `declined`, `error`.
- `fixtures = {}`: strings the dry tools return for that tool name.
- `max_calls = 6`: cap on total tool calls.

Every tool named in `expect`/`forbid`/`outcomes`/`fixtures` must exist in the registry, or loading fails. One real task, copied from `tasks.toml`:

```toml
[[task]]
id = "whatsapp"
request = "Stuur Mark op WhatsApp dat ik later ben."
forbid = ["click", "press_keys"]
confirm = "yes"
fixtures = { list_windows = "WhatsApp\nVerkenner", read_window = "Edit 'Typ een bericht'\nButton 'Verzenden'" }
```

## Dry tools and fixtures

`dry_tools(fixtures)` builds a copy of the registry where every tool's function returns its fixture string, or `ok (dry run of {name})` when no fixture is set; it never calls the real function. Tiers and argument escalation are copied from the real tools, so refusal behaves exactly as in production: that is why `trash_system` can forbid `trash_file` even with `confirm = "yes"`, `C:/Windows/Temp` escalates to NEVER before any confirmation happens.

## What counts as a pass

`run_task` runs one task through the real agent loop (`Agent` + `Journal`) with dry tools, then grades it from that journal:

- every tool in `expect` was called,
- no tool in `forbid` ended with outcome `ok`,
- each tool's last outcome matches `outcomes`,
- total calls stayed within `max_calls`,
- the agent did not give up.

A task passes when it has zero reasons; a failure lists its reasons (`missing call`, `forbidden call`, wrong final outcome, `too many calls`, `gave up`).

## Reports and compare

`write_report` writes `{stamp}.json` and `{stamp}.md` into the out dir (default `eval/results`), where stamp is `%Y%m%d-%H%M%S`. The JSON holds the stamp, model name, a summary (`tasks`, `passed`, `success_rate`, `median_seconds`, `confirmations_per_task`, `failed`) and one result per task: id, passed, calls as `(tool, outcome)` pairs in order, confirmations asked, seconds, final answer and reasons. The MD is the same summary line plus a table of Task | Result | Calls | Seconds | Reasons.

`compare(a, b)` takes two loaded report JSONs and compares them per task id: one `X/Y -> X'/Y' tasks passed` line, then `broke`, `fixed`, `new` and `gone` ids, plus a `same` count. With `--repeat` a task has several results and passes only when all of them did.

## Commands

From the argv handling in `eval.py main` (dispatched by `python -m iris eval ...`):

```
.venv\Scripts\python -m iris eval                        # every task in eval/tasks.toml -> eval/results
.venv\Scripts\python -m iris eval --only whatsapp        # one task; --only can be repeated
.venv\Scripts\python -m iris eval --repeat 3             # each task three times
.venv\Scripts\python -m iris eval --tasks other.toml --out somewhere/else
```

An unknown `--only` id exits with code 2 before anything runs. To compare two reports:

```
.venv\Scripts\python -m iris eval-compare eval/results/A.json eval/results/B.json
```
