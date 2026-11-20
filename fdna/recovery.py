"""Two-feeder recovery experiment; truth stays outside the fitted model."""
from copy import deepcopy
import csv
import io
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from .engine import MODEL, evaluate
from .fitting import _libraries, fit_parameters
from .model import ModelError, _finite, validate_model
from .residuals import diagnose_residuals
from .structure import digest


def validate_recovery(config):
    _finite(config)
    schema = json.loads(Path(__file__).with_name('recovery_schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(config))
    if errors:
        raise ModelError('参数恢复配置不符合数据契约：' + errors[0].message)
    ids = [c['id'] for c in config['cases']]
    if len(ids) != len(set(ids)):
        raise ModelError('参数恢复案例编号不能重复')


def recovery_model(identifier):
    refs = ['equations:eq1_eq2']
    model = dict(format_version='1.0', id='recovery_'+identifier,
        label='双供给节点参数恢复检查', scope='人工三节点方法检查；拟合参数未知，不使用表7。',
        sources=[dict(id='equations', kind='literature',
            reference='DOI:10.16182/j.issn1004731x.joss.23-1542，仅引用式(1)—(2)',
            sha256='210ab50b6d97c68f0c402336ef05fb6ca5bb659f84f42d663d66b11a1093f5e5',
            locators=dict(eq1_eq2='第1079页式(1)—(2)；参数与样本均为人工方法检查'))],
        subjects=[dict(id='U_'+n,label='示例主体'+n,source_refs=refs) for n in ('A','B','R')],
        targets=[], chains=[], reported_fit=[], gaps=[],
        nodes=[dict(id=n,label='供给能力'+n if n!='R' else '接受能力R',subject='U_'+n,
                    role='insight' if n!='R' else 'coordination',
                    indicator=dict(label='人工SE',direction='unspecified',normalization=None),
                    source_refs=refs) for n in ('A','B','R')],
        edges=[dict(feeder=n,receiver='R',alpha=None,beta=None,source_refs=refs) for n in ('A','B')],
        case_extension=dict(delta={'R':None},delta_scope=None,output_policy=None,source_refs=refs))
    validate_model(model)
    return model


def independent_forward(a, b, alpha, beta):
    """Expanded scalar equations independent of the solver's vectorized predictor."""
    sod = ((alpha[0]*a+100*(1-alpha[0])) + (alpha[1]*b+100*(1-alpha[1])))/2
    ca, cb = a+beta[0], b+beta[1]
    return min(sod, ca, cb), sod, ca, cb


def _coverage(inputs, truth, np):
    jac, counts = [], dict(sod=0, cod_A=0, cod_B=0, ties=0)
    margin = 1e-7
    for a,b in inputs:
        _, sod, ca, cb = independent_forward(a,b,truth['alpha'],truth['beta'])
        if sod < min(ca,cb)-margin:
            counts['sod'] += 1
            jac.append([-(100-a)/2, -(100-b)/2, 0, 0])
        elif ca < min(sod,cb)-margin:
            counts['cod_A'] += 1
            jac.append([0,0,100,0])
        elif cb < min(sod,ca)-margin:
            counts['cod_B'] += 1
            jac.append([0,0,0,100])
        else:
            counts['ties'] += 1
            jac.append([0,0,0,0])
    singular = np.linalg.svd(np.asarray(jac),compute_uv=False)
    rank = int(np.sum(singular > max(1e-7, float(singular[0])*1e-7)))
    return dict(counts=counts, local_jacobian_rank=rank, parameter_count=4,
                global_uniqueness_established=False)


def run_recovery(config):
    validate_recovery(config)
    np, _, _ = _libraries()
    streams = np.random.SeedSequence(config['seed']).spawn(3)
    train = np.random.default_rng(streams[0]).uniform(0,100,(config['random_train'],2))
    holdout = np.random.default_rng(streams[1]).uniform(0,100,(config['random_holdout'],2))
    optimizer_seed = int(streams[2].generate_state(1)[0])
    results=[]
    for truth in config['cases']:
        model = recovery_model(truth['id'])
        samples=dict(format_version='1.0',id=config['id']+'_'+truth['id'],data_kind='method_check',
                     basis=config['basis'],case_sha256=digest(model),records=[])
        forward_model=deepcopy(model)
        for i,edge in enumerate(forward_model['edges']):
            edge.update(alpha=truth['alpha'][i],beta=truth['beta'][i])
        max_forward_error=0.0
        for split,inputs in [('train',train),('holdout',holdout)]:
            for i,(a,b) in enumerate(inputs):
                value,*_=independent_forward(float(a),float(b),truth['alpha'],truth['beta'])
                scenario=dict(id=f'{split}:{i}',basis='独立展开公式与核心正向计算对照',
                              node_values=dict(A=float(a),B=float(b)))
                core=evaluate(forward_model,scenario)['nodes'][-1]['value']
                max_forward_error=max(max_forward_error,abs(value-core))
                samples['records'].append(dict(id=f'{split}:{i}',split=split,
                    node_values=dict(A=float(a),B=float(b),R=value)))
        settings=dict(format_version='1.0',id=config['id']+'_solver',basis='未知参数拟合；不读取真值作起点',
                      model=MODEL,solver='L-BFGS-B',seed=optimizer_seed,starts=config['starts'],
                      max_nfev=config['max_nfev'],delta_policy='excluded_for_base_check')
        fit=fit_parameters(model,samples,settings)
        audit=diagnose_residuals(model,fit)
        receiver=fit['receivers'][0]
        parameters=[]
        for i,p in enumerate(receiver['parameters']):
            for name in ('alpha','beta'):
                known=truth[name][i]
                error=p[name]-known
                parameters.append(dict(feeder=p['feeder'],parameter=name,known=known,
                    fitted=p[name],error=error,absolute_error=abs(error),
                    relative_error=abs(error)/abs(known) if known else None))
        metrics={}
        for series in audit['series']:
            metrics[series['split']]=deepcopy(series['metrics'])
        coverage=_coverage(train,truth,np)
        criteria=dict(
            forward_equivalent=max_forward_error <= config['acceptance']['forward_absolute_error'],
            converged=fit['status']=='complete',
            training_supports_local_identification=coverage['local_jacobian_rank']==4,
            fitted_local_rank_full=receiver['sensitivity']['local_jacobian_rank']==4,
            alpha_recovered=max(p['absolute_error'] for p in parameters if p['parameter']=='alpha')
                            <= config['acceptance']['alpha_absolute_error'],
            beta_recovered=max(p['absolute_error'] for p in parameters if p['parameter']=='beta')
                           <= config['acceptance']['beta_absolute_error'],
            train_prediction=metrics['train']['rmse']<=config['acceptance']['prediction_rmse'],
            holdout_prediction=metrics['holdout']['rmse']<=config['acceptance']['prediction_rmse'])
        outcome=('passed' if all(criteria.values()) else 'formula_mismatch' if not criteria['forward_equivalent']
                 else 'solver_not_converged' if not criteria['converged']
                 else 'insufficient_identifiability' if not criteria['training_supports_local_identification']
                 else 'not_recovered')
        results.append(dict(id=truth['id'],outcome=outcome,criteria=criteria,
            known_parameters=deepcopy(truth),parameter_comparison=parameters,metrics=metrics,
            true_training_coverage=coverage,max_forward_absolute_error=max_forward_error,
            fitting_case=model,fit_report=fit))
    return dict(format_version='1.0',status='complete' if all(r['outcome']=='passed' for r in results) else 'blocked',
        scope='第一阶段无噪声双供给节点参数恢复；只检查公式、求解及指定样本条件，不证明真实场景有效性或全局唯一。',
        data_kind='method_check',case_reproduction_ready=False,config_snapshot=deepcopy(config),
        input_sha256=digest(config),
        random_streams=dict(train_spawn_key=list(streams[0].spawn_key),holdout_spawn_key=list(streams[1].spawn_key),
                            optimizer_spawn_key=list(streams[2].spawn_key),optimizer_seed=optimizer_seed),
        summary=dict(cases=len(results),passed=sum(r['outcome']=='passed' for r in results),
                     random_train=config['random_train'],random_holdout=config['random_holdout']),
        cases=results,
        policies=dict(truth_used_for_initialization=False,holdout_used_for_optimization=False,
                      input_distribution='independent Uniform(0,100)',noise='none',delta='excluded',
                      paired_inputs_across_parameter_cases=True,reported_table7_used=False,
                      generator='independent_expanded_scalar_equations',fit='existing_bounded_fit_parameters'))


def recovery_csvs(report):
    outputs={}
    for name,fields,rows in [
        ('parameter_comparison.csv',['case','feeder','parameter','known','fitted','error','absolute_error','relative_error'],
         [dict(case=c['id'],**p) for c in report['cases'] for p in c['parameter_comparison']]),
        ('predictions.csv',['case','id','split','A','B','observed','predicted','residual'],
         [dict(case=c['id'],id=p['id'],split=split,
               A=sample['node_values']['A'],B=sample['node_values']['B'],
               observed=p['observed'],predicted=p['predicted'],residual=p['residual'])
          for c in report['cases'] for split in ('train','holdout')
          for p,sample in zip(c['fit_report']['receivers'][0]['metrics'][split]['rows'],
              [r for r in c['fit_report']['samples_snapshot']['records'] if r['split']==split])])]:
        stream=io.StringIO(newline='')
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
        outputs[name]=stream.getvalue()
    return outputs


def render_recovery(report):
    config=report['config_snapshot']
    lines=['# 第一阶段 双供给节点参数恢复','',report['scope'],'',
           f"每组随机训练样本{config['random_train']}条，独立验证样本{config['random_holdout']}条；总种子{config['seed']}。",
           '两个供给节点分别从U(0,100)独立抽样。三组人工真值共用输入，分别拟合；不使用表7。',
           f"采用L-BFGS-B及{config['starts']}个中性或固定种子随机起点；真值未传给拟合器。",'',
           '| 案例 | 参数 | 预设值 | 拟合值 | 绝对误差 |',
           '| --- | --- | ---: | ---: | ---: |']
    for c in report['cases']:
        for p in c['parameter_comparison']:
            lines.append(f"| {c['id']} | {p['parameter']}:{p['feeder']} | {p['known']:.8g} | {p['fitted']:.12g} | {p['absolute_error']:.6g} |")
    lines+=['','| 案例 | 训练RMSE | 验证RMSE | 验证MAE | 判定 |','| --- | ---: | ---: | ---: | --- |']
    for c in report['cases']:
        lines.append(f"| {c['id']} | {c['metrics']['train']['rmse']:.6g} | {c['metrics']['holdout']['rmse']:.6g} | {c['metrics']['holdout']['mae']:.6g} | {c['outcome']} |")
    lines+=['','训练输入的有效分支覆盖：','']
    for c in report['cases']:
        coverage=c['true_training_coverage']
        lines+=[f"- {c['id']}：{coverage['counts']}；真值处局部敏感度秩{coverage['local_jacobian_rank']}/4。"]
    lines+=['','自检阈值：'+json.dumps(config['acceptance'],ensure_ascii=False),
            '', '本结果检验无噪声、输入有覆盖时的数值恢复；不证明任意参数均可辨识，不替代独立机制实验，也不表示已求回原文表7。',
            'predictions.csv保存每条随机输入、目标、预测和残差；parameter_comparison.csv保存全部参数对照；JSON保留完整配置、样本、起点、收敛记录及指纹。',
            '', '重跑命令（输出目录须尚未使用）：', '', '```powershell',
            "Set-Location '"+str(Path(__file__).resolve().parents[1])+"'",
            "& '"+sys.executable+"' -B -m fdna recovery --config data/fdna/recovery_check.json --output-dir generated/recovery-new",
            '```','']
    return '\n'.join(lines)
