"""Run with: python -m unittest discover -s tests -v.

Requires the application's redis/telegram packages and fakeredis[lua].
Media tests also require ffmpeg and ffprobe on PATH.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456:TEST_TOKEN')

import fakeredis
from app import bot, quality, worker, storage, transcription_queue


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.patches = [patch.object(worker, 'r', self.redis),
                        patch.dict(os.environ, DATA_DIR=self.tmp.name, TELEGRAM_ALLOWED_USER_IDS='123'),
                        patch.object(bot, 'r', self.redis),
                        patch.object(worker, 'DOWNLOAD_DIR', self.root),
                        patch.object(worker, 'send', AsyncMock(return_value=SimpleNamespace(message_id=1))),
                        patch.object(worker, 'edit', AsyncMock())]
        for item in self.patches:
            item.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def job(self, source='oryx'):
        job = dict(job_id='test-job', chat_id=123, source=source,
                   requested_at=time.time(), url='https://www.tiktok.com/@tester/live' if source == 'tiktok' else 'https://youtube.com/watch?v=test')
        self.redis.set('capture:owner', job['job_id'], ex=30)
        self.redis.set('active:123', job['job_id'], ex=30)
        return job

    def update(self, chat=123):
        message = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(effective_chat=SimpleNamespace(id=chat),
                               effective_user=SimpleNamespace(id=123), callback_query=None,
                               effective_message=message, message=message)

    def media(self, path):
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=25', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000', '-t', '1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-f', 'mpegts', str(path),
        ], check=True, capture_output=True, timeout=30)

    def valid_info(self):
        return dict(container='mov,mp4,m4a,3gp,3g2,mj2', video_codec='h264',
                    audio_codec='aac', pix_fmt='yuv420p', duration=600,
                    video_duration=600, audio_duration=600)

    async def test_processing_failures_preserve_valid_original_as_ready_not_incomplete_media(self):
        for reason in ('timeout', 'cancelled', 'disk_space', 'ffmpeg_failed'):
            with self.subTest(reason=reason):
                job = self.job()
                job.update(compression='balanced', download_exit_code=0)
                source = self.root / 'test-job-valid.mp4'
                source.write_bytes(b'validated original capture')
                def fail(*args):
                    checkpoint = storage.recording(job['job_id'])
                    self.assertEqual(checkpoint['state'], 'finalizing')
                    self.assertEqual((self.root / checkpoint['original_filename']).read_bytes(), b'validated original capture')
                    raise worker.ProcessingError(reason)
                with patch.object(worker, 'probe_media_file', return_value=self.valid_info()), \
                     patch.object(worker, 'capture', AsyncMock(return_value='completed')), \
                     patch.object(worker, 'run_processing', side_effect=fail):
                    await worker.run_download(job)
                row = storage.recording(job['job_id'])
                self.assertEqual(row['state'], 'ready')
                self.assertEqual(row['compression'], 'original')
                self.assertEqual(row['requested_compression'], 'balanced')
                self.assertEqual(row['processing_error'], reason)
                self.assertEqual(row['filename'], row['original_filename'])
                self.assertEqual((self.root / row['filename']).read_bytes(), b'validated original capture')
                self.assertIn('Rekaman berhasil', row['detail'])
                self.assertNotIn('incomplete_media', row['detail'])
                self.assertIsNone(self.redis.get('capture:owner'))

    def test_processing_disk_guard_does_not_start_ffmpeg_or_destroy_original(self):
        job = self.job()
        job['compression'] = 'compact'
        source = self.root / 'test-job-valid.mp4'
        source.write_bytes(b'original')
        with patch.object(worker, 'probe_media_file', return_value=self.valid_info()), \
             patch.object(storage, 'disk_status', return_value=dict(available=True, free=1, minimum=10)), \
             patch.object(worker.subprocess, 'Popen') as popen:
            final = worker.complete_recording(job)
        popen.assert_not_called()
        self.assertEqual(job['processing_error'], 'disk_space')
        self.assertEqual(final.read_bytes(), b'original')

    def test_original_default_keeps_compatible_capture_bytes_without_encoding(self):
        job = self.job();path = self.root / 'test-job-capture.mp4';path.write_bytes(b'original')
        with patch.object(worker, 'probe_media_file', return_value=self.valid_info()), patch.object(worker.subprocess, 'Popen') as popen:
            final = worker.complete_recording(job)
        popen.assert_not_called()
        self.assertEqual(final.read_bytes(), b'original')
        self.assertEqual(final.name, job['original_filename'])
        self.assertEqual(job['requested_compression'], 'original')

    async def test_running_processing_stop_kills_process_and_keeps_media(self):
        job = self.job()
        path = self.root / 'original.mp4';path.write_bytes(b'original')
        command = [sys.executable, '-c', 'import time; print("out_time_us=50000000", flush=True); time.sleep(10)']
        started = time.monotonic()
        with patch.object(storage, 'disk_status', return_value=dict(available=True, free=10**10, minimum=0, can_record=True)):
            task = asyncio.create_task(asyncio.to_thread(worker.run_processing, command, path, self.valid_info(), job))
            await asyncio.sleep(.3)
            self.redis.set('stop:test-job', '1')
            with self.assertRaises(worker.ProcessingError) as caught:
                await asyncio.wait_for(task, 3)
        self.assertEqual(caught.exception.code, 'cancelled')
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(path.read_bytes(), b'original')

    def test_processing_timeout_is_duration_aware_capped_and_interrupts_execution(self):
        with patch.object(worker, 'FINALIZE_TIMEOUT', 600), patch.object(worker, 'FINALIZE_DURATION_MULTIPLIER', 4), \
             patch.object(worker, 'FINALIZE_TIMEOUT_CAP', 21600):
            self.assertEqual(worker.processing_timeout({}), 600)
            self.assertEqual(worker.processing_timeout(dict(duration=3600)), 15000)
            self.assertEqual(worker.processing_timeout(dict(duration=20000)), 21600)
        job = self.job();path = self.root / 'original.mp4';path.write_bytes(b'original')
        with patch.object(worker, 'processing_timeout', return_value=.1), \
             patch.object(storage, 'disk_status', return_value=dict(available=True, free=10**10, minimum=0, can_record=True)):
            with self.assertRaises(worker.ProcessingError) as caught:
                worker.run_processing([sys.executable, '-c', 'import time; time.sleep(10)'], path, {}, job)
        self.assertEqual(caught.exception.code, 'timeout')
        self.assertEqual(path.read_bytes(), b'original')

    def test_ffmpeg_progress_updates_shared_job_and_unknown_duration_is_indeterminate(self):
        for duration in (100, None):
            job = self.job();job.update(progress_percent=None, progress_phase='processing')
            path = self.root / 'original.mp4';path.write_bytes(b'original')
            command = [sys.executable, '-c', 'import time; print("out_time_us=50000000", flush=True); time.sleep(.3)']
            with patch.object(storage, 'disk_status', return_value=dict(available=True, free=10**10, minimum=0, can_record=True)):
                worker.run_processing(command, path, dict(duration=duration), job)
            checkpoint = storage.recording('test-job')
            self.assertEqual(checkpoint['state'], 'finalizing')
            self.assertEqual(checkpoint['progress_percent'], 50 if duration else None)

    def test_invalid_processed_output_and_stop_before_publish_retain_original_and_clean_temp(self):
        for reason in ('invalid', 'cancelled'):
            job = self.job();job.update(compression='balanced')
            source = self.root / 'test-job-valid.mp4';source.write_bytes(b'original')
            def encode(command, path, info, payload):
                Path(command[-1]).write_bytes(b'processed')
                if reason == 'cancelled':
                    self.redis.set('stop:test-job', '1')
            def probe(path):
                info = self.valid_info()
                if reason == 'invalid' and '.finalize_tmp.' in path.name:
                    info['audio_duration'] = 10
                return info
            with patch.object(worker, 'probe_media_file', side_effect=probe), patch.object(worker, 'run_processing', side_effect=encode):
                final = worker.complete_recording(job)
            self.assertEqual(final.read_bytes(), b'original')
            self.assertEqual(job['processing_error'], 'validation_failed' if reason == 'invalid' else 'cancelled')
            self.assertFalse(list(self.root.glob('*.finalize_tmp.mp4')))
            self.redis.delete('stop:test-job')

    def test_restart_recovers_checkpoint_and_legacy_processing_without_deleting_other_files(self):
        for legacy in (False, True):
            job = self.job();job.update(compression='balanced')
            path = self.root / ('test-job-capture.mp4' if legacy else 'original.mp4')
            path.write_bytes(b'original')
            if not legacy:
                job.update(original_filename=path.name, original_size=8, filename=path.name)
            storage.save_recording(job, 'finalizing')
            temp = path.with_name(path.stem + '.finalize_tmp.mp4');temp.write_bytes(b'partial')
            unrelated = self.root / 'other.finalize_tmp.mp4';unrelated.write_bytes(b'keep')
            self.redis.set('stop:test-job', '1')
            with patch.object(worker, 'probe_media_file', return_value=self.valid_info()):
                worker.recover_processing()
            row = storage.recording('test-job')
            self.assertEqual(row['state'], 'ready')
            self.assertEqual(row['processing_error'], 'interrupted')
            self.assertEqual((self.root / row['filename']).read_bytes(), b'original')
            self.assertFalse(temp.exists())
            self.assertTrue(unrelated.exists())
            self.assertIsNone(self.redis.get('capture:owner'))
            self.assertIsNone(self.redis.get('active:123'))
            self.assertIsNone(self.redis.get('stop:test-job'))

    def test_original_rename_is_recoverable_before_progress_publication(self):
        job = self.job();job['compression'] = 'balanced'
        path = self.root / 'test-job-capture.mp4';path.write_bytes(b'original')
        with patch.object(worker, 'processing_checkpoint', side_effect=RuntimeError('interrupted before Redis publication')):
            with self.assertRaises(RuntimeError):
                worker.preserve_original(job, path, self.valid_info())
        checkpoint = storage.recording('test-job')
        self.assertEqual(checkpoint['state'], 'finalizing')
        self.assertEqual((self.root / checkpoint['original_filename']).read_bytes(), b'original')
        with patch.object(worker, 'probe_media_file', return_value=self.valid_info()):
            worker.recover_processing()
        self.assertEqual(storage.recording('test-job')['state'], 'ready')

    def test_processing_exception_cleans_partial_output_without_touching_original(self):
        job = self.job();job['compression'] = 'balanced'
        path = self.root / 'test-job-capture.mp4';path.write_bytes(b'original')
        def fail(command, *args):
            Path(command[-1]).write_bytes(b'partial')
            raise RuntimeError('interrupted')
        with patch.object(worker, 'probe_media_file', return_value=self.valid_info()), patch.object(worker, 'run_processing', side_effect=fail):
            final = worker.complete_recording(job)
        self.assertEqual(final.read_bytes(), b'original')
        self.assertFalse(list(self.root.glob('*.finalize_tmp.mp4')))
        self.assertEqual(job['processing_error'], 'processing_failed')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_balanced_success_publishes_validated_output_atomically_preserving_original(self):
        job = self.job();job['compression'] = 'balanced'
        path = self.root / 'test-job-capture.ts';self.media(path)
        original_bytes = path.read_bytes()
        final = worker.complete_recording(job)
        self.assertTrue(worker.compatible_media(worker.probe_media_file(final), 'balanced'))
        self.assertEqual((self.root / job['original_filename']).read_bytes(), original_bytes)
        self.assertNotEqual(final.name, job['original_filename'])
        self.assertEqual(job['processing_status'], 'completed')
        self.assertFalse(list(self.root.glob('*.finalize_tmp.mp4')))

    async def test_telegram_stop_during_processing_uses_shared_cancellation(self):
        self.job();self.redis.set('state:test-job', 'finalizing')
        update = self.update()
        await bot.stop(update, None)
        self.assertEqual(self.redis.get('stop:test-job'), '1')
        self.assertIn('Original', update.message.reply_text.call_args.args[0])

    async def test_duplicate_record_rejected_atomically(self):
        self.redis.set('worker:heartbeat', '1', ex=15)
        with patch.object(bot, 'ORYX_STREAM_URL', 'http://oryx/live/feed.flv'):
            first, second = self.update(), self.update()
            await bot.record(first, None)
            await bot.record(second, None)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        self.assertIn('masih punya proses aktif', second.message.reply_text.call_args.args[0])
        payload = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual(payload['source'], 'oryx')
        self.assertNotIn('url', payload)
        self.assertEqual(json.loads(self.redis.eval(worker.CLAIM_JOB, 0)), payload)

    async def test_record_rejected_when_worker_busy_queued_or_offline(self):
        with patch.object(bot, 'ORYX_STREAM_URL', 'http://oryx/live/feed.flv'):
            for mode in ('offline', 'busy', 'queued'):
                self.redis.flushall()
                if mode != 'offline':
                    self.redis.set('worker:heartbeat', '1')
                if mode == 'busy':
                    self.redis.set('capture:owner', 'someone-else')
                if mode == 'queued':
                    self.redis.rpush('download_queue', '{}')
                before = self.redis.llen('download_queue')
                update = self.update()
                await bot.record(update, None)
                self.assertEqual(before, self.redis.llen('download_queue'))
                self.assertNotIn('Starting', update.message.reply_text.call_args.args[0])

    async def test_stop_enters_shared_finalization_for_both_sources(self):
        for source in ('oryx', 'youtube', 'tiktok'):
            with self.subTest(source=source):
                job = self.job(source)
                target = self.root / '14092601.mp4'
                target.write_bytes(b'valid-media-placeholder')
                with patch.object(worker, 'capture', AsyncMock(return_value='operator_stop')), \
                     patch.object(worker, 'inspect_youtube', AsyncMock(return_value={'title': 'test', 'is_live': True})), \
                     patch.object(worker, 'complete_recording', return_value=target) as complete:
                    await worker.run_download(job)
                complete.assert_called_once_with(job)
                self.assertEqual(self.redis.get('state:test-job'), 'ready')
                self.assertIsNone(self.redis.get('capture:owner'))
                messages = [c.args[2] for c in worker.edit.call_args_list]
                self.assertLess(next(i for i, m in enumerate(messages) if 'Finalizing' in m),
                                next(i for i, m in enumerate(messages) if 'Ready' in m))
                worker.edit.reset_mock()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    async def test_stopped_ts_becomes_valid_named_mp4_without_transcode(self):
        job = self.job()
        self.media(self.root / 'test-job-capture.ts')
        original_run = subprocess.Popen
        commands = []

        def observe(command, **kwargs):
            commands.append(command)
            return original_run(command, **kwargs)

        with patch.object(worker, 'capture', AsyncMock(return_value='operator_stop')), \
             patch.object(worker.subprocess, 'Popen', side_effect=observe):
            await worker.run_download(job)
        files = list(self.root.glob('*.mp4'))
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r'^\d{8}\.mp4$')
        self.assertTrue(worker.compatible_media(worker.probe_media_file(files[0])))
        ffmpeg_commands = [c for c in commands if c[0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg_commands), 1)
        self.assertIn('copy', ffmpeg_commands[0])
        self.assertNotIn('libx264', ffmpeg_commands[0])
        self.assertEqual(self.redis.get('state:test-job'), 'ready')

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    async def test_unavailable_endpoint_fails_and_releases_worker(self):
        job = self.job()
        with patch.object(worker, 'ORYX_STREAM_URL', 'http://127.0.0.1:1/missing.flv'), \
             patch.object(worker, 'STARTUP_TIMEOUT', 1), \
             patch.object(worker, 'IDLE_TIMEOUT', 1):
            await asyncio.wait_for(worker.run_download(job), 10)
        self.assertEqual(self.redis.get('state:test-job'), 'failed')
        self.assertIsNone(self.redis.get('capture:owner'))
        self.assertFalse(list(self.root.glob('*.mp4')))
        self.assertFalse(any('Ready' in c.args[2] for c in worker.edit.call_args_list))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    async def test_real_capture_sigint_then_finalize(self):
        job = self.job()
        path = self.root / 'test-job-capture.ts'
        command = ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-re', '-f', 'lavfi',
                   '-i', 'testsrc2=size=160x90:rate=25', '-f', 'lavfi', '-i',
                   'sine=frequency=440:sample_rate=48000', '-c:v', 'libx264',
                   '-preset', 'ultrafast', '-g', '25', '-c:a', 'aac', '-f',
                   'mpegts', '-flush_packets', '1', str(path)]

        async def operator_stop():
            for _ in range(100):
                if path.exists() and path.stat().st_size > 20000:
                    self.redis.set('stop:test-job', '1')
                    return
                await asyncio.sleep(0.1)
            self.fail('Capture did not produce media')

        with patch.object(worker, 'capture_command', return_value=(command, path)):
            await asyncio.wait_for(asyncio.gather(worker.run_download(job), operator_stop()), 20)
        self.assertEqual(self.redis.get('state:test-job'), 'ready')
        self.assertEqual(len(list(self.root.glob('*.mp4'))), 1)
        messages = [c.args[2] for c in worker.edit.call_args_list]
        self.assertTrue(any('Stopping' in m for m in messages))
        self.assertTrue(any('Finalizing' in m for m in messages))

    async def test_rename_failure_never_reports_ready(self):
        job = self.job()
        with patch.object(worker, 'capture', AsyncMock(return_value='operator_stop')), \
             patch.object(worker, 'complete_recording', side_effect=PermissionError):
            await worker.run_download(job)
        self.assertEqual(self.redis.get('state:test-job'), 'failed')
        self.assertFalse(any('Ready' in c.args[2] for c in worker.edit.call_args_list))

    async def test_expired_request_does_not_start_capture(self):
        job = self.job()
        job['requested_at'] -= 15
        with patch.object(worker, 'capture', AsyncMock()) as capture:
            await worker.run_download(job)
        capture.assert_not_called()
        self.assertEqual(self.redis.get('state:test-job'), 'failed')

    def test_inspection_diagnostics_prefer_error_and_hide_credentials(self):
        error = worker.inspection_error(
            b'WARNING: impersonation target unavailable\n'
            b'ERROR: channel is not currently live https://example.test/?token=secret')
        self.assertEqual(error.code, 'not_live')
        self.assertNotIn('secret', error.detail)
        self.assertNotIn('example.test', error.detail)
        self.assertEqual(worker.inspection_error(b'ERROR: HTTP Error 400: Bad Request').code, 'http_400')
        diagnostic = worker.safe_diagnostic(
            'ERROR: fragment failed https://googlevideo.test/videoplayback?token=secret')
        self.assertIn('fragment failed', diagnostic)
        self.assertNotIn('secret', diagnostic)
        self.assertNotIn('googlevideo', diagnostic)

    async def test_inspection_error_reaches_status(self):
        job = self.job('tiktok')
        error = worker.inspection_error(b'ERROR: HTTP Error 403: Forbidden')
        with patch.object(worker, 'inspect_youtube', AsyncMock(side_effect=error)), \
             patch.object(worker, 'capture', AsyncMock()) as capture:
            await worker.run_download(job)
        capture.assert_not_called()
        public = json.loads(self.redis.get('web:job:test-job'))
        self.assertEqual(public['error_code'], 'http_403')
        self.assertEqual(public['error_title'], 'Gagal mengambil media')
        self.assertEqual(public['error_message'], 'YouTube menolak permintaan media. Coba lagi untuk mengambil sumber media baru.')
        self.assertIn('http error 403', public['detail'].lower())
        self.assertNotIn(public['detail'], public['error_message'])
        self.assertEqual(public['state'], 'failed')
        self.assertIsNone(self.redis.get('capture:owner'))

    def test_failure_classification_maps_unavailable_network_cancelled_and_unknown(self):
        job = self.job('youtube')
        cases = [
            (worker.SourceInspectionError('unavailable', 'Video unavailable', 'ERROR: HTTP Error 404'), 'unavailable', 'Video tidak tersedia'),
            (TimeoutError('Source inspection timed out'), 'network', 'Koneksi bermasalah'),
            (RuntimeError('unexpected extractor state'), 'unknown', 'Capture gagal'),
        ]
        for error, code, title in cases:
            with self.subTest(code=code):
                self.assertEqual(worker.classify_failure(job, error)[:2], (code, title))
        job['stop_reason'] = 'operator_stop'
        self.assertEqual(worker.classify_failure(job, worker.MediaValidationError('incomplete', 'partial'))[:2],
                         ('cancelled', 'Dibatalkan'))

    async def test_tiktok_offline_does_not_capture(self):
        job = self.job('tiktok')
        with patch.object(worker, 'inspect_youtube', AsyncMock(return_value={'is_live': False})), \
             patch.object(worker, 'capture', AsyncMock()) as capture:
            await worker.run_download(job)
        capture.assert_not_called()
        self.assertEqual(self.redis.get('state:test-job'), 'failed')
        self.assertIsNone(self.redis.get('capture:owner'))

    async def test_social_videos_use_finite_capture_and_persist_metadata(self):
        for source, url in [('tiktok', 'https://www.tiktok.com/@tester/video/123'),
                            ('instagram', 'https://www.instagram.com/reel/ABC123/')]:
            with self.subTest(source=source):
                job = self.job(source)
                job['job_id'] = source + '-job'
                self.redis.set('capture:owner', job['job_id'], ex=30)
                job.update(url=url, origin='web', requested_at=time.time() - 60)
                path = self.root / (source + '.mp4')
                self.media(path)
                info = dict(id='ABC123', title='Dialog Batam', description='Original description',
                            uploader='RRI Batam', channel='rribatam', upload_date='20261005', duration=1,
                            formats=[dict(url='https://cdn.example/video.mp4', ext='mp4', vcodec='h264')])
                with patch.object(worker, 'inspect_youtube', AsyncMock(return_value=info)), \
                     patch.object(worker, 'capture', AsyncMock(return_value='completed')) as capture, \
                     patch.object(worker, 'complete_recording', return_value=path):
                    await worker.run_download(job)
                capture.assert_awaited_once()
                self.assertFalse(job['is_live'])
                saved = storage.recording(job['job_id'])
                self.assertEqual(saved['state'], 'ready')
                self.assertEqual(saved['source'], source)
                self.assertEqual(saved['source_metadata']['description'], 'Original description')
                self.assertEqual(saved['source_metadata']['uploader'], 'RRI Batam')
                self.assertNotIn('youtube_id', saved['source_metadata'])
                self.assertEqual(transcription_queue.enqueue(self.redis, job['job_id']), ('queued', True))

    async def test_instagram_unsupported_media_and_auth_errors_do_not_capture(self):
        for info in [{'formats': []}, {'entries': [{'url': 'https://cdn.example/photo.jpg', 'ext': 'jpg', 'height': 1080}]}]:
            job = self.job('instagram')
            job['url'] = 'https://instagram.com/p/ABC/'
            with patch.object(worker, 'inspect_youtube', AsyncMock(return_value=info)), \
                 patch.object(worker, 'capture', AsyncMock()) as capture:
                await worker.run_download(job)
            capture.assert_not_called()
            public = json.loads(self.redis.get('web:job:test-job'))
            self.assertEqual(public['state'], 'failed')
            self.assertIn('tidak berisi video', public['detail'])
        for diagnostic in [b'ERROR: login required', b'ERROR: HTTP Error 429', b'ERROR: Unable to extract video']:
            job = self.job('instagram')
            job['url'] = 'https://instagram.com/p/ABC/'
            error = worker.inspection_error(diagnostic)
            with patch.object(worker, 'inspect_youtube', AsyncMock(side_effect=error)), \
                 patch.object(worker, 'capture', AsyncMock()) as capture:
                await worker.run_download(job)
            capture.assert_not_called()
            self.assertEqual(json.loads(self.redis.get('web:job:test-job'))['detail'], error.detail)

    async def test_social_cancellation_and_telegram_finite_admission(self):
        job = self.job('instagram')
        job['url'] = 'https://instagram.com/p/ABC/'
        self.redis.set('stop:test-job', 1)
        with patch.object(worker, 'inspect_youtube', AsyncMock()) as inspect:
            await worker.run_download(job)
        inspect.assert_not_called()
        for url, source in [('https://www.tiktok.com/@tester/video/123', 'tiktok'),
                            ('https://instagram.com/reel/ABC/', 'instagram')]:
            self.redis.delete('active:123')
            update = self.update()
            update.message.text = url
            await bot.handle_url(update, SimpleNamespace(user_data={}))
            queued = json.loads(self.redis.lindex('download_queue', -1))
            self.assertEqual(queued['source'], source)
            self.assertFalse(queued['is_live'])

    def test_instagram_carousel_selects_only_first_video_and_retains_post_metadata(self):
        photo = {'url': 'https://cdn.example/photo.jpg', 'ext': 'jpg', 'height': 1080}
        video = {'id': 'video1', 'formats': [{'url': 'https://cdn.example/v.mp4', 'vcodec': 'h264'}]}
        selected, index = storage.social_video({'description': 'Post caption', 'entries': [photo, video, video]}, 'instagram')
        self.assertEqual(index, 2)
        self.assertEqual(selected['description'], 'Post caption')
        command, _ = worker.capture_command(dict(source='instagram', job_id='carousel', is_live=False,
                                                 url='https://instagram.com/p/ABC/', playlist_item=index))
        self.assertEqual(command[command.index('--playlist-items') + 1], '2')
        self.assertIn('--progress-template', command)
        with self.assertRaises(ValueError):
            storage.social_video({'formats': [{'url': 'https://cdn.example/audio.m4a', 'vcodec': 'none'}]}, 'tiktok')

    def test_confirmed_silent_social_video_is_not_confused_with_missing_audio_fragment(self):
        path = self.root / 'silent-video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=s=64x64:d=0.5',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True)
        info = worker.probe_media_file(path)
        self.assertFalse(worker.compatible_media(info))
        self.assertTrue(worker.compatible_media(info, allow_silent=True))
        with patch.object(worker, 'generate_final_filename', return_value=self.root / 'final.mp4'):
            job = dict(job_id='silent', source='instagram', silent_video=True, stop_reason='operator_stop')
            with self.assertRaises(worker.MediaValidationError):
                worker.complete_recording(job)
            job['stop_reason'] = 'completed'
            self.assertEqual(worker.complete_recording(job).name, 'final.mp4')

    async def test_tiktok_bot_admission(self):
        self.redis.set('worker:heartbeat', '1')
        update = self.update()
        update.message.text = 'https://www.tiktok.com/@tester/live'
        await bot.handle_url(update, None)
        await bot.handle_url(update, None)
        self.assertEqual(self.redis.llen('download_queue'), 1)
        self.assertEqual(json.loads(self.redis.lindex('download_queue', 0))['source'], 'tiktok')

    def test_filename_uses_source_title_and_daily_sequence(self):
        from datetime import datetime
        with patch.object(worker, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 14)
            self.assertEqual(worker.generate_final_filename('Judul asli').name,
                             '14092601 - Judul asli.mp4')
            (self.root / '14092601 - Judul asli.mp4').touch()
            (self.root / '14092609.mp4').touch()
            (self.root / '13092699.mp4').touch()
            self.assertEqual(worker.generate_final_filename('Judul berikutnya').name,
                             '14092610 - Judul berikutnya.mp4')

    def test_quality_selector_caps_video_without_forcing_upscale(self):
        best = quality.ytdlp_selector('best')
        capped = quality.ytdlp_selector('720')
        self.assertNotIn('height<=', best)
        self.assertIn('[height<=720]', capped)
        command, _ = worker.capture_command(dict(job_id='quality-job', source='youtube',
                                                 quality='720', url='https://youtu.be/test'))
        self.assertEqual(command[command.index('-f') + 1], capped)
        with self.assertRaises(ValueError):
            quality.ytdlp_selector('2160')

    def test_post_live_command_is_resumable_and_strict_about_missing_fragments(self):
        command, _ = worker.capture_command(dict(
            job_id='post-live', source='youtube', quality='720',
            live_status='post_live', url='https://youtu.be/test'))
        self.assertIn('--continue', command)
        self.assertIn('--abort-on-unavailable-fragments', command)
        self.assertIn('%(format_id)s', command[command.index('-o') + 1])

    def test_track_progress_template_is_enabled_for_finite_downloads(self):
        for source, is_live in (('youtube', False), ('youtube', True), ('tiktok', True), ('tiktok', False), ('instagram', False)):
            with self.subTest(source=source, is_live=is_live):
                command, _ = worker.capture_command(dict(
                    job_id='progress-job', source=source, is_live=is_live,
                    url='https://youtu.be/test'))
                self.assertEqual('--progress-template' in command, not is_live)
                if '--progress-template' in command:
                    template = command[command.index('--progress-template') + 1]
                    self.assertIn('%(info.vcodec)s|%(info.acodec)s', template)

    def test_track_progress_parser_handles_formats_and_unknown_percentages(self):
        for codecs, phase in (('avc1|none', 'video'), ('none|mp4a', 'audio'),
                              ('avc1|mp4a', 'media'), ('NA|NA', None)):
            with self.subTest(codecs=codecs):
                parsed = worker.parse_progress(f'streamfetch:{codecs}| 42.5%')
                self.assertEqual(parsed['phase'], phase)
                self.assertEqual(parsed['percent'], 42.5)
        self.assertIsNone(worker.parse_progress('streamfetch:none|mp4a|Unknown')['percent'])
        self.assertIsNone(worker.parse_progress('streamfetch:malformed'))
        self.assertEqual(worker.parse_progress('[download]  25.0% of 10MiB at 2MiB/s ETA 00:03')['percent'], 25)

    async def test_public_status_exposes_progress_without_private_job_fields(self):
        job = self.job('youtube')
        job.update(is_live=False, progress_percent=42.5, progress_phase='audio',
                   download_attempt=2, download_attempts=3, _capture_diagnostics=['private'])
        await worker.set_state(job, None, 'recording', 'Downloading audio')
        public = json.loads(self.redis.get('web:job:test-job'))
        for key in ('progress_percent', 'progress_phase', 'download_attempt', 'download_attempts'):
            self.assertEqual(public[key], job[key])
        self.assertNotIn('url', public)
        self.assertNotIn('_capture_diagnostics', public)

    async def test_capture_publishes_current_track_progress_from_stdout(self):
        job = self.job('youtube')
        job.update(is_live=False, download_attempt=1, download_attempts=1)
        lease = asyncio.get_running_loop().create_future()
        self.addCleanup(lease.cancel)
        command = [sys.executable, '-c',
                   "import pathlib, sys, time; pathlib.Path(sys.argv[1]).write_bytes(b'partial'); "
                   "print('streamfetch:none|mp4a| 42.5%', flush=True); time.sleep(1)",
                   str(self.root / 'test-job-140.m4a.part')]
        with patch.object(worker, 'capture_command', return_value=(command, None)):
            reason = await worker.capture(job, None, lease)
        self.assertEqual(reason, 'completed')
        public = json.loads(self.redis.get('web:job:test-job'))
        self.assertEqual(public['progress_phase'], 'audio')
        self.assertEqual(public['progress_percent'], 42.5)
        self.assertEqual(public['download_attempts'], 1)

    async def test_normal_vod_and_active_live_do_not_use_outer_retry(self):
        lease = asyncio.get_running_loop().create_future()
        self.addCleanup(lease.cancel)
        for live_status in ('not_live', 'was_live', 'is_live'):
            with self.subTest(live_status=live_status):
                job = self.job('youtube')
                job['live_status'] = live_status
                with patch.object(worker, 'capture', AsyncMock(return_value='source_error')) as capture, \
                     patch.object(worker, 'inspect_youtube', AsyncMock()) as inspect:
                    self.assertEqual(
                        await worker.capture_with_retries(job, None, lease), 'source_error')
                capture.assert_awaited_once()
                inspect.assert_not_awaited()

    async def test_capture_preserves_sanitized_ytdlp_error_and_exit_code(self):
        job = self.job('youtube')
        lease = asyncio.get_running_loop().create_future()
        self.addCleanup(lease.cancel)
        command = [
            sys.executable, '-c',
            "import sys; print('ERROR: fragment failed "
            "https://googlevideo.test/file?token=secret', file=sys.stderr); sys.exit(2)",
        ]
        with patch.object(worker, 'capture_command', return_value=(command, None)):
            reason = await worker.capture(job, None, lease)
        self.assertEqual(reason, 'source_error')
        self.assertEqual(job['download_exit_code'], 2)
        diagnostic = '\n'.join(job['_capture_diagnostics'])
        self.assertIn('fragment failed', diagnostic)
        self.assertNotIn('secret', diagnostic)
        self.assertNotIn('googlevideo', diagnostic)

    async def test_post_live_retry_refreshes_metadata_and_then_succeeds(self):
        job = self.job('youtube')
        job.update(live_status='post_live', is_live=False)
        lease = asyncio.get_running_loop().create_future()
        self.addCleanup(lease.cancel)
        with patch.object(
                worker, 'capture', AsyncMock(side_effect=['source_error', 'completed'])) as capture, \
             patch.object(worker, 'wait_for_youtube_retry', AsyncMock(return_value=True)) as wait, \
             patch.object(worker, 'inspect_youtube', AsyncMock(return_value={
                 'title': 'Arsip siap', 'live_status': 'was_live', 'was_live': True,
             })) as inspect:
            reason = await worker.capture_with_retries(job, None, lease)
        self.assertEqual(reason, 'completed')
        self.assertEqual(capture.await_count, 2)
        wait.assert_awaited_once()
        inspect.assert_awaited_once()
        self.assertEqual(job['download_attempt'], 2)
        self.assertEqual(job['download_attempts'], worker.YOUTUBE_POSTLIVE_ATTEMPTS)
        self.assertEqual(job['live_status'], 'was_live')

    async def test_post_live_retries_are_bounded_and_keep_partial_files(self):
        job = self.job('youtube')
        job.update(live_status='post_live', is_live=False)
        video = self.root / 'test-job-136.mp4'
        audio = self.root / 'test-job-140.m4a.part'
        video.write_bytes(b'complete video')
        audio.write_bytes(b'partial audio')
        lease = asyncio.get_running_loop().create_future()
        self.addCleanup(lease.cancel)
        with patch.object(worker, 'YOUTUBE_POSTLIVE_ATTEMPTS', 3), \
             patch.object(worker, 'capture', AsyncMock(return_value='source_error')) as capture, \
             patch.object(worker, 'wait_for_youtube_retry', AsyncMock(return_value=True)), \
             patch.object(worker, 'inspect_youtube', AsyncMock(return_value={
                 'live_status': 'post_live', 'was_live': True,
             })):
            reason = await worker.capture_with_retries(job, None, lease)
        self.assertEqual(reason, 'source_error')
        self.assertEqual(capture.await_count, 3)
        self.assertTrue(video.exists())
        self.assertTrue(audio.exists())

    async def test_cancellation_during_post_live_backoff_stops_retry(self):
        job = self.job('youtube')
        job.update(live_status='post_live', is_live=False)
        lease = asyncio.get_running_loop().create_future()
        self.addCleanup(lease.cancel)

        async def fail_and_cancel(*_):
            self.redis.set('stop:test-job', '1')
            return 'source_error'

        with patch.object(worker, 'capture', AsyncMock(side_effect=fail_and_cancel)) as capture:
            reason = await worker.capture_with_retries(job, None, lease)
        self.assertEqual(reason, 'operator_stop')
        capture.assert_awaited_once()

    def test_download_size_ignores_file_renamed_between_glob_and_stat(self):
        class VanishingPath:
            def is_file(self):
                return True

            def stat(self):
                raise FileNotFoundError

        root = SimpleNamespace(glob=lambda _pattern: [VanishingPath()])
        with patch.object(worker, 'DOWNLOAD_DIR', root):
            self.assertEqual(worker.download_size('test-job'), 0)

    def test_video_presets_validate_and_build_expected_encoders(self):
        info = dict(container='mpegts', video_codec='h264', audio_codec='aac',
                    pix_fmt='yuv420p', duration='1')
        completed = SimpleNamespace(returncode=1)
        for preset, codec, crf, bitrate in (
                ('balanced', 'libx264', '23', '128k'),
                ('compact', 'libx265', '27', '128k')):
            with self.subTest(preset=preset), \
                 patch.object(worker, 'probe_media_file', return_value=info), \
                 patch.object(worker.subprocess, 'run', return_value=completed) as run:
                self.assertIsNone(worker.finalize_to_compatible_mp4(
                    self.root / 'source.mp4', preset))
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('-c:v') + 1], codec)
            self.assertEqual(command[command.index('-crf') + 1], crf)
            self.assertEqual(command[command.index('-b:a') + 1], bitrate)
        with self.assertRaises(ValueError):
            quality.validate_preset('tiny')

    def test_mp3_command_uses_best_audio_and_audio_extraction(self):
        command, _ = worker.capture_command(dict(
            job_id='audio-job', source='youtube', quality='1080',
            output_format='mp3', url='https://youtu.be/test'))
        self.assertEqual(command[command.index('-f') + 1], 'ba/b')
        self.assertIn('--extract-audio', command)
        self.assertEqual(command[command.index('--audio-format') + 1], 'mp3')
        self.assertNotIn('--merge-output-format', command)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_mp3_finalization_creates_named_audio_file(self):
        source = self.root / 'test-job-source.ts'
        self.media(source)
        target = worker.complete_recording(dict(
            job_id='test-job', source='youtube', output_format='mp3',
            note='Audio pilihan'))
        self.assertEqual(target.suffix, '.mp3')
        self.assertTrue(worker.compatible_mp3(worker.probe_media_file(target)))

    def test_probe_timeout_returns_failure(self):
        with patch.object(worker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffprobe', 1)):
            self.assertIsNone(worker.probe_media_file(self.root / 'test.ts'))

    async def test_hung_startup_is_terminated_and_releases_worker(self):
        job = self.job()
        command = [sys.executable, '-c', 'import time; time.sleep(60)']
        with patch.object(worker, 'capture_command', return_value=(command, self.root / 'test-job.ts')), \
             patch.object(worker, 'STARTUP_TIMEOUT', 0.1), \
             patch.object(worker, 'STOP_TIMEOUT', 0.5):
            await asyncio.wait_for(worker.run_download(job), 5)
        self.assertEqual(self.redis.get('state:test-job'), 'failed')
        self.assertIsNone(self.redis.get('capture:owner'))

    def test_finalize_timeout_preserves_original(self):
        path = self.root / 'test-job-capture.ts'
        path.write_bytes(b'original media')
        info = dict(container='mpegts', video_codec='h264', audio_codec='aac',
                    pix_fmt='yuv420p', duration='1')
        with patch.object(worker, 'probe_media_file', return_value=info), \
             patch.object(worker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffmpeg', 1)):
            self.assertIsNone(worker.finalize_to_compatible_mp4(path))
        self.assertEqual(path.read_bytes(), b'original media')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_interrupted_youtube_separate_tracks_are_recovered(self):
        source = self.root / 'fixture.ts'
        self.media(source)
        for stream, label in (('v', 'video'), ('a', 'audio')):
            subprocess.run([
                'ffmpeg', '-v', 'error', '-y', '-i', str(source), '-map', f'0:{stream}:0',
                '-c', 'copy', '-f', 'matroska', str(self.root / f'test-job-{label}.part'),
            ], check=True, capture_output=True, timeout=10)
        target = worker.complete_recording(self.job('youtube'))
        self.assertTrue(worker.compatible_media(worker.probe_media_file(target)))
        self.assertTrue((self.root / 'test-job-video.part').exists())
        self.assertTrue((self.root / 'test-job-audio.part').exists())

    def test_mismatched_completed_video_and_partial_audio_are_not_merged(self):
        video = self.root / 'test-job-136.mp4'
        audio = self.root / 'test-job-140.m4a.part'
        video.write_bytes(b'video')
        audio.write_bytes(b'audio')

        def probe(path):
            if path == video:
                return dict(container='mov,mp4', video_codec='h264',
                            audio_codec='unknown', pix_fmt='yuv420p',
                            duration='2877.47', video_duration='2877.47',
                            audio_duration=None)
            if path == audio:
                return dict(container='mov,mp4', video_codec='unknown',
                            audio_codec='aac', pix_fmt='unknown',
                            duration='1273.48', video_duration=None,
                            audio_duration='1273.48')

        with patch.object(worker, 'probe_media_file', side_effect=probe), \
             patch.object(worker.subprocess, 'run') as run, \
             self.assertRaises(worker.MediaValidationError) as failure:
            worker.complete_recording(dict(job_id='test-job', source='youtube'))
        self.assertEqual(failure.exception.code, 'incomplete_tracks')
        run.assert_not_called()
        self.assertTrue(video.exists())
        self.assertTrue(audio.exists())

    def test_final_mp4_requires_aligned_audio_and_video_durations(self):
        info = dict(container='mov,mp4', video_codec='h264', audio_codec='aac',
                    pix_fmt='yuv420p', duration='100', video_duration='100',
                    audio_duration='50')
        self.assertFalse(worker.compatible_media(info))
        info['audio_duration'] = '99.5'
        self.assertTrue(worker.compatible_media(info))


if __name__ == '__main__':
    unittest.main()
