# 装备组质量语义与流程长度边界

本轮只补强第二阶段的评价契约与解释，不修改串行乘积公式、资源约束、候选身份或原单装备评价。装备组业务RDF与观测扩展仍未实施，领域有效性继续pending。

## 输入契约1.1

新输入为`task-flow-team-evaluation-1.1`，新优化报告为`task-flow-team-optimization-1.1`。数学政策仍为`team-fixed-interval-explicit-quality-1.0`，因为算术、归一化和目标函数都未改变。[当前Schema](../../task_flow/team_evaluation.schema.json)与[历史1.0 Schema](../../task_flow/team_evaluation.v1.schema.json)分别保存，历史报告和证据未回写。

原有任务、完整活动流程、环节、成员、能力分工、汇聚者绑定继续保留。每条`quality`额外要求：

| 字段 | 含义 |
| --- | --- |
| `semantics` | `quality_score`、`marginal_success_probability`、`conditional_success_probability`，或未填的`UNASSIGNED` |
| `definition` | 该环节组级数值评价什么输出、什么算正确或优良 |
| `evaluation_method` | 评价方法及其版本/引用标识 |
| `population` | 适用样本总体；不能将不同总体的数值直接当作同总体比较 |
| `comparison_scale_id` | 声明共同的数值口径、方向和量表 |
| `execution_conditions.context_hash` | 同时绑定完整来源输入与执行/知识/资源/时间上下文，包含任务初始事实、目标和活动定义 |
| `execution_conditions.description` | 说明实际适用条件、环境和限制；哈希不能替代这段说明 |
| `probability_basis` | 普通质量分数必须为null；概率声明需要成功事件、条件事件、依赖假设和证据引用 |

成员、分工、任务或执行条件变化后，不自动沿用旧值。系统只能核验声明与输入绑定，不能证明外部来源或适用条件在现实中为真；未声明的硬件/固件变化仍需要采集者补录，不能由快照哈希猜测。

同一任务的候选及环节不能混合数值语义、比较量表或样本总体。缺少语义、定义、方法、总体、量表、条件描述或概率依据时，整体阻塞评分；不丢掉缺数据候选，不补默认精度。已填写但相互冲突的口径明确拒绝。不同环节的方法可以不同，但须声明共同量表和总体，声明本身仍待领域审查。

## 概率解释

报告统一称为**串行乘积质量指标**。内部`accuracy`保留为评分器兼容字段，不因此赋予概率意义。报告显式记录：

```json
{
  "metric_label": "串行乘积质量指标",
  "actual_chain_success_probability_claimed": false,
  "normalized_scores_are_probabilities": false,
  "probability_interpretation": "not_validated",
  "cross_length_domain_validity": "pending"
}
```

普通质量分数不能附带概率解释。边际成功概率声明须注明依赖/独立性假设及证据；条件成功概率还须明确条件事件。即使这些字段完整，当前程序也只记录声明，不自动认证其真实性或把整链乘积升级为实际成功率。

若要进行概率解释，需额外确认任务成功是否等同所有环节成功、各值是否为相应共同总体下的条件成功概率；若直接相乘边际成功率，则需相应独立性依据。归一化不会建立这些条件。本轮没有实现概率领域验收或改变原评分公式。

## 增加复核的受控反例

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_quality_counterexample --output-dir generated/task-flow/my-quality-counterexample
```

使用三个一般质量分数0.9，再附加质量分数0.95的纠错环节：

| 情况 | 环节数 | 串行乘积质量指标 | 独立生成的合成终点正确数 |
| --- | --- | --- | --- |
| 原流程 | 3 | 0.729 | 900/1000 |
| 增加纠错 | 4 | 0.69255 | 980/1000 |

模拟机制显式假定：原来最后100个样本错误，诊断oracle能修复其中80个，不损伤原正确样本。输出保存1000条样本的独立参考标签、纠错前后输出及修复事件；终点正确率由这些记录计数，未用质量乘积生成。

这说明：**保持前面各因子不变时，附加[0,1]因子不能提高乘积，但有纠错机制的终点结果可以改善。** 不同长度流程不能据此声称真实成功率已公平比较。本例的质量分数是预设一般指标、纠错能力也是预设机制；它不是实测、不是概率校准，也不是实际装备能纠错的证据。

## 兼容与核验

旧1.0归档可继续按旧契约做独立复算，不伪造缺失的语义。新的优化调用若收到1.0指标文件，会报告`legacy_quality_semantics_require_explicit_v1_1_completion`并阻塞；需生成1.1骨架后明确补填，不自动把旧值解释成概率或改写旧文件。

新版报告的解释字段由独立验证器检查；把一般指标篡改为整链概率、篡改长度说明或降级格式以绕过新版语义都会失败。新版合成演示仍为28候选、196组合、16可行网络、最佳分数0.6；候选身份与原数值结果保持。

本次演示与反例的原始产物位于`generated/task-flow/team-quality-semantics/`，可审阅归档见[验收证据](team-quality-semantics-evidence/verification.json)。真实质量与纠错有效性仍需后续领域数据验证。

本轮20项装备组评价/语义测试与34项第一阶段及原候选/网络回归通过，共54项不同测试；另对条件绑定、概率声明和历史复核做3项针对性重跑。完整新版报告落盘后再次执行真实OWL与独立穷举核验，并与旧归档逐项比较候选身份、原始指标和网络结果，均保持一致。归档含新版完整报告、空语义模板、合成指标和1000条纠错样本，不包含测试控制台全文。
