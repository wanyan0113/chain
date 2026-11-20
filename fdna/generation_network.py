"""DAG composition of independent node mechanisms; only clean states propagate."""
from copy import deepcopy
import math

from .generation import NodeGenerator, _clip, _number, validate_config
from .model import ModelError


class NetworkGenerator:
    def __init__(self,network,node_configs,*,condition_values=None):
        validate_config('network',network)
        validate_config('node_configs',node_configs)
        if condition_values is not None:
            validate_config('condition_values',condition_values)
        nodes=set(network['nodes']); incoming={n:[] for n in nodes}; pairs=set()
        for edge in network['edges']:
            a,b=edge['feeder'],edge['receiver']
            if a==b or a not in nodes or b not in nodes or (a,b) in pairs:
                raise ModelError('整网依赖边存在自指、未知节点或重复')
            pairs.add((a,b)); incoming[b].append(a)
        pending=set(nodes); order=[]
        while pending:
            ready=sorted(n for n in pending if not set(incoming[n]) & pending)
            if not ready:
                raise ModelError('独立生成网络存在环路，须另行定义时间或迭代模型：'+','.join(sorted(pending)))
            order.extend(ready); pending.difference_update(ready)
        receivers={n for n in nodes if incoming[n]}
        if set(node_configs['nodes'])!=receivers:
            raise ModelError('生成配置须恰好覆盖全部接受节点；根节点不配置接受机制')
        generators={}
        for n in sorted(receivers):
            config=deepcopy(node_configs['nodes'][n])
            if config['node_id']!=n or set(config['parent_nodes'])!=set(incoming[n]):
                raise ModelError('节点配置编号或上游引用与网络入边不一致：'+n)
            if condition_values is not None:
                config['condition_function']['values']=deepcopy(condition_values)
            generators[n]=NodeGenerator(config)
        self.nodes=sorted(nodes)
        self.roots=sorted(nodes-receivers)
        self.receivers=sorted(receivers)
        self.order=order
        self._incoming={n:sorted(v) for n,v in incoming.items()}
        self._generators=generators

    def evaluate(self,root_clean,*,process_noise=None,observation_noise=None,interaction_gamma=0.0):
        if not isinstance(root_clean,dict) or set(root_clean)!=set(self.roots):
            raise ModelError('整网输入须恰好提供全部根节点的干净效能')
        _number(interaction_gamma,'交互强度',0,1)
        process={} if process_noise is None else process_noise
        observation={} if observation_noise is None else observation_noise
        for noise,allowed in [(process,self.receivers),(observation,self.nodes)]:
            if not isinstance(noise,dict) or set(noise)-set(allowed):
                raise ModelError('噪声配置引用未知节点或向根节点施加接受节点过程波动')
            for v in noise.values(): _number(v,'噪声')
        clean={}; details={}
        for n in self.order:
            if n in root_clean:
                value=_number(root_clean[n],n+'根节点效能',0,100)
                clean[n]=value
                details[n]=dict(node_id=n,kind='root',parent_clean={},receiver_se_clean=value,
                                process_noise=0.0,process_clipped=False)
            else:
                generated=self._generators[n].generate({p:clean[p] for p in self._incoming[n]},
                    process_noise=process.get(n,0.0),interaction_gamma=interaction_gamma)
                clean[n]=generated['receiver_se_clean']
                details[n]={**generated,'kind':'receiver'}
        observed={}
        for n in self.nodes:
            epsilon=observation.get(n,0.0)
            raw=clean[n]+epsilon
            if not math.isfinite(raw): raise ModelError('整网观测值超出有限数值范围')
            observed[n]=_clip(raw)
            details[n].update(observation_noise=epsilon,receiver_se_observed=observed[n],
                              observation_clipped=not 0<=raw<=100)
        return dict(topological_order=list(self.order),node_values_clean=clean,
                    node_values_observed=observed,node_details=details)
