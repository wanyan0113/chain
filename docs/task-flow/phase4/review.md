# 第四阶段检查记录

日期：2026-11-27。父提交为第三阶段f8d6158，分支codex/task-flow-planning。

## 交付

实现独立重放模块、单序列/候选/批量验证API、verify及plan --verify命令、逐步证据轨迹、输入绑定与候选元数据核对、验证预算与未验证状态管理。提供手工三步输入、实际完整验证轨迹和八场景结果归档。总体进展见上一级STATUS.md。

独立实现不调用生成侧state或planner函数，并且不消费prepare_problem产生的initial_base、fact_specs、activities或derived_rules作为执行依据；重新从原始目录/请求构造事实、规则顺序和状态。共享Schema与输入合法性检查仍是共同依赖，不声称完全消除共同错误。

## 实际测试

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_task_flow_contracts tests.test_task_flow_state tests.test_task_flow_planner tests.test_task_flow_validator tests.test_network_search tests.test_design tests.test_sequences tests.test_table10_pruning tests.test_flow_scenario tests.test_equipment_bridge tests.test_bridge_intake tests.test_bridge_observed tests.test_flow_compare
```

结果：215项相关测试通过，约92.8秒。其中新增36项独立验证测试，已有70项任务流程组件测试及109项旧链网与桥接测试。不称为全仓库回归。

随后补充归档一致性断言，再次运行新模块106项测试全部通过。提交前从父提交Git归档覆盖本次暂存的12个文件，在独立目录复跑106项全部通过，12个本地文档链接及暂存空白检查通过；未使用工作区未跟踪实验数据。

新增验证检查包括：

- 7条人工正例、八场景81个生成候选全部通过独立重放；无候选场景保持not_run。
- 在长度0～3下逐一验证156个原始序列，接受集合恰为2条，与另一份独立穷举参考一致。
- 将生成侧条件、状态、执行及搜索函数替换为会抛错的Mock，验证仍通过且这些函数未被调用。
- 污染prepare_problem的执行缓存映射，验证器仍从原始声明重建状态，正确拒绝不满足前置条件的活动。
- 已知/未知/否定的完整二输入三值表，整数条件派生链的重新排序、失效重算及原子更新。
- 步骤交换、遗漏、未知活动、域外参数、版本/摘要不符、长度/次数限制、禁止状态、目标被破坏、未知效果源和数值越界。
- 候选最终状态、能力需求、ID、目标声明及已有通过标记篡改；bool/int伪相等不能绕过比较。
- 报告快照、重复候选、空候选、批量/轨迹超限、输出不覆盖、有限浮点统计及CLI保存后复核。

归档example_results.json的场景数量、状态和目录摘要，以及verified-example.json的完整轨迹，都与测试重跑结果核对。归档只含通用合成业务证据，不包含真实装备或实际任务性能数据。

## 输出语义复核

- rejected、blocked_unknown与limit_reached分别表示已知不满足、条件未证实和验证未完成。
- 拟议效果违反约束时不提交；最终状态保留最后合法状态，trace另保存被拒绝的拟议状态。
- 验证通过只证明在指定活动知识、事实和目标下流程逻辑成立。
- 批量覆盖不验证搜索完整性，search_claims_verified始终false；外部资源状态保持not_run。
- 输入规范化仍拒绝活动/状态中的float；报告解析允许有限统计小数，但会再次严格核对绑定的目录、请求和序列。

## 下一步及限制

第一至第四阶段已交付，第五阶段能力/资源适配和反馈尚未实现；第六阶段完整对比实验与综合分析尚未完成。旧能力推理、装备筛选及资源共享检查保留，但任意新活动流程尚不能直接作为旧链网设计或FDNA桥接输入。仍不支持同环节多装备分担、跨流程动态排程、并行分支及实时重规划。
