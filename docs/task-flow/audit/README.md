# 前四阶段研究可信度专项审查

审查日期：2026-11-27；生产基线：`4ea1145`。本轮暂停第五阶段，不增加规划或资源功能，集中检查需求落实、语义正确性、有限范围完整性与复现证据。

## 从哪里看

1. [需求—实现—测试持续追踪表](traceability.md)：逐项区分已覆盖、部分覆盖和未实现。
2. [专项验收清单及实际结果](phase1-4/acceptance.md)：23组集中检查。
3. [完整机器记录](phase1-4/report.json)：环境、基线、源文件摘要、结果与未实施需求。
4. [审查结论与发现](review.md)：可支持的结论、修正项和不能外推的范围。

每组`cases/Cxx.json`保存计划输入、预期、实际、通过判据、耗时以及实际调用时的目录/请求/序列/候选。实际输入被按摘要去重保存，不仅记录随机种子；search_runs保存每次完整搜索报告，包括节点数、耗时、边界、候选和终止原因。C22还保存逐模型的完整参考与实际序列集合，便于检查遗漏或多报的具体序列。

## 复跑与校验

在仓库根目录、现有依赖环境下运行，选择全新的输出目录：

```powershell
# 旧顺序链回归夹具使用仓库tmp目录；新克隆先准备此临时目录。
New-Item -ItemType Directory -Force -Path tmp | Out-Null
.\.venv\Scripts\python.exe -B -m workflow_tools.task_flow_research_audit run --output-dir generated/task-flow-audit/new-review
.\.venv\Scripts\python.exe -B -m workflow_tools.task_flow_research_audit verify --output-dir generated/task-flow-audit/new-review

# 校验本次冻结记录
.\.venv\Scripts\python.exe -B -m workflow_tools.task_flow_research_audit verify --output-dir docs/task-flow/audit/phase1-4

# 审查工具自身的失败处理与证据完整性检查
.\.venv\Scripts\python.exe -B -m unittest tests.test_task_flow_research_audit
```

本次工具9项测试的实际输出另存于[工具测试日志](tool-tests.log)。

run拒绝覆盖。verify检查完整文件清单、每个产物摘要及当前源文件与审查版本的一致性；报告或源文件已改变时，不继续声称该记录对应当前代码。输出状态`passed_with_scope_limits`表示专项检查在声明范围内通过，不表示方法已普遍有效或外部条件已落实。

来源代码比较只将CRLF规范为LF，保留其他字节。证据产物按原始字节校验，并通过.gitattributes保留；manifest不包含自身，Git提交提供已发布清单的版本定位。sources目录是核对用的关键来源副本，不是包含全部旧设计器、FDNA及依赖的独立软件发行包；完整复跑需相应仓库与依赖。

## 本轮范围

- C01～C09：活动与能力结构、有限域、三值表、原子性、派生失效、版本、目标变化及空流程等。
- C10～C14：八场景集合对照、恢复、超限、序列身份与重复运行。
- C15～C18：错误流程、篡改候选、独立性隔离、主动生成器故障注入和逐步证据。
- C19～C21：外部结论边界、等价输入排列与来源身份、未知信息补全。
- C22：固定种子2026112704的32个混合布尔/整数模型，长度0～3，共1410个原始序列的规划器—重放器—独立穷举对照。
- C23：既有215项相关回归，完整日志保留在冻结记录中。

重复运行只要求非时间语义记录一致；耗时本身单独记录，不要求字节相同。时间触限采用可控单调时钟验证终止分类，不声称真实时间预算临界点下能得到完全相同的候选前缀。这里没有进行算法速度排名。

## 四层结论

| 层次 | 本轮证据支持什么 |
| --- | --- |
| 规划逻辑成立 | 所测有限模型、事实和边界内符合活动与目标规则 |
| 独立验证通过 | 独立执行语义重放及故障注入检查通过；仍共享输入契约与模型假设 |
| 外部条件可落实 | 尚未验收，能力声明仍未完成旧资源接口映射 |
| 系统集成有效 | 已测通用规划—验证CLI；完整旧能力/资源/反馈端到端链路尚未验收 |

本轮属于软件与证据层面的专项复核，不等同于第三方鉴定、研究新颖性认定或实际应用有效性证明。
