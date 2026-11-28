# 阶段三后续：真实观测采集与验收接口

用户确认目前没有逐样本真实数据，本轮因此交付采集模板与可运行验收接口。**真实领域数据仍未取得，模型领域有效性仍为pending。** 既有受控实验及manifest保持原样，不新增评分政策，不修改原始精度连乘或网络优化。

开始填写：[协议模板](../../../../data/accuracy-observations/templates/protocol.json)、[空CSV模板](../../../../data/accuracy-observations/templates/observations.csv)、[人工复核记录](../../../../data/accuracy-observations/templates/review-record.json)。接口验收见[evidence/verification.json](evidence/verification.json)，其中所有非空记录均为明确标注的**合成软件测试夹具**。

## 支持与边界

接入口读取实际真实标签、逐环节输出、版本和采样信息，不要求自然观测里不存在的故障日志或影子探针。当前只支持各环节输出都能对照**同一二元目标标签**的分类研究；若探知、解析、决策输出的是不同物理量或不同目标事件，应先制定领域映射，不直接把这些对象当作同一标签。

每条流程有独立的有序stage_instance_id列表。重复同一活动或同一法器仍有不同环节实例，保留各自校准率；不同流程即使使用相同环节编号也不合并。每个环节可以有不同数量的校准样本，但校准与评估必须声明相同目标总体，当前不做跨人群权重修正。

当前统计仅实现独立样本单位下的Wilson/Bonferroni包络。共享患者、目标、设备批次等导致多sample_id属于一个cluster_id时，现接口阻塞置信推断，不把行数当独立样本数；聚类重采样方法尚未实现。人口/独立性声明是否符合现实仍需人工审查。

## 采集顺序

1. 定义二元标签、真实标签取得方法、适用总体、难度分层，以及每个环节输出为何可与该标签比较。审核标注方法后填写ground_truth_method_review；这不是对尚未采集标签的预先认证。
2. 填写活动/法器身份和版本。相同活动多次出现时给不同stage_instance_id，按真实顺序排列，可从3步扩为4步或更多；不能用复制观测代替新增环节测量。
3. 选定独立抽样单位和cluster_id。按整个样本/关联簇划分calibration与evaluation，保持真实身份，不在两侧复用同一对象。选择适合任务的误差容差和样本量。
4. 完成协议并在采集前用freeze-lock冻结。data_origin是来源类别的自我声明；计划真实观测可填real_observational，但空数据仍被阻塞，不因该值宣称已经采集。初始空模板保持uncollected。
5. 单环节校准只进入calibration；同一评估样本贯穿各环节并进入evaluation。保留缺失、未执行和拒判，不用0或默认成功补全。
6. 保存原始CSV和锁文件，运行验收，再由人工结合来源、标签、分布和统计不确定性复核。人工表不作为自动升级领域结论的指令。

模板误差容差为null，必须研究者先选定；没有把上一轮受控筛查的5个百分点自动搬来。200个校准/评估样本、每分层类别50个样本是可修改的最低覆盖示例，不保证已有足够统计功效。区间过宽时返回inconclusive；可先做探索性试验，再为新的确认性留出数据冻结方案。

## CSV完整字段

每行是一名样本在一个环节的一次观测。编码UTF-8，可含BOM；列名与顺序必须与模板一致。

| 字段 | 填写含义 |
| --- | --- |
| observation_id | 本次环节观测的唯一ID |
| sample_id | 同一个真实样本跨环节保持一致；不同采样对象使用不同ID |
| cluster_id | 关联采样单位，跨拆分不得重用；独立样本通常等于sample_id |
| split | calibration或evaluation |
| flow_id | 与协议流程身份相同 |
| stage_instance_id | 该流程中的环节实例身份，与协议顺序一致 |
| activity_id / activity_version | 实际活动及版本，与协议匹配 |
| artifact_id / artifact_version | 实际法器/服务及版本，与协议匹配 |
| stratum | 协议预定义分层；不适用分层时统一all |
| gold_label | 独立真实标签，取协议label_space中的值 |
| input_label | 输入确实是可评分标签时填实际值；原始特征输入可空，不假装已知输入正确 |
| output_label | 实际输出标签；拒判、缺失、未执行按下面规则处理 |
| status | observed、abstained、missing或not_executed |
| label_source_id | 对应协议label_sources中已说明的真实标签来源 |
| recorded_at | 带时区的ISO8601测量记录时间，例如2026-11-28T16:00:00+08:00 |

observed要求真实标签与输出标签有效；abstained表示**已执行并明确拒判**，output_label为空，本接口按预先声明的全尝试正确率将其记为错误。missing与not_executed不进入已知成功/失败分母，整个研究统计判定被阻塞，保留计数；这与剔除缺失后宣称模型通过不同。

calibration_input_condition=correct_reference_label要求校准输入为真实参考标签，input_label必须等于gold_label；natural_case允许自然输入，不推造条件成功率。输入来自上一环节输出时将input_from_previous_output设为true，后续input_label必须与前序输出一致；上一环节拒判用保留值__ABSTAIN__衔接。没有这种直接标签传递时保留false。

输出“前序错误后本环节正确”的次数只是恢复现象；有真实input_label时另列输入正确/错误条件率，缺少该输入信息时返回null。它们不等于已证明该环节因果纠错，也不能从累计输出错误相关性证明局部故障独立。

## 命令

检查尚未填写的模板，预期退出码2并报告blocked：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.validate_accuracy_observations --protocol data/accuracy-observations/templates/protocol.json --observations data/accuracy-observations/templates/observations.csv --output-dir generated/task-flow/my-planned-data-check
```

将模板复制为自己的研究文件，完成来源、身份、标签方法、抽样、总体与容差，再分别冻结和分析。目录及锁文件必须尚不存在：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.validate_accuracy_observations --protocol path/to/my-protocol.json --freeze-lock path/to/my-protocol-lock.json
.\.venv\Scripts\python.exe -B -m workflow_tools.validate_accuracy_observations --protocol path/to/my-protocol.json --lock path/to/my-protocol-lock.json --observations path/to/my-observations.csv --output-dir generated/task-flow/my-observation-analysis
```

prospective模式检查记录时间不早于冻结时间；历史资料使用retrospective_analysis并保留原始时间，不称事前预注册。未来时间、无时区时间、协议/代码摘要变化、重复观测、样本泄漏、版本不匹配均会阻塞。摘要和自报时间只验证一致性，不证明来源或实际采集时点真实。

退出码0仅表示数据允许进行统计分析，**不表示乘积模型等效，更不表示领域有效性通过**；退出码2表示有阻塞。结构非法、文件错误等调用失败退出码1。报告独立保存，不更改业务joint_report或RDF。

## 验收与产物

17项接入口专项及已有精度/联合评价回归合计36项通过；另有11组实际CLI验收覆盖空模板、空观测、完整小夹具、缺标签、未执行、缺环节、缺校准、版本错误、簇泄漏、未冻结和冻结后改协议。软件夹具统计等效的专门测试仍必须保持domain_validity=pending。

CLI先读取CSV字节快照，再对同一份快照解析、计算摘要并保存observations_snapshot.csv，避免“分析了旧内容，摘要却对应被修改的新文件”。样本身份必须使用NFC且无首尾空白，防止常见的身份别名绕过拆分检查；它仍无法证明使用者提供的真实身份可信。

任何完整性或覆盖阻塞都会使本批各流程的statistical_assessment和置信区间为null，保留可观测计数及描述性率。此时完整样本子集的描述率不能当作全体无偏估计。多个有效流程分别评价，当前不提供跨流程全家族置信结论。

文件与源码摘要见[manifest.json](manifest.json)。生产评价公式与既有受控实验记录保持原样；真实采集和人工领域复核依旧待开展，不能凭本轮接口测试关闭整个阶段三。
