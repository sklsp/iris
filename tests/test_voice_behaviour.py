"""Behavioural tests for iris/voice.py: lazy model loading, push-to-talk listen(),
the VoiceSession stream lifecycle and the SAPI fallback paths.

openwakeword, faster_whisper, sounddevice and pyttsx3 are all faked in sys.modules,
so nothing is downloaded, no mic is opened and no audio device is touched."""

import queue
import sys
import threading
import types

import numpy as np

from iris.voice import FRAME, RATE, Speaker, Voice, VoiceSession, WakeWord

# --- openwakeword fake -------------------------------------------------------


class _FakeOWModel:
    def __init__(self, wakeword_models=None):
        self.wakeword_models = list(wakeword_models or [])


def _fake_openwakeword(monkeypatch, models, downloads):
    fake = types.ModuleType("openwakeword")
    fake.MODELS = models

    class _Utils:
        @staticmethod
        def download_models(names):
            downloads.append(list(names))

    fake.utils = _Utils()
    fake.Model = _FakeOWModel
    monkeypatch.setitem(sys.modules, "openwakeword", fake)


def test_wake_word_model_resolves_builtin_name_to_onnx(tmp_path, monkeypatch):
    tflite = tmp_path / "hey_jarvis.tflite"
    onnx = tmp_path / "hey_jarvis.onnx"
    onnx.write_bytes(b"x")  # already present: no download may happen
    downloads = []
    _fake_openwakeword(monkeypatch, {"hey_jarvis": {"model_path": str(tflite)}}, downloads)

    w = WakeWord(name="hey_jarvis")
    m = w._model()

    assert isinstance(m, _FakeOWModel)
    assert m.wakeword_models == [str(onnx)]  # .tflite swapped for the ONNX variant
    assert downloads == []


def test_wake_word_model_downloads_when_onnx_missing(tmp_path, monkeypatch):
    tflite = tmp_path / "hey_jarvis.tflite"
    downloads = []
    _fake_openwakeword(monkeypatch, {"hey_jarvis": {"model_path": str(tflite)}}, downloads)

    w = WakeWord(name="hey_jarvis")
    m = w._model()

    assert m.wakeword_models == [str(tmp_path / "hey_jarvis.onnx")]
    assert downloads == [["hey_jarvis"]]  # built-in name passed to the downloader


def test_wake_word_model_custom_onnx_path_skips_the_downloader(tmp_path, monkeypatch):
    custom = tmp_path / "custom.onnx"
    custom.write_bytes(b"x")
    downloads = []
    _fake_openwakeword(monkeypatch, {}, downloads)

    w = WakeWord(name=str(custom))
    m = w._model()

    assert m.wakeword_models == [str(custom)]  # used as-is, no .tflite swap possible
    assert downloads == []


def test_wake_word_model_unknown_missing_path_downloads_nothing(tmp_path, monkeypatch):
    downloads = []
    _fake_openwakeword(monkeypatch, {}, downloads)

    w = WakeWord(name=str(tmp_path / "missing.onnx"))
    m = w._model()

    assert m.wakeword_models == [str(tmp_path / "missing.onnx")]
    assert downloads == [[]]  # not a built-in name, so the downloader gets no names


def test_wake_word_model_is_loaded_once(tmp_path, monkeypatch):
    tflite = tmp_path / "hey_jarvis.tflite"
    onnx = tmp_path / "hey_jarvis.onnx"
    onnx.write_bytes(b"x")
    _fake_openwakeword(monkeypatch, {"hey_jarvis": {"model_path": str(tflite)}}, [])

    w = WakeWord(name="hey_jarvis")
    assert w._model() is w._model()


# --- faster_whisper fake -----------------------------------------------------


class _Seg:
    def __init__(self, text):
        self.text = text


class _FakeWhisperModel:
    def __init__(self, name=None, device=None, compute_type=None):
        self.init_args = (name, device, compute_type)
        self.transcribed = []

    def transcribe(self, audio, vad_filter=False):
        self.transcribed.append((audio, vad_filter))
        return [_Seg("  one "), _Seg("two")], None


def _fake_faster_whisper(monkeypatch, model_cls):
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = model_cls
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)


# --- sounddevice fake --------------------------------------------------------


class _FakeStream:
    """Stands in for sounddevice.InputStream; records kwargs and the callback."""

    def __init__(self, samplerate=None, channels=None, dtype=None, blocksize=None, callback=None):
        self.kwargs = dict(samplerate=samplerate, channels=channels, dtype=dtype, blocksize=blocksize)
        self.callback = callback
        self.exit_calls = []
        self.raise_on_exit = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exit_calls.append(exc)
        if self.raise_on_exit:
            raise RuntimeError("device gone")


def _fake_sounddevice(monkeypatch, stream_cls=_FakeStream):
    fake = types.ModuleType("sounddevice")
    fake.InputStream = stream_cls
    monkeypatch.setitem(sys.modules, "sounddevice", fake)


class _StubSpeaker:
    def speak(self, text, stop_event=None):
        return True

# --- Voice (push-to-talk) ----------------------------------------------------


def test_voice_init_builds_whisper_with_int8_on_cpu(monkeypatch):
    built = []

    class Ctor(_FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(name, device, compute_type)
            built.append(self)

    _fake_faster_whisper(monkeypatch, Ctor)
    import iris.voice as v

    monkeypatch.setattr(v, "WHISPER_MODEL", "tiny")
    monkeypatch.setattr(v, "WHISPER_DEVICE", "cpu")

    Voice()

    assert len(built) == 1
    assert built[0].init_args == ("tiny", "cpu", "int8")


def test_voice_init_uses_float16_when_the_device_is_cuda(monkeypatch):
    built = []

    class Ctor(_FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(name, device, compute_type)
            built.append(self)

    _fake_faster_whisper(monkeypatch, Ctor)
    import iris.voice as v

    monkeypatch.setattr(v, "WHISPER_MODEL", "tiny")
    monkeypatch.setattr(v, "WHISPER_DEVICE", "cuda")

    Voice()

    assert built[0].init_args == ("tiny", "cuda", "float16")


def test_voice_listen_records_frames_and_transcribes_them(monkeypatch):
    built = []

    class Ctor(_FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(name, device, compute_type)
            built.append(self)

    _fake_faster_whisper(monkeypatch, Ctor)
    import iris.voice as v

    monkeypatch.setattr(v, "WHISPER_MODEL", "tiny")
    monkeypatch.setattr(v, "WHISPER_DEVICE", "cpu")

    frames = [np.full((FRAME, 1), level, dtype="float32") for level in (0.1, 0.3, 0.5)]
    streams = []

    class Stream(_FakeStream):
        def __init__(self, **kw):
            super().__init__(**kw)
            streams.append(self)

        def __enter__(self):
            for f in frames:  # the audio thread delivers three buffers
                self.callback(f, len(f), None, None)
            return self

    _fake_sounddevice(monkeypatch, Stream)
    monkeypatch.setattr("builtins.input", lambda: "")  # Jay presses Enter right away

    text = Voice().listen()

    assert text == "one two"  # segments stripped and joined with single spaces
    s = streams[0]
    assert s.kwargs["samplerate"] == RATE
    assert s.kwargs["channels"] == 1
    assert s.kwargs["dtype"] == "float32"
    audio, vad = built[0].transcribed[0]
    expected = np.concatenate([np.full(FRAME, lv) for lv in (0.1, 0.3, 0.5)]).astype("float32")
    assert np.array_equal(audio, expected)  # mono column of every buffer, in order
    assert vad is True


def test_voice_listen_returns_empty_string_when_no_audio_arrived(monkeypatch):
    built = []

    class Ctor(_FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(name, device, compute_type)
            built.append(self)

    _fake_faster_whisper(monkeypatch, Ctor)
    import iris.voice as v

    monkeypatch.setattr(v, "WHISPER_MODEL", "tiny")
    monkeypatch.setattr(v, "WHISPER_DEVICE", "cpu")
    _fake_sounddevice(monkeypatch)  # the stream never delivers a single buffer
    monkeypatch.setattr("builtins.input", lambda: "")

    assert Voice().listen() == ""
    assert built[0].transcribed == []  # nothing to transcribe


# --- VoiceSession stream lifecycle --------------------------------------------


def test_session_enter_opens_the_stream_and_queues_frames(monkeypatch):
    _fake_sounddevice(monkeypatch)
    s = VoiceSession(speaker=_StubSpeaker())
    with s:
        indata = np.full((FRAME, 1), 0.25, dtype="float32")
        s._stream.callback(indata, FRAME, None, None)
        assert s._frames.qsize() == 1
        got = s._frames.get_nowait()

    assert np.array_equal(got, indata[:, 0])  # mono column copied out of the buffer
    st = s._stream
    assert st.kwargs["samplerate"] == RATE
    assert st.kwargs["blocksize"] == FRAME


def test_session_enter_drops_the_oldest_frame_when_the_queue_is_full(monkeypatch):
    _fake_sounddevice(monkeypatch)
    s = VoiceSession(speaker=_StubSpeaker())
    with s:
        for i in range(1024):  # maxsize of the frame queue
            s._frames.put(np.full(FRAME, float(i % 7), dtype="float32"))
        new = np.full((FRAME, 1), 9.0, dtype="float32")
        s._stream.callback(new, FRAME, None, None)
        assert s._frames.qsize() == 1024  # bounded: one in, one out
        items = [s._frames.get_nowait() for _ in range(1024)]

    assert np.all(items[0] == 1.0)  # the oldest frame (value 0) was dropped
    assert np.all(items[-1] == 9.0)  # the new one landed at the end


def test_session_exit_closes_the_stream(monkeypatch):
    _fake_sounddevice(monkeypatch)
    s = VoiceSession(speaker=_StubSpeaker())
    with s:
        pass

    assert s._stream.exit_calls == [(None, None, None)]


def test_session_exit_swallows_errors_from_closing_the_mic():
    s = VoiceSession(speaker=_StubSpeaker())
    st = _FakeStream()
    st.raise_on_exit = True
    s._stream = st

    err = ValueError("boom")
    s.__exit__(err, ValueError, None)  # must not raise: the real error keeps propagating

    assert st.exit_calls == [(err, ValueError, None)]


def test_session_exit_is_a_noop_when_the_stream_never_opened():
    s = VoiceSession(speaker=_StubSpeaker())
    assert s._stream is None
    s.__exit__(None, None, None)  # nothing to close


def test_session_enter_survives_a_race_between_drop_and_reput(monkeypatch):
    """If another consumer empties the queue between the Full and the drop (and refills it before
    the re-put), both defensive excepts fire and the frame is simply lost - no exception escapes."""
    _fake_sounddevice(monkeypatch)
    s = VoiceSession(speaker=_StubSpeaker())
    with s:
        q = s._frames
        for _ in range(1024):  # fill to capacity so put_nowait raises Full
            q.put(np.zeros(FRAME, dtype="float32"))

        def racy_get():
            raise queue.Empty  # the last frame was grabbed by a consumer in between

        monkeypatch.setattr(q, "get_nowait", racy_get)
        s._stream.callback(np.full((FRAME, 1), 5.0, dtype="float32"), FRAME, None, None)
        assert q.qsize() == 1024  # the new frame was dropped; nothing raised


# --- next_request / next_command ----------------------------------------------


class _FakeWake:
    def __init__(self, misses):
        self.misses = misses
        self.seen = []

    def detect(self, frame):
        self.seen.append(len(frame))
        return len(self.seen) > self.misses  # the first `misses` frames are not it


def test_next_request_waits_for_the_wake_word_before_recording(monkeypatch):
    s = VoiceSession(speaker=_StubSpeaker())
    wake = _FakeWake(misses=2)  # two non-wake frames, then the hit
    s.wake = wake
    commands = []

    def fake_next_command():
        commands.append("cmd")
        return "the command"

    monkeypatch.setattr(s, "next_command", fake_next_command)
    for _ in range(3):
        s._frames.put(np.zeros(FRAME, dtype="float32"))

    assert s.next_request() == "the command"
    assert wake.seen == [FRAME, FRAME, FRAME]  # frames consumed one by one until the hit
    assert commands == ["cmd"]


class _Clock:
    """Deterministic stand-in for time.time(): every call advances half a second."""

    def __init__(self):
        self.calls = 0

    def time(self):
        self.calls += 1
        return 1000.0 + 0.5 * self.calls


def test_next_command_stops_at_the_hard_cap_even_while_speech_continues(monkeypatch):
    import iris.voice as v

    s = VoiceSession(max_record_seconds=30, speaker=_StubSpeaker())
    captured = {}

    def fake_transcribe(audio):
        captured["audio"] = audio
        return "capped"

    monkeypatch.setattr(s, "_transcribe", fake_transcribe)
    clock = _Clock()
    monkeypatch.setattr(v, "time", clock)  # 0.5 s per call: the cap is hit after ~61 frames

    stop = threading.Event()

    def feeder():  # the mic keeps delivering speech while recording runs (next_command drains first)
        while not stop.is_set():
            try:
                s._frames.put_nowait(np.full(FRAME, 0.3, dtype="float32"))
            except queue.Full:  # consumer is keeping up; nothing to drop on the test side
                pass

    t = threading.Thread(target=feeder, daemon=True)
    t.start()
    try:
        assert s.next_command() == "capped"  # returns at all only because the cap cut it off
    finally:
        stop.set()
        t.join(timeout=5)

    audio = captured["audio"]
    assert len(audio) > 0 and np.all(audio == 0.3)  # continuous speech was recorded...
    assert len(audio) <= int(31 * RATE) + FRAME  # ...but bounded by the cap, not by silence


# --- _transcribe ---------------------------------------------------------------


def test_session_transcribe_builds_whisper_lazily_and_joins_segments(monkeypatch):
    built = []

    class Ctor(_FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(name, device, compute_type)
            built.append(self)

    _fake_faster_whisper(monkeypatch, Ctor)
    import iris.voice as v

    monkeypatch.setattr(v, "WHISPER_DEVICE", "cpu")

    s = VoiceSession(whisper_model="tiny-test", speaker=_StubSpeaker())
    audio = np.arange(160, dtype="float32")
    assert s._transcribe(audio) == "one two"  # stripped and joined with single spaces

    m = built[0]
    assert m.init_args == ("tiny-test", "cpu", "int8")
    got_audio, vad = m.transcribed[0]
    assert np.array_equal(got_audio, audio)
    assert vad is True


def test_session_transcribe_reuses_the_loaded_model(monkeypatch):
    built = []

    class Ctor(_FakeWhisperModel):
        def __init__(self, name, device=None, compute_type=None):
            super().__init__(name, device, compute_type)
            built.append(self)

    _fake_faster_whisper(monkeypatch, Ctor)
    import iris.voice as v

    monkeypatch.setattr(v, "WHISPER_DEVICE", "cpu")

    s = VoiceSession(whisper_model="tiny-test", speaker=_StubSpeaker())
    assert s._transcribe(np.zeros(FRAME, dtype="float32")) == "one two"
    assert s._transcribe(np.ones(FRAME, dtype="float32")) == "one two"

    assert len(built) == 1  # the model is constructed once and reused


# --- Speaker SAPI fallback ------------------------------------------------------


def test_speaker_sapi_survives_a_broken_pyttsx3(monkeypatch):
    fake = types.ModuleType("pyttsx3")

    def boom():
        raise RuntimeError("no SAPI on this machine")

    fake.init = boom
    monkeypatch.setitem(sys.modules, "pyttsx3", fake)

    assert Speaker(engine="sapi").speak("hello") is True  # printed instead of spoken


# --- module level speak() --------------------------------------------------------


def test_module_level_speak_creates_and_caches_a_speaker(monkeypatch):
    import iris.voice as v

    said = []

    class FakeEngine:
        def say(self, t):
            said.append(t)

        def runAndWait(self):
            pass

    fake_pyttsx3 = types.ModuleType("pyttsx3")
    fake_pyttsx3.init = lambda: FakeEngine()
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_pyttsx3)
    monkeypatch.delenv("IRIS_TTS", raising=False)
    monkeypatch.setattr(v, "_speaker", None)

    assert v.speak("hello") is True
    first = v._speaker
    assert isinstance(first, Speaker) and first.engine == "sapi"
    assert v.speak("again") is True
    assert v._speaker is first  # created once, reused afterwards
    assert said == ["hello", "again"]


