# 跨流程联合优化实现与验收

后续增量：[阶段一实现审计与可复现性](stage1-audit/README.md)，含发布版本复现、数量相等边界修复及独立证据。下文保留初次实现记录。

实现日期：2026-11-28。设计依据：TFDO-DESIGN-1.0。原[设计](design-v1.md)、[任务拆分](development-v1.md)、[验收矩阵](acceptance-v1.md)和manifest保持冻结时内容，不改写历史状态。本文件记录后续的软件实现与实际验收。

## 1 当前实现

新增 `joint_candidate_optimization` 模式，执行活动流程搜索、独立重放、真实OWL法器链生成、候选身份与去重、集合冻结、统一评价、跨任务资源组网及独立核验。保留默认 `first_resource_feasible` 快速模式及原模板、评价和通用文档服务接口。

| 工作包 | 实现文件 | 已完成内容 |
| --- | --- | --- |
| A 上下文 | task_flow/joint_identity.py、joint.py | 统一规范化、字段投影、输入/知识冻结、运行元数据 |
| B 流程 | joint.py、原planner/validator | 有界枚举、逐候选独立重放、语义flow_id和覆盖状态 |
| C 候选链 | design/engine.py、sequences.py | generate_candidates未评分入口；明确的部分生成状态；实际HermiT |
| D 集合 | joint_candidates.py | 结构化candidate_id、独立运行和证据身份、去重、冲突检测、冻结摘要 |
| E 评价 | joint_candidates.py | 显式sequential_product_v2、跨流程统一极值、退化0.5、政策和评价上下文 |
| F 联合优化 | joint_network.py、design/search.py | 复用实际资源约束，穷举/分支定界，超限返回未证最优的当前最好解 |
| G 核验输出 | joint_verify.py、joint.py | 独立流程/算术/连接/区间占用核验、搜索证明复核、RDF及来源记录 |
| H 实验 | tests/test_joint_*.py、tests/joint_reference.py | 独立小规模排列/组合参考、兼容与反例、同基准快速模式对照 |

“已完成”指这些受限软件机制及检查，不表示协调精度的实际业务规律已被证明。`domain_validity=pending`始终保留。

## 2 运行入口

项目根目录运行，输出目录必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.design_bridge --mode joint_candidate_optimization --jobs task_flow/examples/joint_optimization/jobs.json --scene task_flow/examples/joint_optimization/scene.json --mapping task_flow/examples/joint_optimization/mapping.json --output-dir generated/task-flow/joint-my-run

# 一键实际验收及独立参考计算，每次创建独立目录
.\.venv\Scripts\python.exe -B -m workflow_tools.verify_joint_optimization

# 原快速模式仍可运行，以下使用相同原始指标政策，便于比较
.\.venv\Scripts\python.exe -B -m task_flow.design_bridge --jobs task_flow/examples/joint_optimization/jobs.json --scene task_flow/examples/joint_optimization/scene.json --mapping task_flow/examples/joint_optimization/mapping.json --output-dir generated/task-flow/quick-my-run
```

新示例的mapping显式声明 `evaluation.mode=sequential_product_v2`。联合模式拒绝缺少此政策或选择其他精度政策，不静默采用旧表13默认值。原design顺序模式和快速桥接仍可使用其原有政策。

## 3 输入与范围

沿用原jobs/catalog/request/scene/mapping输入结构。scene不得预带完整流程模板；活动映射不提供完整活动顺序。新模式要求 `equal_metric_score=0.5`，不隐式覆盖不同的原配置。

每个任务必须具有独立对应job，活动实例必须满足现有单目标参数映射和顺序骨架限制。相邻法器不同、非相邻复用遵循明确策略。目标、能力、连接、可用性、容量、库存、冷却、资源组、任务依赖和开启的表10规则继续生效。

新增预算为 `--max-flow-analyses`（默认10000，最多10000），限制活动流程适配次数；`--max-flows-per-task`限制各任务规划候选数。原请求的节点、时间和长度限制继续生效。法器生成使用scene中的成链候选与节点预算；超过50个模板时按现有知识契约分批生成，每批保留独立输入/推理证据。网络阶段使用原网络组合或节点预算。各阶段完整性分别记录，不能互相代替。

单链级别的未知资源候选可以使覆盖不完整，但不必抹掉其他已证实候选。整体任务的能力需求若仍未知（如UNKNOWN_TASK_RULE、UNKNOWN_OWL_FACT），不能当作完整需求使用，联合评价明确阻塞。缺原始指标同样阻塞，不补1、0.5或原文数据。

## 4 身份和集合

候选实体ID由任务上下文、具备活动语义的flow_id、完整活动参数序列、按位置绑定的法器、全场景资源上下文、基础知识和映射摘要决定。flow_id包含catalog身份/版本、活动身份/版本/语义和目标参数标识；不以flow-1或chain-1生成编号作为稳定身份。

同候选重复生成合并实体、保留全部证据；每次运行run_id不同。预算、输出路径、分数、权重和发现顺序不进入候选身份。资源记录、任务和映射集合顺序采用规范处理，完整搜索下相同规范输入产生相同集合与确定性选择。

候选集合先冻结，后计算极值和分数。每个任务跨流程统一归一化，精确相等的退化指标一律0.5，不引入隐藏容差。评价上下文保存集合摘要、极值、政策和权重。加入候选需要新集合版本，旧分数不跨集合直接比较。

首版资源上下文保守覆盖全场景，改变其他资源也可能使候选ID变化；知识文件采用精确快照，不承诺识别所有OWL逻辑等价改写。

## 5 最优性与产物

| 结果 | 含义 |
| --- | --- |
| bounded_global_optimum | 声明的流程/成链边界完整，相关未知与不支持不妨碍证明，冻结集合的联合网络优化证明完成 |
| candidate_set_optimum | 冻结的已生成候选集合中最优，流程/链覆盖不完整或存在未解决分支 |
| not_proven + feasible | 网络搜索超限或未完成证明，但有独立资源核验通过的当前最好解 |
| bounded_global_infeasible | 完整声明范围内没有可行网 |
| candidate_set_infeasible | 只证明冻结集合不能组网，不外推所有未生成流程 |
| undetermined/blocked | 未知、缺指标、不支持、超限无解或其他不能证明的情况 |

选中网络必须经过独立重放、全部候选原始指标与统一评分复算、连接及资源核验。完整逻辑覆盖和网络最优性标记会从保留快照重跑复核，不能只修改passed/proven字段。优化算法本身另外与独立小规模穷举参考对照。

输出包括joint_report.json、candidate_sets.json、candidate_evidence.json、输入scene/mapping/jobs与各catalog/request、基础知识快照、各批reasoning_input/result及knowledge_snapshot。已证候选集最优导出design.ttl，网络未证最优的当前最好解只导出incumbent.ttl；design_summary.md同时显示覆盖范围、选中活动和法器、原始指标与评分。

输出输入快照可重新交给同一CLI运行；知识文件依然按项目配置加载。归档保存精确知识内容，当前知识变化时拒绝旧报告导出，不偷偷改写项目知识文件恢复旧环境。

## 6 实际结果

三任务合成场景：T1不需要解析，T2/T3需要解析。主解析法器可用但较慢、成本较高；备用解析法器更快且质量系数较高。原快速模式在主解析流程可行后停止；新模式将备用流程也纳入候选。

| 项目 | 实际结果 |
| --- | --- |
| 逻辑流程 | 5条：T1为1条、T2/T3各2条 |
| 去重后候选链 | 14条：4、6、4 |
| 独立参考完整网络组合 | 96组 |
| 独立参考可行网络 | 32组 |
| 网络最优选择 | 与独立参考一致 |
| 快速模式所选网在统一集合中的重评分 | 0.29440765925199025 |
| 联合模式评分，同一评价上下文 | 0.7998864162816491 |
| 源案例结论 | bounded_global_optimum，范围仅为声明的有限模型 |
| RDF静态检查 | ERROR=0、WARN=0 |

不能用快速模式自己单独归一化的旧分数与联合分数直接比较。上述快速得分是先定位其实际活动/法器选择，再放到联合模式的同一冻结集合中重算所得。这是一个特定合成案例的改进证据，不是普遍的严格提升或效率提升证明。

实际验收覆盖完整联合、快速模式同基准对照、部分流程、部分链生成、超限当前最好解、库存为零、缺指标及重复运行身份复现。额外软件测试覆盖55条流程分为两批、165条候选不丢失，明确任务需求未知时阻塞，以及规范记录顺序变化下的完整集合复现。

详细证据：[本次实际验收摘要](evidence/joint-verification.json)。测试记录与逐设计条目追踪见[验收记录](acceptance-results-v1.json)。必要摘要和日志已归档到本目录，GitHub检出不依赖本地被忽略的generated和tmp目录；完整中间产物可按命令重建。

源码清单同时保存历史原始字节摘要与CRLF转LF后的校验摘要，防止Git的文本换行转换被误判为源码语义改变。冻结设计、归档证据和新增知识文件通过.gitattributes保留精确字节；运行时知识快照仍采用精确字节策略，不改成源码的换行归一化策略。

## 7 剩余边界

原始精度乘积仅完成政策显式化、算术实现与软件反例检查；其业务有效性未完成，不能据此断言增加协调环节后的实际精度规律。EV-10及VR-06保留领域复核pending，相关软件防误报检查已通过。

仍未实现自然语言/API、自动创造活动、同环节多法器融合、动态执行/重规划、任意多对象多参数流程、自动插入等待及FDNA扩展。固定时间冲突会排除组合，不会自动调整任务时间。完成跨度和总成本是报告值，网络目标仍为受约束的平均链评分。
