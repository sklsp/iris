<#
.SYNOPSIS
  Install IrisSetup silently, check what it put where, start the app, then uninstall and check what is left.
  Runs in CI (.github/workflows/installer.yml) on a fresh windows-latest runner. It installs for the current user, so
  on your own PC run it only in Windows Sandbox or a VM.
#>
param([Parameter(Mandatory = $true)][string]$Installer)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Check([bool]$Ok, [string]$What) {
    if (-not $Ok) { throw "FAILED: $What" }
    Write-Host "ok  $What"
}

$App = Join-Path $env:LOCALAPPDATA "Programs\Iris"
$Data = Join-Path $env:USERPROFILE ".iris"
$Programs = [Environment]::GetFolderPath("Programs")
$Desktop = [Environment]::GetFolderPath("Desktop")
$Startup = [Environment]::GetFolderPath("Startup")
$Version = ([IO.Path]::GetFileNameWithoutExtension($Installer)) -replace "^IrisSetup-", ""
$Shell = New-Object -ComObject WScript.Shell

# --- install ---------------------------------------------------------------------------------------------------------
$Log = Join-Path $env:RUNNER_TEMP "iris-setup.log"
$p = Start-Process $Installer -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
    '/TASKS="desktopicon,startatlogin"', "/LOG=`"$Log`"" -Wait -PassThru
Check ($p.ExitCode -eq 0) "installer exits 0 (log: $Log)"
foreach ($f in "Iris.exe", "python.exe", "pythonw.exe", "unins000.exe", "Lib\sitecustomize.py",
               "THIRD_PARTY_LICENSES.txt", "LICENSE-Iris.txt", "LICENSE.txt") {
    Check (Test-Path (Join-Path $App $f)) "installed $f"
}
foreach ($Link in @(@("$Programs\Iris.lnk", "-s -m iris app"), @("$Desktop\Iris.lnk", "-s -m iris app"),
                    @("$Startup\Iris.lnk", "-s -m iris app --background"))) {
    Check (Test-Path $Link[0]) "shortcut $($Link[0])"
    $lnk = $Shell.CreateShortcut($Link[0])
    Check ($lnk.TargetPath -eq (Join-Path $App "Iris.exe") -and $lnk.Arguments -eq $Link[1]) "$($Link[0]) runs Iris.exe $($Link[1])"
}
Check (-not (Get-ChildItem "$App\Lib\site-packages\openwakeword" -Recurse -Include *.onnx, *.tflite)) "no openWakeWord model bundled"
Check (-not (Test-Path "$App\Lib\site-packages\piper")) "Piper not bundled"

# --- the bundled Python ----------------------------------------------------------------------------------------------
$Py = Join-Path $App "python.exe"
$Out = & $Py -s -m iris --version
Check ($LASTEXITCODE -eq 0 -and $Out -eq "iris $Version") "python -m iris --version says iris $Version ($Out)"
$UserSite = (& $Py -s -c "import site; print(site.getusersitepackages())")
New-Item -ItemType Directory -Force -Path $UserSite | Out-Null
Set-Content -Path (Join-Path $UserSite "iris_user_site_probe.py") -Value "x = 1"
& $Py -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('iris_user_site_probe') is not None)"
Check ($LASTEXITCODE -eq 0) "the user's own site-packages stay out of the bundle"
Remove-Item (Join-Path $UserSite "iris_user_site_probe.py")
& $Py -I -c "import iris, pytest"
Check ($LASTEXITCODE -eq 0) "isolated mode (how skills run) still finds iris and pytest"
& $Py -c "import tkinter; tkinter.Tcl(); import clr, webview, faster_whisper, openwakeword, sounddevice, pywinauto"
Check ($LASTEXITCODE -eq 0) "voice, overlay and window packages import"
Write-Host "--- iris doctor (no model server or microphone here, so FIX lines are expected)"
& $Py -s -m iris doctor
$global:LASTEXITCODE = 0

# --- the app ---------------------------------------------------------------------------------------------------------
$first = Start-Process (Join-Path $App "Iris.exe") -ArgumentList "-s", "-m", "iris", "app", "--background" -PassThru
$up = $false
foreach ($i in 1..60) {
    Start-Sleep -Seconds 1
    try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8765/"; $up = $r.StatusCode -eq 200 } catch { }
    if ($up -or $first.HasExited) { break }
}
if (Test-Path "$Data\logs\app.log") { Write-Host "--- app.log"; Get-Content "$Data\logs\app.log" -Tail 40 }
Check ($up -and -not $first.HasExited) "Iris.exe starts and serves the dashboard on 127.0.0.1:8765"
$second = Start-Process (Join-Path $App "Iris.exe") -ArgumentList "-s", "-m", "iris", "app", "--background" -PassThru
Check ($second.WaitForExit(30000) -and $second.ExitCode -eq 0) "a second start hands over to the first and exits"
Check (-not $first.HasExited) "the first app keeps running"
Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force
Start-Sleep -Seconds 2

# --- uninstall -------------------------------------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $Data | Out-Null
Set-Content -Path "$Data\keep-me.txt" -Value "user data"
$p = Start-Process (Join-Path $App "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Check ($p.ExitCode -eq 0) "uninstaller exits 0"
foreach ($i in 1..60) { if (-not (Test-Path $App)) { break }; Start-Sleep -Seconds 1 }
if (Test-Path $App) { Get-ChildItem $App -Recurse | Select-Object -First 20 FullName | Out-Host }
Check (-not (Test-Path $App)) "the app folder is gone"
foreach ($Link in "$Programs\Iris.lnk", "$Desktop\Iris.lnk", "$Startup\Iris.lnk") {
    Check (-not (Test-Path $Link)) "removed $Link"
}
Check (Test-Path "$Data\keep-me.txt") "~\.iris is kept"
Write-Host "smoke test passed"
