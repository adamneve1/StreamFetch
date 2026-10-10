import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import storage, youtube_transcript


class YouTubeTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, DOWNLOAD_DIR=self.tmp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def track(extension='vtt'):
        return [{'ext': extension, 'url': 'https://example.invalid/subtitle'}]

    def test_subtitle_priority_is_manual_then_auto_and_indonesian_then_original(self):
        metadata = {
            'language': 'en',
            'subtitles': {'en': self.track(), 'id-ID': self.track()},
            'automatic_captions': {'id': self.track()},
        }
        self.assertEqual(youtube_transcript.select_subtitle(metadata),
                         youtube_transcript.SubtitleChoice('manual', 'id-ID', 'vtt'))
        metadata['subtitles'].pop('id-ID')
        self.assertEqual(youtube_transcript.select_subtitle(metadata),
                         youtube_transcript.SubtitleChoice('manual', 'en', 'vtt'))
        metadata['subtitles'] = {'fr': self.track()}
        self.assertEqual(youtube_transcript.select_subtitle(metadata),
                         youtube_transcript.SubtitleChoice('auto', 'id', 'vtt'))
        metadata['automatic_captions'] = {'fr': self.track()}
        self.assertIsNone(youtube_transcript.select_subtitle(metadata))

    def test_normalization_preserves_timestamps_and_publish_reuses_existing_format(self):
        job = dict(job_id='subtitle-job', source='youtube', source_type='youtube',
                   source_id='abcdefghijk', source_name='YouTube', transcript_only=True,
                   owner_type='telegram', owner_id='7', requested_at=100,
                   source_metadata={'youtube_id': 'abcdefghijk', 'title': 'Dialog'})
        content = ('WEBVTT\n\n00:00:01.250 --> 00:00:03.500\n<c>Halo &amp; selamat pagi</c>\n\n'
                   '00:00:04.000 --> 00:00:06.000\nBerita berikutnya\n')
        row = youtube_transcript.publish(
            job, youtube_transcript.SubtitleChoice('manual', 'id', 'vtt'), content)
        transcript = row['transcript']
        self.assertEqual((transcript['source'], transcript['language']), ('manual', 'id'))
        self.assertIn('00:00:01,250 --> 00:00:03,500',
                      Path(self.tmp.name, transcript['srt_filename']).read_text())
        self.assertEqual(Path(self.tmp.name, transcript['txt_filename']).read_text(),
                         'Halo & selamat pagi\nBerita berikutnya\n')
        self.assertEqual(youtube_transcript.find_cached(
            'abcdefghijk', 'id-ID', lambda candidate: candidate.get('owner_id') == '7')['job_id'],
            'subtitle-job')
        self.assertIsNone(youtube_transcript.find_cached(
            'abcdefghijk', 'id', lambda candidate: candidate.get('owner_id') == '8'))

    def test_subtitle_download_uses_metadata_only_ytdlp_path(self):
        commands = []
        def run(command, **_kwargs):
            commands.append(command)
            root = Path(command[command.index('--paths') + 1])
            root.joinpath('abcdefghijk.id.vtt').write_text(
                'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHalo\n')
        with patch.object(youtube_transcript.subprocess, 'run', side_effect=run):
            content = youtube_transcript.download_subtitle(
                'https://youtu.be/abcdefghijk', 'abcdefghijk',
                youtube_transcript.SubtitleChoice('manual', 'id', 'vtt'))
        self.assertIn('Halo', content)
        self.assertIn('--skip-download', commands[0])
        self.assertIn('--write-subs', commands[0])
        self.assertNotIn('-x', commands[0])

    def test_temporary_audio_failure_removes_partial_files_and_hides_diagnostics(self):
        def fail(command, **_kwargs):
            output = Path(command[command.index('-o') + 1].replace('%(ext)s', 'm4a.part'))
            output.write_bytes(b'partial')
            raise subprocess.CalledProcessError(1, command, stderr=b'private token')
        with patch.object(youtube_transcript.subprocess, 'run', side_effect=fail):
            with self.assertRaisesRegex(youtube_transcript.SubtitleUnavailable,
                                        'Audio sementara tidak dapat disiapkan'):
                youtube_transcript.download_temporary_audio(
                    'https://youtube.com/watch?v=abcdefghijk', 'job')
        self.assertEqual(list(Path(self.tmp.name).glob('transcript-ai-job.*')), [])


if __name__ == '__main__':
    unittest.main()
