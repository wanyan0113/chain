# 奥术网设计工具

本地运行：外置OWL知识 → HermiT分类与具名关系推理 → JSON业务规则 → 成链、评分及资源约束下组网。无需AI API。

## 启动与验证

依赖见 `requirements.txt`、`requirements-reasoner.txt`；本体推理需要PATH中的Java。

```powershell
# 启动Excel网页入口
powershell -NoProfile -ExecutionPolicy Bypass -File .\启动Excel演示.ps1

# 生成文章原文保留版、补充假设版及变更实验；每次使用新的输出目录
.\.venv\Scripts\python.exe -B -m experiments.article_pair --output-dir generated/article_pair_check

# 独立运行某个场景
.\.venv\Scripts\python.exe -B -m design run design/examples/knowledge_driven_demo.json --output-dir generated/knowledge_check --require-canonical

# 回归测试
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

网页地址：`http://127.0.0.1:8765`。模板位于 `excel_entry/templates/`。

## 保留的核心文件

| 路径 | 用途 |
| --- | --- |
| `arcane_ontology.ttl` | 主本体 |
| `knowledge/*.ttl`、`knowledge/*.json` | 外置本体及业务规则 |
| `data/`、`source_mappings/`、`adapter_mappings/` | 原始数据、来源记录及映射 |
| `design/`、`adapters/` | 规则、OWL推理、设计及导出 |
| `experiments/article_pair/` | 两套案例生成与复验代码，假设保存在assumptions.json |
| `tests/` | 自动化验证 |

输出目录中 `design_report.json` 为完整结果，`design_summary.md` 为摘要。`reasoning_input.ttl` 含完整知识与场景，可用Protégé打开并启动HermiT；场景个体有“任务：…”等标签。`reasoning_result.ttl` 仅含回流的具名结果，不能代替完整本体。设计完成且严格检查通过时生成 `design.ttl`。

原文保留版预期blocked；补充假设版验证单任务完整流程，不代表文章全部真实实验复现。最优性限定于声明的输入、约束与评分政策；同分时选中结果不表示唯一最优。

`generated/` 与 `tmp/` 中的运行输出可重新生成；清理它们不影响源代码和知识文件。再次运行仍会按需生成报告。
