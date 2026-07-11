# TF-agent（主应用）

基于 `Streamlit + 深度学习推理 + 遥感后处理 + LLM Copilot` 的潮滩遥感分析系统。  
支持批量 GeoTIFF 推理、时空后处理、地图可视化、智能体对话调度。

> 本目录为仓库主应用。研究原型脚本见上级目录 `research/jb/`。

---

## 1. 项目能力

- 批量推理遥感影像，输出单景掩膜 `*_mask.tif`
- 时空后处理融合，输出 `*_Final_p{prob}_c{cnt}.tif`
- 地图叠加展示与资产缓存管理
- 智能体对话（地图跳转、任务触发、知识库检索）
- TIFF 上传对话分析（含大文件与无效像素容错）

---

## 2. 项目结构

```text
TF-agent/
├─ app.py                 # Streamlit 主界面 + 调度 + 地图 + 聊天
├─ agent.py               # 智能体与多模态输入构建
├─ pre_engine.py          # 单景推理引擎
├─ post_engine.py         # 时空后处理与结果合成
├─ e1_engine.py           # 多源一致性诊断（封装 research/jb/E1.py）
├─ m5_engine.py           # 时空异常告警（封装 research/jb/M5.py）
├─ m4_engine.py           # GEE 影像下载
├─ YYnet.py               # CDNet 主模型定义
├─ backbone.py            # 主干网络
├─ modules.py             # 网络模块
├─ assets_registry.json   # 结果资产注册表（自动维护）
├─ requirements.txt
├─ scripts/               # 启动脚本（gateway、ngrok）
└─ .env                   # 本地密钥（勿提交）
```

---

## 3. 环境准备

推荐：

- Python `3.10`
- Conda 环境名：`yynet`

```powershell
conda activate gwx
cd TF-agent
python -m pip install -r requirements.txt
```

---

## 4. `.env` 配置

在 `TF-agent/.env` 中配置（可复制 `.env.example`）：

```env
# 必填：百炼 Key
DASHSCOPE_API_KEY=你的Key

# 模型
QWEN_CHAT_MODEL=qwen-vl-plus
QWEN_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# TIFF 对话输入策略
YYNET_TIFF_MODE=auto
YYNET_ATTACH_GEO_META=1
YYNET_TIFF_AUTO_PNG_MB=12
YYNET_VLM_MAX_SIDE=2048

# 代理（建议清空，避免 Connection error）
HTTP_PROXY=
HTTPS_PROXY=
ALL_PROXY=
GIT_HTTP_PROXY=
GIT_HTTPS_PROXY=
NO_PROXY=localhost,127.0.0.1,::1,dashscope.aliyuncs.com

# Windows OpenMP 兼容
KMP_DUPLICATE_LIB_OK=TRUE
```

### TIFF 策略说明

- `YYNET_TIFF_MODE=auto`：优先原样 TIFF，接口拒收时自动降级 PNG
- `YYNET_TIFF_MODE=native`：强制原样 TIFF（理论信息损失最小，但可能 400）
- `YYNET_TIFF_MODE=png`：始终转 PNG（兼容性最好）

---

## 5. 启动方式

```powershell
conda activate gwx
cd TF-agent
python -m streamlit run app.py --server.port 8501
```

访问：`http://localhost:8501`

---

## 6. 使用流程

1. 在侧边栏选择输入根目录（任务目录）。
2. 配置输出目录、模型权重、矢量约束（可选）。
3. 设置阈值：
   - `Probability (prob_th)`
   - `Absolute Count (min_cnt)`
4. 点击“运行模型”。
5. 在地图查看结果图层，或在聊天区让智能体解释。

---

## 7. 聊天与图片分析（当前实现）

- 用户与智能体消息样式已区分（便于识别）
- 上传图片后输入框自动激活（无需再点）
- 发送后聊天记录会回显用户上传图片
- 仅上传图片不输入文本时，会自动使用默认提示词并给出提醒

默认提示词：

`请结合上传的遥感/地图影像进行专业解译，说明可能的地物、波段组合或异常现象。`

---

## 8. GeoTIFF 元信息（对话可见）

系统会向模型附带（可通过 `YYNET_ATTACH_GEO_META` 控制）：

- `bands`
- `size`
- `dtype`
- `crs`
- `resolution`
- `nodata`
- `bounds`
- `compression`
- `tiled`
- `block_size`（有则显示）
- `finite_pixel_ratio`

---

## 9. 常见问题（重点）

### 9.1 上传 TIFF 报 `Connection error` 或 400

常见原因：

- 代理污染（尤其是 `127.0.0.1:9`）
- 模型端不接受该 TIFF 编码
- 大 TIFF base64 后过大

建议：

1. 清空代理环境变量并重启终端。
2. 使用 `YYNET_TIFF_MODE=auto` 或 `png`。
3. 检查 `.env` 中 `DASHSCOPE_API_KEY` 与模型名。

### 9.2 为什么有些 TIFF 会被说“全黑/无效像素”

这通常是数据本身有效像素极少或全为 `NaN/Inf`。  
可看 `finite_pixel_ratio`：

- 接近 `0`：数据几乎不可解译
- 较大：可正常分析

### 9.3 VS Code 一直提示“同步更改”

这通常表示本地 `ahead` 远端。先看：

```powershell
git -C YYnet status --short --branch
```

如果显示 `ahead N`，直接推送即可：

```powershell
git -C YYnet push -u origin main
```

### 9.4 `push` 报 `Recv failure: Connection was reset`

优先排查代理：

```powershell
$env:HTTP_PROXY=""
$env:HTTPS_PROXY=""
$env:ALL_PROXY=""
git -c http.proxy= -c https.proxy= -C YYnet push -u origin main
```

---

## 10. 开发建议

- 将路径硬编码进一步收敛到 `.env` 或配置文件
- 为 `pre_engine/post_engine` 增加最小测试集
- 为 `assets_registry.json` 增加 schema 校验

---

## 11. 安全说明

- `.env` 含密钥，禁止提交到公共仓库
- 建议 `.gitignore` 包含：

```gitignore
.env
_chat_upload_tmp/
streamlit.out.log
streamlit.err.log
```

