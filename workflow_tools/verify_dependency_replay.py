"""Independent stdlib audit of every exported multi-baseline intervention response."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def verify(report_path,samples_path,replay_dir):
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    report=read(report_path);rows=read(samples_path)
    analysis=read(replay_dir/'多基准依赖分析.json');audit=read(replay_dir/'多基准核验.json')
    assert hashlib.sha256(Path(report_path).read_bytes()).hexdigest()==audit['report_file_sha256']
    for name,expected in read(replay_dir/'manifest.json').items():
        assert hashlib.sha256((replay_dir/name).read_bytes()).hexdigest()==expected,name
    order=report['topology']['order'];roots=report['topology']['roots']
    by_sample={(r['fit_group'],r['sample_id']):r for r in rows}
    by_job={(j['fit_group'],j['model']):j for j in report['jobs']}
    def forward(parameters,values,delta):
        upper=math.fsum(p['alpha']*values[p['feeder']]+100*(1-p['alpha']) for p in parameters)/len(parameters)
        lower=min(values[p['feeder']]+p['beta'] for p in parameters)
        return min(upper,lower)+(delta or 0.)
    def compute(job,row,forced=None):
        source=row['node_values'] if report['config_snapshot']['input_role']=='observed' else row['predictor_values']
        values={n:source.get(n) for n in roots};forced=forced or {}
        fitted={l['node']:l['fit']['receivers'][0] for l in job['local_fits'] if l.get('fit')}
        for n in order:
            if n in forced:values[n]=forced[n];continue
            if n in roots:continue
            fit=fitted.get(n)
            if not fit or fit['status']!='fitted' or any(values[p['feeder']] is None for p in fit['parameters']):values[n]=None
            else:values[n]=forward(fit['parameters'],values,fit.get('delta'))
        return values
    states={};maximum=0.;checked=0
    for e in analysis['effects']:
        key=(e['group'],e['model'],e['baseline_sample']);job=by_job[key[:2]];row=by_sample[key[0],key[2]]
        if key not in states:states[key]=compute(job,row)
        baseline=states[key];forced=baseline[e['intervened_node']]*e['retained_fraction']
        changed_key=key+(e['intervened_node'],e['retained_fraction'])
        if changed_key not in states:states[changed_key]=compute(job,row,{e['intervened_node']:forced})
        changed=states[changed_key];target=e['target']
        expected=dict(forced_value=forced,baseline=baseline[target],intervened=changed[target],
            effect=changed[target]-baseline[target] if changed[target] is not None and baseline[target] is not None else None)
        for name,value in expected.items():
            if value is None:assert e[name] is None
            else:maximum=max(maximum,abs(value-e[name]))
        assert e['outside']==(changed[target] is not None and not 0<=changed[target]<=100)
        assert e['blocked']==(changed[target] is None)
        checked+=1
    assert maximum<1e-9
    assert checked==audit['effects']
    # Verify baseline identities separately from the application selector.
    count=analysis['config_snapshot']['baseline_count']
    for key in by_job:
        held=sorted((r for r in rows if r['fit_group']==key[0] and r['split']=='holdout'),key=lambda r:r['sample_id'])
        n=min(count,len(held));indices=[i*(len(held)-1)//(n-1) for i in range(n)] if n>1 else list(range(n))
        expected={held[i]['sample_id'] for i in indices}
        actual={c['baseline_sample'] for c in analysis['checks'] if (c['group'],c['model'])==key and 'baseline_sample' in c}
        assert actual==expected
    # Compare frozen historical first-baseline effects whenever the file is present.
    historical=Path(report_path).parent/'dependency_analysis.json';preserved=None
    if historical.exists():
        original=read(historical);index={(e['group'],e['model'],e['baseline_sample'],e['intervened_node'],e['retained_fraction'],e['target']):e for e in analysis['effects']}
        assert all(index[(e['group'],e['model'],e['baseline_sample'],e['intervened_node'],e['retained_fraction'],e['target'])]==e for e in original['effects'])
        assert original['paths']==analysis['paths'];preserved=True
    return dict(status='passed',intervention_responses_checked=checked,max_abs_discrepancy=maximum,
        manifest_hashes_checked=True,baseline_selection_checked=True,historical_first_baseline_preserved=preserved,
        optimizer_called=False,method='Independent standard-library scalar replay; no FDNA module imported')


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--samples',type=Path,required=True)
    p.add_argument('--replay-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=verify(a.report,a.samples,a.replay_dir);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
