"""Ears and mouth. Wake word (openWakeWord) + energy-VAD recording, faster-whisper to transcribe,
Windows SAPI or Kokoro to speak."""

from __future__ import annotations

import json
import os
import queue
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

# None means automatic: large-v3-turbo on the GPU when CUDA works (pip install iris[gpu]), small on the CPU otherwise.
WHISPER_MODEL = os.environ.get("IRIS_WHISPER")
WHISPER_DEVICE = os.environ.get("IRIS_WHISPER_DEVICE")  # "cpu" or "cuda" to force one
RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz, the frame size openWakeWord wants

# IRIS_WAKEWORD picks a built-in openWakeWord name, an .onnx file, or "iris" for NameWake (no model needed).
# Read at construction time so tests can override it.
WAKE_THRESHOLD = float(os.environ.get("IRIS_WAKE_THRESHOLD", 0.5))
# Stop recording after this much continuous silence (IRIS_SILENCE). 1.2 s cut people off when they paused to think.
SILENCE_SECONDS = float(os.environ.get("IRIS_SILENCE", 1.8))
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
PIPER_VOICES = {"nl": os.environ.get("IRIS_PIPER_NL", "nl_NL-ronnie-medium"),
                "en": os.environ.get("IRIS_PIPER_EN", "en_US-joe-medium")}

# VoxCPM2 (tts = "voxcpm"): one natural voice for Dutch and English, also when they mix in one sentence. It needs
# torch, so it runs in its own Python through iris/voxcpm_worker.py; the README's Speech section says how to set it up.
VOXCPM_DIR = Path(os.environ.get("IRIS_VOXCPM_DIR") or (Path.home() / ".iris" / "voxcpm"))
VOXCPM_PYTHON = Path(os.environ.get("IRIS_VOXCPM_PYTHON") or (VOXCPM_DIR / "venv" / "Scripts" / "python.exe"))
# A GPU too slow for real time (an RTX 3060 makes 1 s of speech in about 1.5 s) still plays without gaps when this
# share of the answer is made before it starts.
VOXCPM_LEAD = float(os.environ.get("IRIS_VOXCPM_LEAD", "0.35"))
CHARS_PER_SECOND = 17  # how fast VoxCPM2 speaks (measured 16 to 20), to guess an answer's length before it is made

# A wake phrase at the start of a request ("Hey Jarvis, ...") is not part of it.
_WAKE_PHRASE = re.compile(r"^\s*(?:(?:hey|hé|hee|hoi|hi|ok|okay)[\s,!.]*)?(?:jarvis|iris)\b[\s,!.?]*", re.I)


def _strip_wake(text: str) -> str:
    return _WAKE_PHRASE.sub("", text).strip()


_YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|ja|jazeker|doe maar|ga je gang|prima|oké|oke|okay|"
                  r"genau|klar|mach das|oui|d'accord|vas-y|allez-y|sí|si|claro|vale|adelante|hazlo)\b", re.I)
# Any no-word wins over a yes-word, so a mixed or unclear answer is a "no".
_NO = re.compile(r"\b(no|nee|niet|stop|cancel|nein|nicht|non|pas|annuler|abbrechen|cancelar)\b", re.I)


def is_yes(text: str | None) -> bool:
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
        self.name = name or os.environ.get("IRIS_WAKEWORD", "hey_jarvis")
        self.threshold = threshold
        self._m = None

    def _model(self):
        if self._m is not None:
            return self._m
        import openwakeword

        path = self.name
        if self.name in openwakeword.MODELS:
            path = openwakeword.MODELS[self.name]["model_path"]
        onnx = path.replace(".tflite", ".onnx")
        if not os.path.exists(onnx):
            print(f"Downloading wake word model {self.name} (first run only)...")
            names = [self.name] if self.name in openwakeword.MODELS else []
            openwakeword.utils.download_models(names)
        self._m = openwakeword.Model(wakeword_models=[onnx])
        return self._m

    def detect(self, frame: np.ndarray) -> bool:
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
    """The nvidia-* wheels of the gpu extra ship cuBLAS and cuDNN as DLLs that Windows does not find on its own."""
    import sysconfig

    root = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
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
    gpu = os.environ.get("IRIS_WHISPER_GPU")
    extra = {"device_index": int(gpu)} if device == "cuda" and gpu else {}
    return WhisperModel(model, device=device, compute_type=compute, **extra)


class NameWake:
    """Wake on the spoken name "Iris" without a trained model: an energy VAD cuts short utterances (at most
    `max_seconds`) out of the mic stream, and a small Whisper model (IRIS_WAKE_WHISPER, default tiny, with "Iris" as
    a hotword: about 0.6 s on a CPU) checks whether they say the name. Same detect(frame) interface as WakeWord.

    ponytail: every short utterance in the room is transcribed; a trained openWakeWord model is cheaper on CPU."""

    NAME = re.compile(r"\b(iris|irıs|iriss)\b", re.I)

    def __init__(self, transcribe=None, threshold: float | None = None, max_seconds: float = 3.0,
                 silence_seconds: float = 0.4):
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
    name = name or os.environ.get("IRIS_WAKEWORD", "hey_jarvis")
    return NameWake(threshold=vad_threshold) if name.lower() in ("iris", "hey_iris") else WakeWord(name)


class Voice:
    """Push-to-talk mode: press Enter to stop recording."""

    def __init__(self):

        self.model = _whisper_model(WHISPER_MODEL, WHISPER_DEVICE)

    def listen(self, prompt: str = "Listening... press Enter when done.") -> str:
        import numpy as np
        import sounddevice as sd

        chunks, stop = [], threading.Event()

        def cb(indata, frames, t, status):
            chunks.append(indata.copy())

        print(prompt)

        def _stop_on_enter():
            input()
            stop.set()

        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb):
            threading.Thread(target=_stop_on_enter, daemon=True).start()
            stop.wait()
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
    the agent is thinking or talking (speak() returns True when that happens)."""

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

    def __enter__(self):
        import sounddevice as sd

        def cb(indata, frames, t, status):
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

        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME, callback=cb)
        self._stream.__enter__()
        return self

    def __exit__(self, *exc):
        if self._stream is not None:
            try:
                self._stream.__exit__(*exc)
            except Exception:  # noqa: BLE001 - closing the mic must never mask a real error
                pass

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
                self._speech = None
        return self._interrupted

    def next_request(self) -> str:
        """Block until the user says the wake word and then an utterance; returns the transcript."""
        self.wait_for_speech()  # never race the wake watcher for frames
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
            if self.wake.detect(self._frames.get()):
                break
        self.chime()
        return self.next_command()

    def chime(self) -> None:
        """A short tone right after the wake word, so the user hears that Iris is listening (IRIS_CHIME=0 mutes
        it). It finishes before recording starts, so it never ends up in the request."""
        if os.environ.get("IRIS_CHIME", "1") == "0":
            return
        try:
            import numpy as np
            import sounddevice as sd

            t = np.arange(int(0.12 * 22050)) / 22050
            tone = (0.4 * np.sin(2 * np.pi * 880 * t) * np.minimum(1, (0.12 - t) * 40)).astype("float32")
            sd.play(tone, 22050, blocking=True)
        except Exception:  # noqa: BLE001 - the chime is a nicety; listening goes on without it
            pass

    def follow_up(self) -> str | None:
        """Right after an answer: if the user starts talking within IRIS_FOLLOW_UP seconds (default 8), record and
        transcribe that as the next request, no wake word needed. None when nobody spoke, so talk that is not meant
        for Iris only reaches her within that short window."""
        import numpy as np

        window = float(os.environ.get("IRIS_FOLLOW_UP", FOLLOW_UP_SECONDS))
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
                return self._record(recent)
        return None

    def next_command(self) -> str:
        """The wake word was already heard (barge-in); record and transcribe what follows."""
        print("Listening...")
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
            frame = self._frames.get()
            chunks.append(frame)
            if det.feed(frame):
                break
            if time.time() - t0 > self.max_record_seconds:
                break
        text = _strip_wake(self._transcribe(np.concatenate(chunks)))
        print(f"You: {text or '(nothing heard)'}")  # so the user sees what Iris understood
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
        self.wait_for_speech()  # one speech at a time
        self._interrupted = False
        self._answered = True
        thread = threading.Thread(target=self._speak_and_watch, args=(text,), daemon=True)
        self._speech = thread
        thread.start()
        return thread

    def _speak_and_watch(self, text: str) -> None:
        stop = threading.Event()
        watcher = threading.Thread(target=self._watch_for_wake, args=(stop,), daemon=True)
        watcher.start()
        try:
            finished = self.speaker.speak(text, stop_event=stop)
        finally:
            stop.set()  # release the watcher whether or not it was interrupted
        watcher.join(timeout=1.0)
        self._interrupted = not finished

    def _watch_for_wake(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                frame = self._frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.wake.detect(frame):
                print("Interrupted.")
                self._wake_seen = True
                stop.set()

    def _transcribe(self, audio: np.ndarray) -> str:

        if self._whisper is None:
            self._whisper = _whisper_model(self.whisper_name, WHISPER_DEVICE)
        segments, _ = self._whisper.transcribe(audio, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class PiperProcess:
    """One running `python -m piper` for one voice. It loads the model once and writes a WAV file per line of text
    into its own temp folder; wav() waits until that file is complete."""

    def __init__(self, model: Path):
        self.dir = Path(tempfile.mkdtemp(prefix="iris-piper-"))
        self.proc = subprocess.Popen([sys.executable, "-m", "piper", "-m", str(model), "-d", str(self.dir)],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8")

    def wav(self, text: str, timeout: float = 20) -> Path:
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
        env = {**os.environ, "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "TQDM_DISABLE": "1",
               "CUDA_VISIBLE_DEVICES": os.environ.get("IRIS_VOXCPM_GPU", "0")}
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

    def _next(self, timeout: float):
        try:
            item = self._frames.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("VoxCPM took too long") from None
        if item is EOFError:
            self._frames.put(EOFError)
            raise RuntimeError(f"VoxCPM stopped; see {VOXCPM_DIR / 'worker.log'}")
        return item

    def stream(self, text: str, timeout: float = 30):
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


def _exact(f, n: int) -> bytes:
    data = b""
    while len(data) < n:
        part = f.read(n - len(data))
        if not part:
            raise EOFError
        data += part
    return data


def _play_stream(chunks, rate: int, lead: float, stop_event: threading.Event | None, slack: float = 3.0) -> bool:
    """Play streamed float32 audio once `lead` seconds are made (or all of it), so a voice made slower than real time
    still plays without gaps. Like _play_wav it stops for a wake word and never hangs on a stalled sound device."""
    import numpy as np
    import sounddevice as sd

    pending: queue.Queue = queue.Queue()
    left = np.zeros(0, dtype="float32")

    def feed(outdata, frames, _time, _status):
        nonlocal left
        while len(left) < frames and not pending.empty():
            left = np.concatenate([left, pending.get_nowait()])
        take, left = left[:frames], left[frames:]
        outdata[:len(take), 0] = take
        outdata[len(take):, 0] = 0  # not made yet: a moment of silence rather than a crash

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    made = 0
    stream = None
    try:
        for chunk in chunks:
            if stopped():
                return False
            pending.put(chunk.astype("float32"))
            made += len(chunk)
            if stream is None and made >= lead * rate:
                stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", callback=feed)
                stream.start()
                started = time.monotonic()
        if stream is None:
            stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", callback=feed)
            stream.start()
            started = time.monotonic()
        deadline = started + made / rate + slack
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
        from iris import config

        self.engine = engine or os.environ.get("IRIS_TTS") or config.get("tts", "sapi")
        self._kokoro: Any = None  # kokoro_onnx.Kokoro; loaded lazily (no type stubs)
        self._piper: dict[str, PiperProcess] = {}
        self._vox: VoxProcess | None = None
        self._lang = "en"  # the language of the last answer, for a short one whose language is unclear
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

    def speak(self, text: str, stop_event: threading.Event | None = None) -> bool:
        print(f"Iris: {text}")
        if self.engine == "voxcpm" and self._vox is not None:
            return self._speak_voxcpm(self._vox, text, stop_event)
        if self.engine == "piper":
            return self._speak_piper(text, stop_event)
        if self.engine == "kokoro":
            return self._speak_kokoro(text, stop_event)
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
            return _play_wav(path, stop_event)
        except Exception as exc:  # noqa: BLE001 - fall back to SAPI so Iris keeps talking
            print(f"Piper failed ({exc}); using Windows SAPI instead.")
            self.engine = "sapi"
            return self.speak(text, stop_event)

    def _speak_voxcpm(self, vox: VoxProcess, text: str, stop_event: threading.Event | None) -> bool:
        if not vox.ready.is_set():  # still loading (a minute or more): Piper says this one rather than keep you waiting
            return self._speak_piper(text, stop_event)
        try:
            lead = VOXCPM_LEAD * len(text) / CHARS_PER_SECOND
            return _play_stream(vox.stream(text), vox.rate, lead, stop_event)
        except Exception as exc:  # noqa: BLE001 - fall back to Piper so Iris keeps talking
            print(f"VoxCPM failed ({exc}); using Piper instead.")
            self.engine = "piper"
            return self._speak_piper(text, stop_event)

    def _kokoro_model(self):
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

            async def play():
                import sounddevice as sd

                async for chunk, sr in model.create_stream(text, voice=KOKORO_VOICE):
                    if stop_event is not None and stop_event.is_set():
                        return False
                    # blocks per chunk: streaming with an interrupt point between chunks
                    sd.play(chunk.astype("float32"), sr)
                return True

            return asyncio.run(play())
        except Exception as exc:  # noqa: BLE001 - fall back to SAPI so Iris keeps talking
            print(f"Kokoro failed ({exc}); using Windows SAPI instead.")
            self.engine = "sapi"
            return self.speak(text, stop_event)


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
