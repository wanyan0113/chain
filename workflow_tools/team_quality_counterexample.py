"""Deterministic synthetic repair mechanism versus a product quality index.

The declared scores are general scores, not fitted probabilities. Endpoint
observations are generated independently of those scores. This is a policy
limitation example, not empirical model calibration or real-device evidence.
"""
import argparse
import csv
import json
import math
from pathlib import Path


def experiment():
    records = []
    for i in range(1000):
        reference = i % 2
        initial_error = i >= 900
        repair_event = 900 <= i < 980
        before = 1-reference if initial_error else reference
        after = reference if repair_event else before
        records.append(dict(sample_id=f'synthetic-{i:04d}', reference_label=reference,
                            before_output=before, after_output=after, synthetic_repair_event=repair_event))
    correct_before = sum(r['before_output'] == r['reference_label'] for r in records)
    correct_after = sum(r['after_output'] == r['reference_label'] for r in records)
    short = [.9, .9, .9]
    long = short + [.95]
    summary = dict(version='team-quality-length-counterexample-1.0', data_origin='synthetic_simulation',
        metric_label='串行乘积质量指标', stage_semantics='quality_score',
        declared_short_quality=short, declared_long_quality=long,
        short_product=math.prod(short), long_product=math.prod(long),
        sample_count=len(records), before_correct=correct_before, after_correct=correct_after,
        before_endpoint_rate=correct_before/len(records), after_endpoint_rate=correct_after/len(records),
        generated_mechanism='Initial last 100 samples fail; a declared diagnostic oracle repairs 80 of them and never damages correct outputs.',
        scores_used_to_generate_observations=False, probability_calibration_attempted=False,
        conclusion='The extra correction stage lowers the product index while improving the synthetic endpoint. General quality scores are not endpoint success probabilities.',
        domain_validity='pending', formula_changed=False)
    return summary, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        raise ValueError('output directory already exists')
    summary, rows = experiment()
    args.output_dir.mkdir(parents=True)
    with (args.output_dir/'synthetic-endpoints.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    (args.output_dir/'counterexample.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
