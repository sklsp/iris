"""The real voxcpm_worker.py through its own protocol, in a child process with fake soundfile and voxcpm packages.
The worker redirects its own file descriptors and needs torch, so it can only be tested as a separate program."""

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent / "iris" / "voxcpm_worker.py"

FAKE_SOUNDFILE = '''\
def write(path, audio, rate):
    open(path, "wb").write(b"RIFF")  # the voice file only has to exist for the worker
'''

FAKE_VOXCPM = '''\
import os


class _Param:
    dtype = "float32"


class _LM:
    def parameters(self):
        yield _Param()

    def setup_cache(self, *args):
        pass


class _TTS:
    sample_rate = 16000
    device = "cpu"
    base_lm = _LM()
    residual_lm = _LM()


class VoxCPM:
    tts_model = _TTS()

    @classmethod
    def from_pretrained(cls, name, load_denoiser=False):
        return cls()

    def generate(self, **kw):
        return [0.0] * 10

    def generate_streaming(self, text, **kw):
        rec = os.environ.get("IRIS_FAKE_RECORD")
        if rec:
            with open(rec, "a", encoding="utf-8") as f:
                f.write(text + "\\n")
        if "boom" in text:
            raise RuntimeError("boom")
        yield [0.5] * 3
        yield [0.25] * 2
'''


def _fakes(tmp_path):
    """The fake soundfile and voxcpm packages the worker imports, as a folder for its PYTHONPATH."""
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "soundfile.py").write_text(FAKE_SOUNDFILE, encoding="utf-8")
    (fakes / "voxcpm.py").write_text(FAKE_VOXCPM, encoding="utf-8")
    return fakes


def _run_worker(fakes, voice_file, stdin):
    """The real worker in its own process: the fake packages on PYTHONPATH, JSON lines on stdin."""
    return subprocess.run(
        [sys.executable, str(WORKER), str(voice_file)],
        input=stdin,
        env={**os.environ, "PYTHONPATH": str(fakes)},
        capture_output=True,
        timeout=60,
    )


def _u32(data, pos):
    """The next little-endian uint32 of the protocol stream, and where it ends."""
    return struct.unpack_from("<I", data, pos)[0], pos + 4


def test_the_worker_speaks_each_answer_and_reports_a_failure(tmp_path):
    fakes = _fakes(tmp_path)
    voice_file = tmp_path / "voice" / "v.wav"
    proc = _run_worker(fakes, voice_file, b'{"text": "Hallo."}\n{"text": "boom"}\n')
    data = proc.stdout
    pos = 0
    rate, pos = _u32(data, pos)
    assert rate == 16000  # the sample rate comes first
    n, pos = _u32(data, pos)
    assert n == 3 and all(v == 0.5 for v in struct.unpack_from(f"<{n}f", data, pos))
    pos += 4 * n
    n, pos = _u32(data, pos)
    assert n == 2 and all(v == 0.25 for v in struct.unpack_from(f"<{n}f", data, pos))
    pos += 4 * n
    end, pos = _u32(data, pos)
    assert end == 0  # the first answer is done
    failed, pos = _u32(data, pos)
    assert failed == 0xFFFFFFFF  # the second one raised inside the worker
    assert pos == len(data)  # nothing after that
    assert voice_file.exists()  # the first voice was made and kept for later answers
    assert "RuntimeError: boom" in proc.stderr.decode("utf-8", "replace")


def test_a_long_answer_is_spoken_in_pieces_of_at_most_250_characters(tmp_path, monkeypatch):
    fakes = _fakes(tmp_path)
    record = tmp_path / "record.txt"
    monkeypatch.setenv("IRIS_FAKE_RECORD", str(record))  # the fake writes every piece it is asked to speak
    text = ("This is a sentence that the worker speaks out loud. " * 12)[:600]
    assert len(text) == 600
    proc = _run_worker(fakes, tmp_path / "voice" / "v.wav", (json.dumps({"text": text}) + "\n").encode("utf-8"))
    pieces_spoken = record.read_text(encoding="utf-8").splitlines()
    assert len(pieces_spoken) > 1  # one generate_streaming call per piece of the answer
    assert all(len(piece) <= 250 for piece in pieces_spoken)
    assert proc.stdout.endswith(struct.pack("<I", 0)) and struct.pack("<I", 0xFFFFFFFF) not in proc.stdout
