# 知识外置、本体推理与规则推理协作

本入口不需要AI API。本地HermiT负责逻辑推理，显式规则引擎负责业务条件，设计引擎负责组合、评分和资源优化。

## 分工

| 层次 | 文件 | 修改内容 |
| --- | --- | --- |
| 场景事实 | 设计JSON或Excel | 任务、目标类型、法器、距离、资源和来源 |
| 领域知识 | 主本体及 `knowledge/*.ttl` | 类、等价类、限制、能力个体及关系语义 |
| 业务规则 | `knowledge/*.json` | 数值阈值、适用条件和能力筛选 |
| 通用程序 | `design/` | 加载、推理、组合及导出算法 |

在现有字段、三种角色和规则运算符范围内，新增目标分类、具名能力或改变筛选阈值无需改Python。新增物理模型、输入映射、运算符或设计角色仍需要开发。

## 运行

安装 `requirements.txt`、`requirements-reasoner.txt`，确保Java在PATH中。在项目根目录执行，输出目录使用新目录：

```powershell
.\.venv\Scripts\python.exe -B -m design run design/examples/knowledge_driven_demo.json --output-dir tmp/knowledge-driven-run --require-canonical
```

示例全部为虚构工程知识，不是文章真实规则或完整案例复现。TTL通过 `targetType` 分类目标，通过 `hasTarget some ...` 分类任务，通过 `requiresCapability value <具名能力URI>` 推出需求；法器等价类通过 `hasCapability value ...` 提供能力和角色。外部JSON再核验目标类型、距离等条件，设计引擎生成链并优化。

## JSON配置

在设计输入增加：

```json
{
  "knowledge": {
    "ontology_files": ["demo-task-capabilities.ttl"],
    "rules_file": "demo-selection.rules.json",
    "source_refs": ["虚构工程演示知识，非文章原文"]
  }
}
```

文件名相对于项目 `knowledge/` 目录。`ontology_files` 和非空 `source_refs` 必填，`rules_file` 可选；主本体自动合并。只允许目录内本地TTL/JSON，不加载 `owl:imports`。规则文件必须且只能包含 `task_rules`、`artifact_rules` 两个数组，追加到内联规则，重复编号报错。完整设计仍保留两个内联字段，可填空数组。

任务可填 `ontology_types`、`target_ontology_types`，法器可填 `ontology_types`，类须预先声明。当前自动投影的任务属性是字符串 `attributes.target_type`；其他属性继续由显式规则读取，不自动成为OWL数据属性。规则可读取推理后的 `task.ontology_types`、`artifact.ontology_types` 和 `artifact.ontology_capabilities`。

新增能力使用完整URI，声明为具名个体并明确能力角色类型；规则结论使用该URI，无需在Python名称表登记。缺失正向类型/能力事实保持未知。`someValuesFrom` 只保证存在，不等于推出某个具名能力；指定能力使用有依据的 `hasValue` 公理。

## Excel

“设置”（综合Excel为“设计设置”）增加“本体知识文件”“业务规则文件”“知识来源”，值对应上述配置；多个本体文件以英文分号分隔。文件预先放在服务项目的 `knowledge/` 中，上传Excel只引用文件。

可增加“本体类型”表，表头为 `对象类型、对象编号、类型URI`。对象类型填“任务”“目标”或“法器”；目标行填写目标编号。原有“本体关系”表及法器角色列类URI仍支持。

配置后设计网页与CLI均自动执行HermiT。没有 `knowledge` 的旧输入仍用有限类型/关系推导，不启动Java。

## 输出与边界

额外保存 `knowledge_snapshot.json`（内容、编码、SHA256）、`reasoning_input.ttl`（知识与场景声明）、`reasoning_result.ttl`（回流的具名类型和关系）。报告记录知识版本、HermiT状态和任务能力依据；执行标识结合场景与知识，知识变化后旧报告不能导出。

回流范围为具名分类及 `requiresCapability`、`hasCapability`、`communicatesWith`、`orchestrates`，不是全部OWL蕴含的完整物化。复杂表达式由HermiT处理，当前重点验证等价类、交集、存在限制及具名值限制；尚未提供SWRL业务入口或属性链专门验收。

依赖缺失、超时、不一致或不可满足类会停止运行，不自动回退。证据可追溯到输入、公理集合和结论，但没有逐结论最小逻辑证明。距离、库存、数值比较与优化仍在程序中执行，不补造缺失参数。
