# 固定数据与起点下的两套求解流程对照

本步推进优化清单第5项及论文分析指出的初值敏感问题。固定06原始输入、90训练/30留出基础样本、三种环境、两模型、同一随机种子、同一组六个初值、参数界限及1500数值上限，仅切换`solver`字段，在L-BFGS-B与SciPy least_squares两条现有路径间对照。

这比较的是两套完整配置：L-BFGS-B使用解析梯度、minimize与其收敛条件；least_squares使用残差、有限差分雅可比及自身停止条件。相同数值上限不等于相同函数调用、梯度计算量或时间预算。本步不作算法级速度或普遍优劣基准，不换种子、不跨算法热启动、不用留出结果选择算法。

## 运行与核验

协议在`data/fdna/solver_comparison_protocol.json`。从`.publish/fdna-bridge`运行，输出目录使用新空目录：

```powershell
& '..\..\.venv\Scripts\python.exe' -B -m fdna.solver_comparison --source-dir '..\..\generated\关于两篇连接\06_完整场景全流程运行' --protocol data/fdna/solver_comparison_protocol.json --output-dir '..\..\generated\关于优化\20_固定起点求解配置对照_复跑'
& '..\..\.venv\Scripts\python.exe' -B -m workflow_tools.verify_solver_comparison --report-dir '..\..\generated\关于优化\20_固定起点求解配置对照_复跑'
```

独立标量脚本逐条重算起点训练损失、选中参数、训练/留出局部与递推预测和指标，并核对正式06 L-BFGS-B控制。核验不调用拟合器。首次结果、图表和测试记录在`generated/关于优化/20_固定起点求解配置对照`，发布摘要见[快照](solver-comparison-report/README.md)。

原留出集已被此前分析查看，本步不是新独立测试。两路径的梯度和停止准则不同，差异不能归因于单一因素。传播尚未重算，这里也尚无跨算法、同输入定义下的级联传播对照；已有两组模型记录本身不能当作已经完成该分析。原正式结果未改变，第5项仍部分完成。
