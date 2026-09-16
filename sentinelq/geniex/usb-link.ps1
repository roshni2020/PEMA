# USB link between the laptop and the UNO Q. Works with no Wi-Fi at all.
#   laptop -> board : adb forward 7000   (dashboard at http://127.0.0.1:7000 on the laptop)
#   board  -> laptop: adb reverse 18182  (board's 127.0.0.1:18182 = laptop NPU server)
#                     + usb-relay.sh on the board so the app container can reach it
# Run after every board reboot or USB replug:  .\usb-link.ps1
# If the app is stopped (board rebooted), also run:  .\deploy-board.ps1
$adb = "$env:LOCALAPPDATA\Arduino15\packages\arduino\tools\adb\32.0.0\adb.exe"
if (-not (Test-Path $adb)) { $adb = "adb" }

& $adb forward tcp:7000 tcp:7000 | Out-Null
& $adb reverse tcp:18182 tcp:18182 | Out-Null

$src = Join-Path $PSScriptRoot "usb-relay.sh"
$tmp = Join-Path $env:TEMP "usb-relay.sh"
$t = [IO.File]::ReadAllText($src) -replace "`r`n", "`n"
[IO.File]::WriteAllText($tmp, $t, (New-Object Text.UTF8Encoding $false))
& $adb push $tmp /home/arduino/usb-relay.sh | Out-Null
& $adb shell "bash /home/arduino/usb-relay.sh"

$state = (& $adb shell "arduino-app-cli app list 2>/dev/null | grep -i sentinelq") -join ""
if ($state -match "running") { Write-Host "app: running" } else { Write-Host "app: NOT running -> run .\deploy-board.ps1" }
Write-Host "Dashboard: http://127.0.0.1:7000"
