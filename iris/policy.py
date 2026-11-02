"""The escalation rules for Iris's tools, in one module apart from the tools themselves.

tools.py imports every name back from here, so both modules agree on one policy. Skills Iris builds for herself
never run inside Iris (iris.skills runs them as a separate process), so they can not change these rules.

safe     runs immediately (reading, opening apps, status)
confirm  asks the user first (clicking, typing)
never    refused, always (sending, buying, paying, passwords, deleting)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from iris import config, files

SAFE, CONFIRM, NEVER = "safe", "confirm", "never"

# Control names that mean an irreversible or outward-facing action, in English and Dutch. Matched as word
# starts so Dutch inflections count too (verzenden, betalen). ponytail: over-refuses names like "Poster";
# per-app allowlists if that gets in the way.
_NEVER_WORDS = re.compile(
    r"\b(send|submit|post|publish|buy|order|pay|checkout|purchase|delete|remove|password|sign ?in|log ?in|"
    r"verzend|verstu|plaats|koop|kopen|bestel|betaal|betalen|afreken|verwijder|wachtwoord|inlog|aanmeld|"
    # German, French and Spanish: stems long enough not to catch English words (no "pag", "envi" or "compr")
    r"versend|absend|abschick|kauf|bestell|bezahl|zahlung|lösch|entfern|passwort|kennwort|anmeld|einlog|"
    r"envoy|achet|commande|paiement|supprim|effac|mot de passe|connexion|publier|"
    r"enviar|envía|comprar|compra|pagar|pago|pedido|eliminar|borrar|contraseña|iniciar sesión|publicar)",
    re.I,
)

# Window titles that mean "pressing Enter sends a message or email": never automated.
_MAIL_CHAT = re.compile(r"(outlook|gmail|whatsapp|discord|\bteams\b|slack|telegram|\bsignal\b|messenger|thunderbird|"
                        r"skype|\bmail\b|\binbox\b|postvak in|posteingang|boîte de réception|bandeja de entrada)", re.I)


def _unload_tier(args: dict) -> str:
    from iris.llm import effective_model  # read at call time so a model switch applies at once

    model = args.get("model")
    if model == "all" or model == effective_model():
        return NEVER
    return CONFIRM


def _click_tier(args: dict) -> str:
    return NEVER if _NEVER_WORDS.search(str(args.get("control", ""))) else CONFIRM


def _click_on_screen_tier(args: dict) -> str:
    """Like click: NEVER when the described target names a send/buy/pay/password/delete control. ponytail: the words
    the model chose, not the pixels it clicks; the spoken confirm shows them, so the user is the real check."""
    return NEVER if _NEVER_WORDS.search(str(args.get("target", ""))) else CONFIRM


def _type_tier(args: dict) -> str:
    return NEVER if _NEVER_WORDS.search(str(args.get("into", ""))) else CONFIRM


def _open_url_tier(args: dict) -> str:
    from urllib.parse import urlparse

    scheme = urlparse(str(args.get("url", ""))).scheme.lower()
    return SAFE if scheme in ("http", "https") else NEVER


def _press_keys_tier(args: dict) -> str:
    keys = str(args.get("keys", "")).upper()
    if "{ENTER}" not in keys:
        return CONFIRM
    # Enter in a mail/chat window sends the message, so that combination is never automated.
    return NEVER if _MAIL_CHAT.search(str(args.get("window", ""))) else CONFIRM


# File names that look like secrets; reading them is never automated.
_SECRET_WORDS = re.compile(r"(password|wachtwoord|passw[oö]rt|kennw[oö]rt|mots?.?de.?passe|contrase[nñ]a|geheim|secret"
                           r"|token|credential|apikey|api_key|private"
                           r"|(^|[^a-z])keys?([^a-z]|$))", re.I)  # key as a word: google_key.txt, not keyboard
_SECRET_SUFFIXES = (".key", ".pem", ".pfx", ".kdbx")


def _read_tier(args: dict) -> str:
    """Reading a file whose name looks like a secret is never automated; everything else is safe."""
    name = Path(str(args.get("path", ""))).name.lower()
    if (_SECRET_WORDS.search(name) or name.endswith(_SECRET_SUFFIXES) or name == ".env"
            or name.startswith(("id_rsa", "id_ed25519"))):
        return NEVER
    return SAFE


def _trash_dir() -> Path:
    d = Path(os.environ.get("IRIS_TRASH_DIR") or (Path.home() / ".iris" / "trash"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trash_roots() -> list[Path]:
    """Trash roots from IRIS_TRASH_ROOTS (';' separated); config.folders() otherwise."""
    raw = os.environ.get("IRIS_TRASH_ROOTS")
    if not raw:
        return config.folders()
    roots = [Path(s.strip()) for s in raw.split(";") if s.strip()]
    if Path.home() not in roots:
        roots.insert(0, Path.home())
    return roots


_SYSTEM_DIRS = [Path(os.environ.get("SystemRoot", "C:/Windows")), Path("C:/Program Files"),
                Path("C:/Program Files (x86)"), Path("C:/ProgramData")]


def _under(p: Path, root: Path) -> bool:
    try:
        return p.is_relative_to(root.resolve())
    except OSError:
        return False


def _trash_tier(args: dict) -> str:
    """Only files under the trash roots may be trashed; never a root itself, system dirs, or the trash."""
    p = Path(str(args.get("path", ""))).expanduser().resolve()
    roots = [r.resolve() for r in _trash_roots()]
    if p in roots or not any(_under(p, r) for r in roots):
        return NEVER
    if any(_under(p, s) for s in _SYSTEM_DIRS) or _under(p, _trash_dir()):
        return NEVER
    return CONFIRM


def _writable(path: str) -> bool:
    """The resolved path is under a config folder, not in a system dir and not in the trash."""
    p = Path(str(path)).expanduser().resolve()
    if any(_under(p, s) for s in _SYSTEM_DIRS):
        return False
    if _under(p, _trash_dir()):
        return False
    return any(_under(p, r.resolve()) for r in config.folders())


def _write_tier(args: dict) -> str:
    """Writing is never outside the writable folders; inside them it always asks first."""
    return NEVER if not _writable(str(files.resolve_target(str(args.get("path", ""))))) else CONFIRM


def _make_folder_tier(args: dict) -> str:
    return NEVER if not _writable(str(args.get("path", ""))) else SAFE


def _move_tier(args: dict) -> str:
    """Moving never touches the source or the destination outside the writable folders."""
    if not _writable(str(args.get("destination", ""))):
        return NEVER
    if not _writable(str(args.get("source", ""))):
        return NEVER
    return CONFIRM


def _copy_tier(args: dict) -> str:
    return NEVER if not _writable(str(args.get("destination", ""))) else CONFIRM


def _new_file_tier_for(suffix: str):
    """The tier of a tool that creates one file with this suffix: NEVER outside the writable folders, CONFIRM when
    the file exists (it is replaced; the old one goes to the trash first), else SAFE."""
    def tier(args: dict) -> str:
        p = files.resolve_target(str(args.get("path", "")), suffix)
        if not _writable(str(p)):
            return NEVER
        return CONFIRM if p.exists() else SAFE
    return tier


_docx_tier = _new_file_tier_for(".docx")
_pptx_tier = _new_file_tier_for(".pptx")
_xlsx_tier = _new_file_tier_for(".xlsx")


def _screenshot_tier(args: dict) -> str:
    """No path means a new file in ~/Pictures/Iris: SAFE. A given path follows the new-file rules."""
    return _new_file_tier_for(".png")(args) if args.get("path") else SAFE


