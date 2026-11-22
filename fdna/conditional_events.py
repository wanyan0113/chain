"""Synthetic record-quality events; no FDNA parameters enter generation."""
from io import BytesIO
import numpy as np
from .workflow_common import number
from .model import ModelError


def validate_experiment(config, slots):
    for key in ('seed','base_cases','train_count','trials'):
        if isinstance(config[key],bool) or not isinstance(config[key],int) or config[key]<(0 if key=='seed' else 1):
            raise ModelError(key+'须为有效整数')
    if config['train_count']>=config['base_cases']:raise ModelError('须保留训练和留出基础样本')
    env=config['environments']
    if not isinstance(env,list) or not env:raise ModelError('环境须为非空数组')
    for e in env:number(e,'环境',0,1)
    if len(set(env))!=len(env):raise ModelError('环境不能重复')
    bounds=config['state_range']
    if not isinstance(bounds,list) or len(bounds)!=2:raise ModelError('共享状态须给定上下界')
    for v in bounds:number(v,'共享状态范围',0,1)
    if bounds[0]>=bounds[1]:raise ModelError('共享状态范围须递增')
    tasks=[s['task'] for s in slots]
    if len(tasks)!=len(set(tasks)) or set(config['task_coefficients'])!=set(tasks):raise ModelError('任务系数须覆盖实际任务')
    ids=[n for s in slots for n in s['nodes']]
    if len(ids)!=len(set(ids)):raise ModelError('任务能力实例编号重复')
    for v in config['task_coefficients'].values():number(v,'任务产品系数',0,1)
    probs=config['probabilities'];pen=config['penalties']
    if set(probs)!={'command_good','command_bad','receipt_good','receipt_bad'} or set(pen)!={'information','command','receipt'}:
        raise ModelError('条件事件参数字段不匹配')
    for v in probs.values():number(v,'条件合格概率',0,1)
    for v in pen.values():number(v,'环境作用系数',0,1)
    if probs['command_good']<probs['command_bad'] or probs['receipt_good']<probs['receipt_bad']:
        raise ModelError('本场景要求改善上游不降低合格概率')


def expected_probabilities(h, subjects, slots, environment, config):
    index={s:i for i,s in enumerate(subjects)}
    p=config['probabilities'];v=config['penalties']
    a=np.column_stack([h[:,index[s['information_subject']]]*config['task_coefficients'][s['task']]*(1-v['information']*environment) for s in slots])
    b=(p['command_bad']+(p['command_good']-p['command_bad'])*a)*(1-v['command']*environment)
    f=(p['receipt_bad']+(p['receipt_good']-p['receipt_bad'])*b)*(1-v['receipt']*environment)
    return np.stack((a,b,f),axis=-1)


def event_block(probability_a, environment, uniforms, config):
    """Recompute downstream conditional thresholds from actual upstream events."""
    p=config['probabilities'];v=config['penalties']
    a=uniforms[...,0,:]<np.asarray(probability_a)[...,None]
    b=uniforms[...,1,:]<(np.where(a,p['command_good'],p['command_bad'])*(1-v['command']*environment))
    f=uniforms[...,2,:]<(np.where(b,p['receipt_good'],p['receipt_bad'])*(1-v['receipt']*environment))
    return np.stack((a,b,f),axis=-2)


def generate(slots, config):
    slots=sorted(slots,key=lambda s:s['task']);validate_experiment(config,slots)
    children=np.random.SeedSequence(config['seed']).spawn(3)
    state_rng,split_rng,event_rng=[np.random.default_rng(c) for c in children]
    count=config['base_cases'];trials=config['trials'];envs=sorted(config['environments'])
    train=np.zeros(count,dtype=bool);train[split_rng.permutation(count)[:config['train_count']]]=True
    subjects=sorted({s['information_subject'] for s in slots})
    h=state_rng.uniform(*config['state_range'],size=(count,len(subjects)))
    uniforms=event_rng.random((count,len(slots),3,trials))
    flags=[];probabilities=[];rows=[];audit=[]
    for ei,e in enumerate(envs):
        probs=expected_probabilities(h,subjects,slots,e,config)
        if not np.all((probs>=0)&(probs<=1)):raise ModelError('生成概率超出[0,1]')
        events=event_block(probs[...,0],e,uniforms,config)
        counts=events.sum(axis=-1)
        flags.append(events);probabilities.append(probs)
        for i in range(count):
            values={};clean={};hits={}
            for j,slot in enumerate(slots):
                for k,n in enumerate(slot['nodes']):
                    hits[n]=int(counts[i,j,k]);values[n]=hits[n]*100/trials;clean[n]=float(probs[i,j,k]*100)
            group='env_'+format(e,'.3g');sample=f'b{i:03d}_e{ei}'
            rows.append(dict(sample_id=sample,base_case_id=f'b{i:03d}',scenario_id=group,fit_group=group,
                split='train' if train[i] else 'holdout',node_values=values))
            audit.append(dict(sample_id=sample,base_case_id=f'b{i:03d}',environment=e,counts=hits,expected_se=clean))
    flags=np.asarray(flags);probabilities=np.asarray(probabilities)
    if not np.all(np.diff(flags.astype(np.int8),axis=0)<=0):raise ModelError('配对环境事件违反登记的单调关系')
    stream=BytesIO()
    np.savez_compressed(stream,uniforms=uniforms,events=flags.astype(np.uint8),states=h,train_mask=train,
        probabilities=probabilities,environments=np.asarray(envs),subjects=np.asarray(subjects),
        tasks=np.asarray([s['task'] for s in slots]),nodes=np.asarray([s['nodes'] for s in slots]))
    state_rows=[dict(base_case_id=f'b{i:03d}',split='train' if train[i] else 'holdout',states={s:float(h[i,j]) for j,s in enumerate(subjects)}) for i in range(count)]
    rng=dict(seed=config['seed'],streams=[dict(purpose=p,entropy=int(c.entropy),spawn_key=list(c.spawn_key)) for p,c in zip(('state','split','events'),children)],
        bit_generator=type(state_rng.bit_generator).__name__,numpy=np.__version__,event_index_order=['base_case','task','stage','trial'],
        environment_uniforms_reused=True,subjects_independent=True)
    return dict(rows=rows,audit=audit,states=state_rows,rng=rng,micro_events=int(flags.size),archive=stream.getvalue(),
        invariants=dict(probabilities_in_range=True,paired_environment_events_monotone=True,
            count_se_identity=True,split_frozen_before_events=True,one_state_per_subject_per_base=True))
