# 星穹观测站：由目标搜索4/5/6环节

虚构场景：观测站同时处理北侧、东侧、核心三处能量裂隙。基础处置需要探知、解析、决策和稳定；东侧还需安全复核；核心还需相位校准。任务仅给出初始事实、需求标志和最终stable=true目标。

目录包含完整活动库、请求、能力映射、法器属性、连接及资源约束。没有sequences_file或sequences_inline输入，不提供执行顺序。活动库定义每个动作的前置条件和效果，task_flow负责搜索顺序；各任务max_length均为7，不靠分别设置4/5/6的上限决定结果。

- [jobs.json](jobs.json)：3任务文件清单。
- [scene.json](scene.json)：所有法器属性、实际本体类型、每活动指标、容量/库存、上下文和有向连接。
- [mapping.json](mapping.json)：活动到observe/decide/act、角色及关系的映射，不是流程顺序。
- catalog-1/2/3.json：可用活动与状态转换，包含主/备用解析及不可执行的干扰动作。
- request-1/2/3.json：不同目标参数和needs_tune/needs_audit事实。
- 本体补充：[observatory-multistage.ttl](../../../knowledge/observatory-multistage.ttl)，新增校准与安全复核能力及法器类。

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observatory_demo --output-dir generated/task-flow/my-observatory-demo
```

只运行规划、真实OWL推理与链网：

```powershell
.\.venv\Scripts\python.exe -B -m task_flow.design_bridge --mode joint_candidate_optimization --jobs task_flow/examples/observatory_multistage/jobs.json --scene task_flow/examples/observatory_multistage/scene.json --mapping task_flow/examples/observatory_multistage/mapping.json --output-dir generated/task-flow/my-observatory-network
```

现有design后端接收的是自动编译后的序列表示，其历史字段名为templates；这些字段由搜索结果生成，不是本例预写的输入路线。没有修改原始评价公式，预测精度仍为sequential_product_v2。
