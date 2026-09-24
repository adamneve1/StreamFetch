import json
import os
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import test_web
from app import storage, worker


class ClipFeatures(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    post = test_web.WebTests.post
    def test_low_disk_rejects_before_queueing(self):
        self.redis.set('worker:heartbeat', 1)
        with patch.object(storage.shutil, 'disk_usage', return_value=type('Usage', (), dict(total=10*1024**3, free=1024))()):
            status = self.client.get('/api/status').json
            self.assertFalse(status['disk']['can_record'])
            result = self.post('record', {'source': 'youtube', 'url': 'https://youtu.be/test'})
            self.assertEqual(result.status_code, 507)
        self.assertEqual(self.redis.llen('download_queue'), 0)
        self.assertFalse(self.redis.exists('capture:owner'))

    def test_marker_requires_current_recording_and_survives_updates(self):
        job = dict(job_id='clip', source='oryx', started_at=time.time()-20, note='Batam menyapa')
        self.redis.set('capture:owner', 'clip')
        self.redis.set('state:clip', 'recording')
        self.redis.set('web:job:clip', json.dumps(job))
        self.assertEqual(self.post('markers', {'job_id': 'old', 'note': 'wrong'}).status_code, 409)
        result = self.post('markers', {'job_id': 'clip', 'note': 'Opening'})
        self.assertEqual(result.status_code, 201)
        self.assertAlmostEqual(result.json['markers'][0]['seconds'], 20, delta=1)
        storage.save_recording(job, 'recording')
        storage.save_recording(dict(job, filename='Batam menyapa -14092601.mp4'), 'ready')
        self.assertEqual(storage.recordings()[0]['markers'][0]['note'], 'Opening')
        self.redis.set('state:clip', 'finalizing')
        self.assertEqual(self.post('markers', {'job_id': 'clip'}).status_code, 409)

    def test_filters_combine_title_source_status_and_date(self):
        for key, source, title in [('one', 'oryx', 'Batam menyapa'), ('two', 'youtube', 'Opening')]:
            storage.save_recording(dict(job_id=key, source=source, note=title,
                                       requested_at=datetime(2026, 9, 14, 12).timestamp()), 'ready')
        rows = self.client.get('/api/recordings?q=BATAM&source=oryx&state=ready&date=2026-09-14').json
        self.assertEqual(rows['total'], 1)
        self.assertEqual(rows['recordings'][0]['job_id'], 'one')
        self.assertEqual(self.client.get('/api/recordings?q=missing').json['total'], 0)
        self.assertEqual(self.client.get('/api/recordings?date=bad').status_code, 400)

    def test_named_and_unnamed_files_share_daily_sequence(self):
        with patch.object(worker, 'DOWNLOAD_DIR', Path(self.tmp.name)), patch.object(worker, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 14)
            Path(self.tmp.name, '14092601.mp4').touch()
            Path(self.tmp.name, 'Other title -14092602.mp4').touch()
            target = worker.generate_final_filename(note='Batam menyapa')
            self.assertEqual(target.name, '14092603 - Batam menyapa.mp4')
            target.touch()
            self.assertEqual(worker.generate_final_filename().name, '14092604.mp4')
            self.assertEqual(worker.generate_final_filename(title='Judul asli').name,
                             '14092604 - Judul asli.mp4')
            unsafe = worker.generate_final_filename(note='../Bad: /title\\ name\x00')
            self.assertEqual(unsafe.parent, Path(self.tmp.name))
            self.assertNotIn(':', unsafe.name)
            self.assertNotIn('\\', unsafe.name)
            self.assertLess(len(worker.generate_final_filename(note='😀'*300).name.encode()), 255)

    def test_unavailable_disk_fails_closed(self):
        with patch.object(storage.shutil, 'disk_usage', side_effect=OSError):
            self.assertFalse(storage.disk_status()['can_record'])


class WorkerDiskGuard(unittest.IsolatedAsyncioTestCase):
    async def test_worker_rejects_low_disk_before_capture(self):
        import tempfile
        import fakeredis
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as root:
            r = fakeredis.FakeRedis(decode_responses=True)
            job = dict(job_id='disk-test', source='youtube', chat_id='web', origin='web')
            r.set('capture:owner', 'disk-test')
            r.set('active:web', 'disk-test')
            with patch.object(worker, 'r', r), patch.object(worker, 'DOWNLOAD_DIR', Path(root)), \
                 patch.object(storage, 'disk_status', return_value={'can_record': False}), \
                 patch.object(worker, 'capture', AsyncMock()) as capture:
                await worker.run_download(job)
            capture.assert_not_called()
            self.assertEqual(r.get('state:disk-test'), 'failed')
            self.assertFalse(r.exists('capture:owner'))
