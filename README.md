# 奥术网模型与数据工具

当前主本体为`arcane_ontology.ttl`，业务命名空间为`http://example.org/arcaneweb#`。主要入口：

| 模块 | 入口／配置 |
|---|---|
| 本体检查 | `check_ontology.py` |
| 实例校验 | `validate_data.py` |
| 环境 | `environment/loader.py`、`environment/ontology.ttl`、`environment/profile.json` |
| 背景联合加载 | `integration/load_project.py`、`integration/environment_mapping.ttl` |
| 来源整理 | `source_data/cli.py` |
| 案例输入 | `data/article_source/case_records.json` |
| 案例读档配置 | `source_mappings/case_fields.json` |
| 案例核对记录 | `data/article_source/case_review.json` |
| 适配与验收 | `adapters/`、`adapter_mappings/` |

案例输入现已采用温和西幻对象名称和字段名，不再是原文名称逐字快照；数字、单位、对象数量、关系及疑点保持。名称对应由项目外单独维护的《本体命名对照表》记录。来源包仍完整保留当前输入及摘要，但旧名称包须重新生成。

```powershell
.\.venv\Scripts\python.exe check_ontology.py arcane_ontology.ttl --fail-on-warn
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

来源、环境、适配的详细命令分别见`source_data/README.md`、`environment/README.md`、`integration/README.md`、`adapters/README.md`。`generated/`是按当前输入重建的输出，不是历史结果。

当前支持本体声明、来源管理、环境读取、显式关联与校验；尚未实现自动成链、评分、成网及仿真。Git目录与版本历史不作清理。
