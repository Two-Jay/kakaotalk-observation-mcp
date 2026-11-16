"""Loopback-only monitoring console and single persistent collection worker."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from monitor_service import MonitorService

WEB = Path(__file__).with_name('web')


def handler_for(service, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, content_type='application/json; charset=utf-8', extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def guard(self, mutation=False):
            # Cloudflare must only target the isolated read-only listener, never admin.
            if any(key.lower().startswith('cf-') for key in self.headers.keys()):
                self.reply(403, {'error': '터널 요청은 원격 조회 포트로 연결하세요.'})
                return False
            port = self.server.server_address[1]
            hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if host not in hosts or (origin is not None and origin != 'http://' + host):
                self.reply(403, {'error': '허용되지 않은 Host 또는 Origin입니다.'})
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.reply(403, {'error': '외부 사이트 요청은 허용하지 않습니다.'})
                return False
            if mutation and not secrets.compare_digest(self.headers.get('X-Monitor-Token', ''), token):
                self.reply(403, {'error': '페이지를 새로고침하고 다시 시도하세요.'})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                self.reply(200, {**service.snapshot(), 'token': token})
            elif path in ('/', '/app.js', '/style.css'):
                name, kind = {'/': ('index.html', 'text/html; charset=utf-8'),
                              '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                              '/style.css': ('style.css', 'text/css; charset=utf-8')}[path]
                self.reply(200, (WEB / name).read_bytes(), kind)
            elif path.startswith('/api/transcript/'):
                policy_id = path.removeprefix('/api/transcript/')
                policy = next((p for p in service.list_policies() if p['id'] == policy_id), None)
                if not policy:
                    self.reply(404, {'error': '정책을 찾을 수 없습니다.'})
                else:
                    # Export takes no desktop actions. Only configured rooms are accessible.
                    data = service.store.export(policy['room']).read_bytes()
                    self.reply(200, data, 'text/plain; charset=utf-8', {'Content-Disposition': 'attachment; filename="transcript.txt"'})
            else:
                self.reply(404, {'error': '페이지를 찾을 수 없습니다.'})

        def do_POST(self):
            if not self.guard(mutation=True):
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536 or self.headers.get_content_type() != 'application/json':
                    raise ValueError('올바른 JSON 요청이 필요합니다. 최대 64KB입니다.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('JSON 객체가 필요합니다.')
                path = urlsplit(self.path).path
                if path == '/api/policies':
                    result = service.save_policy(body.get('policy'), body.get('id'))
                elif path == '/api/policies/delete':
                    result = service.delete_policy(body['id'])
                elif path == '/api/check':
                    if body.get('allow_foreground') is not True:
                        raise ValueError('카카오톡 화면 조작 안내를 확인해 주세요.')
                    result = service.enqueue(body['id'])
                elif path == '/api/scheduler':
                    if body.get('enabled') is True and body.get('allow_foreground') is not True:
                        raise ValueError('자동 점검의 화면 조작 안내를 확인해 주세요.')
                    result = {'scheduler_enabled': service.scheduler(body['enabled'])}
                else:
                    self.reply(404, {'error': '알 수 없는 API입니다.'})
                    return
                self.reply(200, result)
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                self.reply(500, {'error': '저장 처리에 실패했습니다. 서버 로그와 데이터 폴더를 확인하세요.'})
    return Handler


def worker(service, stop):
    while not stop.is_set():
        try:
            service.schedule_due()
            job = service.claim()
            if job:
                service.perform(job)
                continue
        except Exception as exc:
            import sys
            print(f'worker_error: {type(exc).__name__}', file=sys.stderr)
        stop.wait(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[2] / 'data' / 'demo')
    args = parser.parse_args()
    service = MonitorService(args.data)
    # Hold an OS file lock for the worker lifetime, including when another port is chosen.
    lock = (service.folder / 'worker.lock').open('a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    import msvcrt
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit('이미 이 데이터 폴더의 웹 수집기가 실행 중입니다.')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(service, secrets.token_hex(32)))
    with service.db() as db:
        db.execute("UPDATE jobs SET status='error',finished=?,error='server_restarted: 이전 실행이 중단되었습니다.' WHERE status='running'", (time.time(),))
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(service, stop), daemon=True)
    thread.start()
    print(f'Local: http://127.0.0.1:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop.set()
        thread.join(timeout=65)
        lock.close()


if __name__ == '__main__':
    main()
