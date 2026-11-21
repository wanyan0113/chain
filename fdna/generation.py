"""Independent task-mechanism generator; no FDNA formula or fit parameters."""
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .model import ModelError, _finite


@lru_cache(maxsize=2)
def _validator(kind):
    schema=json.loads(Path(__file__).with_name('generation_schema.json').read_text(encoding='utf-8'))
    return Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/'+kind})


def validate_config(kind,config):
    _finite(config)
    errors=list(_validator(kind).iter_errors(config))
    if errors:
        raise ModelError('独立生成配置不符合契约：'+errors[0].message)


def _number(value,name,low=None,high=None):
    if (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)
            or low is not None and value<low or high is not None and value>high):
        raise ModelError(name+'须为规定范围内的有限数值')
    return value


def _clip(value):
    return min(100.0,max(0.0,value))


class NodeGenerator:
    """Validated snapshot; explicit noises keep sampling outside the node formula."""
    def __init__(self,config):
        validate_config('node',config)
        parents=set(config['parent_nodes'])
        if config['node_id'] in parents:
            raise ModelError('接受节点不能以自身为供给节点')
        key='support_coefficients' if config['mechanism_type']=='complementary' else 'weights'
        if set(config[key])!=parents:
            raise ModelError('生成参数必须按节点编号恰好覆盖全部供给节点')
        if key=='weights' and not math.isclose(math.fsum(config[key].values()),1,rel_tol=0,abs_tol=1e-12):
            raise ModelError('生成权重之和须为1，不自动归一化')
        condition=config['condition_function']
        for prefix in ('capability','task'):
            if not math.isclose(condition[prefix+'_base']+condition[prefix+'_gain'],1,rel_tol=0,abs_tol=1e-12):
                raise ModelError('条件函数的基础项与增益项之和须为1')
        self._config=deepcopy(config)

    @property
    def config(self):
        return deepcopy(self._config)

    def generate(self,parent_clean,*,process_noise=0.0,observation_noise=0.0,interaction_gamma=0.0,condition_values=None):
        config=self._config
        parents=config['parent_nodes']
        if not isinstance(parent_clean,dict) or set(parent_clean)!=set(parents):
            raise ModelError('上游干净效能必须恰好覆盖已配置供给节点，不补缺失值')
        x={p:_number(parent_clean[p],p+'效能',0,100)/100 for p in parents}
        _number(process_noise,'过程波动'); _number(observation_noise,'观测噪声')
        _number(interaction_gamma,'交互强度',0,1)
        kind=config['mechanism_type']
        if kind=='weighted':
            support=math.fsum(config['weights'][p]*x[p] for p in parents)
        elif kind=='geometric':
            active=[p for p in parents if config['weights'][p]>0]
            support=0.0 if any(x[p]==0 for p in active) else math.exp(
                math.fsum(config['weights'][p]*math.log(x[p]) for p in active))
        else:
            support=1-math.prod(1-config['support_coefficients'][p]*x[p] for p in parents)
        f=config['condition_function']; v=f['values'] if condition_values is None else condition_values
        if not isinstance(v,dict) or set(v)!={'C','M','E'}:
            raise ModelError('逐样本条件须恰好提供C、M、E')
        for name,value in v.items(): _number(value,name+'条件',0,1)
        factor=(f['capability_base']+f['capability_gain']*v['C'])*(f['task_base']+f['task_gain']*v['M'])*(1-f['environment_penalty']*v['E'])
        formula=100*support*factor
        interaction=100*interaction_gamma*v['E']*(1-v['C'])
        process_raw=formula-interaction+process_noise
        if not math.isfinite(process_raw):
            raise ModelError('过程生成结果超出有限数值范围')
        clean=_clip(process_raw)
        observed_raw=clean+observation_noise
        if not math.isfinite(observed_raw):
            raise ModelError('观测生成结果超出有限数值范围')
        result=dict(node_id=config['node_id'],mechanism_type=kind,support=support,condition_factor=factor,
                    parent_clean=deepcopy(parent_clean),receiver_se_formula=formula,
                    interaction_deduction=interaction,process_noise=process_noise,
                    receiver_se_before_clip=process_raw,receiver_se_clean=clean,
                    observation_noise=observation_noise,receiver_se_observed=_clip(observed_raw),
                    process_clipped=not 0<=process_raw<=100,observation_clipped=not 0<=observed_raw<=100)
        if condition_values is not None: result['condition_values']=deepcopy(v)
        return result
