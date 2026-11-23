"""Independent scalar audit of saved grouped draws, losses, contrasts and quantiles."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(directory):
    out = Path(directory); report = read(out/'stability_report.json')
    for name, expected in read(out/'manifest.json').items():
        assert sha(out/name) == expected, name
    source = Path(report['source']['directory'])
    assert sha(source/'baseline_report.json') == report['source']['baseline_report_sha256']
    for name, expected in read(source/'manifest.json').items():
        assert sha(source/name) == expected, name
    baseline = read(source/'baseline_report.json')
    raw_source = Path(baseline['source']['directory'])
    for name, expected in read(raw_source/'完整包校验.json').items():
        assert sha(raw_source/name) == expected, name
    panel = report['panel']; protocol = report['protocol']
    draws = read(out/'成组抽样索引.json')
    assert draws['base_cases'] == panel['base_cases']
    indices = np.array(draws['indices'])
    assert indices.shape == (protocol['resamples'], len(panel['base_cases']))
    assert np.issubdtype(indices.dtype, np.integer) and indices.min() >= 0 and indices.max() < len(panel['base_cases'])
    generator = np.random.Generator(np.random.PCG64(protocol['seed']))
    np.testing.assert_array_equal(indices, generator.integers(0, len(panel['base_cases']), size=indices.shape))
    errors = {}
    for p in baseline['points']:
        if p['split'] == 'holdout':
            for kind in panel['prediction_levels']:
                key = p['fit_group'], p['node'], kind, p['method'], p['base_case_id']
                assert key not in errors
                errors[key] = p[kind+'_prediction']-p['target']
    assert len(errors) == len(panel['groups'])*len(panel['nodes'])*2*4*len(panel['base_cases'])
    with np.load(out/'逐次指标与差值.npz', allow_pickle=False) as arrays:
        losses = arrays['losses']; differences = arrays['differences']
    assert losses.shape == (len(indices), len(panel['groups']), len(panel['nodes']), 2, 4, 2)
    assert differences.shape == (len(indices), len(panel['groups']), len(panel['nodes']), 2, 2, 2)
    assert np.all(np.isfinite(losses)) and np.all(np.isfinite(differences))
    maximum = 0.; loss_checks = 0; difference_checks = 0

    def close(a, b):
        nonlocal maximum
        difference = abs(float(a)-float(b)); maximum = max(maximum, difference)
        assert difference <= 1e-9, (a, b, difference)

    originals = {}
    # The oracle uses scalar fsum and saved base-case IDs, not the production array reducer.
    for gi, group in enumerate(panel['groups']):
        for ni, node in enumerate(panel['nodes']):
            for ki, kind in enumerate(panel['prediction_levels']):
                for mi, method in enumerate(panel['methods']):
                    source_errors = [errors[group, node, kind, method, b] for b in panel['base_cases']]
                    n = len(source_errors)
                    originals[group, node, kind, method] = (
                        math.sqrt(math.fsum(e*e for e in source_errors)/n), math.fsum(abs(e) for e in source_errors)/n)
                    for di, draw in enumerate(indices):
                        values = [source_errors[int(i)] for i in draw]
                        expected = math.sqrt(math.fsum(e*e for e in values)/n), math.fsum(abs(e) for e in values)/n
                        for si, value in enumerate(expected):
                            close(losses[di, gi, ni, ki, mi, si], value); loss_checks += 1
                for ci, (left, right) in enumerate(protocol['comparisons']):
                    li = panel['methods'].index(left); ri = panel['methods'].index(right)
                    for di in range(len(indices)):
                        for si in range(2):
                            close(differences[di, gi, ni, ki, ci, si],
                                  losses[di, gi, ni, ki, li, si]-losses[di, gi, ni, ki, ri, si])
                            difference_checks += 1
    summary_checks = 0

    def summary(values, row):
        nonlocal summary_checks
        ordered = sorted(float(v) for v in values)
        for q, field in zip(protocol['quantiles'], ('q025', 'median', 'q975')):
            position = (len(ordered)-1)*q; low = math.floor(position); high = math.ceil(position)
            value = ordered[low]+(position-low)*(ordered[high]-ordered[low])
            close(row[field], value); summary_checks += 1
        mean = math.fsum(ordered)/len(ordered)
        close(row['resample_sd'], math.sqrt(math.fsum((v-mean)**2 for v in ordered)/(len(ordered)-1)))
        summary_checks += 1

    assert len(report['metrics']) == 288 and len(report['contrasts']) == 144
    for row in report['metrics']:
        gi = panel['groups'].index(row['fit_group']); ni = panel['nodes'].index(row['node'])
        ki = panel['prediction_levels'].index(row['prediction']); mi = panel['methods'].index(row['method'])
        si = ['rmse', 'mae'].index(row['metric'])
        summary(losses[:, gi, ni, ki, mi, si], row)
        close(row['original'], originals[row['fit_group'], row['node'], row['prediction'], row['method']][si])
    for row in report['contrasts']:
        gi = panel['groups'].index(row['fit_group']); ni = panel['nodes'].index(row['node'])
        ki = panel['prediction_levels'].index(row['prediction']); si = ['rmse', 'mae'].index(row['metric'])
        ci = protocol['comparisons'].index([row['left'], row['right']]); values = differences[:, gi, ni, ki, ci, si]
        summary(values, row)
        key = row['fit_group'], row['node'], row['prediction']
        close(row['original'], originals[*key, row['left']][si]-originals[*key, row['right']][si])
        tolerance = protocol['numerical_tie_tolerance']
        assert row['negative_count'] == sum(v < -tolerance for v in values)
        assert row['numerical_tie_count'] == sum(abs(v) <= tolerance for v in values)
        assert row['positive_count'] == sum(v > tolerance for v in values)
        assert row['central_range_contains_zero'] == (row['q025'] <= 0 <= row['q975'])
    return dict(status='passed', base_cases=len(panel['base_cases']), resamples=len(indices),
        loss_values_checked=loss_checks, paired_difference_values_checked=difference_checks,
        quantile_and_sd_values_checked=summary_checks, maximum_absolute_difference=maximum,
        source_baseline_sha256=sha(source/'baseline_report.json'), model_refitted=False, parameter_confidence_interval=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='独立标量核验冻结预测成组成对重采样')
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(); result = verify(args.report_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        if args.output.exists():
            raise ValueError('复核记录已存在，拒绝覆盖')
        args.output.write_text(text, encoding='utf-8')
    print(text)
