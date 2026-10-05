import json
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from pathlib import Path
import fakeredis
from app import web, storage, worker, telegram_store


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, DOWNLOAD_DIR=self.tmp.name,
                              WEB_PASSWORD='test-password', WEB_ADMIN_PASSWORD='admin-password',
                              WEB_SECRET_KEY='test-secret', ORYX_STREAM_URL='http://oryx/live/original.flv')
        self.env.start()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.app = web.create_app(self.redis)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.post('/api/login', json={'password': 'admin-password'}).json['csrf']
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)

    def post(self, path, data):
        return self.client.post('/api/' + path, json=data, headers={'X-CSRF-Token': self.csrf})

    def save_watch(self, identifier='watch', **fields):
        value = dict(id=identifier, user_id=7, chat_id=77, channel='https://www.youtube.com/@rribatam',
                     start=100, end=200, mode='first', auto_transcribe=False, status='active')
        value.update(fields)
        telegram_store.save_watch(value)
        return value

    def test_transcription_cancel_queue_restart_auth_and_completed_race(self):
        from app import transcription_queue, transcription_worker
        storage.save_recording(dict(job_id='cancel-me', filename='recording.mp4', source='youtube'), 'ready')
        self.assertEqual(self.post('recordings/cancel-me/transcript', {}).status_code, 202)
        old = storage.recording('cancel-me')['transcript']['request_id']
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post('/api/recordings/cancel-me/transcript/cancel', json={}).status_code, 401)
        self.assertEqual(self.client.post('/api/recordings/cancel-me/transcript/cancel', json={}).status_code, 403)
        self.redis.set('capture:owner', 'media')
        self.assertEqual(self.post('recordings/cancel-me/transcript/cancel', {}).json['status'], 'cancelled')
        self.assertEqual(self.post('recordings/cancel-me/transcript/cancel', {}).status_code, 200)
        self.assertFalse(storage.save_transcription_state('cancel-me', 'completed', guarded=True, request_id=old))
        with patch.object(transcription_worker, 'r', self.redis):
            transcription_worker.recover_jobs()
        self.assertEqual(storage.recording('cancel-me')['transcript']['status'], 'cancelled')
        self.assertEqual(self.post('recordings/cancel-me/transcript', {}).status_code, 202)
        new = storage.recording('cancel-me')['transcript']['request_id']
        self.assertNotEqual(old, new)
        self.assertEqual(self.post('recordings/cancel-me/transcript/cancel', {'request_id': old}).status_code, 409)
        self.assertEqual(storage.recording('cancel-me')['transcript']['status'], 'queued')
        self.assertFalse(storage.save_transcription_state('cancel-me', 'failed', guarded=True, request_id=old))
        self.assertTrue(storage.save_transcription_state('cancel-me', 'completed', guarded=True, request_id=new))
        self.assertEqual(self.post('recordings/cancel-me/transcript/cancel', {}).status_code, 409)
        self.assertEqual(storage.recording('cancel-me')['transcript']['status'], 'completed')
        self.assertEqual(self.redis.get('capture:owner'), 'media')
        self.assertEqual(self.redis.keys('stop:*'), [])
        self.assertEqual(self.post('recordings/missing/transcript/cancel', {}).status_code, 404)

    def test_admin_watches_use_shared_ledger_with_derived_status_and_no_private_chat_data(self):
        self.save_watch('waiting', start=160, end=200)
        self.save_watch('active', mode='every', auto_transcribe=True)
        self.save_watch('expired', end=150)
        self.save_watch('finished', status='finished')
        self.save_watch('cancelled', status='cancelled')
        with patch.object(web, 'time') as clock:
            clock.time.return_value = 150
            response = self.client.get('/api/admin/watches')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['active_count'], 2)
        rows = {row['id']: row for row in response.json['watches']}
        self.assertEqual({key: row['status'] for key, row in rows.items()},
                         dict(waiting='waiting', active='waiting', expired='expired', finished='finished', cancelled='cancelled'))
        self.assertTrue(rows['active']['auto_transcribe'])
        self.assertEqual(rows['active']['mode'], 'every')
        self.assertNotIn('user_id', response.text)
        self.assertNotIn('chat_id', response.text)
        self.assertEqual(next(w for w in telegram_store.watches() if w['id'] == 'expired')['status'], 'active')
        self.assertEqual(self.redis.llen('download_queue'), 0)
        with patch.object(web, 'time') as clock:
            clock.time.return_value = 160
            self.assertEqual(next(w for w in self.client.get('/api/admin/watches').json['watches']
                                  if w['id'] == 'waiting')['status'], 'waiting')

    def test_watch_apis_require_admin_auth_csrf_and_do_not_offer_web_creation(self):
        self.save_watch()
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/api/admin/watches').status_code, 401)
        self.assertEqual(anonymous.post('/api/admin/watches/watch/cancel', json={}).status_code, 401)
        user = self.app.test_client()
        token = user.post('/api/login', json={'password': 'test-password'}).json['csrf']
        self.assertEqual(user.get('/api/admin/watches').status_code, 403)
        self.assertEqual(user.post('/api/admin/watches/watch/cancel', json={},
                                  headers={'X-CSRF-Token': token}).status_code, 403)
        self.assertEqual(self.client.post('/api/admin/watches/watch/cancel', json={}).status_code, 403)
        self.assertEqual(self.post('admin/watches', {}).status_code, 405)
        self.assertEqual(telegram_store.watches()[0]['status'], 'active')

    def test_watch_discovery_issue_and_recording_derive_from_shared_job_without_window_stop(self):
        self.save_watch('issue')
        telegram_store.discovery_state('issue', 'HTTP Error 403: [redacted]', 120)
        self.save_watch('recording', status='finished', last_capture=dict(job_id='watch-job', video_id='abcdefghijk'))
        storage.save_recording(dict(job_id='watch-job', source='youtube', origin='telegram_watch', is_live=True,
                                    source_metadata=dict(title='Dialog RRI Batam')), 'recording')
        with patch.object(web, 'time') as clock:
            clock.time.return_value = 150
            rows = {row['id']: row for row in self.client.get('/api/admin/watches').json['watches']}
        self.assertEqual(rows['issue']['status'], 'discovery_issue')
        self.assertTrue(rows['issue']['can_cancel'])
        self.assertEqual(rows['recording']['status'], 'recording')
        self.assertFalse(rows['recording']['can_cancel'])
        self.assertEqual(rows['recording']['last_capture']['title'], 'Dialog RRI Batam')
        with patch.object(web, 'time') as clock:
            clock.time.return_value = 220
            rows = {row['id']: row for row in self.client.get('/api/admin/watches').json['watches']}
        self.assertEqual(rows['issue']['status'], 'expired')
        self.assertEqual(rows['recording']['status'], 'recording')
        self.assertFalse(self.redis.keys('stop:*'))

    def test_canonical_queued_watch_stays_queued_in_history_and_original_defaults(self):
        job = dict(job_id='watch-job', source='youtube', origin='telegram_watch', is_live=True)
        storage.save_recording(job, 'queued')
        self.redis.rpush('download_queue', json.dumps(job))
        rows = self.client.get('/api/recordings').json['recordings']
        self.assertEqual(rows[0]['state'], 'queued')
        self.assertEqual(rows[0]['origin'], 'telegram_watch')
        self.redis.delete('download_queue')
        self.redis.set('worker:heartbeat', 1)
        response = self.post('record', dict(source='youtube', url='https://youtu.be/abcdefghijk'))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(json.loads(self.redis.lindex('download_queue', 0))['compression'], 'original')

    def test_original_media_remains_securely_downloadable_during_and_after_processing(self):
        root = Path(self.tmp.name)
        (root / 'original.mp4').write_bytes(b'0123456789')
        job = dict(job_id='preserved', source='youtube', filename='original.mp4', original_filename='original.mp4')
        storage.save_recording(job, 'finalizing')
        response = self.client.get('/api/files/original.mp4?inline=1', headers={'Range': 'bytes=2-5'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b'2345')
        response.close()
        (root / 'processed.mp4').write_bytes(b'processed')
        job['filename'] = 'processed.mp4'
        storage.save_recording(job, 'ready')
        response = self.client.get('/api/files/original.mp4')
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.app.test_client().get('/api/files/original.mp4').status_code, 401)

    def test_web_cancel_persists_for_telegram_restart_without_stopping_capture_or_tasks(self):
        self.save_watch(mode='every')
        task = dict(job=dict(job_id='capture', requested_at=150), dispatch='pending')
        self.assertTrue(telegram_store.claim_video('watch', 'abcdefghijk', task))
        self.redis.set('capture:owner', 'capture')
        self.redis.rpush('download_queue', 'existing-task')
        self.assertEqual(self.post('admin/watches/watch/cancel', {}).status_code, 200)
        self.assertEqual(self.post('admin/watches/watch/cancel', {}).status_code, 200)
        restarted = web.create_app(self.redis).test_client()
        restarted.post('/api/login', json={'password': 'admin-password'})
        self.assertEqual(restarted.get('/api/admin/watches').json['watches'][0]['status'], 'cancelled')
        self.assertEqual(telegram_store.watches()[0]['last_capture']['video_id'], 'abcdefghijk')
        self.assertFalse(telegram_store.claim_video('watch', 'bbbbbbbbbbb', task))
        self.assertEqual(self.redis.get('capture:owner'), 'capture')
        self.assertEqual(self.redis.lrange('download_queue', 0, -1), ['existing-task'])
        self.assertEqual(self.redis.keys('stop:*'), [])
        self.assertEqual(len(telegram_store.tasks()), 1)
        self.assertEqual(self.post('admin/watches/missing/cancel', {}).status_code, 404)

    def test_watch_last_capture_uses_existing_recording_metadata_and_legacy_watches_are_safe(self):
        self.save_watch('legacy')
        self.save_watch('captured', mode='every')
        storage.save_recording(dict(job_id='capture', source_metadata={'title': 'Dialog Batam'}), 'ready')
        task = dict(job=dict(job_id='capture', requested_at=150), dispatch='pending')
        self.assertTrue(telegram_store.claim_video('captured', 'abcdefghijk', task))
        with patch.object(web, 'time') as clock:
            clock.time.return_value = 150
            rows = {row['id']: row for row in self.client.get('/api/admin/watches').json['watches']}
        self.assertIsNone(rows['legacy']['last_capture'])
        self.assertEqual(rows['captured']['last_capture']['title'], 'Dialog Batam')
        self.assertEqual(rows['captured']['last_capture']['state'], 'ready')
        self.assertFalse(telegram_store.claim_video('captured', 'abcdefghijk', task))
        self.assertEqual(telegram_store.watches()[1]['last_capture']['video_id'], 'abcdefghijk')

    def test_tiktok_admission_and_validation(self):
        self.redis.set('worker:heartbeat', 1)
        for url in ('https://tiktok.com.evil.test/@a/live', 'https://www.tiktok.com/@a/video/not-an-id',
                    'https://evil.test/https://www.tiktok.com/@a/live', 'https://www.tiktok.com/@a',
                    'https://user:pass@www.tiktok.com/@a/live'):
            self.assertEqual(self.post('record', {'source': 'tiktok', 'url': url}).status_code, 400)
        self.assertEqual(self.redis.llen('download_queue'), 0)
        result = self.post('record', {'source': 'tiktok', 'url': 'https://www.tiktok.com/@tester/live?share=1'})
        self.assertEqual(result.status_code, 202)
        job = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(job['source_name'], 'TikTok')
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

    def test_social_url_detection_validation_and_job_options(self):
        for source, url, live in [('tiktok', 'https://tiktok.com/@a/video/123?share=1', False),
                                  ('tiktok', 'https://tiktok.com/@a/live', True),
                                  ('tiktok', 'https://tiktok.com/@a/photo/123', False),
                                  ('instagram', 'https://instagram.com/reel/AbC_123/?igsh=abc', False),
                                  ('instagram', 'https://www.instagram.com/p/AbC-123/', False)]:
            with self.subTest(url=url):
                self.redis.flushall()
                self.redis.set('worker:heartbeat', 1)
                response = self.post('record', dict(source=source, url=url, quality='720', compression='balanced'))
                self.assertEqual(response.status_code, 202)
                job = json.loads(self.redis.lindex('download_queue', 0))
                self.assertEqual(job['source'], source)
                self.assertEqual(job['is_live'], live)
                self.assertEqual(job['quality'], '720')
                self.assertEqual(job['compression'], 'balanced')
                self.assertNotIn('?', job['url'])
        for url in ['https://instagram.com/stories/abc/123/', 'https://instagram.com/abc/live/',
                    'https://instagram.com/abc/', 'https://instagram.com.evil/p/abc/',
                    'https://user:pass@instagram.com/p/abc/', 'https://instagram.com:8888/p/abc/']:
            self.assertEqual(self.post('record', dict(source='instagram', url=url)).status_code, 400)

    def test_instagram_video_estimate_and_image_only_error(self):
        from types import SimpleNamespace
        video = dict(duration=60, filesize=1000, formats=[dict(url='https://cdn.example/v.mp4', ext='mp4')])
        for metadata, expected in [(video, 200), ({'formats': []}, 400)]:
            with patch.object(web.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode())) as run:
                response = self.post('estimate', dict(source='instagram', url='https://instagram.com/p/ABC/'))
            self.assertEqual(response.status_code, expected)
            self.assertIn('--ignore-no-formats-error', run.call_args.args[0])
            if expected == 400:
                self.assertIn('tidak berisi video', response.json['error'])

    def test_roles_password_change_and_admin_only_delete(self):
        user = self.app.test_client()
        login = user.post('/api/login', json={'password': 'test-password'})
        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.json['is_admin'])
        headers = {'X-CSRF-Token': login.json['csrf']}
        storage.save_recording({'job_id': 'protected-history', 'source': 'youtube'}, 'failed')
        denied = user.post('/api/recordings/delete', json={'job_ids': ['protected-history']},
                           headers=headers)
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(storage.recordings())
        self.assertEqual(user.post('/api/admin/password', json={
            'current_password': 'admin-password', 'target': 'user',
            'new_password': 'new-user-password'}, headers=headers).status_code, 403)

        changed = self.post('admin/password', {'current_password': 'admin-password',
                                               'target': 'user',
                                               'new_password': 'new-user-password'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(user.get('/api/session').status_code, 401)
        self.assertEqual(self.app.test_client().post('/api/login',
                         json={'password': 'test-password'}).status_code, 401)
        self.assertEqual(self.app.test_client().post('/api/login',
                         json={'password': 'new-user-password'}).status_code, 200)

    def test_admin_can_change_own_password_and_keep_current_session(self):
        changed = self.post('admin/password', {'current_password': 'admin-password',
                                               'target': 'admin',
                                               'new_password': 'new-admin-password'})
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(self.client.get('/api/session').json['is_admin'])
        old = self.app.test_client().post('/api/login', json={'password': 'admin-password'})
        new = self.app.test_client().post('/api/login', json={'password': 'new-admin-password'})
        self.assertEqual(old.status_code, 401)
        self.assertTrue(new.json['is_admin'])

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
        self.assertEqual(self.post('stop', {'job_id': job_id}).status_code, 200)

    def test_operator_can_choose_local_or_configured_archive(self):
        self.redis.set('worker:heartbeat', 1)
        local = self.post('record', {'source': 'youtube',
                                     'url': 'https://youtu.be/test', 'storage': 'local'})
        self.assertEqual(local.status_code, 202)
        job = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(job['storage'], 'local')
        self.assertFalse(job['archive'])

        self.redis.flushall()
        self.redis.set('worker:heartbeat', 1)
        unavailable = self.post('record', {'source': 'youtube',
                                           'url': 'https://youtu.be/test', 'storage': 'archive'})
        self.assertEqual(unavailable.status_code, 400)
        with patch.dict(os.environ, ARCHIVE_ENABLED='true'):
            archived = self.post('record', {'source': 'youtube',
                                             'url': 'https://youtu.be/test', 'storage': 'archive'})
            self.assertEqual(archived.status_code, 202)
            job = json.loads(self.redis.lindex('download_queue', 0))
            self.assertEqual(job['storage'], 'archive')
            self.assertTrue(job['archive'])

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

    def test_youtube_size_estimate_and_quality_snapshot(self):
        from types import SimpleNamespace
        metadata = {
            'is_live': False,
            'duration': 60,
            'requested_formats': [
                {'height': 720, 'filesize': 10_000_000, 'tbr': 1500},
                {'filesize_approx': 1_000_000, 'tbr': 128},
            ],
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode())
        with patch.object(web.subprocess, 'run', return_value=result) as run:
            response = self.post('estimate', {'source': 'youtube',
                                              'url': 'https://youtu.be/test',
                                              'quality': '720'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['estimated_bytes'], 11_000_000)
        self.assertEqual(response.json['height'], 720)
        command = run.call_args.args[0]
        self.assertIn('[height<=720]', command[command.index('-f') + 1])

        self.redis.set('worker:heartbeat', 1)
        queued = self.post('record', {'source': 'youtube',
                                      'url': 'https://youtu.be/test',
                                      'quality': '720',
                                      'compression': 'balanced'})
        self.assertEqual(queued.status_code, 202)
        job = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(job['quality'], '720')
        self.assertEqual(job['compression'], 'balanced')

    def test_invalid_compression_preset_is_rejected(self):
        self.redis.set('worker:heartbeat', 1)
        response = self.post('record', {'source': 'youtube',
                                        'url': 'https://youtu.be/test',
                                        'compression': 'tiny'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    def test_youtube_mp3_estimate_and_job_snapshot(self):
        from types import SimpleNamespace
        metadata = {
            'is_live': False,
            'duration': 60,
            'filesize_approx': 1_000_000,
            'tbr': 128,
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(metadata).encode())
        with patch.object(web.subprocess, 'run', return_value=result) as run:
            response = self.post('estimate', {'source': 'youtube',
                                              'url': 'https://youtu.be/test',
                                              'quality': '1080',
                                              'format': 'mp3'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['format'], 'mp3')
        self.assertEqual(response.json['quality'], 'best')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-f') + 1], 'ba/b')

        self.redis.set('worker:heartbeat', 1)
        queued = self.post('record', {'source': 'youtube',
                                      'url': 'https://youtu.be/test',
                                      'quality': '1080',
                                      'format': 'mp3'})
        self.assertEqual(queued.status_code, 202)
        job = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(job['output_format'], 'mp3')
        self.assertEqual(job['quality'], 'best')
        self.assertEqual(job['compression'], 'original')

    def test_mp3_is_rejected_for_live_sources(self):
        source = self.client.get('/api/sources').json['sources'][0]
        self.redis.set('worker:heartbeat', 1)
        response = self.post('record', {'source': 'oryx', 'source_id': source['id'],
                                        'format': 'mp3'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    def test_direct_stream_only_accepts_original_quality(self):
        source = self.client.get('/api/sources').json['sources'][0]
        self.redis.set('worker:heartbeat', 1)
        response = self.post('record', {'source': 'oryx', 'source_id': source['id'],
                                        'quality': '720'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    def test_panel_assets_and_authenticated_download(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/static/favicon.svg', response.data)
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

    def test_delete_download_removes_local_file_history_and_markers(self):
        path = Path(self.tmp.name) / 'file dengan spasi.mp4'
        path.write_bytes(b'fixture')
        storage.save_recording({'job_id': 'delete-job', 'filename': path.name,
                                'source': 'youtube', 'storage': 'local'}, 'ready')
        storage.add_marker('delete-job', 1, 'test')
        response = self.post('files/' + path.name + '/delete', {})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(path.exists())
        self.assertFalse(any(row['job_id'] == 'delete-job' for row in storage.recordings()))

    def test_delete_refuses_unfinished_archive_and_keeps_local_file(self):
        path = Path(self.tmp.name) / 'pending.mp4'
        path.write_bytes(b'fixture')
        storage.save_recording({'job_id': 'pending-job', 'filename': path.name,
                                'source': 'youtube', 'storage': 'archive'}, 'ready')
        storage.save_archive_state('pending-job', 'archive_pending')
        response = self.post('files/' + path.name + '/delete', {})
        self.assertEqual(response.status_code, 409)
        self.assertTrue(path.exists())
        self.assertTrue(any(row['job_id'] == 'pending-job' for row in storage.recordings()))

    def test_batch_delete_cleans_failed_and_missing_file_history(self):
        storage.save_recording({'job_id': 'failed-job', 'source': 'youtube'}, 'failed')
        storage.save_recording({'job_id': 'missing-job', 'source': 'youtube',
                                'filename': 'already-gone.mp4'}, 'ready')
        response = self.post('recordings/delete',
                             {'job_ids': ['failed-job', 'missing-job']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json['deleted']), {'failed-job', 'missing-job'})
        self.assertEqual(response.json['skipped'], [])
        self.assertFalse(storage.recordings())

    def test_rename_download_preserves_extension_and_catalogue(self):
        path = Path(self.tmp.name) / 'old name.mp4'
        path.write_bytes(b'fixture')
        storage.save_recording({'job_id': 'rename-job', 'source': 'youtube',
                                'filename': path.name, 'storage': 'local'}, 'ready')
        response = self.post('recordings/rename-job/rename', {'filename': 'Nama baru'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['filename'], 'Nama baru.mp4')
        self.assertFalse(path.exists())
        self.assertTrue((Path(self.tmp.name) / 'Nama baru.mp4').exists())
        self.assertEqual(storage.recordings()[0]['filename'], 'Nama baru.mp4')

    def test_rename_rejects_traversal_and_pending_archive(self):
        path = Path(self.tmp.name) / 'safe.mp4'
        path.write_bytes(b'fixture')
        storage.save_recording({'job_id': 'safe-job', 'source': 'youtube',
                                'filename': path.name, 'storage': 'local'}, 'ready')
        self.assertEqual(self.post('recordings/safe-job/rename',
                                   {'filename': '../escape.mp4'}).status_code, 400)
        storage.save_recording({'job_id': 'archive-job', 'source': 'youtube',
                                'filename': path.name, 'storage': 'archive'}, 'ready')
        storage.save_archive_state('archive-job', 'archive_pending')
        self.assertEqual(self.post('recordings/archive-job/rename',
                                   {'filename': 'later.mp4'}).status_code, 409)
        self.assertTrue(path.exists())


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
