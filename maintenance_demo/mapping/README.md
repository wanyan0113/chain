# 维修两级映射验证

在仓库根目录安装 `requirements.txt` 中的依赖后执行：

```powershell
python -m maintenance_demo.mapping.run_mapping
python -m unittest discover -s tests -v
```

先由任务属性推导所需能力，再筛选单个满足全部能力、可用且响应不超限的资源。默认输入包含 3 个任务和 5 个资源；预期生成 4 条能力需求与 3 条候选关系。阶段结果及解释报告写入 `generated/`。

详细规则、文件说明和边界见[2.1.1 映射规则验证](../../../docs/第二部分2.1.1映射规则验证.md)。
