# 过程波动扩展实验

对应第四次修订补充版第5.4、12.4与13.3节。本轮在已经冻结的逐接受节点变化C/M/E组上，增加独立的过程波动实验，保持原生成机制系数、δ界限和求解器设置不变。尚未覆盖全部固定条件及其他过程波动强度。

## 生成约定

每次基础网络在T、Q、R各抽取一次过程创新 ε∼N(0,2²)，单位为SE分值。随机数使用原冻结协议的process子流，按排序后的接收节点逐个抽取长度为样本数的向量；不消耗条件、根节点、观测或划分的随机数。

节点计算顺序为：实际干净上游 → 原独立任务机制 → 加本地过程创新 → 截断到[0,100] → 传递给下游。随后另加各档观测噪声、再截断，形成观测值。四档观测版本共享同一次过程创新、条件与潜在真实状态；不能按四次独立推演计数。

`clean`在本轮指“未叠加测量误差的状态”，它包含过程波动。过程后的T进入Q，过程后的Q进入R。总状态变化包含上游作用、本地创新、非线性和截断效应，不等于本地创新；总变化标准差不要求为2。

FDNA局部拟合使用实际过程状态作为上游输入，使用接受节点观测目标；整网预测仅提供干净根节点，所有中间状态由拟合FDNA递推，不向预测端输入过程创新或C/M/E。不裁剪含δ模型输出，越界另行报告。

## 代码与配置

- `fdna/generation_process.py`：重放冻结变化条件组，追加过程组，保存逐节点创新、实际状态及同划分结构下界。
- `fdna/generation_process_experiment.py`：核验冻结来源后依次运行基础式与两种δ对照；旧组不重新拟合。
- `fdna/generation_process_report.py`：核验报告指纹，按同模型、重复、噪声、节点和划分配对，导出图表、截断统计和下界复核。
- `tests/test_fdna_generation_process.py`：数据保留、随机数配对、独立公式复算、过程传递与观测隔离、截断、篡改检查、小规模拟合与报告配对。
- `data/fdna/generation_process_conditions.json`：来源配置、组编号及过程标准差。
- `data/fdna/generation_process_fit.json`和`generation_process_delta.json`：只拟合新增过程组，沿用固定δ=-5与自由δ[-10,10]。

过程报告的`process_config_snapshot`为本轮实验定义；继承的`config_snapshot`保存旧抽样协议与可用组索引，其中旧协议的process_std仍为0，新过程组在groups中另列标准差，不将旧组误标为已启用过程波动。

## 复跑

在工程根目录及已安装工程依赖的Python环境中执行。旧生成报告、旧对照结果可从第10阶段结果包取得。输出目录须为空，以免覆盖冻结基线。

```powershell
python -B -m fdna.generation_process_experiment --network data/fdna/generation_network.json --nodes data/fdna/generation_network_nodes.json --config data/fdna/generation_process_conditions.json --fit-config data/fdna/generation_process_fit.json --delta-config data/fdna/generation_process_delta.json --frozen-generation generated/stage2-varying-conditions/generation/generation_report.json --output-dir generated/stage2-process-conditions-final
python -B -m fdna.generation_process_report --experiment-dir generated/stage2-process-conditions-final --reference-dir generated/stage2-varying-conditions
```

新增2500个过程网络、10000个观测版本、7500条接受节点过程创新；三模型共120组计算、360次节点拟合。训练200、验证50，重复10次，观测σ=0/2/5/8。

## 解释边界

基础式下界必须使用过程组实际干净上游及目标重新计算，并只对照同划分局部误差。原无过程下界不能直接挪用。过程组与无过程组在相同根节点、条件和划分下配对，但中间状态和目标均不同；组间RMSE差描述当前扩展条件的变化，不等于传播误差或模型泛化改善。模型比较、参数可辨识性、预测越界与两类截断分别报告。
