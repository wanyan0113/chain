# 生成系数敏感性：末端权重首批实验

本批只检查R的归一化贡献权重对：Q/D_aux从冻结的0.62/0.38变为0.52/0.48、0.72/0.28。±0.10为事先规定的绝对扰动，两个权重和为1，只有一个自由度，不称为两个独立因素。尚未完成0.90/0.78互补系数及条件函数系数的系统敏感性。

## 协议

使用第10阶段变化条件来源：每接受节点独立C/M/E，过程波动和交互均关闭。共享根节点、条件、划分和观测创新，生成干净状态后再加配对观测噪声。每组10次数据重复，每次250个基础网络，200训练及50验证，观测σ=0/2/5/8。噪声版本不计作独立推演。

原0.62/0.38结果只引用、不重拟合。新增基础式、固定δ=-5、自由δ[-10,10]各80组，共240组整网计算、720次接受节点拟合。12个起点、L-BFGS-B、边界及停止设置沿用来源。预测不裁剪，生成端继续保持既有0到100范围规则。

`generation_coefficients.py`保存每个组的完整节点配置，拒绝非法和重复权重，不自动归一化，保留旧行指纹。`generation_fitting.audit_generation`可按组配置完整重放，输出或快照被修改后不能继续拟合。模拟权重不进入FDNA模型、起点或真值；FDNA仍只用上游SE。

`generation_coefficient_report.py`核对来源指纹、配对样本、同数据局部结构下界，并检查未变动T/Q的拟合参数和指标保持一致。输出训练/验证、局部/整网、干净/观测目标指标，跨权重同重复差值、逐样本生成变化、δ触界、局部秩和越界记录；每次求解的多起点、分支与敏感度仍保留在完整fit_report中。

## 重跑

需要第10阶段冻结结果恢复到`generated/stage2-varying-conditions`，输出目录必须为空：

```powershell
python -B -m fdna.generation_coefficient_experiment --config data/fdna/generation_coefficient_conditions.json --fit-config data/fdna/generation_coefficient_fit.json --delta-config data/fdna/generation_coefficient_delta.json --reference-dir generated/stage2-varying-conditions --output-dir generated/stage2-coefficient-weights
python -B -m fdna.generation_coefficient_report --experiment-dir generated/stage2-coefficient-weights --reference-dir generated/stage2-varying-conditions
```

## 解释边界

R的目标随生成权重变化，跨组RMSE差只是当前合成设计的敏感性，不是因果传播量或α、β恢复误差。局部结构下界只用于同输入同目标的基础式局部结果。均值±SD是十次重复的样本标准差，不是置信区间。

δ改善预测、参数可辨识性及输出范围分开报告。触界不是参数真值证据，局部秩不证明全局唯一或不唯一。没有为降低误差修改机制、扩大δ范围或依据验证集挑选权重。
