import os
import re
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis

from app import storage, transcription_worker, web

REAL_ENSURE_AUDIO_STREAM = transcription_worker.ensure_audio_stream


class FakeModel:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def transcribe(self, path, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        segments = [
            SimpleNamespace(start=3.2, end=7.8,
                            text=' Selamat pagi pendengar RRI Batam.'),
            SimpleNamespace(start=8.1, end=13.4,
                            text='Berikut kami hadirkan informasi terkini.'),
        ]
        return iter(segments), SimpleNamespace(language='id', language_probability=0.98)


class TranscriptionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name,
                              DOWNLOAD_DIR=self.tmp.name)
        self.env.start()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.path = Path(self.tmp.name) / 'berita pagi.mp4'
        self.path.write_bytes(b'media fixture')
        storage.save_recording({'job_id': 'video-job', 'filename': self.path.name,
                                'source': 'youtube'}, 'ready')
        self.patches = [
            patch.object(transcription_worker, 'r', self.redis),
            patch.object(transcription_worker, 'DOWNLOAD_DIR', Path(self.tmp.name)),
            patch.object(transcription_worker, 'ensure_audio_stream'),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_successful_completion_txt_srt_and_status_transitions(self):
        original = storage.save_transcription_state
        with patch.object(storage, 'save_transcription_state', wraps=original) as save:
            transcription_worker.transcribe({'job_id': 'video-job'}, FakeModel())

        statuses = [call.args[1] for call in save.call_args_list]
        self.assertEqual(statuses, ['transcribing', 'completed'])
        transcript = storage.recording('video-job')['transcript']
        self.assertEqual(transcript['status'], 'completed')
        self.assertEqual(transcript['language'], 'id')
        self.assertEqual(transcript['model'], 'small')
        txt = (Path(self.tmp.name) / transcript['txt_filename']).read_text()
        srt = (Path(self.tmp.name) / transcript['srt_filename']).read_text()
        self.assertIn('Selamat pagi pendengar RRI Batam.', txt)
        self.assertIn('Berikut kami hadirkan informasi terkini.', txt)
        self.assertRegex(srt, re.compile(
            r'^1\n00:00:03,200 --> 00:00:07,800\n.+\n\n'
            r'2\n00:00:08,100 --> 00:00:13,400\n.+\n$', re.DOTALL))
        self.assertTrue(self.path.with_suffix('.vtt').read_text().startswith('WEBVTT\n'))

    def test_whisper_failure_sets_failed_without_touching_video(self):
        original = self.path.read_bytes()
        self.redis.set('transcription:guard:video-job', 'queued')
        transcription_worker.transcribe(
            {'job_id': 'video-job'}, FakeModel(RuntimeError('private detail')))
        transcript = storage.recording('video-job')['transcript']
        self.assertEqual(transcript['status'], 'failed')
        self.assertNotIn('private detail', transcript['error'])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.path.with_suffix('.txt').exists())
        self.assertFalse(self.redis.exists('transcription:guard:video-job'))

    def test_missing_video_is_reported_as_failed(self):
        self.path.unlink()
        transcription_worker.transcribe({'job_id': 'video-job'}, FakeModel())
        transcript = storage.recording('video-job')['transcript']
        self.assertEqual(transcript['status'], 'failed')
        self.assertIn('tidak ditemukan', transcript['error'])

    def test_recovery_requeues_interrupted_job_once(self):
        storage.save_transcription_state('video-job', 'transcribing', model='small')
        transcription_worker.recover_jobs()
        transcription_worker.recover_jobs()
        self.assertEqual(self.redis.llen(transcription_worker.QUEUE), 1)
        self.assertEqual(storage.recording('video-job')['transcript']['status'], 'queued')

    def test_timestamp_rounding_and_valid_srt(self):
        txt, srt = transcription_worker.render_outputs([
            SimpleNamespace(start=0.001, end=3661.9996, text=' Halo '),
        ])
        self.assertEqual(txt, 'Halo\n')
        self.assertEqual(srt, '1\n00:00:00,001 --> 01:01:02,000\nHalo\n')

    def test_probe_rejects_missing_audio_and_corrupt_media(self):
        with patch.object(transcription_worker.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=0,
                                                       stdout='{"streams": []}')):
            with self.assertRaisesRegex(transcription_worker.TranscriptionError,
                                        'tidak memiliki stream audio'):
                REAL_ENSURE_AUDIO_STREAM(self.path)
        with patch.object(transcription_worker.subprocess, 'run',
                          return_value=SimpleNamespace(returncode=1, stdout='')):
            with self.assertRaisesRegex(transcription_worker.TranscriptionError,
                                        'rusak atau tidak didukung'):
                REAL_ENSURE_AUDIO_STREAM(self.path)


class CloudflareTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'DATA_DIR': self.tmp.name,
            'DOWNLOAD_DIR': self.tmp.name,
            'TRANSCRIPTION_PROVIDER': 'auto',
            'CLOUDFLARE_ACCOUNT_ID': 'account-id',
            'CLOUDFLARE_API_TOKEN': 'secret-token',
            'CLOUDFLARE_TRANSCRIPTION_CONCURRENCY': '3',
        })
        self.env.start()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.media = Path(self.tmp.name) / 'cloudflare.mp4'
        self.media.write_bytes(b'media')
        self.chunk_path = Path(self.tmp.name) / 'chunk.flac'
        self.chunk_path.write_bytes(b'audio')
        storage.save_recording({'job_id': 'cloud-job', 'filename': self.media.name,
                                'source': 'youtube'}, 'ready')
        self.patches = [
            patch.object(transcription_worker, 'r', self.redis),
            patch.object(transcription_worker, 'DOWNLOAD_DIR', Path(self.tmp.name)),
            patch.object(transcription_worker, 'ensure_audio_stream'),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def chunk(self, index=0, offset=0, duration=30, core_start=0, core_end=30):
        return transcription_worker.AudioChunk(
            index, self.chunk_path, offset, duration, core_start, core_end)

    @staticmethod
    def response(text='Halo dunia', start=1, end=2):
        return {'success': True, 'result': {
            'text': text,
            'transcription_info': {'language': 'id',
                                   'language_probability': 0.9966},
            'segments': [{'start': start, 'end': end, 'text': text,
                          'words': [{'start': start, 'end': end,
                                     'word': text, 'probability': 0.99}]}],
            'vtt': f'WEBVTT\\n\\n00:00:0{start}.000 --> 00:00:0{end}.000\\n{text}\\n',
        }}

    def test_cloudflare_success_normalizes_and_publishes_outputs(self):
        model = FakeModel()
        cloud_result = ([transcription_worker.TranscriptSegment(
            1, 2, 'Halo dunia', [transcription_worker.TranscriptWord(
                1, 2, 'Halo dunia', 0.99)])],
                        transcription_worker.TranscriptInfo('id', 0.9966))
        with patch.object(transcription_worker, 'transcribe_cloudflare',
                          return_value=cloud_result):
            transcription_worker.transcribe({'job_id': 'cloud-job'}, model)
        transcript = storage.recording('cloud-job')['transcript']
        self.assertEqual(transcript['status'], 'completed')
        self.assertEqual(transcript['model'], transcription_worker.CLOUDFLARE_MODEL)
        self.assertEqual(transcript['language'], 'id')
        self.assertEqual(model.calls, 0)
        self.assertEqual(self.media.with_suffix('.txt').read_text(), 'Halo dunia\n')
        self.assertIn('00:00:01.000 --> 00:00:02.000',
                      self.media.with_suffix('.vtt').read_text())

    def test_chunk_timestamp_merge_offsets_words_and_removes_overlap(self):
        first = transcription_worker.ChunkResult(
            self.chunk(0, 0, 12, 0, 10),
            [transcription_worker.TranscriptSegment(9, 10, 'batas', [
                transcription_worker.TranscriptWord(9, 10, 'batas')])],
            transcription_worker.TranscriptInfo('id', 0.9), 1, 0.1)
        second = transcription_worker.ChunkResult(
            self.chunk(1, 8, 12, 10, 20),
            [transcription_worker.TranscriptSegment(1, 3, 'batas lanjut', [
                transcription_worker.TranscriptWord(1, 2, 'batas'),
                transcription_worker.TranscriptWord(2, 3, 'lanjut')])],
            transcription_worker.TranscriptInfo('id', 1.0), 1, 0.1)
        segments, info = transcription_worker.merge_chunk_results([second, first])
        self.assertEqual([item.text for item in segments], ['batas', 'lanjut'])
        self.assertEqual([(item.start, item.end) for item in segments],
                         [(9, 10), (10, 11)])
        self.assertEqual(info.language, 'id')
        self.assertAlmostEqual(info.language_probability, 0.95)

    def test_cloudflare_chunk_concurrency_is_bounded(self):
        chunks = [self.chunk(i, i * 30, 30, i * 30, (i + 1) * 30)
                  for i in range(7)]
        active = 0
        maximum = 0
        lock = threading.Lock()

        def request(chunk, cancel_event=None):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return self.response(f'chunk {chunk.index}', 1, 2)

        with patch.dict(os.environ, CLOUDFLARE_TRANSCRIPTION_CONCURRENCY='2'), \
                patch.object(transcription_worker, 'prepare_audio_chunks',
                             return_value=chunks), \
                patch.object(transcription_worker, 'cloudflare_request',
                             side_effect=request):
            segments, _ = transcription_worker.transcribe_cloudflare(self.media)
        self.assertEqual(maximum, 2)
        self.assertEqual(len(segments), 7)

    def _assert_transient_falls_back(self, error):
        model = FakeModel()
        chunk = self.chunk()
        with patch.object(transcription_worker, 'prepare_audio_chunks',
                          return_value=[chunk]), \
                patch.object(transcription_worker.urllib.request, 'urlopen',
                             side_effect=error):
            transcription_worker.transcribe({'job_id': 'cloud-job'}, model)
        transcript = storage.recording('cloud-job')['transcript']
        self.assertEqual(transcript['status'], 'completed')
        self.assertEqual(transcript['model'], 'small')
        self.assertEqual(model.calls, 1)

    def test_429_falls_back_to_local(self):
        self._assert_transient_falls_back(urllib.error.HTTPError(
            'url', 429, 'quota', {}, None))

    def test_timeout_falls_back_to_local(self):
        self._assert_transient_falls_back(socket.timeout('timed out'))

    def test_network_error_falls_back_to_local(self):
        self._assert_transient_falls_back(
            urllib.error.URLError(OSError('connection reset')))

    def test_5xx_falls_back_to_local(self):
        self._assert_transient_falls_back(urllib.error.HTTPError(
            'url', 503, 'unavailable', {}, None))

    def test_authentication_failure_is_reported_without_local_fallback(self):
        model = FakeModel()
        with patch.object(transcription_worker, 'prepare_audio_chunks',
                          return_value=[self.chunk()]), \
                patch.object(transcription_worker.urllib.request, 'urlopen',
                             side_effect=urllib.error.HTTPError(
                                 'url', 403, 'forbidden', {}, None)):
            transcription_worker.transcribe({'job_id': 'cloud-job'}, model)
        transcript = storage.recording('cloud-job')['transcript']
        self.assertEqual(transcript['status'], 'failed')
        self.assertIn('Autentikasi Cloudflare gagal', transcript['error'])
        self.assertEqual(model.calls, 0)

    def test_malformed_response_is_rejected_without_local_fallback(self):
        model = FakeModel()
        with patch.object(transcription_worker, 'prepare_audio_chunks',
                          return_value=[self.chunk()]), \
                patch.object(transcription_worker, 'cloudflare_request',
                             return_value={'success': True, 'result': {
                                 'text': 'rusak', 'segments': 'bukan daftar'}}):
            transcription_worker.transcribe({'job_id': 'cloud-job'}, model)
        transcript = storage.recording('cloud-job')['transcript']
        self.assertEqual(transcript['status'], 'failed')
        self.assertIn('tidak valid', transcript['error'])
        self.assertEqual(model.calls, 0)

    def test_cancellation_stops_before_chunk_requests(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(transcription_worker.TranscriptionCancelled), \
                patch.object(transcription_worker, 'prepare_audio_chunks',
                             return_value=[self.chunk()]), \
                patch.object(transcription_worker, 'cloudflare_request') as request:
            transcription_worker.transcribe_cloudflare(self.media, cancelled)
        request.assert_not_called()

    def test_explicit_local_provider_does_not_call_cloudflare(self):
        model = FakeModel()
        with patch.dict(os.environ, TRANSCRIPTION_PROVIDER='local'), \
                patch.object(transcription_worker, 'transcribe_cloudflare') as cloud:
            transcription_worker.transcribe({'job_id': 'cloud-job'}, model)
        self.assertEqual(storage.recording('cloud-job')['transcript']['status'],
                         'completed')
        self.assertEqual(model.calls, 1)
        cloud.assert_not_called()


class TranscriptionWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name,
                              DOWNLOAD_DIR=self.tmp.name,
                              WEB_PASSWORD='test-password',
                              WEB_ADMIN_PASSWORD='admin-password',
                              WEB_SECRET_KEY='test-secret')
        self.env.start()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.app = web.create_app(self.redis)
        self.app.testing = True
        self.client = self.app.test_client()
        self.csrf = self.client.post('/api/login',
                                     json={'password': 'admin-password'}).json['csrf']
        self.path = Path(self.tmp.name) / 'existing.mp4'
        self.path.write_bytes(b'video')
        storage.save_recording({'job_id': 'existing', 'filename': self.path.name,
                                'source': 'youtube'}, 'ready')
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def post(self, path):
        return self.client.post('/api/' + path, json={},
                                headers={'X-CSRF-Token': self.csrf})

    def test_job_creation_and_duplicate_prevention(self):
        created = self.post('recordings/existing/transcript')
        duplicate = self.post('recordings/existing/transcript')
        self.assertEqual(created.status_code, 202)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(self.redis.llen('transcription_queue'), 1)
        self.assertEqual(storage.recording('existing')['transcript']['status'], 'queued')

    def test_transcript_download_endpoints(self):
        txt = self.path.with_suffix('.txt')
        srt = self.path.with_suffix('.srt')
        vtt = self.path.with_suffix('.vtt')
        txt.write_text('Isi transkrip\n', encoding='utf-8')
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nIsi\n', encoding='utf-8')
        vtt.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nIsi\n',
                       encoding='utf-8')
        storage.save_transcription_state(
            'existing', 'completed', txt_filename=txt.name,
            srt_filename=srt.name, vtt_filename=vtt.name,
            language='id', model='small')

        view = self.client.get('/api/recordings/existing/transcript/view')
        txt_download = self.client.get('/api/recordings/existing/transcript/txt')
        srt_download = self.client.get('/api/recordings/existing/transcript/srt')
        vtt_download = self.client.get('/api/recordings/existing/transcript/vtt')
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.get_data(as_text=True), 'Isi transkrip\n')
        self.assertEqual(txt_download.status_code, 200)
        self.assertIn('attachment', txt_download.headers['Content-Disposition'])
        self.assertEqual(srt_download.status_code, 200)
        self.assertIn('attachment', srt_download.headers['Content-Disposition'])
        self.assertEqual(vtt_download.status_code, 200)
        self.assertEqual(vtt_download.mimetype, 'text/vtt')
        view.close()
        txt_download.close()
        srt_download.close()
        vtt_download.close()

    def test_unfinished_transcript_cannot_be_downloaded(self):
        storage.save_transcription_state('existing', 'failed', error='failed')
        self.assertEqual(
            self.client.get('/api/recordings/existing/transcript/txt').status_code, 404)


if __name__ == '__main__':
    unittest.main()
