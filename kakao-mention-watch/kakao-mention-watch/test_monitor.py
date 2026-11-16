import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from monitor_service import MonitorService
from web_server import handler_for


def chat(*bodies):
    return '2026년 11월 15일 일요일\n'+'\n'.join(f'[테스트] [오전 9:{i:02d}] {body}' for i, body in enumerate(bodies))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = MonitorService(self.temp.name)
        self.config = dict(name='테스트 정책', room='테스트방', keywords=['두기', 'URGENT'], interval_seconds=60, enabled=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_persistence_validation_and_delete_cancels_queue_preserves_chat(self):
        self.service.store.ingest('테스트방', chat('기존 두기'))
        p = self.service.save_policy(self.config)
        restored = MonitorService(self.temp.name)
        self.assertEqual(restored.list_policies()[0]['keywords'], self.config['keywords'])
        with self.assertRaises(ValueError):
            restored.save_policy({**self.config, 'interval_seconds': 0}, p['id'])
        self.assertEqual(restored.list_policies()[0]['interval_seconds'], 60)
        first = restored.enqueue(p['id'])
        self.assertEqual(restored.enqueue(p['id'])['id'], first['id'])
        restored.save_policy({**self.config, 'enabled': False}, p['id'])
        self.assertIsNone(restored.claim())
        restored.enqueue(p['id'])
        restored.delete_policy(p['id'])
        self.assertEqual(restored.list_policies(), [])
        self.assertEqual(restored.snapshot()['jobs'][0]['status'], 'cancelled')
        self.assertEqual(restored.store.status('테스트방')['total_messages'], 1)

    def test_scheduler_off_due_and_stop_cancels_only_automatic_jobs(self):
        p = self.service.save_policy(self.config)
        self.service.schedule_due(time.time()+120)
        self.assertIsNone(self.service.claim())
        self.service.scheduler(True)
        self.service.schedule_due(time.time()+120)
        self.assertEqual(len(self.service.snapshot()['jobs']), 1)
        self.service.scheduler(False)
        self.assertIsNone(self.service.claim())
        self.service.enqueue(p['id'])
        self.assertIsNotNone(self.service.claim())

    def test_shared_room_independent_matching_and_failed_capture_does_not_advance(self):
        self.service.store.ingest('테스트방', chat('기존 두기'))
        p = self.service.save_policy(self.config)
        q = self.service.save_policy({**self.config,'name':'다른 정책'})
        self.service.enqueue(p['id'])
        self.service.perform(self.service.claim(), lambda room: self.service.store.ingest(room, chat('기존 두기','두기 Urgent 새 대화')))
        self.service.enqueue(q['id'])
        self.service.perform(self.service.claim(), lambda room: self.service.store.ingest(room, chat('기존 두기','두기 Urgent 새 대화')))
        mentions = self.service.snapshot()['mentions']
        self.assertEqual(len(mentions), 2)
        self.assertEqual(mentions[0]['keywords'], ['두기','URGENT'])
        self.service.enqueue(p['id'])
        def fail(room):
            raise RuntimeError('coverage_gap: 연결 실패')
        self.service.perform(self.service.claim(), fail)
        self.assertEqual(self.service.snapshot()['jobs'][0]['status'], 'error')
        self.assertEqual(len(self.service.snapshot()['mentions']), 2)

    def test_http_crud_token_and_origin_guards(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), handler_for(self.service,'test-token'))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def post(body, headers=None):
            req = urllib.request.Request(base+'/api/policies', data=json.dumps(body).encode(), headers={
                'Content-Type':'application/json', **(headers or {})})
            return urllib.request.urlopen(req)
        try:
            state = json.load(urllib.request.urlopen(base+'/api/state'))
            self.assertEqual(state['token'], 'test-token')
            with self.assertRaises(urllib.error.HTTPError) as failed:
                post({'policy':self.config})
            self.assertEqual(failed.exception.code, 403)
            failed.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as failed:
                post({'policy':self.config}, {'X-Monitor-Token':'test-token','Origin':'https://outside.example'})
            self.assertEqual(failed.exception.code, 403)
            failed.exception.close()
            saved = json.load(post({'policy':self.config}, {'X-Monitor-Token':'test-token','Origin':base}))
            self.assertEqual(saved['room'], '테스트방')
            self.assertEqual(len(json.load(urllib.request.urlopen(base+'/api/state'))['policies']), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_policy_tools_share_sqlite_with_console(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as folder:
            parameters = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name('monitor_mcp.py')), '--data',folder])
            async with asyncio.timeout(30):
                async with stdio_client(parameters) as (reader, writer):
                    async with ClientSession(reader,writer) as session:
                        await session.initialize()
                        tools = [t.name for t in (await session.list_tools()).tools]
                        self.assertIn('create_policy',tools)
                        result = await session.call_tool('create_policy',dict(name='MCP 테스트',room='테스트',keywords=['키워드']))
                        self.assertFalse(result.isError)
                        service = MonitorService(folder)
                        policies = service.list_policies()
                        self.assertEqual(len(policies),1)
                        result = await session.call_tool('request_check',dict(policy_id=policies[0]['id']))
                        self.assertTrue(result.isError)
                        self.assertEqual(service.snapshot()['jobs'], [])
                        result = await session.call_tool('delete_policy',dict(policy_id=policies[0]['id']))
                        self.assertFalse(result.isError)
                        self.assertEqual(service.list_policies(), [])


if __name__ == '__main__':
    unittest.main()
