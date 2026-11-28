# 四环节能力互补合成案例

完整输入包含两个任务、活动库/请求、资源场景、新模式映射和显式时间配置。无预写活动顺序。详见 [第一阶段说明](../../../docs/task-flow/complementary-team-phase1.md)。

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.team_demo --output-dir generated/task-flow/my-team-run
```

预期：两个任务均为四环节；4条逻辑流程，28条候选链，16个资源可行网络；独立核验通过，不评分、不宣称最优。`O-Phase-Tuner`与`O-Safety-Auditor`互补，`O-Combined`可单独满足该环节。时间为显式合成实验输入，非设备实测；新场景明确不启用尚未扩展到装备组的表10目标负载政策，旧案例不变。
