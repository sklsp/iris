"""iris.packs: optional voice packs. Every command is a fake; nothing is downloaded or installed."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from iris import config, packs, voice


@pytest.fixture
def plain(monkeypatch):
    """No NVIDIA card, nothing installed."""
    monkeypatch.setattr(packs, "_has_nvidia", lambda: False)
    for name in ("_whisper_installed", "_wake_installed", "_piper_installed", "_gpu_installed", "_voxcpm_installed"):
        monkeypatch.setattr(packs, name, lambda: False)


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_packs_on_a_pc_without_nvidia(plain):
    rows = by_id(packs.packs())
    assert [k for k, r in rows.items() if r["available"]] == ["whisper", "wake", "piper"]
    assert "non-commercial" in rows["wake"]["license"] and "GPL-3.0" in rows["piper"]["license"]
    assert not any(r["installed"] for r in rows.values())


def test_the_name_wake_word_needs_no_model_and_adds_whisper_tiny(plain, monkeypatch):
    monkeypatch.setattr(voice, "WHISPER_MODEL", None)
    config.config_path().write_text('wakeword = "iris"\n', encoding="utf-8")
    rows = by_id(packs.packs())
    assert rows["wake"]["available"] is False
    assert packs.whisper_names() == ["small", "tiny"]
    monkeypatch.setattr(packs, "_has_nvidia", lambda: True)
    assert packs.whisper_names()[0] == "large-v3-turbo"
    assert by_id(packs.packs())["voxcpm"]["available"] is True


def test_steps_keep_every_pack_in_its_own_folder(monkeypatch):
    monkeypatch.setattr(voice, "WHISPER_MODEL", "small")
    gpu = packs.steps("gpu")[0][1]
    assert gpu[:3] == [sys.executable, "-m", "pip"] and gpu[gpu.index("--target") + 1] == str(packs.gpu_dir())
    piper = packs.steps("piper")
    assert piper[0][1][piper[0][1].index("--target") + 1] == str(packs.piper_runtime())
    assert piper[1][1][:3] == [sys.executable, "-m", "piper.download_voices"]
    assert "download_model('small')" in packs.steps("whisper")[0][1][-1]
    assert "download_models(['hey_jarvis'])" in packs.steps("wake")[0][1][-1]
    vox = packs.steps("voxcpm")
    assert vox[0][1] is packs._fetch_uv and vox[1][1][1:4] == ["venv", "-p", "3.11"]
    assert vox[3][1][-1] == "https://download.pytorch.org/whl/cu124"
    with pytest.raises(KeyError):
        packs.steps("nope")


class FakeProc:
    def __init__(self, lines, code):
        self.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        self.returncode = code

    def wait(self):
        return self.returncode


class HungProc:
    """A process whose pipe never closes and which never exits, like a pip stuck on the network."""

    def __init__(self):
        self.killed = False

    @property
    def stdout(self):
        return _hang()

    def kill(self):
        self.killed = True

    def wait(self):
        return -15  # run only waits after it has killed us


def _hang():
    while True:
        time.sleep(0.02)
        yield ""


def test_run_streams_output_and_stops_at_the_first_failure(monkeypatch):
    monkeypatch.setattr(packs, "steps", lambda pack: [("one", ["a"]), ("two", ["b"]), ("three", ["c"])])
    outputs = iter([FakeProc(["fetching", ""], 0), FakeProc(["oops"], 2)])
    seen = []

    def popen(cmd, **kw):
        seen.append((cmd, kw["env"]["PYTHONUNBUFFERED"], kw["stdin"]))
        return next(outputs)

    job = packs.Job("whisper")
    packs.run(job, popen)
    assert job.state == "failed" and seen == [(["a"], "1", subprocess.DEVNULL), (["b"], "1", subprocess.DEVNULL)]
    assert list(job.lines) == ["== one", "fetching", "== two", "oops", "== Stopped: two failed (exit code 2)"]
    assert job.log.read_text(encoding="utf-8").endswith("== Stopped: two failed (exit code 2)\n")


def test_run_calls_function_steps(monkeypatch):
    monkeypatch.setattr(packs, "steps", lambda pack: [("unpack", lambda job: job.say("unpacked"))])
    job = packs.Job("voxcpm")
    packs.run(job)
    assert job.state == "done" and list(job.lines) == ["== unpack", "unpacked", "== Done"]


def test_a_step_past_its_deadline_is_killed_and_the_job_fails(monkeypatch):
    monkeypatch.setattr(packs, "STEP_TIMEOUT", 0.05)
    monkeypatch.setattr(packs, "steps", lambda pack: [("A step that never ends", ["hang"])])
    hung = HungProc()
    job = packs.Job("whisper")
    packs.run(job, lambda cmd, **kw: hung)
    assert job.state == "failed" and hung.killed is True
    assert "== Stopped: timed out after 30 minutes: A step that never ends" in list(job.lines)


def test_an_install_interrupted_after_step_one_is_not_installed(plain, monkeypatch):
    monkeypatch.setattr(packs, "_piper_installed", lambda: True)  # partial artifacts fool the content check
    monkeypatch.setattr(packs, "steps", lambda pack: [("one", ["a"]), ("two", ["b"])])
    outputs = iter([FakeProc(["ok"], 0), FakeProc([], 1)])
    job = packs.Job("piper")
    packs.run(job, lambda cmd, **kw: next(outputs))
    assert job.state == "failed" and not packs.installed_marker("piper").exists()
    assert by_id(packs.packs())["piper"]["installed"] is False  # interrupted: never Done


def test_a_finished_install_writes_the_marker(monkeypatch):
    monkeypatch.setattr(packs, "steps", lambda pack: [("one", ["a"])])
    job = packs.Job("piper")
    packs.run(job, lambda cmd, **kw: FakeProc(["ok"], 0))
    assert job.state == "done" and packs.installed_marker("piper").exists()


def test_a_rerun_deletes_the_marker_before_its_first_step(monkeypatch):
    monkeypatch.setattr(packs, "steps", lambda pack: [("one", ["a"])])
    marker = packs.installed_marker("piper")
    marker.parent.mkdir(parents=True)
    marker.write_text("installed\n", encoding="utf-8")
    job = packs.Job("piper")
    packs.run(job, lambda cmd, **kw: FakeProc([], 3))
    assert job.state == "failed" and not marker.exists()


def test_a_pack_counts_as_installed_only_with_its_marker(plain, monkeypatch):
    monkeypatch.setattr(packs, "_piper_installed", lambda: True)
    rows = by_id(packs.packs())
    assert rows["piper"]["installed"] is False  # the content is there but no marker: not a finished install
    packs.installed_marker("piper").parent.mkdir(parents=True)
    packs.installed_marker("piper").write_text("installed\n", encoding="utf-8")
    assert by_id(packs.packs())["piper"]["installed"] is True


def test_start_runs_one_install_at_a_time(plain, monkeypatch):
    monkeypatch.setattr(packs, "_job", None)
    assert packs.start("gpu") == {"ok": False, "error": "no pack gpu for this PC"}
    started = []
    r = packs.start("piper", runner=lambda job: started.append(job.pack))
    assert r["ok"] and r["job"]["state"] == "running"
    assert packs.start("whisper", runner=lambda job: None)["error"] == "piper is still installing"
    end = time.perf_counter() + 5
    while not started and time.perf_counter() < end:
        time.sleep(0.01)
    assert started == ["piper"]
    packs._job.state = "done"
    assert packs.start("whisper", runner=lambda job: None)["ok"]
    assert packs.payload()["job"]["pack"] == "whisper"


def _zip_with_uv(tmp_path: Path) -> tuple[str, str]:
    archive = tmp_path / "uv-src.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("uv.exe", b"MZ fake uv")
    return archive.as_uri(), hashlib.sha256(archive.read_bytes()).hexdigest()


def test_fetch_uv_checks_the_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "VOXCPM_DIR", tmp_path / "voxcpm")
    url, digest = _zip_with_uv(tmp_path)
    job = packs.Job("voxcpm")
    with pytest.raises(RuntimeError, match="does not match its checksum"):
        packs._fetch_uv(job, url=url, sha256="0" * 64)
    assert not (tmp_path / "voxcpm" / "uv" / "uv.exe").exists()
    packs._fetch_uv(job, url=url, sha256=digest)
    assert (tmp_path / "voxcpm" / "uv" / "uv.exe").read_bytes() == b"MZ fake uv"
    assert not (tmp_path / "voxcpm" / "uv" / "uv.zip").exists()


def test_child_env_inherits_hf_home_and_puts_piper_only_on_the_piper_path(monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    assert "HF_HOME" not in packs.child_env("whisper")  # `iris dashboard`: the cache `iris --voice` reads
    monkeypatch.setenv("HF_HOME", "D:/hf")
    assert packs.child_env("whisper")["HF_HOME"] == "D:/hf"  # the app's ~/.iris/models, or the user's own
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert "PYTHONPATH" not in packs.child_env("piper")  # no Piper pack yet
    packs.piper_runtime().mkdir(parents=True)
    assert packs.child_env("piper")["PYTHONPATH"].startswith(str(packs.piper_runtime()) + os.pathsep)
    for other in ("whisper", "wake", "gpu", "voxcpm"):  # Piper's own numpy must not shadow Iris's
        assert "PYTHONPATH" not in packs.child_env(other)


def test_installed_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "PIPER_DIR", tmp_path / "voices")
    assert packs._piper_installed() is False
    (packs.piper_runtime() / "piper").mkdir(parents=True)
    (tmp_path / "voices").mkdir()
    for v in voice.PIPER_VOICES.values():
        (tmp_path / "voices" / f"{v}.onnx").write_bytes(b"x")
    assert packs._piper_installed() is True
    monkeypatch.setattr(voice, "VOXCPM_PYTHON", tmp_path / "python.exe")
    assert packs._voxcpm_installed() is False
    monkeypatch.setattr(packs, "_wake_files", lambda: [tmp_path / "voices" / f"{v}.onnx"
                                                       for v in voice.PIPER_VOICES.values()])
    assert packs._wake_installed() is True
    monkeypatch.setattr(packs, "_wake_files", lambda: (_ for _ in ()).throw(KeyError("own model")))
    assert packs._wake_installed() is False
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", None)
    assert packs._whisper_installed() is False
    monkeypatch.setattr(voice, "_cuda_available", lambda: True)
    assert packs._gpu_installed() is True


def test_whisper_is_installed_when_every_model_downloads(monkeypatch):
    import types

    calls = []
    utils = types.ModuleType("faster_whisper.utils")
    utils.download_model = lambda name, local_files_only=False: calls.append(name)
    parent = types.ModuleType("faster_whisper")
    parent.utils = utils
    monkeypatch.setitem(sys.modules, "faster_whisper", parent)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    monkeypatch.setattr(voice, "WHISPER_MODEL", "small")  # a fixed name: no GPU probe, one model to check
    assert packs._whisper_installed() is True
    assert calls[0] == "small"


def test_wake_files_name_the_model_and_its_feature_models(monkeypatch):
    import types

    fake = types.SimpleNamespace(MODELS={"hey_jarvis": {"model_path": "C:/m/hey_jarvis_v0.1.tflite"}})
    monkeypatch.setitem(sys.modules, "openwakeword", fake)
    assert [p.name for p in packs._wake_files()] == ["hey_jarvis_v0.1.onnx", "melspectrogram.onnx",
                                                     "embedding_model.onnx"]


def test_has_nvidia_follows_the_gpu_list(monkeypatch):
    from iris import settings

    monkeypatch.setattr(settings, "_gpus", lambda: [{"index": 0, "name": "RTX"}])
    assert packs._has_nvidia() is True


# --- the hooks in voice mode and doctor -----------------------------------------------------------------------------

def test_piper_runs_with_the_packs_runtime_on_its_path(tmp_path, monkeypatch):
    seen = {}

    class P:
        stdin = None

    monkeypatch.setattr(voice.subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or P())
    voice.PiperProcess(tmp_path / "v.onnx")
    assert seen["env"] is not None  # always an env now: UTF-8 stdin in every locale
    assert seen["env"]["PYTHONIOENCODING"] == "utf-8" and seen["env"]["PYTHONUTF8"] == "1"
    voice.piper_runtime().mkdir(parents=True)
    voice.PiperProcess(tmp_path / "v.onnx")
    assert seen["env"]["PYTHONPATH"].startswith(str(voice.piper_runtime()))


def test_cuda_dlls_come_from_the_gpu_pack_too(monkeypatch):
    added = []
    monkeypatch.setattr(voice.os, "add_dll_directory", lambda d: added.append(d), raising=False)
    monkeypatch.setenv("PATH", "")
    d = config.config_path().parent / "gpu" / "nvidia" / "cudnn" / "bin"
    d.mkdir(parents=True)
    voice._cuda_dlls()
    assert str(d) in added and str(d) in os.environ["PATH"]


def test_packs_routes(monkeypatch):
    import json
    import threading
    import urllib.request

    from iris import dashboard

    monkeypatch.setattr(packs, "payload", lambda: {"ok": True, "packs": []})
    monkeypatch.setattr(packs, "start", lambda pid: {"ok": True, "started": pid})
    s = dashboard.make_server(port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{s.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/packs", timeout=10) as r:
            assert json.loads(r.read()) == {"ok": True, "packs": []}
        req = urllib.request.Request(base + "/api/packs/install", b'{"id": "wake"}', method="POST", headers={
            "Content-Type": "application/json", "X-Iris-Token": s.token, "Origin": base})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read()) == {"ok": True, "started": "wake"}
    finally:
        s.shutdown()
        s.server_close()
