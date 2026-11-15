"""Audit existing fit residuals against measured samples without fitting again."""
from copy import deepcopy
import csv
import io
import math

from .additive import ADDITIVE_MODEL
from .fitting import _validate
from .model import ModelError, _finite, validate_model
from .structure import digest


def diagnose_residuals(model, fit):
    validate_model(model)
    _finite(fit)
    if not isinstance(fit, dict):
        raise ModelError('残差诊断需要已有拟合报告')
    if fit.get('status') not in ('complete','blocked'):
        raise ModelError('拟合报告缺少有效的整体求解状态')
    try:
        samples, settings = fit['samples_snapshot'], fit['settings_snapshot']
        _validate('samples', samples)
        _validate('settings', settings)
        if (samples['case_sha256'] != digest(model) or fit['case'] != model['id'] or
                fit['data_kind'] != samples['data_kind'] or fit['input_sha256'] !=
                digest({'case': model, 'samples': samples, 'settings': settings})):
            raise ModelError('拟合报告与案例、样本或设置版本不一致')
        records = {r['id']: r for r in samples['records']}
        if len(records) != len(samples['records']):
            raise ModelError('拟合样本标识重复')
        if any(set(r['node_values'])-{n['id'] for n in model['nodes']} for r in records.values()):
            raise ModelError('拟合样本引用未登记能力节点')
        incoming = {r: [e for e in model['edges'] if e['receiver'] == r]
                    for r in sorted({e['receiver'] for e in model['edges']})}
        reports = {r['receiver']: r for r in fit['receivers']}
        if set(reports) != set(incoming) or len(reports) != len(fit['receivers']):
            raise ModelError('拟合报告须逐接受节点登记且不重复')
        expected_status = 'complete' if all(r['status']=='fitted' for r in reports.values()) else 'blocked'
        if fit['status'] != expected_status:
            raise ModelError('整体求解状态与逐接受节点状态不一致')
        additive = settings['model'] == ADDITIVE_MODEL
        series = []
        for receiver, edges in incoming.items():
            row = reports[receiver]
            feeders = [e['feeder'] for e in edges]
            valid = {split: [r for r in records.values() if r['split'] == split and
                     all(r['node_values'].get(n) is not None for n in [receiver,*feeders])]
                     for split in ('train','holdout')}
            minimum = 2*len(feeders)+int(additive)
            if row['status'] == 'insufficient_samples':
                if len(valid['train']) >= minimum or 'metrics' in row:
                    raise ModelError('样本不足状态与实际样本行数不一致')
                series.extend({'receiver': receiver, 'split': split, 'fit_status': row['status'],
                               'status': 'no_fit', 'metrics': None, 'rows': []}
                              for split in ('train','holdout'))
                continue
            if row['status'] not in ('fitted','iteration_limit') or len(valid['train']) < minimum:
                raise ModelError('不支持或不完整的拟合状态')
            params = {p['feeder']: p for p in row['parameters']}
            if set(params) != set(feeders) or len(params) != len(row['parameters']) or row['feeders'] != feeders:
                raise ModelError('拟合参数的供给节点与案例不一致')
            for p in params.values():
                if (isinstance(p['alpha'],bool) or isinstance(p['beta'],bool) or
                        not 0 <= p['alpha'] <= 1 or not 0 <= p['beta'] <= 100):
                    raise ModelError('拟合参数超出有效范围')
            delta = row['delta'] if additive else 0
            if additive and (isinstance(delta,bool) or delta is None or not
                             settings['delta_bounds'][receiver][0] <= delta <= settings['delta_bounds'][receiver][1]):
                raise ModelError('拟合δ与声明界限不一致')
            for split in ('train','holdout'):
                metrics = row['metrics'][split]
                if not valid[split]:
                    if metrics is not None:
                        raise ModelError('没有对应样本时不可提供残差统计')
                    series.append({'receiver': receiver, 'split': split, 'fit_status': row['status'],
                                   'status': 'no_samples', 'metrics': None, 'rows': []})
                    continue
                points = metrics['rows']
                by_id = {p['id']: p for p in points}
                if len(by_id) != len(points) or set(by_id) != {r['id'] for r in valid[split]}:
                    raise ModelError('残差行遗漏、重复或混入其他样本集合')
                checked = []
                for sample in valid[split]:
                    values = sample['node_values']
                    sod = math.fsum(100-params[f]['alpha']*(100-values[f]) for f in feeders)/len(feeders)
                    cod = min(values[f]+params[f]['beta'] for f in feeders)
                    predicted, observed = min(sod,cod)+delta, values[receiver]
                    point = by_id[sample['id']]
                    expected = {'observed': observed,'predicted': predicted,'residual': predicted-observed}
                    if any(isinstance(point[k],bool) or not math.isclose(point[k],v,rel_tol=1e-10,abs_tol=1e-8)
                           for k,v in expected.items()):
                        raise ModelError('残差、拟合值或观测值与样本及参数重算不一致')
                    checked.append({'id': sample['id'], **expected})
                residuals = [p['residual'] for p in checked]
                stats = {'count': len(checked), 'rmse': math.sqrt(math.fsum(r*r for r in residuals)/len(checked)),
                         'mae': math.fsum(abs(r) for r in residuals)/len(checked),
                         'mean_residual': math.fsum(residuals)/len(checked),
                         'max_absolute_error': max(abs(r) for r in residuals)}
                if (isinstance(metrics['count'],bool) or metrics['count'] != stats['count'] or
                        any(isinstance(metrics[k],bool) or not math.isclose(metrics[k], stats[k],rel_tol=1e-10,abs_tol=1e-8)
                        for k in ('rmse','max_absolute_error'))):
                    raise ModelError('拟合报告汇总误差与逐行残差不一致')
                series.append({'receiver': receiver, 'split': split, 'fit_status': row['status'],
                               'status': 'audited', 'metrics': stats, 'rows': checked})
    except (KeyError,TypeError,AttributeError,OverflowError) as exc:
        raise ModelError('拟合报告字段不完整或数值类型无效') from exc
    return {'format_version': '1.0', 'status': 'complete' if any(s['rows'] for s in series) else 'blocked',
            'case': model['id'], 'data_kind': samples['data_kind'],
            'scope': '第5.4.1节残差诊断工具：核对已有拟合报告并分别展示训练与留出残差，不重新优化，不认定原文图7已复现。',
            'case_reproduction_ready': False, 'input_sha256': digest({'case':model,'fit':fit}),
            'fit_report_sha256': digest(fit), 'source_fit_status': fit['status'],
            'series': series, 'fit_snapshot': deepcopy(fit), 'source_snapshot': deepcopy(model['sources']),
            'policies': {'residual_definition':'predicted_minus_observed', 'refitted':False,
                         'splits_pooled':False, 'pseudo_r_squared':None, 'automatic_outlier_removal':False,
                         'acceptance_threshold':None, 'value_audit_absolute_tolerance':1e-8},
            'limits': ['人工样本仅作方法检查，用户提供数据的真实性仍须核实',
                       '残差图不是原文图7的读图点或原始记录；不能据此重算未知定义的伪R²',
                       '没有收敛的拟合仍展示其已有残差，并保留求解状态；样本不足时不生成残差',
                       '这些误差统计不证明参数唯一、误差独立或正态，也不自动判定拟合可接受']}


def residual_csv(report):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=['receiver','split','fit_status','id','observed','predicted','residual'])
    writer.writeheader()
    for series in report['series']:
        for row in series['rows']:
            writer.writerow({k:series[k] for k in ('receiver','split','fit_status')} | row)
    return stream.getvalue()


def plot_residuals(report):
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ImportError as exc:
        raise ModelError('绘图需要matplotlib；不加--plot仍可导出残差诊断') from exc
    receivers = list(dict.fromkeys(s['receiver'] for s in report['series']))
    fig = Figure(figsize=(12,3*len(receivers)),layout='constrained')
    FigureCanvasAgg(fig)
    axes = fig.subplots(len(receivers),2,squeeze=False)
    for i,receiver in enumerate(receivers):
        for j,split in enumerate(('train','holdout')):
            axis=axes[i,j]
            series=next(s for s in report['series'] if s['receiver']==receiver and s['split']==split)
            points=series['rows']
            if points:
                axis.scatter([p['predicted'] for p in points],[p['residual'] for p in points],
                             s=12,alpha=.65,color=f'C{j}')
                stats=series['metrics']
                axis.set_title(f"{receiver} | {split} | n={stats['count']} | RMSE={stats['rmse']:.4g}")
                if series['fit_status']!='fitted':
                    axis.text(.02,.95,'Solver did not converge',transform=axis.transAxes,va='top',color='darkred')
            else:
                axis.set_title(receiver+' | '+split)
                axis.text(.5,.5,'Insufficient fitting samples' if series['status']=='no_fit' else 'No samples in this split',
                          transform=axis.transAxes,ha='center',va='center')
            axis.axhline(0,color='black',linestyle='--',linewidth=.8)
            axis.set(xlabel='Predicted SE',ylabel='Residual (SE)')
            axis.grid(alpha=.2)
    kind='Artificial method check' if report['data_kind']=='method_check' else 'User-supplied data (source unverified)'
    fig.suptitle(kind+' | Residual = predicted - observed\nTraining (left) and holdout (right) | Not original Figure 7')
    stream=io.BytesIO()
    fig.savefig(stream,format='png',dpi=150)
    return stream.getvalue()
