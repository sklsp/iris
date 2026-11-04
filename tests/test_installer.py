"""installer/: the build helpers (DLL scan, license list, release notes, sitecustomize) and the promises the Inno Setup
script and the release workflow make. The installer itself is built and installed in .github/workflows/installer.yml;
nothing here builds, installs or downloads anything."""

from __future__ import annotations

import importlib.util
import os
import re
import site
import struct
import sys
from email.message import Message
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from iris import __version__, app

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "installer"
ISS = (INSTALLER / "iris.iss").read_text(encoding="utf-8")
BUILD = (INSTALLER / "build.ps1").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "installer.yml").read_text(encoding="utf-8")


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"installer_{name}", INSTALLER / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dlls, licenses, release_notes = load("dlls"), load("licenses"), load("release_notes")


# --- dlls.py: which binaries need the Visual C++ runtime from the PC -------------------------------------------------

def make_pe(imported: list[str], delayed: tuple[str, ...] | list[str] = ()) -> bytes:
    """A minimal 64-bit PE file with one section holding an import table and a delay-load table."""
    pe, opt, opt_size, vaddr, raw, size = 0x40, 0x58, 240, 0x1000, 0x200, 0x400
    data = bytearray(raw + size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe)
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", data, pe + 4, 0x8664, 1)
    struct.pack_into("<H", data, pe + 20, opt_size)
    struct.pack_into("<H", data, opt, 0x20B)
    directories = opt + 112
    imports_rva, delay_rva, names_rva = vaddr, vaddr + 0x100, vaddr + 0x200
    struct.pack_into("<II", data, directories + 8 * 1, imports_rva, 20 * (len(imported) + 1))
    if delayed:
        struct.pack_into("<II", data, directories + 8 * 13, delay_rva, 32 * (len(delayed) + 1))
    section = opt + opt_size
    data[section:section + 8] = b".idata\0\0"
    struct.pack_into("<IIII", data, section + 8, size, vaddr, size, raw)

    def at(rva: int) -> int:
        return rva - vaddr + raw

    name_at = names_rva
    for i, name in enumerate(imported):
        struct.pack_into("<I", data, at(imports_rva) + 20 * i + 12, name_at)
        data[at(name_at):at(name_at) + len(name) + 1] = name.encode() + b"\0"
        name_at += len(name) + 1
    for i, name in enumerate(delayed):
        struct.pack_into("<I", data, at(delay_rva) + 32 * i + 4, name_at)
        data[at(name_at):at(name_at) + len(name) + 1] = name.encode() + b"\0"
        name_at += len(name) + 1
    return bytes(data)


def test_imports_reads_the_import_and_delay_load_tables():
    assert dlls.imports(make_pe(["KERNEL32.dll", "MSVCP140.dll"], ["VCOMP140.dll"])) == [
        "KERNEL32.dll", "MSVCP140.dll", "VCOMP140.dll"]
    assert dlls.imports(make_pe([])) == []


@pytest.mark.parametrize("data", [b"", b"not a binary", b"MZ" + b"\0" * 10, b"MZ" + b"\0" * 0x3A + b"\x40\0\0\0"])
def test_imports_of_anything_else_is_empty(data):
    assert dlls.imports(data) == []


@pytest.mark.skipif(sys.platform != "win32", reason="reads this Python's own DLL")
def test_imports_of_a_real_python_dll():
    name = f"python{sys.version_info[0]}{sys.version_info[1]}.dll"
    found = next(p / name for p in (Path(sys.base_prefix), Path(sys.executable).parent) if (p / name).exists())
    assert "kernel32.dll" in {n.lower() for n in dlls.imports(found.read_bytes())}


def test_missing_runtime_counts_only_dlls_shipped_nowhere_near(tmp_path):
    (tmp_path / "vcruntime140.dll").write_bytes(b"MZ")  # next to Iris.exe, as the bundled Python has it
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "needs.pyd").write_bytes(make_pe(["MSVCP140.dll", "VCRUNTIME140.dll", "KERNEL32.dll"]))
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "carries.pyd").write_bytes(make_pe(["msvcp140.dll"]))
    (tmp_path / "b" / "msvcp140.dll").write_bytes(b"MZ")
    (tmp_path / "b" / "readme.txt").write_bytes(make_pe(["msvcp140.dll"]))  # not a binary by name
    assert dlls.missing_runtime(tmp_path) == ["a/needs.pyd: msvcp140.dll"]


def test_dlls_main(tmp_path, capsys):
    (tmp_path / "x.dll").write_bytes(make_pe(["vcomp140.dll"]))
    assert dlls.main([str(tmp_path)]) == 0
    assert capsys.readouterr().out == "x.dll: vcomp140.dll\n"
    assert dlls.main([]) == 2


# --- licenses.py: THIRD_PARTY_LICENSES.txt ---------------------------------------------------------------------------

def dist(name: str, version: str, **fields: str | list[str]) -> SimpleNamespace:
    meta = Message()
    meta["Name"] = name
    for key, value in fields.items():
        for v in value if isinstance(value, list) else [value]:
            meta[key.replace("_", "-")] = v
    return SimpleNamespace(metadata=meta, version=version)


def test_license_of_prefers_the_expression_then_classifiers_then_a_short_field():
    assert licenses.license_of(dist("a", "1", License_Expression="MIT", License="BSD").metadata) == "MIT"
    classified = dist("b", "1", Classifier=["License :: OSI Approved :: BSD License", "Topic :: Audio",
                                            "License :: OSI Approved :: BSD License"])
    assert licenses.license_of(classified.metadata) == "BSD License"
    assert licenses.license_of(dist("c", "1", License="Apache 2.0").metadata) == "Apache 2.0"
    assert licenses.license_of(dist("d", "1", License="long\ntext").metadata) == "see its license file"
    assert licenses.license_of(dist("e", "1").metadata) == "see its license file"


def test_license_list_is_sorted_and_leaves_iris_out(tmp_path):
    out = tmp_path / "THIRD_PARTY_LICENSES.txt"
    licenses.write(out, [dist("numpy", "2.3.0", License_Expression="BSD-3-Clause"), dist("iris", __version__),
                         dist("Bottle", "0.13", License="MIT")])
    text = out.read_text(encoding="utf-8")
    assert text.index("  Bottle 0.13: MIT\n") < text.index("  numpy 2.3.0: BSD-3-Clause\n")
    assert "iris " + __version__ not in text and "CC BY-NC-SA" in text
    assert licenses.main([]) == 2


def test_license_list_of_this_environment(tmp_path):
    assert licenses.main([str(tmp_path / "list.txt")]) == 0
    assert "pytest " in (tmp_path / "list.txt").read_text(encoding="utf-8")


# --- release_notes.py ------------------------------------------------------------------------------------------------

CHANGELOG = f"""# Changelog

## {__version__} (2026-11-03): the release

- One thing.
- Another.

## 0.0.1 (2026-01-01): older

- Old.
"""


def test_release_notes(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    exe = f"IrisSetup-{__version__}.exe"
    (dist_dir / f"{exe}.sha256").write_text(f"{'ab' * 32}  {exe}", encoding="ascii")
    notes = release_notes.notes(f"v{__version__}", dist_dir, root=tmp_path)
    assert notes.startswith("- One thing.\n- Another.\n\n## Install")
    assert "Old." not in notes and "ab" * 32 in notes and "Run anyway" in notes and exe in notes


def test_release_notes_refuse_a_wrong_tag_or_a_missing_build(tmp_path, capsys):
    with pytest.raises(ValueError, match="does not match"):
        release_notes.notes("v0.0.0-wrong", tmp_path)
    with pytest.raises(ValueError, match="missing"):
        release_notes.notes(f"v{__version__}", tmp_path)
    assert release_notes.main(["v0.0.0-wrong", str(tmp_path)]) == 1
    assert "does not match" in capsys.readouterr().err
    assert release_notes.main([]) == 2
    with pytest.raises(ValueError, match="no section"):
        release_notes.changelog_section(CHANGELOG, "9.9.9")


def test_release_notes_main_prints_them(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(release_notes, "notes", lambda tag, d: f"notes for {tag}")
    assert release_notes.main(["v1", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "notes for v1\n"


def test_the_real_changelog_has_a_section_for_this_version():
    section = release_notes.changelog_section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), __version__)
    assert "\n- " in "\n" + section and release_notes.version() == __version__  # bullets, maybe under ### groups


# --- sitecustomize.py: the user's own site-packages stay out of the bundle -------------------------------------------

def test_sitecustomize_takes_the_user_site_off_the_path(tmp_path, monkeypatch):
    user_site = str(tmp_path / "user-site")
    monkeypatch.setattr(sys, "path", [*sys.path, user_site])
    monkeypatch.setattr(site, "USER_SITE", user_site)
    monkeypatch.setattr(site, "ENABLE_USER_SITE", True)
    monkeypatch.delenv("PYTHONNOUSERSITE", raising=False)
    load("sitecustomize")
    assert user_site not in sys.path and site.ENABLE_USER_SITE is False
    assert os.environ["PYTHONNOUSERSITE"] == "1"  # children never add it at all


# --- iris.iss and the workflow: what the installer promises ----------------------------------------------------------

def define(name: str) -> str:
    match = re.search(rf'^#define {name} "([^"]*)"', ISS, re.M)
    assert match, name
    return match.group(1)


def test_installer_names_match_the_app():
    assert define("AppUserModelID") == app.APP_ID  # the taskbar groups the shortcut and the window together
    assert define("AppMutexName") == app.APP_MUTEX  # setup and uninstall close a running Iris first
    assert define("AppExe") == "Iris.exe"


def test_shortcuts_start_the_app_and_start_at_login_is_optional():
    assert define("AppArgs") == "-s -m iris app"
    assert re.search(r'^Name: "\{userprograms\}\\\{#AppName\}"; Filename: "\{app\}\\\{#AppExe\}"; '
                     r'Parameters: "\{#AppArgs\}"', ISS, re.M)
    login = next(line for line in ISS.splitlines() if line.startswith('Name: "{userstartup}'))
    assert '"{#AppArgs} --background"' in login and "Tasks: startatlogin" in login
    desktop = next(line for line in ISS.splitlines() if line.startswith('Name: "{userdesktop}'))
    assert "Tasks: desktopicon" in desktop
    tasks = [line for line in ISS.splitlines() if line.startswith('Name: "desktopicon"') or
             line.startswith('Name: "startatlogin"')]
    assert len(tasks) == 2 and all("Flags: unchecked" in t for t in tasks)  # both off unless the user ticks them


def test_installs_per_user_without_admin_rights():
    assert "PrivilegesRequired=lowest" in ISS
    assert "DefaultDirName={localappdata}\\Programs\\Iris" in ISS


def test_uninstall_keeps_the_user_data_unless_ticked():
    section = ISS[ISS.index("[UninstallDelete]"):ISS.index("[Code]")]
    entries = [line for line in section.splitlines() if line.startswith("Type:")]
    assert entries and not any(".iris" in e for e in entries)
    assert 'Type: filesandordirs; Name: "{app}"' not in entries  # only the folders Python writes into
    assert ISS.count("DelTree(") == 1
    assert "if (CurUninstallStep = usPostUninstall) and RemoveData then" in ISS
    assert "Box.Checked := False;" in ISS  # the box starts unticked
    assert "if UninstallSilent() or" in ISS  # a silent uninstall never removes it


def test_never_bundles_the_wake_word_models_or_piper():
    assert "openWakeWord models are CC BY-NC-SA and must not be bundled" in BUILD
    assert '"piper", "torch", "nvidia"' in BUILD
    assert '$Extras = "voice,office,browser,app"' in BUILD  # not the gpu, piper or tts extras


def test_release_workflow_only_makes_drafts():
    assert "gh release create" in WORKFLOW and "--draft" in WORKFLOW
    assert "--draft=false" not in WORKFLOW and "gh release edit" not in WORKFLOW
    assert "isDraft" in WORKFLOW  # never uploads to a published release


def test_no_em_dashes_in_the_installer_texts():
    texts = [p for p in INSTALLER.iterdir() if p.suffix in (".py", ".ps1", ".iss", ".md")]
    assert len(texts) >= 7
    for path in [*texts, ROOT / ".github" / "workflows" / "installer.yml"]:
        assert "\u2014" not in path.read_text(encoding="utf-8"), path
