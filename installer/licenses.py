"""Write THIRD_PARTY_LICENSES.txt into the bundle: every Python package in it with its version and license
(installer/build.ps1 runs this with the bundled Python, so it lists exactly what ships).

    python installer/licenses.py build/Iris/THIRD_PARTY_LICENSES.txt
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path

HEADER = """Iris bundles the programs and packages below. Each keeps its own license; the full texts are in the
.dist-info folder of each package under Lib/site-packages, and Python's (with Tcl/Tk's) in LICENSE.txt next to this
file.

Iris itself: MIT, see LICENSE-Iris.txt.
Python {python}: Python Software Foundation License, see LICENSE.txt.

Not included, downloaded only when you choose them in the app: the Whisper models (MIT), the openWakeWord models
(CC BY-NC-SA 4.0), NVIDIA's CUDA libraries, Piper (GPL-3.0, installed as a separate program) and its voices, and
VoxCPM2 (Apache-2.0).

Packages:
"""


def license_of(meta: metadata.PackageMetadata) -> str:
    """The SPDX expression, else the license classifiers, else a short License field."""
    if meta.get("License-Expression"):
        return str(meta["License-Expression"])
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return ", ".join(dict.fromkeys(classifiers))
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) <= 80:
        return text
    return "see its license file"


def rows(dists: Iterable[metadata.Distribution]) -> list[str]:
    seen: dict[str, str] = {}
    for d in dists:
        name = d.metadata["Name"] or "?"
        if name.lower() == "iris":
            continue
        seen[name.lower()] = f"{name} {d.version}: {license_of(d.metadata)}"
    return [seen[k] for k in sorted(seen)]


def write(path: Path, dists: Iterable[metadata.Distribution] | None = None) -> None:
    lines = rows(metadata.distributions() if dists is None else dists)
    python = ".".join(map(str, sys.version_info[:3]))
    path.write_text(HEADER.format(python=python) + "".join(f"  {line}\n" for line in lines), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python installer/licenses.py <output file>", file=sys.stderr)
        return 2
    write(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
