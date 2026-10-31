"""Local Excel entry using this project's source-data CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from adapters.case_instances import instance_outputs, map_case_instances
from source_data.common import read_json, write_outputs

from .importer import EntryError, convert, read_xlsx
from .schema import TABLES

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'excel_entry/templates'


def run_design(data, filename, *, output_root=None):
    """Full design from explicit Excel inputs; strict canonical RDF export."""
    from design.engine import design
    from design.excel import read_design
    from design.rdf import design_to_rdf
    from design.__main__ import render_summary
    from check_ontology import analyze_graph
    from rdflib import Graph
    document = read_design(data)
    result = design(document)
    out = (output_root or ROOT / 'generated/excel-designs') / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    outputs = {'input.xlsx': data, 'design_input.json': json.dumps(document, ensure_ascii=False, indent=2) + '\n'}
    export = {'status': 'not_ready', 'reason': '设计尚未完整或存在未映射能力', 'validation': None}
    if result['status'] == 'complete' and result['canonical_export_ready']:
        graph = design_to_rdf(document, result, require_canonical=True)
        review = analyze_graph(Graph().parse(ROOT / 'arcane_ontology.ttl', format='turtle') + graph)
        export['validation'] = review.counts()
        if review.counts()['ERROR'] or review.counts()['WARN']:
            export.update(status='validation_failed', reason='生成实例未通过主本体静态检查')
        else:
            outputs['design.ttl'] = graph.serialize(format='turtle')
            export.update(status='exported', reason='规范能力RDF已通过静态检查；未执行OWL推理器')
    response = {'filename': filename, 'output': str(out), 'report': result, 'rdf_export': export,
                'input_file_sha256': hashlib.sha256(data).hexdigest(), 'summary': render_summary(result)}
    outputs.update({'design_report.json': json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                    'design_summary.md': response['summary'],
                    'run_result.json': json.dumps(response, ensure_ascii=False, indent=2, allow_nan=False) + '\n'})
    write_outputs(out, outputs)
    return response


def run_rule_review(data, filename, *, output_root=None):
    from rule_review.core import review
    result = review(data)
    out = (output_root or ROOT / 'generated/rule-reviews') / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    lines = ['# 任务规则验证', '', result['scope'], '']
    for task in result['tasks']:
        lines += [f"## {task['id']}", '', f"状态：{task['status']}",
                  '需求结论：' + ('、'.join(task['conclusions']) or '未命中；不代表不需要'),
                  '缺失条件：' + ('、'.join(task['missing']) or '无缺失事实'),
                  '待确认规则：' + ('、'.join(task['pending_rules']) or '无')]
        for item in task['rules']:
            rule = item['rule']
            lines += [f"- {rule['id']}：{item['status']}；{rule['conclusion']}；{rule['basis']}；{rule['source']} / {rule['location']}"]
            lines += [f"  - {c['condition']['field']}：{c['reason']}" for c in item['checks']]
    validation = result['case_validation']
    lines += ['', '## 对照用例', '']
    if not validation['total']:
        lines.append('未登记对照用例；未验证，不能视为对照通过。')
    else:
        lines += [f"用例总数：{validation['total']}；一致：{validation['matched']}；不一致：{validation['mismatched']}。", '']
        for case in result['cases']:
            outcome = '一致' if case['matches'] else '不一致'
            lines += [f"### {case['id']}（{outcome}）", '', f"任务：{case['task']}；规则：{case['rule']}",
                      f"预期状态：{case['expected']}；实际状态：{case['actual']}",
                      f"预期需求结论：{case['expected_conclusion'] if case['expected_conclusion'] is not None else '无结论'}",
                      f"实际需求结论：{case['actual_conclusion'] if case['actual_conclusion'] is not None else '无结论'}",
                      f"状态核对：{'一致' if case['status_matches'] else '不一致'}；结论核对：{'一致' if case['conclusion_matches'] else '不一致'}", '']
    result.update(filename=filename, output=str(out))
    write_outputs(out, {'input.xlsx': data, 'rule_report.json': json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                        'rule_summary.md': '\n'.join(lines) + '\n'})
    return result


def run_import(data, filename, *, output_root=None, bindings=None, reasoner=False):
    document = convert(read_xlsx(data))
    output_root = output_root or ROOT / 'generated/excel-runs'
    out = output_root / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    source = out / 'input.xlsx'
    source.write_bytes(data)
    converted = out / 'case_records.json'
    converted.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result_dir = out / 'results'
    def run(*args):
        cmd = [sys.executable, '-B', '-m', 'source_data.cli', *map(str, args)]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace',
                           env=__import__('os').environ | {'PYTHONIOENCODING': 'utf-8'}, timeout=120)
        (out / ('ingest.log' if args[0] == 'ingest' else 'query.log')).write_text(p.stdout + '\n' + p.stderr, encoding='utf-8')
        if p.returncode:
            raise EntryError('数据处理程序未通过检查：' + p.stdout[-2500:])
        return json.loads(p.stdout)
    report = run('ingest', converted, '--mapping', ROOT / 'source_mappings/case_fields.json', '--output-dir', result_dir, '--rdf')
    query = run('query', result_dir / 'provenance.ttl')
    instance_graph, instance_report = map_case_instances(read_json(result_dir / 'bundle.json'), bindings=bindings, reasoner=reasoner)
    write_outputs(result_dir, instance_outputs(instance_graph, instance_report))
    labels = {key: label for _, _, fields in TABLES.values() for key, label, _ in fields}
    for record in query['records']:
        record['instances'] = [e for e in instance_report['entities'] if record['id'] in e['source_records']]
        for field in record['fields']:
            field['display_label'] = labels.get(field['field']['key'], field['field']['key'])
            # Source queries show original values in their declared input unit.
            unit = field['field']['unit']['description']
            for default in ('n mile', 'ft', 'kn'):
                if unit and f'（{default}）' in field['display_label']:
                    field['display_label'] = field['display_label'].replace(f'（{default}）', f'（{unit}）')
            if field['field']['key'] == 'attunement_probability_percent' and unit == '1':
                field['display_label'] = '概率（0至1）'
    result = {'report': report, 'records': query['records'], 'output': str(out),
              'instance_report': instance_report,
              'filename': filename, 'input_sha256': hashlib.sha256(data).hexdigest(),
              'backend_module': 'source_data.cli', 'backend_path': str(ROOT / 'source_data'),
              'notes': ['结构与引用检查通过不代表所有业务问题已解决。',
                        '疑点为表格中的人工登记内容；修改数值后请复核疑点说明。']}
    (out / 'run_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


class Handler(BaseHTTPRequestHandler):
    bindings = None
    reasoner = False
    def send(self, status, content, mime):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlsplit(self.path).path
        files = {'/': (ROOT / 'excel_entry/index.html', 'text/html; charset=utf-8'),
                 '/design': (ROOT / 'design/excel.html', 'text/html; charset=utf-8'),
                 '/design-example.xlsx': (TEMPLATES / '链网设计示例.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                 '/design-blank.xlsx': (TEMPLATES / '链网设计空白.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                 '/rules': (ROOT / 'rule_review/index.html', 'text/html; charset=utf-8'),
                 '/rules-example.xlsx': (ROOT / 'rule_review/templates/规则验证示例.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                 '/rules-blank.xlsx': (ROOT / 'rule_review/templates/规则验证空白.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                 '/example.xlsx': (TEMPLATES / '案例示例.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                 '/blank.xlsx': (TEMPLATES / '空白模板.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
        if path not in files:
            return self.send(404, b'Not found', 'text/plain')
        file, mime = files[path]
        self.send(200, file.read_bytes(), mime)

    def do_POST(self):
        # Only same-origin local browser requests; reject cross-origin uploads.
        origin = self.headers.get('Origin')
        expected_origin = f'http://127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != expected_origin.removeprefix('http://') or (origin and origin != expected_origin):
            return self.send(403, b'Forbidden', 'text/plain')
        path = urlsplit(self.path).path
        if path not in ('/import', '/review-rules', '/design-run'):
            return self.send(404, b'Not found', 'text/plain')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10_000_000:
                raise EntryError('请选择10MB以内的.xlsx文件。')
            payload = self.rfile.read(size)
            filename = unquote(urlsplit(self.path).query or 'input.xlsx')
            result = (run_design(payload, filename) if path == '/design-run'
                      else run_rule_review(payload, filename) if path == '/review-rules'
                      else run_import(payload, filename, bindings=self.bindings, reasoner=self.reasoner))
            self.send(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        except (EntryError, ValueError) as exc:
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        except Exception as exc:
            self.log_error('%s', exc)
            self.send(500, json.dumps({'error': '运行失败，具体原因见终端。'}, ensure_ascii=False).encode(), 'application/json; charset=utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Excel案例录入与查询展示')
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('--file', type=Path, help='直接导入案例，无需打开网页')
    inputs.add_argument('--design-file', type=Path, help='从链网专用Excel生成完整设计，无需打开网页')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--bindings', type=Path, help='已核实且附来源的实例关系JSON配置')
    parser.add_argument('--reasoner', action='store_true', help='导入时执行HermiT检查')
    args = parser.parse_args(argv)
    if args.design_file:
        try:
            result = run_design(args.design_file.read_bytes(), args.design_file.name)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            if result['rdf_export']['status'] == 'validation_failed':
                return 2
            return 0 if (result['report']['status'] == 'complete'
                         and result['rdf_export']['status'] == 'exported') else 1
        except (ValueError, OSError) as exc:
            from design.engine import SearchLimitError
            print(json.dumps({'status': 'search_limit' if isinstance(exc, SearchLimitError) else 'invalid',
                              'error': str(exc), 'network': None}, ensure_ascii=False))
            print(str(exc), file=sys.stderr)
            return 2
    bindings = read_json(args.bindings) if args.bindings else None
    if args.file:
        result = run_import(args.file.read_bytes(), args.file.name, bindings=bindings, reasoner=args.reasoner)
        print(json.dumps({k: v for k, v in result.items() if k != 'records'}, ensure_ascii=False, indent=2))
        return
    Handler.bindings = bindings
    Handler.reasoner = args.reasoner
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    print(f'请在浏览器打开 http://127.0.0.1:{args.port} ，按 Ctrl+C 停止。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
