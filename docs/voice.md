# Voice

### Run

```bash
.venv\Scripts\python -m iris                      # type requests
.venv\Scripts\python -m iris --voice              # wake word mode, say "hey jarvis", it stops on silence
.venv\Scripts\python -m iris --push-to-talk       # old mode: press Enter to stop recording
```

`--voice` is the normal way to use Iris. It waits for the wake word, plays a short beep, records until 1.8 s of
silence (`IRIS_SILENCE`), transcribes and answers. For 8 seconds after an answer you can ask the next question without the wake word
(`IRIS_FOLLOW_UP` sets the window, 0 turns it off); when nobody speaks in that window, Iris waits for the wake word
again, so talk that is not meant for her is ignored. The microphone stays open the whole time, so saying the wake
word while Iris is still talking cuts the answer short and your next words are taken as the next
command, without saying the wake word twice.

### Overlay

With `overlay = true` in `~/.iris/config.toml` (or `IRIS_OVERLAY=1`) voice mode shows a small dark panel at the bottom
of the screen, above the taskbar, like Siri: a dot while Iris listens, what Whisper heard as `You: ...`, three dots
while she thinks and her answer while she speaks. It fades out 4 seconds after she is done; a long answer keeps its
last lines. It is off by default.

While Iris listens, thinks or speaks, a round cancel button (an X in a circle) sits at the right of the panel. A click
stops that request: the speech stops at once, the agent takes no further step (a model call already under way may
finish, but its result is dropped), the journal gets a `cancelled` entry, and Iris waits for the wake word again.

The panel never takes the keyboard focus, also not when you click its button, and has no taskbar button, so Iris keeps
typing into the window you were in. Clicks on the see-through corners go through to the window behind; the panel
itself catches them. It is left out of screenshots (Windows 10 2004 or later), so `look_at_screen` never sees it.
`list_windows` and `read_window` skip it too. It runs as its own process (`python -m iris.overlay`, tkinter only, no
extra packages) and writes errors to `~/.iris/overlay.log`; if it fails or is closed, voice mode carries on without it.

### Launcher and start at login

`iris shortcut` puts an `Iris.cmd` on your desktop: double-click it and voice mode starts in its own window; close the
window to stop Iris. `iris startup on` starts voice mode when you log in, in a minimized console window you can see and close;
`iris startup off` stops that. Let LM Studio start its local server at login too (its settings have an option for
it), or Iris has no model to talk to.

### Wake word

The default is the built-in openWakeWord model **hey jarvis**: it is tiny and runs on any CPU. Iris downloads it on
first use and does not ship it; openWakeWord's pre-trained models are licensed CC BY-NC-SA 4.0 (non-commercial)
because of their training data, so check that before using it commercially. To wake Iris by
her own name instead, set `IRIS_WAKEWORD=iris`: short utterances are then checked by Whisper `tiny` with "Iris" as a
hotword (about 0.6 s on a CPU, `IRIS_WAKE_WHISPER` picks another size), so "Iris" or "hey Iris" works without a
trained model, at the cost of transcribing every short sound in the room.

```bash
set IRIS_WAKEWORD=hey_jarvis    # any built-in openWakeWord name, a path to your own .onnx, or iris
```

`IRIS_WAKE_THRESHOLD` (default 0.5) decides how loud the wake word has to be. `IRIS_VAD_THRESHOLD`
(default 0.01) is the RMS below which audio counts as silence, and `IRIS_SILENCE` (default 1.8 s) is the silence
that ends a recording; a shorter one answers sooner but cuts you off when you pause to think.

### Hearing

Iris transcribes with faster-whisper. On an NVIDIA GPU it uses `large-v3-turbo` (well under a second per request and
much better with names and mixed Dutch and English); without one it uses `small` on the CPU. The GPU needs NVIDIA's
cuBLAS and cuDNN, which the `gpu` extra installs:

```bash
.venv\Scripts\python -m pip install -e ".[gpu]"
set IRIS_WHISPER_GPU=0              # which GPU, numbered as nvidia-smi shows them
```

`IRIS_WHISPER` and `IRIS_WHISPER_DEVICE` (`cpu` or `cuda`) override the automatic choice. Every request is printed as
`You: ...`, so you can see what Iris understood.

### Speech out

Windows SAPI (pyttsx3) is the default and always available, but its voices are English, so Dutch answers sound
garbled. Piper gives natural local voices in both languages: Iris picks the voice by the language of the answer, and
uses the language you spoke when an answer is too short to tell.

```bash
.venv\Scripts\python -m pip install -e ".[piper]"
.venv\Scripts\python -m piper.download_voices nl_NL-ronnie-medium en_US-joe-medium --data-dir %USERPROFILE%\.iris\piper
```

Then set `tts = "piper"` in `~/.iris/config.toml` (or `IRIS_TTS=piper`). `IRIS_PIPER_NL` and `IRIS_PIPER_EN` pick other
voices. Piper is GPL-3.0, so Iris never imports it: it runs Piper's own command line as a separate program and plays
the WAV it writes. Both default voices were trained on CC0 data; check a voice's model card before you pick another.

VoxCPM2 (Apache-2.0) speaks Dutch and English in one voice, also when they mix in one sentence ("The Shining staat op
Netflix"). It needs PyTorch and a GPU with about 6 GB free, so it gets its own Python next to Iris:

```bash
uv venv -p 3.11 %USERPROFILE%\.iris\voxcpm\venv
uv pip install --python %USERPROFILE%\.iris\voxcpm\venv voxcpm "triton-windows<3.3"
uv pip install --python %USERPROFILE%\.iris\voxcpm\venv --reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu124
```

Then set `tts = "voxcpm"`. The model (about 5 GB) is downloaded from Hugging Face on the first start, which takes a
few minutes; later starts take about half a minute. Iris keeps one voice in `~/.iris/voxcpm/voice.wav`: made on the
first start, or put any clean 5 to 10 second recording there to speak in that voice. `voxcpm_gpu` in config.toml (or
`IRIS_VOXCPM_GPU`) picks the GPU (default 0, in PCI bus order); give it a card with about 4 GB truly free, not the
one the LLM fills: next to a 27B LLM and the desktop's own apps a 3090 spilled into system memory and the LLM's prompt
processing fell from 1100 to 140 tokens a second.

The worker sizes VoxCPM2's attention caches for 512 positions (`IRIS_VOXCPM_CACHE`) instead of the model's 8192, which
it otherwise reads in full on every step, and speaks a long answer in pieces of up to 250 characters that fit them.
On an RTX 3060 that took speech from 0.58 to about 1.9 times real time, so an answer of any length starts after
about 0.3 s and plays in one go. On a GPU still slower than real time, Iris starts once enough is made that the rest
arrives in time (from the speed measured on each answer, times `IRIS_VOXCPM_MARGIN`, default 1.15), and if it runs
dry it pauses once instead of stuttering. The worker's log is `~/.iris/voxcpm/worker.log`; if VoxCPM fails, Piper
speaks instead.

For Kokoro (English only), install the extra and put the two model files in `~/.iris` (or `IRIS_KOKORO_DIR`):

```bash
uv pip install --python .venv -e ".[tts]"
# kokoro-v1.0.fp16.onnx and voices-v1.0.bin, from the kokoro-onnx release, into ~/.iris
set IRIS_TTS=kokoro
set IRIS_KOKORO_VOICE=af_heart
```

Iris never downloads model files by itself. If Piper or Kokoro is missing its files, or fails for any other reason,
Iris falls back to SAPI and carries on talking. Piper and Kokoro play in short blocks, so the wake word stops them
mid-answer; SAPI cannot be cut off mid-sentence.

### Web pages

For web pages in Brave, Chrome or Edge, Iris reads the page itself through the DevTools Protocol: `browser_read` lists
the buttons, links and fields on screen as numbered lines, and `browser_click` and `browser_type` act on a number and
return the page as it is afterwards. A read takes a few hundredths of a second, against several seconds for a
screenshot and a vision model, and the click lands on exactly the element that was read. The tier comes from the label
the page itself shows, so a "Bestellen en betalen" button is refused however the model describes it.

```bash
.venv\Scripts\python -m pip install -e ".[browser]"
```

Then allow it in the browser: type `brave://inspect/#remote-debugging` (or `chrome://`, `edge://`) in the address
bar and tick "Allow remote debugging for this browser instance". The browser asks every new connection whether it may
be controlled; voice mode connects as soon as it starts (and again after the browser restarts) and says when the
question is waiting, so you click Allow once per run, not in the middle of a request. Only the browser on this PC is reached (127.0.0.1). This gives Iris full control of that browser,
like a person at the keyboard: the tiers above are what keep paying, sending and passwords with you.
`IRIS_BROWSER_PROFILE` points at another browser's user-data folder.

`open_url` opens a page in a tab in front and returns it numbered, so the next step can click without reading
first; a site's own search URL (`youtube.com/results?search_query=...`) makes a search one step. A second page on the
same site reuses the tab Iris opened for it; tabs you opened yourself are never taken over.

`web_search` uses the same connection: it opens the search results in a background tab, reads the result links
and closes the tab again (search engines block plain HTTP clients). If a search engine asks for a human check, Iris
stops and says so; it never solves those. Without the browser it falls back to DuckDuckGo's html page.
