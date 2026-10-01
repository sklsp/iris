"""Ears and mouth. Wake word (openWakeWord) + energy-VAD recording, faster-whisper to transcribe,
Windows SAPI or Kokoro to speak."""

from __future__ import annotations

import os
import queue
import re
import threading
import time

WHISPER_MODEL = os.environ.get("IRIS_WHISPER", "small")
WHISPER_DEVICE = os.environ.get("IRIS_WHISPER_DEVICE", "cpu")  # "cuda" to use a GPU
RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz, the frame size openWakeWord wants

# Built-in models until a custom "hey iris" model exists; IRIS_WAKEWORD picks another built-in name
# or points straight at an .onnx file. Read at construction time so tests can override it.
WAKE_THRESHOLD = float(os.environ.get("IRIS_WAKE_THRESHOLD", 0.5))
SILENCE_SECONDS = 1.2  # stop recording after this much continuous silence
VAD_THRESHOLD = float(os.environ.get("IRIS_VAD_THRESHOLD", 0.01))  # RMS below this counts as silence

_YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|ja|jazeker|doe maar|ga je gang|prima|oké|oke|okay)\b", re.I)


def is_yes(text: str) -> bool:
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

    def feed(self, frame: "np.ndarray") -> bool:
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

    def detect(self, frame: "np.ndarray") -> bool:
        scores = self._model().predict(frame) or {}
        return max(scores.values(), default=0.0) >= self.threshold


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
        with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb):
            threading.Thread(target=lambda: (input(), stop.set()), daemon=True).start()
            stop.wait()
        if not chunks:
            return ""
        audio = np.concatenate(chunks)[:, 0]
        segments, _ = self.model.transcribe(audio, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class VoiceSession:
    """Mic + wake word + VAD in one long-lived session.

    The input stream stays open the whole time, so a new wake word can interrupt speech even while
    the agent is thinking or talking (the on_wake callback stops the speaker)."""

    def __init__(self, whisper_model: str | None = None, wake_word: str | None = None,
                 vad_threshold: float | None = None, silence_seconds: float = SILENCE_SECONDS,
                 max_record_seconds: float = 30):
        self.whisper_name = whisper_model or WHISPER_MODEL
        self.wake = WakeWord(wake_word)
        self.vad_threshold = vad_threshold
        self.silence_seconds = silence_seconds
        self.max_record_seconds = max_record_seconds
        self._frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1024)  # ~80 s, drops oldest when full
        self._stream = None
        self._whisper = None

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

    def next_request(self, on_wake=None) -> str | None:
        """Block until the user says the wake word and then an utterance; returns the transcript."""
        import numpy as np

        print("Say the wake word...")
        while True:  # phase 1: wait for the wake word
            if self.wake.detect(self._frames.get()):
                break
        if on_wake is not None:
            on_wake()
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

    def _transcribe(self, audio: "np.ndarray") -> str:
        from faster_whisper import WhisperModel

        if self._whisper is None:
            compute = "float16" if WHISPER_DEVICE == "cuda" else "int8"
            self._whisper = WhisperModel(self.whisper_name, device=WHISPER_DEVICE, compute_type=compute)
        segments, _ = self._whisper.transcribe(audio, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def speak(text: str) -> None:
    print(f"Iris: {text}")
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception:  # noqa: BLE001 - speech is a nicety; the printed line is the answer
        pass
