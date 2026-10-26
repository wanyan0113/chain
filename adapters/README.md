# 来源与环境适配

来源 RDF 适配只输出来源本体通用类，不根据表号或字段名生成业务属性。`source_to_rdf(bundle)` 校验 `adapter_mappings/source_metadata.json` 与固定契约一致后返回图。命令行由 source_data.cli 提供。

环境适配独立于来源导入。`raw_tables.json` 不符合环境输入契约，会被拒绝。当前已有区域 TTL 直接复用：

```powershell
.venv/Scripts/python.exe -m adapters.environment_to_rdf data/article_environment/environment.ttl --output-dir generated/article_environment
```

输出 environment.ttl 和 environment_report.json；本批实际计数为 1 区域、0 参数、0 观测。保留原有 rdfs:seeAlso 来源定位；不把它自动改指向一个新来源材料。区域不能使用 env:source，不因同图加载就获得任务—区域关联。

未来外部环境 JSON 使用 `--format json`。完整机器契约在 `adapter_mappings/environment.json` 的 schema 中，顶层四个必填键为 format_version（固定 1.0）、areas、parameters、observations，后三者允许空数组，已提供的对象必须完整。

| 对象 | 必填字段及类型 |
| --- | --- |
| Area | id 完整 IRI、name 非空字符串、source_refs 显式来源 IRI 数组 |
| Parameter | id、name、definition、datatype（受现有加载器支持的完整 XSD IRI）、unit（字符串或 null）、sources（至少一项非空字符串）、source_locations（字符串数组）、source_refs（IRI 数组） |
| Observation | id、parameter 和 area（输入中已定义对象的完整 IRI）、value、observed_at（带时区 ISO 时间）、sources、source_locations、source_refs、blocking_issue_ids（数组） |

单位和数值不自动转换。数值参数 unit 不能为 null，无量纲明确用 `1`。来源引用写 rdfs:seeAlso；参数/观测来源说明写 env:source 和 env:sourceLocation。source_refs 可引用包中的稳定来源 IRI，也可显式引用其他来源；程序验证 IRI 格式，不验证外部出处真实性或下载其内容。至少应按资料填写区域来源引用，空数组只表示未随适配输入提供，不能据此宣称已完成来源核验。

JSON boolean 必须使用 true/false；string/dateTime 值必须为字符串；数值可为 JSON 数值或明确的数值词法字符串，避免精度损失。整数小数不截断，NaN/Infinity 不允许，值类型必须与 Parameter 精确匹配。时间没有时区时报错。阻塞疑点不为空时整批适配失败，相关值继续保留在来源层；不产生部分成功观测。

`environment_to_rdf(document)` 返回校验通过的 RDFLib Graph；`load_environment_input(paths, input_format='turtle')` 支持复用现有本地 TTL，JSON 模式每次接受一个完整文件。JSON schema、引用、类型和既有环境加载器均通过后才能写输出。单独只有区域时调用 `load_environment(..., require_observations=False, require_parameters=False)`；参数目录与观测目录仍使用原加载器相应模式，不改变原含义。

分类检查并保存当前背景及严格观测结果：

```powershell
.venv/Scripts/python.exe -m adapters.check_project --source generated/article_source/provenance.ttl --project data/article_environment/scene.ttl --environment generated/article_environment/environment.ttl --links data/article_environment/links.ttl --output-dir generated/acceptance
```

此命令先独立校验来源、环境，再通过原有 load_project 联合加载并记录两种模式。只有 report 无错误才运行对应查询。输出 acceptance_report.json。默认验收成功时退出 0，即使严格模式因未提供观测而失败；严格结果在 modes.observations_required 中明确记录，不改写为成功。背景本身错误仍退出 1。无法读取或命令错误退出 2。诊断 stderr，结果 JSON stdout；已有输出须显式 `--overwrite` 才可替换。

可单独复核既有入口：

```powershell
.venv/Scripts/python.exe -m integration.load_project --project data/article_environment/scene.ttl --environment generated/article_environment/environment.ttl --links data/article_environment/links.ttl
.venv/Scripts/python.exe -m integration.load_project --project data/article_environment/scene.ttl --environment generated/article_environment/environment.ttl --links data/article_environment/links.ttl --require-observations
```

本批默认返回一条 background_only、observation 为 null；严格命令退出 1。报告的路径条数与独立观测数量分开统计。来源追溯由 source_data 查询，不扩充既有联合查询返回结构，不推测 hasObservation 或任务—区域关系。

技术验证：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

测试中的环境参数/观测仅在临时目录存在，明确为 technical fixture，不进入来源目录，也不作为真实观测验收。当前交付没有完整业务实例、自动设计、链网生成、指标评分或环境作用计算。
