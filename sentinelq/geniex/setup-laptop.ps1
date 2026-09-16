# One-time setup for the Snapdragon X Elite laptop (Windows ARM64) that hosts the NPU.
# Run in PowerShell:  .\setup-laptop.ps1
$ErrorActionPreference = "Stop"

# 1. ARM64 Python + VC++ runtime (GenieX's native DLLs need it)
winget install --id Python.Python.3.13 --architecture arm64 --silent --accept-package-agreements --accept-source-agreements
winget install --id Microsoft.VCRedist.2015+.arm64 --silent --accept-package-agreements --accept-source-agreements

$py = "$env:LOCALAPPDATA\Programs\Python\Python313-arm64\python.exe"
& $py -c "import platform; assert platform.machine()=='ARM64', 'need ARM64 python'"

# 2. GenieX Python SDK + server deps
& $py -m pip install -U geniex openai fastapi uvicorn pydantic

# 3. Sanity: the Hexagon NPU must show up as a compute unit
& $py -c "import geniex; geniex.init(); print('GenieX', geniex.version()); [print(rt, '->', geniex.get_compute_unit_list(rt)) for rt in geniex.get_runtime_list()]"
# Expected: llama_cpp -> [GPUOpenCL, HTP0 (Hexagon), CPU]   qairt -> [NPU]

# 4. Let the UNO Q reach the server over Wi-Fi
New-NetFirewallRule -DisplayName "SentinelQ GenieX 18182" -Direction Inbound -Protocol TCP -LocalPort 18182 -Action Allow -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "GenieX serve 18181" -Direction Inbound -Protocol TCP -LocalPort 18181 -Action Allow -ErrorAction SilentlyContinue | Out-Null

Write-Host ""
Write-Host "Laptop IPs (use the Wi-Fi one on the board as GENIEX_URL=http://<ip>:18182/v1):"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object InterfaceAlias, IPAddress
Write-Host ""
Write-Host "Start the NPU server with:  $py npu_server.py --vlm"
