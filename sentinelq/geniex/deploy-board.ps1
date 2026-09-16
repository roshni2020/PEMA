# Deploy SentinelQ to the UNO Q over USB (ADB) and (re)start it. No desktop App Lab needed:
# the board ships with arduino-app-cli, Docker and all bricks preinstalled.
#   .\deploy-board.ps1            push app.yaml, python/, sketch/, assets/ and restart
#   .\deploy-board.ps1 -Logs      also tail the app logs
param([switch]$Logs)
$ErrorActionPreference = "Stop"
$adb = "$env:LOCALAPPDATA\Arduino15\packages\arduino\tools\adb\32.0.0\adb.exe"
if (-not (Test-Path $adb)) { $adb = "adb" }
$root = Split-Path $PSScriptRoot -Parent                    # ...\sentinelq
$stage = Join-Path $env:TEMP "sentinelq-stage"
$remote = "/home/arduino/ArduinoApps/sentinelq"

# Stage with LF line endings and UTF-8 without BOM (the CLI parses app.yaml strictly)
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory $stage | Out-Null
foreach ($d in 'python', 'sketch', 'assets') { Copy-Item (Join-Path $root $d) (Join-Path $stage $d) -Recurse }
Copy-Item (Join-Path $root 'app.yaml') $stage
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -File | ForEach-Object {
    $t = [IO.File]::ReadAllText($_.FullName, [Text.Encoding]::UTF8) -replace "`r`n", "`n"
    [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding $false))
}

& $adb shell "mkdir -p $remote"
& $adb push "$stage\." "$remote/" | Select-Object -Last 1
& $adb forward tcp:7000 tcp:7000 | Out-Null
& $adb shell "cd /home/arduino/ArduinoApps && arduino-app-cli app restart $remote 2>&1 | tail -3"
Write-Host "Dashboard: http://127.0.0.1:7000 (USB)  or  http://<board-ip>:7000 (Wi-Fi)"
if ($Logs) { & $adb shell "sleep 20; docker logs --tail 40 sentinelq-main-1 2>&1" }
# bring the USB path to the laptop NPU server back up (survives Wi-Fi changes)
Start-Sleep 25
& (Join-Path $PSScriptRoot "usb-link.ps1")
