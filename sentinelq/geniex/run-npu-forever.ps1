# Keeps the NPU server alive. The GenieX Hexagon backend occasionally dies with
# "dspqueue_read failed" (a driver hiccup); this restarts it within seconds.
#   .\run-npu-forever.ps1
$py = "$env:LOCALAPPDATA\Programs\Python\Python313-arm64\python.exe"
Set-Location $PSScriptRoot
while ($true) {
  Write-Host ("[{0}] starting npu_server.py --vlm" -f (Get-Date -Format HH:mm:ss))
  & $py npu_server.py --vlm 2>&1 | Where-Object { $_ -notmatch 'gguf:\s+\d+%' }
  Write-Host ("[{0}] server exited, restarting in 5 s" -f (Get-Date -Format HH:mm:ss))
  Start-Sleep 5
}
