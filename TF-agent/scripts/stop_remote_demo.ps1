# Stop TF-agent remote demo services (ports 8501 / 8765 / 9080 and ngrok)
param(
    [int]$StreamlitPort = 8501,
    [int]$GatewayPort = 9080
)

function Stop-ListenerOnPort([int]$Port) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        $stopped = @()
        foreach ($c in $conns) {
            $procId = $c.OwningProcess
            if ($procId -and $procId -gt 0 -and $stopped -notcontains $procId) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $stopped += $procId
                Write-Host "Stopped PID $procId on port $Port"
            }
        }
    } catch {
        Write-Host "Could not query port $Port (skipped)"
    }
}

Write-Host "Stopping TF-agent remote demo services..." -ForegroundColor Cyan
Stop-ListenerOnPort $StreamlitPort
Stop-ListenerOnPort 8765
Stop-ListenerOnPort $GatewayPort

Get-Process -Name "ngrok" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped ngrok PID $($_.Id)"
}

Write-Host "Done." -ForegroundColor Green
