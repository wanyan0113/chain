# 能力映射及反馈接口核对

本次只审查已实现的通用服务接口，复用现有能力/反馈测试；不实现旧能力/装备适配、不运行目标组网。

## 语义与实际证据

| 问题 | 当前约定 | 检查结果及边界 |
| --- | --- | --- |
| 一活动能否要求多项能力 | required_capabilities是非空、唯一URI列表；所有项都必须落实 | 能力探针为submit同时要求Submit与Archive；任一服务明确不可用则拒绝 |
| 一能力能否用于多个活动 | 同一声明URI可以在多个活动需求中引用 | Archive同时用于submit与archive，原身份和来源保留 |
| 是否保留依据 | 活动来源、能力声明来源、映射来源、服务快照来源分别保留 | 新增质量测试检查来源；绑定完整目录与请求摘要 |
| 无法映射 | 缺需求映射或后端不支持即unsupported_mapping | 不用占位能力或指标填充；逻辑通过字段保持独立 |
| 活动约束是否丢失 | 外部分析先用完整catalog/request及候选重新独立重放 | 条件、效果、禁止条件和目标仍在原模型中检查，不从服务能力URI重建 |

需求列表的“多能力”不是多个资源联合分担同一能力。多个URI映射到服务也不证明容量或并发可用。只有服务存在性/可用性合取被本轮接口表达。

外部步骤列表只含身份、参数、能力及来源，不是完整可执行模型。若下游脱离目录、请求、版本与摘要，只取能力列表，不能声称保留了活动逻辑约束。

## 必须随候选保持的上下文

- 完整活动实例序列，含活动ID、版本和有限参数绑定。
- 原目录、原请求、目标及约束，或其可解析的受信版本引用；绑定摘要核对。
- 独立验证状态和轨迹，外部检查状态单列。
- 映射全文及版本、上下文全文及版本、分析器身份/版本。
- 失败步骤、原因、证据和exact_candidate_in_exact_context作用范围。

反馈只缓存明确拒绝，不把未知或不支持当作普遍不可行；缓存命中仍独立重放。目录、请求、映射、服务状态或分析器改变后，原绑定不能直接复用。不得把一个候选的失败推广到所有共享活动或前缀。

已存在且本轮重跑的反馈测试包括：

- `tests.test_task_flow_external.ExternalIntegrationTests.test_same_context_reuses_only_exact_candidate_rejection`
- `test_changed_availability_same_context_label_invalidates_rejection`
- `test_mapping_changes_and_analyzer_version_invalidate_cache`
- `test_catalog_and_request_are_part_of_feedback_scope`
- `test_unknown_and_unsupported_results_are_not_reused_as_rejections`
- `test_feedback_lookup_returns_copy_and_does_not_accept_general_scope`

## 旧接口边界

第一阶段已记录旧系统的固定角色、最少步数、输入资源/评价字段及后续桥接限制。当前通用适配器没有实现任何legacy-design转换，所以当前行为是对该后端统一返回unsupported_mapping；不能仅靠某流程长度或活动名称推定已经兼容。

允许哪些旧输入、哪些不能接入，必须由一个独立、明确、经过验收的适配契约界定。当前没有该实际适配器，也没有通过假步骤、虚构资源指标或改写活动语义绕过限制。本次接口审查不能替代旧系统集成验收，更不能替代十五目标完整系统、资源共享、多装备协同或动态调度的各自结论。
