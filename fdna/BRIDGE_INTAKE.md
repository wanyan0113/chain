# 新装备报告的能力映射接入

新增 `fdna.bridge_intake`，支持草稿生成、审查校验与拓扑导出。只处理上游最终选中的 `single_unit_per_role` 三角色计划，不读取候选链的评分作为SE。源报告的角色成员与计划成员必须一致。

## 三个命令

在仓库根目录运行，各输出目录须不存在或为空：

```powershell
python -m fdna.bridge_intake draft --report design_report.json --output-dir generated/intake-draft
python -m fdna.bridge_intake check --report design_report.json --mapping reviewed_mapping.json --output-dir generated/intake-check
python -m fdna.bridge_intake compile --report design_report.json --mapping reviewed_mapping.json --output-dir generated/intake-compiled
```

`draft`成功表示生成了草稿，不表示可拟合。`check`返回0表示ready、2表示blocked；`compile`仅在ready时导出拓扑。源报告不支持的活动计划报错，不会强制转换。

草稿包含稳定的能力实例编号、角色模板提出的产品输入输出、依赖候选、共享主体清单及源报告摘要。所有提案默认待审。角色模板提出产品关系，不代表上游报告提供了实际产品消费证据。

人工确认或登记模拟假设时，需要在JSON中：填写SE指标定义与场景，确认状态身份，逐实例填写 `review_status=approved_assumption` 和 `review_note`；共享状态也逐项确认；确认各边的实际产品消费关系及设备连接，设定边状态；最后填写 `review_gate` 的状态、登记者与说明。明显占位文本、待审节点和漏登记的共享主体会阻止导出。保留的pending/rejected候选不会成为FDNA边。

CSV是审阅清单，当前不回读CSV编辑。`approved_assumption`表示明确登记的工作假设，不是软件确认了真实因果，也不是完成了专家实证审查。

新格式 `bridge-review-1`强制绑定源报告的规范JSON摘要；任何报告内容变化都会阻止旧映射直接使用。旧手工案例仍可走原演示入口，但不能作为新审查入口的未绑定配置导出。

## 接入SE与拟合

拓扑导出不会自动生成真实SE或数值机制参数。实际数据应另行定义指标与归一化，按既有网络流程配置 `input_role=observed`；模拟数据的生成机制、共同状态、条件和噪声也必须另行登记。

```powershell
python -m fdna.network_workflow --network generated/intake-compiled/输入_拓扑.json --samples node_se.json --config fit_config.json --output-dir generated/intake-fit
```

统一SE格式见 `fdna/NETWORK_WORKFLOW.md`。预测质量、参数约束、边界和外推仍分别报告。

需要保持源报告与映射绑定、直接读取外部观测SE时，使用 [BRIDGE_OBSERVED.md](BRIDGE_OBSERVED.md) 的完整入口；它不调用生成器，也不接收干净供给字段。

## 本仓库可复跑的合成演示

```powershell
python -m fdna.intake_demo --config-dir data/fdna/bridge_intake --output-dir generated/intake-demo
```

先生成待审草稿并确认被拦截，再使用独立保存的已登记合成假设导出，最后运行原有模拟与FDNA流程。登记示例继承 `bridge_full` 的假设并重编号，不是代码替用户自动审批。源报告变化样例也必须被拦截。结果含9个能力状态、6条供给边和24次局部拟合，仍有原模型的衰减表达与可辨识性限制。

目前支持的是三角色计划与显式任务产品状态；多装备活动计划、跨链产品供给、资源竞争与反馈模型尚未支持，不能声称任意装备网均可全自动转换。
