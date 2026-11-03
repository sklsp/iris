"""Which Microsoft Visual C++ runtime DLLs the bundle's binaries import without shipping them (installer/build.ps1).

Most wheels carry their own copy of the runtime (delvewheel renames it, msvcp140-1a2b.dll), and the bundled Python
ships vcruntime140.dll next to Iris.exe. A binary that imports msvcp140.dll and finds no copy next to itself or next
to Iris.exe needs the Visual C++ Redistributable on the PC. The build tells the installer, which then checks for it
and points to Microsoft's download page when it is missing.

    python installer/dlls.py build/Iris      prints one "<binary>: <dll>" line per missing runtime DLL
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

VC_RUNTIME = frozenset({"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
                        "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll"})
BINARIES = (".dll", ".pyd", ".exe")


def imports(data: bytes) -> list[str]:
    """The DLL names in a PE file's import and delay-load tables; [] for anything that is not a PE file."""
    try:
        if data[:2] != b"MZ":
            return []
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        count, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        directories = opt + (112 if struct.unpack_from("<H", data, opt)[0] == 0x20B else 96)  # PE32+ or PE32
        sections = []
        for i in range(count):
            vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, opt + opt_size + 40 * i + 8)
            sections.append((vaddr, max(vsize, rsize), raddr))

        def offset(rva: int) -> int | None:
            for vaddr, size, raddr in sections:
                if vaddr <= rva < vaddr + size:
                    return rva - vaddr + raddr
            return None

        names = []
        for index, size, name_at in ((1, 20, 12), (13, 32, 4)):  # import table, delay-load table
            rva = struct.unpack_from("<I", data, directories + 8 * index)[0]
            at = offset(rva) if rva else None
            while at is not None and at + size <= len(data):
                name_rva = struct.unpack_from("<I", data, at + name_at)[0]
                where = offset(name_rva) if name_rva else None
                if where is None:
                    break
                names.append(data[where:data.index(b"\0", where)].decode("ascii", "replace"))
                at += size
        return names
    except (struct.error, ValueError):
        return []


def missing_runtime(root: Path) -> list[str]:
    """"<binary>: <dll>" for every Visual C++ runtime DLL a binary imports that is neither next to it nor in root."""
    folders: dict[Path, set[str]] = {}

    def files_in(folder: Path) -> set[str]:
        if folder not in folders:
            folders[folder] = {p.name.lower() for p in folder.iterdir() if p.is_file()}
        return folders[folder]

    out = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in BINARIES or not path.is_file():
            continue
        for name in sorted({n.lower() for n in imports(path.read_bytes())} & VC_RUNTIME):
            if name not in files_in(path.parent) and name not in files_in(root):
                out.append(f"{path.relative_to(root).as_posix()}: {name}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python installer/dlls.py <bundle folder>", file=sys.stderr)
        return 2
    for line in missing_runtime(Path(argv[0])):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
