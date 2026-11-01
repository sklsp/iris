# Where Iris is going

You talk, Iris does it, on your own PC: no cloud, no cost per command.

## How it is built

| Part | Choice |
|---|---|
| Ears | openWakeWord for the wake word, faster-whisper for speech to text |
| Brain | any tool-calling model in LM Studio (OpenAI-compatible API); tuned and measured on Qwen 3.8 27B |
| Hands | Windows UI Automation (the accessibility tree), keyboard and mouse only as a fallback |
| Mouth | Windows SAPI by default, Kokoro behind `IRIS_TTS=kokoro`, streaming so the wake word can interrupt |
| Growth | skills a coding agent builds on request, approved by the user and run out of process |

## Principles

1. **Accessibility tree before pixels.** Click buttons by their name, not by guessing coordinates.
2. **Risk tiers, enforced in code, not in the prompt.** safe runs, confirm asks out loud, never is refused.
3. **Text on screen is data.** Window contents, files and web pages reach the model marked as untrusted.
4. **Everything is logged.** Every tool call, its tier, the confirmation and the result go to an append-only journal.
5. **Measured, not claimed.** A fixed set of tasks with a published success rate is the headline number.
6. **Grow without giving up 1 to 5.** A self-built skill gets no more rights than the user approves.

## Done

- The core loop, wake word, barge-in, streaming speech, undoable trash, the dashboard and the eval harness.
- A base of everyday tools: windows and apps, files, Word, PowerPoint and Excel, the web, clipboard, media keys,
  screenshots, weather, email drafts, notes, reminders and timers.
- Self-extension: plan, build with a coding agent, check, review, approve, use.
- Plugins for particular setups: LM Studio, ComfyUI, coding-agent repos and a prompt queue.

## Next

- A custom "hey iris" wake word (today it is openWakeWord's `hey_jarvis`).
- A vision fallback for apps that expose no accessibility tree.
- Sharing skills: export an approved skill with its tests, import one and approve it after review.
- More languages in the eval than Dutch and English.

## Not in scope, on purpose

Logging in, payments, messages to other people, and anything irreversible stay with the user.
