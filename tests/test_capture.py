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
from app import bot, quality, worker


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.patches = [patch.object(worker, 'r', self.redis),
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
        return SimpleNamespace(effective_chat=SimpleNamespace(id=chat),
                               message=SimpleNamespace(reply_text=AsyncMock()))

    def media(self, path):
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=25', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000', '-t', '1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
            '-f', 'mpegts', str(path),
        ], check=True, capture_output=True, timeout=30)

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
        original_run = subprocess.run
        commands = []

        def observe(command, **kwargs):
            commands.append(command)
            return original_run(command, **kwargs)

        with patch.object(worker, 'capture', AsyncMock(return_value='operator_stop')), \
             patch.object(worker.subprocess, 'run', side_effect=observe):
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

    async def test_inspection_error_reaches_status(self):
        job = self.job('tiktok')
        error = worker.inspection_error(b'ERROR: HTTP Error 403: Forbidden')
        with patch.object(worker, 'inspect_youtube', AsyncMock(side_effect=error)), \
             patch.object(worker, 'capture', AsyncMock()) as capture:
            await worker.run_download(job)
        capture.assert_not_called()
        public = json.loads(self.redis.get('web:job:test-job'))
        self.assertEqual(public['detail'], error.detail)
        self.assertEqual(public['state'], 'failed')
        self.assertIsNone(self.redis.get('capture:owner'))

    async def test_tiktok_offline_does_not_capture(self):
        job = self.job('tiktok')
        with patch.object(worker, 'inspect_youtube', AsyncMock(return_value={'is_live': False})), \
             patch.object(worker, 'capture', AsyncMock()) as capture:
            await worker.run_download(job)
        capture.assert_not_called()
        self.assertEqual(self.redis.get('state:test-job'), 'failed')
        self.assertIsNone(self.redis.get('capture:owner'))

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


if __name__ == '__main__':
    unittest.main()
