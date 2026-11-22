# V1.0 可复跑输入验收案例

有效数据取自已有独立机制11节点18边模拟的rep1_sigma2，250条观测、200训练和50留出；保留原划分，移除全部干净供给字段，按observed角色读取。仍是模拟数据，不是实测校准，也未建模供给测量误差。

cases.json列出每例输入文件、布局、预期状态与退出码。invalid表示拒绝结构或非法记录；needs_data表示有效训练数据不足；ready_with_warnings保留缺失、常数输入或覆盖警告，不表示参数可辨识。缺失值不补0。

从仓库根目录运行：

```powershell
python -B -m fdna.release_acceptance --output-dir generated/汇报用/V1验收_新运行
```

该命令逐例运行预检查、保存日志，核对宽/长表与JSON映射，执行一组observed完整拟合及样本不足的失败传播验收，输出图表和结果说明。输出目录不可非空。单例可用network_preflight入口和cases.json中的路径复跑。
