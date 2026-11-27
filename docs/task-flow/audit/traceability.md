# 持续需求—实现—测试追踪表

本表更新第一阶段R01～R12的落实状态；[原始设计表](../phase1/requirements.md)作为历史保留。前四阶段审查基线为4ea1145，原始证据见[专项记录](phase1-4/acceptance.md)。第五阶段增量见[阶段说明](../phase5/README.md)，原始证据不改写。

“已覆盖”只指列明的实现、测试实例和声明边界。C19只检查原基线不越界宣称，不能作为第五阶段功能证据；R09和R10/R12通用部分由新增测试支持，旧接口集成仍未验收。

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
| R09 外部失败反馈作用范围 | `external.py:FeedbackStore/IntegrationSession` | `tests.test_task_flow_external.ExternalIntegrationTests.test_changed_availability_same_context_label_invalidates_rejection`；`test_same_context_reuses_only_exact_candidate_rejection` | 第五阶段feedback_repeat、context_changed | 通用后端已覆盖：候选、请求、目录、映射、上下文和分析器绑定；只复用确切拒绝 |
| R10 能力与来源适配 | `external.py:prepare_external/analyze_candidate` | `tests.test_task_flow_external.ExternalIntegrationTests.test_missing_mapping_is_not_logic_failure_and_retains_original_demands`；`test_legacy_backend_explicitly_unsupported_even_empty_sequence` | 第五阶段fallback、unsupported_legacy | **部分覆盖**：通用能力/服务映射及来源、明确不支持已实现；旧资源实际接入未实现 |
| R11 独立穷举与错误流程 | `tests/task_flow_oracle.py`、专项工具及`workflow_tools/task_flow_study.py` | `tests.test_task_flow_study.TaskFlowStudyTests.test_extended_length_correctness_matches_raw_oracle_and_path_count`；`test_schema_valid_domain_faults_do_not_get_mislabeled_detected` | C10/C15/C17/C22及第六阶段8模型、6模型错误探针 | 通用小规模正确性、模板覆盖与效率证据已补；共同建模假设仍可能漏检，一般性论证及旧系统端到端未完成 |
| R12 完整端到端集成 | `external.py:IntegrationSession`、`integrate.py` | `tests.test_task_flow_external.ExternalIntegrationTests.test_cli_end_to_end_and_no_overwrite`；`test_lengths_zero_one_two_three_four_pass_generic_adapter` | 第五阶段16项端到端记录 | **部分覆盖**：通用文档流程反馈端到端已验收；原能力/装备/资源实际端到端尚未验收 |

## 有界性核对

1. 对象和活动参数必须来自声明的有限域；事前计数并限制落地事实、规则及活动总量。
2. 流程长度有明确上限；循环或无状态变化的活动也受长度和次数限制。
3. 搜索有累计节点、时间和候选数量预算；恢复不清零已用预算。
4. 独立验证限制序列长度及状态轨迹规模；批量验证限制候选数，触限不冒充全部通过。
5. API虽可显式提高某些预算，仍需正整数和上限；“预算有限”不表示实际问题一定能在给定时间内求解。

## 更新原则

修改需求、协议或执行语义时，同步更新实现位置、测试选择器和实际审查记录。不得只增加“支持”描述而无输入/预期/实际证据。第五阶段已补R09及R10/R12通用部分，旧接口完整性与系统效果仍须另行验收。

第六阶段的[报销审批应用补充](../application-expense/README.md)增加原始业务字段映射、双对象身份与独立领域规则负例；对应`tests.test_expense_workflow.ExpenseWorkflowTests.test_prepared_files_work_through_existing_integration_cli`及`test_omitted_numeric_policy_passes_formal_model_but_not_independent_monitor`。它补充R10/R12通用数据链路证据，不改变旧接口尚未验收的结论。
