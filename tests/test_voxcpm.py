"""VoxCPM2 speech: the worker protocol against a fake worker program, and streamed playback against a fake device."""

import sys
import threading
import time
import types

import numpy as np
import pytest

from iris import voice

FAKE_WORKER = '''
import json, struct, sys
out = sys.stdout.buffer
out.write(struct.pack("<I", 1000)); out.flush()
for line in sys.stdin.buffer:
    text = json.loads(line)["text"]
    if text == "die":
        sys.exit(1)
    if text == "fail":
        out.write(struct.pack("<I", 0xFFFFFFFF)); out.flush(); continue
    for i, _word in enumerate(text.split()):
        out.write(struct.pack("<I", 2) + struct.pack("<2f", i, i))
    out.write(struct.pack("<I", 0)); out.flush()
'''


@pytest.fixture
def vox(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "VOXCPM_DIR", tmp_path)
    worker = tmp_path / "worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    v = voice.VoxProcess(python=voice.Path(sys.executable), worker=worker, startup=20)
    yield v
    v.proc.kill()


def test_a_worker_streams_chunks_after_telling_its_rate(vox):
    chunks = list(vox.stream("een twee drie", timeout=10))
    assert vox.rate == 1000 and [c.tolist() for c in chunks] == [[0, 0], [1, 1], [2, 2]]


def test_the_rest_of_an_answer_cut_short_is_skipped_before_the_next(vox):
    g = vox.stream("a b c d", timeout=10)
    next(g)
    g.close()  # a wake word stopped the answer after one chunk
    assert [c.tolist() for c in vox.stream("x y", timeout=10)] == [[0, 0], [1, 1]]


def test_a_failed_answer_raises_and_the_next_one_works(vox):
    with pytest.raises(RuntimeError, match="failed"):
        list(vox.stream("fail", timeout=10))
    assert len(list(vox.stream("weer goed", timeout=10))) == 2


def test_a_worker_that_stops_raises_instead_of_hanging(vox):
    with pytest.raises(RuntimeError, match="stopped"):
        list(vox.stream("die", timeout=10))


def test_without_its_python_voxcpm_says_how_to_install_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="README"):
        voice.VoxProcess(python=tmp_path / "missing.exe")


class FakeOut:
    """A sound device that pulls audio from the callback 100 frames at a time, as PortAudio does."""

    made = []

    def __init__(self, samplerate, channels, dtype, callback, pull=True):
        self.cb, self.played, self.latency, self.pull, self._run = callback, [], 0.0, pull, False
        FakeOut.last = self

    def start(self):
        FakeOut.made.append(self)
        self._run = True
        if self.pull:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._run:
            buf = np.zeros((100, 1), dtype="float32")
            self.cb(buf, 100, None, None)
            self.played.append(buf[:, 0].copy())
            time.sleep(0.001)

    def abort(self):
        self._run = False

    def close(self):
        pass


@pytest.fixture
def device(monkeypatch):
    FakeOut.made = []
    fake = types.ModuleType("sounddevice")
    fake.OutputStream = FakeOut
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


def chunks(n, started):
    for i in range(n):
        started.append(len(FakeOut.made))  # was the device already playing before this chunk was made?
        yield np.full(100, i + 1, dtype="float32")


def test_playback_waits_for_the_lead_then_plays_everything_in_order(device):
    started = []
    assert voice._play_stream(chunks(5, started), 1000, 0.25, None) is True
    assert started == [0, 0, 0, 1, 1]  # 0.25 s at 1000 Hz: the device starts once the third chunk is in
    played = np.concatenate(FakeOut.last.played)
    assert played[:500].tolist() == [v for i in range(5) for v in [i + 1] * 100]


def test_a_wake_word_stops_streamed_speech(device):
    stop = threading.Event()

    def stopping():
        for c in chunks(3, []):
            yield c
            stop.set()

    assert voice._play_stream(stopping(), 1000, 0.0, stop) is False


def test_a_stalled_device_does_not_hang_streamed_speech(device, monkeypatch, capsys):
    monkeypatch.setattr(device, "OutputStream", lambda **kw: FakeOut(**kw, pull=False))
    t = time.monotonic()
    assert voice._play_stream(chunks(3, []), 1000, 0.0, None, slack=0.1) is True
    assert time.monotonic() - t < 2 and "stalled" in capsys.readouterr().out


def test_speaker_uses_piper_when_voxcpm_is_not_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(voice, "VOXCPM_PYTHON", tmp_path / "missing.exe")
    monkeypatch.setattr(voice, "PIPER_DIR", tmp_path)
    assert voice.Speaker(engine="voxcpm").engine == "piper"
    assert "using Piper instead" in capsys.readouterr().out


def test_speaker_falls_back_to_piper_when_voxcpm_fails_mid_answer(monkeypatch):
    class Broken:
        ready, rate, startup = threading.Event(), 48000, 1

        def stream(self, text):
            raise RuntimeError("worker gone")
            yield

    Broken.ready.set()
    spk = voice.Speaker(engine="sapi")
    spk.engine, spk._vox = "voxcpm", Broken()
    said = []
    monkeypatch.setattr(spk, "_speak_piper", lambda text, stop: said.append(text) or True)
    assert spk.speak("Hallo") is True and said == ["Hallo"] and spk.engine == "piper"
