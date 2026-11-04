"""Optional voice packs, offered on the Setup tab. Nothing downloads until the user clicks a pack, after seeing its
size and license; everything lands under ~/.iris (next to config.toml), so it survives upgrades and the uninstaller
can remove it on request. One install runs at a time, as child processes whose output the Setup tab shows.

    whisper   the speech recognition model (faster-whisper; MIT)
    wake      the openWakeWord model for the wake word (CC BY-NC-SA 4.0: never bundled)
    gpu       NVIDIA's cuBLAS and cuDNN, for Whisper on the GPU (pip, into ~/.iris/gpu)
    piper     Piper (GPL-3.0, a separate program; pip into ~/.iris/piper/runtime) and its two voices
    voxcpm    VoxCPM2's own Python with PyTorch (uv, downloaded on demand; the 5 GB model loads on first use)
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import urllib.request
import zipfile
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from iris import config

UV_VERSION = "0.13.4"
UV_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
UV_SHA256 = "ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225"
FEATURE_MODELS = ("melspectrogram.onnx", "embedding_model.onnx")  # openWakeWord needs these next to the wake model


def home() -> Path:
    """The folder every pack installs into, next to config.toml, so it survives upgrades."""
    return config.config_path().parent


def gpu_dir() -> Path:
    """The folder the gpu pack installs its NVIDIA libraries into."""
    return home() / "gpu"


def piper_runtime() -> Path:
    """The Piper runtime folder, asked of iris.voice so both look at the same place."""
    from iris.voice import piper_runtime

    return piper_runtime()


def whisper_names() -> list[str]:
    """The Whisper models voice mode will load: the chosen or automatic one, plus tiny for the "iris" wake word."""
    from iris import voice

    names = [voice.WHISPER_MODEL or ("large-v3-turbo" if _has_nvidia() else "small")]
    if config.setting("IRIS_WAKEWORD", "wakeword", "hey_jarvis").lower() in ("iris", "hey_iris"):
        names.append(config.setting("IRIS_WAKE_WHISPER", "wake_whisper", "tiny"))
    return names


def _has_nvidia() -> bool:
    from iris.settings import _gpus

    return bool(_gpus())


def _wake_name() -> str:
    return config.setting("IRIS_WAKEWORD", "wakeword", "hey_jarvis")


def _wake_files() -> list[Path]:
    import openwakeword

    name = _wake_name()
    model = Path(openwakeword.MODELS[name]["model_path"].replace(".tflite", ".onnx"))
    return [model] + [model.parent / f for f in FEATURE_MODELS]


# --- is it there? ----------------------------------------------------------------------------------------------------

def _whisper_installed() -> bool:
    try:
        from faster_whisper.utils import download_model

        for name in whisper_names():
            download_model(name, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001 - not cached, or the voice parts are missing
        return False


def _wake_installed() -> bool:
    try:
        return all(p.exists() for p in _wake_files())
    except (ImportError, KeyError):
        return False


def _piper_installed() -> bool:
    from iris import voice

    have = (piper_runtime() / "piper").is_dir() or _importable("piper")
    return have and all((voice.PIPER_DIR / f"{v}.onnx").exists() for v in voice.PIPER_VOICES.values())


def _importable(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _gpu_installed() -> bool:
    from iris import voice

    return voice._cuda_available()


def _voxcpm_installed() -> bool:
    from iris import voice

    return voice.VOXCPM_PYTHON.exists()


# --- the packs -------------------------------------------------------------------------------------------------------

def packs() -> list[dict]:
    """Every pack with what it is, its size and license, whether it applies to this PC and whether it is there."""
    from iris import voice

    wake = _wake_name()
    nvidia = _has_nvidia()
    rows: list[dict[str, Any]] = [
        {"id": "whisper", "name": "Speech recognition model", "size": "75 MB to 1.6 GB", "license": "MIT",
         "what": f"Whisper {' and '.join(whisper_names())}, what Iris hears you with. Voice mode downloads it on "
                 "its first start anyway; this does it now.", "available": True, "installed": _whisper_installed},
        {"id": "wake", "name": f"Wake word model ({wake})", "size": "about 5 MB",
         "license": "CC BY-NC-SA 4.0: free for non-commercial use only",
         "what": "The openWakeWord model that listens for the wake word. Choose the wake word iris on the Settings "
                 "tab to need no model at all.", "available": wake.lower() not in ("iris", "hey_iris")
                                                          and not wake.endswith(".onnx"),
         "installed": _wake_installed},
        {"id": "gpu", "name": "NVIDIA libraries for the GPU", "size": "about 2 GB",
         "license": "NVIDIA's license for redistributable CUDA libraries",
         "what": "cuBLAS and cuDNN 9, so Whisper large-v3-turbo runs on your NVIDIA card: better and faster "
                 "hearing.", "available": nvidia, "installed": _gpu_installed},
        {"id": "piper", "name": "Piper voices (Dutch and English)", "size": "about 200 MB",
         "license": "Piper: GPL-3.0, run as a separate program. Voices: per model card (the defaults are trained "
                    "on CC0 data)",
         "what": f"Natural local voices: {', '.join(voice.PIPER_VOICES.values())}. Then pick piper as the voice "
                 "engine on the Settings tab.", "available": True, "installed": _piper_installed},
        {"id": "voxcpm", "name": "VoxCPM2 voice", "size": "about 3 GB now, 5 GB more on first use",
         "license": "Apache-2.0 (uv, which installs it: MIT or Apache-2.0)",
         "what": "One natural voice for Dutch and English, also mixed in one sentence. Needs an NVIDIA GPU with "
                 "about 6 GB free. Then pick voxcpm as the voice engine on the Settings tab.",
         "available": nvidia, "installed": _voxcpm_installed},
    ]
    for r in rows:
        r["installed"] = bool(r["available"]) and r["installed"]()
    return rows


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def steps(pack_id: str) -> list[tuple[str, list[str] | Callable[[Job], None]]]:
    """(what the user sees, a command line or a function) per step."""
    from iris import voice

    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    if pack_id == "whisper":
        return [(f"Downloading Whisper {n}", _python(f"from faster_whisper.utils import download_model; "
                                                     f"print(download_model({n!r}))")) for n in whisper_names()]
    if pack_id == "wake":
        return [(f"Downloading the {_wake_name()} model", _python(
            f"import openwakeword.utils as u; u.download_models([{_wake_name()!r}])"))]
    if pack_id == "gpu":
        return [("Installing cuBLAS and cuDNN (about 2 GB)",
                 pip + ["--target", str(gpu_dir()), "nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"])]
    if pack_id == "piper":
        voices = list(voice.PIPER_VOICES.values())
        return [("Installing Piper as a separate program", pip + ["--target", str(piper_runtime()), "piper-tts"]),
                ("Downloading the voices", [sys.executable, "-m", "piper.download_voices", *voices,
                                            "--data-dir", str(voice.PIPER_DIR)])]
    if pack_id == "voxcpm":
        venv = voice.VOXCPM_DIR / "venv"
        uv = str(voice.VOXCPM_DIR / "uv" / "uv.exe")
        return [(f"Downloading uv {UV_VERSION}", _fetch_uv),
                ("Making VoxCPM2's own Python 3.11", [uv, "venv", "-p", "3.11", str(venv)]),
                ("Installing VoxCPM2", [uv, "pip", "install", "--python", str(venv), "voxcpm", "triton-windows<3.3"]),
                ("Installing PyTorch for CUDA (about 2.5 GB)",
                 [uv, "pip", "install", "--python", str(venv), "--reinstall", "torch", "torchaudio",
                  "--index-url", "https://download.pytorch.org/whl/cu124"])]
    raise KeyError(pack_id)


def _fetch_uv(job: Job, url: str = UV_URL, sha256: str = UV_SHA256) -> None:
    from iris import voice

    dest = voice.VOXCPM_DIR / "uv"
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "uv.zip"
    with urllib.request.urlopen(url, timeout=60) as r, archive.open("wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != sha256:
        archive.unlink()
        raise RuntimeError(f"uv download does not match its checksum ({digest}); nothing was installed")
    with zipfile.ZipFile(archive) as z:
        z.extract("uv.exe", dest)
    archive.unlink()
    job.say(f"uv {UV_VERSION} checked and unpacked")


def child_env(pack_id: str = "") -> dict:
    """What a pack command runs with: plain output, and for the Piper pack only, Piper's own folder on its path.

    HF_HOME is inherited, not set here: the app puts it under ~/.iris for itself and its children, while under
    `iris dashboard` the downloads go to the same Hugging Face cache that `iris --voice` reads. Piper's folder holds
    its own numpy and onnxruntime, so it never goes on the path of the other packs' commands."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1"}
    if pack_id == "piper" and piper_runtime().is_dir():
        env["PYTHONPATH"] = str(piper_runtime()) + os.pathsep + env.get("PYTHONPATH", "")
    return env


# --- the one running install -----------------------------------------------------------------------------------------

class Job:
    def __init__(self, pack_id: str):
        self.pack = pack_id
        self.state = "running"  # running, done, failed
        self.lines: deque[str] = deque(maxlen=200)
        self.log = home() / "logs" / "packs.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def say(self, line: str) -> None:
        """One output line for the Setup tab and the packs.log on disk."""
        self.lines.append(line)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def as_dict(self) -> dict:
        """What the dashboard shows about this install: pack, state and its last lines."""
        return {"pack": self.pack, "state": self.state, "lines": list(self.lines)}


_job: Job | None = None
_lock = threading.Lock()


def run(job: Job, popen: Callable = subprocess.Popen) -> None:
    """Run the pack's steps in order; the first failure stops it."""
    try:
        for title, step in steps(job.pack):
            job.say(f"== {title}")
            if callable(step):
                step(job)
                continue
            proc = popen(step, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         env=child_env(job.pack), text=True, encoding="utf-8", errors="replace",
                         creationflags=subprocess.CREATE_NO_WINDOW)
            for line in proc.stdout:
                if line.strip():
                    job.say(line.rstrip())
            if proc.wait() != 0:
                raise RuntimeError(f"{title} failed (exit code {proc.returncode})")
        job.say("== Done")
        job.state = "done"
    except Exception as exc:  # noqa: BLE001 - the user reads what went wrong on the Setup tab
        job.say(f"== Stopped: {exc}")
        job.state = "failed"


def start(pack_id: str, runner: Callable[[Job], None] = run) -> dict:
    """Begin installing a pack, unless another install is still running."""
    global _job
    if pack_id not in {p["id"] for p in packs() if p["available"]}:
        return {"ok": False, "error": f"no pack {pack_id} for this PC"}
    with _lock:
        if _job is not None and _job.state == "running":
            return {"ok": False, "error": f"{_job.pack} is still installing"}
        _job = Job(pack_id)
        threading.Thread(target=runner, args=(_job,), name=f"iris-pack-{pack_id}", daemon=True).start()
    return {"ok": True, "job": _job.as_dict()}


def payload() -> dict:
    """The Setup tab's view of the voice packs and the one running install."""
    return {"ok": True, "packs": packs(), "job": _job.as_dict() if _job is not None else None}
