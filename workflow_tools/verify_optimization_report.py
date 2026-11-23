"""Check report CSV values and input fingerprints against frozen source artifacts."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(project_root, report_dir):
    root = Path(project_root); out = Path(report_dir)
    for name, expected in read(out/'manifest.json').items():
        if sha(out/name) != expected: raise ValueError('报告产物校验失败：'+name)
    metadata = read(out/'图表来源与核验.json')
    for name, expected in metadata['source_files'].items():
        if sha(root/name) != expected: raise ValueError('源文件指纹变化：'+name)
    base = root/'generated/关于优化'
    raw = root/'generated/关于两篇连接/06_完整场景全流程运行'
    original = read(raw/'完整包校验.json')
    for name, expected in original.items():
        if sha(raw/name) != expected: raise ValueError('06源包变化：'+name)

    def csv_rows(name):
        with (out/'图表数据'/(name+'.csv')).open(encoding='utf-8-sig', newline='') as stream:
            return list(csv.DictReader(stream))

    checks = 0

    def match(row, expected):
        nonlocal checks
        for key, value in expected.items():
            if key not in row: continue
            if isinstance(value, bool) and row[key] not in ('True','False'):
                raise ValueError('图表布尔值无效：'+key)
            parsed = (json.loads(row[key]) if isinstance(value, (dict,list)) else
                      row[key] == 'True' if isinstance(value, bool) else
                      float(row[key]) if isinstance(value, (int,float)) else
                      None if value is None and row[key] == '' else row[key])
            if parsed != value: raise ValueError(f'图表数值不一致：{key}: {parsed!r} != {value!r}')
            checks += 1

    baseline = read(base/'03_同条件简单预测基线_核验版/baseline_report.json')
    expected = {(m['fit_group'],m['node'],m['prediction'],m['method']):m for m in baseline['metrics']
                if m['split']=='holdout' and m['evaluation_scope']=='common_records'}
    rows = csv_rows('四方法共同留出误差')
    if len(rows) != 144 or len(expected) != 144: raise ValueError('预测对照行数不符')
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in ('fit_group','node','prediction','method'))
        if key in seen or key not in expected: raise ValueError('图表预测行重复或未知')
        seen.add(key); match(row, expected[key])
    if seen != set(expected): raise ValueError('预测图表缺行')
    delta = read(base/'02_P1诊断集中总览/δ对照.json')
    rows = csv_rows('自由δ配对改善')
    if len(rows) != len(delta): raise ValueError('δ对照行数不符')
    for row, source in zip(rows, delta): match(row, source)
    stability = read(base/'04_冻结预测成组稳定性/stability_report.json')
    expected = {(r['fit_group'],r['node'],r['prediction']):r for r in stability['contrasts']
                if r['metric']=='rmse' and r['left']=='ols_intercept' and r['right']=='fdna:free_delta'}
    rows = csv_rows('OLS自由δ稳定性')
    if len(rows) != 36 or len(expected) != 36: raise ValueError('稳定性对照行数不符')
    seen = set()
    for row in rows:
        key=tuple(row[k] for k in ('fit_group','node','prediction'))
        if key in seen or key not in expected: raise ValueError('稳定性图表行重复或未知')
        seen.add(key); match(row, expected[key])
    counts = read(base/'02_P1诊断集中总览/整理计数.json')
    for row in csv_rows('诊断标记'):
        match(row, dict(count=counts['flag_counts'][row['flag']], denominator=18 if row['flag']=='DELTA_AT_BOUNDARY' else 36))
    card = read(base/'02_P1诊断集中总览/诊断卡片/env_0__base__B1.json')
    minimum = min(a['rmse'] for a in card['attempts'])
    expected = [a for a in card['attempts'] if a['success'] and abs(a['rmse']-minimum)<=1e-6]
    rows = csv_rows('B1近等参数')
    if len(rows) != len(expected): raise ValueError('近等参数记录数不符')
    for row, source in zip(rows, expected):
        match(row, dict(initial_source=source['initial_source'],rmse=source['rmse'],alpha=source['parameters'][0],beta=source['parameters'][1]))
    figure_names = [s['stem'] for s in metadata['figures']]
    if len(set(figure_names)) != 6 or len(figure_names) != 6: raise ValueError('汇报图应有6张且不重复')
    for name in figure_names:
        if not (out/'图表'/(name+'.png')).is_file() or not (out/'图表'/(name+'.svg')).is_file():
            raise ValueError('缺少图表：'+name)
    return dict(status='passed', figures=len(figure_names), source_fingerprints_verified=len(metadata['source_files']),
        source_package_files_verified=len(original), csv_fields_compared=checks,
        common_holdout_rows=144, delta_pairs=len(delta), stability_rows=36,
        pdf_sha256=sha(out/'优化成果汇报图文.pdf'), fdna_refitted=False, se_generated=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='核对汇报数据、图表CSV和源包指纹')
    parser.add_argument('--project-root',type=Path,required=True);parser.add_argument('--report-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(); result=verify(args.project_root,args.report_dir)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists(): raise ValueError('复核记录已存在')
        args.output.write_text(text,encoding='utf-8')
    print(text)
