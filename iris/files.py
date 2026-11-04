"""File tools for Iris: find files by name, list folders and read text, PDF or Office files."""

import os
import re
import shutil
import zipfile
from pathlib import Path

from iris import config

MAX_CHARS = 20_000
# appended to a missing-path error so the model looks once instead of retrying the same path
_MISSING = ("; it does not exist. Look for it once with find_files, then tell the user what you found "
            "instead of retrying.")
_SKIP_DIRS = {"AppData", "node_modules", ".git", ".venv", "__pycache__"}
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".py", ".log", ".html", ".xml", ".toml", ".ini", ".yaml"}


def find_files(name: str, folder: str = "") -> str:
    """Files and folders whose name contains `name`, newest first; read only."""
    q = name.lower()
    if folder:
        root0 = Path(folder).expanduser()
        if not root0.is_dir():
            return f"error: no such folder: {root0}" + _MISSING
        roots = [root0]
    else:
        roots = config.folders()
    hits: list[tuple[float, Path]] = []
    scanned = 0
    note = ""
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            if _in_trash(current):
                dirnames[:] = []  # never search inside Iris's trash
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
            scanned += len(filenames) + len(dirnames)
            for entry in (*filenames, *dirnames):
                if q in entry.lower():
                    hits.append((_mtime(current / entry), current / entry))
            if len(hits) >= 30:
                note = "\n[stopped after 30 hits]"
                break
            if scanned >= 20_000:
                note = "\n[stopped after scanning 20000 entries]"
                break
        else:
            continue
        break
    if not hits:
        return f"No files named {name} found." + note
    lines = [str(p) for _, p in sorted(hits, key=lambda h: h[0], reverse=True)]
    return "\n".join(lines[:30]) + note


def _in_trash(p: Path) -> bool:
    try:
        trash = Path(os.environ.get("IRIS_TRASH_DIR", "~/.iris/trash")).expanduser().resolve()
        return p.resolve() == trash
    except OSError:
        return False


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def list_folder(path: str) -> str:
    """The entries of a folder: folders first with a trailing /, then files with their size in KB."""
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"error: no such folder: {p}" + _MISSING
    dirs = sorted(e.name + "/" for e in p.iterdir() if e.is_dir())
    files_ = [f"{e.name} {e.stat().st_size // 1024} KB"
              for e in sorted(p.iterdir(), key=lambda e: e.name) if not e.is_dir()]
    lines = dirs + files_
    if len(lines) > 100:
        return "\n".join(lines[:100]) + f"\n... and {len(lines) - 100} more"
    return "\n".join(lines) if lines else "(empty)"


def read_file(path: str) -> str:
    """A file as text: plain text, .docx/.pptx from their XML or .pdf via pypdf; at most 20000 characters."""
    p = Path(path).expanduser()
    if not p.is_file():
        return f"error: no such file: {p}" + _MISSING
    suffix = p.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _pdf_text(p)
        elif suffix in (".docx", ".pptx"):
            text = _office_text(p, suffix)
        else:
            raw = p.read_bytes()
            if suffix in _TEXT_SUFFIXES:
                text = raw.decode("utf-8", "replace")
            else:
                try:
                    text = raw.decode("utf-8")  # any file that decodes as UTF-8 is read as text
                except UnicodeDecodeError:
                    return "error: cannot read this kind of file"
    except Exception as e:  # a corrupt or unreadable file must not crash the agent loop
        return f"error: could not read {p.name}: {e}"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "... (truncated)"
    return text if text.strip() else "(empty file)"


def _pdf_text(p: Path) -> str:
    try:
        import pypdf
    except ImportError:
        return "error: reading PDF files needs pypdf: pip install pypdf in Iris's Python"
    reader = pypdf.PdfReader(str(p))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _office_text(p: Path, suffix: str) -> str:
    """The text of a .docx or .pptx from its XML: paragraphs on separate lines, pptx slide by slide."""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(p) as z:
        if suffix == ".docx":
            root = ET.fromstring(z.read("word/document.xml"))
            return "\n".join(
                "".join(t.text or "" for t in para.iter() if t.tag.endswith("}t"))
                for para in root.iter() if para.tag.endswith("}p")
            )
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=_slide_no)
        lines = []
        for i, slide in enumerate(slides, 1):
            root = ET.fromstring(z.read(slide))
            text = "".join(t.text or "" for t in root.iter() if t.tag.endswith("}t"))
            lines.append(f"Slide {i}: {text}")
        return "\n".join(lines)


def _slide_no(n: str) -> int:
    m = re.search(r"\d+", n)
    return int(m.group()) if m else 0


# --- writing, moving and copying -------------------------------------------------


def resolve_target(path: str, suffix: str = "") -> Path:
    """A bare file name goes to ~/Documents/Iris, and `suffix` is added when the name has none; anything else is
    used as given. The tools and their tiers both call this, so they always look at the same file. Pure."""
    p = Path(path).expanduser()
    if len(p.parts) == 1:
        p = Path.home() / "Documents" / "Iris" / p.name
    if suffix and not p.suffix:
        p = p.with_suffix(suffix)
    return p


def write_file(path: str, text: str, overwrite: bool = False) -> str:
    """Write UTF-8 text to a file. Overwriting sends the old file to Iris's trash first, so it can be restored."""
    p = resolve_target(path)
    if p.exists():
        if not overwrite:
            return f"error: {p} exists; say overwrite to replace it"
        from iris.tools import trash_file  # same mechanism as the trash_file tool, no copy of its logic
        trash_file(str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"wrote {len(text)} characters to {p}"


def make_folder(path: str) -> str:
    """Create a folder with any missing parents. An existing path is an error."""
    p = Path(path).expanduser()
    if p.exists():
        return f"error: {p} already exists"
    p.mkdir(parents=True, exist_ok=False)
    return f"made folder {p}"


def _destination(destination: str, source: Path) -> Path:
    """Where the item lands. 'Into that folder' when the destination is an existing folder, ends in a slash, or is a
    new path without an extension for a file that has one: "move report.docx to ~/Archive" must not rename the file
    to "Archive". ponytail: renaming report.docx to an extensionless name now needs the new name to exist first."""
    d = Path(destination).expanduser()
    new_folder = not d.exists() and not d.suffix and source.is_file() and bool(source.suffix)
    if d.is_dir() or destination.endswith(("/", "\\")) or new_folder:
        return d / source.name
    return d


def _source_and_target(source: str, destination: str) -> tuple[Path, Path] | str:
    """The expanded source and computed destination, or an error string when either check fails."""
    s = Path(source).expanduser()
    if not s.exists():
        return f"error: no such file or folder: {s}"
    d = _destination(destination, s)
    if d.exists():
        return f"error: {d} already exists; never overwrites"
    d.parent.mkdir(parents=True, exist_ok=True)
    return s, d


def move_file(source: str, destination: str) -> str:
    """Move a file or folder. Never overwrites; an existing destination is an error unless it's a folder."""
    st = _source_and_target(source, destination)
    if isinstance(st, str):
        return st
    s, d = st
    shutil.move(str(s), str(d))
    return f"moved {s} to {d}"


def copy_file(source: str, destination: str) -> str:
    """Copy a file or folder (folders with all their contents). Never overwrites."""
    st = _source_and_target(source, destination)
    if isinstance(st, str):
        return st
    s, d = st
    if s.is_dir():
        shutil.copytree(s, d)
    else:
        shutil.copy2(s, d)
    return f"copied {s} to {d}"
