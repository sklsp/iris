"""The speaker records when the first sound of each answer starts, per engine (no real audio)."""

import sys
import threading
import time
import types
from typing import Any, cast

import pytest

from iris.voice import RATE, PiperProcess, Speaker, VoxProcess, _play_stream, _play_wav

np = pytest.importorskip("numpy")  # synthetic-audio tests need real numpy; CI installs only [dev]


class FakeClock:
    """A clock that steps by a fixed amount per read, so the recorded timings are literal."""

    def __init__(self, step):
        self.step = step
        self.t = 0.0

    def __call__(self) -> float:
        t = self.t
        self.t += self.step
        return t


def _wav(path, seconds=0.1, rate=8000):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(2 * int(seconds * rate)))
    return path


def _fake_wav_device(monkeypatch):
    """A sounddevice whose play() returns at once and whose stream is already finished."""
    calls: list[tuple[Any, ...]] = []

    def fake_play(audio, rate):
        calls.append(("play", len(audio), rate))

    def fake_stop():
        calls.append(("stop",))

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(
        play=fake_play, get_stream=lambda: types.SimpleNamespace(active=False), stop=fake_stop))
    return calls


def _fake_pulling_device(monkeypatch):
    """A sounddevice that pulls audio from the callback in small frames, as PortAudio does."""

    class PullingOut:
        def __init__(self, samplerate, channels, dtype, callback):
            self.latency = 0.0
            self._cb, self._run = callback, False

        def start(self):
            self._run = True
            threading.Thread(target=self._loop, daemon=True).start()

        def _loop(self):
            while self._run:
                buf = np.zeros((100, 1), dtype="float32")
                self._cb(buf, 100, None, None)
                time.sleep(0.001)

        def abort(self):
            self._run = False

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=PullingOut))


def _fake_pyttsx3(monkeypatch):
    class Engine:
        def say(self, t):
            pass

        def runAndWait(self):
            pass

    def make_engine():
        return Engine()

    monkeypatch.setitem(sys.modules, "pyttsx3", types.SimpleNamespace(init=make_engine))


class _FakePiperProcess:
    """Stands in for one running Piper; wav() returns a real (silent) WAV."""

    def __init__(self, path):
        self.path = path

    def wav(self, text, timeout=20):
        return self.path


class _FakeVoxProcess:
    """Stands in for the loaded VoxCPM2 worker; stream() yields one short chunk."""

    def __init__(self, rate):
        self.ready = threading.Event()
        self.ready.set()
        self.rate = rate

    def stream(self, text, timeout=30):
        yield np.zeros(int(self.rate * 0.05), dtype="float32")


def test_piper_records_when_the_first_sound_starts(tmp_path, monkeypatch):
    _fake_wav_device(monkeypatch)
    spk = Speaker(engine="sapi", clock=FakeClock(0.25))  # speak() reads t0, the first sound lands one step later
    spk.engine = "piper"
    spk._piper["en"] = cast(PiperProcess, _FakePiperProcess(_wav(tmp_path / "a.wav")))
    assert spk.speak("hello there") is True
    assert spk.last_first_sound_ms == 250


def test_voxcpm_records_when_the_stream_starts(monkeypatch):
    _fake_pulling_device(monkeypatch)
    spk = Speaker(engine="sapi", clock=FakeClock(1.5))  # the first chunk takes a while to be made
    spk.engine = "voxcpm"
    spk._vox = cast(VoxProcess, _FakeVoxProcess(RATE))
    assert spk.speak("a long enough answer") is True
    assert spk.last_first_sound_ms == 1500


def test_on_start_is_called_exactly_once_per_playback(tmp_path, monkeypatch):
    calls = []

    def on_start():
        calls.append(1)

    _fake_wav_device(monkeypatch)
    assert _play_wav(_wav(tmp_path / "a.wav"), None, on_start=on_start) is True
    assert _play_wav(_wav(tmp_path / "b.wav"), None, on_start=on_start) is True
    assert calls == [1, 1]

    calls.clear()
    _fake_pulling_device(monkeypatch)
    chunk = np.zeros(int(RATE * 0.05), dtype="float32")
    assert _play_stream([chunk], RATE, 0.05, None, slack=0.0, on_start=on_start) is True
    assert _play_stream([chunk], RATE, 0.05, None, slack=0.0, on_start=on_start) is True
    assert calls == [1, 1]


def test_on_start_fires_when_the_stream_starts_mid_generation(monkeypatch):
    _fake_pulling_device(monkeypatch)
    calls = []

    def slow_chunks():
        time.sleep(0.005)  # enough work that the speed check runs before playback starts
        yield np.zeros(int(RATE * 0.3), dtype="float32")  # a full lead's worth, made faster than real time

    assert _play_stream(slow_chunks(), RATE, 0.3, None, slack=0.0, on_start=lambda: calls.append(1)) is True
    assert calls == [1]


def test_a_second_speak_resets_the_value(tmp_path, monkeypatch):
    _fake_wav_device(monkeypatch)
    spk = Speaker(engine="sapi", clock=FakeClock(0.25))
    spk.engine = "piper"
    seen_during = []

    class RecordingPiper(_FakePiperProcess):
        def wav(self, text, timeout=20):
            seen_during.append(spk.last_first_sound_ms)  # between speak() and the first sound
            # a fresh file per answer, like real Piper: _play_wav deletes the file it played, and a missing one
            # would fall back to SAPI (which CI runners cannot speak with)
            return _wav(tmp_path / f"{len(seen_during)}.wav")

    spk._piper["en"] = cast(PiperProcess, RecordingPiper(None))
    assert spk.speak("first answer") is True
    assert spk.last_first_sound_ms == 250
    assert spk.speak("second answer") is True
    assert seen_during == [None, None]  # each speak starts with no first sound yet
    assert spk.last_first_sound_ms == 250


def test_sapi_records_the_moment_it_is_asked_to_speak(monkeypatch):
    _fake_pyttsx3(monkeypatch)
    spk = Speaker(engine="sapi", clock=FakeClock(0.7))
    assert spk.speak("hello world") is True
    assert spk.last_first_sound_ms == 700
