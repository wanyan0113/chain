# 逐接受节点独立变化条件实验

本实验沿用已冻结的七节点网络、根节点抽样、观测扰动与训练/验证划分，仅增加逐样本变化的接受节点条件。C表示自身能力，M表示局部任务流程条件，E表示局部环境。它们只进入独立数据生成器，不作为FDNA拟合的额外自变量。

每次独立重复有250个基础网络，200训练、50验证。T/Q/R分别独立抽取C∼U(0.4,1)、M∼U(0,1)、E∼U(0,0.8)，同一基础网络的σ=0/2/5/8版本共享这些条件与干净状态。条件随机数使用原先保留的独立子流，不消耗根节点、过程或观测子流；接收节点按编号排序后分配子流。修改网络节点集合后须视为新实验配置。

## 文件入口

- `data/fdna/generation_varying_conditions.json`：冻结生成设置及新条件抽样范围。
- `data/fdna/generation_varying_fit.json`：只选择新增变化条件组拟合基础式。
- `data/fdna/generation_varying_delta.json`：固定δ=-5、自由δ∈[-10,10]；不依据验证集挑选。
- `fdna/generation_varying.py`：生成并保留旧对照数据，保存逐基础网络条件和分划分结构下界。
- `fdna/generation_varying_experiment.py`：核验冻结对照，依次保存生成、基础拟合、δ对照。
- `fdna/generation_varying_report.py`：核验同划分下界、导出经验抽样统计及汇报图表。
- `tests/test_fdna_generation_varying.py`：配对、重放、配置错误、顺序独立、状态隔离及拟合流程测试。

## 复跑

在工程根目录使用具有NumPy、SciPy、matplotlib及工程依赖的Python环境运行；输出目录须不存在或为空，不覆盖冻结结果。第一条命令要求旧生成报告已存在，可从05阶段结果包取得。第二条命令还要求08阶段摘要，用于引用旧组拟合结果，不重新拟合对照。

```powershell
python -B -m fdna.generation_varying_experiment --network data/fdna/generation_network.json --nodes data/fdna/generation_network_nodes.json --config data/fdna/generation_varying_conditions.json --fit-config data/fdna/generation_varying_fit.json --delta-config data/fdna/generation_varying_delta.json --frozen-generation generated/stage2-step4-noise-repeats-final/generation_report.json --output-dir generated/stage2-varying-conditions
python -B -m fdna.generation_varying_report --experiment-dir generated/stage2-varying-conditions --reference-summary generated/stage2-delta-noise-repeats/delta_repeats_report.json
```

生成报告包含旧两组和新组，新增拟合仅选择新组。每个节点/条件组/噪声/重复估计一个常数δ，验证沿用训练结果。过程波动与交互均关闭；含δ输出不裁剪。

## 结果解释边界

基础式结构下界只与同输入、同目标、同划分的局部RMSE比较，不套用于含δ或整网递推。经验分布与相关性用于检查抽样，不证明现实任务分布。C/M/E共同变化不能识别单因素因果贡献。重复间标准差不是置信区间，噪声版本不是独立网络样本。预测改善、参数可辨识性与输出范围分别报告。
