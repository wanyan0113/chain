"""Build and verify paired article cases without changing the design engine."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'data/article_source/case_records.json'
AP = 'urn:arcane:article-pair:'
KW = 'http://example.org/arcaneweb#'
TASK = 'eliminate-B-Eidolon-1'
COORD = 'R-Command'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def q(value, unit):
    return {'value': value, 'unit': unit}


def build_cases():
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    target = source['table11']
    platforms = {row['platform']: row for row in source['table8']}
    doc = {
        'format_version': '1.0',
        'scope': '原文保留版：仅B-Eidolon#1单任务；保留原始参数，未知项不补成事实。接口必填占位值受阻塞标记保护；能力层为文章三角色结构的显式形式化。',
        'knowledge': {'ontology_files': ['article-pair.ttl'], 'rules_file': 'article-pair.rules.json',
                      'source_refs': ['MODEL-01：第1部分分类与第2部分闭合链结构的工程形式化，不是原文完整能力规则库']},
        'weights': {'time': .3, 'accuracy': .3, 'cost': .4},
        'policies': {'article_table10': True, 'equal_metric_score': .5, 'cost_unit': 'experiment-credit/use',
                     'max_chain_combinations': 10000, 'max_network_combinations': 10000},
        'tasks': [{'id': TASK, 'target': 'B-Eidolon#1', 'start_s': 0,
                   'target_ontology_types': [AP + 'FlyingTarget'],
                   'attributes': {'target_type': target['target_type'],
                                  'target_altitude': q(target['target_altitude_ft'], 'ft'),
                                  'target_speed': q(target['target_speed_kn'], 'kn'),
                                  'impedance': q(target['target_impedance_threshold'], 'article-resonance'),
                                  'longitude_raw': target['longitude_column_raw'],
                                  'latitude_raw': target['latitude_column_raw']},
                   'source_refs': ['表11；151006、151015；MODEL-01'],
                   'blocking_issue_ids': ['GAP-START', 'GAP-UNITS']}],
        'artifacts': [], 'contexts': [], 'links': [], 'ontology_relations': [],
        'task_rules': [], 'artifact_rules': [], 'dependencies': [], 'resource_groups': []}

    def artifact(identifier, role, attrs, source_ref, issues=()):
        return {'id': identifier, 'ontology_types': [AP + role + 'Unit'], 'attributes': attrs,
                'available': True, 'metrics': None, 'capacity': 1, 'consumable': False,
                'inventory': 0, 'cooldown_s': 0, 'source_refs': [source_ref, 'MODEL-01'],
                'blocking_issue_ids': ['GAP-RESOURCE-CONTRACT', *issues]}

    for row in source['table8']:
        attrs = {'source_target_types': row['target_types'], 'detection_range': q(row['detection_range_nm'], 'nmi'),
                 'source_precision': q(row['precision_raw'], 'source-unspecified'),
                 'platform': row['platform'], 'longitude_raw': row['longitude_column_raw'],
                 'latitude_raw': row['latitude_column_raw']}
        # The source says 空中; its relation to 飞行灵体 is explicitly unresolved here.
        doc['artifacts'].append(artifact(row['insight_artifact'], 'Insight', attrs, '表8；150929', ['GAP-TYPE-MAPPING']))
    doc['artifacts'].append(artifact(COORD, 'Coordination', {'platform': 'R-Golem'}, '3.2正文：R-Golem上唯一联合统筹中枢；150929；R-Command为本地编号'))
    for row in source['table9']:
        attrs = {'platform': row['platform'], 'target_types': row['target_types'],
                 'min_range': q(row['min_range_nm'], 'nmi'), 'max_range': q(row['max_range_nm'], 'nmi'),
                 'resonance': q(row['resonance_value'], 'article-resonance'),
                 'max_target_speed': q(row['max_target_speed_kn'], 'kn'),
                 'min_target_altitude': q(row['min_target_altitude_ft'], 'ft'),
                 'max_target_altitude': q(row['max_target_altitude_ft'], 'ft'),
                 'min_activation_altitude': q(row['min_activation_altitude_ft'], 'ft'),
                 'max_activation_altitude': q(row['max_activation_altitude_ft'], 'ft'),
                 'source_lock_percent': q(row['attunement_probability_percent'], '%')}
        doc['artifacts'].append(artifact(row['manifestation_artifact'], 'Manifestation', attrs, '表9；150942、150954'))
        doc['contexts'].append({'task': TASK, 'artifact': row['manifestation_artifact'],
                                'attributes': {'platform_altitude': q(platforms[row['platform']]['altitude_ft'], 'ft')},
                                'source_refs': ['表8平台高度映射到表9承载平台'], 'blocking_issue_ids': []})

    supplemented = deepcopy(doc)
    supplemented['scope'] = '补充假设版：沿用原文实例和参数，补充距离/语义映射/资源/协同/评价数据验证架构；不是原文表12、13或六任务网络复现。'
    task = supplemented['tasks'][0]
    task['blocking_issue_ids'] = []
    del task['target_ontology_types']  # infer the target class from the scene literal
    task['attributes']['target_count'] = q(1, '1')
    task['source_refs'].append('ASSUME-01、02：相对时刻0及单位约定；ASSUME-03：单目标')
    for a in supplemented['artifacts']:
        a['blocking_issue_ids'] = []
        a['source_refs'].append('ASSUME-04：可用/容量1/非消耗/冷却0；ASSUME-05：三阶段实验指标')
        identifier = a['id']
        if identifier.startswith('R-Eye'):
            row = next(r for r in source['table8'] if r['insight_artifact'] == identifier)
            a['attributes']['target_types'] = [*row['target_types'], '飞行灵体']
            a['attributes']['max_tracks'] = q(2, '1')
            a['source_refs'].append('ASSUME-06：飞行灵体属于探知表空中范围；ASSUME-07：标记数2；精度原值按百分比解释')
            duration, accuracy, cost = 2, row['precision_raw'] / 100, 1
        elif identifier == COORD:
            a['attributes']['max_processing_targets'] = q(2, '1')
            duration, accuracy, cost = 1, .99, 1
        else:
            row = next(r for r in source['table9'] if r['manifestation_artifact'] == identifier)
            # Deliberately no repair of inverted ranges, activation limits or costs.
            duration, accuracy, cost = 10, row['attunement_probability_percent'] / 100, 10
        a['metrics'] = {'duration_s': duration, 'accuracy': accuracy, 'cost': q(cost, 'experiment-credit/use')}
    contexts = {c['artifact']: c for c in supplemented['contexts']}
    for a in supplemented['artifacts']:
        if a['id'] == COORD:
            continue
        context = contexts.get(a['id'])
        if context is None:
            context = {'task': TASK, 'artifact': a['id'], 'attributes': {}, 'source_refs': [], 'blocking_issue_ids': []}
            supplemented['contexts'].append(context)
        context['attributes']['target_distance'] = q(50, 'nmi')
        context['source_refs'].append('ASSUME-08：法器到目标距离50nmi，工程测试值；不由疑点坐标计算')
        left, right = (a['id'], COORD) if a['id'].startswith('R-Eye') else (COORD, a['id'])
        supplemented['links'].append({'from': left, 'to': right, 'communicates': None, 'controls': None,
                                      'distance': q(0 if a['attributes']['platform'] == 'R-Golem' else 10, 'nmi'),
                                      'max_distance': q(100, 'nmi'), 'source_refs': ['ASSUME-09：测试通信距离及阈值'],
                                      'blocking_issue_ids': []})
        for predicate in (['communicatesWith'] if a['id'].startswith('R-Eye') else ['communicatesWith', 'orchestrates']):
            supplemented['ontology_relations'].append({
                'subject': {'kind': 'artifact', 'id': left}, 'predicate': KW + predicate,
                'object': {'kind': 'artifact', 'id': right},
                'source_refs': ['ASSUME-10：测试传讯/统筹事实，不是文章逐边事实'], 'blocking_issue_ids': []})
    return source, doc, supplemented


def differences(before, after, path=''):
    """Record every changed value, including removed blockers and source labels."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [d for key in sorted(set(before) | set(after))
                for d in differences(before.get(key), after.get(key), path + '/' + key)]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [d for i, (a, b) in enumerate(zip(before, after)) for d in differences(a, b, path + '/' + str(i))]
    return [] if before == after else [{'path': path, 'source_only': before, 'supplemented': after}]


def summarize(report):
    selected = [c for t in report['tasks'] for c in t['chains']
                if report['network'] and c['id'] in report['network']['chains']]
    best_score = selected[0]['score'] if selected else None
    return {'status': report['status'], 'owl': report['owl_reasoning']['status'],
            'required_capabilities': report['tasks'][0]['required_capabilities'],
            'accepted': [s['artifact'] for s in report['tasks'][0]['selection'] if s['accepted']],
            'chains': len(report['tasks'][0]['chains']), 'blockers': report['tasks'][0]['blockers'],
            'selected': [c['closed_path'] for c in selected],
            'selected_metrics': [c['metrics'] for c in selected], 'selected_score': best_score,
            'best_score_ties': [c['closed_path'] for t in report['tasks'] for c in t['chains']
                                if best_score is not None and abs(c['score'] - best_score) < 1e-12],
            'rdf_validation': report.get('rdf_validation'), 'execution_sha256': report['execution_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--skip-variations', action='store_true')
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if out.exists():
        parser.error('输出目录已存在，请使用新目录，保留每次验证快照')
    source, original, supplemented = build_cases()
    out.mkdir(parents=True)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    (out / 'source_snapshot.json').write_bytes(SOURCE.read_bytes())
    (out / 'assumptions.json').write_bytes(Path(__file__).with_name('assumptions.json').read_bytes())
    save(out / 'input_differences.json', differences(original, supplemented))
    cases = {'source_only': (original, 'blocked'), 'supplemented': (supplemented, 'complete')}
    if not args.skip_variations:
        far = deepcopy(supplemented)
        for context in far['contexts']:
            context['attributes']['target_distance'] = q(500, 'nmi')
        cases['distance_500'] = (far, 'no_feasible_design')
        no_control = deepcopy(supplemented)
        no_control['ontology_relations'] = [r for r in no_control['ontology_relations'] if r['predicate'] != KW + 'orchestrates']
        cases['missing_control'] = (no_control, 'blocked')
        unseen = deepcopy(supplemented)
        unseen['tasks'][0]['attributes']['target_type'] = '试验飞行灵体'
        for a in unseen['artifacts']:
            if 'target_types' in a['attributes']:
                a['attributes']['target_types'].append('试验飞行灵体')
        cases['new_target_before_knowledge'] = (unseen, 'blocked')
        extended = deepcopy(unseen)
        extended['knowledge']['ontology_files'].append('article-pair-extension.ttl')
        cases['new_target_after_knowledge'] = (extended, 'complete')
        strict = deepcopy(supplemented)
        strict['knowledge']['rules_file'] = 'article-pair-strict.rules.json'
        cases['external_rule_stricter'] = (strict, 'no_feasible_design')
    results = {}
    for name, (document, expected) in cases.items():
        input_path = out / 'inputs' / (name + '.json')
        save(input_path, document)
        with (out / (name + '.stdout.json')).open('w', encoding='utf-8') as log:
            run = subprocess.run([sys.executable, '-B', '-m', 'design', 'run', str(input_path),
                                  '--output-dir', str(out / name), '--require-canonical'],
                                 cwd=ROOT, stdout=log, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        report_path = out / name / 'design_report.json'
        if not report_path.exists():
            raise RuntimeError(f'{name}: 执行失败 {run.returncode}: {run.stderr}')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        results[name] = {**summarize(report), 'expected': expected, 'exit_code': run.returncode}
        expected_exit = 0 if expected == 'complete' else 1
        results[name]['verified'] = report['status'] == expected and run.returncode == expected_exit
        if expected == 'complete':
            results[name]['verified'] &= (report.get('rdf_validation', {}).get('ERROR') == 0
                                          and report.get('rdf_validation', {}).get('WARN') == 0
                                          and (out / name / 'design.ttl').exists())
        else:
            results[name]['verified'] &= not (out / name / 'design.ttl').exists()
        print(f"{name}: {report['status']} (expected {expected})", flush=True)
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError('原始来源文件发生变化')
    save(out / 'experiment_results.json', {'source_sha256': source_hash, 'cases': results,
                                          'all_verified': all(r['verified'] for r in results.values())})
    lines = ['# 原文与补充假设对照实验结果', '',
             '仅验证B-Eidolon#1单任务。补充版不是表12/13及六任务网络的独立复现。', '',
             '| 案例 | 预期 | 实际 | 候选链 | 验证 |', '| --- | --- | --- | --- | --- |']
    for name, r in results.items():
        lines.append(f"| {name} | {r['expected']} | {r['status']} | {r['chains']} | {'通过' if r['verified'] else '失败'} |")
    lines.extend(['', '补充版接受的法器：' + '、'.join(results['supplemented']['accepted']), '',
                  '补充版选中链：' + json.dumps(results['supplemented']['selected'], ensure_ascii=False), '',
                  '同分最优链：' + json.dumps(results['supplemented']['best_score_ties'], ensure_ascii=False), '',
                  '同分不表示唯一最优；程序按固定顺序选择其中一条。实验耗时/成本是明确补充的工程值。', '',
                  '各目录保存design_report.json、design_summary.md、知识快照及推理输入/结果；完整运行另保存design.ttl。', '',
                  '原始来源逐字节副本：source_snapshot.json；两套输入逐字段差异：input_differences.json。',
                  '假设台账见assumptions.json；运行入口见项目README.md。', ''])
    (out / '实验结果.md').write_text('\n'.join(lines), encoding='utf-8')
    return 0 if all(r['verified'] for r in results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
