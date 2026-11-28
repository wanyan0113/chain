# 活动流程规划接入法器链网设计

本次接通 `task_flow → design`：输入活动库、结构化初始状态与目标、显式活动映射和法器场景；规划器搜索活动顺序，独立验证器重放，桥接器生成环节配置，原设计引擎进行真实HermiT推理、法器筛选、连接核验、资源约束组网和RDF导出。输入不需要预先列出完整流程模板。

此入口选择**首个能够落实资源的流程组合**。原网络优化器证明的是该流程组合内部的法器网络最优，不是所有未探索活动顺序中的全局最优；输出明确保留 `global_flow_optimality=not_proven`。

## 运行

在项目根目录运行，输出目录必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.design_bridge --jobs task_flow/examples/design_bridge/jobs.json --scene task_flow/examples/design_bridge/scene.json --mapping task_flow/examples/design_bridge/mapping.json --output-dir generated/task-flow/design-bridge-my-run

# 七组实际CLI验收，每次生成独立目录
.\.venv\Scripts\python.exe -B -m workflow_tools.verify_task_flow_design_bridge

# 接口、规划、反例与真实OWL集成测试
.\.venv\Scripts\python.exe -B -m unittest tests.test_task_flow_design_bridge -v
```

旧 `python -m task_flow plan`、通用文档服务 `task_flow.integrate`、原 `python -m design run` 和磁盘顺序模板入口保持各自用途。新入口是 `task_flow.design_bridge`，不是将文档服务可用性检查冒充法器资源调度。

## 不同于原先模板案例的地方

原先迷雾裂隙案例在文件中预写三、四、五环节；本次场景没有 `sequences_file` 或 `sequences_inline`。只有有限活动定义及条件效果：

| 活动 | 前置条件 | 效果 | 角色 |
| --- | --- | --- | --- |
| observe | 未观测 | observed=true | 探知 |
| analyze_primary | 已观测、需要解析、未解析 | analyzed=true | 协调 |
| analyze_secondary | 已观测、需要解析、未解析 | analyzed=true | 协调 |
| decide | 已观测、未决策，且不需解析或已解析 | decided=true | 协调 |
| stabilize | 已决策、未稳定 | stable=true | 具现 |

所有活动至多执行一次。目标为 `stable=true`。T1的静态事实 `needs_analysis=false`；T2和T3为true。于是相同活动库导出不同长度的流程：

```text
T1：observe → decide → stabilize
T2：observe → analyze_primary/secondary → decide → stabilize
T3：observe → analyze_primary/secondary → decide → stabilize
```

每项任务的活动库仅包含该任务的一个目标对象，避免把其他目标上的活动混入同一链。源请求可以单独交给 `task_flow plan --verify` 检查。桥接运行将有效请求的模式明确设为 `first_feasible`，以保留前沿、逐个获得新流程；原输入对象不被修改，报告保存实际使用的请求。

这是条件效果模型中的自动顺序搜索，不是自然语言理解或自动创造活动。活动定义是否完整符合业务，仍须进行领域核对。

## 实际资源失败与继续搜索

场景沿用原实验的目标距离5、15、25和四件稳定法器，但将T3改为共振裂隙，三个任务的环节数为3、4、4，不插入复核。补充两类解析资源：

- 主解析法器R-Analysis需要PrimaryChannel能力，本例设为不可用。
- 备用R-Analysis-Backup需要BackupChannel能力，可用，耗时3秒、成本2。它的协调accuracy值为0.2，用于显式验证该值不进入探知精度×锁定率计算。
- 两类都具有ResonanceAnalysis能力；主、备用活动分别要求通道能力和解析能力。通道能力来自额外本体及规则，不靠法器名称匹配。

实际执行次序：

| 尝试 | T2解析活动 | T3解析活动 | 实际设计结果 |
| --- | --- | --- | --- |
| 1 | primary | primary | no_feasible_design |
| 2 | primary | secondary | no_feasible_design |
| 3 | secondary | primary | no_feasible_design |
| 4 | secondary | secondary | complete |

每次尝试都对整组流程进行真实资源组网。一次组网失败只影响该确切组合，不将某活动或某任务流程一概标成不可用，也不清空规划器重新搜索。当前实现不做跨运行拒绝缓存。

第四组选择的网络：

| 任务 | 选中法器顺序 | 耗时/秒 | 精度 | 成本 |
| --- | --- | ---: | ---: | ---: |
| T1 | Survey → Dispatch → Seal-B | 7 | 0.9405 | 8 |
| T2 | Survey → Analysis-Backup → Dispatch → Seal-D | 15 | 0.9603 | 9 |
| T3 | Survey → Analysis-Backup → Dispatch → Seal-C | 12 | 0.9801 | 7 |

上述编号均省略R-前缀。三个任务在相对时间0秒开始；原有表10检查开启。固定流程组合中有24组法器候选网络、8组可行，选中网络分数为87/140，跨度15秒，成本24。HermiT通过，选中RDF静态检查ERROR=0、WARN=0。

这里没有等待时间插入或自动调度；所有资源占用依照环节串行耗时和明确开始时间计算。

## 输入与映射契约

[输入目录](../../task_flow/examples/design_bridge/)包含：

- `jobs.json`：恰好覆盖场景中每一项任务，声明各自的catalog和request文件；相对路径以jobs文件所在目录为基准。
- `catalog-1/2/3.json`：有限对象、静态/可变事实、活动条件效果、能力URI。
- `request-1/2/3.json`：初始状态、目标、约束、目录哈希及搜索预算。
- `scene.json`：现有design场景契约，声明法器、上下文、指标、连接、资源约束、本体文件及规则，**不得预带顺序模板**。
- `mapping.json`：活动身份、版本到design活动和角色的显式映射，不包含完整活动顺序。

映射顶层须且仅须包含：

```text
format_version = task-flow-design-1.0
mapping_id, version, entries
communication_model = directed 或 undirected
allow_nonadjacent_reuse = Boolean
evaluation = {mode, source_refs}
source_refs = 非空且不重复的来源数组
```

每个映射项须且仅须包含：

```json
{
  "catalog_id": "rift-bridge",
  "activity_id": "analyze_secondary",
  "activity_version": "1.0",
  "target_parameter": "rift",
  "activity": "decide",
  "role": "coordination",
  "min_level": 0.8,
  "incoming_relation": "communication",
  "source_refs": ["synthetic-task-flow-design-bridge-v1"]
}
```

映射不修改活动能力URI。桥接器从经过重放的活动定义读取全部 `required_capabilities`，不相信候选自行声明的能力需求。所有URI须在当前本体中声明、符合映射角色；跨命名体系的URI转换尚不支持，不能根据相似名称自动替代。

本版每个活动实例只允许一个明确目标参数，其绑定值必须等于对应design任务的target。额外参数、错误目标、缺活动映射或版本不匹配会明确返回不支持。每个生成环节有唯一ID，即使活动重复也不会覆盖。

自动生成的模板使用任务专属条件，不能误用于其他任务。`bridge_task_id`是保留属性，原场景出现同名字段时拒绝，不覆盖用户数据。

## 内联顺序配置与证据

为避免桥接器改写全局knowledge文件，design新增可选 `knowledge.sequences_inline`，与 `sequences_file`互斥。它通过现有完整顺序Schema和本体能力校验；不是一个跳过校验的内部开关。

内联配置以 `inline:sequences` 记录在知识快照中，包含精确内容及SHA256；输入或生成顺序变化会改变执行身份。所有外置本体、规则、事实映射在一次桥接运行中冻结核对，变化时停止，不能将前一个知识上下文中的失败用于后一个上下文。

输出包括：

```text
bridge_report.json
attempt-001/
  design_input.json
  design_report.json
  design_summary.md
  knowledge_snapshot.json
  reasoning_input.ttl
  reasoning_result.ttl
...
attempt-004/
  同上
  design.ttl
```

只有选中且通过严格RDF核验的尝试导出design.ttl。每份design_input可以独立使用原 `design run` 重跑，不依赖桥接运行时全局状态或临时知识文件。

bridge_report保存源场景、映射、实际规划请求、输入哈希、逐任务搜索轨迹、候选身份、独立重放证据、生成环节及每次实际组网报告。`selected_attempt`为从0开始的索引，对应目录编号从1开始。

## 评价口径

新模式 `insight_lock_product` 明确限定为“一次探知→一个或多个协调→一次具现”：

\[
T=\sum_i T_i,\qquad A=A_o\times L_a,\qquad C=\sum_i C_i
\]

探知环节的 `activity_metrics.observe.accuracy`解释为探知精度；具现环节的 `activity_metrics.act.accuracy`解释为锁定率。协调环节仍提供完整活动指标以遵循原契约，但其accuracy不参与综合精度。原始数值不会被改写为1，也不会补造缺失指标。

这是用户确认的表13口径在串行多协调环节下的明确扩展，不是“所有环节达标概率”。时间和成本包含每次实际执行。协调环节的能力、连接、资源、耗时和成本仍参与核验与评价。

该模式拒绝多次探知、多次具现和assess，避免隐式决定融合办法。原 `sequential_product` 所有环节精度连乘模式继续保留；原模板和已有结果不自动改用新模式。新模式之后仍沿用原极差归一化及加权评分。

## 预算与结论

CLI提供 `--max-checks`（默认100，最多1000）和 `--max-flows-per-task`（默认100，最多10000）。各任务自己的节点、时间、长度预算保留，恢复不清零；检查预算统计完整候选流程组合，不是HermiT调用次数。设计内部另保留原有成链和网络搜索预算。

| integration_status | 含义 |
| --- | --- |
| selected | 找到至少一组逻辑验证及真实资源组网均通过的流程 |
| logical_no_solution | 至少一任务在给定边界内没有逻辑候选，且没有相关未知/超限结论 |
| resource_no_solution_in_bounds | 逻辑候选穷尽、无未知，所有确切组合都被实际design判为不可行 |
| blocked | 存在未知、缺失/不兼容映射、验证失败或设计错误，不能证明无解 |
| search_limit | task_flow节点、时间或候选数量超限 |
| design_limit | design成链或网络搜索超限，不能当作资源无解 |
| analysis_limit | 候选组合尝试次数达到预算 |
| invalid_input | 输入契约、输出路径或运行知识一致性错误 |

CLI成功选中退出0，未选中退出1，输入/文件错误退出2。输出目录拒绝复用。规划耗时不包含外部HermiT和设计分析耗时，不作为端到端效率结论。

## 验收范围

七组实际CLI验收覆盖：主法器不可用后恢复备用流程、主法器可用时首组成功、稳定法器全部无库存、探知距离未知、缺活动映射、逻辑长度边界不足、外部尝试预算超限。成功必须实际运行HermiT并导出严格检查通过的RDF；失败不得留存选中设计RDF。

本次实际七组全部通过，证据位于 `generated/task-flow/design-bridge-verified-20261128-114308-1fcf578a/verification.json`；相关回归共98项通过，其中新增桥接测试16项，日志位于 `tmp/task-flow-design-regression.log`。回归包括旧顺序模板、表10、多文件知识推理、通用服务接口、网络搜索和Excel设计入口。测试数量只表示软件检查范围，不表示研究效果或真实执行成功。

接口测试还覆盖：状态变化自动改变环节数、篡改终态被独立重放拒绝、目标参数不得偷换、版本缺映射、重复映射、错误角色、错误阈值、禁止输入模板、任务覆盖、保留字段冲突、确切组合反馈、规划/设计/候选超限、未知与无解区分、短流程不补假步骤、内联知识快照及模式边界。

本次接通的是**有限确定性活动、显式能力映射、单目标活动、每环节一个法器、固定任务开始时间**下的受限真实design后端。自然语言/API输入、自动创造活动、同环节多法器联合、任意对象/多参数活动、并行活动规划、任意短流程、自动排程、动态执行和FDNA适配不在本次验收范围。无论规划或资源核验通过，`execution_confirmed=false`。
