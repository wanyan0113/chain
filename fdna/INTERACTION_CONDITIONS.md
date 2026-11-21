# 首轮交互效应实验

本轮对应第四次修订补充版第7节及13.3节，基于第10阶段的变化条件组开展独立交互扩展，关闭过程波动。第11阶段过程波动组继续保留，不把交互影响混入过程实验。

## 预先约定与完成口径

交互扣减为`100 * gamma * E * (1-C)`，在节点任务公式后扣除，再截断到[0,100]，按拓扑传递。扣减使用该节点自己的C/E；上游变化会另外影响当前任务公式。因此本地扣减为0，不保证节点总状态与原组相同。

预先设置γ=0.05、0.15、0.30，三者均生成配对样本，首轮仅对γ=0.15完成正式FDNA拟合。强弱值为本研究预定的模拟假设，不是原论文参数，也不是按验证误差选择的最佳强度。γ=0对照直接使用已冻结的第10阶段数据。

每种强度2500个基础网络，来自10次独立重复；每次200训练、50验证；观测噪声σ=0/2/5/8。各强度共享根节点、C/M/E、划分和观测噪声创新。新增7500个干净网络、30000个观测版本、22500条接受节点交互记录，不将配对强度和噪声版本当作独立推演次数。

主组的基础式、固定δ、自由δ共120组计算、360次接受节点拟合。固定δ=-5、自由δ界限[-10,10]，每节点/组/噪声/重复训练一个常数δ、验证沿用。不向FDNA直接输入γ或C/M/E，不裁剪含δ预测。

## 文件与复跑

- `fdna/generation_interaction.py`生成预设强度组，冻结原数据并保存局部扣减、总变化及同划分下界。
- `fdna/generation_interaction_experiment.py`核验冻结来源，仅拟合主交互组。
- `fdna/generation_interaction_report.py`核验来源和配对指标，导出主组对照及未拟合强弱组的生成响应。
- `tests/test_fdna_generation_interaction.py`覆盖配对、重放、独立公式、γ边界、单调性、截断、传播、异常与拟合流程。
- `data/fdna/generation_interaction_conditions.json`定义所有强度和主组；`generation_interaction_fit.json`、`generation_interaction_delta.json`只选择主组。

输出目录须为空。无交互来源数据和对照结果可从第10阶段结果包取得。继承的`config_snapshot`保存旧抽样协议及组索引；本轮定义以`interaction_config_snapshot`为准，各交互组的γ在groups索引中另列，不能因旧协议gamma=0而误读整个报告未开启交互。

```powershell
python -B -m fdna.generation_interaction_experiment --network data/fdna/generation_network.json --nodes data/fdna/generation_network_nodes.json --config data/fdna/generation_interaction_conditions.json --fit-config data/fdna/generation_interaction_fit.json --delta-config data/fdna/generation_interaction_delta.json --frozen-generation generated/stage2-varying-conditions/generation/generation_report.json --output-dir generated/stage2-interaction-primary
python -B -m fdna.generation_interaction_report --experiment-dir generated/stage2-interaction-primary --reference-dir generated/stage2-varying-conditions
```

## 解释边界

主组的实际局部误差仅与同划分、同实际干净上游和目标的基础式下界比较，不用于含δ或整网误差。生成端强弱曲线不能替代强弱组FDNA拟合敏感性实验；γ的扣减项随强度线性变化，也不意味着最终网络状态随强度线性变化。交互状态和观测截断分开统计。与无交互组的RMSE差描述场景变化，不直接等于传播误差。参数识别、预测表现、输出范围仍分别报告。
