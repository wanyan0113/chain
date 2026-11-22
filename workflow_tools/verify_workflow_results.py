"""Independent standard-library arithmetic audit of frozen experiment outputs."""
import hashlib
import json
import math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

root=Path(__file__).resolve().parents[1]
reports=root/'generated/汇报用'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
recovery=read(reports/'21_多输入核心验证/report.json')
demo=read(reports/'22_11节点完整流程演示/workflow_report.json')
generation=read(reports/'22_11节点完整流程演示/generation_report.json')
rows=read(reports/'22_11节点完整流程演示/输入_SE记录.json')

def forward(parameters,values,delta):
    terms=[p['alpha']*values[p['feeder']]+100*(1-p['alpha']) for p in parameters]
    limits=[values[p['feeder']]+p['beta'] for p in parameters]
    return min(sum(terms)/len(terms),min(limits))+delta

max_error=0.;recovery_checks=0;network_checks=0;metric_checks=0;generation_checks=0
for job in recovery['jobs']:
    fitted=job['fit']['receivers'][0];delta=fitted['delta'] or 0.
    for p in job['points']:
        value=forward(fitted['parameters'],p['inputs'],delta)
        max_error=max(max_error,abs(value-p['predicted']));recovery_checks+=1
    assert job['passed']==all(job['criteria'].values())
assert recovery['counts']['jobs']==72 and recovery['counts']['passed']==72

index={(r['fit_group'],r['sample_id']):r for r in rows}
for job in demo['jobs']:
    local={l['node']:l['fit']['receivers'][0] for l in job['local_fits']}
    by_id={}
    for p in job['points']:
        r=index[job['fit_group'],p['sample_id']];n=p['node'];fit=local[n];delta=fit['delta'] or 0.
        value=forward(fit['parameters'],r['predictor_values'],delta)
        max_error=max(max_error,abs(value-p['local_prediction']));network_checks+=1
        if p['sample_id'] not in by_id:
            state={n:r['predictor_values'][n] for n in demo['topology']['roots']}
            for node in demo['topology']['order']:
                if node in state:continue
                state[node]=forward(local[node]['parameters'],state,local[node]['delta'] or 0.)
            by_id[p['sample_id']]=state
        max_error=max(max_error,abs(by_id[p['sample_id']][n]-p['recursive_prediction']));network_checks+=1
    for metric in job['metrics']:
        selected=[p for p in job['points'] if p['split']==metric['split'] and p['node']==metric['node'] and p['target'] is not None and p[metric['prediction']+'_prediction'] is not None]
        residual=[p[metric['prediction']+'_prediction']-p['target'] for p in selected]
        assert len(selected)==metric['evaluated']
        if not residual:continue
        actual=math.sqrt(sum(v*v for v in residual)/len(residual))
        assert abs(actual-metric['rmse'])<1e-9
        if metric['rmse_lower_bound'] is not None: assert actual+1e-8>=metric['rmse_lower_bound']
        metric_checks+=1

node_configs=read(reports/'22_11节点完整流程演示/生成_节点机制.json')['nodes']
for run in generation['clean_runs']:
    values={n:run['node_values_clean'][n] for n in demo['topology']['roots']}
    for node in demo['topology']['order']:
        if node in values:continue
        config=node_configs[node];x={p:values[p]/100 for p in config['parent_nodes']}
        kind=config['mechanism_type']
        if kind=='complementary': support=1-math.prod(1-config['support_coefficients'][p]*v for p,v in x.items())
        elif kind=='weighted': support=sum(config['weights'][p]*v for p,v in x.items())
        else:
            support=math.prod(v**config['weights'][p] for p,v in x.items() if config['weights'][p]>0)
        f=config['condition_function'];c,m,e=f['values']['C'],f['values']['M'],f['values']['E']
        factor=(f['capability_base']+f['capability_gain']*c)*(f['task_base']+f['task_gain']*m)*(1-f['environment_penalty']*e)
        values[node]=min(100,max(0,100*support*factor))
        max_error=max(max_error,abs(values[node]-run['node_values_clean'][node]));generation_checks+=1

pairs={}
for r in rows:
    key=r['base_case_id']
    frozen=(r['split'],r['predictor_values'])
    if key in pairs:assert pairs[key]==frozen
    else:pairs[key]=frozen
for p in reports.glob('2[12]_*/manifest.json'):
    for name,expected in read(p).items():
        assert hashlib.sha256((p.parent/name).read_bytes()).hexdigest()==expected,name
assert max_error<1e-9
audit=dict(status='passed',recovery_predictions_checked=recovery_checks,network_predictions_checked=network_checks,
    independent_generation_values_checked=generation_checks,metric_groups_checked=metric_checks,max_abs_discrepancy=max_error,
    paired_base_networks_checked=len(pairs),manifest_hashes_checked=True,refitted=False,
    method='Independent standard-library scalar arithmetic on frozen outputs; no fitting or generation module called')
out=reports/'23_统一入口与验收';out.mkdir(parents=True,exist_ok=True)
(out/'独立数值复核.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(audit,ensure_ascii=False,indent=2))
