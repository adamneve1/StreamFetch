import json
import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fakeredis

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456:TEST_TOKEN')
from app import bot, storage, telegram_controls as tc, telegram_store as store, transcription_queue


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, DATA_DIR=self.tmp.name,
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
        self.assertEqual(store.watches()[0]['status'], 'finished')
        self.assertIn('Live terdeteksi', self.sender.send_message.call_args.args[1])

    async def test_every_mode_discovers_later_live_but_deduplicates_ids_after_restart(self):
        self.watch('every')
        with patch.object(tc, 'discover_live', side_effect=[[], [self.live()], [self.live(), self.live('bbbbbbbbbbb')]]):
            for now in [100, 110, 120]:
                await tc.Controls(self.redis).tick(self.sender, now)
        self.assertEqual(self.redis.llen('download_queue'), 2)
        self.assertEqual(store.watches()[0]['status'], 'active')

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
        self.assertEqual(json.loads(self.redis.lindex('transcription_queue', 0)), {'job_id': 'video-job'})
        storage.save_transcription_state('video-job', 'transcribing')
        await self.controls.notify_jobs(self.sender)
        storage.save_transcription_state('video-job', 'completed')
        await tc.Controls(self.redis).notify_jobs(self.sender)
        message = self.sender.send_message.call_args
        self.assertEqual(message.args[1], 'Transkripsi selesai.')
        self.assertEqual(message.kwargs['reply_markup'].inline_keyboard[0][0].url,
                         'https://stream.example/api/recordings/video-job/transcript/view')
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
        self.assertIn(watch['id'], update.message.reply_text.call_args.args[0])
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
            with patch.object(tc, 'watch_window', return_value=(100, 200)):
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
        with patch.object(tc, 'watch_window', return_value=(100, 200)):
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
