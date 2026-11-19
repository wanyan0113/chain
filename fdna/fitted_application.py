"""Audit a base fit before using its parameters in conditional network analysis."""
from copy import deepcopy
import csv
import io

from .application import analyze_application
from .engine import MODEL
from .model import ModelError
from .residuals import diagnose_residuals
from .structure import digest


def analyze_fitted_application(model, fit, protocol):
    # Recompute local predictions and verify snapshots before trusting parameters.
    residuals = diagnose_residuals(model, fit)
    if fit['status'] != 'complete':
        raise ModelError('传播应用须使用全部接受节点均已收敛的拟合报告')
    if fit['settings_snapshot']['model'] != MODEL:
        raise ModelError('当前应用扫描仅支持基础式；不能丢弃拟合δ后继续传播')
    if any(r.get('delta') is not None for r in fit['receivers']):
        raise ModelError('基础式拟合报告不可带有δ取值')
    fitted_model = deepcopy(model)
    fitted_model['scope'] = '按已核验拟合报告替换基础式参数；原文来源仅说明节点及结构，参数来源见外层报告。'
    # Do not attach reported pseudo-R² values to newly fitted parameters.
    fitted_model['reported_fit'] = []
    parameters = {(p['feeder'], row['receiver']): p
                  for row in fit['receivers'] for p in row['parameters']}
    changes = []
    for edge in fitted_model['edges']:
        p = parameters[edge['feeder'], edge['receiver']]
        changes.append(dict(feeder=edge['feeder'], receiver=edge['receiver'],
            reference_alpha=edge['alpha'], reference_beta=edge['beta'],
            fitted_alpha=p['alpha'], fitted_beta=p['beta'],
            topology_source_refs=deepcopy(edge['source_refs']), parameter_source='audited_fit_report'))
        edge.update(alpha=p['alpha'], beta=p['beta'])
    fitted = analyze_application(fitted_model, protocol)
    fitted['scope'] = '使用已核验拟合参数的条件传播与节点影响分析；参数来自拟合报告，不是原文表7报告值。'
    reference_ready = all(e['alpha'] is not None and e['beta'] is not None for e in model['edges'])
    reference = analyze_application(model, protocol) if reference_ready else None
    differences = []
    if reference is not None:
        rows = {(r['driver'], r['observer'], r['input']): r
                for r in reference['sweep_snapshot']['rows']}
        for row in fitted['sweep_snapshot']['rows']:
            ref = rows[row['driver'], row['observer'], row['input']]
            differences.append(dict(driver=row['driver'], observer=row['observer'], input=row['input'],
                reference_value=ref['value'], fitted_value=row['value'],
                difference=row['value']-ref['value'],
                reference_loss=ref['loss'], fitted_loss=row['loss']))
    ranges = []
    receivers = {e['receiver'] for e in model['edges']}
    for driver in protocol['drivers']:
        values = [r['node_values'][driver] for r in fit['samples_snapshot']['records']
                  if r['split'] == 'train' and r['node_values'].get(driver) is not None]
        low, high = (min(values), max(values)) if values else (None, None)
        ranges.append(dict(driver=driver, training_min=low, training_max=high,
            intermediate_intervention=driver in receivers,
            outside_training_range=[v for v in protocol['values'] if v < low or v > high] if values else None,
            max_absolute_response_difference=max((abs(r['difference']) for r in differences
                                                  if r['driver'] == driver), default=None)))
    return dict(format_version='1.0', status='complete', case=model['id'],
        data_kind=fit['data_kind'], case_reproduction_ready=False,
        scope='第5.3至5.4节计算衔接：先核验拟合残差，再用拟合参数重算传播；与来源参数作同条件对照。',
        input_sha256=digest(dict(case=model, fit=fit, protocol=protocol)),
        fit_report_sha256=digest(fit), reference_case_sha256=digest(model),
        fitted_case_sha256=digest(fitted_model),
        parameter_changes=changes, residual_audit=residuals,
        fitted_application=fitted, reference_application=reference,
        comparison_status='compared' if reference_ready else 'reference_parameters_missing',
        response_differences=differences,
        scan_training_ranges=ranges,
        max_absolute_response_difference=max((abs(r['difference']) for r in differences), default=None),
        source_snapshot=deepcopy(model['sources']),
        policies=dict(source_case_modified=False, refitted=False, parameter_source='audited_fit_report',
            reported_pseudo_r_squared_reused=False, holdout_used_for_optimization=False,
            nested_edge_source_refs='topology_only_for_fitted_application',
            reference_parameter_source='input_case_not_assumed_table7_for_arbitrary_cases'),
        limits=['人工样本结果只检验流程，不能替代原文推演资料',
                '逐点差值比较两套参数下的响应，不是对原文图8—9误差或复现精度的估计',
                '扫描约定沿用显式输入，不将效能横轴改称时间；不聚合法术链闭合概率',
                '局部残差很小不保证整网递推误差很小，也不保证参数唯一',
                '训练取值范围仅检查单个变量；落在范围内不证明供给变量组合或中间节点干预已获训练样本覆盖',
                '当前扫描不支持δ扩展，扩展拟合必须保留δ语义后再接入'])


def response_difference_csv(report):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=['driver', 'observer', 'input',
        'reference_value', 'fitted_value', 'difference', 'reference_loss', 'fitted_loss'])
    writer.writeheader()
    writer.writerows(report['response_differences'])
    return stream.getvalue()
