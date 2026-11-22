"""Shared application adapters; no generation parameters enter fitted models."""
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path

from .engine import MODEL
from .additive import ADDITIVE_MODEL
from .generation_fitting import make_fitting_model, _stats
from .model import ModelError, topology, validate_model
from .structure import digest
from .recovery import recovery_model


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ModelError(label+'须为非空字符串')
    return value


def number(value, label, low=None, high=None):
    if (isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value)
            or low is not None and value<low or high is not None and value>high):
        raise ModelError(label+'须为范围内的有限数值')
    return value


def make_model(network):
    if set(network)!={'id','nodes','edges'}:
        raise ModelError('网络须且仅含id、nodes、edges')
    text(network['id'],'网络id')
    nodes=network['nodes']
    if not isinstance(nodes,list) or not nodes or any(not isinstance(n,str) or not n.strip() for n in nodes) or len(nodes)!=len(set(nodes)):
        raise ModelError('节点编号须非空且不重复')
    if not isinstance(network['edges'],list) or not network['edges']:
        raise ModelError('网络须包含待拟合的有向边')
    if any(not isinstance(e,dict) or set(e)!={'feeder','receiver'} for e in network['edges']):
        raise ModelError('边须且仅含feeder、receiver；拟合入口不接受预设边参数')
    if any(e['feeder'] not in nodes or e['receiver'] not in nodes for e in network['edges']):
        raise ModelError('有向边引用未知节点')
    # Canonical IDs and edge order make input permutations irrelevant.
    canonical=dict(id=network['id'],nodes=sorted(nodes),edges=sorted(deepcopy(network['edges']),key=lambda e:(e['receiver'],e['feeder'])))
    model=recovery_model('workflow_template')
    model.update(id='workflow_'+network['id'],label='给定网络的未知参数拟合',scope='仅依据给定拓扑建立模型；不读取表7或生成机制参数作为真值。')
    prefix='__workflow_subject__'
    while any(prefix+n in nodes for n in nodes): prefix='_'+prefix
    refs=['equations:eq1_eq2']
    model['subjects']=[dict(id=prefix+n,label='节点主体'+n,source_refs=refs) for n in canonical['nodes']]
    model['nodes']=[dict(id=n,label=n,subject=prefix+n,role='insight',indicator=dict(label='输入SE',direction='unspecified',normalization=None),source_refs=refs) for n in canonical['nodes']]
    model['edges']=[dict(**e,alpha=None,beta=None,source_refs=refs) for e in canonical['edges']]
    model['case_extension']['delta']={e['receiver']:None for e in canonical['edges']}
    validate_model(model)
    return model


def settings(mode,receivers,*,seed=20261122,starts=12,max_nfev=4000,delta=-3,delta_bounds=(-10,10)):
    result=dict(format_version='1.0',id='workflow_'+mode,basis='中性与固定种子随机起点；只按训练损失选择；不读取生成真值。',
        model=MODEL if mode=='base' else ADDITIVE_MODEL,solver='L-BFGS-B',seed=seed,
        starts=starts,max_nfev=max_nfev,delta_policy='excluded_for_base_check')
    if mode=='fixed_delta':
        result.update(delta_policy='per_receiver_fixed',delta_values={n:delta for n in receivers},output_policy='no_clipping')
    elif mode=='free_delta':
        result.update(delta_policy='per_receiver_bounded',delta_bounds={n:list(delta_bounds) for n in receivers},output_policy='no_clipping')
    elif mode!='base': raise ModelError('未知模型版本')
    return result


def scalar_forward(inputs,alpha,beta,delta=0.):
    if len(inputs)!=len(alpha) or len(inputs)!=len(beta) or not inputs:
        raise ModelError('供给输入与参数维度不一致')
    sod=math.fsum(a*x+100*(1-a) for x,a in zip(inputs,alpha))/len(inputs)
    terms=[x+b for x,b in zip(inputs,beta)]
    return min(sod,min(terms))+delta


def predict_receiver(receiver,values):
    params=receiver['parameters']
    return scalar_forward([values[p['feeder']] for p in params],[p['alpha'] for p in params],
                          [p['beta'] for p in params],receiver.get('delta') or 0.)


def parameter_flags(row,*,boundary_tolerance=1e-6):
    if row.get('status')!='fitted': return ['FIT_'+row.get('status','unknown').upper()]
    flags=[]
    s=row['sensitivity']
    if s['local_jacobian_rank']<s['parameter_count']: flags.append('LOCAL_RANK_DEFICIENT')
    if s['inactive_parameters']: flags.append('INACTIVE_PARAMETERS')
    if any(p['alpha']<=boundary_tolerance or p['alpha']>=1-boundary_tolerance or
           p['beta']<=100*boundary_tolerance or p['beta']>=100*(1-boundary_tolerance) for p in row['parameters']):
        flags.append('EDGE_PARAMETER_AT_BOUNDARY')
    if row.get('delta_bounds') and min(abs(row['delta']-b) for b in row['delta_bounds'])<=boundary_tolerance:
        flags.append('DELTA_AT_BOUNDARY')
    if any(v>1e-3 for v in row.get('near_best_parameter_spread',{}).values()):
        flags.append('MULTISTART_PARAMETER_SPREAD')
    return flags


def csv_text(rows,fields=None):
    stream=io.StringIO(newline='')
    columns=fields or list(dict.fromkeys(k for row in rows for k in row))
    writer=csv.DictWriter(stream,fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    return '\ufeff'+stream.getvalue()


def write_bundle(directory,outputs):
    path=Path(directory)
    if path.exists() and any(path.iterdir()): raise ModelError('输出目录须不存在或为空，拒绝覆盖已有结果')
    path.mkdir(parents=True,exist_ok=True)
    manifest={}
    import hashlib
    for name,value in outputs.items():
        dest=path/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        data=value if isinstance(value,bytes) else value.encode('utf-8')
        dest.write_bytes(data)
        manifest[name]=hashlib.sha256(data).hexdigest()
    (path/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')


def dumps(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
