# 阶段三接通：所选法器网到观测采集计划

上一轮提供通用观测接口，本轮将它接到 `joint_candidate_optimization` 的所选链：独立核验源报告，提取任务、活动实例、版本、顺序、法器分配及完整规划属性，生成可填写的采集包。**尚无真实观测；本轮完成软件接通，不关闭领域验证。** 生产评分公式及旧实验记录均未修改。

## 已跑通的具体案例

使用现有虚构裂隙知识与真实HermiT执行生成三任务网络，提取T1的3环节、T2/T3的4环节。T2清单如下：

| 顺序 | 活动ID | 类型 | 角色 | 所选法器 |
| --- | --- | --- | --- | --- |
| s1 | observe | observe | insight | R-Survey |
| s2 | analyze_secondary | decide | coordination | R-Analysis-Backup |
| s3 | decide | decide | coordination | R-Dispatch |
| s4 | stabilize | act | manifestation | R-Seal-B |

这就是三种类型、四个环节：探知→解析决策→解析决策→稳定。重复的decide没有被合并，清单直接来自核验后的所选链，没有另写一套活动顺序。活动版本来自活动库；法器原始属性、能力、成本/时间/精度假设、库存与容量保留在binding.json的selected_artifact_snapshots及完整源报告中。规划输入里的指标并非实测精度。

可查看[四环节采集协议](evidence/task_T2/protocol.json)、[环节清单](evidence/task_T2/stage-roster.csv)、[完整来源与属性](evidence/task_T2/binding.json)。[观测CSV](evidence/task_T2/observations.csv)只有列头，没有生成假标签或样本。[全部三任务包](evidence/all_selected.zip)与[预算8当前最好解采集包](evidence/incumbent_T2.zip)以ZIP归档，解压后可直接检查。

## 身份和版本

采集协议的flow_id使用candidate_id，来源另保留source_flow_id。这样同一活动流程换了法器分配，也不会被当成同一试验方案。阶段ID使用源链的s1/s2等实例ID；同编号在不同候选链中仍分开绑定。

collection_plan_id仅标识所选设计候选，排除运行ID、排序分数及证明状态；同一选择在新运行出现时保持稳定。实际研究的标签、总体、真实版本及采样上下文由后续冻结协议的hash标识，不混同为源设计身份。来源run_id、知识快照及设计上下文另存，源报告的not_proven不会在导出时升级。

原法器快照没有真实固件/设备版本，因此artifact_version默认UNASSIGNED，且研究冻结仍会被阻塞。不能把知识哈希或活动库1.0版本当作设备版本。真实版本可后续填写，也可通过artifact-versions-json明确提供；它属于外部声明，不是软件认证。

## 包内文件

| 文件 | 用途与可编辑性 |
| --- | --- |
| protocol.json | 可编辑研究协议，填写总体、真实标签、方法审核、版本、抽样及容差 |
| observations.csv | 可编辑真实观测，初始仅列头 |
| protocol-template.json | 原始自动生成模板，保留不改 |
| stage-roster.csv | 环节和法器清单，不是观测CSV |
| binding.json | 候选、流程、任务、活动参数、法器属性及版本声明的来源绑定 |
| source_joint_report.json | 导出时的完整源报告字节快照 |
| pack-manifest.json | 固定文件摘要；工作协议与观测不纳入固定文件列表 |
| README.md | 包内简要说明 |

check-pack复核固定文件、重新核验源报告，并检查可编辑协议中的候选ID、活动版本、环节顺序和法器身份仍对应源选择。目标定义、抽样、容差和校准条件可以补充；未知法器版本可填写，已在导出时声明的版本变化则应导出新包。仅修改源报告的passed标志不能绕过核验。

input_from_previous_output默认false、校准条件默认natural_case是待研究者核实的模板设置；物理通信链不自动等同于二元标签传递。目标标签定义、真实来源、方法审核、独立抽样和容差均保持待填。source验证通过只说明规划逻辑、指标及资源模型自洽，不证明该虚构场景的现实精度。

## 从自己的网络导出

根目录运行。可先用本轮留存的源报告复现四环节导出，输出路径必须不存在：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.design_observation_bridge --joint-report docs/task-flow/joint-optimization/stage3-design-collection/evidence/task_T2/source_joint_report.json --task-id T2 --output-dir generated/task-flow/my-T2-collection
.\.venv\Scripts\python.exe -B -m workflow_tools.design_observation_bridge --check-pack generated/task-flow/my-T2-collection
```

将joint-report参数换成自己的联合组网输出即可。省略task-id导出所选网络的全部任务；可重复该参数选择多个任务。导出任务子集不重新优化网络，也不将源三任务网络的最优性说成新单任务问题的最优性。

如已取得真实版本，可另建JSON，例如键为实际法器ID、值为实际版本字符串，再加 `--artifact-versions-json path/to/versions.json`。未提供的所选法器仍是UNASSIGNED，引用未选法器会拒绝。

复制或重新导出工作包后填写protocol.json，保留各实例和法器绑定，执行check-pack；再按[真实观测接口说明](../stage3-observation-intake/README.md)冻结研究协议、采集并分析observations.csv。check-pack不会读取真实标签或自动开展统计分析，不代表数据已采集。

## 验收

45项相关回归通过，包含9项新增接通测试；另有4组实际链路验收：三任务全部导出、T2单独导出并进入空数据检查、预算8来源保持not_proven、篡改指标但伪造passed仍被拒绝。[验收汇总](evidence/verification.json)及[测试日志](evidence/regression-tests.log)归档。

测试还覆盖不覆盖已有目录、可编辑研究字段与固定分配的区分、重复类型不合并、未知真实版本不伪造、身份冗余字段的一致性，以及采集清单不能误当观测CSV。完整性与源码摘要见[manifest.json](manifest.json)。

现在已接通“任务流程→法器链网→采集计划→真实观测验收入口”的软件路径。尚缺的是实际研究定义、真实版本与逐样本观测；本轮不生成它们，不改变sequential_product_v2，domain_validity继续pending。
