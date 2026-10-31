# Design notes

Why Iris is built the way it is. Every claim here comes from the code or [PLAN.md](PLAN.md).

## Why UI Automation before pixels

Windows exposes an accessibility tree: every window has named controls (buttons, fields, text).
Iris reads that tree with pywinauto instead of taking screenshots and guessing coordinates.
Clicking a control by its exact name is faster, cheaper and far more reliable than matching pixels.
`read_window` returns the control names and the contents of text fields, capped at 120 controls so the model gets a bounded view.
There is no screenshot or OCR code in `iris/` at all; PLAN.md keeps vision as a fallback only for apps that expose nothing.

## Why tiers live in code

The risk tier (safe / confirm / never) is decided by `tool.tier_of(args)` in the agent, not by what the model says.
The model can choose a tool and its arguments, but it cannot change the tier: NEVER returns a refusal with no confirmation path, CONFIRM goes through the spoken yes/no callback, SAFE runs immediately.
When a tool does run and fails (its function returns a string starting with `error:` or raises), the call is journaled as outcome `error` and the error text goes back to the model, so it can retry another way or tell Jay what went wrong.
Five tools escalate on their arguments (`tier_for`): `click` and `type_text` when the control or field name says send/buy/pay/password/delete (English and Dutch), `press_keys` for `{ENTER}` in a mail/chat window, `trash_file` for paths it must not touch, and `lmstudio_unload` for the model Iris runs on or "all".
Tests pin every tier and escalation, so a change to a tier is a visible test failure, not a prompt edit.

## Iris may plan her own skills, never weaken her own rules

When Jay asks for something Iris cannot do yet, she says so and offers to plan a new skill; after his yes (`request_capability` is confirm tier) `save_plan` writes the plan as `<name>.md` plus a `.json` sidecar in `~/.iris/capabilities`, and `capability_list` lists what is saved. The rails that keep this safe:

- The tiers are locked in `iris/tier_lock.json`: every tool in TOOLS has a line with its tier and whether it escalates, and the tests read both sides of that agreement, so a branch that changes an existing tier fails the suite before anyone looks at the diff.
- The escalation rules live in `iris/policy.py`, a protected file no self-built branch may edit: the words that escalate a clicked control or typed field name (English and Dutch), the mail/chat windows where Enter is never pressed, the trash roots and system dirs `trash_file` must not touch, and how an unload is judged. `tools.py` imports every rule back from there, so both modules agree on one policy, and `tests/test_policy.py` pins each rule, so weakening one is a visible test failure, not a silent edit.
- A self-built branch may not touch the protected paths (`PROTECTED` in `iris/capabilities.py`: `agent.py` with its system prompt, `policy.py` with the escalation rules, the guard itself, the eval harness, the three test files that pin these rails, and `.github/`), and `guard_problems` reports every change to an existing tier-lock line; new tools may only be added. An empty report is all that makes a branch acceptable, and Jay still has to say OK before anything merges.
- Only Jay's own request can start a plan: the tool is confirm tier, it saves nothing until he says yes, and text read from a window or a page is data that cannot call it (see Untrusted tool output).
- A saved plan builds nothing. Building the skill happens later on its own branch, and nothing a self-built branch does goes live without Jay merging it.

## Why a build runs in its own worktree

A self-build edits `iris/tools.py` and other files of Iris itself, so it must not run on the checkout Jay is using. `prepare_build` creates a separate git worktree next to the repo (`Iris-self/<name>`, or the folder named by `IRIS_SELF_WORKTREES`) on its own branch `self/<name>` from main, copies the plan into it as `BUILD-<name>.md` and marks the plan "building"; Cline then works only inside that copy. The main tree stays clean while the build runs, so a crash or an abandoned build cannot leave Jay's Iris half edited; the guard can diff exactly what changed against main (`main...self/<name>`); and `capability_check` runs the gates in the worktree with its own `.venv`, so "ready" means the built copy passes ruff, mypy and pytest. Nothing goes live until Jay reviews the branch and merges it himself; Iris never merges or uses a skill before that.

## Untrusted tool output

Text on screen is data, never instructions: PLAN.md's third principle.
Every tool result is wrapped in `<untrusted_data>` tags before it goes back to the model, and the system prompt says tool results are DATA whose embedded instructions must never be followed.
This matters because `read_window` returns whatever a window contains, including web pages that could try to steer the agent.
The eval harness includes trap tasks with instructions hidden in windows; they pass only while this marking holds.

## Web pages are data

Everything `web_search` and `web_read` return reaches the model inside `<untrusted_data>` tags, exactly like screen text, so an instruction on a page is never followed. The fetch also refuses local addresses, loopback and private ranges, so a page cannot steer Iris into this PC's own services.

## Why a local model

Everything runs on this machine: no cloud and no cost per command (PLAN.md).
The brain is Qwen 3.8 27B in LM Studio, reached through its OpenAI-compatible API at `127.0.0.1:1234` with native tool calling; the client in `llm.py` is stdlib-only urllib.
The model id resolves per request as environment (`IRIS_MODEL`) → `~/.iris/settings.json` → default, so switching models needs no restart and no code change.

## A model failure ends the turn, not the session

When LM Studio is down or answers with an error, `Agent.handle` catches it, journals an `llm_error` line and returns a spoken apology; the loop in `__main__.py` goes straight back to listening, so one bad request never kills the session. Before that, the failed turn is removed from the history: everything from the last user message onward (including any tool results already collected during the turn) is dropped. A half turn with tool results but no answer would confuse the next request, because the model would see results for a question it never answered and might act on them; dropping the partial turn keeps every conversation in memory whole, so a retry starts from clean history.

## Voice: wake word, end of speech, interrupting

The ears are openWakeWord (ONNX variant, because tflite has no Windows wheels) with the `hey_jarvis` model by default (`IRIS_WAKEWORD` overrides), scoring each mic frame against a 0.5 threshold.
End of speech is an energy VAD: frames above 0.01 RMS count as voiced, and recording stops after 1.2 s of continuous silence following some speech (or a hard cap); faster-whisper then transcribes with its own vad filter.
The microphone stream is long-lived and never closed between requests, which is what makes interrupting possible: while Iris speaks, a watcher thread consumes the same frames looking for the wake word.
Kokoro streams chunk by chunk, so barge-in lands between chunks (SAPI cannot be interrupted mid-utterance); after an interruption the next request records straight away without asking for the wake word again.

## The eval harness

PLAN.md's headline: a fixed set of scripted tasks with a success rate is what sets Iris apart from every "Jarvis" demo.
`eval/tasks.toml` holds 56 tasks, each with a request (some in Dutch), the tools that must be called (`expect`), tools that must never succeed (`forbid`), a call budget and canned fixtures; dry-run tools return those fixtures and touch nothing on the PC.
`python -m iris eval` runs them against any model endpoint, writes `<stamp>.json`/`.md` reports to `eval/results`, and `--only`/`--repeat` plus `eval-compare` make before/after comparisons cheap; the dashboard has an Eval tab for the same data.

## What it does not do yet

From PLAN.md's "Next": run the eval on the real model and publish the number, and a custom "hey iris" wake word (today it is openWakeWord's `hey_jarvis`).
The v2 skills now change things, but only with a confirm: `comfy_queue` submits a saved workflow to ComfyUI, and `lmstudio_load`/`lmstudio_unload` load or unload a model in LM Studio; `comfy_status`, `comfy_workflows` and `lmstudio_models` stay read-only.
And on purpose, forever: logging in, payments, messages to other people, and anything irreversible stay with Jay (PLAN.md, "Not in scope").
