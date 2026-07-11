# research/jb — 研究原型脚本

本目录为潮滩解译流水线（M1–M5、E1）的 CLI 原型实现。  
**Streamlit 主应用请使用上级 `TF-agent/` 中的 `*_engine.py` 封装层。**

| 脚本 | 功能 | 产品封装 |
|------|------|----------|
| `E1.py` | 多源像元级一致性诊断 | `TF-agent/e1_engine.py` |
| `M5.py` | 时空异常三维度告警 | `TF-agent/m5_engine.py` |
| `M4.py` | GEE Sentinel-2 导出 | `TF-agent/m4_engine.py` |
| `M1_1.1.py` / `M1_1.2.py` / `M1.1.py` | 指数法提取与融合 | `TF-agent/index_engine.py` |
| `combine.py` | TIF vs SHP 精度评价 | `TF-agent/evaluation_geo.py` |

共享工具库 `cstf_ux.py` 位于仓库根目录。
