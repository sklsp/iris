"""Tests for the wake word and energy VAD on synthetic audio (no mic, no LLM)."""

import numpy as np

from iris.voice import FRAME, RATE, SilenceDetector, WakeWord


def frames_of(audio):
    for i in range(0, len(audio), FRAME):
        yield audio[i:i + FRAME]


def test_silence_detector_stops_after_1_2s_of_silence():
    speech = np.full(int(RATE * 1.0), 0.3)  # 1 s of "speech" (RMS 0.3)
    silence = np.zeros(int(RATE * 2.5))     # then 2.5 s of silence
    audio = np.concatenate([np.zeros(int(RATE * 0.5)), speech, silence])
    det = SilenceDetector(silence_seconds=1.2)
    consumed, stopped_at = 0, None
    for f in frames_of(audio):
        consumed += len(f)
        if det.feed(f):
            stopped_at = consumed / RATE
            break
    assert stopped_at is not None
    # speech ends at t=1.5 s; the stop must come 1.2 s later, within one frame (80 ms)
    assert 2.6 <= stopped_at <= 2.79


def test_silence_detector_never_stops_during_continuous_speech():
    audio = np.full(int(RATE * 3), 0.3)
    det = SilenceDetector()
    for f in frames_of(audio):
        assert not det.feed(f)


def test_silence_detector_ignores_leading_silence():
    audio = np.zeros(int(RATE * 3))
    det = SilenceDetector()
    for f in frames_of(audio):
        assert not det.feed(f)


def test_vad_threshold_is_configurable():
    # a threshold above the "speech" level means nothing counts as speech, so it never stops
    audio = np.concatenate([np.full(int(RATE), 0.3), np.zeros(int(RATE * 2))])
    det = SilenceDetector(threshold=0.5)
    for f in frames_of(audio):
        assert not det.feed(f)


class FakeModel:
    def __init__(self, score):
        self.score = score

    def predict(self, x):
        return {"hey_jarvis": self.score}


def test_wake_word_detect_uses_threshold():
    w = WakeWord()
    w._m = FakeModel(0.9)
    assert w.detect(np.zeros(FRAME)) is True
    strict = WakeWord(threshold=0.95)
    strict._m = FakeModel(0.9)
    assert strict.detect(np.zeros(FRAME)) is False


def test_wake_word_name_from_env(monkeypatch):
    monkeypatch.setenv("IRIS_WAKEWORD", "hey_iris")
    assert WakeWord().name == "hey_iris"
    assert WakeWord(name="custom.onnx").name == "custom.onnx"


def test_wake_word_defaults_to_jarvis(monkeypatch):
    monkeypatch.delenv("IRIS_WAKEWORD", raising=False)
    assert WakeWord().name == "hey_jarvis"
