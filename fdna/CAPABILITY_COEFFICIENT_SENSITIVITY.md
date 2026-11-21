# 自身能力条件系数代表性敏感性

本批接续第17批R任务/环境项，冻结剩余代表性范围，并先执行三个接受节点的能力项。独立生成器能力乘数为`(1−h)+hC`。C<1时h增大使乘数降低，C=1时乘数不变；这不是在改变C的采样值，也不能称为“能力增益增强使能力减弱”。h控制的是条件作用函数的形状。

| 节点 | 冻结能力项 | 本批扰动 | 边界约定 |
| --- | --- | --- | --- |
| T | C，即h=1 | 0.10+0.90C | 预设单侧−0.10，不生成1.10后截断 |
| Q | 0.55+0.45C | 0.65+0.35C、0.45+0.55C | 内部±0.10 |
| R | C，即h=1 | 0.10+0.90C | 预设单侧−0.10 |

`generation_condition_sensitivity_plan.json`同时登记下一批T/Q任务增益和环境惩罚的8组预设范围；登记配置不等于已生成或已拟合。所有组都是单因素变化，能力/任务基础项与增益联动保持和为1，没有联合系数实验。

四个本批组沿用第10阶段的根输入、逐接受节点C/M/E、划分和观测创新，过程波动及交互关闭。每组10次数据重复、每次250个基础网络（200训练、50验证），观测σ=0/2/5/8为配对版本。基础式δ=0、固定δ=-5、自由δ[-10,10]，保持12个起点和全部求解设置；新增480组模型计算、1440次接受节点拟合，基线不重拟合。预测不裁剪。

## 影响范围与核验

改T允许T/Q/R干净状态变化；改Q允许Q/R变化；改R只允许R变化。`affected_nodes`按实际有向边求下游集合，而不是把所有节点都判为受影响。路径之外的节点值及其局部拟合参数、局部/整网指标须与来源一致。受影响节点即使生成配置未变，其实际输入改变后也允许重新拟合出不同参数。

`generation_coefficients.py`扩展`condition_groups`到已配置接受节点及capability_gain/task_gain/environment_penalty，保留此前权重、互补和R任务/环境报告的重放兼容性。`generation_capability_coefficient_report.py`按T/Q/R分别与同一冻结基线配对；边界节点只有两个水平，Q有三个水平，不能把面板中的基线重复计作独立实验。

本批保留逐重复误差、结构下界、分支、局部秩、不活跃参数、近等起点跨度、δ及越界记录。它们不替代后续完整有限扰动、参数补偿与供给质量诊断，也不构成参数全局唯一性证明。生成器h不是FDNA的α/β真值，跨组RMSE差不是单独的因果传播量。

## 重跑

输出目录须为空，冻结来源恢复到`generated/stage2-varying-conditions`：

```powershell
python -B -m fdna.generation_coefficient_experiment --config data/fdna/generation_capability_coefficients.json --fit-config data/fdna/generation_capability_coefficients_fit.json --delta-config data/fdna/generation_capability_coefficients_delta.json --reference-dir generated/stage2-varying-conditions --output-dir generated/stage2-capability-coefficients
python -B -m fdna.generation_capability_coefficient_report --experiment-dir generated/stage2-capability-coefficients --reference-dir generated/stage2-varying-conditions
```

`test_fdna_capability_coefficients.py`核验三个独立标量公式、归一化与边界、干净状态传播、不受影响节点、单调方向、非法参数、报告重放/篡改和小规模完整拟合及汇报。后续多输入恢复仍需分别设置可辨识恢复组与不可辨识诊断组，不要求所有参数在任意输入下唯一恢复。
