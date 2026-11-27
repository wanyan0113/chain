# 第二阶段：活动知识模型与状态模型

日期：2026-11-27。设计依据为[任务书](../../../扩展链任务书.txt)第二阶段及[第一阶段总体设计](../phase1/architecture.md)。本阶段新增独立 `task_flow/` 包，不修改旧本体、链网引擎、评分或FDNA流程。

## 已实现

- [正式JSON Schema](../../../task_flow/schema.json)：活动目录、任务请求和活动实例序列，版本 `task-flow-1.0`。
- [契约与有限实例化](../../../task_flow/contracts.py)：严格JSON、目录/请求摘要绑定、类型/对象域/引用检查、派生规则拓扑检查、实例化工作量上限。
- [生成侧状态语义](../../../task_flow/state.py)：真/假/未知、静态事实保护、派生重算、参数化活动、原子效果、整数单位及边界、禁止条件与目标检查。
- 命令行预检查 `python -m task_flow check`，不执行流程搜索。
- [文档业务活动库与8个请求](../../../task_flow/examples/document_workflow/catalog.json)，以及[整数状态示例](../../../task_flow/examples/counter_transfer/catalog.json)。人工见证不是自动生成结果。

执行示例：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow check --catalog task_flow/examples/document_workflow/catalog.json --request task_flow/examples/document_workflow/basic.json
.\.venv\Scripts\python.exe -B -m task_flow check --catalog task_flow/examples/counter_transfer/catalog.json --request task_flow/examples/counter_transfer/request.json
.\.venv\Scripts\python.exe -B -m unittest tests.test_task_flow_contracts tests.test_task_flow_state -v
```

预检查退出码：0表示契约合法且初始禁止条件得到明确排除；1表示契约合法但初始禁止条件违规或未知；2表示输入无效、必要结构知识不完整或文件读取失败。JSON结果用invalid_input和input_incomplete区分后两类输入问题。0不表示目标可达、装备可匹配或流程已生成。未知的活动前置条件不会使契约自动无效；未开展搜索就不报告“任务无解”。

## 数据契约

目录包括：有限对象域、事实声明、派生规则、活动定义和能力声明。任务请求绑定目录ID、版本及规范摘要，提供静态事实、初始可变事实、目标、必需活动、禁止条件、活动次数限制和搜索预算。所有协议对象拒绝额外字段。

事实身份为 `(predicate, arg1, ...)`，外部JSON为 `{"predicate":"collected","args":["doc-1"]}`。活动定义可用 `{"param":"document"}` 绑定参数；实例必须完整绑定全部参数，且取值来自指定对象域。不同对象类型之间不允许重用同一对象ID，以避免歧义。

活动实例序列包含目录摘要、请求摘要和有序steps。`validate_sequence`只检查结构、版本、参数、长度与活动次数，不判断每步前置条件、最终目标或外部可行性。它不是第四阶段的独立验证器。

活动次数上限按activity_id跨所有参数实例合计，任务限制只可收紧目录上限；0表示禁用。单步apply只拒绝显式禁用并检查状态语义，累计次数须由序列校验或后续搜索历史管理。

## 精确语义

| 对象 | 约定 |
| --- | --- |
| 布尔事实 | 只接受真正的bool或表示未知的null；缺失也为未知。false为明确否定 |
| 条件 | all、any、not及类型安全的eq/ne；整数另有lt/le/gt/ge。unknown在not下仍unknown；空all=true、空any=false |
| 静态事实 | 只能由静态输入或显式证据记录导入；活动不能写入，状态中不能删除或修改已知静态值 |
| 可变事实 | 活动set写入；forget使值变为unknown，不等于false |
| 派生事实 | 仅布尔；每个落地事实恰好一条规则；允许无循环依赖，拓扑重算；多规则析取可在when中用any表达 |
| 效果 | 全部读取执行前状态，先计算后一次提交；重复落地写入拒绝，即使写入值相同；失败不修改输入状态 |
| 禁止条件 | 初始及每次转换都检查；true拒绝，unknown为blocked_unknown |
| 目标 | 只按当前终点状态和必须发生的活动检查；不把中途达标当成最终成功 |

数值首版只支持整数，所有JSON整数限制在 `[-(2**53-1), 2**53-1]`，每个数值事实还须有单位和更具体的上下界。值赋值、比较阈值、读取加整数偏移必须同单位；比较阈值允许在事实范围之外。暂不支持浮点、连续参数、乘除、隐式换算或数值派生规则，遇到这些表达明确拒绝，不做舍入或猜测。

已知/未知区别不改变OWL的开放世界语义。`ontology_evidence`只接收显式登记的静态事实和来源/知识摘要/规则依据，并检查与静态输入是否冲突；它不运行HermiT、不读取来源文件来证明证据真实。第二阶段全部能力声明为unmapped，目录URI存在不等于主本体已经声明该能力；实际知识接口和映射确认归第五阶段。

## 有限性、身份与资源边界

实例化按活动ID、参数名和对象ID排序，零参数活动有一个实例。落地事实、落地规则和落地活动共用显式预算，默认总计10000，先算规模再分配。此限制不是第三阶段的搜索节点上限。结构递归深度、总JSON节点数亦有限制。

规范摘要使用UTF-8、NFC字符串、对象键排序、紧凑JSON；列表顺序有意义，不自动重排。拒绝非NFC和非法代理字符。原文件字节哈希与规范摘要不同；请求绑定的是规范目录摘要。JSON读取拒绝重复键和非有限值，内存输入同样拒绝float及非JSON值。

## 与后续阶段的衔接

第三阶段将消费Problem和有限实例，在独立的搜索状态中记录序列、发生次数和前沿，支持首解恢复及边界内枚举。不得只用事实状态去重。节点数定义为尝试扩展一个落地活动实例的次数，扩展前检查预算；长度0参与终点检查；时间使用单调时钟，超限不得声称穷尽。稳定实例顺序便于复跑，不承诺首解最短。

第四阶段独立重放器应共享Schema/解析定义，但不能调用state.py的条件判断或效果执行来证明同一实现正确。现有手工见证测试只用于本阶段状态组件验证。

第五阶段再加入能力映射、旧接口适配和反馈。当前包不生成旧设计报告，不导出FDNA边，不补占位性能数据；原有109项相关测试作为兼容回归基线。

检查结果见[第二阶段检查记录](review.md)。
