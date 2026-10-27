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
| 六步自动设计 | `design/`，入口 `python -m design`，配置及说明见 [设计模块](design/README.md) |

案例对象名称和字段名采用当前项目命名，不再是原文名称逐字快照；数字、单位、对象数量、关系及疑点保持。名称对应由项目外单独维护的《本体命名对照表》记录。来源包仍完整保留当前输入及摘要，但旧名称包须重新生成。

```powershell
.\.venv\Scripts\python.exe check_ontology.py arcane_ontology.ttl --fail-on-warn
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

来源、环境、适配的详细命令分别见`source_data/README.md`、`environment/README.md`、`integration/README.md`、`adapters/README.md`。`generated/`是按当前输入重建的输出，不是历史结果。

当前支持本体声明、来源管理、环境读取、显式关联与校验。新增 `design` 模块实现显式规则驱动的任务—能力—法器筛选、闭合链生成、三指标评分，以及带资源约束的多任务组网和固定时间计划检查。规则阈值、指标和协同事实由输入提供；完整枚举范围内按声明目标函数选择最优可行网络。尚未实现动态场景仿真、环境影响计算或自动修复来源疑点。Git目录与版本历史不作清理。

技术演示（虚构数据，与文章案例分开）：

```powershell
.\.venv\Scripts\python.exe -B -m design run design/examples/technical_demo.json --output-dir generated/design_demo
.\.venv\Scripts\python.exe -B -m design audit-case
```

第一条生成候选链、评分、资源冲突报告、选中网络的 `design.ttl` 和中文摘要；第二条列出当前文章案例的13项疑点、3项缺口及设计所需补充数据，退出1表示被阻塞。用户已明确更正“目标近→近程能力”，更正依据记录在主本体注释和设计模块说明中；具体近程阈值不自动补造。

新增文章单条链复现：`python -m design reproduce-case --output-dir generated/article_reference_chain`。按新增第三部分截图的表13复算4组评分，输出正文明确标识的最优链及RDF；状态为 `reference_reproduced`，尚未独立验证完整筛选和协同可行性。详见设计模块说明。

新增独立Excel入口：在项目根目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\启动Excel演示.ps1`，再打开 `http://127.0.0.1:8765`。提供中文案例示例、空白模板、录入检查以及本次实际导入和查询结果。入口调用 `generated/week2-bbd1f9f` 的历史核心程序，每次核对提交与核心文件；Excel入口是后续操作改进，不属于原历史提交。详见 [Excel使用与展示步骤](excel_entry/使用说明.md)。
