"""Authenticated, GET-only summary viewer. No DB, collector, proxy or command routes."""
import argparse
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import threading
import time

from access_auth import AccessDenied, AccessVerifier
from remote_config import load_config

WEB = Path(__file__).with_name('remote_web')
SNAPSHOT_KEYS = {'schema_version','selection_revision','generated_at','policies'}
POLICY_KEYS = {'alias','automatic_target','check_status','last_check_finished',
               'last_collection_success','mentions_last_24h','last_mention_at'}
STATUSES = {'never','queued','running','ok','error','cancelled'}


def timestamp(value, nullable=False):
    if nullable and value is None:
        return None
    if type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= time.time()+5:
        raise ValueError('snapshot_timestamp')
    return value


def load_snapshot(path, config):
    with Path(path).open('rb') as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError('snapshot_size')
    raw = json.loads(data)
    if not isinstance(raw,dict) or set(raw) != SNAPSHOT_KEYS or type(raw['schema_version']) is not int or raw['schema_version'] != 1:
        raise ValueError('snapshot_schema')
    generated = timestamp(raw['generated_at'])
    if raw['selection_revision'] != config.selection_revision:
        raise ValueError('snapshot_selection_changed')
    if time.time()-generated > config.snapshot_max_age:
        raise ValueError('snapshot_expired')
    rows = raw['policies']
    if not isinstance(rows,list) or len(rows) > 20:
        raise ValueError('snapshot_policies')
    allowed = {alias for _,alias in config.shared_policies}
    sanitized = []
    seen = set()
    for row in rows:
        if not isinstance(row,dict) or set(row) != POLICY_KEYS:
            raise ValueError('snapshot_fields')
        alias = row['alias']
        if not isinstance(alias,str) or alias in seen:
            raise ValueError('snapshot_alias')
        seen.add(alias)
        if alias not in allowed:
            continue  # A locally removed share takes effect before the next publication.
        if type(row['automatic_target']) is not bool or row['check_status'] not in STATUSES:
            raise ValueError('snapshot_status')
        count = row['mentions_last_24h']
        if type(count) is not int or count < 0:
            raise ValueError('snapshot_count')
        success = row['last_collection_success']
        if success is not None:
            if not isinstance(success,str) or len(success) > 40:
                raise ValueError('snapshot_collection_time')
            parsed = datetime.fromisoformat(success)
            if parsed.tzinfo is None:
                raise ValueError('snapshot_timezone')
            timestamp(parsed.timestamp())
            success = parsed.astimezone(timezone.utc).isoformat()
        sanitized.append({'alias':alias, 'automatic_target':row['automatic_target'],
                          'check_status':row['check_status'], 'mentions_last_24h':count,
                          'last_check_finished':timestamp(row['last_check_finished'],True),
                          'last_mention_at':timestamp(row['last_mention_at'],True),
                          'last_collection_success':success})
    return {'schema_version':1,'generated_at':generated,'policies':sanitized}


def human_time(value):
    if value is None:
        return '기록 없음'
    if isinstance(value,str):
        return escape(value)
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def render(snapshot):
    labels = {'never':'미실행','queued':'대기','running':'점검 중','ok':'완료','error':'실패','cancelled':'취소'}
    rows = ''.join(f'''<tr><td>{escape(p['alias'])}</td><td>{labels[p['check_status']]}</td>
        <td>{p['mentions_last_24h']}</td><td>{human_time(p['last_mention_at'])}</td>
        <td>{human_time(p['last_collection_success'])}</td></tr>''' for p in snapshot['policies'])
    if not rows:
        rows = '<tr><td colspan="5">공유하도록 선택한 정책이 없습니다.</td></tr>'
    template = (WEB/'index.html').read_text(encoding='utf-8')
    return template.replace('{{generated}}',human_time(snapshot['generated_at'])).replace('{{rows}}',rows).encode('utf-8')


class ConfigProvider:
    def __init__(self, path):
        self.path = Path(path)
        self._config = None
        self._verifier = None
        self._lock = threading.Lock()

    def get(self):
        # Local enable/disable/email revocation is checked on EVERY request.
        config = load_config(self.path)
        if not config.enabled:
            raise ValueError('sharing_disabled')
        with self._lock:
            if config != self._config:
                self._config, self._verifier = config, AccessVerifier(config)
            return self._config, self._verifier


class RateLimit:
    def __init__(self, capacity=30, rate=2):
        self.capacity, self.rate = capacity, rate
        self.tokens, self.last = capacity, time.monotonic()
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            stamp = time.monotonic()
            self.tokens = min(self.capacity,self.tokens+(stamp-self.last)*self.rate)
            self.last = stamp
            if self.tokens < 1:
                return False
            self.tokens -= 1
            return True


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self,*args,**kwargs):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args,**kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()


def handler_for(provider, snapshot_path, audit, limiter=None):
    limiter = limiter or RateLimit()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'SummaryViewer'
        sys_version = ''
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self,*args):
            pass  # Never log raw URLs, headers, JWTs, emails or chat data.

        def reply(self, code, body, content_type='application/json; charset=utf-8', reason='ok'):
            data = body if isinstance(body,bytes) else json.dumps(body,ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            for key,value in {'Content-Type':content_type,'Content-Length':str(len(data)),
                              'Cache-Control':'no-store, private','Connection':'close',
                              'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
                              'Referrer-Policy':'no-referrer',
                              'Permissions-Policy':'camera=(), microphone=(), geolocation=()',
                              'Strict-Transport-Security':'max-age=31536000',
                              'Content-Security-Policy':"default-src 'none'; style-src 'self'; script-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():
                self.send_header(key,value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
            self.close_connection = True
            audit.info(json.dumps({'at':int(time.time()),'status':code,'reason':reason}))

        def dispatch(self):
            if not limiter.allow():
                self.reply(429,{'error':'요청이 너무 많습니다. 잠시 후 다시 시도하세요.'},reason='rate_limited')
                return
            if sum(len(k)+len(v) for k,v in self.headers.items()) > 20000:
                self.reply(431,{'error':'요청 헤더가 너무 큽니다.'},reason='headers_size')
                return
            try:
                config, verifier = provider.get()
            except Exception:
                self.reply(403,{'error':'원격 공유를 사용할 수 없습니다.'},reason='config_denied')
                return
            hosts = self.headers.get_all('Host',[])
            origins = self.headers.get_all('Origin',[])
            if len(hosts) != 1 or hosts[0].lower() != config.hostname or len(origins)>1 or (origins and origins[0] != config.public_origin):
                self.reply(403,{'error':'접속 주소를 확인하세요.'},reason='origin_denied')
                return
            assertions = self.headers.get_all('Cf-Access-Jwt-Assertion',[])
            if len(assertions) != 1:
                self.reply(401,{'error':'Cloudflare Access 로그인이 필요합니다.'},reason='missing_assertion')
                return
            try:
                verifier.verify(assertions[0])
            except AccessDenied:
                self.reply(401,{'error':'인증이 만료되었거나 허용되지 않았습니다.'},reason='auth_denied')
                return
            except Exception:
                self.reply(401,{'error':'인증을 확인할 수 없습니다.'},reason='auth_unavailable')
                return
            if self.command not in ('GET','HEAD'):
                self.reply(405,{'error':'이 화면은 조회만 지원합니다.'},reason='method_denied')
                return
            if self.path not in ('/','/style.css','/api/summary'):
                self.reply(404,{'error':'제공하지 않는 경로입니다.'},reason='route_denied')
                return
            if self.path == '/style.css':
                self.reply(200,(WEB/'style.css').read_bytes(),'text/css; charset=utf-8')
                return
            try:
                snapshot = load_snapshot(snapshot_path,config)
            except Exception:
                self.reply(503,{'error':'최신 요약을 사용할 수 없습니다. PC의 게시 프로세스를 확인하세요.'},reason='snapshot_unavailable')
                return
            if self.path == '/api/summary':
                self.reply(200,snapshot)
            else:
                self.reply(200,render(snapshot),'text/html; charset=utf-8')

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = dispatch
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--audit-log',type=Path,required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    if not config.enabled:
        raise SystemExit('원격 공유가 꺼져 있습니다. 설정을 완료한 뒤 enabled=true로 지정하세요.')
    if args.port == 8765:
        raise SystemExit('로컬 관리 포트 8765를 원격 조회에 사용하지 마세요.')
    args.audit_log.parent.mkdir(parents=True,exist_ok=True)
    audit = logging.getLogger('remote-access')
    audit.setLevel(logging.INFO)
    audit.addHandler(RotatingFileHandler(args.audit_log,maxBytes=1_000_000,backupCount=2,encoding='utf-8'))
    server = BoundedServer(('127.0.0.1',args.port),handler_for(ConfigProvider(args.config),args.snapshot,audit))
    print(f'Read-only origin: 127.0.0.1:{args.port} (Access JWT required)',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
