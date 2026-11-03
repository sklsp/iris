"""The first-run wizard, the dashboard's Setup tab: find the model server, test the microphone and the speaker, show
what `iris doctor` finds, and ask about update checks. The desktop app opens it until it is finished once; it stays
on the Setup tab afterwards.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from iris import __version__, config

SERVERS = (("LM Studio", "http://127.0.0.1:1234/v1"), ("Ollama", "http://127.0.0.1:11434/v1"),
           ("llama.cpp", "http://127.0.0.1:8080/v1"))
DEFAULT_URL = SERVERS[0][1]
MIC_SECONDS = 3.0
SPEAKER_TEXT = "Hello, I am Iris. If you can hear this, your speakers work."


def marker_path() -> Path:
    return config.config_path().parent / "setup.json"


def done() -> bool:
    return marker_path().exists()


def mark_done() -> None:
    path = marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": __version__, "at": datetime.now().isoformat(timespec="seconds")}),
                    encoding="utf-8")


def probe(url: str, timeout: float = 1.5) -> list[str] | None:
    """The model ids an OpenAI-compatible server lists, or None when nothing answers there."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/models", timeout=timeout) as r:
            data = json.load(r)
        return [str(m.get("id", "")) for m in data.get("data", []) if m.get("id")]
    except (OSError, ValueError, AttributeError):
        return None


def servers(probe_fn: Callable[[str], list[str] | None] = probe) -> list[dict]:
    """The configured server first, then the usual local ones, each probed (in parallel, about a second)."""
    current = config.setting("IRIS_LLM_URL", "llm_url", DEFAULT_URL)
    rows = [("Configured", current)] + [(n, u) for n, u in SERVERS if u.rstrip("/") != current.rstrip("/")]
    with ThreadPoolExecutor(len(rows)) as pool:
        found = list(pool.map(probe_fn, [u for _, u in rows]))
    return [{"name": n, "url": u, "reachable": m is not None, "models": m or [], "current": u == current}
            for (n, u), m in zip(rows, found, strict=True)]


def use_server(body: dict, probe_fn: Callable[[str], list[str] | None] = probe) -> dict:
    """Save the server (llm_url) and the model (settings.json) the user picked; the model must be on that server."""
    from iris import settings
    from iris.llm import read_settings, settings_path

    url, model = str(body.get("url") or "").strip(), str(body.get("model") or "").strip()
    models = probe_fn(url) if url else None
    if models is None:
        return {"ok": False, "error": f"no OpenAI-compatible server answers at {url or '(empty)'}"}
    if model not in models:
        return {"ok": False, "error": "pick one of the models this server lists" if models
                else "the server lists no models: load or download one first"}
    saved = settings.save({"llm_url": None if url == DEFAULT_URL else url})
    if not saved["ok"]:
        return {"ok": False, "error": "; ".join(saved["errors"].values())}
    data = read_settings()
    data["model"] = model
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "url": url, "model": model}


def mic_test(record: Callable[[float], Any] | None = None) -> dict:
    """Record MIC_SECONDS from the default microphone and say how loud it was. Nothing is kept or sent."""
    try:
        import numpy as np

        if record is None:
            import sounddevice as sd

            def record(seconds: float) -> Any:
                audio = sd.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="float32")
                sd.wait()
                return audio

        audio = np.asarray(record(MIC_SECONDS), dtype="float32").reshape(-1)
    except ImportError:
        return {"ok": False, "level": 0, "message": "The voice parts are not installed (pip install -e .[voice])."}
    except Exception as exc:  # noqa: BLE001 - no device, device busy: the message is the answer
        return {"ok": False, "level": 0, "message": f"No microphone could be opened: {exc}"}
    from iris.voice import VAD_THRESHOLD

    rms = float(np.sqrt(np.mean(audio.astype("float64") ** 2))) if audio.size else 0.0
    level = min(100, round(rms / (VAD_THRESHOLD * 10) * 100))  # 100 = ten times the silence threshold
    if rms > VAD_THRESHOLD * 2:
        return {"ok": True, "level": level, "message": "Iris heard you clearly."}
    if rms > VAD_THRESHOLD / 2:
        return {"ok": True, "level": level, "message": "Iris heard you, softly. Move closer or turn the input up."}
    return {"ok": False, "level": level, "message": "Almost nothing came in. Check the microphone in Windows "
                                                    "Sound settings (the default input device and its level)."}


def speaker_test(say: Callable[[str], object] | None = None) -> dict:
    """Say one sentence with the Windows voice, which is always there; the chosen engine speaks when Iris listens."""
    try:
        if say is None:
            from iris.voice import Speaker

            say = Speaker("sapi").speak
        say(SPEAKER_TEXT)
    except Exception as exc:  # noqa: BLE001 - no audio device or no SAPI voice
        return {"ok": False, "message": f"Could not play sound: {exc}"}
    return {"ok": True, "message": "Played. If you heard nothing, check the default output device in Windows."}


def checks() -> list[dict]:
    """iris doctor's checks, marked ok, optional or to fix."""
    from iris import doctor
    from iris.dashboard import lmstudio_payload

    return [{"ok": ok, "line": line, "optional": line.startswith(doctor.OPTIONAL)}
            for ok, line in doctor.checks(lmstudio_payload())]


def payload() -> dict:
    from iris import update

    return {"ok": True, "done": done(), "version": __version__, "servers": servers(), "checks": checks(),
            "update_check": update.enabled()}


def finish(body: dict) -> dict:
    """Store the update-check choice, mark setup done, and in the app start listening."""
    from iris import settings
    from iris.dashboard import APP_HOOKS

    saved = settings.save({"update_check": bool(body.get("update_check", True))})
    if not saved["ok"]:
        return {"ok": False, "error": "; ".join(saved["errors"].values())}
    mark_done()
    hook = APP_HOOKS.get("restart_voice")
    return {"ok": True, "message": hook() if hook else "Setup is done. Start voice mode with iris --voice."}
