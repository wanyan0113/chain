# 第三阶段检查记录

日期：2026-11-27。父提交为第二阶段`850dcd2`，工作分支`codex/task-flow-planning`。

## 交付与范围

实现有限实例上的有界顺序流程搜索，支持first_feasible与enumerate_bounded、进程内恢复、累计节点/时间/候选预算、三类终止结论和候选能力需求输出。活动与状态模型沿用第二阶段，搜索不依赖完整线程模板。

本轮同时修正生成侧三值组合判断：存在已知前置条件失败时，其他未知条件不会使这条明确不可执行的转换误标为unknown；存在明确禁止状态时也优先确定拒绝。对应新增定向测试，未改本体、旧design引擎或FDNA公式。

## 实际检查

完整相关测试命令：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_task_flow_contracts tests.test_task_flow_state tests.test_task_flow_planner tests.test_network_search tests.test_design tests.test_sequences tests.test_table10_pruning tests.test_flow_scenario tests.test_equipment_bridge tests.test_bridge_intake tests.test_bridge_observed tests.test_flow_compare
```

结果：179项通过，约90.5秒。其中24项为本轮新增搜索测试，46项为第二阶段测试，109项为原有链网与桥接回归。不称为全仓库测试。

随后加入归档结果一致性断言并整理公开包入口，再次运行新模块70项测试通过。提交前以父提交Git归档覆盖本次暂存的10个文件，独立目录复跑70项全部通过，6个文档本地链接及暂存空白检查通过；不依赖本地未跟踪的旧实验数据。

新增搜索测试覆盖：长度0与不同目标、不同初始状态、必需活动、状态相同但能力不同的路径、目标达成后继续枚举、首解连续恢复、输出与输入隔离、稳定候选ID、节点精确触限与恢复、候选超限与保留、单调时钟与暂停不计时、错误预算的原子拒绝、已知失败与未知区别、整数模型、两对象模型、CLI输出不覆盖及输入错误。

独立参考`tests/task_flow_oracle.py`未调用生产grounding、state或planner，从原始对象域与活动构造实例，按全部序列从头重放。8个业务场景、24个固定种子20261127小布尔模型、1个两对象模型与1个整数模型的可证实流程集合逐项一致，未见重复或遗漏。业务场景实际数值归档在example_results.json，并在测试中核对归档数量、目录摘要和结论。

模板之外的活动组合、恢复和状态语义得到这些有限实例的支持；它们不证明任意输入、任意规模上的完备性或现实可执行性。测试参考不是生产独立验证器。

## 状态解释复核

- too_short在长度2下穷尽且无未知，报告bounded_no_solution；不推广到更长流程。
- unknown_source报告blocked_unknown，不伪造完成条件或直接当作无解。
- max_nodes=1报告limit_reached及partial，精确记录1次实例尝试。
- max_candidates=1触限时保留已有候选；扩大预算后能继续并得到与穷举相同集合。
- first_feasible仅暂停，并非集合完整；再次run能得到另一个候选。
- coverage=complete只指声明边界下的可证实序列集合，不包含未知条件下未经证实的潜在流程。
- 每个候选的verification_status与external_status均为not_run；不把生成侧检查冒充独立验证或资源通过。

## 后续边界

第四阶段需实现独立重放验证器，检查候选版本、全部步骤、状态变化与最终目标；不得复用state.py执行逻辑作为独立证明。第五阶段再接入现有能力/资源分析和有作用范围的失败反馈。CLI报告不能跨进程恢复搜索前沿，不支持并行分支、实时重规划、资源调度或性能最优性。
