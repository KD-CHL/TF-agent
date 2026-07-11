# 导师跨校区远程试用指南

你的电脑作为**唯一服务器**：VPN、GEE、模型权重、本地数据都在你这台机器上跑。导师在另一个校区只需用浏览器打开你提供的链接，**不需要自己开 VPN**。

## 架构

```
导师电脑（另一校区）  →  浏览器打开 https://你的链接
                              ↓
                    ngrok / Cloudflare Tunnel
                              ↓
你的电脑（本校区，已开 VPN + Clash）
  ├── Streamlit :8501
  ├── 三维地球  :8765
  ├── 模型权重 + 推理
  └── GEE / Copilot（走本机代理）
```

## 一次性准备

1. 安装 [ngrok](https://ngrok.com/) 并登录（免费账号即可演示）。
2. 本机 Clash/VPN **保持开启**，侧栏 GEE 代理端口与 Clash 一致（默认 `7892`）。
3. 确认本机单独打开 `http://localhost:8501` 时系统功能正常。

## 每次演示前（推荐：单端口网关 + 一条 ngrok，支持远程 3D 地球）

免费 ngrok 通常只给一个 https 地址，因此用 **网关 9080** 合并 Streamlit 与地球服务。

### 方式 A：一键启动（推荐）

在 PowerShell 中执行（或双击 `scripts\start_remote_demo.bat`）：

```powershell
cd e:\Code\GEE\TF-agent
.\scripts\start_remote_demo_all.ps1
```

脚本会自动：

1. 打开 **[1] Streamlit** 窗口（8501）
2. 打开 **[2] Gateway** 窗口（9080）
3. 打开 **[3] ngrok** 窗口
4. 从 ngrok API 读取公网地址，**重启 Streamlit** 并注入 `CSTF_GLOBE_PUBLIC_URL`

完成后终端会打印**发给导师的链接**（并尝试复制到剪贴板）。

演示结束后：

```powershell
.\scripts\stop_remote_demo.ps1
```

---

### 方式 B：手动四步（与一键脚本等价）

#### 终端 1：启动 Streamlit（先不设公网地址）

```powershell
conda activate gwx
cd e:\Code\GEE\TF-agent
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

本机访问请用 `http://127.0.0.1:8501`（不要用 `0.0.0.0`）。

### 终端 2：启动合并网关

```powershell
conda activate gwx
cd e:\Code\GEE\TF-agent
python cstf_gateway.py
```

或：`.\scripts\start_gateway.ps1`

### 终端 3：一条 ngrok 转发网关

```powershell
E:\Code\GEE\TF-agent\scripts\start_ngrok_gateway.ps1
```

记下 **Forwarding** 地址，例如 `https://abc123.ngrok-free.dev`。

### 终端 1 重启 Streamlit（填入同一 ngrok 地址）

```powershell
# Ctrl+C 停掉后
$env:CSTF_GLOBE_PUBLIC_URL = "https://abc123.ngrok-free.dev"
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

`CSTF_GLOBE_PUBLIC_URL` 必须与 ngrok 地址**完全相同**（三维地球走 `/globe` 路径）。

## 发给导师

把终端 2 的链接发给导师，例如：

```
https://abc123.ngrok-free.app
```

导师用 Chrome / Edge 打开即可。首次访问 ngrok 免费版可能要点一次 **Visit Site**。

## 演示时注意

| 项目 | 说明 |
|------|------|
| 你的电脑 | 必须开机、联网、VPN 开着，三个终端都不要关 |
| 休眠 | 合盖会断服务，演示时关闭休眠 |
| 人数 | 建议同时 1～2 人，多了会占你上行带宽和 GPU |
| 安全 | 链接相当于远程操作你的电脑，只发给导师，演示完关闭 ngrok |
| 费用 | GEE 下载、Copilot API 都走你的账号配额 |

## 自检

在你本机浏览器打开导师同款链接前，可先测：

```powershell
curl http://127.0.0.1:8765/health
# 应返回 ok

curl https://你的地球-ngrok地址.ngrok-free.app/health
# 应返回 ok
```

若主页面能开但地球是黑的：检查 `CSTF_GLOBE_PUBLIC_URL` 是否与终端 3 的 ngrok 地址一致，并重启 Streamlit。

## 常见问题

**Q：导师那边 GEE 下载失败？**  
A：VPN/代理必须在你电脑上开着，不是导师电脑上。

**Q：推理很慢？**  
A：算力在你本机，跨校区访问还会受你上行带宽影响。

**Q：学校网不让 ngrok？**  
A：可换 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)（同样要两条隧道或自定义域名路由）。

**Q：想长期给导师用？**  
A：建议实验室固定一台服务器 + 云盘同步权重，或租一台带 GPU 的云主机部署，比长期开 ngrok 更稳。
