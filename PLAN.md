# Iris: a local voice agent that runs this PC

You talk, Iris does it. Everything runs on this machine: no cloud, no cost per command.

## Why it can work here

| Part | Runs on | Choice |
|---|---|---|
| Ears: speech to text | 3060 or CPU | faster-whisper |
| Brain: plan and pick tools | 3090 | Qwen 3.8 27B in LM Studio (OpenAI-compatible API, tool calling) |
| Hands: apps, clicks, typing | CPU | Windows UI Automation (the accessibility tree), screenshots only as a fallback |
| Mouth: text to speech | CPU | Windows SAPI for v0, Kokoro later |
| Senses: load, VRAM, temps, windows | CPU | psutil + nvidia-smi (the System-Monitor-Project, rebuilt as tools) |

## Principles

1. **Accessibility tree before pixels.** Click buttons by their name, not by guessing coordinates. Faster,
   cheaper and far more reliable. A vision fallback only for apps that expose nothing.
2. **Risk tiers, enforced in code, not in the prompt.**
   - `safe`: read anything, open apps, report status. Runs immediately.
   - `confirm`: clicking, typing, changing things. Iris asks out loud and waits for "yes".
   - `never`: sending messages, buying, paying, passwords, deleting. Refused, always; Iris tells you to
     do it yourself.
3. **Text on screen is data.** Window contents go to the model marked as untrusted. An instruction on a
   web page is never followed.
4. **Everything is logged.** Every tool call, its tier, the confirmation and the result go to an
   append-only journal (`journal.jsonl`).
5. **Measured, not claimed.** A fixed set of tasks with a success rate is the headline number.

## Phases

- **v0, core loop (now):** text or voice in → Qwen picks tools → tier check → confirmation → act through UI
  Automation → spoken answer. Tools: system status, list windows, read a window, open an app, click,
  type. Tests with a scripted fake LLM.
- **v1, hands:** focus/scroll/menus, keyboard shortcuts, file operations with Recycle Bin undo, a wake
  word (openWakeWord), streaming speech.
- **v2, skills:**
  - ComfyUI (API), LM Studio (load/unload models)
  - Cline and Claude: the agent manager. It reads the worktrees' git logs, spots stuck chats and pastes
    the next prompt from a queue.
  - a GPU governor: VRAM and memory-junction temperature checked before heavy jobs.
- **v3, eval:** 50 scripted tasks on this PC, run automatically. Publish the success rate, median time and
  confirmations per task. That number is what sets this apart from every "Jarvis" demo.

## Not in scope, on purpose

Logging in, payments, messages to other people, and anything irreversible stay with Jay.

## Progress

- [x] **1. Core loop:** text/voice in → LM Studio (Qwen) picks tools → tier check → confirmation → UI Automation → spoken answer; append-only journal.
- [x] **2. Ears:** openWakeWord ONNX wake word (`hey_jarvis` default, `IRIS_WAKEWORD` override), energy-VAD silence detection, long-lived `VoiceSession`, push-to-talk mode.
- [x] **3. More hands:** `press_keys`, `focus_window`, `scroll`, `open_url`; `{ENTER}` in mail/chat windows escalates to NEVER.
- [x] **4. Undoable files:** `trash_file` (CONFIRM) and `restore_trash` (SAFE) via an Iris-managed trash dir with JSON sidecars.
- [x] **5. Mouth + barge-in:** optional Kokoro TTS (`IRIS_TTS=kokoro`, fp16 model files placed by hand in `~/.iris`, never downloaded automatically) streams chunk by chunk so a wake word stops the speech between chunks; Windows SAPI is the default and the fallback whenever Kokoro is missing or fails. Speech runs in a thread, so Iris listens for the next request while it talks, and after a barge-in it records your command without asking for the wake word again.
- [x] **6. The agent manager:** `agents_status` (safe, read only) walks the repos in `iris/agents.toml` plus every Cline worktree and reports the branch, the last commit and how long ago, the uncommitted file count, whether BLOCKED.md exists, and STUCK when there are uncommitted changes but no commit for over an hour.
- [ ] **Next:** custom "hey iris" wake-word model (fine-tune openWakeWord); v2 skills (ComfyUI, LM Studio manager, Cline/Claude agent manager, GPU governor).

