"""Explicit immutable preparation for repeated, unclipped FDNA DAG evaluation."""
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from .model import ModelError
from .workflow_common import make_model


@dataclass(frozen=True, slots=True)
class PreparedTopology:
    nodes: tuple
    parent_items: tuple
    order: tuple

    @classmethod
    def from_network(cls,network):
        make_model(network)  # Full public-contract validation, once per explicit preparation.
        nodes=tuple(sorted(network['nodes']))
        parents={n:tuple(sorted(e['feeder'] for e in network['edges'] if e['receiver']==n)) for n in nodes}
        order=[]
        while len(order)<len(nodes):
            ready=[n for n in nodes if n not in order and all(p in order for p in parents[n])]
            if not ready:raise ModelError('网络存在环')
            order.extend(ready)
        return cls(nodes,tuple(parents.items()),tuple(order))

    @property
    def roots(self):
        return tuple(n for n,p in self.parent_items if not p)

    def bind(self,fits):
        parents=dict(self.parent_items)
        if set(fits)!={n for n in self.nodes if parents[n]}:raise ModelError('接受节点模型不完整')
        equations={}
        for n,f in fits.items():
            params=f['parameters']
            if (len(params)!=len(parents[n]) or tuple(sorted(p['feeder'] for p in params))!=parents[n]
                    or f['status']!='fitted'):raise ModelError('参数供给关系或拟合状态错误')
            # Immutable byte-backed arrays also prevent re-enabling write access.
            a=np.frombuffer(np.asarray([p['alpha'] for p in params],dtype=float).tobytes(),dtype=float)
            b=np.frombuffer(np.asarray([p['beta'] for p in params],dtype=float).tobytes(),dtype=float)
            d=float(f['delta'] or 0.)
            if (not np.isfinite(a).all() or not np.isfinite(b).all() or not np.isfinite(d)
                    or np.any((a<0)|(a>1)) or np.any((b<0)|(b>100))):raise ModelError('参数非有限或越界')
            equations[n]=(tuple(p['feeder'] for p in params),a,b,d)
        return PreparedPredictor(self,MappingProxyType(equations))


@dataclass(frozen=True, slots=True)
class PreparedPredictor:
    topology: PreparedTopology
    equations: object

    def predict(self,roots,forced=None):
        """Validate each call's inputs; no mutable global cache or retained observations."""
        if set(roots)!=set(self.topology.roots):raise ModelError('只接受全部根节点观测')
        values={n:np.asarray(v,dtype=float) for n,v in roots.items()}
        shapes={v.shape for v in values.values()}
        if len(shapes)!=1 or len(next(iter(shapes)))!=1:raise ModelError('根观测须为等长一维数组')
        shape=next(iter(shapes))
        if forced is not None:
            node,vector=forced;vector=np.asarray(vector,dtype=float)
            if node not in self.topology.nodes or vector.shape!=shape:raise ModelError('干预节点或维度错误')
        for n in self.topology.order:
            if n in self.equations:
                feeders,a,b,d=self.equations[n]
                x=np.array([values[p] for p in feeders])
                values[n]=np.minimum(np.mean(100-a[:,None]*(100-x),axis=0),np.min(x+b[:,None],axis=0))+d
            if forced is not None and forced[0]==n:values[n]=vector.copy()
        return np.stack([values[n] for n in self.topology.nodes],axis=1)


def layout(network):
    topology=PreparedTopology.from_network(network)
    return list(topology.nodes),{n:list(p) for n,p in topology.parent_items},list(topology.order)
