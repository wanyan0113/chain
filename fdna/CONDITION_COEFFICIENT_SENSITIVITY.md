# 末端条件函数系数敏感性

本批落实第四次修订补充版附录B及第13.3节中“生成系数敏感性”的一部分，只检查末端R的任务项与环境项。C/M/E样本继续沿用冻结变化条件组；改变的是生成公式系数，不是重新抽取条件，也不把条件加入FDNA输入。

冻结R生成公式为`(.62*Q+.38*D_aux)*C*(.80+.20*M)*(1-.40*E)`，SE单位保持0到100。本批预设四组，每次只改变一个自由因素：

| 因素 | 低值组 | 冻结基线 | 高值组 |
| --- | --- | --- | --- |
| 任务增益 | 0.90+0.10M | 0.80+0.20M | 0.70+0.30M |
| 环境惩罚 | 1−0.30E | 1−0.40E | 1−0.50E |

任务基础项与增益项之和为1，改变增益时基础项联动，属于一个自由度。M≤1时，增益越大反而使任务乘数越低，不能解释为“增益系数越大，最终效能一定越大”。环境惩罚越大，环境乘数越低。±0.10是本次预先指定的绝对扰动，Word未规定这个具体步长；它是实现补充，不是原文参数或置信区间。

## 数据和求解协议

根节点、逐接受节点C/M/E、训练/验证归属与观测创新均复用第10阶段。每组10次独立数据重复，每次250个基础网络、200训练和50验证；观测σ=0/2/5/8是配对版本。过程波动及交互关闭。基础式δ=0、固定δ=-5、自由δ[-10,10]；保留12个起点、L-BFGS-B与停止设置，不按验证误差选参数或改界限。新增480组模型整网计算、1440次接受节点拟合；基线不重拟合。

只改变末端R，因此T/Q的生成值、拟合参数及误差指标应保持一致。R的目标变化，拟合结果可以改变。所有生成系数仍与FDNA待估α/β分开；预测继续不裁剪。报告保留逐重复配对误差、分支、不活跃参数、局部秩、近等起点跨度、δ与越界条目。

## 入口和复跑

- `data/fdna/generation_condition_coefficients.json`定义四个预设组；对应`_fit.json`与`_delta.json`定义完整拟合选择。
- `generation_coefficients.py`增加`condition_groups`，仅接受R的`task_gain`或`environment_penalty`；任务基础项严格联动，非法值、混合类型、重复或未变化组被拒绝。
- `generation_coefficient_experiment.py`核验冻结来源后执行生成、拟合及δ对照；`generation_condition_coefficient_report.py`复用因子配对汇报，额外核验T/Q不变。
- `test_fdna_condition_coefficients.py`覆盖标量公式、单调方向、归一化约束、不变节点、配置/输出篡改以及小规模完整拟合与汇报。

```powershell
python -B -m fdna.generation_coefficient_experiment --config data/fdna/generation_condition_coefficients.json --fit-config data/fdna/generation_condition_coefficients_fit.json --delta-config data/fdna/generation_condition_coefficients_delta.json --reference-dir generated/stage2-varying-conditions --output-dir generated/stage2-condition-coefficients
python -B -m fdna.generation_condition_coefficient_report --experiment-dir generated/stage2-condition-coefficients --reference-dir generated/stage2-varying-conditions
```

## 尚未覆盖

本批不是全部条件函数系数敏感性：T/Q的条件系数、自身能力项、系数联合变化和更广范围仍未全部检查。各敏感性新组保留基础参数/分支诊断，但不等于第14阶段的完整有限扰动诊断已对所有新组重新开展。正式多输入拟合及11节点18边扩展也未完成。
