# FDNA能力依赖关系分析工具 V1.0

V1.0为研究用命令行工具：给定DAG和多条对应节点SE记录，逐接受节点拟合α、β，报告局部与整网预测、参数约束、模型内节点退化和传播分支。软件实现、代表性模拟验证和真实应用证据分别解释；本版本没有真实数据校准。

## 安装与一键验收

在解压后的仓库根目录运行。已验证环境为Windows 11、Python 3.14；18项依赖版本见requirements-v1.lock。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-v1.lock
.\.venv\Scripts\python.exe -B -m fdna.release_acceptance --output-dir generated/汇报用/V1验收_新运行
```

该命令检查16个文件案例，核对预期状态及退出码，执行observed完整流程和样本不足的受阻验收，保存日志、参数、预测、诊断、2张PNG及SVG和文字说明。案例固定在data/fdna/v1_acceptance；输出目录须不存在或为空。

observed案例复用既有独立机制模拟的250条观测，200训练/50留出，不含干净供给字段。仍是模拟数据，没有处理供给测量误差。完成3个任务、15次局部拟合；保留13次局部秩不足、5次自由δ触界和12条模型内退化越界。上述诊断没有被隐藏或裁剪。

## 导入自己的网络

```powershell
.\.venv\Scripts\python.exe -B -m fdna.network_preflight --network 网络拓扑.json --templates --output-dir generated/填写模板
.\.venv\Scripts\python.exe -B -m fdna.network_preflight --network 网络拓扑.json --samples 已填写SE.csv --config 基础式拟合配置.json --output-dir generated/输入预检查
.\.venv\Scripts\python.exe -B -m fdna.network_workflow --network 网络拓扑.json --samples 已填写SE.csv --config 基础式拟合配置.json --output-dir generated/拟合与分析
```

用模板中的基础式配置开始；自由δ另行登记边界。长表附加--layout long。输入拓扑只有id、nodes和feeder/receiver边，不预填α、β；SE范围0至100。每个节点需要多条完整供给与目标记录，一张拓扑或单条样本不能估计参数。缺失留空，不能补0。场景机制不同须分组；同一基础实验的派生记录不得跨训练与留出。

退出码：预检查0为达到基本样本条件（可能有警告），1为needs_data，2为invalid。拟合入口0为complete，1为incomplete，2为invalid。complete表示流程完成，不保证参数唯一或预测可靠。

## 验证记录

独立新虚拟环境重新安装依赖，禁用用户site-packages；代码由Git归档导出到独立目录。完整回归327项通过、无失败和跳过；16个案例独立符合预期。依赖锁定也经离线sync核对。Windows Git换行转换对案例哈希的影响已通过.gitattributes固定字节及重新登记CSV修复，最终归档入口复跑通过。

完整回归的来源文档核验额外使用本地外部Word和审计夹具，不随源码发布。普通克隆缺少该夹具时按既有规则跳过1项来源核验；其余方法测试和交付案例不依赖该Word。运行完整回归：python -B workflow_tools/run_workflow_checks.py。

历史代表性实验：第21包72次3/5输入拟合；第22包11节点18边及90次局部拟合；第26包9720条退化响应；第28包限制分支解释。新observed入口为交付验收，不重算全部旧矩阵。全部16张PNG及同名SVG按四部分编入本地第33包，Word另含图表目录和解释。

## 冻结边界

Git标签为fdna-v1.0.0；本地第34包记录最终提交、源码ZIP和交付文件哈希。更新后的Word为《多场景能力依赖关系模拟实验设计方案_V1.0交付修订版》，旧版保留。后续修改另建版本，不移动已发布标签。

16组系数深入诊断继续暂缓；真实数据来源、指标归一化、输入误差和独立应用验证是下一阶段。局部秩不足、参数补偿、δ触界、未裁剪越界和训练外推仍存在。局部导数不能替代大幅退化效果，拓扑可达不意味着分支活跃，路径影响不简单相加。
