"""The notes for a draft GitHub release (.github/workflows/installer.yml): the CHANGELOG section of the tagged
version, how to install, and the installer's SHA-256. Fails when the tag and iris.__version__ disagree.

    python installer/release_notes.py v0.6.0 dist > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INSTALL = """## Install

1. Download `{exe}` below and run it. No administrator rights are needed; Iris installs for your account only.
2. Windows SmartScreen may say "Windows protected your PC", because the installer is not code-signed. Click
   **More info**, then **Run anyway**. You can check the download first: its SHA-256 is below.
3. Iris opens with a short setup: it finds your model server (LM Studio, Ollama or any OpenAI-compatible one), tests
   the microphone and speakers, and offers optional voice packs. Nothing is downloaded without your click.

SHA-256 of `{exe}`:

```
{sha}
```

To check it in PowerShell: `Get-FileHash .\\{exe} -Algorithm SHA256`.
"""


def version() -> str:
    text = (ROOT / "iris" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not match:
        raise ValueError("no __version__ in iris/__init__.py")
    return match.group(1)


def changelog_section(changelog: str, ver: str) -> str:
    """The text under '## <ver> ...' up to the next '## ' heading."""
    match = re.search(rf"^## {re.escape(ver)}\b.*?$(.*?)(?=^## |\Z)", changelog, re.M | re.S)
    if not match:
        raise ValueError(f"CHANGELOG.md has no section for {ver}")
    return match.group(1).strip()


def notes(tag: str, dist: Path, root: Path = ROOT) -> str:
    ver = tag.removeprefix("v")
    if ver != version():
        raise ValueError(f"tag {tag} does not match iris.__version__ {version()}")
    exe = f"IrisSetup-{ver}.exe"
    sha_file = dist / f"{exe}.sha256"
    if not sha_file.exists():
        raise ValueError(f"{sha_file} is missing; build the installer first")
    sha = sha_file.read_text(encoding="utf-8").split()[0]
    section = changelog_section((root / "CHANGELOG.md").read_text(encoding="utf-8"), ver)
    return f"{section}\n\n{INSTALL.format(exe=exe, sha=sha)}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python installer/release_notes.py <tag> <dist folder>", file=sys.stderr)
        return 2
    try:
        print(notes(argv[0], Path(argv[1])))
    except ValueError as exc:
        print(f"release notes: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
