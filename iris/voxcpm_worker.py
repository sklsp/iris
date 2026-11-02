"""VoxCPM2 speech for Iris, run in its own Python (it needs torch; Iris does not): `python voxcpm_worker.py VOICE.wav`.

Stdin: one JSON line per answer, {"text": "..."}. Stdout, binary, little-endian uint32 counts: first the sample rate,
then per answer float32 chunks as [n][n samples], ended by n = 0; n = 0xFFFFFFFF means it failed (details on stderr).
VOICE.wav is the voice every answer is spoken in; when it does not exist yet one is made and kept, so Iris keeps the
same voice from then on. This file imports nothing from Iris.
"""

import json
import os
import struct
import sys
import traceback

FAILED = 0xFFFFFFFF
FIRST_VOICE = "Hallo, ik ben Iris. Hello, I am Iris, and I speak Dutch and English."


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
    if not os.path.exists(voice):
        os.makedirs(os.path.dirname(voice) or ".", exist_ok=True)
        sf.write(voice, model.generate(text=FIRST_VOICE, inference_timesteps=steps), rate)
    model.generate(text="Klaar.", inference_timesteps=steps, reference_wav_path=voice)  # compile before answer one
    out.write(struct.pack("<I", rate))
    out.flush()

    for line in sys.stdin.buffer:
        try:
            text = json.loads(line)["text"]
            for chunk in model.generate_streaming(text=text, inference_timesteps=steps, reference_wav_path=voice):
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
