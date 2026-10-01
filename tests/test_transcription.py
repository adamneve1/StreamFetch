import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis

from app import storage, transcription_worker, web

REAL_ENSURE_AUDIO_STREAM = transcription_worker.ensure_audio_stream


class FakeModel:
    def __init__(self, error=None):
        self.error = error

    def transcribe(self, path, **kwargs):
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
        txt.write_text('Isi transkrip\n', encoding='utf-8')
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nIsi\n', encoding='utf-8')
        storage.save_transcription_state(
            'existing', 'completed', txt_filename=txt.name,
            srt_filename=srt.name, language='id', model='small')

        view = self.client.get('/api/recordings/existing/transcript/view')
        txt_download = self.client.get('/api/recordings/existing/transcript/txt')
        srt_download = self.client.get('/api/recordings/existing/transcript/srt')
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.get_data(as_text=True), 'Isi transkrip\n')
        self.assertEqual(txt_download.status_code, 200)
        self.assertIn('attachment', txt_download.headers['Content-Disposition'])
        self.assertEqual(srt_download.status_code, 200)
        self.assertIn('attachment', srt_download.headers['Content-Disposition'])
        view.close()
        txt_download.close()
        srt_download.close()

    def test_unfinished_transcript_cannot_be_downloaded(self):
        storage.save_transcription_state('existing', 'failed', error='failed')
        self.assertEqual(
            self.client.get('/api/recordings/existing/transcript/txt').status_code, 404)


if __name__ == '__main__':
    unittest.main()
