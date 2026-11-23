# 来源契约 1.0

此契约先于实现确定。通用来源代码只读取本目录、本地 JSON/TTL 及调用者显式提供的映射；不导入主本体、环境或根目录校验工具，不解析截图，不执行映射表达式。

`schema.json` 定义 bundle；`source_mappings/case_fields.json` 定义本次输入读取位置；`adapter_mappings/source_metadata.json` 定义 RDF 固定字段和引用。所有配置只允许声明式数据。

包包含 format_version、metadata 和 materials/tables/fields/records/values/issues/gaps/statements 八个对象数组。每个对象有全包唯一 id。metadata 保留输入文件名、SHA256 和完整已解析 JSON；完整快照用于逐项验证与恢复，包括顶层元数据。材料保存定位、核对日期及转录摘要；未知表名和表头为 null，不能把整理者的键名冒称原表头。

ID 为 `<mapping.namespace>/<对象类别>/<准确输入路径>`；字段定义使用表路径和键，数组记录使用零基索引。输入行重排会改变该位置的记录身份；不承诺跨版本实体身份合并。IRI 为 `urn:source-data:entity:` 加 ID 的 UTF-8 百分号编码（不保留特殊字符）。名称、业务代号均不参与自动同一对象推断。

值保存 raw_value、raw_type、missing_status、normalized_value、normalization_basis 和 issue_ids。null 只表示原 JSON null，不推断 unknown/not_applicable；缺失键不补值。缺失、未知、不适用、无量纲须由显式输入表达，不能以 null 混用。本版导入不产生任何规范化值。单位保存原说明与 confirmed/unconfirmed/unknown/not_applicable/dimensionless 状态，未知单位不自动当无量纲。

疑点状态 open/resolved/deferred 与 raw.status（原文政策文字）、source_alignment（转录核对）分开。导入的原文疑点均 open。具体字段路径关联值、行和字段；表级路径关联表，集合级路径关联归档陈述/缺口。找不到路径必须报错。处理依据必须为显式陈述 ID；规范化值非 null 时依据不得为空。

RDF 每个对象显式声明通用类，保存 src:id、src:payload（完整 JSON 对象），并输出配置中的对象引用。src:Bundle 的 payload 保存版本及 metadata；src:contains 列出所有对象。原类型在 FieldValue payload 中，JSON null/布尔/数值/列表/对象均可还原。加载器同时验证 payload、成员、类型、引用以及 JSON↔RDF 完全一致，拒绝悬空或被篡改的引用。payload 是可查询原值结构，不作为 env:value。

公开接口：

```text
ingest(input_path, mapping_path) -> bundle（无效映射抛 InputError，数据错误抛 ValidationError）
validate_bundle(bundle, *, original_path=None) -> report（status/counts/errors）
source_to_rdf(bundle) -> rdflib.Graph（无效包抛 ValidationError）
load_source(paths) -> (graph, report)（只读本地 Turtle；解析错误抛 InputError）
bundle_from_graph(graph) -> bundle（无效 RDF 抛 ValidationError）
query_records(graph, *, material=None, table=None, record=None) -> list[dict]
query_issues(graph, *, status=None) -> list[dict]
query_unconfirmed(graph) -> list[dict]
```

查询使用来源 ID 字符串；返回行对象附 fields、table_info、materials 和 issues，保留全部 JSON 原值。未确认查询覆盖疑点值、单位未确认及原值 null。查询前验证图；无效图不返回部分成功。CLI 标准输出 JSON（含 status/counts），诊断 stderr；0 成功（仍可有 open 疑点）、1 校验失败、2 读取/命令错误。

输出仅写用户目录。默认拒绝已有文件；显式覆盖前先完成导入与校验，暂存后逐文件原子替换。不保证跨文件事务；输入、源码、配置目录不得作为输出目录。
