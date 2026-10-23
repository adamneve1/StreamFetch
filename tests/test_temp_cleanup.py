"""Janitor tests for stale capture artifact removal.

Run with: python -m unittest discover -s tests -v.
"""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app import worker


class TempCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [
            patch.object(worker, 'DOWNLOAD_DIR', self.root),
            patch.dict(os.environ, TEMP_CLEANUP_MAX_AGE_HOURS='72'),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])

    def age(self, hours):
        return time.time() - hours * 3600

    def test_stale_artifacts_are_removed_and_final_media_survives(self):
        old_hex = 'a' * 32
        stale = [
            self.root / f'{old_hex}-capture.ts',
            self.root / f'{old_hex}-136.mp4.part',
            self.root / f'{old_hex}-140.m4a.ytdl',
            self.root / 'some.finalize_tmp.mp4',
        ]
        for path in stale:
            path.write_bytes(b'stale')
            os.utime(path, (self.age(80), self.age(80)))
        finals = [self.root / '23102601.mp4', self.root / '14092601 - Judul.mp4']
        for path in finals:
            path.write_bytes(b'final media')
        recent = self.root / ('b' * 32 + '-capture.ts')
        recent.write_bytes(b'fresh')
        removed = worker.cleanup_temporary_files()
        self.assertEqual(removed, 4)
        for path in stale:
            self.assertFalse(path.exists())
        for path in finals + [recent]:
            self.assertTrue(path.exists())

    def test_recent_artifacts_and_unrelated_files_are_kept(self):
        recent_part = self.root / ('c' * 32 + '-140.m4a.part')
        recent_part.write_bytes(b'partial in progress')
        old_unrelated = self.root / 'generated-notes.txt'
        final = self.root / '23102602 - Catatan.mp4'
        final.write_bytes(b'kept')
        worker.cleanup_temporary_files()
        self.assertTrue(recent_part.exists())
        self.assertTrue(final.exists())

    def test_zero_age_disables_cleanup(self):
        (self.root / ('d' * 32 + '-capture.ts')).write_bytes(b'stale')
        with patch.object(worker, 'TEMP_CLEANUP_MAX_AGE_HOURS', 0):
            self.assertEqual(worker.cleanup_temporary_files(), 0)

    def test_missing_downloads_dir_is_tolerated(self):
        with patch.object(worker, 'DOWNLOAD_DIR', self.root / 'missing'):
            self.assertEqual(worker.cleanup_temporary_files(), 0)

    def test_temp_artifact_matches_capture_shapes_only(self):
        for name in ('e' * 32 + '-capture.ts', 'f' * 32 + '-gHp4.mp4',
                     'x.finalize_tmp.mp3', 'g.part', 'h.ytdl',
                     '1' * 32 + '-recovered.mkv'):
            self.assertTrue(worker.temp_artifact(Path(name)))
        for name in ('23102601.mp4', '14092601 - Judul.mp4',
                     '23102610 - Judul lama -23102699.mp4', 'j.mp4'):
            self.assertFalse(worker.temp_artifact(Path(name)))


if __name__ == '__main__':
    unittest.main()
