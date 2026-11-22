"""Common-source scenarios are kept separate from directed FDNA dependency edges."""
from .generation_network import NetworkGenerator
from .network_workflow import recursive, select_baselines


def validate_shared_roots(network, sidecar, experiment):
    roots=set(network['nodes'])-{e['receiver'] for e in network['edges']}
    groups=experiment.get('root_coupling',[])
    for policy in sidecar.get('state_sharing',[]):
        instances=set(policy['instance_ids'])
        if instances <= roots:
            matches=[g for g in groups if set(g['nodes'])==instances]
            if len(matches)!=1:
                raise ValueError('共享根主体必须有且仅有一组共同来源采样配置')


def shared_response(network, nodes, experiment, states, rows, report, fractions):
    groups=experiment.get('root_coupling',[])
    if not groups:return []
    generator=NetworkGenerator(dict(format_version='1.0',basis='共同来源合成情景',**network),nodes)
    targets=sorted(set(network['nodes'])-{e['feeder'] for e in network['edges']})
    lookup={s['base_case_id']:s for s in states};result=[]
    for job in report['jobs']:
        candidates=[r for r in rows if r['fit_group']==job['fit_group'] and r['split']=='holdout']
        baselines=select_baselines(candidates,report['config_snapshot']['analysis'])
        training=[r for r in rows if r['fit_group']==job['fit_group'] and r['split']=='train']
        ranges={n:(min(r['predictor_values'][n] for r in training),max(r['predictor_values'][n] for r in training)) for n in generator.roots}
        local={f['node']:f['fit']['receivers'][0] for f in job['local_fits'] if f.get('fit')}
        for row in baselines:
            original={n:row['predictor_values'][n] for n in generator.roots}
            baseline,_=recursive(network,local,original)
            for group in groups:
                latent=lookup[row['base_case_id']]['root_latents'][group['id']]
                for fraction in fractions:
                    roots=dict(original);clipped=False
                    for n in group['nodes']:
                        raw=latent*fraction+group['offsets'][n]
                        roots[n]=max(0,min(100,raw));clipped=clipped or roots[n]!=raw
                    generated=generator.evaluate(roots)['node_values_clean']
                    predicted,_=recursive(network,local,roots)
                    for n in targets:
                        value=predicted[n];base=baseline[n]
                        result.append(dict(fit_group=job['fit_group'],model=job['model'],baseline_sample=row['sample_id'],
                            common_source=group['id'],retained_fraction=fraction,target=n,baseline=base,predicted=value,
                            effect=None if value is None or base is None else value-base,
                            independent_mechanism=generated[n],independent_baseline=lookup[row['base_case_id']]['values'][n],
                            root_clipped=clipped,prediction_outside=value is not None and not 0<=value<=100,
                            root_outside_training=any(not ranges[k][0]<=v<=ranges[k][1] for k,v in roots.items())))
    return result


def shared_figure(out, effects):
    if not effects:return
    import matplotlib.pyplot as plt
    group=sorted({e['fit_group'] for e in effects})[-1]
    selected=[e for e in effects if e['fit_group']==group]
    baseline=min(e['baseline_sample'] for e in selected);source=min(e['common_source'] for e in selected)
    selected=[e for e in selected if e['baseline_sample']==baseline and e['common_source']==source]
    targets=sorted({e['target'] for e in selected})
    fig,axes=plt.subplots(1,len(targets),figsize=(5*len(targets),4),squeeze=False,sharey=True)
    for ax,target in zip(axes[0],targets):
        own=[e for e in selected if e['target']==target]
        for model in sorted({e['model'] for e in own}):
            items=sorted([e for e in own if e['model']==model],key=lambda e:e['retained_fraction'])
            ax.plot([e['retained_fraction']*100 for e in items],[e['predicted'] for e in items],'-o',label={'base':'基础FDNA','free_delta':'自由δ'}.get(model,model))
        reference=sorted([e for e in own if e['model']==own[0]['model']],key=lambda e:e['retained_fraction'])
        ax.plot([e['retained_fraction']*100 for e in reference],[e['independent_mechanism'] for e in reference],'--',color='black',label='独立机制参考')
        ax.set(title=target+'任务结果',xlabel='共同来源状态保留比例（%）',ylabel='预测SE');ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle('共同状态情景：同时改变同一主体的多个任务产品；不新增跨任务FDNA边')
    fig.tight_layout()
    for suffix in ('png','svg'):fig.savefig(out/('04_共同来源情景响应.'+suffix),dpi=170,bbox_inches='tight')
    plt.close(fig)
