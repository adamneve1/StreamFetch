import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis

from app import media_upload, storage, transcription_worker


class MediaUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name,
                              DOWNLOAD_DIR=self.tmp.name,
                              TRANSCRIPT_UPLOAD_MAX_DURATION_SECONDS='60')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def ffprobe(self, format_name, duration=10, streams=None):
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'format': {'format_name': format_name, 'duration': str(duration)},
            'streams': streams or [{'codec_type': 'audio', 'codec_name': 'aac'}],
        }))

    def test_all_supported_extensions_are_checked_against_actual_container(self):
        formats = {'.mp3': 'mp3', '.mp4': 'mov,mp4,m4a,3gp,3g2,mj2',
                   '.m4a': 'mov,mp4,m4a,3gp,3g2,mj2', '.wav': 'wav',
                   '.mov': 'mov,mp4,m4a,3gp,3g2,mj2', '.webm': 'matroska,webm'}
        for index, (suffix, format_name) in enumerate(formats.items()):
            with self.subTest(suffix=suffix):
                path = media_upload.upload_path('a' * 30 + str(index), suffix)
                path.write_bytes(b'media')
                with patch.object(media_upload.subprocess, 'run',
                                  return_value=self.ffprobe(format_name)):
                    info = media_upload.inspect_media(path, suffix, 100)
                self.assertEqual(info.size, 5)
                self.assertEqual(info.duration, 10)

    def test_actual_type_audio_duration_and_size_are_enforced(self):
        path = media_upload.upload_path('badtype', '.mp3')
        path.write_bytes(b'media')
        with patch.object(media_upload.subprocess, 'run',
                          return_value=self.ffprobe('matroska,webm')):
            with self.assertRaisesRegex(media_upload.UploadError, 'tidak cocok'):
                media_upload.inspect_media(path, '.mp3', 100)
        with patch.object(media_upload.subprocess, 'run', return_value=self.ffprobe(
                'mp3', streams=[{'codec_type': 'video'}])):
            with self.assertRaisesRegex(media_upload.UploadError, 'tidak memiliki audio'):
                media_upload.inspect_media(path, '.mp3', 100)
        with patch.object(media_upload.subprocess, 'run',
                          return_value=self.ffprobe('mp3', duration=61)):
            with self.assertRaisesRegex(media_upload.UploadError, 'terlalu panjang'):
                media_upload.inspect_media(path, '.mp3', 100)
        with self.assertRaisesRegex(media_upload.UploadError, 'terlalu besar'):
            media_upload.inspect_media(path, '.mp3', 4)

    def test_expired_orphan_cleanup_preserves_durable_queued_input(self):
        orphan = media_upload.upload_path('orphan', '.wav')
        orphan.write_bytes(b'orphan')
        old = time.time() - 100
        os.utime(orphan, (old, old))
        in_flight = media_upload.private_root() / 'upload-in-flight.wav.part'
        in_flight.write_bytes(b'uploading')
        active = media_upload.upload_path('active', '.wav')
        active.write_bytes(b'active')
        job = dict(job_id='active', source='upload', transcript_only=True,
                   uploaded_media=True, owner_type='web', owner_id='admin')
        storage.save_recording(job, 'ready')
        storage.save_capture_request({**job, 'temporary_media_filename': active.name,
                                      'temporary_media_private': True})
        storage.save_transcription_state('active', 'queued', replace=True,
                                         requested_at=time.time())
        with patch.dict(os.environ, TRANSCRIPT_UPLOAD_TEMP_TTL_SECONDS='1'):
            media_upload.cleanup_expired()
        self.assertFalse(orphan.exists())
        self.assertTrue(in_flight.exists())
        self.assertTrue(active.exists())

    def test_worker_publishes_exports_and_cleans_private_upload_after_success(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        path = media_upload.upload_path('workerupload', '.wav')
        path.write_bytes(b'private media')
        info = media_upload.MediaInfo(size=13, duration=3, format_name='wav',
                                      has_video=False, sha256='f' * 64)
        media_upload.enqueue(client, job_id='workerupload', path=path,
                             original_name='meeting.wav', owner_type='web', owner_id='admin',
                             origin='web_transcript_upload', info=info)
        request_id = storage.recording('workerupload')['transcript']['request_id']
        model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: (
            iter([SimpleNamespace(start=0, end=2, text=' Hello upload.', words=[])]),
            SimpleNamespace(language='en', language_probability=.9, duration=2)))
        with patch.dict(os.environ, TRANSCRIPTION_PROVIDER='local'), \
                patch.object(transcription_worker, 'DOWNLOAD_DIR', Path(self.tmp.name)), \
                patch.object(transcription_worker, 'r', client), \
                patch.object(transcription_worker, 'ensure_audio_stream'):
            transcription_worker.transcribe(
                {'job_id': 'workerupload', 'request_id': request_id}, model)
        row = storage.recording('workerupload')
        self.assertEqual(row['transcript']['status'], 'completed')
        self.assertFalse(path.exists())
        for kind in ('txt', 'srt', 'vtt'):
            self.assertTrue(Path(self.tmp.name, row['transcript'][kind + '_filename']).is_file())


if __name__ == '__main__':
    unittest.main()
