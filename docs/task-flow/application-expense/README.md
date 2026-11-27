# 第二业务案例：合成报销审批

本轮补充第六阶段的应用案例，复用现有规划、独立重放和服务反馈接口。新增原始业务数据适配器和独立业务规则检查器，没有改动生产规划算法、状态模型或旧系统模块。数据全部合成，案例由研究者设计，不是第三方验证、未见测试集或真实企业财务流程。

## 可复现入口

```powershell
# 将原始业务JSON转换成现有接口文件，选一个新目录
.\.venv\Scripts\python.exe -B -m workflow_tools.expense_evaluation prepare --input task_flow/examples/expense_workflow/sample.json --output-dir generated/task-flow/expense-input-1

# 直接使用已有集成CLI
.\.venv\Scripts\python.exe -B -m task_flow.integrate --catalog generated/task-flow/expense-input-1/catalog.json --request generated/task-flow/expense-input-1/request.json --mapping generated/task-flow/expense-input-1/mapping.json --context generated/task-flow/expense-input-1/context.json --output generated/task-flow/expense-result-1.json

# 另存完整案例与负例实验；原证据不覆盖
.\.venv\Scripts\python.exe -B -m workflow_tools.expense_evaluation run --output-dir generated/task-flow/expense-study-1
.\.venv\Scripts\python.exe -B -m workflow_tools.expense_evaluation verify --output-dir docs/task-flow/application-expense/evidence
.\.venv\Scripts\python.exe -B -m unittest tests.test_expense_workflow
```

所有操作均为本地数据转换或模拟分析，不执行支付、审批提交或远程调用。prepare输出catalog/request/mapping/context及input-trace；文件与目录拒绝覆盖。评估CLI退出码0为准备成功或声明范围内检查通过，1为实际结果不符合实验预期，2为输入或文件错误。评估通过包含正确识别失败场景，不能解读为所有场景均产生可用流程。

## 原始输入和适配

[样本](../../../task_flow/examples/expense_workflow/sample.json)、[15项固定案例](../../../task_flow/examples/expense_workflow/cases.json)与[适配器](../../../workflow_tools/expense_workflow.py)保存正式字段及校验。

| 字段 | 规定 |
| --- | --- |
| format_version、case_id、currency | expense-approval-1.0；稳定标识；仅CNY |
| claims | 1～2个报销单，claim_id唯一；金额为1～100000000的精确整数分，布尔及浮点数拒绝 |
| receipt_valid | true/false/null；null表示未知，不自动当成false |
| state | submitted、screened、manager_approved、authorized、archived；每项为true/false/null，必须显式提供 |
| goal | 非空且存在的claim_ids；require_archive明确为布尔 |
| search | 最大长度0～10、节点1～20000、时间1～30000ms；都是精确整数 |
| source_refs | 原始案例及每个报销单的非空来源列表 |

拒绝字段遗漏、额外字段、重复报销单、未知目标ID及已知相互矛盾的历史状态。状态中的未知信息保留，可能导致保守检查阻塞。一次模型最多12个落地活动实例，不涉及任意对象实例化。

映射保留原始输入摘要、每个报销单字段、对应谓词及来源；初始事实不作为真实执行结果，原始字段导入也不冒充OWL推理。能力URI、活动来源和服务映射来源沿用现有接口独立记录。

## 简化业务规则

正常活动包括提交、自动或人工审核、经理审批、记录授权和归档。新提交要求票据明确有效；审核要求已提交。金额不超过100000分时，审核后可记录授权；超过该阈值时，还需要经理审批。归档要求已经授权。

eligible为按当前状态重新计算的派生事实。低额/高额要求由金额和条件判断决定，不通过分别提供完整流程模板决定。自动、人工审核具有相同状态效果和不同能力需求，服务快照可使首候选不可用并触发恢复。

这里只记录授权状态，不处理资金流、税费、汇率、重复报销检测、身份权限或真实制度合规。1000元阈值是明确给定的合成规则，不代表普遍适用的财务政策。

## 观察结果

[验收摘要](evidence/summary.json)和同目录15个case文件包含原始输入、字段映射、有效规划输入、预期、实际、判据、搜索记录、逻辑重放、服务检查和独立业务检查。

| 场景 | 实际结果 |
| --- | --- |
| 低额只要求授权 | 3步 |
| 低额要求归档 | 4步 |
| 金额恰好100000分 | 4步，不增加经理审批 |
| 金额100001分 | 5步，包含经理审批 |
| 低额已审核 | 2步：授权、归档 |
| 高额已审核但未经理审批 | 3步：经理审批、授权、归档 |
| 两个已审核报销单，高额单已获经理审批 | 4步；参数和最终目标按两单分别保持 |
| 已归档且目标已满足 | 空流程 |
| 自动审核服务不可用 | 首候选外部拒绝，恢复到人工审核候选 |
| 票据状态未知 | blocked_unknown，未选流程 |
| 票据明确无效、长度不足 | 分别为声明边界内无解 |
| 节点预算不足 | search_limit；不能冒充无解 |
| 两种审核服务均不可用 | 逻辑候选仍成立，当前外部上下文无可用候选 |
| 两种审核服务状态未知 | undetermined；不能视作明确拒绝 |

15个案例均符合预先保存的预期，其中9个选出流程。共14个被实际尝试的候选经过独立业务检查，包括外部条件不满足但业务逻辑成立的候选。没有候选的场景明确记录业务检查执行数为0，不把零次检查解释成业务正确性通过率。

这些是有限验收见证，不报告为随机业务成功率。双单实验只检查对象隔离和顺序处理；服务可用性仍是全局声明快照，不能证明多任务资源共享、容量、排程或同环节多人协作。

## 独立业务检查与模型遗漏负例

[业务规则检查器](../../../tests/expense_policy_reference.py)直接读取原始报销单及活动实例，从自己的业务状态副本逐步检查规则；不读取生成模型中的前置条件、效果或派生事实，不调用生产执行函数。其功能是该固定规则集的独立实现，不是任意业务语义审查器。

[policy-mutation.json](evidence/policy-mutation.json)故意删除派生规则中的高额经理审批要求，然后以相同原始高额报销单重新规划。变异模型生成了4步候选，正式逻辑重放通过；独立业务检查在索引2（第三步，授权）报告high amount requires manager approval，按预期拒绝。

这说明相对于已给定的独立规则，能检测本例的模型遗漏。两套实现仍共享原始数据、简化阈值和领域假设，不能由此证明规则完整或现实制度正确。此处“负例实验通过”指成功观测到形式通过与业务拒绝的差异，不是变异流程可执行。业务检查器本轮只在案例评估程序中运行，尚未成为所有集成入口的强制检查。

## 验收范围和后续缺口

[测试日志](tests.log)保存本轮任务流程、独立验证、外部反馈、研究工具和新应用相关测试。新增12项应用测试覆盖阈值一分钱差异、参数混用拒绝、未知/假区分、来源、非法原始输入、服务替代、模型遗漏、独立性及现有CLI接入。

证据清单检查完整文件清单、来源摘要和有效案例摘要，检测意外改动；不防止同时伪造清单与数据，也不是外部鉴定。历史第五、第六阶段证据保持原样，本轮只增加适用范围证据，没有规划效率提升结论。

当前可确认的是通用原始数据→规划输入→候选生成→独立重放→声明服务检查→具体候选反馈的合成应用链路。旧能力/装备接口实际接入、旧系统端到端仍未实现，R10/R12未全量验收。真实非军事业务数据验证、领域规则人工审阅及更充分统计仍待开展。
