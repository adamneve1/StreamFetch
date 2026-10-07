import json
import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fakeredis

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456:TEST_TOKEN')
from app import bot, storage, telegram_controls as tc, telegram_store as store, transcription_queue, watch_service, web, worker


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, DATA_DIR=self.tmp.name,
                         DOWNLOAD_DIR=self.tmp.name, WEB_SECRET_KEY='test-secret',
                         WEB_PASSWORD='test-password', WEB_ADMIN_PASSWORD='admin-password',
                         WEB_USER_TELEGRAM_USER_ID='', WEB_USER_TELEGRAM_CHAT_ID='',
                         WEB_ADMIN_TELEGRAM_USER_ID='', WEB_ADMIN_TELEGRAM_CHAT_ID='',
                         TELEGRAM_ALLOWED_USER_IDS='7,8', STREAMFETCH_PUBLIC_URL='https://stream.example')
        env.start()
        self.addCleanup(env.stop)
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.controls = tc.Controls(self.redis)
        self.sender = SimpleNamespace(send_message=AsyncMock())
        self.context = SimpleNamespace(args=[], user_data={})

    def update(self, text='', user=7, callback=None):
        message = SimpleNamespace(text=text, reply_text=AsyncMock())
        query = SimpleNamespace(data=callback, answer=AsyncMock(), message=message) if callback else None
        return SimpleNamespace(effective_chat=SimpleNamespace(id=77), effective_user=SimpleNamespace(id=user),
                               effective_message=message, message=message, callback_query=query)

    def watch(self, mode='first', auto=False, start=100, end=200, identifier='watch1'):
        watch = dict(id=identifier, user_id=7, chat_id=77, channel='https://www.youtube.com/@rribatam',
                     start=start, end=end, mode=mode, auto_transcribe=auto, status='active')
        store.save_watch(watch)
        return watch

    def live(self, identifier='abcdefghijk'):
        return dict(id=identifier, url='https://www.youtube.com/watch?v=' + identifier)

    def recording(self, job_id='video-job', state='ready', transcript=None):
        storage.save_recording(dict(job_id=job_id, source='youtube', filename='dialog.mp4'), state)
        if transcript:
            storage.save_transcription_state(job_id, transcript, txt_filename='dialog.txt')

    def web_watch(self, role='user', **fields):
        with patch.object(watch_service, 'watch_window', return_value=(100, 200)):
            watch = watch_service.create_watch('@web-channel', 'tomorrow', '09:00', '10:00',
                                              owner_type='web', owner_id=role)
        watch.update(fields)
        store.save_watch(watch)
        return watch

    async def test_watchlist_empty_and_unassociated_web_watch_are_safe(self):
        update = self.update()
        await self.controls.watchlist(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], 'Belum ada Watch aktif.')
        self.web_watch()
        await self.controls.watchlist(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.args[0], 'Belum ada Watch aktif.')

    async def test_watchlist_shared_origins_owner_fields_states_and_cancel(self):
        telegram_watch = self.watch(mode='every', auto=True)
        web_watch = self.web_watch()
        admin_watch = self.web_watch('admin')
        foreign = dict(telegram_watch, id='foreign', user_id=8, channel='https://www.youtube.com/@private')
        store.save_watch(foreign)
        update = self.update()
        with patch.dict(os.environ, WEB_USER_TELEGRAM_USER_ID='7', WEB_USER_TELEGRAM_CHAT_ID='77'), \
                patch.object(tc.time, 'time', return_value=90):
            await self.controls.watchlist(update, self.context)
            message = update.message.reply_text.call_args.args[0]
            for value in ('@rribatam', '@web-channel', 'WIB', 'First', 'Every', 'Auto-transcribe: on', 'Waiting'):
                self.assertIn(value, message)
            for identifier in (telegram_watch['id'], web_watch['id'], admin_watch['id'], 'foreign', 'user_id', 'chat_id'):
                self.assertNotIn(identifier, message)
            self.assertEqual(len(update.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard), 2)
            await self.controls.cancelwatch_callback(self.update(user=8, callback='watchcancel:' + web_watch['id']), self.context)
            self.assertEqual(next(w for w in store.watches() if w['id'] == web_watch['id'])['status'], 'active')
            other_chat = self.update(callback='watchcancel:' + web_watch['id'])
            other_chat.effective_chat.id = 88
            await self.controls.cancelwatch_callback(other_chat, self.context)
            self.assertEqual(next(w for w in store.watches() if w['id'] == web_watch['id'])['status'], 'active')
            await self.controls.cancelwatch_callback(self.update(callback='watchcancel:' + web_watch['id']), self.context)
            self.context.args = [telegram_watch['id']]
            await self.controls.cancelwatch(update, self.context)
        watches = {w['id']: w for w in store.watches()}
        self.assertEqual(watches[web_watch['id']]['status'], 'cancelled')
        self.assertEqual(watches[telegram_watch['id']]['status'], 'cancelled')
        self.assertEqual(watches['foreign']['status'], 'active')
        self.assertEqual(self.redis.keys('stop:*'), [])

    async def test_watchlist_derived_recording_discovery_issue_recent_expired_and_admin_scope(self):
        recording = self.watch(identifier='recording-watch')
        recording.update(status='finished', last_capture=dict(job_id='recording-job'))
        store.save_watch(recording)
        self.recording('recording-job', state='recording')
        issue = self.watch(identifier='issue-watch')
        store.discovery_state(issue['id'], 'probe failure', 110)
        self.watch(identifier='expired-watch', end=120)
        self.watch(identifier='historic-watch', start=-100000, end=-90000)
        self.web_watch('user')
        self.web_watch('admin')
        with patch.dict(os.environ, WEB_ADMIN_TELEGRAM_USER_ID='7', WEB_ADMIN_TELEGRAM_CHAT_ID='77'), \
                patch.object(tc.time, 'time', return_value=150):
            update = self.update()
            await self.controls.watchlist(update, self.context)
        message = update.message.reply_text.call_args.args[0]
        for state in ('Recording', 'Discovery issue', 'Expired', 'Waiting'):
            self.assertIn(state, message)
        self.assertEqual(message.count('@web-channel'), 2)
        self.assertEqual(message.count('@rribatam'), 3)

    async def test_watch_created_cancelled_and_expired_notifications_persist(self):
        with patch.dict(os.environ, WEB_USER_TELEGRAM_USER_ID='7', WEB_USER_TELEGRAM_CHAT_ID='77'):
            watch = self.web_watch()
            await self.controls.tick(self.sender, 90)
            await tc.Controls(self.redis).tick(self.sender, 90)
            self.assertEqual(self.sender.send_message.await_count, 1)
            self.assertIn('Watch tersimpan', self.sender.send_message.call_args.args[1])
            self.assertNotIn(watch['id'], self.sender.send_message.call_args.args[1])
            store.cancel_watch(watch['id'])
            await tc.Controls(self.redis).tick(self.sender, 90)
            await tc.Controls(self.redis).tick(self.sender, 90)
            self.assertEqual(self.sender.send_message.await_count, 2)
            self.assertIn('dibatalkan', self.sender.send_message.call_args.args[1])
        self.watch(identifier='expiring')
        self.sender.send_message.side_effect = RuntimeError('blocked')
        await self.controls.tick(self.sender, 200)
        self.assertEqual(next(w for w in store.watches() if w['id'] == 'expiring')['status'], 'expired')
        self.sender.send_message.side_effect = None
        await tc.Controls(self.redis).tick(self.sender, 210)
        count = self.sender.send_message.await_count
        await tc.Controls(self.redis).tick(self.sender, 220)
        self.assertEqual(self.sender.send_message.await_count, count)
        self.assertIn('berakhir', self.sender.send_message.call_args.args[1])

    async def test_admin_cancellation_notifies_other_web_owner_destination(self):
        with patch.dict(os.environ, WEB_USER_TELEGRAM_USER_ID='8', WEB_USER_TELEGRAM_CHAT_ID='88',
                        WEB_ADMIN_TELEGRAM_USER_ID='7', WEB_ADMIN_TELEGRAM_CHAT_ID='77'):
            watch = self.web_watch(created_notice=True)
            await self.controls.cancelwatch_callback(self.update(callback='watchcancel:' + watch['id']), self.context)
            await self.controls.notify_watches(self.sender)
            self.assertEqual(self.sender.send_message.call_args.args[0], 88)
            self.assertIn('dibatalkan', self.sender.send_message.call_args.args[1])
            await tc.Controls(self.redis).notify_watches(self.sender)
            self.assertEqual(self.sender.send_message.await_count, 1)

    async def test_capture_progress_and_transcript_processing_are_quiet(self):
        self.recording(state='starting')
        store.subscribe('video-job', 77, 7, monitor_capture=True)
        await self.controls.notify_jobs(self.sender)
        self.sender.send_message.assert_not_awaited()
        for _ in range(3):
            self.recording(state='recording')
            await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.recording(transcript='transcribing')
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 2)
        self.assertTrue(all('Transkripsi' not in call.args[1] for call in self.sender.send_message.call_args_list))

    async def test_identical_discovery_errors_dedupe_across_recovery_and_restart(self):
        self.watch(mode='every')
        error = tc.subprocess.CalledProcessError(1, 'yt-dlp', stderr=b'403 https://host/?token=secret Cookie: secret')
        with patch.object(tc, 'discover_live', side_effect=error):
            for now in (110, 120, 130):
                await tc.Controls(self.redis).tick(self.sender, now)
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertNotIn('secret', self.sender.send_message.call_args.args[1])
        with patch.object(tc, 'discover_live', return_value=[]):
            await self.controls.tick(self.sender, 140)
        with patch.object(tc, 'discover_live', side_effect=error):
            await tc.Controls(self.redis).tick(self.sender, 150)
        self.assertEqual(self.sender.send_message.await_count, 1)

    async def test_mapped_web_watch_live_detection_and_capture_share_subscription(self):
        with patch.dict(os.environ, WEB_USER_TELEGRAM_USER_ID='7', WEB_USER_TELEGRAM_CHAT_ID='77'):
            watch = self.web_watch()
            with patch.object(tc, 'discover_live', return_value=[self.live()]):
                await self.controls.tick(None, 100)
            await tc.Controls(self.redis).tick(self.sender, 110)
            job = dict(store.tasks())['capture:abcdefghijk']['job']
            storage.save_recording(dict(job, filename='web.mp4', started_at=105), 'ready')
            await tc.Controls(self.redis).tick(self.sender, 120)
            notices = [call.args[1] for call in self.sender.send_message.call_args_list]
            for event in ('Watch tersimpan', 'Live terdeteksi', 'Capture dimulai', 'Capture selesai'):
                self.assertEqual(sum(event in text for text in notices), 1)
            self.assertTrue(all(call.args[0] == 77 for call in self.sender.send_message.call_args_list))
            self.assertEqual(self.redis.llen('download_queue'), 1)
            self.assertEqual(next(w for w in store.watches() if w['id'] == watch['id'])['status'], 'finished')
            await tc.Controls(self.redis).tick(self.sender, 130)
            self.assertEqual(self.sender.send_message.await_count, 4)

    async def test_web_capture_and_web_transcription_results_notify_only_associated_owner(self):
        with patch.dict(os.environ, WEB_USER_TELEGRAM_USER_ID='7', WEB_USER_TELEGRAM_CHAT_ID='77'):
            self.redis.set('worker:heartbeat', 1)
            client = web.create_app(self.redis).test_client()
            csrf = client.post('/api/login', json={'password': 'test-password'}).json['csrf']
            response = client.post('/api/record', json=dict(source='youtube', url=self.live()['url']),
                                   headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 202)
            job = json.loads(self.redis.lindex('download_queue', 0))
            storage.save_recording(dict(job, filename='web.mp4', started_at=100), 'ready')
            await self.controls.notify_jobs(self.sender)
            response = client.post('/api/recordings/' + job['job_id'] + '/transcript', json={},
                                   headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 202)
            storage.save_transcription_state(job['job_id'], 'completed')
            await tc.Controls(self.redis).notify_jobs(self.sender)
            self.assertEqual(self.sender.send_message.await_count, 3)
            self.assertIn('Transkripsi selesai', self.sender.send_message.call_args.args[1])
            await tc.Controls(self.redis).notify_jobs(self.sender)
            self.assertEqual(self.sender.send_message.await_count, 3)

    async def test_successful_start_checkpoint_survives_terminal_send_failure(self):
        storage.save_recording(dict(job_id='fast', source='youtube', filename='fast.mp4', started_at=100), 'ready')
        store.subscribe('fast', 77, 7, monitor_capture=True, auto_transcribe=True)
        self.sender.send_message.side_effect = [None, RuntimeError('blocked')]
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.redis.llen('transcription_queue'), 1)
        self.assertTrue(store.task('subscription:fast:77:7')['capture_started'])
        self.sender.send_message.side_effect = None
        await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(sum('Capture dimulai' in call.args[1] for call in self.sender.send_message.call_args_list), 1)
        self.assertTrue(store.task('subscription:fast:77:7')['capture_done'])

    async def test_duplicate_subscribers_same_chat_receive_one_notice(self):
        self.recording()
        store.subscribe('video-job', 77, 7, monitor_capture=True)
        store.subscribe('video-job', 77, 8, monitor_capture=True)
        await self.controls.notify_jobs(self.sender)
        await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 1)

    async def test_legacy_successful_checkpoints_do_not_replay_after_upgrade(self):
        self.recording()
        transcription_queue.enqueue(self.redis, 'video-job')
        storage.save_transcription_state('video-job', 'completed')
        store.subscribe('video-job', 77, 7, monitor_capture=True, monitor_transcript=True,
                        capture_done=True, transcript_notice='completed')
        for identifier, state in (('old-expired', 'expired'), ('old-cancelled', 'cancelled')):
            watch = self.watch(identifier=identifier)
            watch['status'] = state
            store.save_watch(watch)
        await tc.Controls(self.redis).notify_jobs(self.sender)
        await tc.Controls(self.redis).notify_watches(self.sender)
        self.sender.send_message.assert_not_awaited()
        self.assertEqual(store.task('subscription:video-job:77:7')['transcript_request_notice'],
                         storage.recording('video-job')['transcript']['request_id'])

    async def test_transcript_terminal_notifications_dedupe_by_canonical_request(self):
        self.recording(transcript='failed')
        storage.save_transcription_state('video-job', 'failed', error='403 https://host/?token=secret Cookie: secret')
        store.subscribe('video-job', 77, 7, monitor_transcript=True)
        await self.controls.notify_jobs(self.sender)
        await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertNotIn('secret', self.sender.send_message.call_args.args[1])
        transcription_queue.enqueue(self.redis, 'video-job')
        storage.save_transcription_state('video-job', 'transcribing')
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 1)
        storage.save_transcription_state('video-job', 'failed')
        await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 2)
        transcription_queue.enqueue(self.redis, 'video-job')
        storage.save_transcription_state('video-job', 'completed')
        with patch.dict(os.environ, STREAMFETCH_PUBLIC_URL=''):
            await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 3)
        self.assertIsNone(self.sender.send_message.call_args.kwargs['reply_markup'])
        await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 3)

    async def test_missing_or_revoked_destination_and_subscription_failure_do_not_affect_web_jobs(self):
        for configuration in ({}, {'WEB_USER_TELEGRAM_USER_ID': '99', 'WEB_USER_TELEGRAM_CHAT_ID': '77'},
                              {'WEB_USER_TELEGRAM_USER_ID': '7', 'WEB_USER_TELEGRAM_CHAT_ID': 'not-a-chat'}):
            with patch.dict(os.environ, configuration):
                store.subscribe_web('silent-job', 'user')
        self.assertEqual(store.tasks(), [])
        with patch.dict(os.environ, WEB_USER_TELEGRAM_USER_ID='7', WEB_USER_TELEGRAM_CHAT_ID='77'):
            with patch.object(store, 'subscribe', side_effect=RuntimeError('unavailable')):
                store.subscribe_web('silent-job', 'user')
            store.subscribe_web('silent-job', 'user')
        self.recording('silent-job')
        await self.controls.notify_jobs(self.sender)
        self.sender.send_message.assert_not_awaited()

    async def test_failed_telegram_sends_do_not_change_capture_or_transcription_result(self):
        self.recording(transcript='completed')
        store.subscribe('video-job', 77, 7, monitor_capture=True, monitor_transcript=True)
        self.sender.send_message.side_effect = RuntimeError('offline')
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(storage.recording('video-job')['state'], 'ready')
        self.assertEqual(storage.recording('video-job')['transcript']['status'], 'completed')
        self.sender.send_message.side_effect = None
        await tc.Controls(self.redis).notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 3)

    async def test_worker_commits_capture_failure_without_sending_progress_message(self):
        job = dict(job_id='worker-failure', source='youtube', chat_id=77, url=self.live()['url'])
        store.subscribe(job['job_id'], 77, 7, monitor_capture=True)
        with patch.object(worker, 'r', self.redis), patch.object(worker, 'DOWNLOAD_DIR', Path(self.tmp.name)), \
                patch.object(worker, 'inspect_youtube', AsyncMock(side_effect=RuntimeError('https://host/?token=secret'))), \
                patch.object(worker, 'send', AsyncMock()) as send:
            await worker.run_download(job)
        send.assert_not_awaited()
        self.assertEqual(storage.recording(job['job_id'])['state'], 'failed')
        await self.controls.notify_jobs(self.sender)
        self.assertIn('Capture gagal', self.sender.send_message.call_args.args[1])
        self.assertNotIn('secret', self.sender.send_message.call_args.args[1])

    def test_wib_today_tomorrow_date_and_boundaries(self):
        now = datetime(2026, 10, 4, 9, tzinfo=tc.WIB)
        for day, date in [('today', 4), ('tomorrow', 5), ('2026-10-06', 6)]:
            begin, end = tc.watch_window(day, '09:00', '10:00', now)
            self.assertEqual(datetime.fromtimestamp(begin, tc.WIB).day, date)
            self.assertEqual(end - begin, 3600)
        for day, start, end in [('today', '10:00', '09:00'), ('today', '08:00', '09:00'),
                                ('today', '24:00', '25:00'), ('today', '9:00', '10:00')]:
            with self.assertRaises(ValueError):
                tc.watch_window(day, start, end, now)

    def test_channel_validation_only_accepts_channel_not_arbitrary_urls(self):
        for value in ['@rribatam', 'youtube.com/@rribatam', 'https://www.youtube.com/@rribatam',
                      'https://youtube.com/@rribatam/live', 'https://www.youtube.com/@rribatam/streams']:
            self.assertEqual(tc.channel_url(value), 'https://www.youtube.com/@rribatam')
        for value in ['https://youtube.com.evil/@rribatam', 'https://youtu.be/abcdefghijk',
                      'https://youtube.com/watch?v=abcdefghijk', 'https://user@youtube.com/@rribatam']:
            with self.assertRaises(ValueError):
                tc.channel_url(value)

    def test_discovery_is_metadata_only_and_resolves_active_actual_video_urls(self):
        payload = {'entries': [{'id': 'abcdefghijk', 'live_status': 'is_live', 'url': 'untrusted'},
                               {'id': 'bbbbbbbbbbb', 'live_status': 'is_upcoming'},
                               {'id': 'ccccccccccc', 'live_status': 'was_live'}, None]}
        with patch.object(tc.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(payload))) as run:
            self.assertEqual(tc.discover_live('https://www.youtube.com/@rribatam'), [self.live()])
        self.assertIn('--skip-download', run.call_args.args[0])
        self.assertIn('/streams', run.call_args.args[0][-1])

    async def test_already_live_at_start_captures_immediately_once_and_persists_first_mode(self):
        self.watch()
        with patch.object(tc, 'discover_live', return_value=[self.live()]) as discover:
            await self.controls.tick(self.sender, 99)
            discover.assert_not_called()
            await self.controls.tick(self.sender, 100)
            await tc.Controls(self.redis).tick(self.sender, 110)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        payload = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(payload['url'], self.live()['url'])
        self.assertEqual(payload['source'], 'youtube')
        self.assertEqual(payload['origin'], 'telegram_watch')
        self.assertTrue(payload['is_live'])
        self.assertEqual(payload['compression'], 'original')
        self.assertEqual(storage.recording(payload['job_id'])['state'], 'queued')
        self.assertEqual(store.watches()[0]['status'], 'finished')
        self.assertEqual(store.watches()[0]['last_capture']['video_id'], 'abcdefghijk')
        self.assertEqual(store.watches()[0]['last_capture']['job_id'], payload['job_id'])
        self.assertIn('Live terdeteksi', self.sender.send_message.call_args.args[1])

    async def test_web_watch_without_telegram_detects_canonical_job_and_auto_transcribes(self):
        watch_service.create_watch('@rribatam', '1970-01-01', '07:01', '07:04', mode='first',
                                   auto_transcribe=True, owner_type='web', owner_id='user',
                                   now=datetime.fromtimestamp(0, tc.WIB))
        with patch.object(tc, 'discover_live', return_value=[self.live()]):
            await self.controls.tick(None, 100)
        task = next(value for key, value in store.tasks() if key == 'capture:abcdefghijk')
        job = task['job']
        self.assertEqual(job['origin'], 'web_watch')
        self.assertEqual(job['chat_id'], 'web')
        self.assertNotIn('chat_id', task)
        self.assertNotIn('user_id', task)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        self.assertEqual(storage.recording(job['job_id'])['state'], 'queued')
        self.assertEqual([key for key, _ in store.tasks() if key.startswith('subscription:')], [])
        storage.save_recording(dict(job, filename='web-watch.mp4'), 'ready')
        await self.controls.tick(self.sender, 110)
        self.assertEqual(storage.recording(job['job_id'])['transcript']['status'], 'queued')
        updated = dict(store.tasks())['capture:abcdefghijk']
        self.assertTrue(updated['transcript_requested'])
        self.sender.send_message.assert_not_awaited()

    def test_discovery_without_streams_tab_falls_back_to_live_metadata_only(self):
        missing = tc.subprocess.CalledProcessError(1, 'yt-dlp', stderr=b'This channel does not have a streams tab')
        with patch.object(tc.subprocess, 'run', side_effect=[missing, SimpleNamespace(stdout=json.dumps(
                dict(id='abcdefghijk', is_live=True, live_status='is_live', media_type='livestream')))]) as run:
            self.assertEqual(tc.discover_live('https://www.youtube.com/@rribatam'), [self.live()])
        self.assertTrue(run.call_args.args[0][-1].endswith('/live'))
        self.assertIn('--skip-download', run.call_args.args[0])
        self.assertNotIn('--flat-playlist', run.call_args.args[0])

    def test_discovery_fallback_rejects_upcoming_past_premiere_upload_and_normal_no_live(self):
        for metadata in [dict(live_status='is_upcoming'), dict(live_status='was_live'),
                         dict(live_status='post_live'), dict(live_status='not_live'),
                         dict(is_live=False, live_status='is_live'),
                         dict(is_live=True, is_premiere=True), dict(is_live=True, media_type='video'),
                         dict(is_live=True, is_upcoming=True), dict()]:
            missing = tc.subprocess.CalledProcessError(1, 'yt-dlp', stderr=b'This channel does not have a streams tab')
            with self.subTest(metadata=metadata), patch.object(tc.subprocess, 'run', side_effect=[
                    missing, SimpleNamespace(stdout=json.dumps(dict(id='abcdefghijk', **metadata)))]):
                self.assertEqual(tc.discover_live('https://www.youtube.com/@rribatam'), [])
        with patch.object(tc.subprocess, 'run', side_effect=[missing,
                tc.subprocess.CalledProcessError(1, 'yt-dlp', stderr=b'The channel is not currently live')]):
            self.assertEqual(tc.discover_live('https://www.youtube.com/@rribatam'), [])

    async def test_real_discovery_failure_is_sanitized_persisted_and_cleared_on_no_live(self):
        self.watch('every')
        error = tc.subprocess.CalledProcessError(1, ['yt-dlp', 'https://host/?token=secret'],
                                               stderr=b'HTTP Error 403: https://host/?token=secret token=secret\nAuthorization: Bearer secret\nCookie: session=secret')
        with patch.object(tc, 'discover_live', side_effect=error), self.assertLogs(tc.log, level='WARNING') as logs:
            await self.controls.tick(self.sender, 110)
        self.assertNotIn('secret', ''.join(logs.output))
        self.assertIn('403', store.watches()[0]['discovery_error'])
        self.assertEqual(self.redis.llen('download_queue'), 0)
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertIn('discovery issue', self.sender.send_message.call_args.args[1])
        self.assertNotIn('secret', self.sender.send_message.call_args.args[1])
        with patch.object(tc, 'discover_live', return_value=[]):
            await self.controls.tick(self.sender, 120)
        self.assertIsNone(store.watches()[0]['discovery_error'])

    async def test_dispatch_checkpoints_before_queue_and_retries_admission_without_overwriting_worker(self):
        self.watch()
        admit = self.redis.eval
        def observe(script, count, job_id, payload):
            self.assertEqual(storage.recording(job_id)['state'], 'queued')
            return admit(script, count, job_id, payload)
        with patch.object(tc, 'discover_live', return_value=[self.live()]), patch.object(self.redis, 'eval', side_effect=observe):
            await self.controls.tick(self.sender, 100)
        task_key, task = next((key, value) for key, value in store.tasks() if key.startswith('capture:'))
        task['dispatch'] = 'pending'
        store.save_task(task_key, task)
        storage.save_recording(task['job'], 'recording')
        await self.controls.dispatch_task(self.sender, task_key, task)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        self.assertEqual(storage.recording(task['job']['job_id'])['state'], 'recording')

    async def test_every_mode_discovers_later_live_but_deduplicates_ids_after_restart(self):
        self.watch('every')
        with patch.object(tc, 'discover_live', side_effect=[[], [self.live()], [self.live(), self.live('bbbbbbbbbbb')]]):
            for now in [100, 110, 120]:
                await tc.Controls(self.redis).tick(self.sender, now)
        self.assertEqual(self.redis.llen('download_queue'), 2)
        self.assertEqual(store.watches()[0]['status'], 'active')
        self.assertEqual(store.watches()[0]['last_capture']['video_id'], 'bbbbbbbbbbb')

    async def test_shared_admin_cancellation_during_discovery_blocks_claim_after_restart(self):
        self.watch(mode='every')
        def discover(channel):
            store.cancel_watch('watch1')
            return [self.live()]
        with patch.object(tc, 'discover_live', side_effect=discover) as discovery:
            await self.controls.tick(self.sender, 150)
            await tc.Controls(self.redis).tick(self.sender, 160)
        self.assertEqual(discovery.call_count, 1)
        self.assertEqual(self.redis.llen('download_queue'), 0)
        self.assertEqual(store.watches()[0]['status'], 'cancelled')
        self.assertEqual(self.redis.keys('stop:*'), [])

    async def test_shared_transcription_cancellation_notifies_once_and_can_restart(self):
        self.recording(transcript='cancelled')
        store.subscribe('video-job', 77, 7, monitor_transcript=True)
        await self.controls.notify_jobs(self.sender)
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertIn('dibatalkan', self.sender.send_message.call_args.args[1])
        status, created = transcription_queue.enqueue(self.redis, 'video-job')
        self.assertTrue(created)
        self.assertEqual(status, 'queued')

    async def test_expiry_stops_discovery_not_capture_and_notifies_once(self):
        self.watch('every')
        self.redis.set('capture:owner', 'existing-job')
        with patch.object(tc, 'discover_live') as discover:
            await self.controls.tick(self.sender, 200)
            await self.controls.tick(self.sender, 210)
        discover.assert_not_called()
        self.assertEqual(self.redis.get('capture:owner'), 'existing-job')
        self.assertEqual(self.redis.keys('stop:*'), [])
        self.assertEqual(self.sender.send_message.await_count, 1)
        self.assertEqual(store.watches()[0]['status'], 'expired')

    async def test_persisted_intent_dispatches_after_restart_without_duplicate_queue(self):
        watch = self.watch()
        task = dict(job=dict(job_id='intent-job', chat_id=77, source='youtube', url=self.live()['url']),
                    user_id=7, chat_id=77, auto_transcribe=False, dispatch='pending')
        self.assertTrue(store.claim_video(watch['id'], self.live()['id'], task))
        self.assertFalse(store.claim_video(watch['id'], self.live()['id'], task))
        for now in [300, 310]:
            await tc.Controls(self.redis).tick(self.sender, now)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        self.assertTrue(any(key.startswith('subscription:') for key, value in store.tasks()))

    async def test_discovery_timeout_is_retryable_and_window_end_during_probe_is_not_captured(self):
        self.watch('every')
        with patch.object(tc, 'discover_live', side_effect=tc.subprocess.TimeoutExpired('yt-dlp', 25)):
            await self.controls.tick(self.sender, 110)
        self.assertEqual(store.watches()[0]['status'], 'active')
        with patch.object(tc, 'discover_live', return_value=[self.live()]), \
                patch.object(tc.time, 'monotonic', side_effect=[0, 100]):
            await self.controls.tick(self.sender, 150)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    async def test_auto_transcribe_after_capture_and_completion_links_existing_reader(self):
        self.recording()
        store.subscribe('video-job', 77, 7, monitor_capture=True, auto_transcribe=True)
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.redis.llen('transcription_queue'), 1)
        queued = json.loads(self.redis.lindex('transcription_queue', 0))
        self.assertEqual(queued['job_id'], 'video-job')
        self.assertEqual(queued['request_id'], storage.recording('video-job')['transcript']['request_id'])
        storage.save_transcription_state('video-job', 'transcribing')
        await self.controls.notify_jobs(self.sender)
        storage.save_transcription_state('video-job', 'completed')
        await tc.Controls(self.redis).notify_jobs(self.sender)
        message = self.sender.send_message.call_args
        self.assertEqual(message.args[1], 'Transkripsi selesai.')
        self.assertEqual(message.kwargs['reply_markup'].inline_keyboard[0][0].url,
                         'https://stream.example/api/recordings/video-job/transcript/view')

    async def test_compression_failure_notification_reports_capture_success_and_original(self):
        storage.save_recording(dict(job_id='video-job', source='youtube', filename='original.mp4',
                                    processing_detail='Rekaman berhasil · kompresi gagal · Original tersedia.'), 'ready')
        store.subscribe('video-job', 77, 7, monitor_capture=True)
        await self.controls.notify_jobs(self.sender)
        message = self.sender.send_message.call_args.args[1]
        self.assertIn('Capture selesai', message)
        self.assertIn('kompresi gagal', message)
        self.assertIn('Original', message)
        before = self.sender.send_message.await_count
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.await_count, before)

    async def test_capture_started_completed_failed_and_transcribe_action(self):
        self.recording(state='recording')
        store.subscribe('video-job', 77, 7, monitor_capture=True)
        await self.controls.notify_jobs(self.sender)
        self.assertIn('Capture dimulai', self.sender.send_message.call_args.args[1])
        self.recording()
        await self.controls.notify_jobs(self.sender)
        self.assertEqual(self.sender.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data,
                         'transcribe:video-job')
        self.recording('failed-job', state='failed')
        store.subscribe('failed-job', 77, 7, monitor_capture=True, auto_transcribe=True)
        await self.controls.notify_jobs(self.sender)
        self.assertIn('Capture gagal', self.sender.send_message.call_args.args[1])
        self.assertEqual(self.redis.llen('transcription_queue'), 0)

    async def test_transcribe_prioritizes_untranscribed_and_callback_does_not_duplicate(self):
        self.recording('fresh')
        self.recording('old', transcript='completed')
        update = self.update()
        await self.controls.transcribe(update, self.context)
        buttons = update.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, 'transcribe:fresh')
        update = self.update(callback='transcribe:fresh')
        await self.controls.callback(update, self.context)
        await self.controls.callback(update, self.context)
        self.assertEqual(self.redis.llen('transcription_queue'), 1)
        self.assertIn('sedang diproses', update.message.reply_text.call_args.args[0])
        update = self.update(callback='transcribe:old')
        await self.controls.callback(update, self.context)
        self.assertEqual(update.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].text, 'View Transcript')

    async def test_invalid_callback_and_missing_recording_do_not_enqueue(self):
        for data in ['transcribe:../private', 'transcribe:missing']:
            await self.controls.callback(self.update(callback=data), self.context)
        self.assertEqual(self.redis.llen('transcription_queue'), 0)
        self.assertEqual(store.tasks(), [])

    async def test_failed_transcription_notification_and_retry(self):
        self.recording(transcript='failed')
        store.subscribe('video-job', 77, 7, monitor_transcript=True)
        await self.controls.notify_jobs(self.sender)
        self.assertIn('Transkripsi gagal', self.sender.send_message.call_args.args[1])
        await self.controls.callback(self.update(callback='transcribe:video-job'), self.context)
        self.assertEqual(storage.recording('video-job')['transcript']['status'], 'queued')

    async def test_authorization_for_every_handler_and_callback_and_revoked_watches(self):
        handlers = [bot.start, bot.stop, bot.record, bot.handle_url, self.controls.watch,
                    self.controls.watchlist, self.controls.cancelwatch, self.controls.transcribe]
        for handler in handlers:
            update = self.update(user=99)
            await handler(update, self.context)
            self.assertEqual(update.message.reply_text.call_args.args[0], 'Tidak diizinkan.')
        update = self.update(user=99, callback='transcribe:video-job')
        await self.controls.callback(update, self.context)
        update.callback_query.answer.assert_awaited_once_with('Tidak diizinkan.', show_alert=True)
        update = self.update(user=99, callback='watch:12345678:confirm')
        await self.controls.watch_callback(update, self.context)
        update.callback_query.answer.assert_awaited_once_with('Tidak diizinkan.', show_alert=True)
        self.watch()
        with patch.dict(os.environ, TELEGRAM_ALLOWED_USER_IDS=''), patch.object(tc, 'discover_live') as discover:
            await self.controls.tick(self.sender, 110)
        discover.assert_not_called()
        self.assertEqual(self.redis.llen('download_queue'), 0)

    async def test_watch_wizard_default_first_list_and_owner_only_cancel(self):
        update = self.update()
        await self.controls.watch(update, self.context)
        for answer in ['@rribatam', 'tomorrow', '9.00 - 10.00 WIB', 'no', 'confirm']:
            update.message.text = answer
            self.assertTrue(await self.controls.watch_reply(update, self.context))
        watch = store.watches()[0]
        self.assertEqual(watch['mode'], 'first')
        self.assertFalse(watch['auto_transcribe'])
        await self.controls.watchlist(update, self.context)
        self.assertNotIn(watch['id'], update.message.reply_text.call_args.args[0])
        self.assertIn('Waiting', update.message.reply_text.call_args.args[0])
        self.assertEqual(update.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data,
                         'watchcancel:' + watch['id'])
        self.context.args = [watch['id']]
        await self.controls.cancelwatch(self.update(user=8), self.context)
        self.assertEqual(store.watches()[0]['status'], 'active')
        await self.controls.cancelwatch(update, self.context)
        self.assertEqual(store.watches()[0]['status'], 'cancelled')
        self.assertEqual(self.redis.keys('stop:*'), [])

    def test_flexible_window_and_compact_command_parsing(self):
        for value in ['08:00-10:00', '8:00 - 10:00', '08.00-10.00', '8.00 - 10.00 WIB']:
            self.assertEqual(tc.parse_window(value), ('08:00', '10:00'))
        self.assertEqual(tc.parse_window('21:22 - 23:00 wib'), ('21:22', '23:00'))
        for value in ['24:00-25:00', '8:60-10:00', '08:00', '08:00-10:00 UTC', '8:0-10:00']:
            with self.assertRaises(ValueError):
                tc.parse_window(value)
        for command in ['@rribatam today 08:00-10:00', 'youtube.com/@rribatam tomorrow 08.00-10.00',
                        '@rribatam tomorrow 8:00 - 10:00 WIB']:
            values = tc.watch_arguments(command.split())
            self.assertEqual(values[2:], ['08:00', '10:00', 'first', 'no'])
        self.assertEqual(tc.watch_arguments('@rribatam tomorrow 08:00-10:00 every yes'.split())[4:], ['every', 'yes'])
        self.assertEqual(tc.watch_arguments('@rribatam tomorrow 08:00 10:00 first yes'.split())[2:],
                         ['08:00', '10:00', 'first', 'yes'])

    async def test_one_line_watch_uses_existing_creation_and_clears_draft_only_on_success(self):
        await self.controls.watch(self.update(), self.context)
        self.context.args = '@rribatam tomorrow bad-window'.split()
        await self.controls.watch(self.update(), self.context)
        self.assertIn('watch_draft', self.context.user_data)
        self.assertEqual(store.watches(), [])
        for command in ['@rribatam today 08:00-10:00', 'https://youtube.com/@rribatam tomorrow 08.00-10.00']:
            self.context.args = command.split()
            with patch.object(watch_service, 'watch_window', return_value=(100, 200)):
                await self.controls.watch(self.update(), self.context)
        self.assertNotIn('watch_draft', self.context.user_data)
        self.assertEqual(len(store.watches()), 2)
        for watch in store.watches():
            self.assertEqual(watch['channel'], 'https://www.youtube.com/@rribatam')
            self.assertEqual(watch['mode'], 'first')
            self.assertFalse(watch['auto_transcribe'])

    async def test_watch_owns_text_including_invalid_source_urls_and_other_chats(self):
        await self.controls.watch(self.update(), self.context)
        with patch.object(bot, 'controls', self.controls), patch.object(bot, 'r', self.redis):
            await bot.handle_url(self.update('https://youtube.com/watch?v=abcdefghijk'), self.context)
            self.assertEqual(self.context.user_data['watch_draft']['step'], 'channel')
            await bot.handle_url(self.update('youtube.com/@rribatam'), self.context)
            self.assertEqual(self.context.user_data['watch_draft']['step'], 'date')
            await bot.handle_url(self.update('https://youtube.com/watch?v=abcdefghijk'), self.context)
            self.assertEqual(self.context.user_data['watch_draft']['step'], 'date')
            other_chat = self.update('https://youtube.com/watch?v=abcdefghijk')
            other_chat.effective_chat.id = 88
            await bot.handle_url(other_chat, self.context)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    async def test_watch_buttons_confirmation_and_invalid_input_preserve_step(self):
        update = self.update()
        await self.controls.watch(update, self.context)
        token = self.context.user_data['watch_draft']['token']
        await self.controls.watch_reply(self.update('@rribatam'), self.context)
        async def click(action):
            await self.controls.watch_callback(self.update(callback='watch:' + token + ':' + action), self.context)
        await click('date:choose')
        self.assertEqual(self.context.user_data['watch_draft']['step'], 'date')
        await self.controls.watch_reply(self.update('bad date'), self.context)
        self.assertEqual(self.context.user_data['watch_draft']['step'], 'date')
        await click('date:tomorrow')
        for bad in ['bad window', '10:00-09:00', '24:00-25:00']:
            await self.controls.watch_reply(self.update(bad), self.context)
            self.assertEqual(self.context.user_data['watch_draft']['step'], 'window')
        await self.controls.watch_reply(self.update('8:00 - 10:00 WIB'), self.context)
        await click('date:today')  # Stale date button cannot change the current step.
        self.assertEqual(self.context.user_data['watch_draft']['step'], 'auto')
        await self.controls.watch_reply(self.update('maybe'), self.context)
        self.assertEqual(self.context.user_data['watch_draft']['step'], 'auto')
        await click('auto:yes')
        self.assertEqual(store.watches(), [])
        await click('confirm')
        self.assertTrue(store.watches()[0]['auto_transcribe'])
        await click('confirm')  # Repeated confirmation cannot create another watch.
        self.assertEqual(len(store.watches()), 1)

    async def test_watch_cancel_and_stale_or_foreign_buttons(self):
        await self.controls.watch(self.update(), self.context)
        token = self.context.user_data['watch_draft']['token']
        other_context = SimpleNamespace(args=[], user_data={})
        await self.controls.watch_callback(self.update(user=8, callback='watch:' + token + ':cancel'), other_context)
        self.assertIn('watch_draft', self.context.user_data)
        await self.controls.watch_callback(self.update(callback='watch:00000000:cancel'), self.context)
        self.assertIn('watch_draft', self.context.user_data)
        await self.controls.watch_callback(self.update(callback='watch:' + token + ':cancel'), self.context)
        self.assertFalse(await self.controls.watch_reply(self.update('@rribatam'), self.context))
        self.assertEqual(store.watches(), [])

    def test_web_and_bot_share_admission_and_public_url_validation(self):
        self.recording()
        self.assertEqual(transcription_queue.enqueue(self.redis, 'video-job'), ('queued', True))
        self.assertEqual(transcription_queue.enqueue(self.redis, 'video-job'), ('queued', False))
        with patch.dict(os.environ, STREAMFETCH_PUBLIC_URL='https://token@example.com'):
            with self.assertRaises(ValueError):
                tc.reader_url('video-job')

    async def test_pending_detection_notification_retries_after_send_failure(self):
        self.watch()
        self.sender.send_message.side_effect = RuntimeError('network')
        with patch.object(tc, 'discover_live', return_value=[self.live()]):
            await self.controls.tick(self.sender, 100)
        self.sender.send_message.side_effect = None
        await self.controls.tick(self.sender, 110)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        self.assertIn('Live terdeteksi', self.sender.send_message.call_args.args[1])

    async def test_cancel_during_discovery_prevents_dispatch(self):
        watch = self.watch()
        def discover(channel):
            watch['status'] = 'cancelled'
            store.save_watch(watch)
            return [self.live()]
        with patch.object(tc, 'discover_live', side_effect=discover):
            await self.controls.tick(self.sender, 110)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    async def test_blocked_chat_does_not_block_other_subscriptions(self):
        self.recording('blocked')
        self.recording('good')
        store.subscribe('blocked', 99, 7, monitor_capture=True)
        store.subscribe('good', 77, 7, monitor_capture=True)
        async def send(chat, text, **kwargs):
            if chat == 99:
                raise RuntimeError('blocked')
        self.sender.send_message.side_effect = send
        await self.controls.notify_jobs(self.sender)
        good = next(value for key, value in store.tasks() if value['job_id'] == 'good')
        self.assertTrue(good['capture_done'])

    def test_command_defaults_and_link_config_required_for_auto_transcribe(self):
        with patch.object(watch_service, 'watch_window', return_value=(100, 200)):
            watch = self.controls.create_watch(['https://youtube.com/@rribatam', 'today', '09:00', '10:00'], 7, 77)
            self.assertEqual(watch['mode'], 'first')
            self.assertFalse(watch['auto_transcribe'])
            with patch.dict(os.environ, STREAMFETCH_PUBLIC_URL=''):
                with self.assertRaises(ValueError):
                    self.controls.create_watch(['https://youtube.com/@rribatam', 'today', '09:00', '10:00', 'first', 'yes'], 7, 77)
        self.assertEqual(len(store.watches()), 1)

    async def test_transcription_redis_outage_keeps_durable_queued_request(self):
        self.recording()
        with patch.object(self.redis, 'eval', side_effect=RuntimeError('offline')):
            await self.controls.callback(self.update(callback='transcribe:video-job'), self.context)
        self.assertEqual(storage.recording('video-job')['transcript']['status'], 'queued')
        subscriptions = [value for key, value in store.tasks() if key.startswith('subscription:')]
        self.assertTrue(subscriptions[0]['monitor_transcript'])

    async def test_poll_task_lifecycle_stops_cleanly_without_jobqueue_dependency(self):
        application = SimpleNamespace(bot=self.sender, bot_data={})
        with patch.object(self.controls, 'tick', AsyncMock()) as tick:
            await self.controls.post_init(application)
            await asyncio.sleep(0)
            tick.assert_awaited_once()
            await self.controls.post_stop(application)
        self.assertTrue(application.bot_data['watch_task'].cancelled())
