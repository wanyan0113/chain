# 互补支持系数逐一扰动

本批接续末端贡献权重敏感性，检查T的独立互补机制：

`T=100*[1-(1-a*A/100)*(1-b*B/100)]*C*(0.82+0.18*M)*(1-0.28*E)`。

冻结基线a=0.90、b=0.78。预设a=0.80/1.00时b不变，b=0.68/0.88时a不变，每组只改一个系数。±0.10是绝对扰动；不按验证误差挑选数值。a=1处于合法系数区间端点，不等于A的SE恒为100。支持系数不是归一化贡献权重，不要求a+b=1。未检查a/b同时变化的相互作用。

沿用第10阶段变化条件组根输入、逐节点C/M/E、划分及观测创新；过程波动和交互关闭。每组10次数据重复，每次250个基础网络、200训练与50验证，观测σ=0/2/5/8为配对版本。四个新系数组、三模型共480组整网计算、1440次接受节点拟合。基线只引用、不重拟合。

T干净输出进入Q，Q干净输出进入R，观测噪声不沿网传播。Q/R配置保持不变，但输入和目标会随T变化，其拟合参数和误差可以改变。本批不能沿用末端R权重实验中“T/Q拟合不变”的判据。

基础式δ=0、固定δ=-5、自由δ[-10,10]。保持来源的12个起点、L-BFGS-B及停止设置。生成系数不作为α/β真值或起点；FDNA输入仍仅上游SE，预测不裁剪。C/M/E是生成条件，不是新增FDNA特征。

## 代码与复跑

- `generation_coefficients.py`兼容冻结权重实验并增加`support_groups`，保存逐组完整节点配置，拒绝非法范围、重复组、两系数同时变动及两种实验类型混入。
- `generation_coefficient_experiment.py`沿用来源指纹、完整选择与求解设置核验。
- `generation_support_report.py`分别按A/B配对基线，保存各节点训练/验证、局部/整网误差、生成变化、分支和参数诊断、δ及越界记录。两因素表中的基线是同一组，不能重复计为独立数据。
- `test_fdna_support_coefficients.py`检查独立标量公式、干净状态传播、单调方向、根节点不变、非法配置、快照/输出篡改与小规模完整拟合及汇报。

输出目录须为空，来源第10阶段恢复到`generated/stage2-varying-conditions`：

```powershell
python -B -m fdna.generation_coefficient_experiment --config data/fdna/generation_support_conditions.json --fit-config data/fdna/generation_support_fit.json --delta-config data/fdna/generation_support_delta.json --reference-dir generated/stage2-varying-conditions --output-dir generated/stage2-support-coefficients
python -B -m fdna.generation_support_report --experiment-dir generated/stage2-support-coefficients --reference-dir generated/stage2-varying-conditions
```

## 结论边界

这是模拟生成系数的局部敏感性，不是参数恢复或真实作战效度验证。跨系数组时实际目标变化，RMSE差不是因果传播量；局部结构下界只用于同输入同目标的基础式局部预测。均值±SD是重复间样本标准差，不是置信区间。

预测表现、参数可辨识性和输出范围分开报告。支持系数实验完成不等于条件函数系数敏感性、多输入正式拟合或11节点18边扩展完成。
