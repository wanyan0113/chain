# 同环节多装备能力互补：第一阶段试验

日期：2026-11-28。模式：`complementary_team_v1`。分支保持 `codex/task-flow-planning`。

本次接通独立的能力互补可行性入口，保留原单装备入口、评价公式和最优网流程。旧代码的改动仅为评分、RDF及观测出口增加明确拒绝新模式的保护。尚未实施装备组质量评价和最优组网。

## 实际运行

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_demo --output-dir generated/task-flow/my-team-run
```

或者直接使用已提供的完整输入：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.team_feasibility --jobs task_flow/examples/complementary_team/jobs.json --scene task_flow/examples/complementary_team/scene.json --mapping task_flow/examples/complementary_team/mapping.json --config task_flow/examples/complementary_team/config.json --output-dir generated/task-flow/my-team-run
```

输出目录须不存在。输入不含预写流程；活动前置条件、效果与目标驱动 `task_flow` 搜索。经独立重放的流程由原编译器转成内部顺序表示，再运行真实 HermiT；没有把装备组伪装成普通装备输入旧成链器。

合成观测站的两个任务各有两条不同解析活动流程，每条都是：探知 → 联合解析 → 处置决策 → 稳定。类型为 observe/decide/decide/act，四个独立实例。联合解析在本试验中明确声明可由两种能力互补实现；这是虚构场景的业务假设，不代表任何真实设备已能融合。

| 成员 | 本体推导能力 | 联合解析单独可行 |
| --- | --- | --- |
| O-Phase-Tuner（A） | PhaseTune | 否 |
| O-Safety-Auditor（B） | SafetyAudit | 否 |
| O-Combined（C） | PhaseTune、SafetyAudit | 是 |

`{A,B}` 和 `{C}` 均被保留。`{A,C}`、`{B,C}` 在双方各承担一项所需能力、协作通过时也合法；没有因存在全能成员就提前删除其他有效分工。成员集合相同但汇聚者或能力分工不同，属于不同执行方案。

第一阶段运行结果：4条逻辑流程，28条候选链（每任务14条），196个网络组合中16个通过实际成员资源检查。独立小规模参考直接从夹具规定的能力重建分工集合，并遍历候选对、独立计数各时段成员占用及库存，结果一致。报告中的所有链和网络还通过新验证器的独立流程重放、全新 HermiT、能力分工、连接及资源复核。

本次交付原始结果位于 `generated/task-flow/complementary-team-phase1-delivery/`，包含完整 `team_report.json`、`summary.md`、每次分析的知识快照及推理输入/结果TTL。这里的TTL是推理证据，不是新模式业务RDF。可审阅的归档位于 [complementary-team-phase1-evidence](complementary-team-phase1-evidence/verification.json)。

## 契约

- 显式新映射版本 `task-flow-team-mapping-1.0`，评价只能为 `feasibility_only`。复用旧编译器时内部临时采用其认可的映射格式，编译完成立即切换为可行性配置；从未执行旧指标计算或优化。
- [配置Schema](../../task_flow/team_config.schema.json)与[环节方案Schema](../../task_flow/team_plan.schema.json)确定第一阶段字段；报告格式为 `task-flow-team-feasibility-1.0`。
- 每环节1～2件同角色装备，每项能力恰有一名主要提供者，每名成员至少承担一项需求。逐成员检查可用性、阻塞、角色、活动等级与适用目标；逐能力检查其提供者的规则。不拼接成员数值属性。没有能力规则或条件缺失仍为unknown。
- 组内采用非汇聚成员 → 汇聚成员直接通联。组间采用汇聚者交接。遇到control时，明确采用上一环节所有实际贡献成员 → 下一环节所有实际执行成员的直接控制政策；不以任意一条控制边代替。
- 相邻组成员交集非空时拒绝；非相邻组复用遵循原映射开关。每次真实执行均登记，不按成员集合去重库存消耗。
- 不自动安排时间。`schedules`按任务给出各环节绝对开始/结束时间，所有成员占用整个环节区间，再附加自身冷却。该时间是外部明确输入，不来自单装备指标。当前同一任务不同候选流程若长度不同，提供的索引式时间表不能直接覆盖，需另行准备一致的实验输入或不提供时间。
- 无时间时仍输出每次成员容量/库存记录，但时间为null；仅核验单链库存，不能声称容量、冷却或跨任务同时执行成立，也不输出可行网络。
- 跨任务网络检查实际成员容量、库存、共享资源组及任务先后依赖。第一阶段**未实现表10并行目标负载规则的装备组扩展**，因此启用 `article_table10=true` 的新模式输入明确拒绝。新演示独立声明 `false`，没有修改原案例或关闭旧入口检查。
- 资源模型为现有静态容量与库存，加显式时间区间；不包含真实融合质量、自动调度、备用切换、多跳路由或异角色组队。

## 身份、预算和核验边界

候选身份版本为 `team-candidate-1.0`，绑定任务上下文、完整流程语义、知识快照、资源/映射上下文、组内/控制政策、显式时间，以及每环节成员、能力分工和汇聚者。成员和能力键规范化；成员输入顺序不改变身份。分数、发现顺序、运行ID、搜索预算不进入候选身份。`run_id`单独生成，来源输入及各分析记录保留在报告中。

流程、流程分析、组队、环节方案数、成链和网络枚举均有预算。触限保留已知可行结果并报告不完整；未找到结果不能据此宣称无解。`candidate_search_complete`还保守考虑未知贡献/连接，`network_search_complete`只表示对**已生成候选集合**的枚举是否完整，不替代候选覆盖。

`verify_teams`不调用组队或成链生成器。它复算来源、重放活动条件与效果、重新执行HermiT、独立检查贡献证据和实际成员分工，并复核必要连接、成员占用、库存和依赖。可传入调用者持有的 `expected_input_hash`额外核对来源。核验通过的含义是列出的可行结果及证据通过检查；`search_coverage_certified=false`，不把生成器覆盖标记包装成独立的全空间证明。`evaluation`与`optimality`始终为 `not_run`。

## 验收与旧路径保护

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_team_feasibility -v
.\.venv\Scripts\python.exe -B -m workflow_tools.observatory_demo --output-dir generated/task-flow/my-legacy-recheck
```

新测试覆盖AC-01～13：互补与单成员并存、缺能力/未知/数值失败、不可用/阻塞/等级、缺边/明确失败、上下游和直接控制、成员容量/库存/冷却、规范身份及分工、贡献/成员/边/逻辑效果/占用篡改、预算截断、旧评分/RDF/观测出口拒绝。无时间降级、全报告输入顺序稳定性和网络预算行为也有检查。

本轮13项新测试、105项原路径相关测试，共118项通过。完整JSON落盘后再次加载，重新执行HermiT及独立核验通过；全部已生成环节方案通过Schema检查。归档包含原始完整报告、推理证据、旧观测站复跑摘要及文件摘要；不包含测试控制台全文，测试数量为本次实际命令的运行记录。

AC-14通过原单装备相关回归及真实观测站重跑：4/5/6环节、6流程、18候选链、独立穷举192组合/64可行网络、最佳评分 `0.8493957213534583`，RDF ERROR=0/WARN=0。原公式与候选身份未修改；原采集和观测测试继续验证旧模式。

本轮仅为合成场景的软件可行性验证。装备组评价、最优性、业务RDF和观测契约留到后续阶段；真实领域精度有效性继续pending。
