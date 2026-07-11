# 一条 ngrok 同时转发 8765（地球）和 8501（Streamlit）
# 启动后打开 http://127.0.0.1:4040 查看两个 https 地址

$ngrok = "E:\Code\GEE\ngrok\ngrok.exe"
$mainCfg = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
$tunnelCfg = "E:\Code\GEE\ngrok\cstf-tunnels.yml"

if (-not (Test-Path $ngrok)) {
    Write-Error "找不到 ngrok: $ngrok"
    exit 1
}

# 避免走失效的系统代理（如 7890）
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:http_proxy = ""
$env:https_proxy = ""

Write-Host "[CSTF] 启动双隧道 globe:8765 + streamlit:8501 ..."
Write-Host "[CSTF] 面板: http://127.0.0.1:4040"
& $ngrok start --all --config $mainCfg --config $tunnelCfg
