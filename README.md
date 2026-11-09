# 奥术网设计工具

本地运行：外置OWL知识 → HermiT分类与具名关系推理 → JSON业务规则 → 成链、评分及资源约束下组网。无需AI API。

## 启动与验证

依赖见 `requirements.txt`、`requirements-reasoner.txt`；本体推理需要PATH中的Java。

```powershell
# 启动Excel网页入口
powershell -NoProfile -ExecutionPolicy Bypass -File .\启动Excel演示.ps1

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
| `tests/` | 自动化验证 |

输出目录中 `design_report.json` 为完整结果，`design_summary.md` 为摘要。`reasoning_input.ttl` 含完整知识与场景，可用Protégé打开并启动HermiT；场景个体有“任务：…”等标签。`reasoning_result.ttl` 仅含回流的具名结果，不能代替完整本体。设计完成且严格检查通过时生成 `design.ttl`。

示例用于验证程序机制，不代表文章全部真实实验复现。最优性限定于声明的输入、约束与评分政策；同分时选中结果不表示唯一最优。

## 场景属性参与本体推理

在输入的 `knowledge.fact_mappings_file`（Excel设置“本体事实映射文件”）指定knowledge目录内的JSON文件，例如：

```json
{"mappings":[{"id":"height","kind":"target","field":"operating_height","property":"http://example.org/arcaneweb#targetAltitudeFt","datatype":"decimal","unit":"ft","source_refs":["字段定义来源"]}]}
```

对应任务的 `attributes.operating_height` 填 `{"value":1000,"unit":"ft"}`。`kind` 可为task、target或artifact；target从所属任务的attributes读取。属性须在本体声明为数据属性；支持string、boolean、decimal、integer、double，数值必须声明单位，不自动换算。映射默认 `required:true`，缺失、类型或单位不符会记录unknown并阻塞相关任务或法器；明确可选字段才设置false。

修改场景事实、OWL分类公理、字段映射或JSON规则可改变结果，无需修改Python。程序仍固定三种角色、闭合链结构、评分公式和规则运算符；新增这些机制仍需改程序。具名推理结果回流范围为类型及requiresCapability、hasCapability、communicatesWith、orchestrates四种关系，尚非任意OWL结论的通用消费器。

`generated/` 与 `tmp/` 中的运行输出可重新生成；清理它们不影响源代码和知识文件。再次运行仍会按需生成报告。

## 网络搜索

原有时间、精度、成本公式与网络平均评分保持不变。`policies.network_search_method` 可选 `auto`（默认）、`exhaustive` 或 `branch_and_bound`。auto在组合数超过 `max_network_combinations` 时改用分支定界；穷举模式仍以该参数为硬上限。分支定界提前排除容量、库存、冷却、资源组及任务依赖冲突，用剩余任务最高分作上界，并保留同分按候选链编号选择的顺序。

`max_network_search_nodes` 默认1000000，限制分支定界访问节点数；触限抛出搜索上限错误，不返回已证明最优的设计。`network_search.optimality` 表示证明状态；剪枝模式的evaluated、feasible和rejections仅统计实际访问的完整候选或剪枝冲突，不代表全部可行方案数。表10模式为保留所有未知条件的审计，仍使用穷举。Excel可选设置为“网络搜索方式”和“搜索节点上限”。

候选链新增 `plan.members`、`plan.connections` 和 `plan.resource_usage`，把成员集合、连接与容量/库存占用交给搜索模块；保留原artifacts字段兼容现有导出。当前生成与评价仍是一探知、一协调、一具现，多成员只预留内部结构及资源核验接口，尚未开放多单元生成、融合公式或导出。
