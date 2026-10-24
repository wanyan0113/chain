# 维修流程协同验证

从仓库根目录运行：

```powershell
python -m maintenance_demo.collaboration.run_collaboration
```

入口先调用两级映射，再验证维修报告交接、任务/流程依赖、固定时间窗口、共享资源容量和维修耗材库存。默认生成 3 个流程、4 个参与记录和 4 个组合关系，写入 `generated/`。

主要任务代码：`run_collaboration.py` 和 `handoff_rules.rq`；事实与词汇分别在 `input.ttl`、`schema.ttl`。详细规则和边界见[阶段说明](../../../docs/第二部分2.1.2与2.1.3流程协同验证.md)。

统计入口：`python -m maintenance_demo.collaboration.summarize_workflows`。该入口先验证计划，再输出历时、工时、资源占用和耗材余量，详见[指标口径](../../../docs/维修流程统计与指标口径.md)。
