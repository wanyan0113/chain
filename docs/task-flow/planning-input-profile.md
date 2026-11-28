# 规划与组网输入的可选元数据

新增显式`--input-profile minimal`，让描述性来源与人工摘要成为可选项。默认仍为`strict`，原入口、核心Schema、计算公式和正式观测契约不变。精简输入先规范化，再交给原有严格规划器、组网器和独立核验器。

本轮只简化规划/组网的人工填写，不放宽正式观测中的样本、成员、实际环节、重试、终点标签或来源报告关联。

## 必填、可选与自动生成

| 内容 | minimal模式 | 说明 |
| --- | --- | --- |
| 格式版本、活动/任务/装备ID及实际引用 | 必填 | 不猜版本，不修补不存在的ID |
| 活动定义、前置条件、效果、初始状态、目标 | 按原契约必填 | 资源矩阵不能替代流程知识 |
| 数值、单位、数值范围、质量量表和适用条件 | 按原契约保留 | 缺指标不补0，不虚构单位或质量值 |
| `source_refs`中的外部文献/说明引用 | 可选 | 支持省略或空数组；填写了无效类型仍报错 |
| `catalog_sha256` | 自动计算 | 可不手填；填写了则先核对原始目录内容，错误不自动纠正 |
| 装备组指标`binding` | 自动计算 | 从提供的源报告计算；原优化器仍独立核验该来源，已有错误绑定不覆盖 |
| 生成器版本 | 可选 | 用`--generator-version`记录；未提供为null，不虚构版本 |
| 合成/专家假设/实测/混合声明 | 必填一次 | minimal模式用`--data-origin`明确声明，不自动猜测；不等于实测真实性已认证 |
| 本体证据的`source_ref`、`knowledge_sha256`、`rule_ref` | 仍按原契约检查 | 这是实际推理证据绑定，不是可有可无的说明文字 |
| 概率解释的`probability_basis.evidence_refs` | 仍按原契约检查 | 不用本地输入引用冒充独立概率依据 |

允许的数据性质声明为`synthetic_simulation`、`expert_assumption`、`real_observational`或`mixed`。声明`real_observational`不自动将具体指标转成实测概率，也不替代各指标已有的`origin`与语义。

装备组的质量定义、成员分工、条件、总体、量表以及串行乘积政策继续保留。缺少质量数值或语义仍不能评分；可以继续使用原有不评分的可行性组网路径。旧评价1.0数据不会因此自动升级成1.1。

## 未提供来源时会发生什么

不要求为演示数据编写假论文引用。程序保留原始JSON输入快照，对其规范化内容计算SHA256，并生成类似以下的内部引用：

```text
input-snapshot:<规范化输入摘要>#/jobs/0/catalog/activities/1
```

它只表示“这个定义来自本次提交输入的这个位置”，**不表示有外部文献、实测或独立验证支持**。报告逐项标记`external_source_status=not_provided`，并保留`local_references_are_external_evidence=false`、`external_sources_authenticated=false`与`domain_validity=pending`。没有为指标或事实插入默认数值。

如果原始目录提供了正确摘要，规范化新增本地引用后，会另算核心目录摘要，并记录前后绑定。若原始摘要本来就错，立即阻塞。删改目录后不想手算摘要，可省略`catalog_sha256`，不能保留旧摘要并让系统忽略冲突。

本地摘要针对规范化JSON内容，不是原文件字节、设备真实性、第三方签名或现实发生时间的证明。独立核验器复核的是规范化后的规划/网络结果；本轮未新增对输入性质声明的认证。

## 使用方式

普通活动规划：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow plan --catalog catalog.json --request request.json --input-profile minimal --data-origin synthetic_simulation --verify --output generated/task-flow/minimal-plan.json
```

之后可用相同精简原文件及相同参数独立重放：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow verify --catalog catalog.json --request request.json --input-profile minimal --data-origin synthetic_simulation --report generated/task-flow/minimal-plan.json
```

单装备跨流程联合选网：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.design_bridge --jobs jobs.json --scene scene.json --mapping mapping.json --mode joint_candidate_optimization --input-profile minimal --data-origin synthetic_simulation --output-dir generated/task-flow/minimal-network
```

同环节多装备可行性组网：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.team_feasibility --jobs jobs.json --scene scene.json --mapping mapping.json --config config.json --input-profile minimal --data-origin synthetic_simulation --output-dir generated/task-flow/minimal-teams
```

装备组指标与优化（仅评价1.1）：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.team_optimization --source-report generated/task-flow/minimal-teams/team_report.json --metrics metrics.json --input-profile minimal --data-origin synthetic_simulation --output-dir generated/task-flow/minimal-team-optimization
```

单流程命令在结果的`input_profile`中保存元数据；组网命令另外写`input_profile.json`，原业务报告格式不变。已有输出不覆盖。仍可不加新参数使用旧严格入口；严格模式不会自动回退到精简模式。

API对应`task_flow.input_profile.normalize_problem`、`normalize_design`和`normalize_metrics`，分别返回规范化输入及元数据报告，然后调用原有核心API。API调用者仍需执行原核心验证，规范化函数本身不宣称输入业务正确。

## 本轮验收

152项测试通过，其中精简输入新增18项，既有回归134项；另有6组独立进程CLI演练。缺来源的单装备案例生成18条4/5/6环节候选链，经真实OWL、独立核验及RDF检查；多装备四环节案例生成28条链、16个可行网络，显式合成质量优化得分0.6，独立核验通过。仍仅是受控合成的软件验收。

完整命令、原始精简输入、规范化报告、运行日志与摘要见[验收证据](planning-input-profile-evidence/verification.json)。本轮没有修改核心Schema、原计算公式或正式观测契约。
