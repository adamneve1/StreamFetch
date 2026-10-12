"""Private, validated media inputs for the existing transcription worker."""
import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from . import storage, transcription_queue
except ImportError:
    import storage
    import transcription_queue


PUBLIC_EXTENSIONS = {'.mp3', '.mp4', '.m4a', '.wav', '.mov', '.webm'}
VOICE_EXTENSIONS = {'.ogg', '.oga', '.opus'}
EXTENSION_FORMATS = {
    '.mp3': {'mp3'},
    '.wav': {'wav'},
    '.webm': {'matroska', 'webm'},
    '.ogg': {'ogg'}, '.oga': {'ogg'}, '.opus': {'ogg'},
    '.mp4': {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2'},
    '.m4a': {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2'},
    '.mov': {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2'},
}


class UploadError(ValueError):
    """A safe validation error suitable for Web or Telegram."""


@dataclass(frozen=True)
class MediaInfo:
    size: int
    duration: float
    format_name: str
    has_video: bool
    sha256: str


def _positive_int(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(1, value)


def web_max_bytes():
    return _positive_int('WEB_TRANSCRIPT_UPLOAD_MAX_BYTES', 100 * 1024 * 1024)


def telegram_max_bytes():
    # Telegram's hosted Bot API getFile download ceiling is 20 MB. A smaller
    # deployment limit may be selected, but never advertise more than that.
    return min(_positive_int('TELEGRAM_TRANSCRIPT_UPLOAD_MAX_BYTES', 20 * 1024 * 1024),
               20 * 1024 * 1024)


def max_duration_seconds():
    return _positive_int('TRANSCRIPT_UPLOAD_MAX_DURATION_SECONDS', 4 * 60 * 60)


def private_root():
    root = Path(os.getenv('DATA_DIR', '/data')).resolve() / 'transcript-uploads'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def display_name(value, fallback='media'):
    name = Path(str(value or '')).name.strip()
    if (not name or name in {'.', '..'} or len(name.encode('utf-8')) > 240
            or any(ord(char) < 32 or char == '\x7f' for char in name)):
        return fallback
    return name


def supported_suffix(filename, voice=False):
    suffix = Path(display_name(filename)).suffix.lower()
    allowed = PUBLIC_EXTENSIONS | (VOICE_EXTENSIONS if voice else set())
    if suffix not in allowed:
        formats = 'MP3, MP4, M4A, WAV, MOV, atau WEBM'
        raise UploadError(f'Format file tidak didukung. Gunakan {formats}.')
    return suffix


def human_size(size):
    size = max(0, int(size or 0))
    return f'{size / 1024 / 1024:.1f} MB' if size >= 1024 * 1024 else f'{size / 1024:.1f} KB'


def human_duration(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f'{seconds / 3600:g} jam'
    if seconds >= 60:
        return f'{seconds / 60:g} menit'
    return f'{seconds} detik'


def validate_declared_size(size, limit):
    try:
        size = int(size)
    except (TypeError, ValueError) as exc:
        raise UploadError('Ukuran file tidak tersedia.') from exc
    if size <= 0:
        raise UploadError('File kosong tidak dapat ditranskripsi.')
    if size > limit:
        raise UploadError(f'File terlalu besar. Batasnya {human_size(limit)}.')
    return size


def upload_path(job_id, suffix):
    if not isinstance(job_id, str) or not job_id.isalnum():
        raise UploadError('ID upload tidak valid.')
    return private_root() / f'upload-{job_id}{suffix}'


def private_media_path(filename):
    if not filename or Path(filename).name != filename:
        return None
    root = private_root()
    path = root / filename
    try:
        return path if path.parent.resolve() == root and not path.is_symlink() else None
    except OSError:
        return None


def save_stream(stream, job_id, filename, limit):
    """Copy an upload to a mode-0600 private file with an enforced byte cap."""
    suffix = supported_suffix(filename)
    final = upload_path(job_id, suffix)
    partial = final.with_suffix(final.suffix + '.part')
    total = 0
    digest = hashlib.sha256()
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UploadError(f'File terlalu besar. Batasnya {human_size(limit)}.')
                digest.update(chunk)
                output.write(chunk)
        validate_declared_size(total, limit)
        partial.replace(final)
        return final, digest.hexdigest()
    except Exception:
        partial.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_media(path, expected_suffix, limit, known_digest=None):
    """Validate the real container, audio stream, size, and duration with ffprobe."""
    path = Path(path)
    size = validate_declared_size(path.stat().st_size, limit)
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_format', '-show_streams',
            '-of', 'json', str(path),
        ], capture_output=True, text=True, check=False, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise UploadError('Pemeriksaan media kehabisan waktu.') from exc
    except OSError as exc:
        raise UploadError('FFprobe tidak tersedia untuk memeriksa media.') from exc
    if result.returncode:
        raise UploadError('Isi file bukan media yang valid atau formatnya rusak.')
    try:
        payload = json.loads(result.stdout)
        format_data = payload.get('format') or {}
        streams = payload.get('streams') or []
        duration = float(format_data.get('duration') or 0)
        names = {name.strip().lower() for name in
                 str(format_data.get('format_name') or '').split(',') if name.strip()}
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UploadError('Metadata media tidak valid.') from exc
    expected = EXTENSION_FORMATS.get(expected_suffix, set())
    if not names.intersection(expected):
        raise UploadError('Ekstensi file tidak cocok dengan isi media sebenarnya.')
    if not any(stream.get('codec_type') == 'audio' for stream in streams):
        raise UploadError('File tidak memiliki audio yang dapat ditranskripsi.')
    if not math.isfinite(duration) or duration <= 0:
        raise UploadError('Durasi media tidak dapat dibaca atau tidak valid.')
    maximum = max_duration_seconds()
    if duration > maximum:
        raise UploadError(f'Durasi media terlalu panjang. Batasnya {human_duration(maximum)}.')
    return MediaInfo(size=size, duration=duration,
                     format_name=next(iter(sorted(names)), ''),
                     has_video=any(stream.get('codec_type') == 'video' for stream in streams),
                     sha256=known_digest or sha256_file(path))


def duplicate(owner_type, owner_id, digest=None, source_id=None):
    for row in storage.recordings():
        transcript = row.get('transcript') or {}
        if (not row.get('uploaded_media') or row.get('owner_type') != owner_type
                or str(row.get('owner_id')) != str(owner_id)
                or transcript.get('status') not in {'queued', 'transcribing', 'completed'}):
            continue
        if digest and row.get('upload_digest') == digest:
            return row
        if source_id and row.get('source_id') == source_id:
            return row
    return None


def enqueue(client, *, job_id, path, original_name, owner_type, owner_id,
            origin, info, source_id=None, workspace_generation=None):
    """Publish a private input and admit it to the canonical transcription queue."""
    name = display_name(original_name)
    job = dict(job_id=job_id, source='upload', source_type='upload',
               source_id=source_id or info.sha256, source_name='Uploaded file',
               owner_type=owner_type, owner_id=str(owner_id), origin=origin,
               transcript_only=True, uploaded_media=True, upload_digest=info.sha256,
               upload_name=name, upload_size=info.size, upload_duration=info.duration,
               requested_at=time.time(), note=name,
               source_metadata={'title': name, 'duration': info.duration})
    if owner_type == 'telegram':
        job['workspace'] = 'transcript'
        job['workspace_generation'] = workspace_generation
    storage.save_recording(job, 'ready', 'Transcript Only · upload menunggu transkripsi AI')
    storage.save_capture_request({
        **job, 'temporary_media_filename': path.name,
        'temporary_media_private': True,
    })
    try:
        status, created = transcription_queue.enqueue(client, job_id)
        return status, created, False
    except Exception:
        # enqueue persists the queue state before touching Redis. Recovery will
        # safely admit that durable job when Redis returns.
        state = ((storage.recording(job_id) or {}).get('transcript') or {}).get('status')
        if state in {'queued', 'transcribing'}:
            return state, False, True
        path.unlink(missing_ok=True)
        storage.delete_recording(job_id)
        raise


def cleanup_job(job_id):
    request = storage.capture_request(job_id) or {}
    if request.get('temporary_media_private'):
        path = private_media_path(request.get('temporary_media_filename'))
        if path:
            path.unlink(missing_ok=True)


def cleanup_expired():
    """Remove unreferenced/terminal private inputs left by interrupted processes."""
    root = private_root()
    active = set()
    for job_id in storage.capture_request_ids():
        request = storage.capture_request(job_id) or {}
        if not request.get('temporary_media_private'):
            continue
        row = storage.recording(job_id) or {}
        status = (row.get('transcript') or {}).get('status')
        if status in {'queued', 'transcribing'}:
            active.add(request.get('temporary_media_filename'))
    cutoff = time.time() - _positive_int('TRANSCRIPT_UPLOAD_TEMP_TTL_SECONDS', 24 * 60 * 60)
    for path in root.iterdir():
        try:
            if (path.is_file() and path.name not in active
                    and path.stat().st_mtime < cutoff):
                path.unlink(missing_ok=True)
        except OSError:
            continue
