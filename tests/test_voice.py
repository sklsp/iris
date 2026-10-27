"""Tests for the wake word, energy VAD, and speaker on synthetic audio (no mic, no LLM)."""

import sys
import threading
import time
import types

import pytest

from iris.voice import FRAME, RATE, SilenceDetector, Speaker, VoiceSession, WakeWord

np = pytest.importorskip("numpy")  # synthetic-audio tests need real numpy; CI installs only [dev]


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


class FakeKokoroModel:
    def __init__(self, chunks):
        self.chunks = chunks

    async def create_stream(self, text, **kwargs):
        for c in self.chunks:
            yield (c, 24000)


def _fake_sounddevice(monkeypatch, played):
    fake_sd = types.ModuleType("sounddevice")
    fake_sd.play = lambda data, sr: played.append(len(data))
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)


def test_speaker_kokoro_streams_all_chunks_when_not_interrupted(monkeypatch):
    played = []
    _fake_sounddevice(monkeypatch, played)
    spk = Speaker(engine="kokoro")
    spk._kokoro = FakeKokoroModel([np.zeros(2400, dtype="float32") for _ in range(5)])
    assert spk.speak("hello", stop_event=threading.Event()) is True
    assert len(played) == 5


def test_speaker_kokoro_stops_between_chunks_on_interrupt(monkeypatch):
    played = []
    stop = threading.Event()

    def play(data, sr):
        played.append(len(data))
        if len(played) == 1:
            stop.set()  # Jay says the wake word after the first chunk

    fake_sd = types.ModuleType("sounddevice")
    fake_sd.play = play
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    spk = Speaker(engine="kokoro")
    spk._kokoro = FakeKokoroModel([np.zeros(2400, dtype="float32") for _ in range(5)])
    assert spk.speak("hello", stop_event=stop) is False
    assert len(played) == 1


def test_speaker_kokoro_falls_back_to_sapi(monkeypatch, tmp_path):
    said = []

    class FakeEngine:
        def say(self, t):
            said.append(t)

        def runAndWait(self):
            pass

    fake_pyttsx3 = types.ModuleType("pyttsx3")
    fake_pyttsx3.init = lambda: FakeEngine()
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_pyttsx3)

    class BrokenKokoro:
        def __init__(self, *a, **k):
            raise RuntimeError("no model")

    fake_kokoro_onnx = types.ModuleType("kokoro_onnx")
    fake_kokoro_onnx.Kokoro = BrokenKokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_onnx)

    import iris.voice as v

    # point the model paths at existing temp files so no download is attempted
    for name in ("KOKORO_MODEL", "KOKORO_VOICES"):
        p = tmp_path / f"{name.lower()}.bin"
        p.write_bytes(b"x")
        monkeypatch.setattr(v, name, str(p))

    spk = Speaker(engine="kokoro")
    assert spk.speak("still talking") is True  # Kokoro blew up; SAPI picked it up
    assert said == ["still talking"]


def test_speaker_sapi_uses_pyttsx3(monkeypatch):
    said = []

    class FakeEngine:
        def say(self, t):
            said.append(t)

        def runAndWait(self):
            pass

    fake_pyttsx3 = types.ModuleType("pyttsx3")
    fake_pyttsx3.init = lambda: FakeEngine()
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_pyttsx3)
    assert Speaker(engine="sapi").speak("hello world") is True
    assert said == ["hello world"]


def _fake_pyttsx3(monkeypatch, said):
    class FakeEngine:
        def say(self, t):
            said.append(t)

        def runAndWait(self):
            pass

    fake = types.ModuleType("pyttsx3")
    fake.init = lambda: FakeEngine()
    monkeypatch.setitem(sys.modules, "pyttsx3", fake)


def test_speaker_kokoro_without_model_files_falls_back_and_never_downloads(monkeypatch, tmp_path):
    said = []
    _fake_pyttsx3(monkeypatch, said)
    import iris.voice as v

    for name in ("KOKORO_MODEL", "KOKORO_VOICES"):
        monkeypatch.setattr(v, name, str(tmp_path / "does-not-exist.onnx"))

    # Iris must never fetch anything by itself: no module in the import chain may do network I/O
    import socket

    def no_network(*a, **k):
        raise AssertionError("Iris must not download model files on its own")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    spk = Speaker(engine="kokoro")
    assert spk.speak("still talking") is True  # no Kokoro files, so SAPI speaks instead
    assert said == ["still talking"]
    assert spk.engine == "sapi"


class _FakeWake:
    def __init__(self, hit):
        self.hit = hit

    def detect(self, frame):
        return self.hit


def test_session_speak_returns_true_when_wake_word_heard_during_speech():
    class SlowSpeaker:  # "talks" until the stop event fires (or 2 s)
        def speak(self, text, stop_event=None):
            return not (stop_event.wait(2.0) if stop_event else False)

    s = VoiceSession(speaker=SlowSpeaker())
    s.wake = _FakeWake(True)
    for _ in range(50):  # enough frames to last while the speaker "talks"
        s._frames.put(np.zeros(FRAME, dtype="float32"))
    assert s.speak("a long answer") is True


def test_session_speak_completes_without_interruption():
    class QuickSpeaker:
        def speak(self, text, stop_event=None):
            time.sleep(0.15)  # the watcher must sit idle and be released afterwards
            return True

    s = VoiceSession(speaker=QuickSpeaker())
    s.wake = _FakeWake(False)
    assert s.speak("short answer") is False


def test_session_speak_async_does_not_block_the_loop():
    done = []

    class SlowSpeaker:
        def speak(self, text, stop_event=None):
            time.sleep(0.4)  # still talking when speak_async returns
            done.append(text)
            return True

    s = VoiceSession(speaker=SlowSpeaker())
    s.wake = _FakeWake(False)
    t0 = time.time()
    thread = s.speak_async("the answer")
    assert time.time() - t0 < 0.2  # the agent can keep working right away
    assert done == []  # not finished yet
    thread.join()
    assert done == ["the answer"]


def test_session_barge_in_skips_the_wake_word_for_the_next_request():
    class BlockingSpeaker:
        def speak(self, text, stop_event=None):
            return not (stop_event.wait(2.0) if stop_event else False)

    s = VoiceSession(speaker=BlockingSpeaker())
    s.wake = _FakeWake(True)  # the wake word fires on every frame
    s._transcribe = lambda audio: "close the window"
    for _ in range(50):
        s._frames.put(np.zeros(FRAME, dtype="float32"))
    assert s.speak("a long answer") is True  # interrupted

    # Jay already said the wake word, so the next request records straight away, no wake word needed.
    # The frames come from their own thread: next_command() drains the queue first, and only what
    # arrives after that drain is treated as the utterance.
    utterance = np.concatenate([np.full(int(RATE * 0.5), 0.3), np.zeros(int(RATE * 1.5))])

    def feed():
        deadline = time.time() + 2.0
        while not s._frames.empty() and time.time() < deadline:
            time.sleep(0.01)  # wait for the drain
        for f in frames_of(utterance):
            s._frames.put(f)

    feeder = threading.Thread(target=feed)
    feeder.start()
    assert s.next_request() == "close the window"
    feeder.join()
    assert s._wake_seen is False  # consumed, so the next request waits for a real wake word
