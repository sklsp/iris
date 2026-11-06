"""Ears and mouth. Wake word (openWakeWord) + energy-VAD recording, faster-whisper to transcribe,
Windows SAPI or Kokoro to speak."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from iris import config
from iris.overlay import Overlay, launch

if TYPE_CHECKING:
    import numpy as np

def whisper_model() -> str | None:
    """The Whisper model from config.toml, read at call time so a change on the Settings tab needs no restart."""
    return config.setting("IRIS_WHISPER", "whisper_model") or None


# None means automatic: large-v3-turbo on the GPU when CUDA works (pip install -e .[gpu]), small on the CPU otherwise.
WHISPER_MODEL = whisper_model()
WHISPER_DEVICE = os.environ.get("IRIS_WHISPER_DEVICE")  # "cpu" or "cuda" to force one
RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz, the frame size openWakeWord wants

# IRIS_WAKEWORD picks a built-in openWakeWord name, an .onnx file, or "iris" for NameWake (no model needed).
# Read at construction time so tests can override it.
WAKE_THRESHOLD = config.number("IRIS_WAKE_THRESHOLD", "wake_threshold", 0.5)
# Stop recording after this much continuous silence (IRIS_SILENCE). 1.2 s cut people off when they paused to think.
SILENCE_SECONDS = config.number("IRIS_SILENCE", "silence", 1.8)
SPEECH_TIMEOUT = 60.0  # the longest one answer may take to say before Iris carries on without it
FOLLOW_UP_SECONDS = 8.0  # after an answer, a question that starts this soon needs no wake word (IRIS_FOLLOW_UP)
VAD_THRESHOLD = float(os.environ.get("IRIS_VAD_THRESHOLD", 0.01))  # RMS below this counts as silence

# Kokoro TTS (optional, IRIS_TTS=kokoro). fp16: 0.999 spectral correlation with the 326 MB fp32 model
# for half the download. Nothing is downloaded automatically: drop the two files in ~/.iris by hand
# and Iris uses them, otherwise it keeps speaking with Windows SAPI.
KOKORO_DIR = os.environ.get("IRIS_KOKORO_DIR", str(Path.home() / ".iris"))
KOKORO_MODEL = os.path.join(KOKORO_DIR, "kokoro-v1.0.fp16.onnx")
KOKORO_VOICES = os.path.join(KOKORO_DIR, "voices-v1.0.bin")
KOKORO_VOICE = os.environ.get("IRIS_KOKORO_VOICE", "af_heart")
_KOKORO_FILES = {
    "kokoro-v1.0.fp16.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.fp16.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
}

# Piper (IRIS_TTS=piper): natural local voices, one per language. Piper is GPL, so Iris never imports it: it runs
# Piper's own command line in a separate process. Get a voice with
#   python -m piper.download_voices nl_NL-ronnie-medium --data-dir %USERPROFILE%/.iris/piper
PIPER_DIR = Path(os.environ.get("IRIS_PIPER_DIR") or (Path.home() / ".iris" / "piper"))
def piper_voices() -> dict[str, str]:
    """The Piper voices from config.toml, read at call time so a change on the Settings tab needs no restart."""
    return {"nl": config.setting("IRIS_PIPER_NL", "piper_nl", "nl_NL-ronnie-medium"),
            "en": config.setting("IRIS_PIPER_EN", "piper_en", "en_US-joe-medium")}


# The default voices for each language; config.toml (piper_nl / piper_en) can point to any other voice
# from the same list.
PIPER_VOICES = piper_voices()


def piper_runtime() -> Path:
    """Where the app's Piper pack installs Piper (GPL-3.0), so it stays a separate program next to Iris."""
    return config.config_path().parent / "piper" / "runtime"


# VoxCPM2 (tts = "voxcpm"): one natural voice for Dutch and English, also when they mix in one sentence. It needs
# torch, so it runs in its own Python through iris/voxcpm_worker.py; the README's Speech section says how to set it up.
VOXCPM_DIR = Path(os.environ.get("IRIS_VOXCPM_DIR") or (Path.home() / ".iris" / "voxcpm"))
VOXCPM_PYTHON = Path(os.environ.get("IRIS_VOXCPM_PYTHON") or (VOXCPM_DIR / "venv" / "Scripts" / "python.exe"))
# A GPU too slow for real time (an RTX 3060 makes speech at 0.56 times real time) still plays without gaps when enough
# is made before it starts: _lead_needed works that out from the speed measured on the answer itself, times this margin.
VOXCPM_MARGIN = float(os.environ.get("IRIS_VOXCPM_MARGIN", "1.15"))
CHARS_PER_SECOND = 17  # how fast VoxCPM2 speaks (measured 16 to 20), to guess an answer's length before it is made
REBUFFER_SECONDS = 1.0  # when the speech still runs dry: one clean pause until this much is ready, not a stutter

# A wake phrase at the start of a request ("Hey Jarvis, ...") is not part of it.
_WAKE_PHRASE = re.compile(r"^\s*(?:(?:hey|hé|hee|hoi|hi|ok|okay)[\s,!.]*)?(?:jarvis|iris)\b[\s,!.?]*", re.I)


def _strip_wake(text: str) -> str:
    return _WAKE_PHRASE.sub("", text).strip()


_YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|ja|jazeker|doe maar|ga je gang|prima|oké|oke|okay|"
                  r"genau|klar|mach das|oui|d'accord|vas-y|allez-y|sí|si|claro|vale|adelante|hazlo)\b", re.I)
# Any no-word wins over a yes-word, so a mixed or unclear answer is a "no".
_NO = re.compile(r"\b(no|nee|niet|stop|cancel|nein|nicht|non|pas|annuler|abbrechen|cancelar)\b", re.I)


def is_yes(text: str | None) -> bool:
    """Whether a spoken answer counts as yes: any no-word wins over a yes-word."""
    if text is None:
        return False  # no answer at all is a "no"
    return bool(_YES.search(text)) and not _NO.search(text)


class SilenceDetector:
    """Energy VAD. Feed it mono float32 frames; it says stop once `silence_seconds` of continuous
    silence has followed some speech. Pure numpy logic so tests can run on synthetic audio."""

    def __init__(self, sr: int = RATE, silence_seconds: float = SILENCE_SECONDS, threshold: float | None = None):
        self.sr = sr
        self.silence_samples = int(sr * silence_seconds)
        self.threshold = VAD_THRESHOLD if threshold is None else float(threshold)
        self._silent_run = 0
        self._voiced_seen = False

    def feed(self, frame: np.ndarray) -> bool:
        """Feed one frame of audio; True once some speech has been followed by the full silence run."""
        import numpy as np

        rms = float(np.sqrt(np.mean(frame.astype("float64") ** 2))) if len(frame) else 0.0
        if rms > self.threshold:
            self._voiced_seen = True
            self._silent_run = 0
        else:
            self._silent_run += len(frame)
        return self._voiced_seen and self._silent_run >= self.silence_samples


class WakeWord:
    """openWakeWord wrapper. The model loads lazily on first use (it downloads itself the first time).

    tflite-runtime has no Windows wheels, so we always load the ONNX variant of the model; openwakeword
    switches to onnxruntime automatically when given a .onnx path."""

    def __init__(self, name: str | None = None, threshold: float = WAKE_THRESHOLD):
        self.name = name or config.setting("IRIS_WAKEWORD", "wakeword", "hey_jarvis")
        self.threshold = threshold
        self._m = None

    def _model(self) -> Any:
        if self._m is not None:
            return self._m
        import openwakeword

        path = self.name
        if self.name in openwakeword.MODELS:
            path = openwakeword.MODELS[self.name]["model_path"]
        onnx = path.replace(".tflite", ".onnx")
        if not os.path.exists(onnx):
            if self.name not in openwakeword.MODELS:  # your own model file: download_models([]) would fetch them all
                raise FileNotFoundError(f"no wake word model at {onnx}; check wakeword in config.toml")
            print(f"Downloading wake word model {self.name} (first run only)...")
            openwakeword.utils.download_models([self.name])
        self._m = openwakeword.Model(wakeword_models=[onnx])
        return self._m

    def detect(self, frame: np.ndarray) -> bool:
        """Whether this frame contains the wake word; a hit resets the model so one shout does not fire twice."""
        import numpy as np

        if frame.dtype != np.int16:  # openWakeWord wants 16-bit PCM; float samples in [-1, 1] score as silence
            frame = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16)
        model = self._model()
        scores = model.predict(frame) or {}
        if max(scores.values(), default=0.0) < self.threshold:
            return False
        # one "hey jarvis" scores high for several frames; without a reset it fires again after the request
        reset = getattr(model, "reset", None)
        if callable(reset):
            reset()
        return True


def _cuda_dlls() -> None:
    """The nvidia-* wheels ship cuBLAS and cuDNN as DLLs that Windows does not find on its own: from the gpu extra,
    or from the app's GPU pack in ~/.iris/gpu (iris.packs)."""
    import sysconfig

    for root in (Path(sysconfig.get_paths()["purelib"]) / "nvidia", config.config_path().parent / "gpu" / "nvidia"):
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            d = root / sub / "bin"
            if d.is_dir() and str(d) not in os.environ.get("PATH", ""):
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _cuda_available() -> bool:
    """True when faster-whisper can run on a GPU: a CUDA device plus loadable cuBLAS 12 and cuDNN 9."""
    import ctypes

    os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")  # GPU numbers as nvidia-smi shows them
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() == 0:
            return False
        _cuda_dlls()
        for dll in ("cublas64_12.dll", "cudnn64_9.dll"):
            ctypes.WinDLL(dll)  # type: ignore[attr-defined]
        return True
    except (ImportError, OSError, AttributeError, RuntimeError):
        return False


def _whisper_model(name: str | None, device: str | None, gpu_default: str = "large-v3-turbo",
                   cpu_default: str = "small") -> Any:
    """A faster-whisper model: on the GPU (float16, IRIS_WHISPER_GPU picks which) when CUDA works, else int8 on the
    CPU. An explicit name or device wins."""
    from faster_whisper import WhisperModel

    device = device or ("cuda" if _cuda_available() else "cpu")
    if device == "cuda":
        _cuda_dlls()
    model = name or (gpu_default if device == "cuda" else cpu_default)
    compute = "float16" if device == "cuda" else "int8"
    gpu = config.setting("IRIS_WHISPER_GPU", "whisper_gpu")
    extra = {"device_index": int(gpu)} if device == "cuda" and gpu.isdigit() else {}
    return WhisperModel(model, device=device, compute_type=compute, **extra)


class NameWake:
    """Wake on the spoken name "Iris" without a trained model: an energy VAD cuts short utterances (at most
    `max_seconds`) out of the mic stream, and a small Whisper model (IRIS_WAKE_WHISPER, default tiny, with "Iris" as
    a hotword: about 0.6 s on a CPU) checks whether they say the name. Same detect(frame) interface as WakeWord.

    ponytail: every short utterance in the room is transcribed; a trained openWakeWord model is cheaper on CPU."""

    NAME = re.compile(r"\b(iris|irıs|iriss)\b", re.I)

    def __init__(self, transcribe: Callable[[np.ndarray], str] | None = None, threshold: float | None = None,
                 max_seconds: float = 3.0, silence_seconds: float = 0.4):
        self.transcribe = transcribe or self._tiny
        self._whisper: Any = None
        self.threshold = VAD_THRESHOLD if threshold is None else float(threshold)
        self.max_frames = int(max_seconds * RATE / FRAME)
        self.silence_seconds = silence_seconds
        self._chunks: list[np.ndarray] = []
        self._det: SilenceDetector | None = None
        self._before: list[np.ndarray] = []
        self._long = False

    def detect(self, frame: np.ndarray) -> bool:
        """Whether the user just said Iris's name: record until silence, transcribe, and look for it."""
        import numpy as np

        if self._long:  # the rest of a long sentence: wait for its silence, then listen again
            assert self._det is not None
            self._long = not self._det.feed(frame)
            return False
        if not self._chunks:
            rms = float(np.sqrt(np.mean(frame.astype("float64") ** 2))) if len(frame) else 0.0
            if rms <= self.threshold:
                self._before = [*self._before[-2:], frame]  # 240 ms of lead-in, so the first sound is not cut
                return False  # still quiet: nothing to listen to
            self._det = SilenceDetector(silence_seconds=self.silence_seconds, threshold=self.threshold)
            self._chunks, self._before = self._before, []
        assert self._det is not None
        self._chunks.append(frame)
        ended = self._det.feed(frame)
        if not ended and len(self._chunks) < self.max_frames:
            return False
        audio, self._chunks = np.concatenate(self._chunks), []
        if not ended:
            self._long = True  # a long sentence, not a wake word
            return False
        return bool(self.NAME.search(self.transcribe(audio)))

    def _tiny(self, audio: np.ndarray) -> str:

        if self._whisper is None:
            size = os.environ.get("IRIS_WAKE_WHISPER", "tiny")
            self._whisper = _whisper_model(size, WHISPER_DEVICE, gpu_default=size, cpu_default=size)
        segments, _ = self._whisper.transcribe(audio, hotwords="Iris")
        return " ".join(s.text.strip() for s in segments).strip()


def make_wake(name: str | None = None, vad_threshold: float | None = None) -> WakeWord | NameWake:
    """"iris" or "hey_iris" wakes on the name through Whisper; anything else is an openWakeWord model."""
    name = name or config.setting("IRIS_WAKEWORD", "wakeword", "hey_jarvis")
    return NameWake(threshold=vad_threshold) if name.lower() in ("iris", "hey_iris") else WakeWord(name)


class Voice:
    """Push-to-talk mode: press Enter to stop recording."""

    def __init__(self) -> None:

        self.model = _whisper_model(WHISPER_MODEL, WHISPER_DEVICE)

    def listen(self, prompt: str = "Listening... press Enter when done.") -> str:
        """Record until Enter is pressed and return what Whisper heard, or an empty string if nothing was."""
        import numpy as np
        import sounddevice as sd

        chunks, stop = [], threading.Event()
        no_console = False  # input() died (no stdin): there is no console to press Enter in

        def cb(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
            chunks.append(indata.copy())

        print(prompt)

        def _stop_on_enter() -> None:
            nonlocal no_console
            try:
                input()
            except (EOFError, OSError):  # the desktop app and pipes have no stdin; Enter can never come
                no_console = True
            stop.set()

        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb):
            threading.Thread(target=_stop_on_enter, daemon=True).start()
            stop.wait()
        if no_console:
            print("Push-to-talk needs a console window; use the wake word instead.")
            return ""
        if not chunks:
            return ""
        audio = np.concatenate(chunks)[:, 0]
        segments, _ = self.model.transcribe(audio, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        print(f"You: {text or '(nothing heard)'}")
        return text


class VoiceSession:
    """Mic + wake word + VAD in one long-lived session.

    The input stream stays open the whole time, so a new wake word can interrupt speech even while
    the agent is thinking or talking (speak() returns True when that happens). The overlay's cancel button calls
    cancel(): it stops the speech, sets `cancelled` (the agent checks it between steps) and ends the request."""

    def __init__(self, whisper_model: str | None = None, wake_word: str | None = None,
                 vad_threshold: float | None = None, silence_seconds: float = SILENCE_SECONDS,
                 max_record_seconds: float = 30, speaker: Speaker | None = None):
        self.whisper_name = whisper_model or WHISPER_MODEL
        self.wake = make_wake(wake_word, vad_threshold)
        self.vad_threshold = vad_threshold
        self.silence_seconds = silence_seconds
        self.max_record_seconds = max_record_seconds
        self.speaker = speaker or Speaker()
        self._frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=1024)  # ~80 s, drops oldest when full
        self._stream: Any = None  # sounddevice.InputStream; opened in __enter__ (no type stubs)
        self._whisper: Any = None  # faster_whisper.WhisperModel; loaded lazily (no type stubs)
        self._speech: threading.Thread | None = None
        self._interrupted = False
        self._wake_seen = False  # a wake word landed while Iris was talking (barge-in)
        self._answered = False  # Iris spoke since the last request: a follow-up may come without the wake word
        self._stop: threading.Event | None = None  # stops the speech that is playing now
        self._speak_lock = threading.Lock()  # timers, reminders and the skill watcher speak from their own threads
        self._listening = threading.Event()  # next_request reads the frames now: a speech must not watch them too
        self.cancelled = threading.Event()  # this request was cancelled; cleared when the next one starts
        self.overlay = Overlay()  # sends nothing until __enter__ starts the overlay (opt-in, see iris.overlay)

    def __enter__(self) -> VoiceSession:
        import sounddevice as sd

        def cb(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
            frame = indata[:, 0].copy()
            try:
                self._frames.put_nowait(frame)
            except queue.Full:
                try:
                    self._frames.get_nowait()  # drop the oldest so memory stays bounded
                except queue.Empty:
                    pass
                try:
                    self._frames.put_nowait(frame)
                except queue.Full:
                    pass

        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME, callback=cb)
            self._stream.__enter__()
        except Exception as exc:  # noqa: BLE001 - PortAudioError and friends: one line, not a traceback
            self._stream = None
            raise SystemExit(f"No microphone could be opened ({exc}). Plug one in, or pick the default input "
                             "device in Windows Sound settings, then start voice mode again.") from exc
        self.overlay = launch()
        self.overlay.on_cancel = self.cancel
        return self

    def __exit__(self, *exc: object) -> None:
        self.overlay.close()
        close = getattr(self.speaker, "close", None)
        if callable(close):
            close()  # the VoxCPM2 worker and Piper: they hold the GPU and would outlive a voice mode run in a terminal
        if self._stream is not None:
            try:
                self._stream.__exit__(*exc)
            except Exception:  # noqa: BLE001 - closing the mic must never mask a real error
                pass

    MIC_SILENT_SECONDS = 10.0  # no frame at all for this long: the microphone stopped (unplugged, taken away)

    def _frame(self) -> np.ndarray:
        """The next 80 ms of sound. Waits in short steps, so Ctrl+C and the app's stop get in, and ends voice mode with
        one sentence when the microphone sends nothing at all any more."""
        waited = 0.0
        while True:
            try:
                return self._frames.get(timeout=0.25)
            except queue.Empty:
                waited += 0.25
                if waited >= self.MIC_SILENT_SECONDS:
                    raise SystemExit("The microphone stopped sending sound (unplugged, or taken by another app). "
                                     "Plug it back in, then start voice mode again.") from None

    def _drain(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    def wait_for_speech(self) -> bool:
        """Block until any running speech (and its watcher) is done. Returns True if it was interrupted."""
        if self._speech is not None:
            self._speech.join(SPEECH_TIMEOUT)  # bounded: a stuck speech thread must never freeze Iris
            if self._speech.is_alive():
                print("(speech got stuck; carrying on without it)")
                stop = self._stop
                if stop is not None:
                    stop.set()  # the stuck speech keeps playing otherwise and talks over the next answer
                self._speech = None
        return self._interrupted

    def cancel(self) -> None:
        """Stop this request (the overlay's cancel button, from its reader thread): the speech stops as for a wake
        word, the agent stops before its next step, and Iris waits for the wake word again."""
        print("Cancelled.")
        self.cancelled.set()
        self._answered = False  # no follow-up window after a cancel
        stop = self._stop
        if stop is not None:
            stop.set()
        self.overlay.send("idle")

    def next_request(self) -> str:
        """Block until the user says the wake word and then an utterance; returns the transcript."""
        self.wait_for_speech()  # never race the wake watcher for frames
        self._listening.set()
        try:
            return self._next_request()
        finally:
            self._listening.clear()

    def _next_request(self) -> str:
        self.cancelled.clear()  # a new request; a cancel of the last one is done with
        if self._wake_seen:  # barge-in: the user said the wake word while Iris was talking
            self._wake_seen = False
            self.chime()
            return self.next_command()
        if self._answered:
            self._answered = False
            text = self.follow_up()
            if text:
                return text
        print("Say the wake word...")
        while True:  # phase 1: wait for the wake word
            if self.wake.detect(self._frame()):
                break
        stop = self._stop  # a timer or reminder that is talking now: the wake word stops it, as during an answer
        if stop is not None:
            stop.set()
        self.cancelled.clear()  # a click that came while Iris waited for the wake word cancels nothing
        self.chime()
        return self.next_command()

    def chime(self) -> None:
        """A short tone right after the wake word, so the user hears that Iris is listening (IRIS_CHIME=0 mutes
        it). It finishes before recording starts, so it never ends up in the request; a stalled sound device can
        hold it for at most 1.0 s."""
        if os.environ.get("IRIS_CHIME", "1") == "0":
            return
        try:
            import numpy as np
            import sounddevice as sd

            t = np.arange(int(0.12 * 22050)) / 22050
            tone = (0.4 * np.sin(2 * np.pi * 880 * t) * np.minimum(1, (0.12 - t) * 40)).astype("float32")
            sd.play(tone, 22050)
            waiter = threading.Thread(target=sd.wait, daemon=True)
            waiter.start()
            waiter.join(1.0)
            if waiter.is_alive():
                sd.stop()  # the device stalled: never let a chime hold listening
        except Exception:  # noqa: BLE001 - the chime is a nicety; listening goes on without it
            pass

    def follow_up(self) -> str | None:
        """Right after an answer: if the user starts talking within IRIS_FOLLOW_UP seconds (default 8), record and
        transcribe that as the next request, no wake word needed. None when nobody spoke, so talk that is not meant
        for Iris only reaches her within that short window."""
        import numpy as np

        window = config.number("IRIS_FOLLOW_UP", "follow_up", FOLLOW_UP_SECONDS)
        if window <= 0:
            return None
        print(f"Listening for a follow-up ({window:g} s, no wake word needed)...")
        self._drain()  # the tail of Iris's own voice is not a question
        threshold = VAD_THRESHOLD if self.vad_threshold is None else self.vad_threshold
        recent: list[np.ndarray] = []
        loud = 0
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            try:
                frame = self._frames.get(timeout=0.1)
            except queue.Empty:
                continue
            recent = [*recent[-3:], frame]  # a little lead-in, so the first word is not cut
            rms = float(np.sqrt(np.mean(frame.astype("float64") ** 2))) if len(frame) else 0.0
            loud = loud + 1 if rms > threshold else 0
            if loud >= 3:  # 240 ms of sound: speech, not a click or a cough
                self.overlay.send("listening")
                return self._record(recent)
        return None

    def next_command(self) -> str:
        """The wake word was already heard (barge-in); record and transcribe what follows."""
        if self.cancelled.is_set():  # a confirm question the user cancelled: no answer to wait for
            return ""
        print("Listening...")
        self.overlay.send("listening")
        self._drain()  # drop the wake word itself so it does not end up in the transcript
        return self._record([])

    def _record(self, chunks: list) -> str:
        """Record until 1.2 s of silence follows speech (or a hard cap), then transcribe without the wake phrase."""
        import numpy as np

        # once someone talks, half the speech threshold still counts as talking: soft words keep the recording going
        quiet = (VAD_THRESHOLD if self.vad_threshold is None else self.vad_threshold) / 2
        det = SilenceDetector(threshold=quiet, silence_seconds=self.silence_seconds)
        for frame in chunks:
            det.feed(frame)
        t0 = time.time()
        while True:
            frame = self._frame()  # every 80 ms, so a cancel ends the recording at once
            if self.cancelled.is_set():
                return ""  # the overlay already went idle; nothing to transcribe
            chunks.append(frame)
            if det.feed(frame):
                break
            if time.time() - t0 > self.max_record_seconds:
                break
        text = _strip_wake(self._transcribe(np.concatenate(chunks)))
        if self.cancelled.is_set():  # cancelled while Whisper was busy
            return ""
        print(f"You: {text or '(nothing heard)'}")  # so the user sees what Iris understood
        self.overlay.send("heard", text)
        if not text:
            self.overlay.send("idle")  # nothing to answer, so the panel can go
        heard = getattr(self.speaker, "heard", None)
        if callable(heard):
            heard(text)
        return text

    def speak(self, text: str) -> bool:
        """Speak with barge-in and block until done. Returns True if a wake word cut it short."""
        self.speak_async(text)
        return self.wait_for_speech()

    def speak_async(self, text: str) -> threading.Thread:
        """Speak in a background thread, so the agent can listen for the next request while Iris talks.

        A wake word during the speech stops it; the next next_request() then records straight away
        instead of waiting for a new wake word."""
        with self._speak_lock:  # one speech at a time, whichever thread asks
            self.wait_for_speech()
            self._interrupted = False
            self._answered = True
            thread = threading.Thread(target=self._speak_and_watch, args=(text,), daemon=True)
            self._speech = thread
            thread.start()
        return thread

    def _speak_and_watch(self, text: str) -> None:
        self.overlay.send("answer", text)  # before the watcher starts, so an interruption always comes after it
        stop = self._stop = threading.Event()
        # while next_request listens (a timer speaking from its own thread), it owns the frames and hears the wake word
        watch = not self._listening.is_set()
        watcher = threading.Thread(target=self._watch_for_wake, args=(stop,), daemon=True)
        if watch:
            watcher.start()
        try:
            finished = self.speaker.speak(text, stop_event=stop)
        finally:
            stop.set()  # release the watcher whether or not it was interrupted
        if watch:
            watcher.join(timeout=1.0)
        self._interrupted = not finished
        if finished:  # not when a wake word or a cancel cut it short: the panel moved on
            self.overlay.send("idle")

    def _watch_for_wake(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                frame = self._frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.wake.detect(frame):
                print("Interrupted.")
                self.overlay.send("interrupted")
                self._wake_seen = True
                stop.set()

    def _transcribe(self, audio: np.ndarray) -> str:

        if self._whisper is None:
            self._whisper = _whisper_model(self.whisper_name, WHISPER_DEVICE)
        segments, _ = self._whisper.transcribe(audio, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def _stop_process(proc: subprocess.Popen, seconds: float) -> None:
    """End a speech worker: close its input (it then quits on its own), and terminate or kill it if it does not."""
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(seconds)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(seconds)
        except subprocess.TimeoutExpired:
            proc.kill()


class PiperProcess:
    """One running `python -m piper` for one voice. It loads the model once and writes a WAV file per line of text
    into its own temp folder; wav() waits until that file is complete."""

    def __init__(self, model: Path):
        self.dir = Path(tempfile.mkdtemp(prefix="iris-piper-"))
        runtime = piper_runtime()  # the app's Piper pack keeps Piper in its own folder, outside Iris's packages
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}  # UTF-8 stdin in every locale
        if runtime.is_dir():
            env["PYTHONPATH"] = str(runtime) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.proc = subprocess.Popen([sys.executable, "-m", "piper", "-m", str(model), "-d", str(self.dir)],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", env=env)

    def close(self) -> None:
        """Stop Piper and remove its temp folder."""
        _stop_process(self.proc, 2)
        shutil.rmtree(self.dir, ignore_errors=True)

    def wav(self, text: str, timeout: float = 20) -> Path:
        """Send one line of text to Piper and wait for the WAV file it writes, claiming it once complete."""
        before = set(self.dir.glob("*.wav"))
        assert self.proc.stdin is not None
        self.proc.stdin.write(" ".join(text.split()) + "\n")  # one line is one WAV; Piper splits the sentences
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("piper stopped")
            for path in set(self.dir.glob("*.wav")) - before:
                mine = _claim(path, self.dir / "done")
                if mine is not None:
                    return mine
            time.sleep(0.05)
        raise TimeoutError("piper took too long")


def _claim(path: Path, done: Path) -> Path | None:
    """Move a WAV Piper has finished into `done`; None while Piper still writes it. Windows refuses to move a file
    another process has open, which is exactly the signal: the header alone is not, it is written per sentence."""
    done.mkdir(exist_ok=True)
    target = done / path.name
    try:
        os.replace(path, target)
    except OSError:
        return None
    try:
        with wave.open(str(target), "rb") as w:
            return target if w.getnframes() > 0 else None
    except (OSError, EOFError, wave.Error):
        return None


class VoxProcess:
    """The running VoxCPM2 worker. stream(text) yields float32 chunks while the worker is still making the rest."""

    FAILED = 0xFFFFFFFF

    def __init__(self, python: Path | None = None, voice: Path | None = None, startup: float = 300,
                 worker: Path | None = None):
        python = python or VOXCPM_PYTHON
        if not python.exists():
            raise FileNotFoundError(f"no VoxCPM Python at {python}; see Speech in the README to install it")
        VOXCPM_DIR.mkdir(parents=True, exist_ok=True)
        # voxcpm_gpu = 1 in config.toml: a faster card that also holds the LLM (it fits with a 32k context on 24 GB)
        gpu = os.environ.get("IRIS_VOXCPM_GPU") or str(config.raw().get("voxcpm_gpu", 0))
        env = {**os.environ, "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "TQDM_DISABLE": "1", "CUDA_VISIBLE_DEVICES": gpu}
        worker = worker or Path(__file__).with_name("voxcpm_worker.py")
        with (VOXCPM_DIR / "worker.log").open("ab") as log:  # the child keeps its own handle
            self.proc = subprocess.Popen([str(python), str(worker), str(voice or VOXCPM_DIR / "voice.wav")],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, env=env)
        self.rate = 0
        self.startup = startup  # loading and compiling the model takes 30 s to a few minutes the first time
        self.ready = threading.Event()
        self._frames: queue.Queue = queue.Queue()
        self._behind = 0  # answers cut short whose rest is still coming back
        threading.Thread(target=self._read, daemon=True).start()

    def close(self) -> None:
        """Stop the worker, so its GPU memory is freed; one that is still loading its model is ended too."""
        _stop_process(self.proc, 5)

    def _read(self) -> None:
        import numpy as np

        assert self.proc.stdout is not None
        out = self.proc.stdout
        try:
            self.rate = struct.unpack("<I", _exact(out, 4))[0]
            self.ready.set()
            while True:
                n = struct.unpack("<I", _exact(out, 4))[0]
                self._frames.put(n if n in (0, self.FAILED) else np.frombuffer(_exact(out, 4 * n), dtype="<f4"))
        except EOFError:
            self._frames.put(EOFError)
            self.ready.set()  # so a waiting stream() sees the stop at once

    def _next(self, timeout: float) -> Any:
        try:
            item = self._frames.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("VoxCPM took too long") from None
        if item is EOFError:
            self._frames.put(EOFError)
            raise RuntimeError(f"VoxCPM stopped; see {VOXCPM_DIR / 'worker.log'}")
        return item

    def stream(self, text: str, timeout: float = 30) -> Iterator[np.ndarray]:
        """Send the text to the worker and yield its audio chunks as they are made, until the answer is done."""
        if not self.ready.wait(self.startup):
            raise TimeoutError("VoxCPM did not start")
        while self._behind:  # the rest of an answer that a wake word cut short
            if isinstance(self._next(timeout), int):
                self._behind -= 1
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps({"text": " ".join(text.split())}) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        self._behind += 1
        while not isinstance(item := self._next(timeout), int):
            yield item
        self._behind -= 1
        if item == self.FAILED:
            raise RuntimeError(f"VoxCPM failed on this answer; see {VOXCPM_DIR / 'worker.log'}")


def _exact(f: IO[bytes], n: int) -> bytes:
    data = b""
    while len(data) < n:
        part = f.read(n - len(data))
        if not part:
            raise EOFError
        data += part
    return data


def _lead_needed(expected: float, speed: float, margin: float = VOXCPM_MARGIN) -> float:
    """Seconds of speech to have made before playing an answer of about `expected` seconds, made at `speed` times real
    time, so playback never catches up with the making: the rest, (expected - lead) / speed seconds of work, must not
    take longer than playing all of it. A real-time GPU needs only a moment."""
    if speed >= 1:
        return 0.3
    return expected * (1 - speed) * margin


def _play_stream(chunks: Iterable[np.ndarray], rate: int, expected: float, stop_event: threading.Event | None,
                 slack: float = 3.0) -> bool:
    """Play streamed float32 audio of about `expected` seconds once enough is made that the rest arrives in time (see
    _lead_needed). If it still runs dry, it pauses until REBUFFER_SECONDS are ready instead of stuttering. Like
    _play_wav it stops for a wake word and never hangs on a stalled sound device."""
    import numpy as np
    import sounddevice as sd

    pending: queue.Queue = queue.Queue()
    left = np.zeros(0, dtype="float32")
    waiting, finished = False, threading.Event()

    def feed(outdata: np.ndarray, frames: int, _time: Any, _status: Any) -> None:
        nonlocal left, waiting
        while not pending.empty():
            left = np.concatenate([left, pending.get_nowait()])
        if waiting and (len(left) >= REBUFFER_SECONDS * rate or finished.is_set()):
            waiting = False
        if waiting:
            outdata[:, 0] = 0
            return
        take, left = left[:frames], left[frames:]
        outdata[:len(take), 0] = take
        outdata[len(take):, 0] = 0
        if len(take) < frames and not finished.is_set():
            waiting = True  # ran dry mid-answer: wait for a real piece rather than play crumbs

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def start() -> Any:
        s = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", callback=feed)
        s.start()
        return s

    made, t0 = 0, time.perf_counter()  # not monotonic(): on Windows it ticks every ~16 ms, so work could read 0
    stream = None
    try:
        for chunk in chunks:
            if stopped():
                return False
            pending.put(chunk.astype("float32"))
            made += len(chunk)
            work = time.perf_counter() - t0
            if stream is None and work > 0 and made / rate >= _lead_needed(expected, made / rate / work):
                stream = start()
        finished.set()
        if stream is None:
            stream = start()
        deadline = time.monotonic() + made / rate + slack
        while not pending.empty() or len(left):
            if stopped():
                return False
            if time.monotonic() > deadline:
                print("(the sound device stalled; skipped the rest of that answer)")
                return True
            time.sleep(0.05)
        time.sleep(float(stream.latency) + 0.05)  # the device's own buffer still has to play out
        return True
    finally:
        if stream is not None:
            stream.abort()
            stream.close()


class Speaker:
    """The mouth. Windows SAPI by default; Piper when IRIS_TTS=piper (or tts = "piper" in config.toml), VoxCPM2 when
    tts = "voxcpm", Kokoro when IRIS_TTS=kokoro.

    speak() blocks until done and returns True if the text was fully spoken, False if a wake word
    cut it short. SAPI cannot be interrupted mid-utterance, so barge-in only lands between chunks
    in Kokoro mode (which streams)."""

    def __init__(self, engine: str | None = None):
        self.engine = engine or os.environ.get("IRIS_TTS") or config.get("tts", "sapi")
        self._kokoro: Any = None  # kokoro_onnx.Kokoro; loaded lazily (no type stubs)
        self._piper: dict[str, PiperProcess] = {}
        self._vox: VoxProcess | None = None
        self._lang = "en"  # the language of the last answer, for a short one whose language is unclear
        self._failures: dict[str, int] = {}  # consecutive failures per engine; three in a row switches the voice
        if self.engine == "voxcpm":  # start loading now: it takes half a minute or more
            try:
                self._vox = VoxProcess()
            except FileNotFoundError as exc:
                print(f"{exc}; using Piper instead.")
                self.engine = "piper"
        if self.engine == "piper":  # start the voices now: loading a model takes seconds, the first answer should not
            for lang in PIPER_VOICES:
                try:
                    self._piper_voice(lang)
                except FileNotFoundError:
                    pass  # speak() says what is missing when that language is needed

    def close(self) -> None:
        """Stop every speech worker this speaker started (VoxCPM2, Piper)."""
        workers: list[PiperProcess | VoxProcess] = [*self._piper.values()]
        if self._vox is not None:
            workers.append(self._vox)
        self._piper, self._vox = {}, None
        for w in workers:
            try:
                w.close()
            except Exception:  # noqa: BLE001 - stopping one worker must not keep the others running
                pass

    def _note_failure(self, key: str, display: str, fallback: str) -> None:
        """Count one failure of engine `key`; after three in a row the voice switches to `fallback` for good."""
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= 3:
            print(f"{display} failed {count} times; using {fallback} from now on.")
            self.engine = fallback

    def _note_success(self, key: str) -> None:
        """A fully spoken answer resets the failure streak of its engine."""
        if self._failures.get(key):
            self._failures[key] = 0

    def speak(self, text: str, stop_event: threading.Event | None = None) -> bool:
        """Print the answer and speak it with the best engine available; True unless a wake word cut it short."""
        print(f"Iris: {text}")
        if self.engine == "voxcpm" and self._vox is not None:
            return self._speak_voxcpm(self._vox, text, stop_event)
        if self.engine == "piper":
            return self._speak_piper(text, stop_event)
        if self.engine == "kokoro":
            return self._speak_kokoro(text, stop_event)
        return self._speak_sapi(text)

    def _speak_sapi(self, text: str) -> bool:
        """Windows SAPI. Speech is a nicety, so any failure just means the printed line stands."""
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception:  # noqa: BLE001 - speech is a nicety; the printed line is the answer
            pass
        return True

    def heard(self, text: str) -> None:
        """What the user just said: a short answer whose language is unclear ("Klaar.") is spoken in theirs."""
        from iris.eval import answer_language

        lang = answer_language(text)
        if lang in PIPER_VOICES:
            self._lang = lang

    def _piper_voice(self, lang: str) -> PiperProcess:
        if lang not in self._piper:
            model = PIPER_DIR / f"{PIPER_VOICES[lang]}.onnx"
            if not model.exists():
                raise FileNotFoundError(f"missing {model.name}; get it with: python -m piper.download_voices "
                                        f"{PIPER_VOICES[lang]} --data-dir {PIPER_DIR}")
            self._piper[lang] = PiperProcess(model)
        return self._piper[lang]

    def _speak_piper(self, text: str, stop_event: threading.Event | None) -> bool:
        from iris.eval import answer_language

        lang = answer_language(text)
        if lang in PIPER_VOICES:
            self._lang = lang
        try:
            path = self._piper_voice(self._lang).wav(text)
            ok = _play_wav(path, stop_event)
            if ok:
                self._note_success("piper")  # a fully spoken answer resets Piper's failure streak
            return ok
        except Exception as exc:  # noqa: BLE001 - fall back to SAPI so Iris keeps talking
            print(f"Piper failed ({exc}); using Windows SAPI instead.")
            self._note_failure("piper", "Piper", "sapi")  # switches the voice only after three in a row
            return self._speak_sapi(text)

    def _speak_voxcpm(self, vox: VoxProcess, text: str, stop_event: threading.Event | None) -> bool:
        if not vox.ready.is_set():  # still loading (a minute or more): Piper says this one rather than keep you waiting
            return self._speak_piper(text, stop_event)
        try:
            ok = _play_stream(vox.stream(text), vox.rate, len(text) / CHARS_PER_SECOND, stop_event)
            if ok:
                self._note_success("voxcpm")  # a fully spoken answer resets VoxCPM's failure streak
            return ok
        except Exception as exc:  # noqa: BLE001 - fall back to Piper so Iris keeps talking
            print(f"VoxCPM failed ({exc}); using Piper instead.")
            self._note_failure("voxcpm", "VoxCPM", "piper")  # switches the voice only after three in a row
            return self._speak_piper(text, stop_event)

    def _kokoro_model(self) -> Any:
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            missing = [os.path.basename(p) for p in (KOKORO_MODEL, KOKORO_VOICES) if not os.path.exists(p)]
            if missing:
                urls = " ".join(_KOKORO_FILES[n] for n in missing if n in _KOKORO_FILES)
                raise FileNotFoundError(f"missing {', '.join(missing)} in {KOKORO_DIR}; get them from {urls}")
            self._kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
        return self._kokoro

    def _speak_kokoro(self, text: str, stop_event: threading.Event | None) -> bool:
        import asyncio

        try:
            model = self._kokoro_model()

            async def play() -> bool:
                import sounddevice as sd

                async for chunk, sr in model.create_stream(text, voice=KOKORO_VOICE):
                    if stop_event is not None and stop_event.is_set():
                        return False
                    # sd.play returns at once and the next play would cut this chunk off: wait for its end,
                    # stopping at once for a wake word
                    sd.play(chunk.astype("float32"), sr)
                    end = time.perf_counter() + len(chunk) / sr
                    while time.perf_counter() < end:
                        if stop_event is not None and stop_event.is_set():
                            getattr(sd, "stop", lambda: None)()
                            return False
                        time.sleep(0.02)
                return True

            ok = asyncio.run(play())
            if ok:
                self._note_success("kokoro")  # a fully spoken answer resets Kokoro's failure streak
            return ok
        except Exception as exc:  # noqa: BLE001 - fall back to SAPI so Iris keeps talking
            print(f"Kokoro failed ({exc}); using Windows SAPI instead.")
            self._note_failure("kokoro", "Kokoro", "sapi")  # switches the voice only after three in a row
            return self._speak_sapi(text)


def _play_wav(path: Path, stop_event: threading.Event | None, slack: float = 3.0) -> bool:
    """Play a 16-bit mono WAV in the background and wait for it, stopping when a wake word sets stop_event. Playback
    never blocks Iris: if the sound device stalls (another app holding it), it is cut off `slack` seconds after the
    audio should have ended. The file is removed after."""
    import numpy as np
    import sounddevice as sd

    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype("float32") / 32768
    finally:
        path.unlink(missing_ok=True)
    sd.play(audio, rate)
    deadline = time.monotonic() + len(audio) / rate + slack
    try:
        while sd.get_stream().active:
            if stop_event is not None and stop_event.is_set():
                return False
            if time.monotonic() > deadline:
                print("(the sound device stalled; skipped the rest of that answer)")
                return True
            time.sleep(0.05)
        return True
    finally:
        sd.stop()


_speaker: Speaker | None = None


def speak(text: str) -> bool:
    """Speak out loud (module level, for push-to-talk mode). Returns True if fully spoken."""
    global _speaker
    if _speaker is None:
        _speaker = Speaker()
    return _speaker.speak(text)
