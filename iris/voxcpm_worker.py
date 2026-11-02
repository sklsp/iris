"""VoxCPM2 speech for Iris, run in its own Python (it needs torch; Iris does not): `python voxcpm_worker.py VOICE.wav`.

Stdin: one JSON line per answer, {"text": "..."}. Stdout, binary, little-endian uint32 counts: first the sample rate,
then per answer float32 chunks as [n][n samples], ended by n = 0; n = 0xFFFFFFFF means it failed (details on stderr).
VOICE.wav is the voice every answer is spoken in; when it does not exist yet one is made and kept, so Iris keeps the
same voice from then on. This file imports nothing from Iris.
"""

import json
import os
import re
import struct
import sys
import traceback

FAILED = 0xFFFFFFFF
FIRST_VOICE = "Hallo, ik ben Iris. Hello, I am Iris, and I speak Dutch and English."
# The model sizes its attention caches for 8192 positions and reads all of them every step, used or not: on an RTX 3060
# that made speech at 0.58 times real time. A piece of an answer needs a few hundred (its text, the voice sample and the
# speech), so 512 positions make it 1.96 times real time with the same output. Longer answers go in pieces.
CACHE = int(os.environ.get("IRIS_VOXCPM_CACHE", "512"))
PIECE = 250  # characters per piece: well inside CACHE with the voice sample and the speech it makes
_BREAKS = (re.compile(r"(?<=[.!?…])\s+"), re.compile(r"(?<=[,;:])\s+"), re.compile(r"\s+"))


def pieces(text: str, longest: int = PIECE) -> list[str]:
    """The text in pieces of at most `longest` characters, cut at sentence ends, else at commas, else between words."""
    text = " ".join(text.split())
    if len(text) <= longest:
        return [text] if text else []
    for brk in _BREAKS:
        parts = brk.split(text)
        if len(parts) > 1:
            break
    else:
        return [text[i:i + longest] for i in range(0, len(text), longest)]  # one endless word
    out: list[str] = []
    for part in parts:
        if out and len(out[-1]) + 1 + len(part) <= longest:
            out[-1] += " " + part
        else:
            out += pieces(part, longest) if len(part) > longest else [part]
    return out


def main(voice: str) -> None:
    out = os.fdopen(os.dup(1), "wb")  # the protocol; anything the libraries print goes to stderr instead
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    import numpy as np
    import soundfile as sf
    from voxcpm import VoxCPM

    steps = int(os.environ.get("IRIS_VOXCPM_STEPS", "10"))
    model = VoxCPM.from_pretrained(os.environ.get("IRIS_VOXCPM_MODEL", "openbmb/VoxCPM2"), load_denoiser=False)
    rate = model.tts_model.sample_rate
    tts = model.tts_model
    dtype = next(tts.base_lm.parameters()).dtype
    tts.base_lm.setup_cache(1, CACHE, tts.device, dtype)
    tts.residual_lm.setup_cache(1, CACHE, tts.device, dtype)
    if not os.path.exists(voice):
        os.makedirs(os.path.dirname(voice) or ".", exist_ok=True)
        sf.write(voice, model.generate(text=FIRST_VOICE, inference_timesteps=steps), rate)
    model.generate(text="Klaar.", inference_timesteps=steps, reference_wav_path=voice)  # compile before answer one
    out.write(struct.pack("<I", rate))
    out.flush()

    for line in sys.stdin.buffer:
        try:
            for piece in pieces(json.loads(line)["text"]):
                # max_len keeps the text, the voice sample and the speech inside CACHE even if the model rambles
                for chunk in model.generate_streaming(text=piece, inference_timesteps=steps, reference_wav_path=voice,
                                                      max_len=CACHE - 200):
                    audio = np.ascontiguousarray(chunk, dtype="<f4").reshape(-1)
                    out.write(struct.pack("<I", len(audio)) + audio.tobytes())
                    out.flush()
            out.write(struct.pack("<I", 0))
        except Exception:  # noqa: BLE001 - one bad answer must not end the voice
            traceback.print_exc()
            out.write(struct.pack("<I", FAILED))
        out.flush()


if __name__ == "__main__":
    main(sys.argv[1])
