import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_web
from app import clipper, storage, web, worker


class TimestampTests(unittest.TestCase):
    def test_supported_timestamp_forms_normalize_to_seconds(self):
        for value in (90, '90', '1:30', '01:30', '00:01:30', '1m30s'):
            with self.subTest(value=value):
                self.assertEqual(clipper.parse_timestamp(value), 90)
        self.assertEqual(clipper.format_timestamp(755), '12:35')

    def test_invalid_negative_and_ranges_are_rejected(self):
        for value in ('', '-1', '1:70', 'nope', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                clipper.parse_timestamp(value)
        for start, end, duration in ((5, 5, 100), (10, 5, 100), (10, 101, 100)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                clipper.validate_range(start, end, duration)

    def test_youtube_url_timestamp_support(self):
        self.assertEqual(clipper.youtube_url_start('https://youtu.be/abcdefghijk?t=755'), 755)
        self.assertEqual(clipper.youtube_url_start('https://youtube.com/watch?v=abcdefghijk&t=12m35s'), 755)
        self.assertEqual(clipper.youtube_url_start('https://youtube.com/watch?v=abcdefghijk#t=1m30s'), 90)
        self.assertEqual(clipper.youtube_url_start('https://youtube.com/watch?v=abcdefghijk&t=bad'), 0)


class ClipperWebTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    post = test_web.WebTests.post

    URL = 'https://youtu.be/abcdefghijk?t=755'

    def metadata(self, duration=1200):
        info = dict(id='abcdefghijk', title='Dialog Batam', duration=duration,
                    thumbnail='https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg', is_live=False)
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(info).encode(), stderr=b'')
        with patch.object(web.subprocess, 'run', return_value=completed) as run:
            response = self.post('clipper/metadata', {'url': self.URL})
        self.assertEqual(response.status_code, 200)
        command = run.call_args.args[0]
        self.assertIn('--skip-download', command)
        self.assertNotIn('--download-sections', command)
        return response.json

    def test_clipper_page_navigation_and_api_auth(self):
        anonymous = self.app.test_client()
        page = anonymous.get('/clipper')
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="clipper-panel"', page.get_data(as_text=True))
        self.assertIn('script-src \'self\' https://www.youtube.com', page.headers['Content-Security-Policy'])
        self.assertIn('frame-src https://www.youtube-nocookie.com', page.headers['Content-Security-Policy'])
        denied = anonymous.post('/api/clipper/metadata', json={'url': self.URL})
        self.assertEqual(denied.status_code, 401)

    def test_lightweight_metadata_returns_title_thumbnail_duration_and_url_start(self):
        data = self.metadata()
        self.assertEqual(data['title'], 'Dialog Batam')
        self.assertEqual(data['video_id'], 'abcdefghijk')
        self.assertEqual(data['duration'], 1200)
        self.assertEqual(data['duration_label'], '20:00')
        self.assertEqual(data['url_start'], 755)
        self.assertEqual(data['url_start_label'], '12:35')
        self.assertTrue(data['token'])

    def test_metadata_exposes_only_validated_youtube_video_ids(self):
        self.assertEqual(clipper.youtube_video_id('abcdefghijk'), 'abcdefghijk')
        for value in ('short', 'abcdefghijk<script>', None):
            self.assertEqual(clipper.youtube_video_id(value), '')

    def test_video_and_mp3_jobs_use_canonical_queue_and_persist_clip_metadata(self):
        for output_format in ('mp4', 'mp3'):
            with self.subTest(output_format=output_format):
                self.redis.flushall()
                self.redis.set('worker:heartbeat', 1)
                data = self.metadata()
                response = self.post('clipper', dict(
                    url=self.URL, metadata_token=data['token'], start='12:35', end='14:05',
                    format=output_format, quality='720', compression='compact', storage='local'))
                self.assertEqual(response.status_code, 202)
                job = json.loads(self.redis.lindex('download_queue', 0))
                self.assertTrue(job['is_clip'])
                self.assertEqual((job['clip_start'], job['clip_end'], job['clip_duration']), (755, 845, 90))
                self.assertEqual(job['output_format'], output_format)
                self.assertEqual(job['quality'], 'best' if output_format == 'mp3' else '720')
                self.assertEqual(job['compression'], 'original' if output_format == 'mp3' else 'compact')
                private = storage.capture_request(job['job_id'])
                self.assertEqual(private['clip_start'], 755)
                storage.save_recording(job, 'ready')
                public = storage.recording(job['job_id'])
                self.assertEqual(public['clip_duration'], 90)
                self.assertNotIn('url', public)

    def test_invalid_ranges_are_rejected_before_admission(self):
        self.redis.set('worker:heartbeat', 1)
        for start, end in (('-1', '10'), ('10', '10'), ('20', '10'), ('1190', '1210')):
            data = self.metadata()
            response = self.post('clipper', dict(url=self.URL, metadata_token=data['token'],
                                                 start=start, end=end, format='mp4'))
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.redis.llen('download_queue'), 0)

    def test_retry_preserves_clip_range_and_history_supports_transcription(self):
        self.redis.set('worker:heartbeat', 1)
        data = self.metadata()
        created = self.post('clipper', dict(url=self.URL, metadata_token=data['token'],
                                            start=755, end=845, format='mp4')).json
        original = json.loads(self.redis.lpop('download_queue'))
        storage.save_recording(original, 'failed', 'network timeout')
        self.redis.delete('capture:owner', 'active:web')
        self.redis.set('worker:heartbeat', 1)
        retried = self.post('recordings/' + created['job_id'] + '/retry', {})
        self.assertEqual(retried.status_code, 202)
        replacement = json.loads(self.redis.lindex('download_queue', 0))
        self.assertEqual((replacement['clip_start'], replacement['clip_end'], replacement['clip_duration']),
                         (755, 845, 90))
        replacement['filename'] = 'clip.mp4'
        storage.save_recording(replacement, 'ready')
        history = {row['job_id']: row for row in self.client.get('/api/recordings').json['recordings']}
        self.assertTrue(history[replacement['job_id']]['is_clip'])
        self.assertEqual(self.post('recordings/' + replacement['job_id'] + '/transcript', {}).status_code, 202)


class ClipperWorkerTests(unittest.TestCase):
    def test_ytdlp_section_args_are_generated_only_from_validated_seconds(self):
        job = dict(job_id='clip', source='youtube', is_live=False, is_clip=True,
                   clip_start='1:30', clip_end='2:00', source_duration=600,
                   quality='720', output_format='mp4', url='https://youtu.be/abcdefghijk')
        command, _ = worker.capture_command(job)
        self.assertEqual(command[command.index('--download-sections') + 1], '*90-120')
        self.assertIn('--force-keyframes-at-cuts', command)
        self.assertEqual(command[-1], job['url'])
        self.assertNotIn('1:30', command)
        with self.assertRaises(ValueError):
            worker.capture_command({**job, 'clip_end': '$(touch /tmp/unsafe)'})

    def test_existing_source_command_is_unchanged_by_clipper(self):
        command, _ = worker.capture_command(dict(job_id='source', source='youtube', is_live=False,
                                                 quality='720', output_format='mp4',
                                                 url='https://youtu.be/abcdefghijk'))
        self.assertNotIn('--download-sections', command)
        self.assertNotIn('--force-keyframes-at-cuts', command)


if __name__ == '__main__':
    unittest.main()
