"""Ears and mouth. v0: press Enter to stop recording, faster-whisper to transcribe, Windows SAPI to speak."""

from __future__ import annotations

import os
import re
import threading

WHISPER_MODEL = os.environ.get("HERMES_WHISPER", "small")
WHISPER_DEVICE = os.environ.get("HERMES_WHISPER_DEVICE", "cpu")  # "cuda" to use a GPU
RATE = 16000

_YES = re.compile(r"\b(yes|yeah|yep|sure|do it|go ahead|ja|jazeker|doe maar|ga je gang|prima|oké|oke|okay)\b", re.I)


def is_yes(text: str) -> bool:
    return bool(_YES.search(text)) and not re.search(r"\b(no|nee|niet|stop|cancel)\b", text, re.I)


class Voice:
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


def speak(text: str) -> None:
    print(f"Hermes: {text}")
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception:  # noqa: BLE001 - speech is a nicety; the printed line is the answer
        pass
