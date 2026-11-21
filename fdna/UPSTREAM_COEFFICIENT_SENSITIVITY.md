# T/Q任务与环境系数代表性敏感性

本批执行`generation_condition_sensitivity_plan.json`在上一批已登记的八组单因素扰动，范围不按验证结果选择。

| 节点 | 因素 | 低/冻结/高 |
| --- | --- | --- |
| T | task_gain | 0.08 / 0.18 / 0.28 |
| T | environment_penalty | 0.18 / 0.28 / 0.38 |
| Q | task_gain | 0.12 / 0.22 / 0.32 |
| Q | environment_penalty | 0.25 / 0.35 / 0.45 |

任务基础项与增益联动，乘数为`(1−g)+gM`；M<1时g增大使乘数降低，M=1时不变。环境乘数为`1−eE`；E>0时e增大使乘数降低，E=0时不变。这是系数变化，不是改变M/E抽样；乘数单调性不决定重新拟合后RMSE的方向。

八个新组保持冻结根输入、逐节点C/M/E、划分及观测创新。10次独立数据重复，每次250个基础网络，200训练、50验证。观测σ=0/2/5/8是同一基础网络的配对版本。过程波动及交互关闭。基础式、固定δ=-5、自由δ[-10,10]沿用12个起点及原求解设置，共960组模型计算、2880次接受节点拟合；冻结基线不重拟合。

改T允许T/Q/R干净状态变化；改Q允许Q/R变化，T及根输入不变。所有接受节点按相同设置重新拟合，核验不受影响节点的参数和指标与基线一致。报告分别保留配对误差、同数据基础式局部结构下界、分支、局部秩、不活跃参数、近等起点跨度、δ及越界记录。下界不直接用于含δ或整网递推误差。

```powershell
python -B -m fdna.generation_coefficient_experiment --config data/fdna/generation_upstream_coefficients.json --fit-config data/fdna/generation_upstream_coefficients_fit.json --delta-config data/fdna/generation_upstream_coefficients_delta.json --reference-dir generated/stage2-varying-conditions --output-dir generated/stage2-upstream-coefficients
python -B -m fdna.generation_upstream_coefficient_report --experiment-dir generated/stage2-upstream-coefficients --reference-dir generated/stage2-varying-conditions
```

输出目录须为空。测试覆盖已冻结范围、独立标量公式、下游传播与路径外不变量、端点等式、联动基础项、篡改拒绝及小规模全流程。

本批是代表性单因素覆盖，不是全系数空间或联合敏感性。新组完整有限扰动、参数补偿、残差和输入质量诊断尚待开展；不能以旧基线诊断替代。生成系数不是FDNA参数真值。预测改善、参数可辨识性及输出范围分开报告，合成实验不证明真实任务有效性。
