# 第三阶段：有界顺序流程生成

依据[任务书](../../../扩展链任务书.txt)第三阶段，实现有限活动实例上的流程搜索。前置数据契约和状态语义见[第二阶段](../phase2/README.md)。生产实现为[planner.py](../../../task_flow/planner.py)，不包含独立验证器、装备筛选适配或资源排程。

## 运行

```powershell
# 根据目标和初始状态寻找首个候选流程
.\.venv\Scripts\python.exe -B -m task_flow plan --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json

# 枚举声明长度边界内全部可证实流程，保存新报告文件
.\.venv\Scripts\python.exe -B -m task_flow plan --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json --mode enumerate_bounded --output generated/task-flow/run-1.json

# 限定工作量；超限不报告无解或完整覆盖
.\.venv\Scripts\python.exe -B -m task_flow plan --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json --max-nodes 1
```

原check入口继续只做输入预检查。plan退出码：0为找到候选且当前调用非超限终止；1为边界内无解、未知阻塞或工作量超限；2为输入无效、知识不完整或文件错误。候选只是生成阶段通过，不表示独立验证或外部分析通过。指定输出路径时拒绝覆盖，报告包括实际目录/请求快照及摘要。--mode会写入本次有效请求并重新计算请求摘要；其他运行预算覆盖项记录在limits中，不改任务逻辑。

## 搜索与恢复

使用迭代加深的显式栈深度优先搜索，按长度0、1、…、L逐层检查精确长度的序列，层内按活动ID与参数值的稳定顺序展开。因此优先检查短流程，不以航程、成本或资源性能最优为目标。没有写死三阶段、环节数或完整模板。

每条前缀保存基本状态、完整活动序列、各活动的累计次数和下一个实例索引。上限按activity_id跨参数实例合计；达到次数限制的扩展被排除。不能仅依据状态去重；状态相同但活动、参数、能力需求或历史不同的方案继续保留。达到目标后，后续长度层仍检查更长的合法序列。

进程内恢复API：

```python
from task_flow import SearchSession, prepare_problem, read_json

problem = prepare_problem(read_json('task_flow/examples/document_workflow/catalog.json'),
                          read_json('task_flow/examples/document_workflow/basic.json'))
session = SearchSession(problem)
first = session.run()     # first_feasible：返回一个新增候选后暂停
second = session.run()    # 从同一前沿继续；不是从头重复返回首个候选
# 若节点预算触限，可显式扩大累计预算继续：session.run(max_nodes=5000)
```

每份报告包含会话迄今全部候选，恢复时不修改先前候选。调用者可以在外部拒绝当前候选后继续搜索，但当前模块不登记资源反馈、也不把调用者的拒绝当成全局活动禁用；正式反馈协议在第五阶段实现。不同进程之间不能从报告恢复前沿；CLI每次是新会话。

## 边界、未知与统计

- max_length来自任务契约，不可在同一会话改变；完整枚举只针对这个长度和活动域。
- nodes统计尝试一个落地活动实例的次数，包括次数限制拒绝；扩展前检查累计预算。迭代加深会重新访问较短前缀，这些实际工作也计数。
- timeout_ms使用单调时钟累计各次run的有效搜索时间，调用者暂停时间不计入。输入编译、报告复制和JSON写出不在该计时内。检查是协作式的，单次转换/输出不具有硬实时中断保证。
- max_candidates默认10000，防止枚举结果无界积累；若下一个合法终点超出此上限，保留该前沿并报告超限。第一次run可设置更小预算，后续仅允许增加预算，不能清零已用工作量。
- 未知前置条件、未知效果读取或未知禁止条件阻塞该分支；其他已证实分支继续。未知目标不作为成功，仍可扩展当前合法前缀以尝试消除未知。
- 已知不满足的条件优先确定拒绝，即使同时有别的条件未知；明确禁止状态同样优先于其他未知禁止条件。

search_status与结论分别记录：

| search_status / conclusion | 含义 |
| --- | --- |
| candidate_found / candidates_available | 首解模式暂停；coverage=partial，尚未完整枚举 |
| limit_reached / undetermined | 搜索未完成、尚无候选；不能称为无解 |
| limit_reached / candidates_available | 搜索未完成，但保留此前候选；不能称为全部方案 |
| exhausted / candidates_available | 声明边界内已证实的流程集合枚举完成 |
| exhausted / bounded_no_solution | 搜索穷尽且没有未知阻塞；仅在声明模型和边界内无解 |
| exhausted / blocked_unknown | 没有已证实方案，但存在未知阻塞；不能断言实际任务不可行 |

coverage=complete仅指 `certified_executable_sequences_within_declared_bounds`。未知被阻塞的潜在执行不在已证实集合中；报告另保留unknown_conditions_seen与最多30条样例。未知计数是实际检查次数，不是唯一未知事实或潜在方案数。报告中的生成目标检查为true，不会将verification_status或external_status设为passed。

## 小规模对照结果

[归档结果](example_results.json)来自实际运行，并与[独立穷举参考](../../../tests/task_flow_oracle.py)逐个序列比较。参考实现不调用生产实例化、状态转换、条件判断或搜索函数；它从原始输入构造有限实例，穷举所有序列并从初始状态逐条重放，仅作测试参考，不是第四阶段的生产验证器。

| 输入场景 | 首个候选长度 | 边界内已证实流程数 | 结果 |
| --- | ---: | ---: | --- |
| basic | 3 | 6 | 集合与穷举一致 |
| verified | 4 | 2 | 集合与穷举一致 |
| collected | 2 | 32 | 集合与穷举一致 |
| already_done | 0 | 21 | 集合与穷举一致，包含空流程及合法延长 |
| mandatory_verify | 1 | 14 | 保留必需活动要求 |
| too_short | 无 | 0 | 长度2内无解 |
| unknown_source | 无 | 0 | 未知条件阻塞 |
| feedback_fallback | 3 | 6 | 可恢复获取另一候选，尚不执行资源反馈 |

较多流程来自合法重复前缀和目标达成后的合法延长，不代表都必须执行或同样经济。首解优先短序列，完整枚举保留全部声明允许的序列。

另有固定种子20261127生成的24个小布尔模型、两对象参数化模型和整数模型的精确集合对照。所有结论限定于这些测试实例和长度边界，不是任意规模完备性的经验性证明。测试和阶段状态见[检查记录](review.md)。下一阶段实现独立重放验证器。
