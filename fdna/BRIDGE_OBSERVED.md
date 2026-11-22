# 已登记拓扑与外部观测SE接入

`fdna.bridge_observed`将上游报告、已登记映射和已有节点SE直接接到拟合器。与模拟演示入口不同，它不调用生成器、不接受 `predictor_values` 或 `predictor:`列、不重建干净值或共同状态。

在仓库根目录运行，输出目录必须不存在或为空：

```powershell
python -m fdna.bridge_observed --report data/fdna/bridge_intake/source_report.json --mapping data/fdna/bridge_intake/mapping.json --samples data/fdna/bridge_observed/samples_observed.csv --config data/fdna/bridge_observed/fit_observed.json --output-dir generated/observed-bridge
```

示例文件是上一轮保存的240条**模拟观测**，不是实测数据。JSON与CSV宽表表达同一组记录；示例只移除干净供给字段，没有重新抽样。

## 实际接入要求

- 映射必须通过 `bridge-review-1` 审查校验并绑定源报告；API与CLI采用相同导出门槛。
- 拟合配置必须显式设 `input_role=observed`，并声明 `data_kind=simulated` 或 `user_supplied_se`。后者仅表示外部提供的数据，软件不据此认证来源真实或完成校准。
- SE使用明确能力实例编号，不能将共享装备的一个数值自动复制给多个任务产品。原始指标、方向和归一化须由研究者定义；本入口接收[0,100]SE，不自动将链排名分数当SE。
- 支持JSON、CSV宽表和 `--layout long` 长表。记录含sample_id、base_case_id、scenario_id、fit_group、split；训练/留出划分由外部提供并检查同一基础样本是否泄漏。
- 缺失值可显式留空；不补零。未知节点、非法SE和混入干净供给字段在优化前报错。读取期间文件变化也会被拦截，避免输入快照与实际拟合数据不一致。

输出保存原始SE文件及哈希、规范化记录、映射与指标对照、参数及诊断、预测指标、排除记录、递推和节点退化结果、三组PNG/SVG图表。数值预测不裁剪，失败节点和越界保留。

## 解释边界

这是观测输入的普通拟合，不是测量误差校正。供给SE本身可能有测量误差，参数偏差与可辨识性需要另外研究；低留出误差不证明参数唯一。

没有共同来源观测模型时，不从共享装备身份反推潜在状态，也不复用模拟器内部H生成共同来源情景。标准节点退化仍是拟合模型内的推演，不证明真实因果效应。

配套接口与拓扑登记说明见 [BRIDGE_INTAKE.md](BRIDGE_INTAKE.md)，统一SE契约见 [NETWORK_WORKFLOW.md](NETWORK_WORKFLOW.md)。
