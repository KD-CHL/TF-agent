# CSTF gateway: port 9080 merges Streamlit 8501 + globe 8765
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$env:HTTP_PROXY = ""
$env:HTTPS_PROXY = ""
$env:ALL_PROXY = ""
$env:http_proxy = ""
$env:https_proxy = ""
conda activate gwx
python cstf_gateway.py
