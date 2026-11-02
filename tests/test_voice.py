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


def test_wake_word_feeds_openwakeword_16_bit_pcm():
    seen = []

    class Recorder(FakeModel):
        def predict(self, x):
            seen.append(x)
            return {"hey_jarvis": 0.0}

    w = WakeWord()
    w._m = Recorder(0.0)
    w.detect(np.full(FRAME, 0.5, dtype=np.float32))  # the mic stream delivers float32 in [-1, 1]
    assert seen[0].dtype == np.int16 and seen[0][0] == 16383


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


def _tone(seconds, amp=0.2):
    n = int(seconds * RATE / FRAME)
    return [np.full(FRAME, amp, dtype=np.float32) for _ in range(n)]


def _quiet(seconds):
    return _tone(seconds, 0.0)


def test_name_wake_transcribes_a_short_utterance_with_its_lead_in_and_matches_the_name():
    from iris.voice import NameWake

    heard = []
    w = NameWake(transcribe=lambda a: heard.append(len(a)) or "Hey Iris.")
    frames = _quiet(1) + _tone(0.8) + _quiet(0.6)
    hits = [w.detect(f) for f in frames]
    assert sum(hits) == 1 and len(heard) == 1
    assert heard[0] >= len(_tone(0.8)) * FRAME + 2 * FRAME  # the speech plus lead-in frames before it
    w.transcribe = lambda a: "I bought a new phone"
    assert not any(w.detect(f) for f in _tone(0.8) + _quiet(0.6))


def test_name_wake_skips_long_speech_and_stays_quiet_on_silence():
    from iris.voice import NameWake

    calls = []
    w = NameWake(transcribe=lambda a: calls.append(1) or "Iris", max_seconds=1.0)
    assert not any(w.detect(f) for f in _quiet(2))
    assert not any(w.detect(f) for f in _tone(2.5) + _quiet(0.6))  # a long sentence is not a wake word
    assert calls == []
    assert any(w.detect(f) for f in _tone(0.3) + _quiet(0.6))  # lead-in + speech + silence fit in 1 s


def test_make_wake_picks_the_name_or_an_openwakeword_model(monkeypatch):
    from iris.voice import NameWake, make_wake

    monkeypatch.setenv("IRIS_WAKEWORD", "iris")
    assert isinstance(make_wake(), NameWake)
    assert isinstance(make_wake("Hey_Iris"), NameWake)
    assert isinstance(make_wake("hey_jarvis"), WakeWord)
    assert make_wake("iris", vad_threshold=0.05).threshold == 0.05
    monkeypatch.delenv("IRIS_WAKEWORD")
    assert make_wake().name == "hey_jarvis"


def test_name_wake_loads_tiny_whisper_once_with_iris_as_hotword(monkeypatch):
    import sys
    import types

    from iris.voice import NameWake

    made, seen = [], []

    class FakeModel:
        def __init__(self, size, **kw):
            made.append(size)

        def transcribe(self, audio, **kw):
            seen.append(kw)
            return [types.SimpleNamespace(text=" Iris ")], None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.delenv("IRIS_WAKE_WHISPER", raising=False)
    w = NameWake()
    assert w.transcribe(np.zeros(10, dtype=np.float32)) == "Iris"
    w.transcribe(np.zeros(10, dtype=np.float32))
    assert made == ["tiny"] and seen[0] == {"hotwords": "Iris"}


# --- Piper and Whisper choice ----------------------------------------------------

FAKE_PIPER = """import sys, wave
from pathlib import Path
out = Path(sys.argv[sys.argv.index("-d") + 1])
for i, line in enumerate(sys.stdin):
    with wave.open(str(out / f"{i}.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(bytes(200 * len(line.strip())))
"""


def test_piper_runs_as_its_own_program_and_returns_one_wav_per_text(tmp_path, monkeypatch):
    import wave

    from iris.voice import PiperProcess

    (tmp_path / "piper").mkdir()
    (tmp_path / "piper" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "piper" / "__main__.py").write_text(FAKE_PIPER, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))  # the fake piper wins over an installed one
    p = PiperProcess(tmp_path / "voice.onnx")
    try:
        first, second = p.wav("Hallo daar."), p.wav("Tweede\nregel")
        assert first != second
        with wave.open(str(second), "rb") as w:
            assert w.getnframes() == 100 * len("Tweede regel")  # the newline became a space: one line, one WAV
    finally:
        p.proc.stdin.close()
        p.proc.wait(timeout=10)


def test_wav_complete_only_after_the_header_has_frames(tmp_path):
    import wave

    from iris.voice import _wav_complete

    (tmp_path / "half.wav").write_bytes(b"RIFF")
    assert not _wav_complete(tmp_path / "half.wav")
    with wave.open(str(tmp_path / "done.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 10)
    assert _wav_complete(tmp_path / "done.wav")


def test_piper_picks_the_voice_by_language_and_follows_the_user_for_short_answers(tmp_path, monkeypatch):
    import iris.voice as v

    used = []

    class FakeVoice:
        def __init__(self, lang):
            self.lang = lang

        def wav(self, text):
            used.append((self.lang, text))
            return tmp_path / "x.wav"

    spk = Speaker(engine="sapi")
    spk.engine = "piper"
    monkeypatch.setattr(spk, "_piper_voice", lambda lang: FakeVoice(lang))
    monkeypatch.setattr(v, "_play_wav", lambda path, stop: True)
    assert spk.speak("Het is nu tien over twaalf, neem een paraplu mee.")
    assert spk.speak("It is ten past twelve, take an umbrella with you.")
    spk.heard("Hoe laat is het nu eigenlijk?")
    assert spk.speak("Klaar.")
    assert [lang for lang, _ in used] == ["nl", "en", "nl"]


def test_piper_missing_voice_falls_back_to_sapi(tmp_path, monkeypatch, capsys):
    import iris.voice as v

    monkeypatch.setattr(v, "PIPER_DIR", tmp_path)
    spk = Speaker(engine="sapi")
    spk.engine = "piper"
    monkeypatch.setitem(sys.modules, "pyttsx3", types.SimpleNamespace(init=lambda: types.SimpleNamespace(
        say=lambda t: None, runAndWait=lambda: None)))
    assert spk.speak("Het is nu tien over twaalf.") is True
    out = capsys.readouterr().out
    assert "python -m piper.download_voices nl_NL-ronnie-medium" in out and spk.engine == "sapi"


def test_whisper_uses_turbo_on_the_gpu_and_small_on_the_cpu(monkeypatch):
    import iris.voice as v

    built = []

    class FakeModel:
        def __init__(self, name, device=None, compute_type=None, **kw):
            built.append((name, device, compute_type, kw))

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(v, "_cuda_dlls", lambda: None)
    v._whisper_model(None, None)
    monkeypatch.setattr(v, "_cuda_available", lambda: True)
    monkeypatch.setenv("IRIS_WHISPER_GPU", "1")
    v._whisper_model(None, None)
    v._whisper_model("medium", "cpu")
    assert built == [("small", "cpu", "int8", {}), ("large-v3-turbo", "cuda", "float16", {"device_index": 1}),
                     ("medium", "cpu", "int8", {})]
