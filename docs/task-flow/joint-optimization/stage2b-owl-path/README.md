# 阶段二-B：OWL推理路径与进程开销细分

阶段二90次有效运行的基线按用户评审关闭，原数据和问题历史保持冻结。本轮继续在 `codex/task-flow-planning` 分支，新增可选观测，不实施缓存、并行、增量推理或网络算法加速。结果见[细分报告](results-v1/README.md)。

## 测量设计

生产路径保持原有Owlready2→HermiT CLI及原结果结构。`adapters.owl_infer` 的可选observer只发送阶段起止事件，指标另存实验报告；普通调用不新增性能字段。显式区分以下实测范围：

- 父进程：输入序列化、Python推理工作进程启动至返回、结果JSON解析。
- Python工作进程：首次Owlready2导入、World创建、本体加载、同步推理整体、N-Triples序列化、Java子进程整体、结果应用、查询转换。实验包装器先导入Owlready2以安装埋点，首次导入单列，随后worker内import只是模块查找。
- Java API诊断探针：Manager创建、本体加载、Reasoner构造、一致性检查、预计算、查询/格式化、输出和释放，均在实际调用前后用同一JVM的nanoTime直接测量。

没有用总时间减去其他项冒充“纯推理”。原CLI内部的本体加载、Reasoner构造、分类和纯JVM初始化依然不能直接观测，保持未测量；独立Java探针的数据不能当作原CLI内部数据。两条路径的具名类型和关系逐次核对，结果相同只支持这些查询的语义一致，不保证内部算法次序与完整逻辑证明相同。

`launch_to_ready` 是从父进程开始启动Java到读到探针READY行的直接延迟，包含OS创建、JVM/主类初始化和管道交付；另记Popen返回时间。它不是纯JVM引导成本。阶段有嵌套，不能直接相加；分类工作可能分布在构造、检查、预计算及查询中。

## 矩阵及有效性

7档：base、1/4条解析流程、法器复制3倍、32/128层附加本体、仅一份封印法器可用。各档5次完整生产路径和5次Java冷进程诊断。所有普通参考运行先生成完整输入快照及独立穷举结论，再冻结协议和源码摘要，随后测量。

同一Python进程重复6次：每次新World、新本体、新JVM。另在base及128层本体上同一JVM重复各6次：每次新Manager、新本体、新Reasoner，仅复用进程及JIT/类加载状态。各组第一轮作为首次观测单列，后5轮用于同进程统计，不把新JVM的Python复用组称为JVM热启动。没有复用已经推理过的本体或Reasoner缓存。

## 本轮观察及后续决策

生产路径中，OWL调用整体中位数约2.04至2.40秒，其中Java子进程整体约1.20至1.47秒。首次Owlready2导入、Python本体加载、序列化、结果应用和查询均另有直接观测，不能把整段OWL调用时间归为纯HermiT分类。

Java API诊断中，基础场景冷进程本体加载、Reasoner构造、预计算中位数分别约0.256、0.271、0.177秒；128层场景预计算约0.429秒。启动至READY约0.10秒，但后续Manager创建、本体加载、构造器仍有首次使用成本，READY不代表所有Java库都完成初始化。

基础场景同JVM首轮约1.046秒、后5轮批次中位数约0.107秒；128层场景约1.420秒和0.233秒。这是重新加载本体、重新构造Reasoner前提下的同进程状态观测，原因可能包含类加载、JIT及文件缓存；本轮未进一步归因，也没有给生产CLI实现持久JVM，不能据此声称生产系统已加速相应倍数。

这些数据支持下一步单独评估推理进程生命周期方案；尚不足以直接选择持久JVM、应用缓存或增量算法。更大规模退化点仍未测定。阶段三须以可观测的真实/受控误差及标签独立验证精度政策，不能用本轮性能结果替代其证据。

base和one-seal完整OWL输入、查询与结果必须一致，而候选链分别4条与1条，且候选生成均完整。区别来自OWL之外的可用性筛选，不能冒称同一业务约束下的加速。flows-1和flows-4基础本体文件相同、活动请求和环节实例数量不同，因此完整OWL图不同；输入规模与配置规模分别记录。生产计时中的流程搜索调用还包含独立核验的搜索重证，主规划工作量另存planning_statistics。

## 正确性及永久回归

新增真实推理测试确认observer开关不改变结果结构或具名结论；验证计时嵌套在异常路径下平衡。实验逐次比较候选集合、身份、原始指标、评分、所选网络及证明状态，并与普通运行一致。

OWL检查、分类/关系、联合组网及观测工具共50项相关回归通过，包含本轮3项专项；两者有重叠，不累加为53项。归档保存完整测试日志。

`test_budget8_optimal_incumbent_never_upgrades_production_proof`固定网络预算8：独立穷举确认当前解分数最优，生产搜索依旧未完成；再次独立核验后仍必须为 `not_proven`。该测试不修改生产证明等级。

## 复现

需要当前环境的Java/Javac及已安装HermiT，根目录运行。输出目录必须不存在：

```powershell
New-Item -ItemType Directory -Force tmp/owl-java-compile | Out-Null
javac -encoding UTF-8 -cp .venv/Lib/site-packages/owlready2/hermit/HermiT.jar -d tmp/owl-java-compile workflow_tools/java/OwlTimingProbe.java
.\.venv\Scripts\python.exe -B -m workflow_tools.benchmark_owl_path --output-dir generated/task-flow/my-stage2b --compiled-dir tmp/owl-java-compile --hardware-json docs/task-flow/joint-optimization/stage2-performance/results-v1/evidence/batch-1/hardware.json --repetitions 5
.\.venv\Scripts\python.exe -B -m unittest tests.test_owl_profile tests.test_joint_integration.JointIntegrationTests.test_budget8_optimal_incumbent_never_upgrades_production_proof
```

硬件JSON仅适用于本主机；其他主机须重新记录CPU及物理内存。编译类摘要、Java版本、HermiT jar摘要、输入与源码摘要进入独立实验记录，不改写旧manifest。Windows沙箱曾拒绝Javac读取jar的真实路径，本轮通过已授权的沙箱外编译完成，未替换jar或安装依赖。

通用环境采集器的 `harness_sha256_lf` 仍指向被复用的阶段二工具；本轮执行源码以protocol.json中的四份明确路径及摘要为准。

原始完整业务报告留在generated目录，报告归档保留其路径及SHA256，不能声称这些大文件全部进入Git。跨平台、更大规模退化点及阶段三精度模型的领域有效性依旧未完成。性能实验不能替代、推迟或省略精度领域验证；本轮仅形成下一步优化决策所需证据。
