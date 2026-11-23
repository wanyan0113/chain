# 06场景优化成果汇报：复核快照

本目录保存可以直接查看的图文成果，而不只保存生成脚本。依据截至e186575的实验功能与冻结结果整理。

- [图文说明](汇报图文说明.md)：6张图，每张配两段说明，GitHub可直接阅读。
- [横向6页PDF](优化成果汇报图文.pdf)与[本地HTML](汇报图文.html)。
- 图表目录包含3840×2160的PNG和可缩放SVG，图表数据目录保存未舍入的CSV。
- [数值复核](数值复核.json)：3770个CSV字段与源结果一致，18个来源指纹及94个源文件通过核对。

本轮复核收紧图3标题为“当前06场景未显示FDNA优于OLS”，明确数值近等阈值为1e-8，并说明已有留出集此前已查看，本次不构成新的独立测试。没有改变原FDNA公式、参数、SE、边界或正式图。

自由δ相对基础式的原有误差改善仍成立；不将OLS在当前场景的表现推广为所有数据和结构的方法排名。冻结预测的经验范围不是参数置信区间。结构预览没有实际应用新拓扑。

复跑绘图需保留项目本地generated输入，命令为：

```powershell
python workflow_tools/build_optimization_report.py --project-root <包含generated的项目根目录> --output-dir <新的空目录>
python workflow_tools/verify_optimization_report.py --project-root <同一项目根目录> --report-dir <图文输出目录>
```

Windows原生PDF逐页渲染脚本为workflow_tools/render_report_pdf.ps1；PDF共6页已渲染，修改后的第3页已目视检查。此快照可直接阅读，不要求联网调用推理器或拟合器。完整原始合成实验数据仍保存在本地来源包，源码仓库不重复包含全部事件数据。
