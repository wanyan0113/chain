"""One read-only entry for published artifacts and explicit full numerical rechecks."""
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from urllib.parse import quote

from .model import ModelError


REPOSITORY=Path(__file__).resolve().parents[1]
DEFAULT_CATALOG=REPOSITORY/'data/fdna/evidence_catalog.json'
TASKS=(
    ('source06','06原始场景完整包','fdna.flow_scenario','generated/关于两篇连接/06_完整场景全流程运行','source_integrity'),
    ('figures','六图汇报与来源','workflow_tools.verify_optimization_report','generated/关于优化/06_汇报图表与说明_复核版','source_csv_comparison'),
    ('baselines','训练均值与OLS基线','workflow_tools.verify_prediction_baselines','generated/关于优化/03_同条件简单预测基线_核验版','scalar_numerical'),
    ('frozen_predictions','冻结预测重采样','workflow_tools.verify_prediction_stability','generated/关于优化/04_冻结预测成组稳定性','scalar_numerical'),
    ('frozen_propagation','冻结模型传播','workflow_tools.verify_propagation_stability','generated/关于优化/07_冻结模型多基准传播_核验版','scalar_numerical'),
    ('training_refit','训练成组重拟合','workflow_tools.verify_training_refit','generated/关于优化/08_训练成组重拟合','scalar_numerical'),
    ('refit_propagation','重估模型传播','workflow_tools.verify_refit_propagation','generated/关于优化/09_重估模型传播敏感性','scalar_numerical'),
    ('refit_branches','重估模型分支','workflow_tools.verify_refit_branches','generated/关于优化/10_重估模型分支启用诊断_核验版','scalar_numerical'),
    ('edge_ablation','同信息依赖消融','workflow_tools.verify_edge_ablation','generated/关于优化/11_同信息训练均值依赖消融','scalar_numerical'),
    ('performance','性能记录及全量回放','workflow_tools.verify_prepared_benchmark','generated/关于优化/12_传播计算效率优化','timing_arithmetic_and_scalar_regression'),
)
STATES={'implemented_scoped':'已落实当前范围','partial':'部分完成','deferred':'待新增优化'}


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_path(root,name):
    """Do not read paths outside the explicitly selected package/repository."""
    if not isinstance(name,str) or not name or '\x00' in name:raise ValueError('文件名须为非空相对路径')
    normalized=name.replace('\\','/')
    pure=PurePosixPath(normalized)
    if pure.is_absolute() or PureWindowsPath(name).drive or any(p in ('','..','.') for p in normalized.split('/')):
        raise ValueError('禁止绝对路径或路径回退: '+name)
    root=Path(root).resolve();path=(root/normalized).resolve()
    if not path.is_relative_to(root):raise ValueError('文件链接越出允许目录: '+name)
    return path


def validate_catalog(catalog):
    if not isinstance(catalog,dict):raise ModelError('成果目录须为JSON对象')
    if catalog.get('id')!='flow06_optimization_evidence_catalog_v1':raise ModelError('未知成果目录版本')
    packages=catalog.get('packages',[]);documents=catalog.get('documents',[]);priorities=catalog.get('priorities',[])
    for entries,required in ((packages,{'id','title','directory','entrypoint','required_files'}),
            (documents,{'id','title','path','normalized_sha256'}),
            (priorities,{'number','title','state','evidence','scope','remaining'})):
        if not isinstance(entries,list) or any(not isinstance(p,dict) or not required<=set(p) for p in entries):
            raise ModelError('成果目录缺少必需字段或列表格式错误')
    ids=[p['id'] for p in packages]+[p['id'] for p in documents]
    if not packages or len(ids)!=len(set(ids)):raise ModelError('成果编号为空或重复')
    for p in packages:
        if p['entrypoint'] not in p['required_files']:raise ModelError('阅读入口须列为必需成果')
    if sorted(p['number'] for p in priorities)!=list(range(1,10)):raise ModelError('须明确列出原九项研究建议')
    for p in priorities:
        if p['state'] not in STATES or not set(p['evidence'])<=set(ids):raise ModelError('研究状态或成果引用无效')
        if p['state']!='deferred' and not p['evidence']:raise ModelError('已推进项目须绑定成果')
    return catalog


def check_package(repository,entry):
    errors=[];checked=0;total_bytes=0;fingerprint=None
    try:
        directory=safe_path(repository,entry['directory']);index=directory/'manifest.json'
        manifest=read(index);fingerprint=sha(index)
        if not isinstance(manifest,dict) or not manifest:raise ValueError('精确字节清单为空或格式错误')
        if 'manifest.json' in manifest:raise ValueError('清单不能包含自身')
        listed=set()
        for name,expected in manifest.items():
            try:
                path=safe_path(directory,name);listed.add(path.relative_to(directory.resolve()).as_posix())
                if not isinstance(expected,str) or not re.fullmatch('[0-9a-fA-F]{64}',expected):raise ValueError('SHA256格式错误')
                if not path.is_file():raise ValueError('缺少文件')
                actual=sha(path);checked+=1;total_bytes+=path.stat().st_size
                if actual!=expected.lower():raise ValueError('精确字节摘要不一致')
            except (ValueError,OSError) as exc:errors.append(dict(file=name,error=str(exc)))
        actual=set()
        for path in directory.rglob('*'):
            if path.is_symlink():safe_path(directory,path.relative_to(directory).as_posix())
            if path.is_file():actual.add(path.relative_to(directory).as_posix())
        unlisted=sorted(actual-listed-{'manifest.json'})
        if unlisted:errors.append(dict(file='manifest.json',error='存在未登记文件',details=unlisted))
        for name in entry['required_files']:
            safe_path(directory,name)
            if name not in manifest:errors.append(dict(file=name,error='必需成果未登记在清单中'))
    except (ValueError,OSError,TypeError) as exc:errors.append(dict(file='manifest.json',error=str(exc)))
    return dict(id=entry['id'],title=entry['title'],directory=entry['directory'],entrypoint=entry['entrypoint'],
                status='failed' if errors else 'passed',files_checked=checked,bytes_checked=total_bytes,
                manifest_sha256=fingerprint,errors=errors,check_kind='published_exact_bytes_only')


def check_document(repository,entry):
    try:
        path=safe_path(repository,entry['path'])
        digest=hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        if digest!=entry['normalized_sha256']:raise ValueError('说明文档内容与登记版本不一致')
        return dict(id=entry['id'],title=entry['title'],path=entry['path'],status='passed',normalized_sha256=digest,errors=[])
    except (ValueError,OSError) as exc:
        return dict(id=entry['id'],title=entry['title'],path=entry['path'],status='failed',errors=[str(exc)])


def execute_task(module,project_root,directory):
    verifier=importlib.import_module(module).verify
    return verifier(project_root,directory) if module=='workflow_tools.verify_optimization_report' else verifier(directory)


def full_checks(project_root,progress=None,runner=None):
    if sys.flags.optimize:raise ModelError('完整复核依赖assert，请使用未开启-O或-OO优化的Python运行')
    root=Path(project_root).resolve();runner=runner or execute_task;results=[]
    for ident,title,module,relative,kind in TASKS:
        if progress:progress(dict(stage='started',task=ident,title=title))
        log=io.StringIO();invoked=False
        try:
            directory=safe_path(root,relative)
            if not directory.is_dir():raise FileNotFoundError('缺少本地成果目录: '+str(directory))
            invoked=True
            with contextlib.redirect_stdout(log):result=runner(module,root,directory)
            if module=='fdna.flow_scenario' and isinstance(result,int):result=dict(status='passed',source_files_verified=result)
            if not isinstance(result,dict) or result.get('status')!='passed':raise ValueError('复核函数未明确返回passed')
            item=dict(id=ident,title=title,status='passed',check_kind=kind,source=relative,result=result)
        except Exception as exc:
            # One damaged/missing artifact must not conceal the remaining failed tasks.
            item=dict(id=ident,title=title,status='failed',check_kind=kind,source=relative,
                      error_type=type(exc).__name__,error=str(exc) or '断言失败；请运行对应独立脚本定位')
        if log.getvalue():item['verifier_log']=log.getvalue()
        item['verifier_invoked']=invoked
        results.append(item)
        if progress:progress(dict(stage='finished',task=ident,status=item['status'],verifier_invoked=invoked))
    return results


def report_markdown(report,repository,output_dir):
    status=lambda value:{'passed':'通过','failed':'失败'}.get(value,value)
    kinds={'source_integrity':'源包完整性','source_csv_comparison':'CSV与来源对照',
           'scalar_numerical':'独立标量重算','timing_arithmetic_and_scalar_regression':'计时记录与标量回归'}
    def link(name,path):
        relative=os.path.relpath(safe_path(repository,path),output_dir).replace('\\','/')
        return '['+name+']('+quote(relative,safe='/:')+')'
    lines=['# 优化成果统一复核与阅读入口','',
        '本次检查时间（UTC）：'+report['checked_at_utc']+'；模式：'+report['mode']+'；检查状态：'+report['status']+'。','',
        '**材料字节一致、数值重算通过与真实有效性是不同层次。研究进度来自版本化目录的人工登记，不由哈希检查自动推断。**','',
        '## 已发布成果','', '| 成果 | 本次材料检查 | 文件数 | 阅读入口 |','| --- | --- | ---: | --- |']
    for p in report['packages']:
        lines.append('| '+' | '.join([p['title'],status(p['status']),str(p['files_checked']),link('图文说明',p['directory']+'/'+p['entrypoint'])])+' |')
    lines+=['','## 接口和使用说明','']
    lines.extend('- '+link(p['title'],p['path'])+'：本次版本检查'+status(p['status']) for p in report['documents'])
    lines+=['','## 当前九项建议的已记录进度','', '| 项目 | 登记状态 | 现有范围与未完成事项 |','| --- | --- | --- |']
    for p in report['priorities']:
        lines.append(f"| {p['number']}. {p['title']} | {STATES[p['state']]} | {p['scope']}；待办：{p['remaining']} |")
    count=report['declared_progress_counts']
    lines+=['',f"按当前场景范围登记：{count['implemented_scoped']}项已落实、{count['partial']}项部分完成、{count['deferred']}项待新增优化。各项工作量不同，不换算成完成百分比。",'',
        '## 本次数值复核','']
    if report['mode']=='published':
        lines+=['本次没有重新执行数值核验；只检查发布清单和说明文档版本。成果包中的历史核验记录不能写成本次重新计算通过。']
    else:
        lines+=['本次命令逐项检查以下任务；来源齐备时调用独立脚本，未调用及失败均保留记录。完整包检查、CSV来源对照、标量重算和计时算术分别标记，不把重复检查的计数加总成独立实验数。','',
                '| 复核任务 | 类型 | 独立脚本 | 本次状态 |','| --- | --- | --- | --- |']
        lines.extend('| '+r['title']+' | '+kinds[r['check_kind']]+' | '+('已调用' if r['verifier_invoked'] else '未调用')+' | '+status(r['status'])+' |' for r in report['full_checks'])
    errors=[(p['title'],e) for p in report['packages']+report['documents'] for e in p['errors']]
    errors.extend((r['title'],r.get('error','')) for r in report['full_checks'] if r['status']!='passed')
    if errors:
        lines+=['','## 待处理检查问题','']
        for name,error in errors:lines.append('- '+name+'：'+str(error).replace('\n',' ').replace('|','/'))
    lines+=['','## 解释边界','',
        '- 5轮内存传播计时不是整个FDNA流程或任意机器的加速保证；此命令不会重新测时。',
        '- 参数/传播有限范围不是置信区间；当前06结果不支持FDNA预测优于OLS。',
        '- 训练均值依赖消融不是完整替代结构重估，不证明真实边成立或不成立。',
        '- 缺少数据、哈希不一致或数值断言失败会保留失败记录并返回非零退出码，不默认为通过。','']
    return '\n'.join(lines)


def run(repository,project_root,output_dir,mode='published',catalog_path=DEFAULT_CATALOG,progress=None):
    repo=Path(repository).resolve();out=Path(output_dir).resolve()
    if mode not in ('published','full'):raise ModelError('只支持published或full复核模式')
    if mode=='full' and project_root is None:raise ModelError('完整复核须显式提供包含generated的--project-root')
    if mode=='full' and sys.flags.optimize:raise ModelError('完整复核不支持python -O优化模式')
    try:catalog=validate_catalog(read(catalog_path))
    except (KeyError,TypeError) as exc:raise ModelError('成果目录字段类型错误，请核对登记格式') from exc
    protected=[repo/p for p in ('docs','fdna','workflow_tools','tests','data','.git')]
    if project_root is not None:protected.extend(safe_path(project_root,t[3]) for t in TASKS)
    if out==repo or any(out==p.resolve() or out.is_relative_to(p.resolve()) for p in protected):
        raise ModelError('复核报告不能写入代码、发布成果或已有来源包内部')
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ModelError('输出目录须不存在或为空')
    packages=[check_package(repo,p) for p in catalog['packages']]
    documents=[check_document(repo,p) for p in catalog['documents']]
    checks=full_checks(project_root,progress) if mode=='full' else []
    failed=sum(p['status']!='passed' for p in packages+documents+checks)
    report=dict(stage='unified_optimization_evidence_review',mode=mode,status='failed' if failed else 'passed',
        checked_at_utc=datetime.now(timezone.utc).isoformat(),repository=str(repo),
        project_root=str(Path(project_root).resolve()) if project_root is not None else None,
        catalog_id=catalog['id'],catalog_sha256=sha(catalog_path),packages=packages,documents=documents,full_checks=checks,
        priorities=catalog['priorities'],declared_progress_counts={s:sum(p['state']==s for p in catalog['priorities']) for s in STATES},
        package_files_checked=sum(p['files_checked'] for p in packages),failed_checks=failed,
        full_verifiers_invoked=sum(r['verifier_invoked'] for r in checks),
        numerical_rechecks_executed=any(r['verifier_invoked'] and r['check_kind'] in ('scalar_numerical','timing_arithmetic_and_scalar_regression') for r in checks),
        numerical_rechecks_completed=mode=='full' and all(r['status']=='passed' for r in checks),
        performance_remeasured=False,scientific_validity_established=False)
    from .workflow_common import dumps,write_bundle
    write_bundle(out,{'review_report.json':dumps(report),'成果复核与阅读入口.md':report_markdown(report,repo,out),'目录版本.json':dumps(catalog)})
    return report
