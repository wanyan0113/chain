# 给定网络的FDNA参数拟合与依赖分析

V1.0已完成交付收尾，安装、验收和冻结说明见[V1.0交付说明](V1_RELEASE.md)。

本功能在正式基线 `288f590` 上开发。GitHub分支将应用模块整合进fdna包，不复制一套完整源码。桌面工作区的fdna_workflow目录仍保留，原七节点实验和剩余16组诊断状态不变。

本次新增的主线包括3/5输入核心验证、统一网络与SE数据入口、11节点18边独立生成演示，以及参数、预测、节点退化和传播路径四层分析。代码不会从拓扑推断真实依赖结构，也不会把生成机制系数或表7参数当作待拟合参数真值。

Word验收对照及剩余交付工作见[方案状态核对](PLAN_STATUS.md)。当前四项主线已有代表性运行产物，剩余重点为输入验收示例整理和文档口径统一；16组深入诊断继续暂缓，真实数据校准另行开展。

## 数据填写与拟合前检查

先用给定网络生成空白SE长表、宽表和JSON模板，再填写多次实验记录。模板不包含假造的SE，也不会自动归一化原始指标。

```powershell
python -B -m fdna.network_preflight --network data/fdna/workflow_demo_network.json --templates --output-dir generated/汇报用/25_真实SE输入准备/填写模板
python -B -m fdna.network_preflight --network 网络拓扑.json --samples 已填写SE.csv --config 基础式拟合配置.json --output-dir generated/汇报用/输入预检查_新运行
```

预检查不运行优化器，报告每组每节点的有效训练/留出行数、独立基础实验数、不同供给组合数、缺失记录、常数输入和训练范围外的留出输入。ready仅表示满足基本样本条件，不证明参数可辨识；needs_data表示至少一个节点的有效训练行数不足。输出警告仍需在拟合后的分支与约束诊断中继续检查。

## 使用统一入口

在仓库根目录运行，使用已安装requirements-fdna.txt与matplotlib的Python环境：

可用 `python -m pip install -r requirements-workflow.txt` 安装工作流依赖。新克隆没有本地汇报数据时，先运行 `python -B -m fdna.workflow_demo --output-dir generated/汇报用/22_11节点完整流程演示` 生成演示输入和结果，再使用下列统一入口示例；已有数据目录不会被覆盖。

```powershell
# Set-Location 到仓库根目录
python -B -m fdna.network_workflow --network 'generated\汇报用\22_11节点完整流程演示\输入_拓扑.json' --samples 'generated\汇报用\22_11节点完整流程演示\输入_SE宽表.csv' --config 'generated\汇报用\22_11节点完整流程演示\输入_拟合配置.json' --output-dir 'generated\汇报用\网络拟合_新运行'
```

长表另加 `--layout long`；JSON记录直接读取，不需要layout设置。输出目录必须不存在或为空，避免覆盖历史结果。

网络JSON只有 `id`、`nodes`、`edges`；每条边只有 `feeder`、`receiver`。第一版支持DAG，拒绝环路、自指、重复边、未知节点。网络提供候选供给关系，拟合仍需要多条对应的节点SE记录。

CSV宽表的必需元数据为 `sample_id,base_case_id,scenario_id,fit_group,split`，其余列为节点编号，取值为0到100的SE。长表使用相同元数据及 `node_id,se`。相同基础实验的衍生记录必须同属训练或留出；`split`只允许 `train`、`holdout`。

`fit_group`决定哪批记录共用一套参数。默认 `pool_scenarios=false`；同组跨多个场景时，只有明确同意场景共享参数才可设为true。缺失值留空或null，不补0；每个接受节点保存自己的排除记录。训练样本不足的节点保留失败状态，其下游递推标为受阻。

真实数据使用 `input_role=observed`；供给和目标均来自实测SE，不假装有干净真值。模拟自检使用 `input_role=clean_predictors_observed_targets`，宽表另外提供 `predictor:节点编号` 列，或长表提供 `predictor_se`。节点目标仍取观测SE。输入测量误差尚未建模，指标方向和归一化须由数据提供方明确。

模型设置支持基础式、固定δ和自由δ，保持α∈[0,1]、β∈[0,100]；δ按接受节点登记，不按样本估计。使用中性与随机多初值，只按训练损失选解；预测不裁剪。

## 已运行的汇报产物

- `generated/汇报用/21_多输入核心验证`：72次拟合、3张PNG及SVG、参数恢复表、逐条预测和完整诊断。
- `generated/汇报用/22_11节点完整流程演示`：11节点18边网络、独立机制、三种输入示例、90次局部拟合、5张PNG及SVG、边参数、节点状态、退化影响和传播路径。
- `generated/汇报用/23_统一入口与验收`：回归测试、独立标量复核、输入契约和代码快照。

PNG用于汇报，SVG用于缩放编辑；没有PDF。图中重复分布和样本SD均不是置信区间。

## 复跑与验证

```powershell
python -B -m fdna.multi_input_recovery --config data/fdna/multi_input_recovery.json --output-dir 'generated\汇报用\多输入验证_新运行'
python -B -m fdna.workflow_demo --output-dir 'generated\汇报用\11节点演示_新运行'
python -B workflow_tools/run_workflow_checks.py
python -B workflow_tools/verify_workflow_results.py
```

多输入正式配置在开发试运行后冻结：3次独立重复、384训练/192留出、σ=0/2、12起点。恢复组检查无噪声参数恢复；诊断组检查约束不足告警和预测/扰动记录，不要求任意参数唯一恢复，也不要求当前有限扰动必然可行。诊断组留出误差为零不是验收条件。含噪组不要求精确参数恢复。

基础式已知参数自检使用δ=0生成目标，固定/自由δ自检使用相同δ=-3目标。因此自检图不作为跨不同目标的模型优劣对照。11节点独立机制演示中的三模型则使用完全相同的目标与划分。

## 分析边界

局部满秩不证明全局唯一；有限扰动仅说明当前输入上的预测响应。触界、约束不足和多初值参数跨度均随结果输出，不以单个α或β值给出确定性重要度排名。

退化分析固定模型与根输入，明确暂停被干预中间节点的入边，重新计算后代。基准为各组按sample_id排序的首条留出记录；保留比例为0与0.5，不根据响应挑样本。结构路径可能数值不活跃，多路径影响不简单相加。训练输入范围外的推演会被标记；该模型内情景推演不自动等同于真实因果效应。

### 多基准补充分析

分析配置可额外指定`baseline_policy`：默认`first_holdout`保持首条基准；`all_holdout`使用全部留出；`evenly_spaced_holdout`配合整数`baseline_count>=2`按排序后的整数等距索引选择，包含首尾，样本不足时全取。选取不使用目标或响应。缺失或越界的被干预节点基准保留跳过状态，不据结果换样本。结构路径每个任务只导出一次。

已有拟合报告可直接做多基准重算、汇总、生成3张PNG/SVG及文字说明，无须重新拟合：

```powershell
python -B -m fdna.dependency_replay --report generated/汇报用/22_11节点完整流程演示/workflow_report.json --samples generated/汇报用/22_11节点完整流程演示/输入_SE记录.json --baseline-count 10 --figure-group-suffix sigma0 --output-dir generated/汇报用/26_多基准退化分析与复核
```

`--figure-group-suffix`只筛选图，原始响应与按组汇总仍覆盖全部任务；对自己的分组可省略该参数。分位区间只描述不同基准下的响应，不是置信区间或参数不确定性区间。退化推演不检查整条路径的联合训练覆盖，外推标记针对目标节点的直接供给。此扩展不会消除参数不可辨识和δ触界。

输入预检查现在对留出供给超出训练范围、独立基础训练实验数少于参数个数给出显式警告；历史第25包保留为当时版本的记录，新检查输出到新目录。所有分析配置在运行优化器前校验，避免拟合后才发现设置无效。

### 限制分支与传播解释

多基准响应生成后，可核对每个目标的SOD均值、各条COD候选、加δ前的最小值和退化前后分支变化，并输出3张PNG/SVG及文字说明：

```powershell
python -B -m fdna.branch_explanation --report generated/汇报用/22_11节点完整流程演示/workflow_report.json --samples generated/汇报用/22_11节点完整流程演示/输入_SE记录.json --replay-dir generated/汇报用/26_多基准退化分析与复核 --figure-group-suffix sigma0 --output-dir generated/汇报用/28_限制分支与传播解释
python -B workflow_tools/verify_branch_explanation.py --report generated/汇报用/22_11节点完整流程演示/workflow_report.json --samples generated/汇报用/22_11节点完整流程演示/输入_SE记录.json --branch-dir generated/汇报用/28_限制分支与传播解释 --output generated/汇报用/29_本轮分支解释验收/独立分支复核.json
```

只在唯一候选分支时报告局部导数：SOD分支为αᵢ/n；单条COD限制时对应输入为1、其他输入为0。容差内并列或近并列保存全部候选，仅列候选斜率范围，不挑一个唯一导数。容差单位为SE点，默认1e-8，参数可用`--tolerance`调整；不同容差的计数须分别报告。

局部零斜率不等于大幅退化无效，分支可切换；这些斜率不用于拆分有限干预的路径贡献。目标δ固定时只加到最小值之后，但上游δ可通过输入改变下游分支。COD候选超过100也不等于最终输出越界。

新模块检查拟合报告、SE数据、多基准结果的来源，复算每条响应及越界/外推标记；对分支不明或受阻的情况保留状态。基准节点表保留拟合诊断，不能把分支唯一解释成参数唯一。图中例子按首个末端、首组、首基准及最多两个直接供给节点的固定规则选取，不按误差或响应挑例子。没有有效响应的图表位置明确标注，不当作零。

现阶段完整流程能运行，不表示拟合误差、参数识别或输出范围问题已经全部解决。剩余16组诊断仍暂缓，真实数据校准待数据可用后开展。

开发时使用的本地来源Word与审计夹具不随新增功能提交；缺少该夹具的克隆环境会按既有规则跳过1项来源核验。方法与应用测试仍执行。正式结果与图表通过workflow_demo和workflow_reports命令重建。
