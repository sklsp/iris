"""Branch tests for iris/voice.py that need no mic, no Whisper, no Piper and no sound device."""

import queue
import sys
import threading
import types

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

