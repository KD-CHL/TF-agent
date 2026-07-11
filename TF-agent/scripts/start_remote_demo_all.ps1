# TF-agent remote demo launcher (opens 3 windows + auto-restart Streamlit with ngrok URL)
# Usage: cd e:\Code\GEE\TF-agent ; .\scripts\start_remote_demo_all.ps1

param(
    [string]$CondaEnv = "gwx",
    [int]$StreamlitPort = 8501,
    [int]$GatewayPort = 9080,
    [int]$NgrokPollSeconds = 120
)

$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RepoRoot = Split-Path -Parent $AppRoot
$NgrokExe = Join-Path $RepoRoot "ngrok\ngrok.exe"
$NgrokMainCfg = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
$NgrokTunnelCfg = Join-Path $RepoRoot "ngrok\cstf-tunnels.yml"

function Write-Step([string]$Msg) {
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor Cyan
}

function Escape-Sq([string]$s) {
    return ($s -replace "'", "''")
}

function Invoke-DemoWindow([string]$Title, [string[]]$Lines) {
    $app = Escape-Sq $AppRoot
    $titleEsc = Escape-Sq $Title
    $block = @(
        "`$Host.UI.RawUI.WindowTitle = '$titleEsc'"
        "Set-Location '$app'"
        "`$env:HTTP_PROXY = ''"
        "`$env:HTTPS_PROXY = ''"
        "`$env:ALL_PROXY = ''"
        "`$env:http_proxy = ''"
        "`$env:https_proxy = ''"
        "conda activate $CondaEnv"
    ) + $Lines
    $cmd = $block -join "`n"
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $cmd
    ) | Out-Null
}

function Wait-NgrokHttpsUrl {
    param([int]$TimeoutSec = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 4
            $tunnel = $resp.tunnels | Where-Object { $_.public_url -match '^https://' } | Select-Object -First 1
            if ($tunnel -and $tunnel.public_url) {
                return ($tunnel.public_url.Trim().TrimEnd('/'))
            }
        } catch {
        }
        Start-Sleep -Seconds 2
    }
    return $null
}

function Stop-ListenerOnPort([int]$Port) {
    try {
        $pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($procId in $pids) {
            if ($procId -and $procId -gt 0) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  TF-agent Remote Demo Launcher" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "App:  $AppRoot"
Write-Host "Repo: $RepoRoot"

if (-not (Test-Path (Join-Path $AppRoot "app.py"))) {
    throw "app.py not found under $AppRoot"
}

if (-not (Test-Path $NgrokExe)) {
    throw "ngrok not found: $NgrokExe"
}

if (-not (Test-Path $NgrokTunnelCfg)) {
    throw "ngrok tunnel config not found: $NgrokTunnelCfg"
}

Stop-ListenerOnPort $StreamlitPort
Stop-ListenerOnPort 8765
Stop-ListenerOnPort $GatewayPort
Start-Sleep -Seconds 1

Write-Step "Step 1/4: Start Streamlit (temporary)"
Invoke-DemoWindow "TF-agent [1] Streamlit" @(
    "Write-Host '[TF-agent] Streamlit starting on port $StreamlitPort ...' -ForegroundColor Yellow"
    "Write-Host '[TF-agent] Local URL: http://127.0.0.1:$StreamlitPort' -ForegroundColor Gray"
    "streamlit run app.py --server.address 0.0.0.0 --server.port $StreamlitPort"
)

Start-Sleep -Seconds 6

Write-Step "Step 2/4: Start gateway on port $GatewayPort"
Invoke-DemoWindow "TF-agent [2] Gateway" @(
    "Write-Host '[TF-agent] Gateway starting on port $GatewayPort ...' -ForegroundColor Yellow"
    "python cstf_gateway.py"
)

Start-Sleep -Seconds 3

Write-Step "Step 3/4: Start ngrok tunnel"
$ngrokExeEsc = Escape-Sq $NgrokExe
$ngrokMainEsc = Escape-Sq $NgrokMainCfg
$ngrokTunnelEsc = Escape-Sq $NgrokTunnelCfg
Invoke-DemoWindow "TF-agent [3] ngrok" @(
    "Write-Host '[TF-agent] ngrok -> gateway $GatewayPort' -ForegroundColor Yellow"
    "Write-Host '[TF-agent] Dashboard: http://127.0.0.1:4040' -ForegroundColor Gray"
    "& '$ngrokExeEsc' start cstf --config '$ngrokMainEsc' --config '$ngrokTunnelEsc'"
)

Write-Step "Step 4/4: Wait for ngrok URL and restart Streamlit"
Write-Host "Polling ngrok API (max ${NgrokPollSeconds}s) ..." -ForegroundColor Gray

$publicUrl = Wait-NgrokHttpsUrl -TimeoutSec $NgrokPollSeconds
if (-not $publicUrl) {
    Write-Host ""
    Write-Host "Could not read ngrok public URL automatically." -ForegroundColor Red
    Write-Host "Check window [3] ngrok for the Forwarding https URL, then run in Streamlit window:" -ForegroundColor Red
    Write-Host '  $env:CSTF_GLOBE_PUBLIC_URL = "https://YOUR-URL.ngrok-free.dev"' -ForegroundColor Yellow
    Write-Host "  streamlit run app.py --server.address 0.0.0.0 --server.port $StreamlitPort" -ForegroundColor Yellow
    exit 1
}

Write-Host "Public URL: $publicUrl" -ForegroundColor Green

try {
    Set-Clipboard -Value $publicUrl
    Write-Host "(copied to clipboard)" -ForegroundColor Gray
} catch {
}

Write-Host "Restarting Streamlit with CSTF_GLOBE_PUBLIC_URL ..." -ForegroundColor Gray
Stop-ListenerOnPort $StreamlitPort
Start-Sleep -Seconds 2

$urlEsc = Escape-Sq $publicUrl
Invoke-DemoWindow "TF-agent [1] Streamlit + Public URL" @(
    "`$env:CSTF_GLOBE_PUBLIC_URL = '$urlEsc'"
    "Write-Host '[TF-agent] CSTF_GLOBE_PUBLIC_URL =' `$env:CSTF_GLOBE_PUBLIC_URL -ForegroundColor Green"
    "Write-Host '[TF-agent] Share this link with remote users:' `$env:CSTF_GLOBE_PUBLIC_URL -ForegroundColor Green"
    "Write-Host '[TF-agent] Local debug: http://127.0.0.1:$StreamlitPort' -ForegroundColor Gray"
    "streamlit run app.py --server.address 0.0.0.0 --server.port $StreamlitPort"
)

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Ready" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Remote URL (share with others):" -ForegroundColor White
Write-Host "  $publicUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Keep these 3 windows open:" -ForegroundColor Gray
Write-Host "    [1] Streamlit   [2] Gateway   [3] ngrok" -ForegroundColor Gray
Write-Host ""
Write-Host "  To stop: .\scripts\stop_remote_demo.ps1" -ForegroundColor Gray
Write-Host ""
