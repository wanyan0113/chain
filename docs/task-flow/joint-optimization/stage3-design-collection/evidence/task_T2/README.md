# 从所选法器网生成的数据采集包

此目录只有计划和身份清单，没有生成真实测量。原始设计通过独立核验，不代表精度领域有效。
填写protocol.json及observations.csv；保留其余文件，以便核对任务、活动、法器及来源。
stage-roster.csv是环节清单，不是观测数据。UNASSIGNED表示真实法器版本尚未提供。
规划快照及其指标是设计输入，不是实测精度或实际固件版本。
填写目标标签、总体、标签来源、方法审核、版本、抽样和容差后，再冻结研究协议。
input_from_previous_output及calibration_input_condition是可编辑采集假设，需要按实际研究核实。
运行workflow_tools.design_observation_bridge --check-pack检查身份；实际统计另用validate_accuracy_observations。
