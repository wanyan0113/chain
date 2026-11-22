"""Build the reporting index and archive the implemented workflow and its checks."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import shutil
import zipfile

root=Path(__file__).resolve().parents[1]
reports=root/'generated/汇报用'
out=reports/'23_统一入口与验收'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
tests=read(out/'测试摘要.json');audit=read(out/'独立数值复核.json')
recovery=read(reports/'21_多输入核心验证/report.json')
demo=read(reports/'22_11节点完整流程演示/workflow_report.json')
intervention=read(reports/'22_11节点完整流程演示/dependency_analysis.json')
core_checks=read(reports/'22_11节点完整流程演示/补充核验.json')
assert tests['passed']>=297 and tests['skipped']==0 and tests['failures']==tests['errors']==0
github=read(out/'GitHub提交记录.json') if (out/'GitHub提交记录.json').exists() else {}
figure_dirs=[reports/name for name in ('21_多输入核心验证','22_11节点完整流程演示','26_多基准退化分析与复核','28_限制分支与传播解释')]
figure_count=sum(len(list(p.glob('*.png'))) for p in figure_dirs)
replay_path=reports/'26_多基准退化分析与复核/多基准核验.json'
replay_audit=read(replay_path) if replay_path.exists() else None
replay_note=(f"新增多基准分析：{replay_audit['baseline_job_pairs']}个任务与基准组合，{replay_audit['effects']}条响应，{replay_audit['outside']}条输出越界，{replay_audit['extrapolated']}条目标供给外推。见第26包结果说明；这些结果不证明参数唯一或真实干预效应。" if replay_audit else '本地尚未生成多基准补充结果。')
branch_path=reports/'28_限制分支与传播解释/分支核验.json'
branch_audit=read(branch_path) if branch_path.exists() else None
branch_note=(f"新增限制分支解释：{branch_audit['baseline_receiver_records']}条基准节点、{branch_audit['baseline_edge_records']}条边局部斜率、{branch_audit['response_records']}条响应；{branch_audit['branch_changes']}条候选分支集合变化，{branch_audit['zero_responses']}条近零响应。见第28包图文及第29包独立复核。" if branch_audit else '本地尚未生成限制分支解释结果。')
flags={name:sum(name in l['flags'] for j in demo['jobs'] for l in j['local_fits']) for name in ('LOCAL_RANK_DEFICIENT','DELTA_AT_BOUNDARY','PREDICTION_OUTSIDE_SE_RANGE')}
errors={k:max(p['absolute_error'] for j in recovery['jobs'] if j['case']=='recovery' and j['noise_sigma']==0 for p in j['parameter_comparison'] if p['parameter']==k) for k in ('alpha','beta','delta')}
outside_intervention=sum(e['outside'] for e in intervention['effects'])
notes=f'''# 应用流程主线阶段汇报

截至2026年11月22日，已完成调整后四项近期主线的首版实现与代表性验证：3/5输入核心验证、统一输入接口、11节点18边独立模拟与拟合、依赖关系分析与导出。测试为{tests['passed']}项通过、无失败、无跳过。代码来源基线为288f590；应用模块已提交至GitHub分支codex/fdna-network-workflow，提交号见第23包GitHub记录；原有实验保留。

## 汇报时优先展示

1. 第21包的图01和图03：可辨识恢复组与不可辨识诊断组分别验收。72次拟合达到各自的预设条件；这不表示所有参数均已唯一识别。
2. 第22包的图01和图02：11节点18边由独立机制实际生成数据，再经统一入口逐节点拟合和整网递推。
3. 第22包的图04和图05：展示参数约束与边界问题，再说明模型内退化影响。不要只凭α或β数值给出确定排名。

## 本次完成规模

| 项目 | 实际完成 |
| --- | --- |
| 3/5输入验证 | 2种输入规模 × 2类案例 × 3模型 × 2噪声 × 3重复 = 72次拟合 |
| 已知参数恢复 | 无噪声恢复组最大α误差{errors['alpha']:.6g}、β误差{errors['beta']:.6g}、自由δ误差{errors['delta']:.6g} |
| 11节点演示样本 | 750组基础网络样本、1500个配对观测版本、16500条节点观测 |
| 11节点拟合 | 18个整网拟合任务、90次局部拟合全部收敛 |
| 依赖分析 | 972条下游响应记录；396次核心公式干预交叉核验 |
| 图表 | {figure_count}张PNG，另附同名SVG及绘图数据表；没有PDF |
| 独立复核 | {audit['recovery_predictions_checked'] + audit['network_predictions_checked']}条预测、3750个生成值、360组指标；最大差{audit['max_abs_discrepancy']:.3g} |
| 回归 | {tests['passed']}项通过，无跳过；补齐本地来源文档与审计夹具后来源核验也已执行 |

## 结果解释

11节点本批{flags['LOCAL_RANK_DEFICIENT']}/90次局部拟合秩不足；30次自由δ拟合均触及声明边界。这些结果继续表明参数解释需要限制。原始样本上的预测越界标记为{flags['PREDICTION_OUTSIDE_SE_RANGE']}/90，不应外推为所有输入均有界；退化情景中有{outside_intervention}条下游响应越界，已保留原值并单独标记。

多输入验证中的诊断组不要求参数唯一恢复，也不要求留出预测误差为零。5输入诊断组的部分拟合仍出现留出误差，原始结果保留在第21包。72项验收通过表示求解和诊断达到各自规定的检查条件，不表示所有模型在全部样本上都精确预测。

3次重复和σ=0/2用于代表性核心验证与流程演示，不等于重做原10次重复、四档噪声及22组系数矩阵。独立机制3输入和5输入拟合在S5/S6及F9/F11节点中覆盖，不额外声称每种机制和全部组合均已穷尽。

退化分析在固定拟合模型中，将各组首条留出基准下的节点效能保留0%或50%，暂停被干预节点的入边并重算后代。图05是三次不同基准响应的均值，只作情景描述；涉及训练范围外输入时保留外推标记。它不是对真实干预因果效应的证明，多条结构路径的影响也不简单相加。

## 文件索引

- [第21包 结果说明](21_多输入核心验证/结果说明.md)：多输入参数恢复、预测与约束诊断。
- [第22包 结果说明](22_11节点完整流程演示/结果说明.md)：独立模拟、拟合、整网预测、边参数和传播分析。
- [第23包 测试与复核](23_统一入口与验收/)：完整测试日志、独立数值复核、接口说明和代码归档。
- [第26包 多基准退化分析](26_多基准退化分析与复核/结果说明.md)：按固定规则选取留出基准的退化响应与解释；需另行运行生成。
- [第27包 本轮检查](27_本轮检查记录/)：更新后的输入警告、独立多基准复核及本轮检查说明。
- [第28包 限制分支与传播解释](28_限制分支与传播解释/可直接用于汇报的分支解读.md)：分支构成、退化前后约束及例子。
- [第29包 分支解释验收](29_本轮分支解释验收/)：完整回归与独立复核。
- [代码使用说明](../../fdna/NETWORK_WORKFLOW.md)：统一入口命令、宽/长表字段和模型配置。

第22包同时保存输入拓扑、JSON记录、CSV宽表/长表及拟合配置，可作为后续真实数据替换模板。真实数据应明确指标定义、归一化与场景机制，再将input_role设为observed；输入测量误差专项建模仍未开展。

{replay_note}

{branch_note}

## 继续保留的待办

剩余16组系数深入诊断继续标记暂缓；此前22组正式拟合、6组系数诊断和5组条件/过程/交互诊断均保留。真实数据校准、输入误差专项实验、更广泛敏感性和更多组合不是本次完成范围。
'''
(reports/'24_应用流程主线_汇报索引.md').write_text(notes,encoding='utf-8')
(out/'统一输入接口说明.md').write_text((root/'fdna/NETWORK_WORKFLOW.md').read_text(encoding='utf-8'),encoding='utf-8')
progress=reports/'当前模拟实验进度.md'
historical=out/'上一阶段进度记录.md'
if progress.exists() and not historical.exists(): shutil.copy2(progress,historical)
progress.write_text(f'''# 当前模拟实验进度

2026年11月22日应用流程主线更新。代码来源正式基线288f590，应用模块已提交至GitHub分支codex/fdna-network-workflow，未合并原分支。

近期四项主线已完成首版实现与代表性验证：3/5输入核心验证72次拟合，统一拓扑与JSON/CSV宽表/长表SE输入接口，11节点18边独立生成与90次局部拟合，以及参数/预测/节点退化/传播路径分析。全部{tests['passed']}项FDNA回归通过、无跳过。

本轮独立标量复核86472条预测、3750个独立生成值及360组指标；8张PNG/SVG已检查。汇报材料见第21、22、23包和[应用流程主线汇报索引](24_应用流程主线_汇报索引.md)。

11节点演示为3次独立重复、每次250个基础网络样本、σ=0/2；这是代表性流程演示，不等于原全部模拟矩阵完成。参数约束不足和δ触界仍存在；模型内退化外推可越界，保留原值。数值自检通过不证明真实任务有效或参数全局唯一。

{replay_note}

{branch_note}

剩余16组深入诊断保持暂缓；此前22组正式拟合和既有诊断均保留。真实数据校准、输入误差专项实验和更广泛敏感性仍待后续。
''',encoding='utf-8')

files=[]
for p in root.rglob('*'):
    if p.is_file() and not any(part in ('__pycache__','.git','generated') for part in p.relative_to(root).parts): files.append(p)
archive=out/'应用流程代码_20261122.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for p in files: z.write(p,Path('fdna_network_workflow')/p.relative_to(root))
manifest={'baseline_commit':'288f590e60f2ad85b1b1d7985c5bd60a2345289a','code_directory':str(root),
    'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'source_hashes':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    'tests':tests,'numerical_audit':audit,'figure_count_png':figure_count,'figure_count_svg':sum(len(list(p.glob('*.svg'))) for p in figure_dirs),'github':github,'branch_audit':branch_audit,'pdf_created':False,'diagnostics_deferred':16}
(out/'代码与验收清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(source_files=len(files),archive_bytes=archive.stat().st_size,index=str(reports/'24_应用流程主线_汇报索引.md'),intervention_outside=outside_intervention),ensure_ascii=False))
