# Building the Windows installer

`IrisSetup-<version>.exe` is built by GitHub Actions (`.github/workflows/installer.yml`): on every pull request that
touches this folder, and on a version tag, where it is attached to a **draft** release. A person publishes it.

To build it on your own PC you need Windows 10 or 11 (x64), the official CPython 3.12 for Windows (from python.org,
not the Microsoft Store one) and [Inno Setup 6](https://jrsoftware.org/isinfo.php). Then, from the repository root:

```powershell
.\installer\build.ps1 -Python C:\Path\To\Python312\python.exe
```

The result is `dist\IrisSetup-<version>.exe` and its `.sha256`. `build\Iris` holds the unpacked bundle; run
`build\Iris\python.exe -s -m iris doctor` to try it without installing. `-SkipInstaller` stops before Inno Setup.

| File | What it does |
|---|---|
| `build.ps1` | copies the Python, installs Iris with the `voice`, `office`, `browser` and `app` extras plus pip and pytest, refuses openWakeWord models, Piper, PyTorch and NVIDIA's libraries, makes `Iris.exe`, runs Inno Setup |
| `iris.iss` | the Inno Setup script: per-user install, Start menu entry, optional desktop shortcut and start at sign-in, WebView2 and Visual C++ checks, an uninstall that keeps `~\.iris` unless ticked |
| `sitecustomize.py` | goes into the bundle's `Lib`: keeps the user's own `pip install --user` packages out |
| `dlls.py` | finds binaries that need Microsoft's Visual C++ runtime from the PC |
| `licenses.py` | writes `THIRD_PARTY_LICENSES.txt` from the packages that actually ship |
| `release_notes.py` | the draft release's text: the CHANGELOG section, install steps and the SHA-256 |
| `smoke.ps1` | installs silently, checks the shortcuts and the bundle, starts the app, uninstalls (CI; on your PC only in Windows Sandbox or a VM) |

The installer is not code-signed, so Windows SmartScreen warns before it runs ("More info", then "Run anyway").
docs/desktop-app.md explains the choices.
