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
        if urlsplit(self.path).path != '/import':
            return self.send(404, b'Not found', 'text/plain')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10_000_000:
                raise EntryError('请选择10MB以内的.xlsx文件。')
            result = run_import(self.rfile.read(size), unquote(urlsplit(self.path).query or 'input.xlsx'), bindings=self.bindings, reasoner=self.reasoner)
            self.send(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        except (EntryError, ValueError) as exc:
            self.send(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        except Exception as exc:
            self.log_error('%s', exc)
            self.send(500, json.dumps({'error': '运行失败，具体原因见终端。'}, ensure_ascii=False).encode(), 'application/json; charset=utf-8')


def main():
    parser = argparse.ArgumentParser(description='Excel案例录入与查询展示')
    parser.add_argument('--file', type=Path, help='直接导入，无需打开网页')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--bindings', type=Path, help='已核实且附来源的实例关系JSON配置')
    parser.add_argument('--reasoner', action='store_true', help='导入时执行HermiT检查')
    args = parser.parse_args()
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
    main()
