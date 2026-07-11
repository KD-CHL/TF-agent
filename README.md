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
