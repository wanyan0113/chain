# 第四阶段：独立流程重放与验证

依据[任务书](../../../扩展链任务书.txt)第四阶段，实现[独立重放器](../../../task_flow/validator.py)。从外部指定的活动目录、任务请求和候选序列出发，重新计算状态变化，不信任生成器的“通过”标记或声称的最终状态。

## 使用方式

```powershell
# 生成候选后立即进行独立验证，输出新报告
.\.venv\Scripts\python.exe -B -m task_flow plan --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json --verify --output generated/task-flow/verified-plan-1.json

# 重新验证已保存报告中的候选，不重新运行规划器
.\.venv\Scripts\python.exe -B -m task_flow verify --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json --report generated/task-flow/verified-plan-1.json

# 验证独立序列文件：此文件是手工输入例子
.\.venv\Scripts\python.exe -B -m task_flow verify --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json --sequence task_flow/examples/document_workflow/sequence-basic.json
```

verify退出码：0为本次请求验证全部通过；1为拒绝、未知阻塞、验证超限或没有候选可验证；2为目录/任务输入无效、必要知识不完整、命令选项或文件错误。--output始终拒绝覆盖。

外部目录、请求与序列/报告中的摘要必须一致。若规划时用--mode覆盖原请求，报告绑定的是覆盖后的有效请求；重新验证时应提供该有效请求，不自动相信报告内部快照。输入快照也与外部输入摘要核对。

## 独立性与检查内容

验证器共享第二阶段Schema、JSON解析和输入合法性检查，但不调用state.py或planner.py的状态执行、条件判断、目标判断或搜索函数。它直接读取原始目录/请求，重新实例化事实和规则，以落地依赖图重新排序派生规则，并采用自己的三值条件计算及原子效果执行。

独立检查包括：

1. 目录、请求摘要，活动版本、参数绑定、长度和累计次数。
2. 初始禁止状态；每步适用条件、前置条件。
3. 所有效果从执行前状态取值，先计算后提交，数值类型/界限及重复写入。
4. 每步后的派生事实重算和禁止条件；失败保留上一合法状态。
5. 最终目标与必须发生的活动；中途达标不等于最终通过。
6. 候选ID、声称的最终状态及能力需求是否与独立重放一致。

每步输出执行前状态、条件表达式与实际值证据、效果定义与拟议值、执行后状态、状态差异、派生规则及来源、提交状态和失败原因。步骤索引从0开始。输入结构错误能定位到具体steps索引时也保留索引；目录/请求或候选头部错误不归咎于某个执行步骤。

执行后触发禁止条件时，trace.after_state保存被拒绝的拟议状态，committed=false，报告final_state仍是上一合法状态。这两者不能混为实际已经提交的状态。

## 输出与预算

单序列报告版本为task-flow-verification-1.0。verification_status有passed、rejected、blocked_unknown、limit_reached；independent_verification_executed区分是否真正开始语义重放。只通过结构检查或因预算拒绝执行时，不标记语义验证已执行。

批量报告版本为task-flow-verification-batch-1.0，分别列出每个候选报告、状态数量和处理覆盖范围。默认最多验证1000个候选；每个序列的状态记录预算为100000，可通过--verification-max-candidates和--verification-max-trace-records调整。状态记录预算按 `(2*步骤数+2)*落地事实数` 估计，不是严格字节/时间上限。触限保留已检查结果并标记partial，不能把未验证候选当作已通过。

批量coverage只指候选验证尝试范围，不证明搜索覆盖、最短性、评分最优性或资源可行性。空候选报告为not_run，不把“没有候选”当成“所有流程均已验证”。候选中已有verification_status或external_status声明不作为证据；验证器始终重新计算逻辑结论，external_status保持not_run。

plan --verify将本次实际验证状态写回对应候选，并附完整独立报告；未处理的候选仍为not_run。普通plan继续保留第三阶段行为，不强制开启验证。

读取搜索报告时允许统计元数据中的有限浮点值，严格目录、请求和序列的整数政策不变；非有限值、重复JSON键和结构超限仍拒绝。规范候选状态列表与能力声明按原协议核对，Python中true与1相等不会掩盖类型篡改。

## 本轮证据与边界

[八场景结果](example_results.json)中81个已生成候选全部通过独立逻辑重放；两个无候选场景保持not_run。[完整三步验证轨迹](verified-example.json)由实际验证器生成，测试会检查它与[手工输入序列](../../../task_flow/examples/document_workflow/sequence-basic.json)重新运行一致。

错误实验覆盖交换步骤、遗漏步骤、错误参数/版本、禁用及重复活动、目标被破坏、未知条件、非法数值效果，以及篡改候选最终状态、能力、ID和通过标记。对长度0～3的基础实例，156个原始序列逐一验证，接受集合与另一份独立穷举参考一致。

共享Schema及共同模型解释仍可能带来共同错误；独立实现和有限测试不是对所有输入或现实过程的正确性证明。验证只针对声明的逻辑模型，能力声明仍未接入旧装备筛选，尚不能把通用流程直接当成完整链网。当前总体状态见[阶段总览](../STATUS.md)，具体测试记录见[检查记录](review.md)。
