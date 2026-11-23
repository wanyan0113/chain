# 重复传播的运行效率优化

本轮优化现有传播代码的重复工作，接入`fdna.refit_propagation.replay`。没有新拟合、SE生成、结构改变或参数变化，不新增一项统计识别或真实有效性结论。

## 实现

`PreparedTopology.from_network(network)`完整校验拓扑，复制节点、父节点关系和拓扑序。`bind(fits)`检查完整参数关系、拟合成功状态、α/β边界和有限性，生成与原字典脱离的不可变参数快照。参数数组由不可变字节支撑，不允许重新开启写入；拓扑和方程表也不能直接修改。

`prepared.predict(roots, forced)`每次仍检查根输入恰好匹配原根、数组维度一致、干预节点及维度合法。缺失保持NaN，不读取内部节点观测，不裁剪预测。原`predict(network,fits,roots,forced)`保留为一次性兼容入口。

```python
from fdna.prepared_prediction import PreparedTopology

topology = PreparedTopology.from_network(network)
prepared = topology.bind(fits)
baseline = prepared.predict(root_arrays)
changed = prepared.predict(root_arrays, ("B1", forced_values))
```

原参数字典之后变更不会自动改变已准备模型；应显式重新`bind`。准备对象不保留观测数组。传播回放在单个模型任务内复用相同“干预节点＋比例”的完整结果，覆盖该任务固定输入面板；任务结束即释放，不跨模型、输入面板或重拟合轮次。

## 实测与一致性

[性能图和报告](prepared-prediction-report/结果说明.md)：固定6模型任务、全部30个原留出基准、9组关系及五档比例，5轮交替顺序计时，包含每轮校验与准备。旧实现中位数1.135641秒，优化后0.026733秒，本机比值42.48倍。完整拓扑校验277→1次，DAG计算276→186次；调用次数由单独仪器化回放计数，未把仪器化计时混入性能样本。

参考为提交19d31b8中的原layout/predict/replay三个函数原样副本，仅放在workflow_tools用于性能对照。采用当前同一依赖环境，避免把依赖版本变化误当算法收益。计时不包含文件读取、拟合、核验、绘图或程序启动，不作为整体FDNA求解、任意设备或任意拓扑的加速保证。

全部已有50轮模型重新传播，1296000个数值与1215000个布尔掩码逐字节一致，包含NaN与符号零。独立标量复核405000条响应和270行摘要通过，163项相关测试通过。原09包没有覆盖；完整回放另存于12目录的全量复跑子目录。

## 复跑

从`.publish/fdna-bridge`运行，输出目录须不存在或为空，核验文件不能已存在：

```powershell
& '..\..\.venv\Scripts\python.exe' -B -m workflow_tools.benchmark_prepared_prediction --source-dir '..\..\generated\关于优化\09_重估模型传播敏感性' --frozen-report '..\..\generated\关于优化\07_冻结模型多基准传播_核验版\propagation_report.json' --protocol data/fdna/prepared_prediction_benchmark.json --output-dir '..\..\generated\关于优化\12_传播计算效率优化'
& '..\..\.venv\Scripts\python.exe' -B -m workflow_tools.verify_prepared_benchmark --report-dir '..\..\generated\关于优化\12_传播计算效率优化' --output '..\..\generated\关于优化\12_传播计算效率优化\独立核验.json'
& '..\..\.venv\Scripts\python.exe' -B workflow_tools/publish_prepared_benchmark.py --source '..\..\generated\关于优化\12_传播计算效率优化' --destination docs/prepared-prediction-report
```

本地完整复核需要原06/07/08/09来源包。计时算术核验核对既有计时记录、指纹与完整数值，不等于独立重新测时；另机测时应运行基准命令，并保留自己的环境与逐轮记录。源代码指纹规范化CRLF为LF，科学结果与快照清单则校验精确字节。

这是一项实现层的运行效率优化，不改变前述九项研究建议的完成数量。当前单供给消融的剩余局部方程训练输入、目标与损失未变，单纯重复拟合不会扩展结构证据；完整替代结构重估仍需有明确语义的新结构。
