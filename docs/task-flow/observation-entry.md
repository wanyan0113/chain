# 统一观测入口1.0

现在可使用`python -B -m workflow_tools.observation_entry`处理不同版本的观测数据。统一的是命令、路由与报告外层，内部仍调用原来的处理器；没有转换旧文件、改评分公式或改冻结契约。所有原命令继续可用。

这里统一的是**观测数据入口**，不是将规划任务、活动库、本体和观测数据混为一种输入。

## 版本与输入文件

| 文件中的明确版本 | `--input`含义 | 原处理器 |
| --- | --- | --- |
| `version=tfdo-observation-intake-1.0` | 旧单装备观测协议JSON；观测CSV用`--observations`提供 | `validate_accuracy_observations` |
| `version=tfdo-observation-study-2.0` | 带队列、实际尝试和独立终点的study JSON | `observations_v2` |
| `contract_version=team-observation-contract-1.0` | 装备组六表JSON或六表CSV元数据JSON | `team_observation_intake` |

不根据装备数、文件名或字段相似程度猜版本。版本缺失、未知、字段冲突或混入另一输入族字段时阻塞。可用`--expect-version`额外断言版本；它不能将旧文件变成新版。重复JSON键也不接受。

## 四种操作

| 操作 | v1 | v2 | 装备组六表 |
| --- | --- | --- | --- |
| `check` | 协议结构；若提供CSV则检查表头/列数 | study结构及来源定义 | 六表及协议Schema、CSV格式 |
| `import` | 原CSV导入与分析 | 原study分析，含独立终点 | 原六表导入及独立核验，包含真实OWL |
| `verify` | 不支持统一入口中的独立原始快照复核 | 不支持统一入口中的独立原始快照复核 | 支持，调用既有独立核验器 |
| `preflight` | 不支持 | 不支持 | 调用采集前登记预检 |

`check`不是完整语义核验。它不声明标签、来源能力、研究锁或数据关联已通过；具体范围记录在`scope`。例如六表格式通过后仍可能存在缺成员、跨样本关联等问题，必须看`import`及`verify`结果。v1/v2的既有参考审查工具仍可使用，但不被包装成尚不存在的完整独立快照核验。

不支持的操作返回明确诊断，不悄悄降级。与当前版本/操作无关的参数也会拒绝。冻结研究协议和研究锁仍使用各自原命令，统一入口不会自动冻结或开展采集。

## 使用示例

旧CSV导入（原文件无须改写）：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observation_entry import --input protocol.json --observations observations.csv --lock protocol-lock.json --output-dir generated/task-flow/v1-unified
```

旧版允许缺研究锁进入原分析并报告阻塞，统一入口保留这一行为，不会默认为已冻结。

v2导入：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observation_entry import --input study.json --joint-report joint-report.json --lock study-lock.json --output-dir generated/task-flow/v2-unified
```

装备组格式检查和正式导入：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observation_entry check --input packet/dataset.json --protocol packet/protocol.json --sources packet/sources.json --output-dir generated/task-flow/team-format
.\.venv\Scripts\python.exe -B -m workflow_tools.observation_entry import --input packet/dataset.json --protocol packet/protocol.json --sources packet/sources.json --lock packet/lock.json --output-dir generated/task-flow/team-unified
```

独立复核可传统一入口的导入结果目录，也可传原六表入口结果目录：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observation_entry verify --input generated/task-flow/team-unified --expected-receipt-hash <独立保存的回执摘要> --output-dir generated/task-flow/team-recheck
```

回执摘要取自`backend/intake_report.json`的`receipt_sha256`，采用既有规范化JSON哈希政策，不是直接计算回执文件字节哈希。应独立保管，之后复核时再提供；从同一目录当场读取摘要不能证明回执自身未被更改。不传摘要时仍做原始快照和关联核验，但没有这一独立锚点。

采集前预检（不是研究锁创建）：

```powershell
.\.venv\Scripts\python.exe -B -m workflow_tools.observation_entry preflight --input registration-plan.json --protocol protocol.json --sources sources.json --output-dir generated/task-flow/team-registration-check
```

## 统一报告与证据

每次选择不存在的新输出目录：

```text
entry-report.json     统一状态、版本、操作、诊断、检查范围及原报告位置
backend/             原处理器生成的报告、快照或回执，保留原格式
```

入口退出码0表示当前操作通过，2表示阻塞或输入错误。已有输出目录不覆盖，且不修改输入。原处理器报告中的业务状态、统计结果、来源最优性状态和领域边界不会被统一报告改写。

`entry-report.json.status=passed`只能结合`operation`与`scope`解释：格式检查通过不代表关联核验通过，登记预检通过不代表真实采集已获确认，统计计算通过不代表领域模型已验证。领域有效性继续为`pending`，不写回生产评分。

验收对照包括三版本直接调用与统一调用的实际结果、旧CSV原始字节、v2独立终点和六表规范化快照；装备组导入、独立复核及预检调用真实源核验和HermiT。仅对路由拒绝测试使用后端监视器，以证明错误输入没有误调用处理器。所有演示标签仍为合成数据。

历史v1锁在首次CLI演练中因实现摘要已变化而按原政策阻塞，原锁与阻塞报告保留。后续演练使用另外创建的当前版本合成回顾性测试锁，不将其当成真实前瞻研究锁，也不改写历史来源。[本轮验收与日志](observation-entry-evidence/verification.json)独立归档。

最终验收共74项不同测试通过，其中统一入口新增20项、既有回归54项；8组独立进程CLI演练通过，包含三版本导入、装备组格式检查/复核/预检及两组明确拒绝。完整日志、原始输入和报告均归档。
