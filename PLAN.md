# Iris: a local voice agent that runs this PC

You talk, Iris does it. Everything runs on this machine: no cloud, no cost per command.

## Why it can work here

| Part | Runs on | Choice |
|---|---|---|
| Ears: speech to text | 3060 or CPU | faster-whisper |
| Brain: plan and pick tools | 3090 | Qwen 3.8 27B in LM Studio (OpenAI-compatible API, tool calling) |
| Hands: apps, clicks, typing | CPU | Windows UI Automation (the accessibility tree), screenshots only as a fallback |
| Mouth: text to speech | CPU | Windows SAPI by default, Kokoro (`kokoro-onnx`) behind `IRIS_TTS=kokoro`, streaming so the wake word can interrupt |
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

- **v0, core loop (done):** text or voice in → Qwen picks tools → tier check → confirmation → act through UI
  Automation → spoken answer. Tools: system status, list windows, read a window, open an app, click,
  type. Tests with a scripted fake LLM.
- **v1, hands (done):** focus/scroll/menus, keyboard shortcuts, file operations with undo, a wake
  word (openWakeWord), streaming speech. Shipped as `press_keys`, `focus_window`, `scroll`, `open_url`,
  `trash_file` and `restore_trash`, wake word plus energy VAD, and Kokoro with SAPI fallback and barge-in.
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
- [x] **7. README and PLAN:** every tool documented with its tier, the wake word and how to run it; v1 ticked.
- [x] **8. Read-only skills:** gpu_check (the GPU governor: free VRAM and temperature before a heavy job), comfy_status, lmstudio_models (never loads or unloads), followups from the leadscan CRM, journal_today.
- [x] **9. Eval harness (v3 start):** 50 tasks in eval/tasks.toml, dry-run tools, `python -m iris eval` with --only and --repeat, reports in eval/results, eval-compare, and an Eval tab on the dashboard.
- [x] **10. The prompt queue:** `queue_list` (safe), `queue_add` and `queue_take` (confirm) on a JSON queue (`IRIS_QUEUE`, default `~/.iris/queue.json`, saved atomically, a broken file is reported and never overwritten); `agents_status` ends with the queue line; four eval tasks (54 in total).
- [x] **11. Queue tab and concept sites:** the dashboard's Queue tab (`/api/queue`, read only) and `concept_sites` (safe), which reads conceptkit's `list` and `check --all`; two eval tasks (56 in total).
- [x] **12. Skills that act:** lmstudio_load and lmstudio_unload (confirm; unload refuses the model Iris runs on), comfy_workflows (safe, lists `IRIS_COMFY_WORKFLOWS`) and comfy_queue (confirm, checks free VRAM first); six eval tasks (62 in total).
- [x] **13. Eval categories and safety score:** every task has a category (read, hands, files, safety, skills, agents), reports show per-category pass rates plus a safety line (forbidden calls that succeeded, unknown tools, confirmations Jay never gave), repeats as pass@k with progress printed per task, `eval publish` writes the latest run into README.md between markers; results stay out of git and iris/eval.py is at 100% coverage.

Next: run the eval on the real model with --repeat 3 and publish; a custom "hey iris" wake word.

