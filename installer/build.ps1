<#
.SYNOPSIS
  Build IrisSetup-<version>.exe: a private Python with Iris and its packages, wrapped by Inno Setup.

.DESCRIPTION
  1. Copies the Python that runs this script (an official CPython 3.12 for Windows, x64) to build\Iris, without its
     tests, docs, headers and site-packages. Tcl/Tk come along, so the overlay works.
  2. Installs Iris with the voice, office, browser and app extras, plus pip and pytest, into its Lib\site-packages.
  3. Refuses to continue if an openWakeWord model (CC BY-NC-SA) or Piper (GPL) ended up in the bundle.
  4. Adds Lib\sitecustomize.py (keeps the user's own site-packages out), Iris.exe (pythonw.exe with the Iris icon,
     set by rcedit), the license list, and compiles the Python files.
  5. Checks the bundle imports, then runs Inno Setup: dist\IrisSetup-<version>.exe and its .sha256.

  See docs/desktop-app.md (section 3) for why this is a full CPython and not PyInstaller or the embeddable package.

.EXAMPLE
  .\installer\build.ps1                       # with `python` on PATH being CPython 3.12 x64
  .\installer\build.ps1 -Python C:\Python312\python.exe -SkipInstaller
#>
[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Stage = "build\Iris",
    [string]$Out = "dist",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RceditVersion = "v2.0.0"
$RceditSha256 = "unpinned"  # pinned from the first CI build; a changed download stops the build
$Extras = "voice,office,browser,app"

function Invoke-Checked {
    # a plain function on purpose: "-m", "-s" and "-c" go to the program, not to PowerShell's parameter binding
    $Exe, $Rest = $args
    & $Exe @Rest
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Rest -join ' ') failed with exit code $LASTEXITCODE" }
}

$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    $Version = (Select-String -Path "iris\__init__.py" -Pattern '^__version__ = "([^"]+)"').Matches[0].Groups[1].Value
    if ($env:GITHUB_REF_TYPE -eq "tag" -and $env:GITHUB_REF_NAME -ne "v$Version") {
        throw "tag $env:GITHUB_REF_NAME does not match iris.__version__ $Version"
    }
    $PyInfo = (& $Python -c "import platform, sys; print(sys.base_prefix); print(platform.python_version()); print(platform.machine())")
    $PyHome, $PyVersion, $PyMachine = $PyInfo
    if (-not $PyVersion.StartsWith("3.12.") -or $PyMachine -ne "AMD64") {
        throw "build with CPython 3.12 for x64 (found $PyVersion on $PyMachine at $PyHome)"
    }
    Write-Host "Iris $Version, bundling Python $PyVersion from $PyHome"

    # --- 1. the Python ---------------------------------------------------------------------------------------------
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
    New-Item -ItemType Directory -Force -Path $Stage, $Out | Out-Null
    $StageFull = (Resolve-Path $Stage).Path
    $Skip = @("Lib\site-packages", "Lib\test", "Lib\idlelib", "Lib\turtledemo", "Lib\ensurepip", "Lib\lib2to3",
              "Doc", "Tools", "Scripts", "include", "libs", "tcl\tk8.6\demos") | ForEach-Object { Join-Path $PyHome $_ }
    robocopy $PyHome $StageFull /E /NFL /NDL /NJH /NJS /NP /XD @Skip __pycache__ /XF *.pyc *.pdb python*_d.* | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying Python failed (robocopy $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
    $Site = Join-Path $StageFull "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $Site | Out-Null

    # --- 2. Iris and its packages ----------------------------------------------------------------------------------
    Invoke-Checked $Python -m pip install --disable-pip-version-check --no-input --no-warn-script-location `
        --no-compile --target $Site pip pytest ".[$Extras]"
    if (Test-Path "$Site\bin") { Remove-Item -Recurse -Force "$Site\bin" }  # console scripts; the app needs none

    # --- 3. what must never be in it -------------------------------------------------------------------------------
    $Models = @(Get-ChildItem -Path "$Site\openwakeword" -Recurse -Include *.onnx, *.tflite -ErrorAction SilentlyContinue)
    if ($Models.Count -gt 0) {
        throw "openWakeWord models are CC BY-NC-SA and must not be bundled: $($Models.FullName -join ', ')"
    }
    foreach ($Forbidden in "piper", "torch", "nvidia") {
        if (Test-Path (Join-Path $Site $Forbidden)) { throw "$Forbidden must not be bundled (it is an optional pack)" }
    }

    # --- 4. finishing touches --------------------------------------------------------------------------------------
    Copy-Item "installer\sitecustomize.py" "$StageFull\Lib\sitecustomize.py"
    Copy-Item "LICENSE" "$StageFull\LICENSE-Iris.txt"
    Copy-Item "$StageFull\pythonw.exe" "$StageFull\Iris.exe"
    $Tools = "build\tools"
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    $Rcedit = Join-Path $Tools "rcedit-x64.exe"
    if (-not (Test-Path $Rcedit)) {
        Invoke-WebRequest -UseBasicParsing -OutFile $Rcedit `
            -Uri "https://github.com/electron/rcedit/releases/download/$RceditVersion/rcedit-x64.exe"
    }
    $Hash = (Get-FileHash $Rcedit -Algorithm SHA256).Hash.ToLower()
    if ($Hash -ne $RceditSha256) {
        Remove-Item $Rcedit
        throw "rcedit-x64.exe has SHA-256 $Hash, expected $RceditSha256"
    }
    Invoke-Checked $Rcedit "$StageFull\Iris.exe" --set-icon "iris\assets\iris.ico" `
        --set-version-string FileDescription "Iris" --set-version-string ProductName "Iris" `
        --set-version-string CompanyName "skl" --set-version-string LegalCopyright "MIT License" `
        --set-version-string OriginalFilename "Iris.exe" --set-version-string InternalName "Iris" `
        --set-file-version $Version --set-product-version $Version
    $StagePython = Join-Path $StageFull "python.exe"
    Invoke-Checked $StagePython -s "installer\licenses.py" "$StageFull\THIRD_PARTY_LICENSES.txt"
    & $StagePython -s -m compileall -q -j 0 "$StageFull\Lib" | Out-Null  # a few packages ship files for other
    $global:LASTEXITCODE = 0                                              # Pythons; those simply stay uncompiled

    # --- 5. check, then package ------------------------------------------------------------------------------------
    Invoke-Checked $StagePython -c @"
import os, sys, site, tkinter, iris, webview, faster_whisper, openwakeword, sounddevice, pywinauto, pytest, pip, docx, pptx, openpyxl, websocket
tkinter.Tcl()
assert site.ENABLE_USER_SITE is False and os.environ.get('PYTHONNOUSERSITE') == '1', 'sitecustomize did not run'
print('bundle imports ok: iris', iris.__version__, 'on Python', sys.version.split()[0])
"@
    $VcMissing = @(& $StagePython -s "installer\dlls.py" $StageFull)
    if ($LASTEXITCODE -ne 0) { throw "installer\dlls.py failed" }
    if ($VcMissing.Count -gt 0) {
        Write-Host "Needs the Visual C++ runtime from the PC (the installer checks for it):"
        $VcMissing | ForEach-Object { Write-Host "  $_" }
    }
    $Bytes = (Get-ChildItem $StageFull -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("Bundle: {0:N0} MB in {1}" -f ($Bytes / 1MB), $StageFull)
    if ($SkipInstaller) { return }

    $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    $OnPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($OnPath) { $Iscc = $OnPath.Source }
    if (-not (Test-Path $Iscc)) {
        Invoke-Checked choco install innosetup -y --no-progress
        $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    }
    $IsccArgs = @("/Qp", "/DAppVersion=$Version", "/DStageDir=$StageFull", "/O$((Resolve-Path $Out).Path)")
    if ($VcMissing.Count -gt 0) { $IsccArgs += "/DNeedsVCRuntime" }
    Invoke-Checked $Iscc @IsccArgs "installer\iris.iss"
    $Exe = Join-Path $Out "IrisSetup-$Version.exe"
    $Sha = (Get-FileHash $Exe -Algorithm SHA256).Hash.ToLower()
    Set-Content -Path "$Exe.sha256" -Value "$Sha  IrisSetup-$Version.exe" -Encoding ascii -NoNewline
    Write-Host ("Installer: {0} ({1:N1} MB), SHA-256 {2}" -f $Exe, ((Get-Item $Exe).Length / 1MB), $Sha)
}
finally {
    Pop-Location
}
