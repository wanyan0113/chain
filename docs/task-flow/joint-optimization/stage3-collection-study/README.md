# 阶段三软件闭环：绑定采集包、冻结研究、分析实际记录

数据要求先看[第三阶段需要什么输入与规则](data-requirements.md)。用户尚未取得真实观测；本轮只完成软件闭环，**模型真实领域有效性继续pending**。软件会计算模型，不代表模型已符合实际；生产精度、排序与RDF保持原样。

之前的通用观测分析器可独立用于分类研究，但不会强制核对一个具体组网来源。对已经绑定某条候选链的采集包，现在增加collection_study统一入口：每次冻结和分析都先复核源报告、候选身份、阶段顺序与法器分配，防止采集包检查与统计分析被无意分开。

## 用法

按[导出说明](../stage3-design-collection/README.md)取得工作采集包。填写protocol.json中的真实标签定义、来源、总体、法器版本、标签方法、抽样及容差。协议准备齐全后冻结：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.collection_study freeze --pack generated/task-flow/my-collection --lock generated/task-flow/my-collection-lock.json
```

之后在observations.csv中录入真实校准和评估观测，再运行：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.collection_study analyze --pack generated/task-flow/my-collection --lock generated/task-flow/my-collection-lock.json --output-dir generated/task-flow/my-study-result
```

空包、未定义标签或真实版本仍未填写时不允许冻结。已冻结可用协议但尚未录入观测时，分析报告为blocked/no_observations，不会凭规划输入中的accuracy生成实测值。prospective与retrospective_analysis规则沿用原采集接口。

## 绑定内容与输出

研究锁包含原协议锁、精确源报告/绑定/包清单摘要、来源证明状态及网关源码摘要。collection_plan_id标识源设计选择；研究锁另绑定这次具体来源字节，不能把另一次报告自动换进已冻结研究。

工作包先复制到一次临时字节快照中，源验证、工作协议验证和CSV分析全部针对同一快照。分析输出留存该快照、研究锁及：

- collection_study_report.json：每个统计结果追溯到task_id、source_flow_id、candidate_id和实际协议中的环节/法器版本。
- observation_report.json：通过来源和协议检查后才生成的原始标签统计；数据不足时保留阻塞说明。

修改冻结容差或其他研究定义、换来源包、修改版本/分配、缺失环节或代码摘要变化时阻塞。分配篡改等包完整性错误会直接拒绝且不生成结果目录；锁或数据条件不满足时保存blocked报告，统计结论为null。

统计数据可分析、乘积在容差内等效、真实领域有效性通过是三个不同结论。即使统计可分析或通过局部容差，domain_validity仍pending，不改写原joint_report、法器accuracy属性、原评分或候选网络。条件率和恢复次数仅为诊断，不自动改成新的评价政策。

## 验收记录

52项相关测试通过，其中7项新增闭环测试；另有3组实际CLI验收：冻结后空数据阻塞、四环节来源绑定与合成标签格式分析、冻结后改容差阻塞。[汇总](evidence/verification.json)和[测试日志](evidence/regression-tests.log)归档。

端到端演练使用显式标注的synthetic_fixture、2025年固定示意记录时间及retrospective_analysis，只检查软件通路，没有真实标签来源或领域结论。完整演练文件压缩在[workflow-fixture.zip](evidence/workflow-fixture.zip)，不能充当采集研究结果。

复现软件演练：

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_collection_study tests.test_design_observation_bridge tests.test_accuracy_observations tests.test_accuracy_validation tests.test_joint_candidates
.\.venv\Scripts\python.exe -B -m workflow_tools.verify_collection_study --output-dir generated/task-flow/my-study-verification
```

源码和归档摘要见[manifest.json](manifest.json)。旧实验与接口文件没有回写；当前阶段的缺项仍是具体领域事件定义、真实标签和逐样本实际观测，而非继续堆叠推理规则。
