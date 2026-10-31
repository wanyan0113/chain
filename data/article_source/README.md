# 共鸣案例资料

`case_records.json`是按参考文章案例迁移名称后的输入，`case_review.json`保存核对统计，`source_mappings/case_fields.json`配置表与字段读取。当前使用R-Golem、R-Gryphon、R-Rune、R-Eye、R-Sigil、B-Mirror、B-Eidolon、B-Orb；现实身份的历史对应保存在根目录命名对照表。identity_mapping中的键和值现均为当前代号，只作登记，不用于实体推理。

数值、单位、坐标字符串、数量、承载关系及疑点保持：海里、英尺、节、百分比不会因显示名而变成其他单位。现有13项疑点及3项缺口没有因改名而解决。坐标N/E与列名冲突、D/E/F作用距离上下界倒置、严格释放高度条件与0/0值冲突仍保留。不新增环境观测，不以结果反填输入。

当前资料可导入来源包并查询；尚未映射为完整业务本体实例。来源模块的original/raw意为当前输入原值，不表示当前文件仍使用历史现实名称。
