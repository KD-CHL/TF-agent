# ngrok single tunnel -> gateway 9080 (Streamlit + 3D globe)
$ngrok = "E:\Code\GEE\ngrok\ngrok.exe"
$mainCfg = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
$tunnelCfg = "E:\Code\GEE\ngrok\cstf-tunnels.yml"

$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""

Write-Host 'CSTF: ngrok -> gateway port 9080'
Write-Host 'CSTF: dashboard http://127.0.0.1:4040'
& $ngrok start cstf --config $mainCfg --config $tunnelCfg
