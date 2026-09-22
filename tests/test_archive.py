import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import fakeredis

from app import archive, worker


class ArchiveFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.downloads = root / "downloads"
        self.destination = root / "archive"
        self.downloads.mkdir()
        self.destination.mkdir()
        self.addCleanup(self.temp.cleanup)

    def archive(self, source):
        with patch.object(archive, "validate_archive_storage",
                          return_value=self.destination.resolve()):
            return archive.archive_file(source, self.downloads, self.destination)

    def test_stream_copy_verify_publish_and_idempotency(self):
        source = self.downloads / "spaces 日本語.mp4"
        source.write_bytes(b"media" * 10000)
        result = self.archive(source)
        final = self.destination / source.name
        self.assertEqual(final.read_bytes(), source.read_bytes())
        self.assertFalse((self.destination / (source.name + ".part")).exists())
        self.assertFalse(result["existing"])
        self.assertTrue(self.archive(source)["existing"])

    def test_conflict_never_overwrites_destination(self):
        source = self.downloads / "clip.mp4"
        source.write_bytes(b"new")
        final = self.destination / source.name
        final.write_bytes(b"old")
        with self.assertRaises(archive.ArchiveConflict):
            self.archive(source)
        self.assertEqual(final.read_bytes(), b"old")

    def test_rejects_source_outside_downloads_and_symlink(self):
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(b"media")
        with self.assertRaises(archive.ArchiveError):
            self.archive(outside)
        link = self.downloads / "link.mp4"
        link.symlink_to(outside)
        with self.assertRaises(archive.ArchiveError):
            self.archive(link)

    def test_mount_validation_requires_mount_entry_and_marker(self):
        with patch.object(archive, "_mount_points", return_value={str(self.destination.resolve())}):
            with self.assertRaises(archive.ArchiveError):
                archive.validate_archive_storage(self.destination)
            (self.destination / archive.MOUNT_MARKER).touch()
            self.assertEqual(archive.validate_archive_storage(self.destination),
                             self.destination.resolve())


class ArchiveWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_is_delayed_and_producer_state_is_untouched(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        job = {"job_id": "job-1", "source": "youtube", "path": "/downloads/a.mp4",
               "attempt": 1}
        redis.set("state:job-1", "ready")
        with patch.object(worker, "r", redis), \
             patch.object(worker, "ARCHIVE_MAX_RETRIES", 2), \
             patch.object(worker, "ARCHIVE_RETRY_BASE_SECONDS", 10), \
             patch.object(worker.storage, "save_archive_state"), \
             patch.object(worker.archive, "archive_file", side_effect=archive.ArchiveError("offline")):
            before = time.time()
            await worker.run_archive(job)
        self.assertEqual(redis.get("state:job-1"), "ready")
        pending = json.loads(redis.get("archive:state:job-1"))
        self.assertEqual(pending["archive_status"], "archive_pending")
        delayed = redis.zrange("archive_delayed", 0, -1, withscores=True)
        self.assertEqual(len(delayed), 1)
        self.assertGreaterEqual(delayed[0][1], before + 9)

    async def test_conflict_is_not_retried(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        job = {"job_id": "job-2", "source": "oryx", "path": "/downloads/a.mp4",
               "attempt": 1}
        with patch.object(worker, "r", redis), \
             patch.object(worker.storage, "save_archive_state"), \
             patch.object(worker.archive, "archive_file", side_effect=archive.ArchiveConflict("different")):
            await worker.run_archive(job)
        state = json.loads(redis.get("archive:state:job-2"))
        self.assertEqual(state["archive_status"], "archive_conflict")
        self.assertEqual(redis.zcard("archive_delayed"), 0)
