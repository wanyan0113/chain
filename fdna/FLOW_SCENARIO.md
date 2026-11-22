# MD抽象链网场景的完整运行

本场景使用三类抽象任务、六个虚构主体与信息产品合格事件，验证链网生成、能力转换、模拟观测、FDNA拟合及传播接口。没有真实目标、装备性能或部署参数。任务与概率来自用户批准的MD，执行参数转录到data/fdna/flow_scenario；程序不自动解析MD自然语言。

## 依赖与运行

需要Python、V1数值依赖、owlready2==0.51和PATH上的Java。新环境可安装requirements-v1.lock与requirements-reasoner.txt。本机已配置，详细本机命令在generated/关于两篇连接/06_自己复跑步骤.md。

从仓库根目录运行：

```powershell
python -m fdna.flow_scenario run --plan "你的全流程场景设计_待运行.md" --output-dir generated/flow-run-1
python -m fdna.flow_scenario verify --output-dir generated/flow-run-1
```

每次运行选择不存在或为空的输出目录。run重新执行HermiT知识推理、现有design引擎与独立枚举，而不是读取旧链网报告。比较入口：

```powershell
python -m fdna.flow_compare --reference generated/flow-reference --candidate generated/flow-run-1
```

两个包均先校验完整文件哈希；再比较冻结MD、输入、实际链网、映射、原始事件数组、观测与审计、拟合、分析和实现文件哈希。只允许显式列出的求解器/随机流版本字段不同；图像与RDF序列化不要求字节相同。比较报告用--report另存包外。

## 本例固定约定

- 任务T1甲、T2乙、T3丙。实际生成8条候选链、16种组合、4种容量可行网，数量作为核验预期，不用于造出输出。
- 语义排序为任务编号，再按insight/coordination/manifestation角色的主体编号元组。旧接口的时长、正确率和成本全部同值占位；可行网同分，独立核对引擎选网恰是首个语义可行组合。占位值不是实际性能。
- 新知识命名空间urn:fdna:synthetic-flow:v1:定义独立的任务、主体类和具名能力，复用基础角色类型；不复用旧场景能力的身份。实际HermiT推导与来源快照保存。
- 9个任务产品状态、6条直接消费边；共享主体按实际选网登记。无跨任务FDNA边不等于统计独立。
- 120组基础样本，90/30预先划分，3个环境共用状态及split。每任务每环境200次逐级条件事件，得到360条网络记录。
- SeedSequence(20261122)分离状态、划分、事件三个随机流。不同主体H独立均匀；同主体只抽一次；环境复用对应微观均匀数并重新判断上游和下游事件。
- 正式拟合只用观测SE；生成概率、H和微观事件仅用于审计及另行标记的已知合成来源情景。基础式与自由δ共同数据和划分，6初值，δ边界[-10,10]，36次局部拟合。

修改方案时应同时核对MD与结构化配置，不得只改MD便宣称按新参数执行。复跑既有方案时两者均保持不变。

## 输出与解释

00冻结输入、01实际链网与知识、02映射、03事件与SE、04拟合、05已知合成来源情景、06汇报图表。NPZ保存states、uniforms、events、probabilities、train_mask等数组，不使用pickle。

事件数组维度为environment/base_case/task/stage/trial；均匀数数组为base_case/task/stage/trial，三个环境复用。stage对应A/B/F，subjects、tasks、nodes数组给出编号顺序。SE须能由events计数重算。

分层验收不把模型适用性压成通过/失败：结构及计算通过后，仍报告秩不足、多初值跨度、触界、预测误差和外推。固定本组p后的有限重复抽样标准差至多约3.54 SE点；它不限制跨H、任务和环境的总体离散度。

测试使用test_flow_scenario.py、test_flow_compare.py，并复用已有桥接、网络流程及test_fdna_recovery.py的已知参数检查，不另扩正式实验矩阵。
