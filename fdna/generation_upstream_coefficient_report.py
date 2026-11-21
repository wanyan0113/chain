"""Paired T/Q task/environment sensitivity, with actual descendant checks."""
import argparse
import io
import math
from pathlib import Path

from source_data.common import read_json, write_outputs
from .generation_coefficients import affected_nodes
from .generation_coefficient_experiment import validate_protocol
from .generation_interaction_sensitivity_report import paired_strength_metrics, verify_sample_pairing
from .generation_delta_repeats import distribution
from .generation_varying_experiment import csv_rows, dumps
from .model import ModelError
from .structure import digest


def build_report(generation, base, comparison, summary, reference, old_base, old_comparison, old_summary):
    validate_protocol(generation, base['config_snapshot'], comparison['config_snapshot'], reference, old_base, old_comparison)
    for g, b, c, s in ((generation, base, comparison, summary), (reference, old_base, old_comparison, old_summary)):
        if (any(r.get('status') != 'complete' for r in (g, b, c, s))
                or b['generation_report_sha256'] != digest(g) or c['generation_sha256'] != digest(g)
                or c['baseline_fit_sha256'] != digest(b) or s['comparison_sha256'] != digest(c)):
            raise ModelError('上游条件系数报告来源指纹或完成状态不一致')
    cfg = generation['coefficient_config_snapshot']; groups = cfg['condition_groups']
    fc = base['config_snapshot']; source = cfg['reference_experiment']['condition_sampling']['group_id']
    expected_panels = {(n, f) for n in ('T', 'Q') for f in ('task_gain', 'environment_penalty')}
    if len(groups) != 8 or {(v['node'], v['factor']) for v in groups.values()} != expected_panels:
        raise ModelError('须完整覆盖T/Q任务项、环境项四个面板的八个预设组')
    metrics = [r for c in (old_comparison, comparison) for r in c['metrics']]
    originals = generation['node_configs_snapshot']['nodes']; factors = {}; aggregates = []; paired = []
    for node, factor in sorted(expected_panels):
        original = originals[node]['condition_function'][factor]
        levels = {source: original, **{g: v['value'] for g, v in groups.items() if (v['node'], v['factor']) == (node, factor)}}
        if len(levels) != 3 or any(not math.isclose(a, b, abs_tol=1e-12, rel_tol=0)
                for a, b in zip(sorted(levels.values()), (original - .10, original, original + .10))):
            raise ModelError('本批每个内部系数须为预先登记的±0.10及冻结基线')
        factors[node, factor] = levels
        rows, changes = paired_strength_metrics([r for r in metrics if r['group'] in levels], levels,
            fc['noise_std'], fc['repetitions'], ['T', 'Q', 'R'], source)
        for r in rows + changes:
            r.update(perturbed_node=node, factor=factor, coefficient=r.pop('gamma'))
        aggregates.extend(rows); paired.extend(changes)
    pairing = verify_sample_pairing(generation, list(groups), source)
    bounds = {(r['group'], r['repetition'], r['node'], r['split']): r for r in generation['coefficient_structural_by_split']}
    checked = []
    for j in base['jobs']:
        for m in j['metrics']:
            if m['mode'] != 'local' or m['target'] != 'clean': continue
            expected = bounds[j['group'], j['repetition'], m['node'], m['split']]['clean_local_base_rmse_lower_bound']
            if not math.isclose(expected, m['rmse_lower_bound'], abs_tol=1e-10, rel_tol=0):
                raise ModelError('实际输入、目标对应的局部结构下界不一致')
            checked.append(dict(group=j['group'], repetition=j['repetition'], noise_sigma=j['noise_sigma'], **m))
    invariant_parameters = 0; unchanged = 0; branches = []
    fit_pairs = [('base', base, old_base)] + [(m, comparison['fits'][m], old_comparison['fits'][m])
        for m in ('fixed_delta', 'free_delta')]
    for mode, new, old in fit_pairs:
        lookup = {(j['noise_sigma'], j['repetition']): j for j in old['jobs'] if j['group'] == source}
        for j in new['jobs']:
            before = lookup[j['noise_sigma'], j['repetition']]
            affected = affected_nodes(generation['network_snapshot'], groups[j['group']]['node'])
            for n in set(generation['topology']['receivers']) - affected:
                a = next(l for l in j['local_fits'] if l['receiver'] == n)['fit']['receivers'][0]
                b = next(l for l in before['local_fits'] if l['receiver'] == n)['fit']['receivers'][0]
                if a['parameters'] != b['parameters'] or a['delta'] != b['delta']:
                    raise ModelError('不受影响节点的局部参数漂移')
                invariant_parameters += 1
                for m in j['metrics']:
                    if m['node'] != n: continue
                    old_m = next(r for r in before['metrics'] if all(r[k] == m[k] for k in ('node', 'split', 'target', 'mode')))
                    if any(not math.isclose(m[k], old_m[k], abs_tol=1e-12, rel_tol=0) for k in ('rmse', 'mae', 'mean_residual')):
                        raise ModelError('不受影响节点的误差指标漂移')
                    unchanged += 1
        for fit in (new, old):
            for j in fit['jobs']:
                if j['group'] not in set(groups) | {source}: continue
                for l in j['local_fits']:
                    r = l['fit']['receivers'][0]; s = r['sensitivity']
                    branches.append(dict(model=mode, group=j['group'], noise_sigma=j['noise_sigma'], repetition=j['repetition'],
                        node=l['receiver'], local_rank=s['local_jacobian_rank'], parameter_count=s['parameter_count'],
                        inactive_parameters=','.join(s['inactive_parameters']), sod_rows=s['sod_active_rows'],
                        cod_rows=dumps(s['cod_active_rows']), excluded_tie_rows=s['excluded_tie_rows'],
                        near_best_start_count=r['near_best_start_count'], near_best_parameter_spread=dumps(r['near_best_parameter_spread'])))
    delta = [r for s in (old_summary, summary) for r in s['delta_summary'] if r['group'] in set(groups) | {source}]
    outside = [r for s in (old_summary, summary) for r in s['outside_predictions'] if r['group'] in set(groups) | {source}]
    def find(panel, model, group, sigma, node='R', prediction='recursive', quantity='rmse'):
        return next(r for r in aggregates if (r['perturbed_node'], r['factor'], r['model'], r['group'], r['noise_sigma'],
            r['node'], r['split'], r['target'], r['prediction'], r['quantity'])
            == (*panel, model, group, sigma, node, 'holdout', 'clean', prediction, quantity))
    def show(r): return f"{r['mean']:.3f}" + (f" ± {r['sd']:.3f}" if r['sd'] is not None else '（单次，无SD）')
    repeats = len(fc['repetitions']); jobs = 3 * len(groups) * len(fc['noise_std']) * repeats
    noises = list(dict.fromkeys((fc['noise_std'][0], fc['noise_std'][-1])))
    experiment = cfg['reference_experiment']['base_experiment']
    labels = {'task_gain': '任务增益g', 'environment_penalty': '环境惩罚e'}
    lines = ['# T/Q任务项与环境项代表性敏感性', '',
        '执行上一批已登记的八组单因素扰动：T任务增益0.08/0.18/0.28、环境惩罚0.18/0.28/0.38；Q任务增益0.12/0.22/0.32、环境惩罚0.25/0.35/0.45，中间值均为同一冻结基线。', '',
        '任务乘数为(1−g)+gM；M<1时g增大使乘数降低，M=1时不变。环境乘数为1−eE；E>0时e增大使乘数降低，E=0时不变。只改系数，不改C/M/E抽样值；任务基础项联动为1−g。系数方向不能直接代替拟合误差方向。', '',
        f"本批{len(groups)}个新组、{jobs}组模型整网计算、{jobs*3}次接受节点拟合。每组{repeats}次数据重复，每次{experiment['base_cases']}个基础网络（{experiment['train_count']}训练、{experiment['base_cases']-experiment['train_count']}验证），观测σ={fc['noise_std']}为配对版本。共享根输入、逐节点条件、划分与噪声创新；冻结基线不重拟合。", '',
        f"过程波动和交互关闭，三模型为基础式δ=0、预设固定δ=-5、训练集估计的自由δ[-10,10]；沿用{fc['settings']['starts']}个起点和原求解设置。FDNA预测只读上游SE，未将C/M/E加入预测端；预测不裁剪。", '',
        '| 扰动节点 | 因素 | 值 | 观测σ | 基础式R | 固定δ R | 自由δ R |', '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for panel, levels in factors.items():
        for g in sorted(levels, key=levels.get):
            for sigma in noises:
                lines.append(f'| {panel[0]} | {labels[panel[1]]} | {levels[g]:.2f} | {sigma} | '
                    + ' | '.join(show(find(panel, m, g, sigma)) for m in ('base', 'fixed_delta', 'free_delta')) + ' |')
    lines += ['', '表中为末端R对干净验证目标的整网RMSE；±为重复间样本标准差，不是置信区间。四个面板引用同一冻结基线，不重复计为独立实验。跨组输入与目标可能变化，RMSE差不是单独的因果传播误差。', '',
        '| 组 | 自由δ触下界/节点估计数 | 自由δ局部秩不足 | 含δ越界预测条目 |', '| --- | ---: | ---: | ---: |']
    for g in [source, *groups]:
        rows = [r for r in delta if r['group'] == g and r['model'] == 'free_delta']
        lines.append(f"| {g} | {sum(r['lower_bound_count'] for r in rows)}/{sum(r['n'] for r in rows)} | "
            f"{sum(r['rank_deficient_count'] for r in rows)} | {sum(r['group']==g for r in outside)} |")
    lines += ['', '估计数跨接受节点、配对噪声及重复；越界条目另含模型、划分和局部/整网维度，不是独立实验次数。预测表现、参数识别与输出范围仍须分别报告。', '',
        f'{pairing}个观测版本配对、{len(checked)}项同数据基础式局部结构下界核对通过；不受影响节点的{invariant_parameters}次局部拟合参数和{unchanged}项指标与基线一致。改T允许T/Q/R变化；改Q允许Q/R变化，T应不变。受影响节点均按原设置重新拟合。下界不直接用于含δ或整网误差。', '',
        '本批补齐已登记的T/Q任务项和环境项代表性扰动，连同既有能力项、R任务/环境、R权重及T互补系数，形成预设代表性单因素覆盖；不代表全参数空间或联合敏感性已完成。敏感性新组的完整有限扰动、参数补偿、残差及输入质量诊断仍待补齐，随后开展多输入已知参数恢复、正式多输入及11节点18边独立拟合。', '']
    out = {'结果解释.md': '\n'.join(lines), 'upstream_metrics.csv': csv_rows(aggregates),
        'paired_upstream_changes.csv': csv_rows(paired), 'generation_shifts.csv': csv_rows(generation['coefficient_shifts']),
        'structural_crosscheck.csv': csv_rows(checked), 'rank_and_branches.csv': csv_rows(branches),
        'all_upstream_delta.csv': csv_rows(delta), 'outside_predictions.csv': csv_rows(outside),
        '汇报来源核验.json': dumps(dict(status='complete', generation_sha256=digest(generation), comparison_sha256=digest(comparison),
            reference_comparison_sha256=digest(old_comparison), new_model_jobs=jobs, new_receiver_fits=jobs*3,
            paired_observation_checks=pairing, structural_checks=len(checked), invariant_local_fits=invariant_parameters,
            unchanged_metric_checks=unchanged))}
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    def save(fig, name):
        for ext in ('png', 'svg'):
            b = io.BytesIO(); fig.savefig(b, format=ext, dpi=160); out[name+'.'+ext] = b.getvalue()
    modes = [('base', '基础式', '#367AA8'), ('fixed_delta', '固定δ', '#32986B'), ('free_delta', '自由δ', '#D78C36')]
    panel_order = [(n, f) for n in ('T', 'Q') for f in ('task_gain', 'environment_penalty')]
    with rc_context({'font.family': 'Microsoft YaHei', 'axes.unicode_minus': False, 'font.size': 10}):
        for quantity, name, ylabel in [('rmse', 'upstream_rmse', '末端R整网验证RMSE（SE点）'),
                ('mean_residual', 'upstream_residual', '平均残差：预测−目标（SE点）')]:
            for sigma in noises:
                fig = Figure(figsize=(12, 8), layout='constrained'); FigureCanvasAgg(fig); axes = fig.subplots(2, 2)
                for ax, panel in zip(axes.flat, panel_order):
                    levels = factors[panel]; order = sorted(levels, key=levels.get)
                    for mode, label, color in modes:
                        rows = [find(panel, mode, g, sigma, quantity=quantity) for g in order]
                        ax.errorbar([levels[g] for g in order], [r['mean'] for r in rows],
                            yerr=[r['sd'] or 0 for r in rows], marker='o', capsize=3, label=label, color=color)
                    ax.set(title=f'只改{panel[0]}的{labels[panel[1]]}', xlabel=labels[panel[1]], ylabel=ylabel,
                        xticks=[levels[g] for g in order]); ax.grid(alpha=.2)
                fig.legend(*axes[0, 0].get_legend_handles_labels(), loc='outside lower center', ncol=3)
                fig.suptitle(f'T/Q条件系数敏感性：观测σ={sigma}（{repeats}次重复，误差棒为样本标准差）')
                save(fig, f'{name}_sigma{sigma}')
        fig = Figure(figsize=(12, 8), layout='constrained'); FigureCanvasAgg(fig); axes = fig.subplots(2, 2)
        for ax, panel in zip(axes.flat, panel_order):
            levels = factors[panel]; order = sorted(levels, key=levels.get)
            actual = [find(panel, 'base', g, fc['noise_std'][0], node=panel[0], prediction='local') for g in order]
            values = [distribution([r['clean_local_base_rmse_lower_bound']
                for r in reference['structural_by_split'] + generation['coefficient_structural_by_split']
                if r['group'] == g and r['node'] == panel[0] and r['split'] == 'holdout']) for g in order]
            for rows, label, color in [(actual, '基础式局部RMSE', '#367AA8'), (values, '同数据结构下界', '#8999AA')]:
                ax.errorbar([levels[g] for g in order], [r['mean'] for r in rows], yerr=[r['sd'] or 0 for r in rows],
                    marker='o', capsize=3, label=label, color=color)
            ax.set(title=f'被扰动节点{panel[0]}：{labels[panel[1]]}', xlabel=labels[panel[1]], ylabel='SE点',
                xticks=[levels[g] for g in order]); ax.grid(alpha=.2)
        fig.legend(*axes[0, 0].get_legend_handles_labels(), loc='outside lower center', ncol=2)
        fig.suptitle(f"基础式局部误差与同数据下界（观测σ={fc['noise_std'][0]}；均值±样本标准差）")
        save(fig, 'upstream_bound')
    return out


def main():
    p = argparse.ArgumentParser(description='T/Q任务和环境生成系数配对敏感性汇报')
    for flag in ('experiment-dir', 'reference-dir'): p.add_argument('--'+flag, type=Path, required=True)
    args = p.parse_args()
    def load(d): return [read_json(d/f) for f in ('generation/generation_report.json', 'base/fit_report.json',
        'comparison/delta_comparison_report.json', 'comparison/delta_repeats_report.json')]
    write_outputs(args.experiment_dir/'report', build_report(*load(args.experiment_dir), *load(args.reference_dir)))


if __name__ == '__main__': main()
