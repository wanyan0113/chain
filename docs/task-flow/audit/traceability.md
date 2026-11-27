# 持续需求—实现—测试追踪表

本表更新第一阶段R01～R12的落实状态；[原始设计表](../phase1/requirements.md)作为历史保留。本轮基线为4ea1145，实际输入、预期和结果见[专项记录](phase1-4/acceptance.md)。

“已覆盖”只指本表列明的实现、测试实例和声明边界，不是任意输入正确性的证明。C19是防止结论越界的检查，不能用于把尚未实现的R09、完整R10或R12标为通过。

| 需求 | 实现位置 | 对应测试选择器 | 专项证据 | 当前结论 |
| --- | --- | --- | --- | --- |
| R01 活动、版本及能力声明 | `task_flow/contracts.py:prepare_problem`、`schema.json` | `tests.test_task_flow_contracts.TaskFlowContractTests.test_strict_missing_and_unknown_fields` | C01、C07 | 已覆盖结构和版本；URI为机器可读列表，不能据此认为能力已映射 |
| R02 有限域与严格类型 | `contracts.py:_check_tree`、`_bindings`、`prepare_problem` | `tests.test_task_flow_contracts.TaskFlowContractTests.test_ground_limit_checked_before_large_allocation` | C02、C06、C23 | 已覆盖有限域、落地上限与冲突拒绝；不支持连续参数 |
| R03 三值、效果与派生状态 | `state.py:evaluate_condition/state_view/apply_activity`、`validator.py:_evaluate/_Replay` | `tests.test_task_flow_state.TaskFlowStateTests.test_numeric_overflow_rolls_back_whole_step`；`tests.test_task_flow_validator.TaskFlowValidatorTests.test_full_three_valued_truth_tables` | C03～C06、C21、C22 | 所测真值表、原子更新及混合模型中一致 |
| R04 按需求组织活动 | `planner.py:SearchSession.run` | `tests.test_task_flow_planner.TaskFlowPlannerTests.test_first_sequences_follow_initial_state_and_goal` | C08、C10、C22 | 所测输入会产生不同活动组成/长度；不依赖完整模板 |
| R05 最终目标与必需活动分开 | `state.py:goal_status`、`validator.py:_Replay.verify` | `tests.test_task_flow_validator.TaskFlowValidatorTests.test_goal_achieved_then_destroyed_is_rejected` | C08、C09 | 已测空流程、强制确认与最终破坏；非执行期间实时重规划 |
| R06 独立重放及版本绑定 | `validator.py:verify_sequence/verify_candidate` | `tests.test_task_flow_validator.TaskFlowValidatorTests.test_does_not_call_generation_state_or_planner`；`tests.test_task_flow_validator.TaskFlowValidatorTests.test_forged_final_state_capabilities_identity_and_goal_claim` | C07、C15～C18 | 执行语义独立性有证据；Schema/合法性检查仍是共享依赖 |
| R07 首解、恢复、枚举与身份 | `planner.py:SearchSession` | `tests.test_task_flow_planner.TaskFlowPlannerTests.test_successive_first_calls_recover_every_candidate_without_duplicates` | C10～C14、C20 | 测试边界内集合一致；完整实例序列不因同终态合并 |
| R08 无解、未知和超限 | `planner.py:run/report`、`__main__.py` | `tests.test_task_flow_planner.TaskFlowPlannerTests.test_known_no_solution_unknown_block_and_limit_are_distinct` | C12、C21 | 已验证分类；complete仅指声明边界内已证实序列集合 |
| R09 外部失败反馈作用范围 | 第五阶段待实现 | 尚无正式反馈实现验收用例 | C19只检查不越界宣称 | **未验收**；恢复搜索存在不等于上下文反馈协议已实现 |
| R10 能力与来源适配 | `planner.py:_candidate`保存需求；旧资源适配待实现 | `tests.test_task_flow_planner.TaskFlowPlannerTests.test_same_state_different_capabilities_not_merged` | C01、C13、C19 | **部分覆盖**：能力需求及来源保留；unsupported_mapping适配入口尚未实现 |
| R11 独立穷举与错误流程 | `tests/task_flow_oracle.py`及专项工具 | `tests.test_task_flow_planner.TaskFlowPlannerTests.test_seeded_boolean_models_match_independent_oracle`；`tests.test_task_flow_validator.TaskFlowValidatorTests.test_all_small_raw_sequences_match_independent_exhaustive_reference` | C10、C15、C17、C22 | 小规模证据已覆盖，综合实验和一般性论证未完成 |
| R12 完整端到端集成 | `__main__.py`已有规划/验证CLI；外部集成待实现 | `tests.test_task_flow_validator.TaskFlowValidatorTests.test_cli_plan_verify_and_reverify_saved_report` | C19、C23 | **部分覆盖**：逻辑CLI已测；原能力/装备/资源/反馈端到端尚未验收 |

## 有界性核对

1. 对象和活动参数必须来自声明的有限域；事前计数并限制落地事实、规则及活动总量。
2. 流程长度有明确上限；循环或无状态变化的活动也受长度和次数限制。
3. 搜索有累计节点、时间和候选数量预算；恢复不清零已用预算。
4. 独立验证限制序列长度及状态轨迹规模；批量验证限制候选数，触限不冒充全部通过。
5. API虽可显式提高某些预算，仍需正整数和上限；“预算有限”不表示实际问题一定能在给定时间内求解。

## 更新原则

修改需求、协议或执行语义时，同步更新实现位置、测试选择器和实际审查记录。不得只增加“支持”描述而无输入/预期/实际证据。第五阶段开始后，优先补R09、完整R10和R12，再讨论完整系统效果。
