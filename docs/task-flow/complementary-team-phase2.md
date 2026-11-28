# 能力互补第二阶段：显式指标与候选集联合优化

日期：2026-11-28。前置基线：`b5adc42`（开始一环节多装备）。本轮保持原分支，原单装备评分与第一阶段可行性入口不变。

本轮实现的是**固定显式时间区间下**的装备组评价、跨流程统一归一化和资源约束联合选网。不是自动调度，也不是已经验证的装备融合精度模型。表10并行目标负载、业务RDF、观测与任意协作拓扑仍不在此模式范围。

## 评价契约

格式：`task-flow-team-evaluation-1.0`；政策：`team-fixed-interval-explicit-quality-1.0`；[机器Schema](../../task_flow/team_evaluation.schema.json)。

每个“任务＋完整流程＋环节＋成员＋能力分工＋汇聚者”的执行方案，都有自己的指标记录与稳定`plan_key`。记录绑定第一阶段输入、知识/执行上下文及完整候选集合；不同活动流程中的同成员方案不能误用另一流程记录。

| 输入 | 必须提供的内容 |
| --- | --- |
| 每名真实成员 | 正的执行耗时、当次执行成本及其单位、来源引用 |
| 融合过程 | 显式融合耗时和成本、来源引用；确实无开销时也须明确填0 |
| 环节组级质量 | 0～1的独立声明值、来源及性质：合成假设/专家假设/实测声明 |
| 评价政策 | 来源、组级质量含义及串行连乘的适用假设 |

组内耗时采用显式政策：

```text
T_stage = max(各成员执行耗时) + 显式融合耗时
C_stage = sum(各成员当次执行成本) + 显式融合成本
A_stage = 外部明确提供的组级质量值
```

**不读取成员精度来相乘，也不自动计算冗余概率。** 单成员方案同样需要独立的环节质量声明。来源字段本身不构成实测证据；即使输入标为measured，软件也不会自动把领域有效性改为passed。

当前要求`T_stage`与第一阶段给定的对应环节区间长度严格一致；不一致则拒绝，不能把较慢组强行塞入原区间或悄悄缩短占用。成员始终占用整个显式区间，再计自身冷却。暂不自动为不同装备组重新生成时间表。浮点数按有限JSON数值处理，无隐含容差或单位换算。

串行链采用显式的新政策：

```text
T_chain = sum(T_stage)
A_chain = product(外部声明的各环节组级质量)
C_chain = sum(C_stage)
```

这是**环节组级质量之间**的串行乘积假设，仍待领域验证；不是组内成员精度的乘积。`T_chain`是各活动区间的工作时间之和，不把外部时间表中的空档偷偷当成计算耗时；网络另行输出实际时间跨度`makespan_s`，包括区间之间的空档。

缺失任一候选的指标、质量或必要来源，整体评价进入blocked，不补0.5、不把该候选静默删除。缺显式时间同样阻塞评价。错单位、无效数值、错误来源绑定、时长不匹配等输入明确拒绝。

## 联合优化与最优性

1. 对第一阶段报告重新执行真实HermiT和独立可行性核验。
2. 保留每任务所有已生成跨流程候选，对全部候选计算原始指标。
3. 冻结候选与指标，再按任务跨流程统一min-max归一化；退化指标统一0.5。
4. 目标沿用各任务所选链评分的平均值，复用现有网络优化器；实际成员容量、库存、共享资源组、冷却及先后依赖继续作为硬约束。
5. 独立复算指标、评分、网络关系及资源占用；在独立预算内穷举候选组合，对照所选结果和确定性平分排序。

因此，平均值只定义目标函数，不能覆盖或替代资源约束。局部分数低一些但能解除冲突的候选仍可能进入整体最佳网络；不会预先只保留各流程的最高分链。

新模式输出最优性至多为`candidate_set_optimum`，范围限已生成候选及显式评价/时间政策。第一阶段的覆盖标记原样保留，因其并非独立的全空间覆盖证明，本轮不升级为`bounded_global_optimum`；`global_optimality`保持`not_proven`。

生产搜索预算耗尽时保持`not_proven`，即使独立参考发现当前解恰好最优也不升级。独立参考本身触限时，明确`reference_complete=false`和`optimality_verified=false`。领域有效性一直pending。

## 运行

先完成第一阶段输出，再生成**全空指标采集骨架**：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.team_optimization --source-report generated/task-flow/my-team-run/team_report.json --output-dir generated/task-flow/my-team-metrics
```

补全`metrics-to-fill.json`后执行：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.team_optimization --source-report generated/task-flow/my-team-run/team_report.json --metrics generated/task-flow/my-team-metrics/metrics-to-fill.json --output-dir generated/task-flow/my-team-optimization
```

为验证软件，另有**显式合成指标**演示。没有输入来源报告时会重新运行第一阶段，包含实际流程搜索与HermiT：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_optimization_demo --output-dir generated/task-flow/my-team-phase2-demo
```

本次实跑：28候选链，独立参考穷举196组合、16可行网络；最佳网络分数`0.6`，当次执行总成本25 demo-credit/use，时间跨度40秒。两个任务分别选择`{O-Phase-Tuner,O-Safety-Auditor}`与`{O-Combined}`，解除成员争用。正向组级质量在演示代码中逐方案明确赋值，不来自设备实测或成员accuracy计算。

原始CLI交付：`generated/task-flow/complementary-team-phase2-delivery/`；演示及预算记录：`generated/task-flow/complementary-team-phase2-trial/`；[归档验收摘要](complementary-team-phase2-evidence/verification.json)。

## 验收边界

新测试覆盖：独立穷举、成员成本和融合开销、组级质量来源、跨流程统一评分、退化指标、缺指标整体阻塞、错误单位和绑定、时间不匹配拒绝、评价变化不改变执行候选身份、预算下有解/无解、预算解不得继承参考证明、独立参考触限，以及指标/分数/资源/网络关系/领域结论篡改。

已有第一阶段、单装备成链、原候选评价及原联合网络测试同时回归。此次没有改动原生产评分公式或把新模式送进旧RDF/观测出口。下一阶段输出/观测扩展仍待单独实施。

本轮61项联合测试通过，另补1项“省略可选网络节点预算”的真实流程/OWL/独立核验测试通过，共62项不同测试。JSON落盘后重新读取的完整报告复核通过；预算6下已找到分数0.6的可行解，但生产证明状态仍为not_proven。归档保存完整报告、空指标骨架、合成指标和预算反例；测试控制台全文未归档，测试命令与实际计数记录在验收摘要。
