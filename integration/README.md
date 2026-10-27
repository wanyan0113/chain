# 环境与主本体连接模块

连接路径为 `任务 --kw:hasSituation--> 态势 --link:hasObservation--> 环境观测 --env:parameter--> 参数定义`。

接口现在区分两种验收范围：默认“场景背景接入”只要求有效的任务—态势关联；`--require-observations` 才要求完整观测路径。文章没有明确要求独立环境观测，不能把项目的严格观测要求当成文章缺项。

`kw:` 沿用主本体的 `http://example.org/arcaneweb#`；`env:` 沿用独立环境模块的 `urn:environment:`；新增关系使用 `urn:environment-integration:`。不复制核心类，不修改主本体或环境模块的类和标识符。

## 文件与来源

| 文件 | 作用 |
| --- | --- |
| `environment_mapping.ttl` | 声明唯一新增关系 `link:hasObservation`，从态势指向环境观测 |
| `load_project.py` | 显式加载各模块及实例，校验关联，查询完整路径 |
| `__init__.py` | Python 包标识 |

文章第一部分图 1、表 1 提供任务、态势、能力的概念背景；主本体已有 `kw:hasSituation`。`link:hasObservation` 是为连接数据新增的项目关系，不是文章原有字段，也不是环境影响规则。模块没有样例数据、场景、自动匹配或方案生成逻辑。

## 需要准备的实际数据

1. 项目实例文件：任务显式声明为 `kw:ArcaneMission` 或其具名子类，态势声明为 `kw:ElementalFieldSituation`；用 `kw:hasSituation` 从任务引用态势。
2. 可选环境文件：可以只提供资料给出的区域；有实际参数和观测时，按 `environment/README.md` 提供数据。未提供观测不必补造时间或数值；已经提供的参数、观测始终完整校验。
3. 可选观测关联文件：有观测时用 `link:hasObservation` 从态势 IRI 引用观测 IRI；两端必须使用前述文件中的同一个完整 IRI。仅包含关联边也可以，不必重复声明实例。

文件名由用户自行确定，加载器不依赖固定实例文件名。三个输入分组用于明确职责；实际是在全部文件合并后校验，因此允许跨文件引用。程序不移动数据，也不会自动写入或补全关系。

## 运行

从仓库根目录运行，以下占位符需替换为实际文件路径：

```powershell
.\.venv\Scripts\python.exe -m integration.load_project --project <任务态势文件.ttl> --environment <参数文件.ttl> <观测文件.ttl> --links <关联文件.ttl>
```

自动显式加载主本体、环境本体、连接本体。主本体默认为根目录 `arcane_ontology.ttl`，可用 `--ontology` 指定本地文件；不自动下载 `owl:imports`。其余数据均需自行提供。

只有场景背景时，`--environment` 和 `--links` 可省略：

```powershell
.\.venv\Scripts\python.exe -m integration.load_project --project <任务态势文件.ttl>
```

完整环境观测验收：在第一条命令后追加 `--require-observations`。此时无观测、无观测关联、路径缺口继续视为错误。

过滤参数：`--mission`、`--situation`、`--parameter`、`--area` 均接收完整 IRI；`--from-time`、`--to-time` 接收带时区的时间。时间过滤仅针对观测时间，不会比较任务时间或判断有效性。

标准输出为 JSON：`mode` 标明 `scene_background` 或 `observations_required`；`count` 是返回条数，`connections` 列出关联。

没有关联观测的场景返回 `connection_status: background_only`、`observation: null`，且 `path_properties` 只含 `hasSituation`。有观测的记录返回 `connection_status: observation_linked` 及完整观测信息。不会将两种状态混称为完整环境接入。

同一观测通过不同任务或态势被引用时，保留各条路径，不把共享记录计为不同观测。使用参数、区域或观测时间过滤时，无观测背景不参与匹配；过滤后没有结果时正常输出空列表。区域单独加载不等于已经与态势建立区域关联，也不代表区域有效性已验证。

校验信息写入标准错误。退出码 0 表示校验和查询成功，1 表示数据关联或环境数据校验失败，2 表示读取、查询或命令参数错误。指定的任务或态势不存在/类型不符时报告查询错误，避免将错误标识符当作正常空结果。

Python 接口为 `load_project(project_paths, environment_paths=(), link_paths=(), require_observations=False)`；检查返回报告 `report.counts()['ERROR'] == 0` 后，才调用 `query_project(graph, ...)`。严格模式在加载和查询时都传入 `require_observations=True`。IRI 过滤使用 URIRef，时间过滤使用带时区 datetime。

## 校验范围

- 复用完整环境校验，文件之间可以互相引用。
- 检查两条连接属性的对象属性声明、定义域和值域与约定一致；替换主本体或输入文件追加冲突声明时报告错误。
- 检查每条关联端点的稳定 IRI、显式类型或具名子类类型；只有被引用但未定义的节点不能通过。
- 默认至少存在一条有效任务—态势背景关联；完全没有背景关联仍报错。
- 无观测关联或态势缺观测时，默认提示警告；严格模式要求完整任务—态势—观测路径，缺口为错误。
- 未接入的任务、未被任务引用的关联态势、未被态势引用的观测给出警告；不擅自连线，不丢弃输入。
- 拼错的连接命名空间字段报错；`hasSituation` 仅按显式方向读取，不执行逆关系、等价关系或属性链推理。

本模块不校验任务全部业务属性，也不替代 OWL 一致性检查。它验证的是引用和类型，不会判定观测是否真实、在任务时刻有效、区域是否重叠，或据环境记录推导能力和法器选择。
