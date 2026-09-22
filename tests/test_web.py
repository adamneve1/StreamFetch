import json
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from pathlib import Path
import fakeredis
from app import web, storage, worker


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, DOWNLOAD_DIR=self.tmp.name, WEB_PASSWORD='test-password', WEB_SECRET_KEY='test-secret', ORYX_STREAM_URL='http://oryx/live/original.flv')
        self.env.start()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.app = web.create_app(self.redis)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.post('/api/login', json={'password': 'test-password'}).json['csrf']
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)

    def post(self, path, data):
        return self.client.post('/api/' + path, json=data, headers={'X-CSRF-Token': self.csrf})

    def test_tiktok_admission_and_validation(self):
        self.redis.set('worker:heartbeat', 1)
        for url in ('https://tiktok.com.evil.test/@a/live', 'https://www.tiktok.com/@a/video/123',
                    'https://evil.test/https://www.tiktok.com/@a/live', 'https://www.tiktok.com/@a',
                    'https://user:pass@www.tiktok.com/@a/live'):
            self.assertEqual(self.post('record', {'source': 'tiktok', 'url': url}).status_code, 400)
        self.assertEqual(self.redis.llen('download_queue'), 0)
        result = self.post('record', {'source': 'tiktok', 'url': 'https://www.tiktok.com/@tester/live?share=1'})
        self.assertEqual(result.status_code, 202)
        job = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(job['source_name'], 'TikTok Live')
        self.assertEqual(job['url'], 'https://www.tiktok.com/@tester/live')
        self.assertEqual(self.post('stop', {'job_id': job['job_id']}).status_code, 200)

    def test_auth_and_csrf_required(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/api/sources').status_code, 401)
        self.assertEqual(self.client.post('/api/record', json={}).status_code, 403)
        self.assertEqual(anonymous.post('/api/login', json={'password': 'wrong'}).status_code, 401)
        missing = self.client.get('/api/missing')
        self.assertEqual(missing.status_code, 404)
        self.assertTrue(missing.is_json)

    def test_persistent_source_edit_and_job_snapshot(self):
        source = self.client.get('/api/sources').json['sources'][0]
        self.redis.set('worker:heartbeat', 1)
        result = self.post('record', {'source': 'oryx', 'source_id': source['id'], 'note': 'Opening'})
        self.assertEqual(result.status_code, 202)
        self.assertEqual(self.post('sources', dict(source, url='http://oryx/live/new.flv')).status_code, 200)
        job = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(job['stream_url'], 'http://oryx/live/original.flv')
        self.assertEqual(job['chat_id'], 'web')
        self.assertEqual(job['note'], 'Opening')
        self.assertEqual(storage.sources()[0]['url'], 'http://oryx/live/new.flv')
        self.assertEqual(web.create_app(self.redis).test_client().get('/api/sources').status_code, 401)

    def test_busy_record_rejected_and_stop_targets_exact_job(self):
        source = self.client.get('/api/sources').json['sources'][0]
        self.redis.set('worker:heartbeat', 1)
        data = {'source': 'oryx', 'source_id': source['id']}
        job_id = self.post('record', data).json['job_id']
        self.assertEqual(self.post('record', data).status_code, 409)
        self.assertEqual(self.post('stop', {'job_id': 'old-job'}).status_code, 409)
        self.assertEqual(self.post('stop', {'job_id': job_id}).status_code, 200)
        self.assertTrue(self.redis.exists('stop:' + job_id))
        self.redis.set('state:' + job_id, 'finalizing')
        self.assertEqual(self.post('stop', {'job_id': job_id}).status_code, 409)

    def test_url_validation_and_private_data_not_in_history(self):
        self.assertEqual(self.post('sources', {'name': 'bad', 'url': 'file:///etc/passwd'}).status_code, 400)
        self.assertEqual(self.post('record', {'source': 'youtube', 'url': 'https://youtube.com.evil.test/video'}).status_code, 400)
        storage.save_recording({'job_id': 'test', 'stream_url': 'http://user:secret@oryx/feed', 'source': 'oryx'}, 'failed')
        self.assertNotIn('secret', self.client.get('/api/recordings').get_data(as_text=True))

    def test_rri_player_resolves_for_save_check_and_record(self):
        from types import SimpleNamespace
        player = 'https://public-streaming.rri.go.id/playersite_238787d3-6705-4849-88aa-47cdf58dda1b.html'
        stream = 'https://public-streaming.rri.go.id/memfs/238787d3-6705-4849-88aa-47cdf58dda1b.m3u8'
        saved = self.post('sources', {'name': 'PRO 2 RRI BATAM', 'url': player})
        self.assertEqual(saved.status_code, 200)
        source_id = saved.json['id']
        self.assertEqual(next(s for s in storage.sources() if s['id'] == source_id)['url'], stream)
        probe = SimpleNamespace(returncode=0, stdout=b'{"streams":[{"codec_type":"video","codec_name":"h264"}]}')
        with patch.object(web.subprocess, 'run', return_value=probe) as run:
            self.assertEqual(self.post('check', {'url': player}).status_code, 200)
        self.assertEqual(run.call_args.args[0][-1], stream)
        self.redis.set('worker:heartbeat', 1)
        self.assertEqual(self.post('record', {'source': 'oryx', 'source_id': source_id}).status_code, 202)
        job = json.loads(self.redis.lindex('download_queue', 0))
        command, _ = worker.capture_command(job)
        self.assertEqual(command[command.index('-i') + 1], stream)
        self.assertEqual(job['source_name'], 'PRO 2 RRI BATAM')
        for bad in (player.replace('rri.go.id', 'rri.go.id.evil.test'),
                    player.replace('238787d3', 'invalid'),
                    player.replace('https://', 'https://user:secret@')):
            with self.assertRaises(ValueError):
                storage.validate_url(bad)

    def test_check_timeout_is_clean(self):
        import subprocess
        with patch.object(web.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffprobe', 10)):
            self.assertEqual(self.post('check', {'url': 'http://oryx/live.flv'}).status_code, 422)
        self.assertFalse(self.redis.exists('web:probe'))

    def test_panel_assets_and_authenticated_download(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        response.close()
        for asset in ('app.js', 'style.css', 'favicon.svg'):
            response = self.client.get('/static/' + asset)
            self.assertEqual(response.status_code, 200)
            response.close()
        path = Path(self.tmp.name) / '14092601.mp4'
        path.write_bytes(b'fixture')
        storage.save_recording({'job_id': 'ready-job', 'filename': path.name}, 'ready')
        with patch.dict(os.environ, DOWNLOAD_DIR=self.tmp.name):
            response = self.client.get('/api/files/' + path.name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'fixture')
            response.close()
            self.assertEqual(self.app.test_client().get('/api/files/' + path.name).status_code, 401)
            self.assertEqual(self.client.get('/api/files/unknown.mp4').status_code, 404)


class WebWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_job_finishes_without_telegram_and_records_catalogue(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, DATA_DIR=root):
            r = fakeredis.FakeRedis(decode_responses=True)
            job = {'job_id': 'web-test', 'chat_id': 'web', 'source': 'youtube', 'origin': 'web', 'url': 'https://youtu.be/test'}
            target = Path(root) / '14092601.mp4'
            target.write_bytes(b'test')
            r.set('capture:owner', 'web-test')
            r.set('active:web', 'web-test')
            with patch.object(worker, 'r', r), patch.object(worker, 'DOWNLOAD_DIR', Path(root)), \
                 patch.object(worker, 'send', AsyncMock()) as send, \
                 patch.object(worker, 'inspect_youtube', AsyncMock(return_value={})), \
                 patch.object(worker, 'capture', AsyncMock(return_value='operator_stop')), \
                 patch.object(worker, 'complete_recording', return_value=target):
                await worker.run_download(job)
            send.assert_not_called()
            self.assertEqual(storage.recordings()[0]['state'], 'ready')
            self.assertEqual(storage.recordings()[0]['filename'], target.name)
            self.assertEqual(json.loads(r.get('web:job:web-test'))['state'], 'ready')
