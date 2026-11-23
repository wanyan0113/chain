"""Isolated inputs and real worker progress for the local presentation."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

PACKAGE = Path(__file__).resolve().parent
CASES = {'sequence', 'propagation', 'extraction', 'network'}
JOBS = {}
LOCK = threading.RLock()

def execute(job_id, case, options):
    folder = PACKAGE / 'runs' / job_id
    folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps(options, ensure_ascii=False, indent=2), encoding='utf-8')
    started = time.monotonic()
    with LOCK:
        JOBS[job_id].update(state='running', folder=folder)
    try:
        with (folder/'process.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, '-u', '-B', str(PACKAGE/'interactive_cases.py'), case,
                '--request', str(folder/'request.json'), '--run-dir', str(folder)], cwd=PACKAGE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            timer = threading.Timer(120, process.kill)
            timer.start()
            try:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    if line.startswith('@event '):
                        event = json.loads(line[7:])
                        with LOCK:
                            JOBS[job_id]['events'].append(event['message'])
                code = process.wait()
            finally:
                timer.cancel()
            if code:
                raise RuntimeError((folder/'process.log').read_text(encoding='utf-8')[-2400:])
        result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
        with LOCK:
            JOBS[job_id].update(state='succeeded', result=result)
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(state='failed', error=str(exc))
    finally:
        with LOCK:
            JOBS[job_id]['elapsed'] = round(time.monotonic()-started, 3)

class Handler(BaseHTTPRequestHandler):
    def reply(self, code, value, content_type='application/json; charset=utf-8'):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/':
            self.reply(200, (PACKAGE/'demo.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/network':
            self.reply(200, (PACKAGE/'network.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/network-input':
            self.reply(200, json.loads((PACKAGE/'case_inputs/network_scene.json').read_text(encoding='utf-8')))
        elif path in {'/assets/figure6.png', '/assets/table3.png', '/assets/table7.png'}:
            self.reply(200, (PACKAGE/path.lstrip('/')).read_bytes(), 'image/png')
        elif path == '/health':
            self.reply(200, {'interactive': True})
        elif path.startswith('/jobs/'):
            with LOCK:
                job = JOBS.get(path[6:])
                public = {k:v for k,v in job.items() if k != 'folder'} if job else None
            self.reply(200 if public else 404, public or {})
        elif path.startswith('/runs/'):
            parts = path.split('/')
            with LOCK:
                job = JOBS.get(parts[2]) if len(parts) == 4 else None
                allowed = job and job.get('result', {}).get('files', [])
            if not allowed or parts[3] not in allowed:
                self.reply(404, {})
                return
            self.reply(200, (job['folder']/parts[3]).read_bytes(), 'text/plain; charset=utf-8')
        else:
            self.reply(404, {})

    def do_POST(self):
        case = self.path.removeprefix('/jobs/')
        if not self.path.startswith('/jobs/') or case not in CASES:
            self.reply(404, {})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('输入内容过长或为空')
            options = json.loads(self.rfile.read(length))
            if not isinstance(options, dict):
                raise ValueError('输入须为字段对象')
            job_id = uuid.uuid4().hex
            with LOCK:
                JOBS[job_id] = {'id': job_id, 'case': case, 'state': 'queued', 'events': []}
            threading.Thread(target=execute, args=(job_id, case, options), daemon=True).start()
            self.reply(202, {'id': job_id})
        except (ValueError, json.JSONDecodeError) as exc:
            self.reply(400, {'error': str(exc)})

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    server = None
    for port in ([args.port] if args.port else range(8876, 8886)):
        try:
            server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit('演示端口已被占用，请使用 --port 指定其他端口。')
    url = f'http://127.0.0.1:{server.server_port}/'
    print('演示地址 ' + url, flush=True)
    print('按 Ctrl+C 结束演示。', flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
