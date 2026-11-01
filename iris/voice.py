"""Ears and mouth. Wake word (openWakeWord) + energy-VAD recording, faster-whisper to transcribe,
Windows SAPI or Kokoro to speak."""

from __future__ import annotations

import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

WHISPER_MODEL = os.environ.get("IRIS_WHISPER", "small")
WHISPER_DEVICE = os.environ.get("IRIS_WHISPER_DEVICE", "cpu")  # "cuda" to use a GPU
RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz, the frame size openWakeWord wants

# IRIS_WAKEWORD picks a built-in openWakeWord name, an .onnx file, or "iris" for NameWake (no model needed).
# Read at construction time so tests can override it.
WAKE_THRESHOLD = float(os.environ.get("IRIS_WAKE_THRESHOLD", 0.5))
SILENCE_SECONDS = 1.2  # stop recording after this much continuous silence
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

_YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|ja|jazeker|doe maar|ga je gang|prima|oké|oke|okay)\b", re.I)


def is_yes(text: str | None) -> bool:
    if text is None:
        return False  # no answer at all is a "no"
    return bool(_YES.search(text)) and not re.search(r"\b(no|nee|niet|stop|cancel)\b", text, re.I)


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
        scores = self._model().predict(frame) or {}
        return max(scores.values(), default=0.0) >= self.threshold


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
        from faster_whisper import WhisperModel

        if self._whisper is None:
            compute = "float16" if WHISPER_DEVICE == "cuda" else "int8"
            self._whisper = WhisperModel(os.environ.get("IRIS_WAKE_WHISPER", "tiny"), device=WHISPER_DEVICE,
                                         compute_type=compute)
        segments, _ = self._whisper.transcribe(audio, hotwords="Iris")
        return " ".join(s.text.strip() for s in segments).strip()


def make_wake(name: str | None = None) -> WakeWord | NameWake:
    """"iris" or "hey_iris" wakes on the name through Whisper; anything else is an openWakeWord model."""
    name = name or os.environ.get("IRIS_WAKEWORD", "hey_jarvis")
    return NameWake() if name.lower() in ("iris", "hey_iris") else WakeWord(name)


class Voice:
    """Push-to-talk mode: press Enter to stop recording."""

    def __init__(self):
        from faster_whisper import WhisperModel

        compute = "float16" if WHISPER_DEVICE == "cuda" else "int8"
        self.model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=compute)

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
        return " ".join(s.text.strip() for s in segments).strip()


class VoiceSession:
    """Mic + wake word + VAD in one long-lived session.

    The input stream stays open the whole time, so a new wake word can interrupt speech even while
    the agent is thinking or talking (speak() returns True when that happens)."""

    def __init__(self, whisper_model: str | None = None, wake_word: str | None = None,
                 vad_threshold: float | None = None, silence_seconds: float = SILENCE_SECONDS,
                 max_record_seconds: float = 30, speaker: Speaker | None = None):
        self.whisper_name = whisper_model or WHISPER_MODEL
        self.wake = make_wake(wake_word)
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
            self._speech.join()
        return self._interrupted

    def next_request(self) -> str:
        """Block until the user says the wake word and then an utterance; returns the transcript."""
        self.wait_for_speech()  # never race the wake watcher for frames
        if self._wake_seen:  # barge-in: the user said the wake word while Iris was talking
            self._wake_seen = False
            return self.next_command()
        print("Say the wake word...")
        while True:  # phase 1: wait for the wake word
            if self.wake.detect(self._frames.get()):
                break
        return self.next_command()

    def next_command(self) -> str:
        """The wake word was already heard (barge-in); record and transcribe what follows."""
        import numpy as np

        print("Listening...")
        self._drain()  # drop the wake word itself so it does not end up in the transcript
        det = SilenceDetector(threshold=self.vad_threshold, silence_seconds=self.silence_seconds)
        chunks, t0 = [], time.time()
        while True:  # phase 2: record until 1.2 s of silence (or a hard cap)
            frame = self._frames.get()
            chunks.append(frame)
            if det.feed(frame):
                break
            if time.time() - t0 > self.max_record_seconds:
                break
        return self._transcribe(np.concatenate(chunks))

    def speak(self, text: str) -> bool:
        """Speak with barge-in and block until done. Returns True if a wake word cut it short."""
        self.speak_async(text).join()
        return self._interrupted

    def speak_async(self, text: str) -> threading.Thread:
        """Speak in a background thread, so the agent can listen for the next request while Iris talks.

        A wake word during the speech stops it; the next next_request() then records straight away
        instead of waiting for a new wake word."""
        self.wait_for_speech()  # one speech at a time
        self._interrupted = False
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
        from faster_whisper import WhisperModel

        if self._whisper is None:
            compute = "float16" if WHISPER_DEVICE == "cuda" else "int8"
            self._whisper = WhisperModel(self.whisper_name, device=WHISPER_DEVICE, compute_type=compute)
        segments, _ = self._whisper.transcribe(audio, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class Speaker:
    """The mouth. Windows SAPI by default; Kokoro (kokoro-onnx) when IRIS_TTS=kokoro.

    speak() blocks until done and returns True if the text was fully spoken, False if a wake word
    cut it short. SAPI cannot be interrupted mid-utterance, so barge-in only lands between chunks
    in Kokoro mode (which streams)."""

    def __init__(self, engine: str | None = None):
        self.engine = engine or os.environ.get("IRIS_TTS", "sapi")
        self._kokoro: Any = None  # kokoro_onnx.Kokoro; loaded lazily (no type stubs)

    def speak(self, text: str, stop_event: threading.Event | None = None) -> bool:
        print(f"Iris: {text}")
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


_speaker: Speaker | None = None


def speak(text: str) -> bool:
    """Speak out loud (module level, for push-to-talk mode). Returns True if fully spoken."""
    global _speaker
    if _speaker is None:
        _speaker = Speaker()
    return _speaker.speak(text)
