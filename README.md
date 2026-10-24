# RDF 与 OWL 本体静态检查

现有检查器支持本地 Turtle 文件解析、模式声明检查、实例属性检查和 JSON 报告导出。本轮完善的是通用 RDF/OWL 工具部分。

## 安装与运行

建议使用 Python 3.10 及以上版本，在 PowerShell 中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe check_ontology.py examples/library.ttl --generic --json-output output/library-report.json
```

`--generic` 仅运行通用检查，适用于任意领域的 Turtle 本体。省略该选项会保留原有的论文覆盖和项目结构检查。`--fail-on-warn` 可将警告也作为检查失败处理。

## 检查范围

- 本地 Turtle 语法、对象属性与数据属性声明、标签和本体元数据。
- 定义域和值域的显式类型匹配，包含具名子类继承。
- 多个定义域或值域按交集处理：需要同时满足，不能只匹配其中一个。
- 整数数据类型的派生关系，例如 `xsd:int` 可匹配 `xsd:integer` 和 `xsd:decimal`，反方向不成立。
- 非法已知数据类型字面量；语言标签字符串识别为 `rdf:langString`。
- 原有的限制、互逆、函数属性和互斥检查。

JSON 报告包含 `schema_version`、输入绝对路径、`exit_code`、按级别统计的 `counts`、问题列表 `issues` 和检查范围说明。输入读取或解析失败时也会导出报告；报告路径不能覆盖输入文件。

退出码：`0` 为静态检查通过，`1` 为存在错误或启用严格选项后的警告，`2` 为输入读取、输出写入或命令参数错误。

## 语义边界

该程序不是完整 OWL 2 DL 推理器。缺少显式类型通常记为警告；检查通过不证明本体逻辑一致。程序不会自动下载或加载 `owl:imports`，匿名类表达式及复杂数据类型的检查也有限。需要完整一致性判定时，请在 Protégé 中运行 HermiT 等推理器。

示例是图书馆领域的小型本体，可独立用于验证通用检查流程。

## 实例校验与两级映射

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe validate_data.py maintenance_demo/maintenance_ontology.ttl --profile examples/maintenance_data_profile.json
.\.venv\Scripts\python.exe -m maintenance_demo.mapping.run_mapping
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

两级映射示例由维修任务条件推导能力，再匹配满足全部能力、明确可用且响应不超限的单个资源。预期得到 4 条能力需求和 3 条候选资源关系；结果写入 `maintenance_demo/mapping/generated/`。这是候选匹配，不进行资源分配、排程或完整 OWL 推理。

详见[数据校验使用说明](docs/数据校验使用说明.md)、[2.1.1 映射规则验证](docs/第二部分2.1.1映射规则验证.md)及[本体对照与实施顺序](docs/本体对照与实施顺序.md)。

## 流程协同验证

```powershell
.\.venv\Scripts\python.exe -m maintenance_demo.collaboration.run_collaboration
```

该入口复用两级映射，验证维修资源交接、任务顺序、流程依赖、固定窗口共享容量和耗材库存，生成 3 个流程及串联/并行/分支关系。它验证给定请求的可行性，选择首个可行资源组合，不作最优排程声明。详见 [2.1.2 与 2.1.3 阶段说明](docs/第二部分2.1.2与2.1.3流程协同验证.md)。

## 维修流程统计

```powershell
.\.venv\Scripts\python.exe -m maintenance_demo.collaboration.summarize_workflows
```

先重新验证维修请求，再统计网络历时、任务工时、资源容量利用率、峰值并发和耗材计划余量。示例网络历时 6 分钟，任务工时合计 8 分钟；成本和质量缺少数据，报告会明确说明。结果写入 `maintenance_demo/collaboration/generated/maintenance_statistics.json`。详见[维修流程统计与指标口径](docs/维修流程统计与指标口径.md)。
