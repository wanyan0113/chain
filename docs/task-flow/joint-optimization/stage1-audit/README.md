# 阶段一：实现审计与可复现性

审计日期：2026-11-28。受审提交：`c17c515`（尝试最优网）。本轮在原 `codex/task-flow-planning` 分支工作，没有创建或切换分支。Git reflog记录该分支从 `ch/codex/fdna-bridge` 的 `67def62929619620452b60e395720918e64cbb52` 创建。

## 结果与范围

从已提交的1038个追踪文件导出独立副本，未复制未追踪目录；确认导入路径来自副本。28个实现文件的LF归一化摘要一致，15个原始摘要差异来自已声明的CRLF/LF转换；三份冻结设计文档的原始字节摘要一致。没有改写历史设计或实现manifest。

独立副本重跑8组真实推理与组网验收通过，候选身份、集合摘要、原始指标、统一评分、网络选择和结论与原运行语义一致。副本72项测试通过；修复后回归75项通过；最终专项5项通过。专项与回归存在重叠，不把这些数量相加作为独立测试总数。这是在同一主机和Python环境下的独立文件副本复现，不是跨平台或独立领域复核。

## JV-AUDIT-001：有效数量被验证器误拒绝

同单位的数量 `{"value":1,"unit":"1"}` 与 `{"value":1.0,"unit":"1"}`，生产条件判断接受相等，但原独立验证器拒绝。已将数量相等判断与生产契约对齐，标记验证器版本 `tfdo-joint-verifier-1.0.1`。标量类型判断保持原规则，没有引入浮点容差或单位换算。

专项包含生产器与验证器边界对照、真实HermiT入口、独立副本导出不改变HEAD与真实Git索引、拒绝覆盖已有副本。此次修复不改变精度公式、评分、权重、优化目标或资源约束；`sequential_product_v2` 的领域有效性仍为 `pending`。

## 证据

- [结构化审计](evidence/audit.json)及[8组重跑记录](evidence/snapshot-replay.json)
- [副本72项测试](evidence/snapshot-tests.log)
- [修复后75项回归](evidence/joint-stage1-regression.log)及[最终5项专项](evidence/joint-stage1-audit-tests.log)
- [本次修复源码摘要](fix-manifest.json)，与历史实现manifest分开保存

## 复现

在仓库根目录运行，副本和输出目录均须尚不存在：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.audit_joint_implementation --revision c17c515 --snapshot-dir tmp/stage1-new-snapshot --export-snapshot --replay-dir tmp/stage1-new-snapshot/generated/replay --run-replay --output-dir generated/task-flow/stage1-new-audit
.\.venv\Scripts\python.exe -B -m unittest tests.test_joint_audit
```

导出器使用临时Git索引读取指定提交，不改变当前分支或真实索引。审计脚本专门复现 `c17c515` 的已知边界并检查当前修复，不是任意版本的通用审计器。

阶段一软件审计已完成并记录上述修正。下一阶段为受控规模与性能实验，分别测量流程搜索、OWL推理、候选生成、网络优化和内存；领域精度验证留待阶段三。
