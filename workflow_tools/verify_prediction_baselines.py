"""Independent scalar verification of the single-input 06 baseline artifact."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(directory):
    out = Path(directory); report = read(out/'baseline_report.json')
    source = Path(report['source']['directory'])
    package = read(source/'完整包校验.json')
    assert sha(source/'完整包校验.json') == report['source']['package_index_sha256']
    for name, expected in package.items():
        assert sha(source/name) == expected, name
    for name, expected in read(out/'manifest.json').items():
        assert sha(out/name) == expected, name
    frozen = read(source/'04_FDNA拟合/workflow_report.json')
    rows = read(source/'04_FDNA拟合/输入_SE记录.json')
    assert frozen['config_snapshot']['input_role'] == 'observed', '本独立标量核验针对06 observed数据'
    network = report['network_snapshot']
    assert network == frozen['network_snapshot']
    parents = defaultdict(list)
    for edge in network['edges']:
        parents[edge['receiver']].append(edge['feeder'])
    assert all(len(v) == 1 for v in parents.values()), '本核验公式针对06单直接上游结构'
    roots = sorted(set(network['nodes']) - set(parents))
    order = list(roots)
    while len(order) < len(network['nodes']):
        ready = [n for n in sorted(parents) if n not in order and parents[n][0] in order]
        assert ready, '拓扑成环'
        order.extend(ready)
    sample = {(r['fit_group'], r['sample_id']): r for r in rows}
    fits = {(f['fit_group'], f['method'], f['node']): f for f in report['baseline_fits']}
    assert len(fits) == len(report['baseline_fits']) == 36
    point_keys = {(p['fit_group'], p['method'], p['sample_id'], p['node']) for p in report['points']}
    assert len(point_keys) == len(report['points']) == 8640
    assert point_keys == {(r['fit_group'], method, r['sample_id'], node) for r in rows
                          for method in ('training_mean', 'ols_intercept', 'fdna:base', 'fdna:free_delta') for node in parents}
    metric_keys = {tuple(m[k] for k in ('fit_group', 'node', 'method', 'split', 'prediction', 'evaluation_scope'))
                   for m in report['metrics']}
    assert len(metric_keys) == len(report['metrics']) == 576
    maximum = 0.; coefficient_checks = 0

    def close(a, b):
        nonlocal maximum
        if a is None or b is None:
            assert a is None and b is None, (a, b)
            return
        assert math.isfinite(a) and math.isfinite(b)
        difference = abs(a-b); maximum = max(maximum, difference)
        assert difference <= 1e-9, (a, b, difference)

    for (group, method, node), fit in fits.items():
        parent = parents[node][0]
        train = sorted((r for r in rows if r['fit_group'] == group and r['split'] == 'train'
                        and r['node_values'].get(parent) is not None and r['node_values'].get(node) is not None),
                       key=lambda r: r['sample_id'])
        assert fit['train_sample_ids'] == [r['sample_id'] for r in train]
        assert fit['train_count'] == len(train) == 90
        x = [r['node_values'][parent] for r in train]; y = [r['node_values'][node] for r in train]
        xm = math.fsum(x)/len(x); ym = math.fsum(y)/len(y)
        expected = [ym]
        if method == 'ols_intercept':
            slope = math.fsum((a-xm)*(b-ym) for a, b in zip(x, y))/math.fsum((a-xm)**2 for a in x)
            expected = [ym-slope*xm, slope]
        assert len(expected) == len(fit['coefficients'])
        for a, b in zip(expected, fit['coefficients']):
            close(a, b); coefficient_checks += 1
    frozen_points = {(j['fit_group'], 'fdna:'+j['model'], p['sample_id'], p['node']): p
                     for j in frozen['jobs'] for p in j['points']}
    values_cache = {}; prediction_checks = 0
    for point in report['points']:
        group, method, sid, node = [point[k] for k in ('fit_group', 'method', 'sample_id', 'node')]
        row = sample[group, sid]; inputs = row['node_values']
        assert point['target'] == inputs.get(node)
        assert point['base_case_id'] == row['base_case_id'] and point['split'] == row['split']
        if method.startswith('fdna:'):
            expected = frozen_points[group, method, sid, node]
            for key in ('local_prediction', 'recursive_prediction'):
                close(point[key], expected[key]); prediction_checks += 1
            continue
        def forward(n, value):
            coef = fits[group, method, n]['coefficients']
            return coef[0] if method == 'training_mean' else coef[0]+coef[1]*value
        cache_key = group, method, sid
        if cache_key not in values_cache:
            values = {n: inputs.get(n) for n in roots}
            for n in order:
                if n in parents:
                    value = values[parents[n][0]]
                    values[n] = forward(n, value) if value is not None else None
            values_cache[cache_key] = values
        direct = inputs.get(parents[node][0])
        close(point['local_prediction'], forward(node, direct) if direct is not None else None)
        close(point['recursive_prediction'], values_cache[cache_key][node]); prediction_checks += 2
    buckets = defaultdict(list)
    for p in report['points']:
        buckets[p['fit_group'], p['node'], p['split']].append(p)
    metric_checks = 0
    for metric in report['metrics']:
        own = buckets[metric['fit_group'], metric['node'], metric['split']]
        key = metric['prediction']+'_prediction'
        methods = {p['method'] for p in own}
        common = set.intersection(*[{p['sample_id'] for p in own if p['method'] == m
            and p['target'] is not None and p[key] is not None} for m in methods])
        candidates = [p for p in own if p['method'] == metric['method']]
        valid = [p for p in candidates if p['target'] is not None and p[key] is not None
                 and (metric['evaluation_scope'] != 'common_records' or p['sample_id'] in common)]
        assert metric['evaluated_sample_ids'] == [p['sample_id'] for p in valid]
        assert metric['evaluated'] == metric['count'] == len(valid)
        assert metric['available'] == len(candidates)
        assert metric['omitted'] == len(candidates)-len(valid)
        assert metric['outside'] == sum(not 0 <= p[key] <= 100 for p in valid)
        assert valid, '06正式比较应没有缺失'
        errors = [p[key]-p['target'] for p in valid]; n = len(errors)
        target_mean = math.fsum(p['target'] for p in valid)/n
        sse = math.fsum(e*e for e in errors)
        sst = math.fsum((p['target']-target_mean)**2 for p in valid)
        expected = dict(rmse=math.sqrt(sse/n), mae=math.fsum(abs(e) for e in errors)/n,
            max_absolute_error=max(abs(e) for e in errors), mean_residual=math.fsum(errors)/n,
            r_squared=1-sse/sst if sst else None)
        for field, value in expected.items():
            close(metric[field], value); metric_checks += 1
    comparisons = {}
    for kind in ('local', 'recursive'):
        selected = {(m['fit_group'], m['node'], m['method']): m for m in report['metrics']
                    if m['split'] == 'holdout' and m['prediction'] == kind and m['evaluation_scope'] == 'common_records'}
        counts = defaultdict(int)
        for group, node in {(g, n) for g, n, _ in selected}:
            difference = selected[group, node, 'ols_intercept']['rmse']-selected[group, node, 'fdna:free_delta']['rmse']
            counts['ols_lower' if difference < -1e-8 else 'free_delta_lower' if difference > 1e-8 else 'equal_within_1e-8'] += 1
        comparisons[kind] = dict(counts)
    return dict(status='passed', verified_source_files=len(package), baseline_fits=len(fits),
        coefficient_values_checked=coefficient_checks, prediction_values_checked=prediction_checks,
        metric_values_checked=metric_checks, maximum_absolute_difference=maximum,
        descriptive_holdout_rmse_comparison=comparisons, fdna_refitted=False, se_generated=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='独立标量核验06简单基线结果，不调用拟合器')
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(); result = verify(args.report_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        if args.output.exists():
            raise ValueError('复核记录已存在，拒绝覆盖')
        args.output.write_text(text, encoding='utf-8')
    print(text)
