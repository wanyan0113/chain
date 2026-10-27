# 通用来源模块 1.0

资料管理层字段目录可从已校验的来源包生成：

```powershell
.venv/Scripts/python.exe -m source_data.cli dictionary generated/article_source/bundle.json --output-dir generated/field_dictionary
```

输出 `field_dictionary.md` 便于阅读，`field_dictionary.json` 保存完整原键、类型、单位说明和状态、输入路径、出处、缺失统计、字段级与表级疑点。重建时加 `--overwrite`。目录沿用已有来源描述，不把机器键冒充原表头，不推定对象身份，不加入业务本体对应或计算规则。

只读整理 JSON、保留原值及未决问题、导出来源 RDF、独立加载并查询。数据结构见 [CONTRACT.md](CONTRACT.md) 和 [schema.json](schema.json)。来源模块不导入主本体、environment、integration 或根目录工具。

从仓库根目录运行；Windows 使用已有虚拟环境：

```powershell
.venv/Scripts/python.exe -m pip install -r source_data/requirements.txt
.venv/Scripts/python.exe -m source_data.cli ingest data/article_source/case_records.json --mapping source_mappings/case_fields.json --output-dir generated/article_source --rdf
.venv/Scripts/python.exe -m source_data.cli validate generated/article_source/bundle.json --original data/article_source/case_records.json
.venv/Scripts/python.exe -m source_data.cli query generated/article_source/provenance.ttl --kind materials
.venv/Scripts/python.exe -m source_data.cli query generated/article_source/provenance.ttl --table article-source-v1/table/table9
.venv/Scripts/python.exe -m source_data.cli query generated/article_source/provenance.ttl --record 'article-source-v1/record/table9[3]'
.venv/Scripts/python.exe -m source_data.cli query generated/article_source/provenance.ttl --kind issues --status open
.venv/Scripts/python.exe -m source_data.cli query generated/article_source/provenance.ttl --kind unconfirmed
```

导入输出 bundle.json、validation_report.json；`--rdf` 另输出 provenance.ttl。已有输出默认拒绝覆盖，重新运行须显式加 `--overwrite`。也可先不加 `--rdf`，之后单独导出：

```powershell
.venv/Scripts/python.exe -m source_data.cli export generated/article_source/bundle.json --output-dir generated/source_rdf
```

标准输出始终为 JSON（帮助命令除外），诊断写 stderr。退出码 0 成功、1 数据校验失败、2 输入/命令错误。输出含 status 和 counts；查询另含 count 和结果数组。0 不代表疑点已经解决。原文疑点、资料缺口和结构错误分别计数。

本批预期 6 个材料定位项、4 张表、31 个字段定义、18 条记录、139 个字段值、13 项 open 疑点、3 项 open 缺口、3 条归档陈述（整个 identity_mapping 与两条 explicit_facts）。case_text 的组合截图定位保留原字符串，不拆造额外材料。表名和原表头未在输入中明确，保存 null；键名只是整理者的机器字段名。

`--material`、`--table`、`--record` 按来源 ID 精确过滤，未知 ID 报错。`--status` 仅用于疑点；默认查全部。查询行包含原对象、字段值、字段定义、材料出处和字段/表级疑点。表级完整性疑点关联到表，查询该表任意记录时一并显示；不会假装已经精确定位到所有单元格。未确认查询包含单位 unknown/unconfirmed、原值 null/missing 或带疑点的字段，因此也会包含缺少明确单位解释的普通归档字段，不等于每项均是独立原文错误。

Python 接口：

```python
from source_data.ingest import ingest
from source_data.validate import validate_bundle
from source_data.load import load_source, bundle_from_graph
from source_data.query import query_records, query_issues

bundle = ingest('data/article_source/case_records.json', 'source_mappings/case_fields.json')
result = validate_bundle(bundle, original_path='data/article_source/case_records.json')
assert result['counts']['ERROR'] == 0
graph, result = load_source(['generated/article_source/provenance.ttl'])
assert result['counts']['ERROR'] == 0
rows = query_records(graph, table='article-source-v1/table/table9')
restored = bundle_from_graph(graph)
```

来源 ID 与业务 IRI 不互换；相同代号出现在多张表时保持不同记录。数组位置用于来源定位，重排行后不承诺记录 ID 不变。源文件摘要追溯字节；完整解析快照追溯 JSON 值，不能恢复截图排版、千位空格或 JSON 数字字面写法。

本版是来源归档快照，不提供人工审核编辑器。由映射导入的疑点均保持 open，normalized_value 为 null；修改包中原值、引用、状态或规范化值会因与输入/映射不一致而拒绝。需要将来记录人工解决结论时，应先扩展显式审核输入契约，不能仅改生成的 JSON 或 TTL 来宣称已解决。已有身份对应作为 SourceStatement 原样归档，不输出 owl:sameAs。

来源 RDF 使用独立 `urn:source-data:`，每个对象保存类型、ID、完整 JSON payload 与显式引用。RDFLib 图迭代顺序不是 JSON 数组顺序；加载时重建稳定次序并逐项验证。一次加载一个完整包，可由多个本地 TTL 文件组成；多包联合归档尚未提供。来源查询会重新验证图，避免无效图部分返回成功。加载器不自动联网或解析 owl:imports。

独立迁移时仅需复制 source_data/、其 requirements.txt、自己的来源映射和来源输入；CLI 的导入、导出、加载、查询均无需 adapters/ 或其他项目目录。`adapters/source_to_rdf.py` 是仓库内的配置一致性封装，与独立 CLI 使用同一固定来源契约。

适配及项目背景接入见 [adapters/README.md](../adapters/README.md)。
