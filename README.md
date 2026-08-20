# TF-agent

潮滩遥感解译与分析平台（Streamlit + 深度学习推理 + 遥感后处理 + LLM Copilot）。

## 仓库结构

```text
TF-agent/                 # 主应用（Streamlit、推理引擎、智能体）
research/jb/              # 研究原型脚本（M1–M5、E1 CLI 版）
tests/                    # E2E / UX 压力 / 单元测试
docs/                     # 文档与 QA 报告
cstf_ux.py                # 共享地学防御工具库
config/                   # 配置样例（ngrok 等）
```

## 快速开始

```powershell
conda activate gwx
cd TF-agent
pip install -r requirements.txt
copy .env.example .env    # 填入 DASHSCOPE_API_KEY
streamlit run app.py --server.port 8501
```

### 同门 / 新机器拉取运行

1. 克隆仓库：`git clone https://github.com/gwxislander/TF-agent.git && cd TF-agent`
2. 创建 Python 3.10 环境并安装依赖：`pip install -r TF-agent/requirements.txt`
3. 配置密钥：`copy TF-agent\.env.example TF-agent\.env`，填入 `DASHSCOPE_API_KEY`
4. 准备模型权重 `best_train_loss_model_resnet50.pth`（见下方说明），在侧栏「提取模型权重」中选择
5. 启动：`python -m streamlit run TF-agent/app.py --server.port 8501`

> 侧栏中的原始影像目录 / 输出目录 / 矢量约束等默认值为空，首次使用时按本机路径选择即可；
> 代码不会强制使用开发机的本地路径（`I:\GEE_data\20` 等仅为开发机示例，新机器自动留空）。

### 模型权重获取（必读）

`best_train_loss_model_resnet50.pth`（CDNet/ResNet50 潮滩分割权重）为推理必需文件，
因体积原因**不放入 Git 仓库**（`.gitignore` 已排除 `*.pth`）。获取方式：

- 方式一：找师兄/师姐拷贝该文件（约 200–400 MB），放到任意目录后在侧栏选择；
- 方式二：如仓库维护者已上传 GitHub Release，在 [Releases](https://github.com/gwxislander/TF-agent/releases) 下载；
- 方式三：自行训练（`TF-agent/train_agent.py` 提供了训练入口）。

> 其它外部数据（AOI 矢量、潮滩数据集、M5 基线 SHP 等）同样按需准备，在侧栏对应输入框选择。


## 运行测试

```powershell
conda activate gwx
cd e:\Code\GEE   # 仓库根目录

python tests/fixtures/generate_sandbox_data.py
python tests/stress/run_ux_stress.py
python tests/e2e/run_diagnostics.py
```

期望：**5/5 UX + 13/13 E2E 全部通过**。

## 模块映射

| 编号 | 功能 | 研究脚本 (`research/jb/`) | 产品引擎 (`TF-agent/`) |
|------|------|---------------------------|------------------------|
| M4 | GEE 影像下载 | `M4.py` | `m4_engine.py` |
| M5 | 时空异常告警 | `M5.py` | `m5_engine.py` |
| E1 | 多源一致性诊断 | `E1.py` | `e1_engine.py` |

详细使用说明见 [TF-agent/README.md](TF-agent/README.md)。

## 远程仓库

```text
https://github.com/gwxislander/TF-agent.git
```

## 文档

- [远程演示配置](TF-agent/REMOTE_DEMO.md)
- [QA 工作报告](docs/reports/导师汇报_潮滩系统测试与质量保障工作汇报.md)
