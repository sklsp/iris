"""The core must import without any of the optional audio/ML dependencies installed."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_core_imports_without_optional_deps():
    code = (
        "import sys\n"
        'for name in ("numpy", "sounddevice", "faster_whisper", "openwakeword", "pyttsx3", "kokoro_onnx"):\n'
        "    sys.modules[name] = None\n"
        "import iris.voice, iris.agent, iris.tools, iris.llm, iris.eval\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
