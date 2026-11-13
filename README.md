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

修改场景事实、OWL分类公理、字段映射或JSON规则可改变结果，无需修改Python。程序仍固定三种角色、评价机制和规则运算符；新增这些机制仍需改程序。具名推理结果回流范围为类型及requiresCapability、hasCapability、communicatesWith、orchestrates四种关系，尚非任意OWL结论的通用消费器。

`generated/` 与 `tmp/` 中的运行输出可重新生成；清理它们不影响源代码和知识文件。再次运行仍会按需生成报告。

## 网络搜索

原有时间、精度、成本公式与网络平均评分保持不变。`policies.network_search_method` 可选 `auto`（默认）、`exhaustive` 或 `branch_and_bound`。auto在组合数超过 `max_network_combinations` 时改用分支定界；穷举模式仍以该参数为硬上限。分支定界提前排除容量、库存、冷却、资源组及任务依赖冲突，用剩余任务最高分作上界，并保留同分按候选链编号选择的顺序。

`max_network_search_nodes` 默认1000000，限制分支定界访问节点数；触限抛出搜索上限错误，不返回已证明最优的设计。`network_search.optimality` 表示证明状态；剪枝模式的evaluated、feasible和rejections仅统计实际访问的完整候选或剪枝冲突，不代表全部可行方案数。Excel可选设置为“网络搜索方式”和“搜索节点上限”。

启用 `policies.article_table10:true` 时，显式选择 `branch_and_bound`，或auto超过网络组合上限，会在逐任务加入候选链时使用部分表10链协同条件剪枝：具现法器在整个计划中重复使用；同时共享探知法器时覆盖距离不满足严格大于；同时共享探知或协调法器时，最大标记数或最大解析目标数不严格大于目标数量总和。数量剪枝仅在全部任务目标数量已知时启用，以保留现有完整检查对未知总量的判断。仍使用固定整链 `[start,end)` 区间判定同时执行，不把间接重叠当成同时执行；不增加地理换算或自动调度假设，不用于可变环节顺序链。

只有添加后续链无法消除的确定失败才剪枝；未知前缀继续扩展，完整组合仍执行原表10检查。表10模式关闭评分上界剪枝（`score_bound_enabled:false`），避免跳过低分但可行性未知的组合；只要存在未被工程约束排除的未知完整组合，仍阻塞设计并报告 `optimality:not_proven`。规则界定可行性，原有评分与同分排序决定已知可行组合的优劣。`exhaustive` 保留为不剪枝的对照路径。

`network_search.pruned_table10` 记录表10剪掉的非完整前缀数，`table10_pruning` 按规则记录命中次数及原文来源；`article_table10.combinations` 只列实际访问的完整组合，不包含被剪子树。剪枝正确性的回归验证见 `tests/test_table10_pruning.py`：与笛卡尔积穷举逐项比较全部可行及未知组合、最优值和同分选择，覆盖严格边界、重叠区间、未知数量及搜索触限。这些是合成数据的软件验证，不是原文全部案例或仿真实验复现，也不构成对任意输入的经验性完整证明。

候选链使用 `plan.members`、`plan.connections` 和 `plan.resource_usage`，把成员集合、连接与容量/库存占用交给搜索模块。不启用任务序列时，仍使用原来的一探知、一协调、一具现模式及其公式。

## 可配置顺序任务序列

在 `knowledge.sequences_file` 指定knowledge目录内的JSON（契约见 `design/sequence_schema.json`）；Excel对应设置为“任务序列文件”。外置模板规定适用条件 `when`、环节 `steps`、`allow_nonadjacent_reuse`，顶层明确 `communication_model` 为directed或undirected，以及 `evaluation.mode`。模板、环节、评价和配置均须登记 `source_refs`。

每个环节填写id、activity、role、capabilities（具名能力完整URI）、min_level和source_refs；可选ontology_types引入OWL环节分类。非首环节须声明incoming_relation为communication或control，control仅适用于协调→具现。保留探知—协调—具现主线：observe起始，含decide，以act结束；至少三个环节，不再限定4至6个环节，也不自动增加评估。既可配置 `observe→decide→act`，也可配置 `observe→decide→decide→act`（探知→协调1→协调2→具现）。重复的decide均为协调，不要求拆为研判和决断。每环节只分配一个对象，相邻对象必须不同并可通联；非相邻复用由模板明确允许。未设置固定环节数上限，但仍受下述搜索工作量与候选数上限约束。

observe对应insight（探知）、decide对应coordination（协调）、act对应manifestation（具现）。为兼容既有配置保留可选assess（insight角色），只有模板显式填写才会参与执行；不使用评估时无需提供评估能力或指标。资源 `attributes.level_<activity>` 使用 `{"value":0.9,"unit":"1"}`，与环节min_level比较。`SequentialStepMission` 的类型和requiresCapability由HermiT实际推导，随后业务规则核验数值条件。stepTarget与整体任务hasTarget分开，避免把整体多角色能力需求强加给每个环节。

两种评价方式须显式选择：

- `feasibility_only`：只生成逐目标可行轨迹，成功状态为 `feasible_trajectories`；不评分、不选择同时可执行的网络、不导出选中设计RDF。可查看报告及reasoning_input/result。
- `sequential_product`：显式采用工程假设——环节耗时相加、精度相乘、每次执行成本相加，再接回原归一化、权重及网络搜索。各资源必须单独提供 `activity_metrics.<activity>`（duration_s、accuracy、cost），不沿用旧metrics。重复执行按次计成本和库存，按环节时间及冷却计资源占用。Excel可添加“环节指标”表，列为法器编号、活动、耗时秒、精度0至1、使用成本、成本单位。

报告中 `plan.steps` 给出顺序、执行对象、能力与时间；`sequence_generation` 记录搜索和剪枝。先筛选环节候选域，再深度优先回溯并即时检查连接，借鉴CPBS-MP；`max_sequence_search_nodes` 默认1000000，生成候选数仍受max_chain_combinations约束，触限报错，不返回截断集合。

顺序链生成借鉴新文章的逐环节搜索方法，主体保留原文探知—协调—具现结构及原有归一化和加权评分；不是新文章的数值实验复现，也不强制采用其流程。多环节指标采用上面显式声明的顺序汇总口径。未实现同环节多个对象的并行融合、一个物理对象承担多种角色、论文转化率或DEA配置评价。网络开始时间仍须输入，程序不自动调度。
