"""Boundary tests use ephemeral RSA keys and synthetic chat; never production auth."""
from dataclasses import replace
from http.server import ThreadingHTTPServer
import http.client
import io
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from access_auth import AccessDenied, AccessVerifier
from monitor_service import MonitorService
from remote_config import RemoteConfig, load_config
from remote_control import disable
from remote_server import BoundedServer, ConfigProvider, RateLimit, handler_for, load_snapshot, render
from share_snapshot import publish
from web_server import handler_for as admin_handler


class RemoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.other = rsa.generate_private_key(public_exponent=65537,key_size=2048)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = RemoteConfig(True,'https://monitor.example.com','unit-team.cloudflareaccess.com',
                                   'a'*64,('allowed@example.com',),())
        self.verifier = AccessVerifier(self.config)
        self.verifier._fetch_keys = lambda: {'unit-key': self.private.public_key()}

    def tearDown(self):
        self.temp.cleanup()

    def token(self, claims=None, headers=None, private=None):
        stamp = int(time.time())
        body = dict(iss=self.config.issuer,aud=[self.config.audience],sub='test-subject',
                    email='allowed@example.com',iat=stamp,exp=stamp+300)
        body.update(claims or {})
        return jwt.encode(body,private or self.private,algorithm='RS256',headers=headers or {'kid':'unit-key'})

    def test_signed_token_accepted_and_auth_attack_cases_denied(self):
        self.assertEqual(self.verifier.verify(self.token())['sub'],'test-subject')
        stamp = int(time.time())
        cases = [None, '', 'not-a-token', self.token(private=self.other),
                 self.token({'iss':'https://attacker.example'}), self.token({'aud':['wrong-app']}),
                 self.token({'email':'outsider@example.com'}), self.token({'email':['allowed@example.com']}),
                 self.token({'exp':stamp-20}), self.token({'iat':stamp+30,'exp':stamp+400}),
                 self.token({'iat':stamp-4000}), self.token({'iat':True}),
                 self.token({'exp':float('inf')}), self.token({'sub':''}),
                 self.token(headers={'kid':'unknown-key'}),
                 self.token(headers={'kid':'unit-key','jku':'https://attacker.example/keys'})]
        for token in cases:
            with self.subTest(token_case=cases.index(token)), self.assertRaises(AccessDenied):
                self.verifier.verify(token)
        token = jwt.encode({'email':'allowed@example.com'},'test-secret-for-algorithm-confusion-rejection-only',algorithm='HS256',headers={'kid':'unit-key'})
        with self.assertRaises(AccessDenied):
            self.verifier.verify(token)

    def test_missing_claim_and_jwks_failure_fail_closed(self):
        stamp = int(time.time())
        body = dict(iss=self.config.issuer,aud=self.config.audience,sub='x',email='allowed@example.com',iat=stamp,exp=stamp+300)
        for key in list(body):
            missing = dict(body)
            del missing[key]
            with self.subTest(claim=key), self.assertRaises(AccessDenied):
                self.verifier.verify(jwt.encode(missing,self.private,algorithm='RS256',headers={'kid':'unit-key'}))
        unavailable = AccessVerifier(self.config)
        with patch.object(unavailable,'_fetch_keys',side_effect=OSError('offline')) as fetch:
            for _ in range(2):
                with self.assertRaises(AccessDenied):
                    unavailable.verify(self.token())
            self.assertEqual(fetch.call_count,1)

    def fixture_snapshot(self):
        service = MonitorService(self.root/'private')
        p = service.save_policy(dict(name='PRIVATE_POLICY_NAME',room='PRIVATE_ROOM',keywords=['PRIVATE_KEYWORD'],interval_seconds=300,enabled=False))
        q = service.save_policy(dict(name='UNSHARED_POLICY',room='UNSHARED_ROOM',keywords=['secret'],interval_seconds=300,enabled=False))
        with service.store.connect() as db:
            db.execute('INSERT INTO checkpoints VALUES(?,?,?)',('PRIVATE_ROOM','[]','2026-11-14T12:22:28+00:00'))
        with service.db() as db:
            db.execute('INSERT INTO mentions(policy_id,message_id,room,keywords,record,at) VALUES(?,?,?,?,?,?)',
                       (p['id'],1,'PRIVATE_ROOM','["PRIVATE_KEYWORD"]','{"sender":"PRIVATE_SENDER","message":"PRIVATE_BODY<script>"}',time.time()))
            db.execute("INSERT INTO jobs(id,policy_id,config,status,created,error) VALUES(?,?,?,'error',?,?)",
                       ('job',p['id'],'{}',time.time(),'PRIVATE_ERROR_PATH'))
        config = replace(self.config,shared_policies=((p['id'],'공유 정책 A'),))
        path = self.root/'public'/'summary.json'
        snapshot = publish(config,self.root/'private',path)
        return service,config,path,snapshot,q['id']

    def test_publisher_redacts_sensitive_fields_and_excludes_unshared(self):
        service,config,path,raw,_ = self.fixture_snapshot()
        payload = path.read_text(encoding='utf-8')
        for private in ('PRIVATE_BODY','PRIVATE_SENDER','PRIVATE_KEYWORD','PRIVATE_ROOM','PRIVATE_ERROR_PATH',
                        'PRIVATE_POLICY_NAME','UNSHARED_POLICY','UNSHARED_ROOM'):
            self.assertNotIn(private,payload)
        public = load_snapshot(path,config)
        self.assertEqual(public['policies'][0]['mentions_last_24h'],1)
        self.assertEqual(public['policies'][0]['check_status'],'error')
        self.assertEqual(len(public['policies']),1)
        self.assertNotIn('selection_revision',public)

    def test_snapshot_stale_unknown_fields_and_alias_reassignment_denied(self):
        _,config,path,raw,other_id = self.fixture_snapshot()
        for mutation in ({**raw,'generated_at':time.time()-301}, {**raw,'generated_at':float('nan')},
                         {**raw,'record':'PRIVATE_BODY'},
                         {**raw,'policies':[{**raw['policies'][0],'message':'PRIVATE_BODY'}]}):
            path.write_text(json.dumps(mutation),encoding='utf-8')
            with self.assertRaises(ValueError):
                load_snapshot(path,config)
        path.write_text(json.dumps(raw),encoding='utf-8')
        with self.assertRaises(ValueError):
            load_snapshot(path,replace(config,shared_policies=((other_id,'공유 정책 A'),)))
        public = load_snapshot(path,config)
        public['policies'][0]['alias'] = '<script>alert(1)</script>'
        html = render(public).decode()
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)

    def write_config(self, **changes):
        raw = dict(enabled=True,public_origin=self.config.public_origin,team_domain=self.config.team_domain,
                   audience=self.config.audience,allowed_emails=list(self.config.allowed_emails),shared_policies=[])
        raw.update(changes)
        path = self.root/'remote.local.json'
        path.write_text(json.dumps(raw),encoding='utf-8')
        return path

    def test_config_validation_and_live_revocation(self):
        path = self.write_config()
        provider = ConfigProvider(path)
        self.assertTrue(provider.get()[0].enabled)
        disable(path)
        with self.assertRaises(ValueError):
            provider.get()
        for change in (dict(public_origin='http://monitor.example.com'),dict(team_domain='attacker.example'),
                       dict(allowed_emails=['*@example.com']),dict(allowed_emails=[]),dict(audience=''),
                       dict(public_origin='https://user:pass@monitor.example.com'),dict(snapshot_max_age=999999),
                       dict(shared_policies=[{'id':'a'*32,'alias':'one'},{'id':'a'*32,'alias':'two'}])):
            with self.subTest(change=change),self.assertRaises(ValueError):
                load_config(self.write_config(**change))

    def test_http_read_only_auth_host_origin_and_logs(self):
        _,config,path,_,_ = self.fixture_snapshot()
        verifier = self.verifier
        class Provider:
            def get(self):
                return config,verifier
        output = io.StringIO()
        audit = logging.getLogger('test-remote-access')
        audit.setLevel(logging.INFO)
        stream = logging.StreamHandler(output)
        audit.addHandler(stream)
        server = BoundedServer(('127.0.0.1',0),handler_for(Provider(),path,audit))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        token = self.token()
        def request(route='/',method='GET',headers=None,authenticated=True):
            connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            h = {'Host':config.hostname}
            if authenticated:
                h['Cf-Access-Jwt-Assertion'] = token
            h.update(headers or {})
            connection.request(method,route,headers=h)
            response = connection.getresponse()
            status,body,response_headers = response.status,response.read(),dict(response.getheaders())
            connection.close()
            return status,body,response_headers
        try:
            self.assertEqual(request(authenticated=False)[0],401)
            self.assertEqual(request(headers={'Cf-Access-Jwt-Assertion':'forged'})[0],401)
            self.assertEqual(request(headers={'Host':'localhost'})[0],403)
            self.assertEqual(request(headers={'Origin':'https://attacker.example'})[0],403)
            status,body,headers = request('/api/summary')
            self.assertEqual(status,200)
            self.assertNotIn(b'PRIVATE',body)
            self.assertIn('no-store',headers['Cache-Control'])
            self.assertIn("script-src 'none'",headers['Content-Security-Policy'])
            self.assertEqual(request()[0],200)
            self.assertEqual(request('/style.css')[0],200)
            for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT'):
                self.assertEqual(request('/api/check',method)[0],405)
            for route in ('/api/state','/api/transcript/anything','/api/policies','/../monitor.sqlite3','/remote.local.json','/?token=leaked'):
                self.assertEqual(request(route)[0],404)
            log = output.getvalue()
            self.assertNotIn(token,log)
            self.assertNotIn('allowed@example.com',log)
            self.assertNotIn('leaked',log)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            audit.removeHandler(stream)

    def test_rate_limit_and_admin_rejects_tunnel_headers(self):
        limiter = RateLimit(capacity=1,rate=0)
        self.assertTrue(limiter.allow())
        self.assertFalse(limiter.allow())
        service = MonitorService(self.root/'admin')
        server = ThreadingHTTPServer(('127.0.0.1',0),admin_handler(service,'token'))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request('GET','/api/state',headers={'CF-Ray':'test'})
            response = connection.getresponse()
            self.assertEqual(response.status,403)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
