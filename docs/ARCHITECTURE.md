# How Iris works

The README says how to use Iris; this page says how it works inside, written from the code in `iris/`.
Every claim names the file that makes it true, usually with the function. If something is not here,
it is either obvious or not worth saying.

## One voice turn, start to end

The voice loop lives in `iris/__main__.py` (the `--voice` mode) around one long-lived object:
`VoiceSession` in `iris/voice.py`. The microphone stream stays open the whole time, so a wake word
can interrupt speech even while Iris is talking.

1. **Wake word.** `WakeWord.detect` scores each 80 ms frame with an openWakeWord model (default
   `hey_jarvis`, set by `wakeword` in config.toml or IRIS_WAKEWORD). The alternative `NameWake` wakes
   on the spoken name through a tiny Whisper hotword check instead of a trained model. A short chime
   plays right after the wake word so you hear that Iris is listening (IRIS_CHIME=0 mutes it).
2. **Recording and speech recognition.** After the wake word, `_record` records until silence follows
   speech (1.8 s by default: `SILENCE_SECONDS`, set with `silence` in config.toml or IRIS_SILENCE) or a
   hard cap, faster-whisper transcribes the audio, and `_strip_wake` removes the wake phrase from the
   transcript. The loop writes an `stt` journal line with how long recognition took.
3. **The agent loop.** The transcript goes to `Agent.handle` in `iris/agent.py`, which runs up to 30
   steps (`MAX_STEPS`). Each step sends the conversation and the tool schemas from
   `registry.active_tools()` to the model through `llm.chat`. When the model stops calling tools, its
   text is the answer (journal event `answer`) and the loop ends; after 30 steps it gives up
   (event `gave_up`).
4. **Tool calls with risk tiers.** A tool call goes through `_run`: the tier comes from the tool's entry
   in `TOOLS`, and `Tool.tier_for` can escalate it based on the arguments. `never` is refused out loud,
   `confirm` asks first (one yes covers the other clicks in that window for the rest of the request),
   `safe` runs at once. The result goes back into the conversation and the loop continues.
5. **The answer and speech output.** The answer is printed and spoken by `speak_async`, which uses the
   best engine available: VoxCPM2 (in its own process, see `iris/voxcpm_worker.py`), Piper, Kokoro or
   Windows SAPI as the fallback. A wake word during the speech stops it (barge-in) and the next request
   is recorded straight away; a question within 8 s of an answer needs no wake word at all
   (`FOLLOW_UP_SECONDS`). The overlay panel in `iris/overlay.py` shows what you said and what Iris
   answers, with a cancel button that stops both.

```mermaid
sequenceDiagram
    participant U as User
    participant V as VoiceSession (voice.py)
    participant A as Agent (agent.py)
    participant M as LM Studio model
    participant T as Tools (tools.py)
    U->>V: wake word, then a request
    V->>V: record until silence, transcribe with faster-whisper
    V->>A: Agent.handle(request)
    loop up to 30 steps
        A->>M: conversation plus tool schemas (registry.active_tools)
        M-->>A: a tool call, or the final text
        alt a tool call
            A->>T: tier check; confirm if needed; run it
            T-->>A: result appended to the conversation
        else no more tools
            A-->>V: answer (journal event "answer")
        end
    end
    V->>U: speaks the answer, overlay shows it
```

## The modules

| File | What it does |
|---|---|
| `iris/__main__.py` | the console entry point: text mode, wake-word voice mode with the overlay panel and push-to-talk; runs the request loop and writes the stt and tts journal lines in voice mode |
| `iris/agent.py` | the core loop: request, model picks tools, tier check, confirm, act, answer; `Agent.handle` runs up to 30 steps and `Journal` appends JSON lines rotated at 5 MB |
| `iris/agents.py` | the agent manager: a read-only view of the coding-agent repos from agents.toml (branch, last commit with age, uncommitted files, BLOCKED.md, STUCK flag); only git commands that cannot write |
| `iris/app.py` | `iris app`: a pywebview window around the dashboard, a tray icon, and voice mode as one supervised child process in a Windows Job Object so nothing outlives the app; one instance per user |
| `iris/autopilot.py` | notices when an agent run is done (the newest PROGRESS file fully ticked or BLOCKED with a clean tree) and asks before starting the next queued prompt through the Cline CLI |
| `iris/browser.py` | web pages in the user's own Chromium browser over the DevTools Protocol: numbered buttons, links and fields to read, click and type; one Allow per run; only 127.0.0.1 on this PC |
| `iris/config.py` | the user's settings from ~/.iris/config.toml (IRIS_CONFIG overrides): stdlib only, every key optional, re-read on every call so an edit applies without a restart |
| `iris/dashboard/__init__.py` | the dashboard server at 127.0.0.1:8765: the page plus JSON routes for system status, agents, journal and timings, settings, prompt queue, model management and autopilot |
| `iris/desktop.py` | desktop actions: clipboard, media keys, locking the screen, screenshots, closing windows, clock, email drafts (never sends) and looking at the screen with a vision model |
| `iris/doctor.py` | `iris doctor`: one line per check of the setup (LM Studio, model, config, plugins, voice); never changes anything |
| `iris/eval.py` | the eval: measures how well the model picks tools with dry-run tools; TOML tasks, grading from the journal, reports in eval/results, history and comparison |
| `iris/files.py` | file tools: find files by name, list folders and read text, PDF or Office files |
| `iris/firstrun.py` | the first-run wizard (the dashboard's Setup tab): finds the model server, tests microphone and speaker, shows what `iris doctor` finds, asks about update checks |
| `iris/llm.py` | chat with tool calling over an OpenAI-compatible API (LM Studio by default); stdlib only |
| `iris/notes.py` | notes in ~/.iris/notes.md: one line per note with a timestamp; add, read and remove |
| `iris/office.py` | office files for the user: Word .docx from a title and simple markdown, PowerPoint .pptx from slides, spreadsheet .xlsx from named worksheets of rows |
| `iris/overlay.py` | the small voice-mode panel above the taskbar (what you said, what Iris answers, a cancel button); its own process speaking JSON over stdin/stdout so a crash never touches the voice loop; stdlib only |
| `iris/packs.py` | optional voice packs offered on the Setup tab: whisper, wake word, GPU cuBLAS/cuDNN, Piper and VoxCPM2; nothing downloads until the user clicks, everything lands under ~/.iris |
| `iris/policy.py` | the escalation rules for the tools in one module apart from the tools themselves: safe/confirm/never plus argument-dependent escalations; imported by both `tools.py` and `agent.py` so they agree on one policy |
| `iris/prompt_queue.py` | the prompt queue for the agent manager: prompts wait in a JSON file; Iris lists them and hands the next out only after the user says yes |
| `iris/registry.py` | which tools are offered right now: core tools, plugins enabled in config.toml, approved skills; re-evaluated on every request so enabling a plugin needs no restart |
| `iris/reminders.py` | reminders that survive a restart: a JSON list in ~/.iris/reminders.json, daemon timers while running, missed ones announced at once on start; clock and scheduler injected for tests |
| `iris/settings.py` | the Settings tab data: every config.toml option with explanation, validation and a writer that keeps the file's comments and unknown keys; environment variables still win over the file |
| `iris/skills.py` | skills Iris builds for herself when no tool can do a request: planned, building (a coding agent writes code and tests), ready or blocked, then approved with a hash of the exact code; run as separate processes so they cannot change the policy |
| `iris/timers.py` | timers that Iris announces out loud when they run out; dependency-free, clock and scheduler injected so tests fire them by hand |
| `iris/timings.py` | summarises the journal's timing entries: median and p90 for request, model and tool time, the model's share, the slowest tools, plus stt and tts times; backs `python -m iris timings` |
| `iris/tools.py` | the TOOLS registry: every bundled tool with name, description, parameters, risk tier (from `policy`) and function; `Tool.tier_for` holds argument-dependent escalations |
| `iris/update.py` | notify-only update check against GitHub Releases: only the desktop app, at most once a day; never downloads or installs anything |
| `iris/voice.py` | ears and mouth: openWakeWord wake word plus energy-VAD recording, faster-whisper to transcribe, SAPI, Kokoro, Piper or VoxCPM2 to speak; `VoiceSession` keeps the mic stream open for barge-in |
| `iris/voxcpm_worker.py` | VoxCPM2 speech in its own Python process (it needs torch, Iris does not): JSON lines on stdin, binary float32 chunks on stdout; imports nothing from Iris |
| `iris/weather.py` | the weather from open-meteo.com: geocode a place, then now and up to seven days; stdlib only through `web.http_get` |
| `iris/web.py` | safe web fetching: the only door to the network in this package; refuses local addresses so page content can never point Iris at this machine, caps how much of a page it reads |

## Safety: tiers, policy and the tier lock

Every tool has one of three risk tiers, enforced in code before it runs (`_run` in `iris/agent.py`):
**safe** runs at once, **confirm** asks you out loud first, **never** is refused with an explanation.
The tier sits on each entry of `TOOLS` in `iris/tools.py`; argument-dependent escalations live as
`Tool.tier_for`, so a click on a control named like Send, Buy or Delete becomes never even though plain
clicking is confirm.

The rules themselves are written once in `iris/policy.py`, which both `tools.py` and `agent.py` import,
so the two modules cannot drift apart. With `confirm = "important"` in config.toml Iris asks less:
everyday actions (clicking, typing, making, moving and trashing files) run without a question, while
paying, buying, sending messages or email, deleting in an app and opening private files are asked about
one at a time. Running a program, building or approving a skill, starting a Cline session and loading
models still ask; typing passwords, permanent deletes, system folders, Iris's own files and non-web links
stay refused. It is off by default because your spoken yes becomes the only check before a payment.

The tier lock: `iris/tier_lock.json` pins the tier and whether each tool escalates for every entry of
`TOOLS`. `tests/test_tier_lock.py` checks both directions (every tool is locked, every lock line is a
real tool), so a branch cannot weaken an existing safety rule without a visible diff in that file.

Skills Iris builds for herself never run inside Iris: `iris/skills.py` runs them as a separate process,
so they cannot change these rules. And on purpose, Iris never logs in, pays, sends messages to people or
deletes permanently; it opens the draft or the page and you take the last step.

## Where the time goes

Every request writes one `timing` line to the journal (`~/.iris/journal.jsonl`, rotated at 5 MB into
`journal.jsonl.1` by `Journal` in `iris/agent.py`): total time, model time and call count, tool time,
and the slowest tool of the request. Each individual tool call writes its own `tool_timing` line with
that one call's time.

In voice mode the loop in `iris/__main__.py` adds two more lines per turn: an `stt` line with how long
speech recognition took, and a `tts` line with how long it takes to start sounding. So the whole voice
turn is covered: recognition, model, tools, first sound.

`python -m iris timings` (`timings_main` in `iris/timings.py`) reads those lines (the last 50 by default)
and prints the median and p90 turn time, the model's share of all the time, the five slowest tools, and
the stt and tts medians with their p90s. `--last N` changes how far back it looks, `--by-tool` reads the
tool_timing lines into one line per tool (call count and median), and `--json` prints the summary for
scripts. The dashboard's Activity tab shows the same numbers in a Speed panel with the five slowest tools,
and `iris doctor` ends with a speed line from the same journal. The point is to measure before optimizing:
almost all of a turn's time is model, recognition and speech output, not Python.

## Tests and the eval

The pytest suite in `tests/` covers the agent loop and the journal (`test_agent.py`,
`test_journal_rotation.py`), the safety rules (`test_policy.py`, `test_tier_lock.py`, `test_safety.py`),
the tools' behaviour (`test_tools*.py`), the voice pipeline (`test_voice*.py`, `test_voxcpm*.py`), the
dashboard routes (`test_dashboard*.py`), the skills lifecycle (`test_skills*.py`) and the eval grading
(`test_eval*.py`). A few files keep the documentation in sync with the code: `test_readme.py`,
`test_commands_documented.py` and `test_settings_documented.py`.

The eval (`python -m iris eval`, `iris/eval.py`) measures how well the model behind Iris picks tools.
Every task in `eval/tasks.toml` is a spoken request plus what should happen: which tools must be called,
which must never succeed, whether you say yes or no when Iris asks, and the outcome a tool must end with.
The 251 tasks are everyday requests in five languages plus traps such as sending, paying, passwords and
instructions hidden in a window. The tools run as dry runs: they return the canned text from the task and
touch nothing on the PC, so the eval is safe to run at any time. A task can span several turns (`turns`)
and check what Iris says back (`answer_has`, `answer_not`). Each run writes `<stamp>.json` and
`<stamp>.md` with the success rate, median time per task and confirmations per task; a filtered run is
marked partial and never published. The README carries the latest full run between its eval markers.
