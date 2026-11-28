# 装备组观测导入与独立核验1.0

本轮接通独立的新入口，支持冻结的`team-observation-contract-1.0`六表JSON或CSV输入、研究登记锁、独立重读与关联核验，并加入实际HermiT观测一致性检查。旧单装备入口、原v2观测、生产组网、公式和权重均未改动。

初期新旧入口分开。以后可以在统一界面中按明确版本路由到不同处理器，不需要重写或误解释旧数据。本轮没有提前实现统一入口，也不会把未知版本猜成旧版或新版。

## 当前实现范围

| 层次 | 行为 |
| --- | --- |
| 导入器 | 读取版本、六表和元数据；严格解析JSON、CSV布尔/整数/数组/空值；检查冻结Schema；保存原始字节与规范化快照 |
| 独立核验器 | 不调用导入器解析函数，从保留的原始字节重新解析；核对快照、协议/研究锁/源报告、六表主外键、全局ID、实际成员与能力分工、时序、重试、配对、cluster与split |
| 本体检查 | 重跑源候选的既有独立OWL核验，再用独立观测校验本体运行真实HermiT；保留类型推导、能力依据和输入图证据 |
| 统计边界 | 只输出按候选/环节/成员/能力/版本/总体/split分层的描述计数；不计算置信区间、质量预测或概率校准，不写回生产评分 |

数据采集支持**已核验但未评分的可行候选**，也支持既有装备组优化报告。前者不需要已有组级质量，避免“先知道精度才能采集验证精度”的循环。源文件仍须逐个匹配协议中的内容摘要，不能拿输入行自行宣称的成员和能力代替源证据。

## 入口与运行

完整显式合成演示（包括真实源OWL复核、研究登记锁、CSV导入、独立重读和观测OWL）：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_observation_demo --feasibility-source --output-dir generated/task-flow/my-team-observations
```

演示仅使用虚构版本、样本、标签、环境/复位引用与研究评审引用，不是装备实测或真实预注册。6个样本/cluster、12次成对执行、48个环节首次槽位，产生60条逐能力成员观测、48条组输出和12条独立终点；终点正确9条，没有从48条组输出全正确推算任务成功。

新入口：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_observation_intake --dataset packet/dataset.json --protocol packet/protocol.json --lock packet/lock.json --sources packet/sources.json --output-dir generated/task-flow/my-team-intake
```

`dataset.json`可以是六表完整JSON，也可以是CSV元数据，包含版本、研究ID、协议摘要、数据性质和六张CSV的完整文件映射。文件引用必须位于所属包目录内。CSV顺序与冻结表头一致：空单元格为null、布尔为true/false、数组为JSON数组；标签`"0"`不会变成布尔或缺失值。旧格式保持在原入口处理；新入口明确返回`UNSUPPORTED_VERSION`。

来源清单格式：

```json
{
  "version": "team-observation-sources-1.0",
  "sources": [{"source_report_id": "source-1", "report_file": "source-0.json"}]
}
```

独立核验可以单独重跑：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_observation_verify --intake-dir generated/task-flow/my-team-intake --expected-receipt-hash <独立保留的receipt摘要> --output generated/task-flow/my-team-intake-recheck.json
```

输出目录与核验输出文件均须不存在。输入文件不会被修改。`imported_structure_only`只表示文件读取和结构检查通过，必须另看`semantic_verification`；任一阶段未通过，入口退出码为2。格式错误报告包含代码和字段路径，业务错误包含表、数据行号、列及样本/执行/环节/成员等定位信息。数据行号不含CSV表头；诊断触限时明确标记截断。

## 研究锁与计划/实际分离

冻结前的`plan.samples`、`plan.executions`、`plan.first_stage_attempts`是独立封存的计划投影，不是实际执行事实。执行后`stage_attempts`记录实际状态、开始/结束时间、失败和重试。删除实际首次记录会同时得到`MISSING_ACTUAL_FIRST_ATTEMPT_RECORD`和计划差异诊断，而锁中的48个槽位仍然保留。

研究锁创建入口：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_study_lock --dataset registration-plan.json --protocol protocol.json --sources sources.json --review-ref <研究评审证据引用> --output study-lock.json
```

它要求协议字段完整且声明frozen，非空的基准队列、执行和首次槽位已登记，实际尚未开始，登记数据中不含观测输出，源候选核验通过。锁绑定协议、来源和计划，拒绝覆盖已有文件。初始计划只含首次槽位；后续重试在前次结束后、下一次开始前追加登记。

这是**软件预登记锁**，不是第三方签名或现实时间证明。`external_signature_verified=false`，评审引用和外部事实的真实性仍需独立保管和审查。合成器可预先构造结果，故合成研究锁只验证软件规则，不构成真实试验已经前瞻预注册的证据。真实研究仍需补齐实际定义、版本和条件后另行开展。

## 实际数据流、失败和统计单位

- 观测ID使用统一命名空间，跨三张观测表查重；同一表复合键也独立查重，不能靠OWL合并个体处理重复数据。
- 每条成员观测核对源候选中的成员、负责能力、协议版本和方法。组输出关联同一次实际尝试的成员观测；输入组ID须来自同一样本/执行的已声明前序实例，并检查上游尝试的输出可用时间与下游开始时间。
- 阶段顺序**不自动证明实际数据依赖**。直接读取基准输入的后续环节可以保留，但标记`SERIAL_DEPENDENCY_NOT_ESTABLISHED`，不能据此做自然串行条件模型验证。成员多组输入可保存声明的来源关联，但外部输入bundle内容未展开核验时标记`INPUT_BUNDLE_CONTENT_UNVERIFIED`。
- 合法使用重试输出可以通过数据一致性检查，但会出现`RETRY_LINEAGE_OUTSIDE_FIRST_ATTEMPT_MODEL`分析阻塞。首次尝试分类计数不被第二次成功替换；末端任务结果按独立事件记录。
- 未执行不能宣称已记录实际输出；运行失败、分类错误、观测丢失和真值未知分别保留。失败运行可有独立可评价的输出，不能仅凭失败状态补错误标签。未知观测不补0/1。
- cluster跨校准/评估泄漏、样本配对ID变化、未登记重复、错误总体、缺配对、复位引用缺失和计划/实际顺序不一致均有定位诊断。这里只核验声明与证据关联，不自动证明环境真正等价或复位有效。
- 表级计数单位是**观测行**，不是独立样本数；另报唯一样本和cluster及分层计数。`confidence_intervals=null`、`independence_assumed=false`，不会把12次执行当作12名独立主体。条件事件数据就绪也不等于条件模型已验证。

## 本体具体做什么

[观测校验本体](../../knowledge/team-observation-validation.ttl)只加载到独立审查图，不修改主本体或生产规则。

1. 由经核验来源的实际装备类型与能力公理，检查贡献所属角色，并对观测贡献类型进行真实推理。例如`LinkedContribution`和`ContributedGroupOutput`是推导得到，未作为观测个体类型预先断言。
2. 在本审查政策中，三种装备角色、三种能力类型互斥；协调环节把探知装备冒充为成员时可造成OWL不一致。三类观测也互斥，跨层同ID碰撞可同时触发本体与数据诊断。
3. 能力支持追溯到经真实OWL复核的源`hasCapability`事实。不会因为观测行填写了某能力，就给装备自动断言该能力。缺乏相应本体依据时保持unknown并阻塞能力证据核验。

这些类型只表示链接和角色语义，不表示贡献成功、覆盖完整或组级质量达标。缺成员行仍可能OWL一致，必须由六表完整性检查阻塞；相同角色中的s2/s3错配也主要由精确源实例/分工检查识别。没有Java/HermiT或推理失败时不回退成“本体通过”。

每次报告保留本体文件摘要、输入TTL、推导类型和能力支持记录。这是校验RDF证据，不是装备组业务RDF导出，也不证明实际输出标签或设备版本真实。

## 对抗测试与证据边界

12组实际演练包括正常CSV和11组损坏包：缺成员、T2的s2/s3成员错配、重试冒充首次、跨样本输入、固件版本变化、cluster泄漏、配对ID篡改、未知伪装错误、删掉实际首次记录、全局观测ID冲突及跨角色装备冒用。每组通过真实源核验与可运行时的HermiT检查；格式错误不进入推理。

另有JSON/CSV独立重读、正常重试、首次分母不替换、输入bundle、未评分来源、冻结契约摘要与快照篡改等测试。单元反例为隔离数据检查会复用已验证的来源/本体结果；正向、缺记录与角色/能力冲突另做实际HermiT检查，实际批量演练不使用这些单元替身。

早期演练曾因探针期待`CAPABILITY_NOT_ASSIGNED`、实际正确返回`MEMBER_NOT_ASSIGNED`而中止；修正探针期待后完整重跑，早期目录未覆盖。交付见[验收证据](team-observation-intake-evidence/verification.json)。

回执核对可发现**导入之后**原文件、快照或规范化数据被更改，包括把原始未知标签改成错误标签。它不能证明导入前的输入没被伪造，不能证明URI指向的外部真值内容正确，也不认证实际固件或环境。独立保留回执摘要可加强对回执自身变更的检测；本版本没有外部签名服务。

本轮仅为显式合成数据的软件闭环，没有真实装备观测、真实试采集、精度领域验证或新概率评分。原软件契约的封存内容和历史“尚未实现”状态不回写；当前实现状态以本说明为准。

下一步才是准备真实采集条件并做小批试采集，先确认完整性和追溯，再按独立留出数据验证模型。统一新旧入口可作为后续界面工作，内部版本处理器继续独立。

?????73?????????/????/??40???????33??????12??????????????????????????????????????????????????????????????
