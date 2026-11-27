# 冻结基线的干净环境重建

结论：冻结提交 `9e3635a47c1a13a0e2de9221f10ce6f31d5157d6` 已在**同一Windows主机、同一Python 3.14.0构建的新虚拟环境**中复现。没有复制原虚拟环境或修改冻结源码。

这不是全新操作系统、跨平台、另一台机器或二进制逐字节重建证明。独立人工领域复核仍待执行，用户目前选择保持待复核，见[复核包](../domain-review/README.md)。

## 1. 重建方式

[重建脚本](../../../workflow_tools/rebuild_frozen_baseline.py)只依赖Python标准库，可使用基础解释器启动。为另一次运行选择全新目录：

```powershell
C:\Python314\python.exe -B -m workflow_tools.rebuild_frozen_baseline prepare --run-dir generated/rebuild-baseline-9e3635a-2 --base-python C:\Python314\python.exe
C:\Python314\python.exe -B -m workflow_tools.rebuild_frozen_baseline install --run-dir generated/rebuild-baseline-9e3635a-2
C:\Python314\python.exe -B -m workflow_tools.rebuild_frozen_baseline check --run-dir generated/rebuild-baseline-9e3635a-2
```

需要本地Git中存在冻结提交，并有下载冻结依赖的网络连接。prepare只接受workspace的generated子目录，拒绝覆盖已有目录。install可能需要执行环境的网络授权；本轮首次沙箱安装被WinError 10013阻断，获准联网后安装成功。这是网络权限失败，不是冻结版本不可用，原始失败及成功日志均保留。

实际运行目录是`generated/rebuild-baseline-9e3635a-1/`。完整源码归档、新虚拟环境及重跑实验保存在该本地ignored目录；Git发布精简后的完整结果摘要、命令、环境来源、语义对照和日志，见[evidence](evidence/rebuild-summary.json)。

## 2. 依赖与源码边界的处理

- 使用[冻结环境清单](../model-quality/environment-requirements.txt)中的21个精确版本。历史requirements-v1.lock的NumPy为2.5.5，冻结快照为2.5.4；本轮明确采用冻结快照的2.5.4，未静默替换版本，也未修改旧锁文件。
- 原本的范围归档只包含声明的通用子集，不是完整仓库分发。为了运行既有专项审查的完整回归，从**同一个冻结Git提交**导出全部已跟踪文件，没有拿当前工作树文件补齐，也没有修改冻结代码来绕过缺失。
- 新环境由基础解释器的venv创建，include-system-site-packages=false。安装采用isolated、no-cache-dir；最终包集合和版本与冻结快照完全相同，pip check无依赖冲突。
- 用新解释器显式运行所有测试，清除子进程继承的PYTHON环境变量，关闭用户site。模块来源证明显示：应用/测试代码来自导出的源码，第三方包来自新虚拟环境。
- 使用GIT_CEILING_DIRECTORIES阻断向上发现开发工作树的.git。导出不是Git检出，因此专项审查报告的baseline_commit为unavailable；实际来源由外层Git归档命令、冻结提交和源文件清单明确绑定，不能误读为父目录当前HEAD。

环境与来源记录见[environment-proof.json](evidence/environment-proof.json)、[setup.json](evidence/setup.json)和[安装采集说明](evidence/acquisition.json)。版本相同不等于构建产物逐字节相同；依赖清单尚未提供全部发行文件的哈希锁定。

## 3. 实际验收结果

| 项目 | 本次实际结果 | 证据 |
| --- | --- | --- |
| 冻结环境版本 | 21个包的集合与版本完全一致；Python构建一致 | environment-proof.json |
| 依赖一致性 | No broken requirements found | pip-check.log |
| 通用原型相关测试 | 159项通过，无跳过 | generic-tests.log |
| 前四阶段专项审查 | 23组预期符合；其中既有215项回归通过 | audit-summary.json、audit-regression-tests.log |
| 第五阶段通用集成 | 16项场景通过 | phase5-summary.json |
| 第六阶段研究实验 | 8模型6808条原始序列、34条接受序列；10配置30次效率记录完成 | phase6-summary.json |
| 合成报销审批 | 15场景符合预期，9场景选出流程，领域遗漏负例符合预期 | expense-summary.json |
| 与原证据语义对照 | 59份结构化记录全部一致 | semantic-comparison.json |
| 冻结源码完整性 | 执行前后892个归档文件均与Git导出逐字节一致 | rebuild-summary.json |

这些计数相互重叠，不能相加为“研究有效性分数”。23组检查通过也包含正确观测到拒绝和无法判断，并非所有模型都获准执行。

59份对照具体为：23份专项case、16份集成case、4份研究实验文件、15份报销case及1份领域规则变异负例。比较原始输入、状态/候选、验证、边界及非时间统计；没有只比较汇总数量。

仅忽略脚本中明确列出的7种计时字段。专项审查原有subject_id哈希包含时间，因此先按归一化内容重新计算subject_id并同步引用，再比较；不丢弃subject或调用记录。状态、预算、候选ID、目标、节点数和JSON类型均保留。布尔true与整数1仍不同。宿主环境汇总和文件manifest不要求逐字节相同，环境及源码版本另行核对。

## 4. 可解释的限制

本轮支持的是冻结程序在同机干净Python环境中的行为复现，不证明领域规则完整、方法新颖性、真实业务有效或旧链网系统已集成。跨平台/JVM/系统工具独立安装不属于此次新虚拟环境证明范围；没有将运行环境快照扩张为全系统部署保证。

完整本地实验输出保留在运行目录中。Git中的[manifest.json](evidence/manifest.json)绑定本次发布结果与重建工具来源，日志从原始shell编码转为UTF-8 LF，原始日志摘要保存在acquisition.json。该清单检测意外修改，不是第三方签名认证。

[重建工具测试](tool-tests.log)单独通过8项，覆盖计时归一化、JSON类型、候选/目标差异、subject引用、源文件改动、环境泄漏、路径边界和人工复核状态。它们属于新增工具检查，不属于被冻结基线的159项计数。
