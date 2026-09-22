"""Verified, idempotent archival of completed files.

The archive filesystem is deliberately treated as secondary storage.  Callers
must only enqueue paths after their producer has committed success.
"""
import hashlib
import os
from pathlib import Path


CHUNK_SIZE = 4 * 1024 * 1024
MOUNT_MARKER = ".streamfetch-archive"


class ArchiveError(RuntimeError):
    """A retryable archive failure."""


class ArchiveConflict(ArchiveError):
    """The final destination exists with different content."""


def _contained_file(path, root):
    root = Path(root).resolve(strict=True)
    candidate = Path(path)
    if candidate.is_symlink():
        raise ArchiveError("source symlinks are not allowed")
    candidate = candidate.resolve(strict=True)
    if not candidate.is_file() or candidate.parent != root:
        raise ArchiveError("source must be a regular file directly inside downloads")
    return candidate, root


def _mount_points():
    """Return decoded mount points visible to this Linux process."""
    points = set()
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.split()
                if len(fields) >= 5:
                    # mountinfo escapes whitespace and backslashes as octal.
                    value = fields[4]
                    for escaped, plain in (("\\040", " "), ("\\011", "\t"),
                                           ("\\012", "\n"), ("\\134", "\\")):
                        value = value.replace(escaped, plain)
                    points.add(str(Path(value).resolve()))
    except OSError as exc:
        raise ArchiveError("cannot inspect Linux mount information") from exc
    return points


def validate_archive_storage(root):
    """Fail closed unless *root* is a mounted, marked, writable filesystem."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or str(root) not in _mount_points():
        raise ArchiveError("archive path is not a distinct visible mount")
    marker = root / MOUNT_MARKER
    if not marker.is_file() or marker.is_symlink():
        raise ArchiveError(f"archive mount marker {MOUNT_MARKER} is missing")
    if not os.access(root, os.W_OK | os.X_OK):
        raise ArchiveError("archive path is not writable")
    return root


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def archive_file(source_path, downloads_root, archive_root):
    """Copy one final file, verify it, then atomically publish its final name."""
    source, _ = _contained_file(source_path, downloads_root)
    archive_root = validate_archive_storage(archive_root)
    destination = archive_root / source.name
    part = archive_root / (source.name + ".part")
    # basename comes from a resolved direct child, but keep the destination
    # containment invariant explicit for future layout changes.
    if destination.parent.resolve() != archive_root or part.parent.resolve() != archive_root:
        raise ArchiveError("archive destination escapes archive root")

    source_before = source.stat()
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise ArchiveConflict("archive destination is not a regular file")
        source_hash = sha256_file(source)
        source_after_hash = source.stat()
        if ((source_before.st_dev, source_before.st_ino, source_before.st_size, source_before.st_mtime_ns) !=
                (source_after_hash.st_dev, source_after_hash.st_ino,
                 source_after_hash.st_size, source_after_hash.st_mtime_ns)):
            raise ArchiveError("source changed during archive verification")
        if source_before.st_size == destination.stat().st_size and source_hash == sha256_file(destination):
            return {"path": str(destination), "bytes": source_before.st_size,
                    "sha256": source_hash, "existing": True}
        raise ArchiveConflict("archive destination exists with different content")

    source_digest = hashlib.sha256()
    try:
        with source.open("rb") as incoming, part.open("wb") as outgoing:
            while chunk := incoming.read(CHUNK_SIZE):
                outgoing.write(chunk)
                source_digest.update(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        source_after = source.stat()
        identity_before = (source_before.st_dev, source_before.st_ino,
                           source_before.st_size, source_before.st_mtime_ns)
        identity_after = (source_after.st_dev, source_after.st_ino,
                          source_after.st_size, source_after.st_mtime_ns)
        if identity_before != identity_after:
            raise ArchiveError("source changed during archive copy")
        destination_digest = sha256_file(part)
        if source_digest.hexdigest() != destination_digest:
            raise ArchiveError("archive checksum mismatch")
        # Re-check after the expensive hash before exposing the final name.
        if destination.exists():
            raise ArchiveConflict("archive destination appeared during copy")
        part.rename(destination)
        return {"path": str(destination), "bytes": source_after.st_size,
                "sha256": destination_digest, "existing": False}
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
