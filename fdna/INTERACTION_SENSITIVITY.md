# 冻结数据上的交互强弱正式拟合

本阶段补齐第四次修订补充版第7及13.3节的预设强弱对照。读取第12阶段已保存的γ=0.05/0.30数据，沿用主组γ=0.15的全部求解设置。γ=0引用第10阶段结果，γ=0.15引用第12阶段结果；旧组不重拟合，不重新生成样本，不按验证误差选择γ或扩大δ界限。

新增两强度×四噪声×十次重复×三模型，共240组整网计算、720次接受节点拟合。每强度仍是2500个基础网络，四档噪声为同一基础网络的观测版本，不能称为额外独立推演。本轮过程波动为0，接受节点的C/M/E在生成端独立抽取，FDNA仅读取上游SE。

## 文件入口

- `data/fdna/generation_interaction_sensitivity_fit.json`、`generation_interaction_sensitivity_delta.json`选择两个既定强度；求解器、12个起点、参数范围、固定δ=-5和自由δ[-10,10]保持原设定。
- `fdna/generation_interaction_sensitivity.py`核验冻结数据、主组拟合和摘要的版本，拒绝设置漂移或混入主组后再拟合。
- `fdna/generation_interaction_sensitivity_report.py`汇总四强度，核对实际样本配对和结构下界，导出指标、配对差值、分支覆盖、不活跃参数、近等损失起点跨度及越界记录。
- `tests/test_fdna_interaction_sensitivity.py`覆盖冻结协议、非法设置、报告篡改、小规模全链路、按重复编号配对、样本条件一致性。

## 复跑

恢复第10、12阶段结果到以下目录，在具有工程依赖的Python环境下运行。输出目录须为空。拟合入口读取现有生成报告；重放只用于核验，没有替换冻结数据。

```powershell
python -B -m fdna.generation_interaction_sensitivity --primary-dir generated/stage2-interaction-primary --fit-config data/fdna/generation_interaction_sensitivity_fit.json --delta-config data/fdna/generation_interaction_sensitivity_delta.json --output-dir generated/stage2-interaction-sensitivity
python -B -m fdna.generation_interaction_sensitivity_report --experiment-dir generated/stage2-interaction-sensitivity --primary-dir generated/stage2-interaction-primary --reference-dir generated/stage2-varying-conditions
```

原生成报告不另造版本，来源由`冻结来源核验.json`及汇报来源指纹登记。基础式、固定δ、自由δ分开存档，失败组不混入成功均值。新模型仍按训练损失选择起点，验证集只评价。

## 解释边界

本阶段完成的是预设γ强弱敏感性，不等于0.90/0.78、0.62/0.38或自身条件系数的系统敏感性全部完成。四强度共享基础样本，但实际中间状态及目标随γ变化，跨强度RMSE差不直接等于传播误差。局部下界只用于相同实际干净输入、目标和划分的基础式局部结果；含δ与整网误差另评。

图表误差棒为十次独立数据重复的样本标准差，不是置信区间。参数局部秩、活跃分支及起点跨度只说明当前样本下的敏感性；预测改善不证明参数唯一，触界不证明真值位于边界。含δ预测不裁剪，负值/超100单独记录。
