"""Branch tests for iris/voice.py that need no mic, no Whisper, no Piper and no sound device."""

import builtins
import queue
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from iris import voice

np = pytest.importorskip("numpy")  # synthetic-audio tests need real numpy; CI installs only [dev]

_REAL_CUDA_AVAILABLE = voice._cuda_available  # the real check, captured before conftest's autouse guard replaces it


def test_is_yes_returns_false_when_there_was_no_answer():
    assert voice.is_yes(None) is False


def test_cuda_available_is_true_when_the_gpus_and_dlls_load(monkeypatch):
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    ctranslate2 = types.ModuleType("ctranslate2")
    ctranslate2.get_cuda_device_count = lambda: 1
    monkeypatch.setitem(sys.modules, "ctranslate2", ctranslate2)
    loaded = []
    fake_ctypes = types.SimpleNamespace(WinDLL=lambda name: loaded.append(name))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(voice, "_cuda_dlls", lambda: None)  # no real DLL directories in tests
    assert _REAL_CUDA_AVAILABLE() is True
    assert loaded == ["cublas64_12.dll", "cudnn64_9.dll"]


def test_cuda_available_is_false_without_a_gpu(monkeypatch):
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    ctranslate2 = types.ModuleType("ctranslate2")
    ctranslate2.get_cuda_device_count = lambda: 0
    monkeypatch.setitem(sys.modules, "ctranslate2", ctranslate2)
    assert _REAL_CUDA_AVAILABLE() is False


def test_cuda_available_is_false_when_ctranslate2_is_missing(monkeypatch):
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    monkeypatch.setitem(sys.modules, "ctranslate2", None)  # import raises ImportError
    assert _REAL_CUDA_AVAILABLE() is False


def test_chime_swallows_sounddevice_failures(monkeypatch):
    monkeypatch.delenv("IRIS_CHIME", raising=False)
    monkeypatch.setitem(sys.modules, "sounddevice", None)  # import raises ImportError
    s = voice.VoiceSession.__new__(voice.VoiceSession)  # chime uses no instance state
    s.chime()  # must not raise: listening goes on without the chime


def test_chime_stops_when_the_sound_device_stalls(monkeypatch):
    stopped = []
    gate = threading.Event()  # never set: the device never finishes playing

    sd = types.ModuleType("sounddevice")
    sd.play = lambda tone, rate: None
    sd.wait = gate.wait  # blocks until the (never-set) event
    sd.stop = lambda: stopped.append(1)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setenv("IRIS_CHIME", "1")  # conftest mutes it for the whole run
    s = voice.VoiceSession.__new__(voice.VoiceSession)
    t0 = time.monotonic()
    s.chime()
    assert time.monotonic() - t0 < 1.5  # a stalled device must never hold listening forever
    assert stopped == [1]


def test_chime_does_not_stop_a_normal_tone(monkeypatch):
    stopped = []
    played = []

    sd = types.ModuleType("sounddevice")

    def _play(tone, rate, **kwargs):
        played.append((rate, kwargs))

    sd.play = _play
    sd.wait = lambda: None  # the tone finishes at once
    sd.stop = lambda: stopped.append(1)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setenv("IRIS_CHIME", "1")  # conftest mutes it for the whole run
    s = voice.VoiceSession.__new__(voice.VoiceSession)
    s.chime()
    assert played == [(22050, {})]  # non-blocking play, then wait for the tone to finish
    assert stopped == []


def test_record_calls_the_speakers_heard_hook():
    s = voice.VoiceSession.__new__(voice.VoiceSession)  # no mic, no wake word, no Whisper
    heard = []

    class _Speaker:
        def heard(self, text):
            heard.append(text)

    sent = []

    class _Overlay:
        def send(self, *args):
            sent.append(args)

    s.cancelled = threading.Event()
    s._frames = queue.Queue()
    s.silence_seconds = 0.1
    s.vad_threshold = 0.01
    s.max_record_seconds = 5
    s.speaker = _Speaker()
    s.overlay = _Overlay()
    s._transcribe = lambda audio: "hello there"
    frame_len = int(voice.RATE * 0.1)  # one silence run of exactly silence_samples
    s._frames.put(np.full(frame_len, 0.5, dtype="float32"))  # voiced
    s._frames.put(np.zeros(frame_len, dtype="float32"))      # then enough silence to stop
    assert s._record([]) == "hello there"
    assert heard == ["hello there"]
    assert sent == [("heard", "hello there")]


def test_piper_wav_raises_when_the_process_died(tmp_path):
    p = voice.PiperProcess.__new__(voice.PiperProcess)  # no real `python -m piper` process
    p.dir = tmp_path / "p"
    p.dir.mkdir()

    class _Stdin:
        def write(self, text):
            pass

        def flush(self):
            pass

    p.proc = types.SimpleNamespace(stdin=_Stdin(), poll=lambda: 0)
    with pytest.raises(RuntimeError, match="piper stopped"):
        p.wav("hi")


def test_piper_wav_times_out_when_no_wav_appears(tmp_path):
    p = voice.PiperProcess.__new__(voice.PiperProcess)
    p.dir = tmp_path / "p"
    p.dir.mkdir()

    class _Stdin:
        def write(self, text):
            pass

        def flush(self):
            pass

    proc = types.SimpleNamespace(stdin=_Stdin(), poll=lambda: None)  # alive, but never writes a WAV
    p.proc = proc
    with pytest.raises(TimeoutError, match="piper took too long"):
        p.wav("hi", timeout=0.1)


def test_claim_returns_none_when_the_wav_is_truncated(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")  # not a finished WAV: wave.open fails while Piper still writes it
    assert voice._claim(wav, tmp_path / "done") is None


def test_vox_next_times_out_when_no_chunk_arrives():
    v = voice.VoxProcess.__new__(voice.VoxProcess)  # no real VoxCPM worker process
    v._frames = queue.Queue()
    with pytest.raises(TimeoutError, match="VoxCPM took too long"):
        v._next(timeout=0.05)


def test_vox_stream_times_out_when_the_worker_never_starts():
    v = voice.VoxProcess.__new__(voice.VoxProcess)
    v.ready = threading.Event()  # never set: the worker never reported its sample rate
    v.startup = 0.05
    with pytest.raises(TimeoutError, match="VoxCPM did not start"):
        list(v.stream("hi"))


def test_play_stream_starts_output_after_all_chunks_are_made(monkeypatch):
    started = []

    class _Stream:
        latency = 0.0

        def __init__(self, **kwargs):
            pass

        def start(self):
            started.append(1)

        def abort(self):
            pass

        def close(self):
            pass

    sd = types.ModuleType("sounddevice")
    sd.OutputStream = _Stream  # never calls the callback: the device "stalls" right after starting
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    chunk = np.zeros(int(voice.RATE * 0.05), dtype="float32")
    assert voice._play_stream([chunk], voice.RATE, 0.05, None, slack=0.0) is True
    assert started == [1]


def test_play_stream_stops_for_a_wake_word_while_draining(monkeypatch):
    stop = threading.Event()

    class _Stream:
        latency = 0.0

        def __init__(self, **kwargs):
            pass

        def start(self):
            stop.set()  # a wake word lands the moment playback starts

        def abort(self):
            pass

        def close(self):
            pass

    sd = types.ModuleType("sounddevice")
    sd.OutputStream = _Stream
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    chunk = np.zeros(int(voice.RATE * 0.05), dtype="float32")
    assert voice._play_stream([chunk], voice.RATE, 0.05, stop, slack=0.0) is False


def test_piper_voice_is_built_once_and_cached(tmp_path, monkeypatch):
    s = voice.Speaker.__new__(voice.Speaker)
    s._piper = {}
    lang = next(iter(voice.PIPER_VOICES))
    model = tmp_path / f"{voice.PIPER_VOICES[lang]}.onnx"
    model.write_bytes(b"x")
    made = []

    def fake_piper_process(path):
        made.append(path)
        return object()

    monkeypatch.setattr(voice, "PIPER_DIR", tmp_path)
    monkeypatch.setattr(voice, "PiperProcess", fake_piper_process)  # no real Piper process
    first = s._piper_voice(lang)
    second = s._piper_voice(lang)
    assert first is second and made == [model]


def test_wait_for_speech_stops_a_stuck_thread(monkeypatch):
    s = voice.VoiceSession.__new__(voice.VoiceSession)  # no mic, no wake word, no Whisper
    stop = threading.Event()
    s._stop = stop
    s._interrupted = False

    class _Stuck:
        def join(self, timeout=None):
            pass  # returns at once; the thread is still "alive"

        def is_alive(self):
            return True

    s._speech = _Stuck()
    monkeypatch.setattr(voice, "SPEECH_TIMEOUT", 0.01)
    assert s.wait_for_speech() is False
    assert stop.is_set()  # the stuck speech must be stopped, not talked over by the next answer
    assert s._speech is None


def test_push_to_talk_without_a_console_says_so(monkeypatch, capsys):
    v = voice.Voice.__new__(voice.Voice)  # no Whisper model load

    def _no_stdin():
        raise EOFError  # the desktop app and pipes have no console to press Enter in

    monkeypatch.setattr(builtins, "input", _no_stdin)

    class _Stream:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    sd = types.ModuleType("sounddevice")
    sd.InputStream = _Stream
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    t0 = time.monotonic()
    text = v.listen()
    assert time.monotonic() - t0 < 1.5  # no console: it must not wait for an Enter that never comes
    assert text == ""
    out = capsys.readouterr().out
    assert "Push-to-talk needs a console window; use the wake word instead." in out


def test_piper_env_is_utf8_with_and_without_a_runtime_folder(tmp_path, monkeypatch):
    envs = []

    class _Popen:
        def __init__(self, args, **kwargs):
            envs.append(kwargs.get("env"))  # no real Piper process

    def _mkdtemp(prefix=""):
        d = tmp_path / "piper-tmp"
        d.mkdir(exist_ok=True)
        return str(d)

    monkeypatch.setattr(voice.subprocess, "Popen", _Popen)
    monkeypatch.setattr(voice.tempfile, "mkdtemp", _mkdtemp)  # no temp folder outside tmp_path
    monkeypatch.delenv("PYTHONPATH", raising=False)
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(voice, "piper_runtime", lambda: runtime)

    voice.PiperProcess(tmp_path / "model.onnx")  # no runtime folder yet
    env = envs[0]
    assert env is not None and env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUTF8"] == "1"
    assert "PYTHONPATH" not in env

    runtime.mkdir()
    voice.PiperProcess(tmp_path / "model.onnx")  # now the runtime folder exists
    env = envs[1]
    assert env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUTF8"] == "1"
    assert env["PYTHONPATH"].startswith(str(runtime))


# --- one bad sentence must not switch the voice for the rest of the run -------------------------------------------

def _bare_speaker(engine):
    s = voice.Speaker.__new__(voice.Speaker)  # no model loading in __init__
    s.engine = engine
    s._kokoro = None
    s._piper = {}
    s._vox = None
    s._lang = "en"
    s._failures = {}
    return s


def _fake_pyttsx3(monkeypatch):
    class _Engine:
        def say(self, text):
            pass

        def runAndWait(self):
            pass

    mod = types.ModuleType("pyttsx3")
    mod.init = lambda: _Engine()  # no real SAPI or desktop
    monkeypatch.setitem(sys.modules, "pyttsx3", mod)


class _ScriptedPiper:
    """A PiperProcess whose wav() follows a script of True (ok) / False (fail)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def wav(self, text, timeout=20):
        ok = self.outcomes[self.calls] if self.calls < len(self.outcomes) else True
        self.calls += 1
        if not ok:
            raise RuntimeError("piper boom")
        return Path("fake.wav")


def test_piper_one_failure_keeps_the_engine(monkeypatch):
    s = _bare_speaker("piper")
    s._piper["en"] = _ScriptedPiper([False, True])  # the first answer fails, the next works
    _fake_pyttsx3(monkeypatch)
    monkeypatch.setattr(voice, "_play_wav", lambda path, stop_event, slack=3.0: True)  # no real audio

    s.speak("one")   # Piper fails once: SAPI says this one answer only
    s.speak("two")   # Piper works again
    assert s.engine == "piper"


def test_piper_three_failures_in_a_row_switch_to_sapi(monkeypatch, capsys):
    s = _bare_speaker("piper")
    s._piper["en"] = _ScriptedPiper([False, False, False])  # keeps failing
    _fake_pyttsx3(monkeypatch)
    monkeypatch.setattr(voice, "_play_wav", lambda path, stop_event, slack=3.0: True)

    for i in range(3):
        s.speak(f"answer {i}")
    assert s.engine == "sapi"  # only after three failures in a row does the voice switch
    out = capsys.readouterr().out
    assert "Piper failed 3 times; using sapi from now on." in out


def test_piper_fail_work_fail_fail_stays_on_piper(monkeypatch):
    s = _bare_speaker("piper")
    s._piper["en"] = _ScriptedPiper([False, True, False, False])  # the success breaks the streak
    _fake_pyttsx3(monkeypatch)
    monkeypatch.setattr(voice, "_play_wav", lambda path, stop_event, slack=3.0: True)

    for i in range(4):
        s.speak(f"answer {i}")
    assert s.engine == "piper"  # never three failures in a row


class _ScriptedStream:
    """A stand-in for voice._play_stream following a script of True (ok) / False (fail)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, chunks, rate, expected, stop_event, slack=3.0):
        ok = self.outcomes[self.calls] if self.calls < len(self.outcomes) else True
        self.calls += 1
        if not ok:
            raise RuntimeError("vox boom")
        return True


class _FakeVox:
    def __init__(self):
        self.ready = threading.Event()
        self.ready.set()  # loaded, so it takes the ready path rather than delegating to Piper
        self.rate = 22050

    def stream(self, text):
        return iter([])


def _vox_speaker(monkeypatch, outcomes):
    s = _bare_speaker("voxcpm")
    s._vox = _FakeVox()
    s._piper["en"] = _ScriptedPiper([True] * 10)  # the Piper fallback always works here
    monkeypatch.setattr(voice, "_play_stream", _ScriptedStream(outcomes))  # no real audio
    monkeypatch.setattr(voice, "_play_wav", lambda path, stop_event, slack=3.0: True)
    return s


def test_voxcpm_one_failure_keeps_the_engine(monkeypatch):
    s = _vox_speaker(monkeypatch, [False, True])  # the first answer fails, the next works
    s.speak("one")   # VoxCPM fails once: Piper says this one answer only
    s.speak("two")   # VoxCPM works again
    assert s.engine == "voxcpm"


def test_voxcpm_three_failures_in_a_row_switch_to_piper(monkeypatch, capsys):
    s = _vox_speaker(monkeypatch, [False, False, False])  # keeps failing
    for i in range(3):
        s.speak(f"answer {i}")
    assert s.engine == "piper"  # only after three failures in a row does the voice switch
    out = capsys.readouterr().out
    assert "VoxCPM failed 3 times; using piper from now on." in out


def test_voxcpm_fail_work_fail_fail_stays_on_voxcpm(monkeypatch):
    s = _vox_speaker(monkeypatch, [False, True, False, False])  # the success breaks the streak
    for i in range(4):
        s.speak(f"answer {i}")
    assert s.engine == "voxcpm"  # never three failures in a row


def test_speaker_initializes_failure_counts():
    s = voice.Speaker(engine="sapi")  # SAPI loads nothing in __init__, so no processes start
    assert s._failures == {} and s.engine == "sapi"

