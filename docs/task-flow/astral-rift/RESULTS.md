# 星界裂缝实验：实际验收结果

依据[实施文档](README.md)运行。以下是实际结果，不是预期替代。生成、重放与独立参考均在本地执行；外部服务、旧链网及资源调度未运行。

## 生成与边界测试

| 场景 | 实际状态/结论 | 首解活动 | 步数 | 合成延迟/秒 | 尝试节点数 | 预期符合 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | candidate_found | S1→S2 | 2 | 5 | 21 | 是 |
| T2 | candidate_found | S1→S2→D1→D2 | 4 | 14 | 68 | 是 |
| T3 | candidate_found | S1→S2→D1→D2→A1→A2 | 6 | 22 | 183 | 是 |
| T4 | candidate_found | S1→S2→S3→D3 | 4 | 10 | 82 | 是 |
| info_known | candidate_found | S2→D1→D2→A1→A2 | 5 | 20 | 305 | 是 |
| verified_start | candidate_found | D1→D2→A1→A2 | 4 | 17 | 210 | 是 |
| plan_start | candidate_found | A1→A2 | 2 | 8 | 11 | 是 |
| goal_met | candidate_found | 空流程 | 0 | 0 | 0 | 是 |
| inconsistent_history | input_rejected，未规划 | 未生成 | — | — | 未运行 | 是 |
| permission_false | exhausted / bounded_no_solution | 未生成 | — | — | 616 | 是 |
| permission_unknown | exhausted / blocked_unknown | 未生成 | — | — | 616 | 是 |
| T1_permission_false | candidate_found | S1→S2 | 2 | 5 | 21 | 是 |
| length_limit | exhausted / bounded_no_solution | 未生成 | — | — | 160 | 是 |
| node_limit | limit_reached / node_limit | 未生成 | — | — | 1 | 是 |
| time_limit | limit_reached / time_limit | 未生成 | — | — | 0 | 是 |
| delay_equal | candidate_found | S1→S2→D1→D2→A1→A2 | 6 | 22 | 183 | 是 |
| delay_insufficient | exhausted / bounded_no_solution | 未生成 | — | — | 640 | 是 |

17组生成测试均符合预期，10组实际生成候选。其余为预期的拒绝、无解、未知或超限，不混算生成成功率。所有生成候选均通过独立重放和独立领域参考检查，累计延迟一致。

time_limit使用确定性测试时钟，仅检查超限分类；其elapsed_ms不是实际运行耗时。其余场景的搜索时间保存在各记录statistics.elapsed_ms，与业务合成延迟分开。节点差异受初态下仍合法的额外活动及搜索次序影响，较短首解不必然尝试更少节点。

## 错误流程与合法长流程

7条错误或篡改流程被独立验证器拒绝：未获取即核验、缺方案即稳定、禁止条件下稳定、缺稳定即确认、重复活动、域外参数、篡改候选最终状态。逐项失败位置及原因见[验证探针](evidence/verification-probes.json)。

T1的S1→S2→S3→D3合法延长通过，未将非最短误判成非法。

## 完整枚举对照

| 任务 | 独立检查原始序列数 | 合法序列数 | 最小活动数 | 最小合成延迟/秒 | 集合及延迟核对 |
| --- | --- | --- | --- | --- | --- |
| T1 | 109601 | 55 | 2 | 5 | 一致 |
| T2 | 109601 | 46 | 4 | 14 | 一致 |
| T3 | 109601 | 21 | 6 | 22 | 一致 |

独立参考共重放328803条不重复活动原始序列；122条合法序列均逐个通过独立验证，实例身份、参数、版本和最终累计延迟一致。完整枚举没有按最终状态合并不同活动序列。最小性结论只适用于本固定模型，不表明系统实现了一般时长优化。

## 复现

```powershell
.\.venv\Scripts\python.exe -B -m task_flow plan --catalog task_flow/examples/astral_rift/catalog.json --request task_flow/examples/astral_rift/requests/T3.json --verify

.\.venv\Scripts\python.exe -B -m workflow_tools.astral_rift_experiment run --output-dir generated/task-flow/astral-rift-run-1
.\.venv\Scripts\python.exe -B -m workflow_tools.astral_rift_experiment verify --output-dir docs/task-flow/astral-rift/evidence
.\.venv\Scripts\python.exe -B -m unittest tests.test_astral_rift
```

输出目录拒绝覆盖。16份合法请求可单独读取；不一致历史的原始案例只保存在scenario.json中，因适配拒绝没有导出为合法请求。time_limit的测试时钟由专项脚本注入，单独运行其请求文件不等同于确定性时钟测试。

[机器摘要](evidence/summary.json)与同目录完整记录保存输入、定义、版本、搜索、每步重放、错误定位和参考集合；[相关测试日志](tests.log)记录本轮189项通过，含11项新增案例测试。证据含完整枚举的逐候选重放轨迹，文件较大，可通过脚本复现。

本轮没有改生产规划器、状态语义、验证器或旧系统模块。此案例增加一个虚构领域的有限实验见证，不代替独立人工需求复核、实际执行或系统接口集成验收。
