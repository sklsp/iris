# Iris

A local voice agent that operates this Windows PC. You say what you want; Iris reads the screen through
Windows UI Automation, picks tools with a local LLM (Qwen in LM Studio), asks before it changes anything,
and answers out loud. No cloud, no cost per command. See [PLAN.md](PLAN.md) for where it is going.

## Run

```bash
.venv\Scripts\python -m iris                      # type requests
.venv\Scripts\python -m iris --voice              # wake word mode, say "hey jarvis", it stops on silence
.venv\Scripts\python -m iris --push-to-talk       # old mode: press Enter to stop recording
```

`--voice` is the normal way to use Iris. It waits for the wake word, records until 1.2 s of silence,
transcribes, answers, and waits again. The microphone stays open the whole time, so saying the wake
word while Iris is still talking cuts the answer short and your next words are taken as the next
command, without saying the wake word twice.

## Wake word

The default is the built-in openWakeWord model **hey jarvis**, because a custom "hey iris" model does
not exist yet.

```bash
set IRIS_WAKEWORD=hey_jarvis    # any built-in openWakeWord name, or a path to your own .onnx
```

`IRIS_WAKE_THRESHOLD` (default 0.5) decides how loud the wake word has to be. `IRIS_VAD_THRESHOLD`
(default 0.01) is the RMS below which audio counts as silence, and the 1.2 s of silence that ends a
recording is the `silence_seconds` default in `iris/voice.py`.

## Tools and risk tiers

| Tool | Tier | What it does |
|---|---|---|
| system_status | safe | CPU, RAM, and per-GPU load, VRAM and temperature |
| list_windows | safe | titles of the open windows |
| read_window | safe | the named controls in a window, with the contents of text fields |
| open_app | safe | start an app, an `ms-settings:` page or a file |
| open_url | safe | open an `http` or `https` URL; `file:`, `javascript:` and `data:` are refused |
| focus_window | safe | bring a window to the front and give it focus |
| scroll | safe | scroll up or down in a window |
| restore_trash | safe | put the most recently trashed item back |
| agents_status | safe | branch, last commit and age, uncommitted files, BLOCKED.md and STUCK per agent repo |
| press_keys | confirm | press shortcuts in a window, `^s` or `{TAB}` |
| click | confirm | click a control by its name |
| type_text | confirm | type into a window or a named field, optionally replacing what is there |
| trash_file | confirm | move a file or folder to Iris's trash, always undoable |

The tiers are enforced in code, not in the prompt:

- A `click` or a field whose name means sending, buying, paying, deleting or a password (English and
  Dutch) is refused even if you say yes.
- `press_keys` with `{ENTER}` in Outlook, Gmail, WhatsApp, Discord, Teams or Slack escalates to
  `never`, because Enter there sends the message. The same shortcut elsewhere stays `confirm`.
- `trash_file` never deletes permanently. Iris's trash holds the file plus a `.json` sidecar with the
  original path, and `restore_trash` puts it back. `IRIS_TRASH_DIR` moves the trash (default
  `~/.iris/trash`).

`trash_file` moves files to Iris's own trash dir instead of the Windows Recycle Bin. send2trash cannot
tell you the original path back out of the Recycle Bin, so `restore_trash` (which needs that path) would
not be reliable. One directory under `~/.iris`, plus a sidecar per item, makes undo exact.

Screen contents reach the model wrapped as untrusted data. Every request, tool call, confirmation and
result is appended to `~/.iris/journal.jsonl`.

## Speech out

Windows SAPI (pyttsx3) is the default and always available. For Kokoro, install the extra and put the
two model files in `~/.iris` (or `IRIS_KOKORO_DIR`):

```bash
uv pip install --python .venv -e ".[tts]"
# kokoro-v1.0.fp16.onnx and voices-v1.0.bin, from the kokoro-onnx release, into ~/.iris
set IRIS_TTS=kokoro
set IRIS_KOKORO_VOICE=af_heart
```

Iris never downloads model files by itself. If `IRIS_TTS=kokoro` but the files are missing, or Kokoro
fails for any other reason, it falls back to SAPI and carries on talking. Kokoro streams chunk by
chunk, so the wake word stops it between chunks; SAPI cannot be cut off mid-sentence.

## The agent manager

`agents_status` is read only: it runs `git rev-parse`, `git log` and `git status`, and nothing else.
The repos live in [iris/agents.toml](iris/agents.toml):

```toml
repos = ["W:/Ledger", "W:/feedbot", "W:/acme-render", "W:/Iris"]
worktrees = ["C:/Users/Jayde/.cline/worktrees"]
stuck_minutes = 60
```

Every git repo under a worktree root is included, so a new worktree shows up without editing the file.
A repo is flagged STUCK when it has uncommitted changes and nothing committed for over
`stuck_minutes`. Spoken, it sounds like:

> Acme Shop: last commit 12 minutes ago. Beacon: stuck for 2 hours with 1 uncommitted.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `IRIS_LLM_URL` | `http://127.0.0.1:1234/v1` | LM Studio endpoint |
| `IRIS_MODEL` | `qwen3.8-27b` | model name to request |
| `IRIS_WHISPER` | `small` | faster-whisper model |
| `IRIS_WHISPER_DEVICE` | `cpu` | `cuda` to transcribe on the GPU |
| `IRIS_WAKEWORD` | `hey_jarvis` | wake word model name or .onnx path |
| `IRIS_WAKE_THRESHOLD` | `0.5` | wake word score to trigger |
| `IRIS_VAD_THRESHOLD` | `0.01` | RMS below which audio is silence |
| `IRIS_TTS` | `sapi` | `kokoro` for the neural voice |
| `IRIS_KOKORO_DIR` | `~/.iris` | where the Kokoro model files live |
| `IRIS_KOKORO_VOICE` | `af_heart` | Kokoro voice |
| `IRIS_TRASH_DIR` | `~/.iris/trash` | Iris's undoable trash |
| `IRIS_AGENTS_TOML` | `iris/agents.toml` | which repos the agent manager watches |

## Status

Core loop works end to end against Qwen 3.8 27B. On this PC, a Dutch status question took 9 s, reading
Notepad took 5 s, and an edit request stopped at the confirmation. 62 tests cover the loop with a
scripted model, the wake word and VAD on synthetic audio, the SAPI and Kokoro speakers with fakes, the
window and URL tools with fake windows, and the agent manager against temporary git repos. Nothing in
the suite touches the real desktop, the microphone, the LLM or your repos.
