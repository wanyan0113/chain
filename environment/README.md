# 环境模块1.1

本模块负责环境参数定义、观测记录、读取、校验和查询。命名空间为 `urn:environment:`，不导入 `killweb_ontology.ttl`，也不引用任务、法器、能力或链网类型。

文章截图第一部分表 1 只定义了态势概念，没有提供完整环境参数表。本目录的字段均为项目数据管理结构，不宣称是文章原始属性。未预设元素种类、环境数值、阈值、修正公式或场景实例。

三部分共 24 张截图的覆盖情况见 [内容涵盖对照](内容涵盖对照.md)。第三部分增加了具体设计规则，但装备、目标、资源和评价数据不属于环境参数，未移入本模块。

| 文件 | 内容 |
| --- | --- |
| `ontology.ttl` | 参数、观测、区域三个类及通用字段 |
| `profile.json` | 字段数量、类型和引用类型的校验配置 |
| `loader.py` | 加载、附加语义校验、按参数/区域/时间查询 |
| `__init__.py` | Python 模块入口标识 |

## 数据约定

参数 `env:Parameter` 需要稳定 IRI、名称 `env:name`、含义 `env:definition`、来源 `env:source` 和值类型 `env:valueDatatype`。数值参数另需 `env:unit`；无量纲用字符串 `1`。参数定义与观测可放在不同 TTL 文件中，一起加载。

参数与观测均可使用 `env:sourceLocation` 记录章节、表号、页码或截图文件名。它是可选的非空字符串，可有多条；来源说明仍须填写，定位信息不能替代来源。

值类型支持 XSD 的整数类型、decimal、float、double、string、boolean 和 dateTime，必须以完整 IRI 声明。观测值类型须与声明精确一致，数值须有限。参数表示固定计量口径；模块不转换单位。同一参数需要使用不同单位时应先统一口径或使用不同参数标识。

观测 `env:Observation` 需要稳定 IRI，并包含：

| 属性 | 要求 |
| --- | --- |
| `env:parameter` | 一个已定义的参数引用 |
| `env:value` | 一个与参数值类型一致的 RDF 字面量 |
| `env:observedAt` | 一个带时区的 xsd:dateTime 时间点 |
| `env:area` | 一个已定义的区域引用 |
| `env:source` | 至少一条非空来源说明 |

区域 `env:Area` 需要稳定 IRI 和非空 `env:name`。区域按 IRI 精确匹配，不计算空间包含关系。记录时间是观测时间，不表示持续有效期。时间查询包含起止边界，比较时统一到 UTC。

所有记录必须显式声明类型；只跟随具名子类继承。字段不完整、类型不符、空字符串、空白节点标识、无时区时间和未知环境字段会报错。不会自动加载 `owl:imports`，也不会修改输入文件。

## 使用

依赖根目录 `requirements.txt`。从仓库根目录运行，以下路径是使用者自行提供的真实数据路径，本目录没有附带样例数据：

```powershell
.\.venv\Scripts\python.exe -m environment.loader <参数定义文件.ttl> <观测数据文件.ttl>
```

可选查询参数：`--parameter <参数完整IRI>`、`--area <区域完整IRI>`、`--from-time <带时区ISO时间>`、`--to-time <带时区ISO时间>`。

标准输出是 JSON，包含 `count` 和 `observations`；校验信息写入标准错误。值保持字符串形式并附数据类型，避免隐式数值精度损失。退出码：0 为校验及查询成功，1 为数据校验失败，2 为读取或查询错误。没有观测数据会校验失败；查询未匹配任何记录会正常返回空列表。

观测结果新增 `area_name`、`source_locations` 和 `parameter_definition`。后者包含参数含义、类型、单位、定义来源及其定位；观测自身的 `sources` 单独表示观测值来源。原有 1.0 输出字段保留。

尚无观测数据时，可独立检查和查询参数定义：

```powershell
.\.venv\Scripts\python.exe -m environment.loader <参数定义文件.ttl> --definitions-only
```

此模式至少需要一个有效参数定义，输出 `count` 和 `parameters`，只允许 `--parameter` 过滤。如果同时提供了观测数据，观测仍会校验。它不会把空输入当作成功，也不创建默认参数。

Python 接口：先调用 `load_environment(paths)` 获得图和报告，确认 `report.counts()['ERROR'] == 0` 后，再调用 `query_observations`。单独处理参数目录时使用 `load_environment(paths, require_observations=False)` 和 `query_parameters`。参数及区域过滤使用 RDFLib 的 URIRef，时间过滤使用带时区 datetime。

连接模块的场景背景模式使用 `load_environment(paths, require_observations=False, require_parameters=False)`，允许只提供区域或无环境数据。独立环境命令行的默认验收和 `--definitions-only` 要求不变；无论哪种模式，已提供的参数和观测都不会跳过校验。

## 范围

本模块复用根目录 `validate_data.py` 和 `check_ontology.py` 的通用校验函数，因此依赖仓库公共工具，但不依赖主本体文件。若需作为另一项目的独立包发布，仍需一并打包公共工具，目前未完成包发布。

主本体连接现由独立的 [integration 模块](../integration/README.md) 实现，环境模块本身不导入该模块。不处理环境对任务、能力或法器的影响，也不生成或评价链网。

本次没有添加真实参数定义或观测文件。具体参数、单位和来源应由研究资料或实际数据确定；来源字符串只用于追溯，程序不验证其真实性。
