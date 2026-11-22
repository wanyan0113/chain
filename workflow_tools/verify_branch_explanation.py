"""Audit branch labels and local slopes using stdlib scalar equations only."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def verify(report_path,samples_path,branch_dir):
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    report=read(report_path);rows=read(samples_path);result=read(branch_dir/'分支解释.json');audit=read(branch_dir/'分支核验.json')
    assert hashlib.sha256(Path(report_path).read_bytes()).hexdigest()==audit['report_file_sha256']
    for name,expected in read(branch_dir/'manifest.json').items():
        assert hashlib.sha256((branch_dir/name).read_bytes()).hexdigest()==expected,name
    jobs={(j['fit_group'],j['model']):j for j in report['jobs']}
    samples={(r['fit_group'],r['sample_id']):r for r in rows};states={};maximum=0.
    tolerance=audit['tolerance'];roots=report['topology']['roots']
    def close(actual,expected):
        nonlocal maximum
        if actual is None or expected is None:assert actual is expected;return
        maximum=max(maximum,abs(actual-expected));assert abs(actual-expected)<1e-8
    def compute(key,forced=None):
        group,model,sample=key;row=samples[group,sample]
        source=row['node_values'] if report['config_snapshot']['input_role']=='observed' else row['predictor_values']
        fitted={l['node']:l['fit']['receivers'][0] for l in jobs[group,model]['local_fits'] if l.get('fit')}
        values={n:source.get(n) for n in roots};branches={};forced=forced or {}
        for n in report['topology']['order']:
            if n in forced:values[n]=forced[n];continue
            if n in roots:continue
            fit=fitted.get(n)
            if not fit or fit['status']!='fitted' or any(values[p['feeder']] is None for p in fit['parameters']):
                values[n]=None;branches[n]=None;continue
            params=fit['parameters']
            sod=math.fsum(p['alpha']*values[p['feeder']]+100*(1-p['alpha']) for p in params)/len(params)
            candidates={'sod_mean':sod,**{'cod:'+p['feeder']:values[p['feeder']]+p['beta'] for p in params}}
            minimum=min(candidates.values());values[n]=minimum+(fit.get('delta') or 0.)
            binding=sorted(b for b,v in candidates.items() if abs(v-minimum)<=tolerance)
            branches[n]=dict(candidates=candidates,minimum=minimum,binding=binding,
                kind='near_tie' if len(binding)>1 else 'sod' if binding==['sod_mean'] else 'cod',params=params)
        return values,branches
    def baseline(key):
        if key not in states:states[key]=compute(key)
        return states[key]
    def check_constraints(stored,expected):
        assert len(stored)==len(expected['candidates'])
        assert {c['branch'] for c in stored}==set(expected['candidates'])
        for c in stored:
            close(c['value'],expected['candidates'][c['branch']]);close(c['slack'],expected['candidates'][c['branch']]-expected['minimum'])
    for r in result['baseline_nodes']:
        values,branches=baseline((r['group'],r['model'],r['baseline_sample']));b=branches[r['node']]
        close(values[r['node']],r['value'])
        if b is None:assert r['branch_type']=='blocked';continue
        assert r['binding_candidates']==b['binding'];assert r['branch_type']==b['kind']
        check_constraints(r['constraints'],b)
    for r in result['baseline_edges']:
        _,branches=baseline((r['group'],r['model'],r['baseline_sample']));b=branches[r['node']]
        p=next(p for p in b['params'] if p['feeder']==r['feeder'])
        slopes=[p['alpha']/len(b['params']) if name=='sod_mean' else float(name=='cod:'+r['feeder']) for name in b['binding']]
        close(r['candidate_slope_min'],min(slopes));close(r['candidate_slope_max'],max(slopes))
        close(r['slope'],slopes[0] if len(slopes)==1 else None)
        assert r['derivative_unique']==(len(slopes)==1)
    for r in result['response_branches']:
        key=(r['group'],r['model'],r['baseline_sample']);values,b=baseline(key)
        changed_key=key+(r['intervened_node'],r['retained_fraction'])
        if changed_key not in states:states[changed_key]=compute(key,{r['intervened_node']:values[r['intervened_node']]*r['retained_fraction']})
        changed,post=states[changed_key];target=r['target'];close(changed[target],r['intervened']);close(values[target],r['baseline'])
        if b[target] is None or post[target] is None:
            assert r['branch_changed'] is None;continue
        assert r['baseline_candidates']==b[target]['binding'];assert r['post_candidates']==post[target]['binding']
        assert r['baseline_type']==b[target]['kind'];assert r['post_type']==post[target]['kind']
        check_constraints(r['post_constraints'],post[target])
        close(r['effect'],changed[target]-values[target])
        assert r['branch_changed']==(b[target]['binding']!=post[target]['binding'])
        assert r['response_changed']==(abs(changed[target]-values[target])>tolerance)
    assert len(result['response_branches'])==audit['response_records']
    assert sum(r['branch_changed'] is True for r in result['response_branches'])==audit['branch_changes']
    assert sum(r['response_changed'] is False for r in result['response_branches'])==audit['zero_responses']
    return dict(status='passed',baseline_nodes_checked=len(result['baseline_nodes']),edge_slopes_checked=len(result['baseline_edges']),
        response_branch_records_checked=len(result['response_branches']),max_abs_discrepancy=maximum,
        manifest_hashes_checked=True,optimizer_called=False,method='Independent standard-library equations; no FDNA module imported')


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,required=True);p.add_argument('--samples',type=Path,required=True)
    p.add_argument('--branch-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=verify(a.report,a.samples,a.branch_dir);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
