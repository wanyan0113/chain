"""Frozen benchmark reference from 19d31b88baa5f09f8620c96dd6fd7798f7ed2e69; not used for production."""
SOURCE_COMMIT = '19d31b88baa5f09f8620c96dd6fd7798f7ed2e69'
SOURCE_FILE_SHA256 = 'f87e31dbacb70d915f079cb1c56cb83cc97c23d3cf9abe5742a50c8428c65c24'
import numpy as np
from fdna.model import ModelError
from fdna.workflow_common import make_model

def layout(network):
    make_model(network)  # Validate nodes, duplicate edges and DAG before compiling.
    nodes=sorted(network['nodes'])
    parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in nodes}
    order=[]
    while len(order)<len(nodes):
        ready=[n for n in nodes if n not in order and all(p in order for p in parents[n])]
        if not ready: raise ModelError('网络存在环')
        order.extend(ready)
    return nodes,parents,order

def predict(network, fits, roots, forced=None):
    """Batch DAG evaluation. Missing values propagate; internal observations are rejected."""
    nodes,parents,order=layout(network)
    if set(roots)!={n for n in nodes if not parents[n]}: raise ModelError('只接受全部根节点观测')
    values={n:np.asarray(v,dtype=float) for n,v in roots.items()}
    shapes={v.shape for v in values.values()}
    if len(shapes)!=1 or len(next(iter(shapes)))!=1: raise ModelError('根观测须为等长一维数组')
    shape=next(iter(shapes))
    if set(fits)!={n for n in nodes if parents[n]}: raise ModelError('接受节点模型不完整')
    if forced is not None:
        node,vector=forced
        vector=np.asarray(vector,dtype=float)
        if node not in nodes or vector.shape!=shape: raise ModelError('干预节点或维度错误')
    for n in order:
        if parents[n]:
            f=fits[n];params=f['parameters']
            if (len(params)!=len(parents[n]) or sorted(p['feeder'] for p in params)!=parents[n]
                    or f['status']!='fitted'): raise ModelError('参数供给关系或拟合状态错误')
            a=np.array([p['alpha'] for p in params]);b=np.array([p['beta'] for p in params]);d=f['delta'] or 0.
            if (not np.isfinite(a).all() or not np.isfinite(b).all() or not np.isfinite(d)
                    or np.any((a<0)|(a>1)) or np.any((b<0)|(b>100))): raise ModelError('参数非有限或越界')
            x=np.array([values[p['feeder']] for p in params])
            values[n]=np.minimum(np.mean(100-a[:,None]*(100-x),axis=0),np.min(x+b[:,None],axis=0))+d
        if forced is not None and forced[0]==n: values[n]=vector.copy()
    return np.stack([values[n] for n in nodes],axis=1)

def replay(network, record, rows, jobs, cases, pairs, fractions, ranges):
    nodes,parents,_=layout(network);index={n:i for i,n in enumerate(nodes)}
    shape=(len(jobs),len(pairs),len(fractions),len(cases))
    arrays={name:np.full(shape,np.nan) for name in ('decrease','changed','forced')}
    arrays.update({name:np.zeros(shape,dtype=bool) for name in ('valid','outside','extrapolated')})
    arrays['baseline']=np.full((len(jobs),len(cases),len(nodes)),np.nan)
    lookup={(r['fit_group'],r['base_case_id']):r for r in rows if r['split']=='holdout'}
    if len(lookup)!=len([r for r in rows if r['split']=='holdout']): raise ModelError('留出面板重复')
    for j,(group,mode) in enumerate(jobs):
        entries=[f for f in record['fits'] if (f['fit_group'],f['model'])==(group,mode)]
        fits={f['node']:f for f in entries}
        if len(entries)!=len(fits): raise ModelError('接受节点拟合重复')
        if set(fits)!={n for n in nodes if parents[n]}: raise ModelError('接受节点拟合缺失')
        if any(f['status']!='fitted' for f in entries): continue
        roots={n:np.array([lookup.get((group,c),{}).get('node_values',{}).get(n,np.nan) for c in cases])
               for n in nodes if not parents[n]}
        baseline=predict(network,fits,roots);arrays['baseline'][j]=baseline
        root_valid=np.all(np.isfinite(np.array(list(roots.values()))),axis=0)
        for k,(start,target) in enumerate(pairs):
            own=baseline[:,index[start]]
            valid=root_valid & np.isfinite(own) & (own>=0) & (own<=100)
            for fi,fraction in enumerate(fractions):
                forced=np.where(valid,own*fraction,np.nan)
                changed=predict(network,fits,roots,(start,forced))
                y=changed[:,index[target]];old=baseline[:,index[target]]
                mask=valid & np.isfinite(y) & np.isfinite(old)
                key=j,k,fi
                arrays['valid'][key]=mask
                arrays['forced'][key]=forced
                arrays['changed'][key]=np.where(mask,y,np.nan)
                arrays['decrease'][key]=np.where(mask,old-y,np.nan)
                arrays['outside'][key]=mask & ((y<0)|(y>100))
                for feeder in parents[target]:
                    low,high=ranges[group,target,feeder];x=changed[:,index[feeder]]
                    arrays['extrapolated'][key]|=mask & ((x<low-1e-8)|(x>high+1e-8))
                if np.any(arrays['decrease'][key][mask]<-1e-8): raise ModelError('违反单调性')
                if fraction==1 and np.any(abs(arrays['decrease'][key][mask])>1e-8): raise ModelError('原值干预不为零')
    return arrays
