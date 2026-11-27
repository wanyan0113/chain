# 第五阶段：通用流程集成与有范围反馈

本阶段实现任务书的通用流程接口与反馈原型。验收范围是合成文档业务：规划、独立重放、显式能力映射、给定服务快照检查、具体候选反馈和继续搜索。旧design能力/装备入口实际适配、组网和FDNA接入仍未实现，不能以本阶段通过替代其验收。

## 使用

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.integrate --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/feedback_fallback.json --mapping task_flow/examples/document_workflow/service_mapping.json --context task_flow/examples/document_workflow/services_backup_offline.json --output generated/task-flow/integration-1.json

# 复现另一份验收证据；不覆盖原目录
.\.venv\Scripts\python.exe -B -m workflow_tools.task_flow_integration_checks --output-dir generated/task-flow/phase5-check-1

.\.venv\Scripts\python.exe -B -m unittest tests.test_task_flow_external
```

CLI退出码：0为选出候选；1为本次未选出候选（需看分类，不能都解释成无解）；2为输入/文件错误。输出文件拒绝覆盖。API为 `task_flow.external.IntegrationSession`，不改变原plan/verify入口。

## 输入契约

活动库和请求沿用task-flow-1.0。能力声明的mapping_status=unmapped保留其原始含义，独立映射不回写活动库。

| 对象 | 字段及语义 |
| --- | --- |
| 映射 | format_version=task-flow-mapping-1.0、mapping_id、version、backend、entries、source_refs |
| 映射项 | capability_uri、service_id、非空source_refs；每个URI最多一项 |
| 上下文 | format_version=task-flow-context-1.0、context_id、version、services、source_refs |
| 服务项 | service_id、available、非空source_refs；每个服务最多一项；available只能为true、false或字符串unknown |

正式校验见[prepare_external](../../../task_flow/external.py)。拒绝额外字段、重复URI/服务、缺版本、空来源、把1当true等。映射/服务各至多10000项，并受共享JSON树大小限制。缺少服务记录按未知处理。service_id是业务服务身份，不自动解释成装备或参数对象匹配。

唯一实现后端为document-service-availability，支持声明范围内的0～100步流程。其他后端（包括legacy-design）返回unsupported_mapping，连空流程也不宣称兼容，不伪造活动、角色、指标或选中结果。

## 处理与反馈

1. 先独立重放候选的序列、版本、ID、后状态与能力，不相信passed字段；拒绝或验证超限时外部检查not_run。
2. 保留每步完整实例、版本、参数、能力URI、活动来源、能力来源、映射来源与服务快照来源。
3. 缺映射为unsupported_mapping，缺服务/unknown为blocked_unknown，显式false为rejected，全部明确为true才passed。多种问题同时存在时，汇总优先级为unsupported_mapping、rejected、blocked_unknown；全部逐项失败仍保存。
4. 只缓存明确rejected的确切候选。绑定包含候选ID、目录摘要、请求摘要、映射全文摘要、上下文全文摘要、分析器ID和版本。显示名称相同不能代替全文相同，不向活动或前缀推广。
5. 从保留前沿继续搜索，同终态不同序列仍保留。缓存命中也重做独立重放，所有输入和输出使用副本。

FeedbackStore可在进程内共享，不支持导入任意JSON拒绝断言或跨进程检查点。它是受信组件内部缓存，不是恶意进程隔离机制。给定可用性快照只代表输入声明，不证明实际执行、容量、库存、排程、权限或成功概率。

## 结果与预算

| 字段/状态 | 含义 |
| --- | --- |
| search | 原规划报告、输入、累计节点/时间、边界和覆盖率 |
| attempts | 候选、独立验证、外部逐步证据、失败索引、绑定与缓存命中 |
| selected | 找到逻辑及声明服务可用性都通过的候选后停止 |
| analysis_limit | 用完累计候选检查预算，不判断其他候选 |
| search_limit | 搜索预算超限，不能声称穷尽 |
| search_exhausted | 逻辑搜索完成，仍须单看外部未知或映射失败 |

max_checks是累计正整数上限（至多10000），包括缓存命中；恢复只能提高或保持。搜索的节点、时间、候选预算也不清零，API可显式提高节点/时间预算。搜索时间不包括外部分析时间；本轮没有效率排名。本后端为有限本地运算，不调用远程服务。

只有搜索穷尽、没有未知分支且全部候选明确被外部拒绝时，或已知边界内没有逻辑候选时，才报告no_supported_flow_in_declared_bounds_and_context。未解决映射、未知、验证拒绝/超限均保留undetermined。规划无解和外部条件下没有可用流程保留各自字段。

execution_confirmed=false、legacy_integration_status=not_implemented始终保留。逻辑通过、独立重放通过、服务声明检查通过、旧系统集成通过不能相互代替。

## 验收与追踪

[16项验收摘要](evidence/summary.json)及同目录各项JSON包含有效输入、预期、实际、判据、完整搜索、验证轨迹和外部分析。source_sha256_lf_normalized锁定相关源码（仅CRLF规范为LF）。前四阶段审查文件保持原样，不能自动覆盖新增代码。

[回归日志](tests.log)：215项既有相关回归、9项审查工具测试及当时20项新增集成测试，共244项通过。随后增加证据生成器测试，新增集成测试最终为21项，[最终记录](integration-tests.log)通过；计数重叠，不相加作为研究得分。

| 验收 | 输入与预期 | 证据 |
| --- | --- | --- |
| A07、A12 | 首个backup三步候选因服务不可用拒绝，恢复至standard三步候选 | fallback.json；首候选失败恢复单测 |
| A09 | 相同上下文复用确切拒绝；快照变化后重新分析 | feedback_first/repeat、context_changed；版本失效单测 |
| A10 | 请求旧后端的两步流程，保留能力与来源并返回不支持 | unsupported_legacy.json；0～4步不支持单测 |
| A12通用部分 | 0、1、2、3、4步流程通过独立验证和服务检查 | already_done、mandatory_verify、collected、basic、verified |
| 边界 | 未知服务、逻辑未知、长度无解、全部拒绝、搜索/分析超限分开 | 其余验收JSON及恢复单测 |

第五阶段通用原型已验收，第一阶段设计中的旧能力/资源实际接口复用仍未验收，R10/R12不能标为全量完成。旧接口明确不支持是诚实的兼容性结果，并非旧系统已接通。

## 保留的研究警惕

- 三方一致仍可能共享模型定义错误。第六阶段增加语法合法但语义不合理的活动/规则，区分可检测矛盾、建模假设与需人工审查的遗漏，不承诺自动发现所有不合理模型。
- 1410条长度0～3序列是小规模正确性证据；随实例数、长度和约束变化的效率实验单独设计预算和指标。
- 前四阶段审查改进属于证据体系补强。本阶段新增通用集成与反馈实现，没有改动搜索、状态语义、独立验证算法或旧系统生产模块，不声称规划性能提升。
