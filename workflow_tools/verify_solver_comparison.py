"""Independent scalar replay of two solver pipelines on fixed FDNA inputs."""
import argparse,csv,hashlib,json,math
from pathlib import Path
import numpy as np

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()

def verify(directory):
    if not __debug__:raise ValueError('独立核验不能使用-O')
    out=Path(directory);report=read(out/'solver_report.json');protocol=read(out/'预登记方案.json')
    inventory=read(out/'manifest.json');supporting={'结果说明.md','执行环境与代码版本.json','回归测试记录.json','独立标量核验.json'}
    assert set(inventory)=={p.name for p in out.iterdir() if p.is_file() and p.name not in ('manifest.json',*supporting)}
    for name,value in inventory.items():assert sha(out/name)==value,name
    assert report['protocol']==protocol and digest(protocol)==report['protocol_sha256']
    assert protocol['solvers']==['L-BFGS-B','least_squares'] and protocol['seed']==20261122 and protocol['starts']==6
    source=Path(report['source']['directory']);original=read(source/'04_FDNA拟合/workflow_report.json');rows=read(source/'04_FDNA拟合/输入_SE记录.json')
    assert sha(source/'04_FDNA拟合/workflow_report.json')==report['source']['fit_sha256']
    for name,value in read(source/'完整包校验.json').items():assert sha(source/name)==value
    assert len({r['base_case_id'] for r in rows if r['split']=='train'})==90 and len({r['base_case_id'] for r in rows if r['split']=='holdout'})==30
    assert digest(rows)==original['data_sha256'] and original['input_sha256']==report['source']['input_sha256']
    network=original['network_snapshot'];parents={n:sorted(e['feeder'] for e in network['edges'] if e['receiver']==n) for n in network['nodes']};order=[]
    while len(order)<len(parents):
        ready=[n for n in sorted(parents) if n not in order and all(p in order for p in parents[n])];assert ready;order.extend(ready)
    source_jobs={(j['fit_group'],j['model']):j for j in original['jobs']}
    records=[read(out/'lbfgsb_record.json'),read(out/'least_squares_record.json')]
    assert [r['solver'] for r in records]==protocol['solvers']
    records_by_solver=[];maximum=0.;counts={'starts':0,'attempt_losses':0,'parameters':0,'local_predictions':0,'recursive_predictions':0,'metrics':0,'csv_fields':0}
    def close(a,b):
        nonlocal maximum
        if a is None or b is None:assert a is b;return
        assert math.isfinite(a) and math.isfinite(b);d=abs(a-b);maximum=max(maximum,d);assert d<=1e-8,(a,b,d)
    def forward(vals,params,delta):
        sod=math.fsum(100-p['alpha']*(100-vals[p['feeder']]) for p in params)/len(params)
        cod=min(vals[p['feeder']]+p['beta'] for p in params)
        return min(sod,cod)+(delta or 0.)
    for si,(solver,record) in enumerate(zip(protocol['solvers'],records)):
        expected_config=json.loads(json.dumps(original['config_snapshot']))
        for setting in expected_config['models'].values():setting['solver']=solver
        assert record['solver']==solver and record['config_snapshot']==expected_config
        fitmap={(f['fit_group'],f['model'],f['node']):f for f in record['fits']};assert len(fitmap)==len(record['fits'])==36
        rawmap={(r['fit_group'],r['sample_id']):r for r in rows}
        pointmap={(p['fit_group'],p['model'],p['node'],p['sample_id']):p for p in record['holdout_points']};assert len(pointmap)==len(record['holdout_points'])==1080
        metricmap={(m['fit_group'],m['model'],m['node'],m['split'],m['prediction']):m for m in record['metrics']};assert len(metricmap)==len(record['metrics'])==144
        for key,f in fitmap.items():
            group,mode,node=key;setting=dict(expected_config['models'][mode]);setting['delta_bounds']={node:setting['delta_bounds'][node]} if 'delta_bounds' in setting else setting.get('delta_bounds')
            if setting.get('delta_bounds') is None:setting.pop('delta_bounds',None)
            assert f['settings_sha256']==digest(setting) and f['protocol_sha256']==digest(protocol)
            assert f['source_input_sha256']==original['input_sha256'] and len(f['attempts'])==6
            dim=2+int(mode=='free_delta');rng=np.random.Generator(np.random.PCG64(protocol['seed']))
            starts=[[.5]*dim]+rng.uniform(.01,.99,(5,dim)).tolist()
            assert sorted(tuple(x) for x in starts)==sorted(tuple(a['initial']) for a in f['attempts']);counts['starts']+=6
            train=[r for r in rows if r['fit_group']==group and r['split']=='train'];assert len(train)==90
            for a in f['attempts']:
                v=a['parameters'];assert len(v)==dim
                params=[dict(feeder=parents[node][0],alpha=v[0],beta=v[1])]
                delta=v[2] if dim==3 else None
                loss=math.sqrt(math.fsum((forward(r['node_values'],params,delta)-r['node_values'][node])**2 for r in train)/90)
                close(a['rmse'],loss);counts['attempt_losses']+=1
            assert [a['rmse'] for a in f['attempts']]==sorted(a['rmse'] for a in f['attempts'])
            tolerance=8*math.ulp(max(1.,abs(f['attempts'][0]['rmse'])))
            selected=next((i for i,a in enumerate(f['attempts']) if a['success'] and a['rmse']-f['attempts'][0]['rmse']<=tolerance),0)
            assert f['attempt_selection']['index']==selected
            chosen=f['attempts'][selected];assert f['status']==('fitted' if chosen['success'] else 'iteration_limit')
            actual=[f['parameters'][0]['alpha'],f['parameters'][0]['beta']]+([f['delta']] if dim==3 else [])
            assert actual==chosen['parameters'];counts['parameters']+=len(actual)
        for (g,m,node,sample),p in pointmap.items():
            raw=rawmap[g,sample];assert raw['split']==p['split'] and p['split']=='holdout'
            close(p['target'],raw['node_values'][node]);f=fitmap[g,m,node]
            local=forward(raw['node_values'],f['parameters'],f['delta']) if f['status']=='fitted' else None
            close(p['local_prediction'],local);counts['local_predictions']+=1
            values={}
            for n in order:
                if not parents[n]:values[n]=raw['node_values'].get(n)
                else:
                    nf=fitmap[g,m,n];values[n]=forward(values,nf['parameters'],nf['delta']) if nf['status']=='fitted' and all(values.get(x) is not None for x in parents[n]) else None
            close(p['recursive_prediction'],values[node]);counts['recursive_predictions']+=1
        for key,m in metricmap.items():
            g,mode,node,split,kind=key;panel=[r for r in rows if r['fit_group']==g and r['split']==split]
            local_errors=[];recursive_errors=[]
            for raw in panel:
                f=fitmap[g,mode,node];target=raw['node_values'].get(node)
                local=forward(raw['node_values'],f['parameters'],f['delta']) if f['status']=='fitted' and all(raw['node_values'].get(x) is not None for x in parents[node]) else None
                values={}
                for n in order:
                    if not parents[n]:values[n]=raw['node_values'].get(n)
                    else:
                        nf=fitmap[g,mode,n];values[n]=forward(values,nf['parameters'],nf['delta']) if nf['status']=='fitted' and all(values.get(x) is not None for x in parents[n]) else None
                pred=local if kind=='local' else values[node]
                if target is not None and pred is not None:
                    (local_errors if kind=='local' else recursive_errors).append((pred-target)**2)
            valid=local_errors if kind=='local' else recursive_errors
            value=math.sqrt(math.fsum(valid)/len(valid)) if valid else None
            assert m['available']==len(panel) and m['evaluated']==len(valid);close(m['rmse'],value);counts['metrics']+=1
        records_by_solver.append((fitmap,pointmap,metricmap))
    source_fits={(j['fit_group'],j['model'],f['node']):f for j in original['jobs'] for f in j['local_fits']}
    lb_fits=records_by_solver[0][0]
    control_max=0.
    for key,f in lb_fits.items():
        old=source_fits[key]['fit']['receivers'][0]
        left={p['feeder']:p for p in old['parameters']};right={p['feeder']:p for p in f['parameters']}
        assert set(left)==set(right)
        for feeder in left:
            for name in ('alpha','beta'):
                close(left[feeder][name],right[feeder][name]);control_max=max(control_max,abs(left[feeder][name]-right[feeder][name]))
        close(old.get('delta'),f['delta'])
        if old.get('delta') is not None:control_max=max(control_max,abs(old['delta']-f['delta']))
    for key,p in records_by_solver[0][1].items():
        old=source_fits[key[:3]]
        source_point=next(x for job in original['jobs'] if (job['fit_group'],job['model'])==key[:2]
                          for x in job['points'] if x['split']=='holdout' and x['sample_id']==key[3] and x['node']==key[2])
        for name in ('local_prediction','recursive_prediction'):close(p[name],source_point[name])
    assert control_max==0 and report['control']['max_difference']==0
    # Pairing and summary rows.
    s=report['summary'];assert len(s['diagnostics'])==36 and len(s['parameters'])==90 and len(s['metrics'])==144 and len(s['holdout_points'])==2160
    for kind,keyfields,filename in [('parameters',('fit_group','model','node','parameter'),'参数配对差.csv'),('diagnostics',('fit_group','model','node'),'训练损失与诊断.csv'),('metrics',('fit_group','model','node','split','prediction'),'训练与留出指标差.csv'),('holdout_points',('fit_group','model','node','sample_id','prediction'),'逐留出预测差.csv')]:
        with (out/filename).open(encoding='utf-8-sig',newline='') as stream:csvrows=list(csv.DictReader(stream))
        assert len(csvrows)==len(s[kind])
        csv_by_key={tuple(x[k] for k in keyfields):x for x in csvrows};assert len(csv_by_key)==len(csvrows)
        maps=[]
        for fi,(fits,pts,metrics) in enumerate(records_by_solver):
            if kind=='parameters':
                obj={}
                for k,f in fits.items():
                    for param in f['parameters']:
                        for n in ('alpha','beta'):obj[(*k,n+':'+param['feeder'])]=param[n]
                    if f['delta'] is not None:obj[(*k,'delta')]=f['delta']
                maps.append(obj)
            elif kind=='diagnostics':maps.append({k:f for k,f in fits.items()})
            elif kind=='metrics':maps.append(metrics)
            else:
                obj={}
                for k,p in pts.items():
                    for pred in ('local','recursive'):obj[(*k,pred)]=p[pred+'_prediction']
                maps.append(obj)
        for row in s[kind]:
            key=tuple(row[x] for x in keyfields)
            a,b=maps[0][key],maps[1][key]
            if kind=='parameters':av,bv=a,b
            elif kind=='diagnostics':
                av=a['attempts'][a['attempt_selection']['index']]['rmse'];bv=b['attempts'][b['attempt_selection']['index']]['rmse'];
            elif kind=='metrics':av,bv=a['rmse'],b['rmse']
            else:av,bv=a,b
            if kind=='diagnostics':stored_a=row['lbfgsb_training_rmse'];stored_b=row['least_squares_training_rmse']
            elif kind=='metrics':stored_a=row['lbfgsb_rmse'];stored_b=row['least_squares_rmse']
            else:stored_a=row['lbfgsb'];stored_b=row['least_squares']
            difference=row['training_rmse_difference'] if kind=='diagnostics' else row['difference']
            close(stored_a,av);close(stored_b,bv);close(difference,bv-av if av is not None and bv is not None else None)
            csvrow=csv_by_key[key]
            for field,value in row.items():
                saved=csvrow[field]
                if value is None:assert saved==''
                elif isinstance(value,bool):assert saved.lower()==str(value).lower()
                elif isinstance(value,(int,float)):close(float(saved),value)
                elif isinstance(value,(list,dict)):assert json.loads(saved)==value
                else:assert saved==value
                counts['csv_fields']+=1
    return dict(status='passed',source_files=94,control_parameter_prediction_maximum_difference=control_max,
        solvers=2,local_fits=72,initial_vectors=432,attempt_losses=432,selected_parameter_values=180,
        holdout_points=2160,metric_rows_checked=288,maximum_absolute_difference=maximum,
        counts=counts,interpretation='scalar_recheck_of_fixed_configuration_solver_pipelines_not_algorithm_wide_benchmark')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--report-dir',required=True);a=p.parse_args();result=verify(a.report_dir)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n';(Path(a.report_dir)/'独立标量核验.json').write_text(text,encoding='utf-8',newline='\n');print(text)
