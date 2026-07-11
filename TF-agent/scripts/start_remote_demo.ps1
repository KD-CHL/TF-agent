# 跨校区远程演示：先在本机单独开 ngrok，再把地址填到下方环境变量后运行本脚本。
# 用法见 REMOTE_DEMO.md

param(
    [Parameter(Mandatory = $true)]
    [string]$GlobePublicUrl,
    [string]$StreamlitPort = "8501"
)

$GlobePublicUrl = $GlobePublicUrl.Trim().TrimEnd("/")
if (-not $GlobePublicUrl.StartsWith("http")) {
    Write-Error "GlobePublicUrl 需以 http:// 或 https:// 开头，例如 https://xxxx.ngrok-free.app"
    exit 1
}

$env:CSTF_GLOBE_PUBLIC_URL = $GlobePublicUrl
Write-Host "[CSTF] CSTF_GLOBE_PUBLIC_URL = $GlobePublicUrl"
Write-Host "[CSTF] 请另开终端执行: ngrok http $StreamlitPort  （把得到的链接发给导师）"
Write-Host "[CSTF] 请另开终端执行: ngrok http 8765  （地址需与 GlobePublicUrl 一致）"
Write-Host "[CSTF] 启动 Streamlit ..."

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
streamlit run app.py --server.address 0.0.0.0 --server.port $StreamlitPort
