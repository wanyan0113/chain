# 跨流程联合网络设计

结论：bounded_global_optimum
资源核验：passed
流程搜索完整：True
候选链生成完整：True
综合精度业务有效性：pending

| 任务 | 活动顺序 | 法器顺序 | 时间/秒 | 精度乘积 | 成本 | 统一评分 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| T1 | observe → analyze_secondary → decide → stabilize | R-Survey → R-Analysis-Backup → R-Dispatch → R-Seal-D | 15 | 0.9411900299999999 | 9 | 0.7354959630911188 |
| T2 | observe → analyze_secondary → decide → safety_audit → stabilize | R-Survey → R-Analysis-Backup → R-Dispatch → O-Safety-Auditor → R-Seal-B | 12 | 0.9079572892499999 | 11 | 0.8126912009692564 |
| T3 | observe → analyze_secondary → phase_tune → decide → safety_audit → stabilize | R-Survey → R-Analysis-Backup → O-Phase-Tuner → R-Dispatch → O-Safety-Auditor → R-Seal-C | 16 | 0.9319942638022499 | 10 | 1.0 |

网络平均评分：0.8493957213534583
网络跨度/秒：16
总成本：30
候选与证据身份详见joint_report.json；不代表真实执行或无限范围最优。
