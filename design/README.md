# 六步规则设计模块

新增可独立运行的能力推理（步骤2）：`python -m design infer-capabilities design/examples/capability_demo.json --output-dir generated/capability_demo`。只需任务与规则，不要求法器、评分或资源数据；输出逐条件解释、能力集合、阻塞报告和完整结果的RDF。该入口与完整设计共用能力推理逻辑，详见 [能力推理说明](能力推理说明.md)。

按用户2026-10-27提供的第一、二部分截图，实现2.3的六步流程。引擎使用显式条件匹配和有上限的穷举搜索，复用主本体导出业务实例。原来源层、环境层和本体定义继续独立工作。

```powershell
.\.venv\Scripts\python.exe -B -m design run design/examples/technical_demo.json --output-dir generated/design_demo
.\.venv\Scripts\python.exe -B -m design audit-case --output-dir generated/design_case_audit
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

`run` 输出 `design_report.json`、`design_summary.md`；只有完整设计才额外输出 `design.ttl`。导出前将实例与主本体合并，运行既有静态检查，错误和警告都阻止写入。目录内已有输出默认拒绝覆盖，显式使用 `--overwrite` 才替换。若目录存在旧成功方案而本次被阻塞，要求使用新目录，避免旧RDF被误当成本次结果。独立文件原子替换，不承诺多文件事务。标准输出JSON；0完整成功、1数据不足或无可行方案、2无效输入／搜索上限／读取写入错误。CLI参数错误由argparse输出到stderr并退出2。

Python入口为 `from design import design; result = design(document)`。不修改输入，不联网，不执行输入中的Python或SPARQL。

## 从能力到完整链网的可运行示例

```powershell
.\.venv\Scripts\python.exe -B -m design run design/examples/canonical_network_demo.json --output-dir generated/canonical_network_demo --require-canonical
```

该输入是虚构工程配置，不是文章案例。它复用原技术示例的两个并发任务、共享探知与协调资源、两个具现资源；任务需求和法器筛选规则使用规范能力名称。每个任务有2条候选链，网络共检查4组组合，其中3组可行；两个任务都选一次性快速资源的组合被容量和库存约束排除。最终选择一条快链和一条慢链，形成包含两条并行链、共享探知和协调资源的网络。

`design_summary.md` 列出所有候选链、选中链的闭合路径、网络搜索数量和资源冲突；JSON保留完整条件判断及来源。`design.ttl` 保存选中网络、链、有序节点、法器与规范能力关系。规范URI同时用于任务 `requiresCapability` 和选中法器 `hasCapability`；任务、链、法器的每项能力证据分别保存，不因共用能力而混在一起。

完整设计和独立能力入口现在共用身份登记及本体检查。完整设计报告为每个任务提供 `ontology_bindings`、`ontology_mapping`，顶层提供汇总和 `canonical_export_ready`。该就绪标记同时要求设计 `complete` 和能力映射完整，不能仅因部分已推导能力都映射成功而宣称网络可导出。

`run --require-canonical` 未就绪时保存JSON及Markdown，退出1，不输出RDF；已有旧RDF时要求新目录，退出2并保留旧结果。默认模式继续允许明确标记为未映射的局部能力个体。Python对应 `design_to_rdf(document, report, require_canonical=True)`。默认和严格模式都会针对当前输入重新执行设计，比较完整推理结果、本体映射和输入摘要，拒绝旧报告或修改过的条件、来源证据、评分及网络结果。重新验证会再次执行有上限的网络搜索，导出有相应计算开销；这仍不能验证外部来源是否真实。

完整设计的 `input_sha256` 现在表示排序键后规范JSON内容的摘要，涵盖全部输入，包括未参与条件匹配的来源字段。CLI另用 `input_file_sha256` 保存输入文件原始字节摘要。`input_file_sha256` 和导出后的 `rdf_validation` 属于文件／检查附注，Python导出不将它们作为推理证据验证或写入RDF；RDF的 `reportPayload` 仅保存与当前输入重新核对一致的核心报告。旧报告需重新运行设计生成。

本次没有从文章疑点中补造规则或坐标，也没有把Excel通用规则页的任意结论自动当作可用于设计的能力。真实案例要走同一流程，仍需按 `schema.json` 提供确认的任务、法器属性、筛选规则、协同边、指标和资源约束。Excel到该设计契约的转换尚未接通。

## 对照截图的实现

| 截图步骤 | 当前行为 | 依据与边界 |
| --- | --- | --- |
| 1 任务实例构建 | 接收任务ID、目标ID、固定相对开始时间、任务属性、来源和阻塞疑点 | 不从坐标疑点自动推算位置，不将任务时间点当作持续时间 |
| 2 能力推理 | 任务规则条件合取成立后，输出该角色所需能力，保留逐条件解释 | 式1；多条命中规则的不同能力都必须满足 |
| 3 法器推理 | 按角色、可用性、全部能力和目标类型筛选 | 式2；同一能力的多条规则为替代方案，每条规则内部取合取 |
| 4 链设计 | 枚举探知—协调—具现三角色；核验定向传讯、严格距离上界和协调→具现统筹边 | 式3和步骤4；目标→探知→协调→具现→同一目标形成闭合记录 |
| 5 链评估 | 时间相加、精度相乘、成本相加；每个任务的完整候选集合归一化后加权 | 式4—10；协调节点汇总O2研判和D决断两阶段，不能再重复相加 |
| 6 网设计 | 为每个任务选一条链，完整搜索兼容组合；检查并发容量、库存、冷却、资源组及前后依赖 | 2.1.3；保留并行、串行、分支和资源共享关系及资源冲突统计 |

**原文更正**：截图2.1.1的“目标距离较近，需要远程能力”由用户在2026-10-27明确更正为“目标近，需要近程能力”。演示的 `near-needs-near` 规则采用更正后的语义。演示中 `distance_class=near` 是显式输入分类；数值分界并非文章给定，也没有在真实案例中预设。实际项目可以用带单位的数值条件替代分类条件。

## 输入契约

完整机器契约为 [schema.json](schema.json)，可运行模板为 [technical_demo.json](examples/technical_demo.json)。模板中的距离、时间、精度、成本、容量和库存均为虚构技术数据，不能复制为文章事实。

- `tasks`：任务ID、`target`、`start_s`、`attributes`、`source_refs`、`blocking_issue_ids`。每条任务有三类能力要求。`attributes.target_type` 为目标类型字符串。
- `artifacts`：三类角色 `insight`、`coordination`、`manifestation`；显式 `available`、属性、`metrics`、并发 `capacity`、`consumable`、`inventory`、`cooldown_s`。探知和具现法器的 `attributes.target_types` 为字符串数组，必须包含任务目标类型。
- `task_rules`：`when` 中全部条件成立时，为该角色推导 `capability`；只允许读取任务属性。每个任务都必须推出三个角色的至少一项能力。空条件明确表示无条件规则。
- `artifact_rules`：为指定能力检查法器属性、任务属性与任务—法器上下文；所有要求的能力必须分别得到一条完整成立的规则。需把项目关心的目标高度、速度、距离、激发高度、状态等要求逐项编码到规则中。引擎不会推测未配置的物理约束。
- `contexts`：每个任务和法器至多一条，提供已确认的目标距离等任务相关量及其来源。不能用同一个任务距离代替不同法器各自到目标的距离。
- `links`：定向 `from`／`to`，显式 `communicates`／`controls`、`distance`、`max_distance`、来源及阻塞疑点。探知→协调和协调→具现两段必须存在、可传讯，且 `distance < max_distance`；后一段还必须 `controls=true`。反向边不自动补全，缺失边视为未知，显式false表示不成立。
- `dependencies`：`before`／`after`／`gap_s` 表示完成—开始前后依赖，依赖必须无环。已有任务固定开始时间，不自动移动。
- `resource_groups`：共同平台、通道等资源组ID、包含的法器ID及容量。组内每个被选法器占一个容量；需要按任务仅计一次的平台占用等更复杂政策时须扩展模型，不能将这里的容量误解为任意资源语义。
- `weights`：时间、精度、成本权重均在[0,1]且和为1。
- `policies`：成本单位、零极差时的相同归一化分数、每任务链组合上限、全网组合上限均需显式给出。任一上限超出时整次搜索失败，不截取前若干候选后声称最优。

每个对象的 `source_refs` 保存来源说明或引用，不自动验证外部真实性，允许显式空数组。`blocking_issue_ids` 非空时相关事实不能用于通过验收。业务规则由输入者明确确认；“字段填写完整”不等于文章事实已核对。

属性中的数值必须写成 `{"value": 5, "unit": "nmi"}`，不允许裸数。支持的条件为 `eq`、`contains`、`lt`、`le`、`gt`、`ge`；左值 `field` 和可选右值 `ref` 只能访问 `task.xxx`、`artifact.xxx`、`context.xxx`，另一种右值为字面量 `value`。不支持任意表达式或嵌套字段访问。数值比较要求单位字符串完全相同；百分比输入必须显式转换为指标所用的[0,1]精度后提供，转换依据记入来源。

`metrics` 为完整对象或null：正的 `duration_s`、[0,1]的 `accuracy`、非负带单位 `cost`。所有成本单位必须与 `policies.cost_unit` 一致。时间以秒、开始时间以相同计划原点的秒表示。这里只支持三阶段依次执行、不重叠，指标口径由输入者负责；精度乘积是文章指标公式，不据此宣称真实联合成功概率。

## 最优性的范围与数据不足

先在同一任务全部通过约束的链集合中按式7—10归一化；`max=min` 时使用调用者显式配置的 `equal_metric_score`，此政策来自工程约定而非文章结论。多个任务的网络目标是**各任务链评分的算术平均值最大**；这是本实现声明的网络选择政策，文章未给出网络总评分公式。同分按确定的链ID顺序选取。网络不重复归一化、不把不同任务的候选集混在一起。

逐任务最优链可能因一次性资源或共享容量而冲突；因此全网搜索保留全部可行候选链，需要时换用某任务的次优链。输出中的 `network_search` 包含评估组合数、可行组合数和按资源汇总的拒绝原因。

每条链为每个法器保留整个 `[start_s,end_s)` 区间，再延长该法器的 `cooldown_s`。这比精确的阶段占用更保守，不是阶段级最优调度。连续区间端点相接允许复用，冷却未结束则不允许。一次性资源每执行一条链消耗一份，库存跨全部任务累计，不能因任务先后执行而恢复。成本按每次执行求和，共享法器的使用费会分别计入，不将网络成本解释为采购去重成本。

有缺失条件、单位不符、未决规则／资源疑点、潜在候选协同未知或任何可行候选缺少指标时，结果 `blocked` 且无选中网络；不能排除未知候选后仍宣称最优。所有事实明确但无链或无兼容网络时为 `no_feasible_design`。`complete` 只表示给定输入、规则、固定时间计划和声明目标函数下的完整搜索通过，不证明规则集合穷尽了所有真实约束。

## RDF与实际案例

选中链使用现有 `SpellChain`、三个角色节点、`nodeOrder`、`nextNode`、`nodeArtifact`、`chainTarget`；共享法器仍在不同链中使用各自的节点。任务、能力、法器、网络及关系均显式声明类型。实例IRI包含完整规范化输入摘要，避免不同配置导出后合并时同名链节点互相覆盖。完整输入与推理报告写到独立 `urn:arcane-design:` 元数据命名空间，不向核心本体随意添加字段。分支在核心RDF中拆成多条单终点有向关系，完整分支列表保留在报告中。

`audit-case` 复用来源导入与校验，输出当前13项疑点、3项缺口以及待补输入，退出1。目前案例缺少确认坐标、协调法器完整指标、协同边、资源库存等，不能自动产生可信完整网络。原始案例没有被修改，技术演示结果也不反填案例。

当前未实现自动坐标纠错、环境作用计算、移动目标轨迹、随机过程、失败重试和动态重规划。现有时间区间及容量检查是确定性的静态执行计划检查；动态仿真还需要额外的状态转移和环境模型。

## 单条文章案例链复现

用户新增的第三部分截图提供了表10的具体规则及表13的4组方案评价值。可以先执行：

```powershell
.\.venv\Scripts\python.exe -B -m design reproduce-case --output-dir generated/article_reference_chain
```

输入存于 `data/article_design/reference_evaluation.json`，带截图文件名和未决问题。复用现有评分函数，按表13的4组数值及0.3／0.3／0.4权重复算，得到0.896154、0.300000、0.700000、0.092308，与原文三位小数结果吻合。胜出行对应正文明确标识的 `R-Eye-A（R-Golem）→联合统筹中枢（R-Golem）→R-Sigil-B（R-Gryphon#1）`。

本命令输出 `reference_chain.md`、`reference_report.json` 和 `reference_chain.ttl`，成功退出0，状态专用 `reference_reproduced`。时间按表13距离／速度计算，沿用来源单位n mile和kn得到小时后换成秒；不把方案级速度回填成法器速度，不以零补造探知或协调阶段耗时。成本保留文章明确的评价代理口径，不冒充真实货币成本。

这是文章给定候选数据的评分复现及方案结构导出。它没有运行完整的任务筛选与协同可行性验证；报告明确标注 `independent_feasibility=not_verified`。RDF使用现有链和节点类保存方案顺序，包含来源和未核验状态，不断言已验证的传讯、统筹关系。第3、4行80%精度与表12／表8的身份冲突继续保留，不给这两行强配法器。原有 `run` 的完整设计检查没有放宽。
