# 补齐观测语义并运行task_flow多环节链网

本轮完成两项工作：以独立v2契约补齐上一轮5项观测语义缺口；新建虚构“星穹观测站”场景，从task_flow搜索，经实际HermiT本体推理生成候选链、受资源约束选网并导出RDF。旧v1接口、原评分公式和历史报告未改写。

## 真实跑出的活动流程

| 任务 | 自动搜索出的所选活动顺序 | 环节数 | 类型数 |
| --- | --- | --- | --- |
| T1 北侧裂隙 | 探知→备用解析→处置决策→稳定 | 4 | 3 |
| T2 东侧裂隙 | 探知→备用解析→处置决策→安全复核→稳定 | 5 | 3 |
| T3 核心裂隙 | 探知→备用解析→相位校准→处置决策→安全复核→稳定 | 6 | 3 |

各解析、校准、决策、复核活动均映射到decide，作为独立环节实例保留，只有observe/decide/act三类型。所选法器如下：

- T1：R-Survey → R-Analysis-Backup → R-Dispatch → R-Seal-D。
- T2：R-Survey → R-Analysis-Backup → R-Dispatch → O-Safety-Auditor → R-Seal-B。
- T3：R-Survey → R-Analysis-Backup → O-Phase-Tuner → R-Dispatch → O-Safety-Auditor → R-Seal-C。

任务输入仅含初始事实、需求标志和最终稳定目标，活动定义提供前置条件/效果；输入没有预写顺序模板。任务都采用长度上限7，搜索得到6条可行活动流程（各有主/备用解析分支）。既有适配器会把搜索结果自动编译为内部序列表示，历史字段仍叫templates；这是内部兼容机制，不是提供给规划器的预设路线。

反事实检查：将核心任务的校准/复核需求标志都改为false，保持活动库和长度上限不变，task_flow自动找到4步流程。将校准法器置为不可用，原核心任务仍需要6步，但无法组成覆盖全部三任务的可行网络，不会擅自跳过校准。

## 本体、链和网的证据

新本体定义TunerUnit为协调法器类，并有hasCapability value PhaseTune限制。O-Phase-Tuner在场景中只有声明的本体类，没有手填role。检查实际reasoning_input.ttl时，该法器的PhaseTune能力三元组不存在；HermiT推理结果中存在，说明能力确实经过本体推导。

| 检查 | 结果 |
| --- | --- |
| 流程搜索完整 | true，6条流程 |
| 候选链 | 18条 |
| 跨任务候选网络组合 | 192种 |
| 独立穷举可行网络 | 64种 |
| 网络评分 | 0.8493957213534583 |
| 最优性 | bounded_global_optimum，限声明的活动库、长度及候选边界 |
| RDF检查 | ERROR=0，WARN=0 |

主优化器实际访问144个完整组合，并通过约束前缀剪枝排除部分冲突；独立参考检查全部192种，确认64个可行组合、所选候选ID与最佳评分一致。因此64不是只根据访问叶子数推断出的全空间数量。网络处理容量、库存及Table10约束，三任务分别使用不同的消耗式稳定法器。

## v2补齐的统计语义

| 审查发现 | v2实现 |
| --- | --- |
| OBS-SEM-001 整条样本丢失不可见 | 独立入组登记及期望环节列表；冻结登记摘要和原始快照，识别全链漏记；删改登记表也阻塞 |
| OBS-SEM-002 评估分母不明确 | 每个stage_instance_id、每个split分别列期望、记录、执行、可评价、正确数和原因；后续阶段不会因中间缺失而被删掉 |
| OBS-SEM-003 标签相同不能追溯来源 | input_reference、output_reference及input_source_observation_id；核对同一入组样本、执行、声明的来源环节、引用及时间 |
| OBS-SEM-004 终点事件范围不足 | 独立endpoints表，分别保存真实参照、实际终点、final_outcome和评价方法/证据；不从末环节分类或乘积生成任务完成结论 |
| OBS-SEM-005 缺失/重试语义 | 执行、记录、评价状态分离；缺失原因必填且与状态一致；attempt_id及冻结的first_attempt/last_attempt规则 |

v2采用分表JSON而非往旧CSV追加列：protocol、enrollments、observations、endpoints。每个观察都有活动和法器版本、评价方法和引用。独立终点只按每个任务执行记录一次。输入/输出引用是可追溯标识，程序不主动读取外部内容，也不凭不同引用自动证明现实来源独立。

first_attempt按预先冻结规则选择首轮，即使重试正确也不事后选“最好一次”；last_attempt只能通过新的冻结协议明确使用。重试增加n_attempt_records及实际执行次数，但不会增加独立样本数。执行失败计入执行尝试，不自动变成可评价错误；末端失败需独立终点评价，未知则保留缺失。

冻结前排除的对象保留在登记快照中，列出原因，不进入相应合格分母。各环节同时展示覆盖和可评价率；任何缺失、完整性错误、覆盖不足或不支持的聚类推断都会阻塞统计判断。v2保持独立样本下的原Wilson/Bonferroni包络，没有新增复杂统计模型。

## 本次观测演练

对实际选中的三条链生成明确标注的synthetic_simulation：72条入组记录（每任务12校准、12评估）、361条环节尝试记录（含1次额外重试）、36条独立终点记录。版本simulated-device-1.0是模拟版本，不是真实设备版本。

环节以合成类别真值做比较；任务终点采用另一条明确声明的模拟稳定状态规则（每4个样本有1个外部扰动导致不稳定），并保留独立遥测与参照标识。该终点不取自环节精度乘积或最后类别输出。样本量仅用于接口演练，三任务统计结论均为inconclusive；领域有效性继续pending。

5个端到端反例均被阻塞：整条样本漏记、中间环节缺失、同标签的错误输入来源、独立终点丢失、冻结后删改登记表。17项新v2测试与既有审查/采集/评价回归共60项通过，包含未到达/未执行/失败原因一致性、引用自评泄漏、校准与评估隔离、版本和协议修改、预定义排除、重试取值等。

这只说明新契约在声明范围内补齐软件检查，不声称数据引用真实、人工标签可信或精度模型符合真实设备。原sequential_product_v2和选网目标没有修改，也没有把模拟精度回填到生产法器属性。

## 运行和查看

完整输入配置与属性在[场景目录](../../../../task_flow/examples/observatory_multistage/README.md)。一条命令完成搜索、真实推理、成链成网、RDF、v2演练和反例：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observatory_demo --output-dir generated/task-flow/my-observatory-run
```

输出目录必须不存在。查看network/design_summary.md、network/design.ttl、verification.json、observations-v2-report.json。network/input_jobs.json等文件保存本次搜索实际输入，batch-001-reasoning_input/result.ttl保存OWL证据。

v2也有独立入口，协议及登记准备好后：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observations_v2 freeze --study path/to/study-v2.json --joint-report path/to/joint_report.json --lock path/to/study-lock.json
.\.venv\Scripts\python.exe -B -m workflow_tools.observations_v2 analyze --study path/to/study-v2.json --joint-report path/to/joint_report.json --lock path/to/study-lock.json --output-dir generated/task-flow/my-v2-analysis
```

冻结研究定义、登记队列和源设计，observations/endpoints随后采集；本轮已声明为回顾性合成演练，不冒充事前真实采集。输出保留协议/登记摘要、观察/终点表摘要及输入快照。v1继续可用且历史证据不变，不能自动把其CSV解释为v2。

正式运行证据：[汇总](evidence/verification.json)、[网络摘要](evidence/design_summary.md)、[业务RDF](evidence/design.ttl)、[v2结果](evidence/observations-v2-report.json)、[完整运行包](evidence/full-run.zip)、[测试日志](evidence/regression-tests.log)、[摘要清单](manifest.json)。

本次工作留在原分支工作区。此前提交操作被拒绝，本轮没有重新提交或推送。
