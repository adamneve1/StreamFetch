"""Persistent source settings and recording catalogue shared by web and worker."""
import json
import os
import sqlite3
import time
import shutil
import math
import re
from pathlib import Path
from contextlib import contextmanager
from urllib.parse import urlsplit


@contextmanager
def connection():
    root = Path(os.getenv('DATA_DIR', '/data'))
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'capture.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS markers (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, seconds REAL NOT NULL, note TEXT NOT NULL, created REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, updated REAL NOT NULL, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS capture_requests (id TEXT PRIMARY KEY, created REAL NOT NULL, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    try:
        with db:
            yield db
    finally:
        db.close()


def validate_url(url, youtube=False):
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError('Link-nya belum valid. Coba cek lagi, ya.')
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.scheme not in ({'http', 'https'} if youtube else {'http', 'https', 'rtmp', 'rtmps', 'srt'}):
        raise ValueError('Pakai link playback HTTP, HTTPS, RTMP, RTMPS, atau SRT, ya.')
    if youtube and parsed.hostname.lower() not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
        raise ValueError('Masukkan link YouTube yang valid, ya.')
    # Public RRI Restreamer players use the channel UUID for their HLS path.
    # Restrict conversion to the observed player route on the official host.
    if not youtube and parsed.hostname.lower() == 'public-streaming.rri.go.id':
        match = re.fullmatch(r'/playersite_([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\.html', parsed.path)
        if match:
            if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
                raise ValueError('Gunakan link player publik RRI tanpa credential atau port tambahan.')
            return f'https://public-streaming.rri.go.id/memfs/{match.group(1)}.m3u8'
    if not youtube and parsed.path.endswith('.html'):
        raise ValueError('Pakai link stream langsung, bukan halaman player.')
    return url


def validate_tiktok_url(url):
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError('Masukkan link TikTok video/post atau Live: https://www.tiktok.com/@username/video/123 atau /live')
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'}
            or parsed.hostname not in {'tiktok.com', 'www.tiktok.com', 'm.tiktok.com'}
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443}
            or not re.fullmatch(r'/@[A-Za-z0-9_.]+/(?:live|(?:video|photo)/\d+)/?', parsed.path)):
        raise ValueError('Masukkan link TikTok video/post atau Live: https://www.tiktok.com/@username/video/123 atau /live')
    return 'https://www.tiktok.com' + parsed.path.rstrip('/')


def tiktok_is_live(url):
    return urlsplit(validate_tiktok_url(url)).path.endswith('/live')


def validate_instagram_url(url):
    message = 'Masukkan link Instagram Reel atau post video: https://www.instagram.com/reel/ID/ atau /p/ID/.'
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError(message)
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'instagram.com', 'www.instagram.com'}
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443}
            or not re.fullmatch(r'/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+/?', parsed.path)):
        raise ValueError(message)
    return 'https://www.instagram.com' + parsed.path.rstrip('/') + '/'


def social_video(info, source):
    """Select one video per job; image-only posts never enter capture."""
    entries = info.get('entries') if isinstance(info, dict) else None
    candidates = list(enumerate(entries, 1)) if isinstance(entries, list) else [(None, info)]
    for index, entry in candidates:
        if not isinstance(entry, dict):
            continue
        formats = entry.get('formats') or [entry]
        if any(isinstance(fmt, dict) and fmt.get('url') and fmt.get('vcodec') != 'none'
               and fmt.get('ext') not in {'jpg', 'jpeg', 'png', 'webp', 'gif', 'mhtml'}
               and (fmt.get('vcodec') or fmt.get('height') or fmt.get('ext') in {'mp4', 'webm', 'mov', 'm3u8'})
               for fmt in formats):
            # Post-level description/uploader remains available on carousel entries.
            return {**{k: v for k, v in info.items() if k != 'entries'}, **entry}, index
    raise ValueError(('Instagram' if source == 'instagram' else 'TikTok') + ' post ini tidak berisi video yang didukung. Foto/gambar tidak diunduh.')


def sources():
    with connection() as db:
        # Seed once; editing this entry later takes precedence over .env.
        if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            url = os.getenv('ORYX_STREAM_URL', '').strip()
            if url:
                db.execute('INSERT OR IGNORE INTO sources VALUES (?, ?, ?)', ('default', 'Oryx utama', url))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('seeded', '1')")
        return [dict(row) for row in db.execute('SELECT * FROM sources ORDER BY name')]


def setting(key, default=None):
    """Read one private application setting."""
    with connection() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def save_setting(key, value):
    """Persist one private application setting."""
    with connection() as db:
        db.execute('INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (key, str(value)))


def save_source(source_id, name, url):
    url = validate_url(url)
    name = str(name).strip()
    if not name or len(name) > 80:
        raise ValueError('Nama sumbernya perlu diisi, maksimal 80 karakter.')
    with connection() as db:
        db.execute('INSERT INTO sources VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url', (source_id, name, url))


def _recording_data(value):
    """Load one catalogue record with backward-compatible download defaults."""
    data = json.loads(value) if isinstance(value, str) else dict(value)
    try:
        data['download_count'] = max(0, int(data.get('download_count') or 0))
    except (TypeError, ValueError):
        data['download_count'] = 0
    data.setdefault('last_downloaded_at', None)
    return data


def save_recording(job, state, detail=''):
    # Never store playback URLs/credentials in catalogue or browser status.
    data = {key: job[key] for key in ('job_id', 'source', 'source_name', 'note', 'origin', 'storage', 'quality', 'output_format', 'compression', 'is_live', 'live_status', 'was_live', 'download_attempt', 'download_attempts', 'download_exit_code', 'requested_at', 'started_at', 'elapsed', 'size', 'filename', 'stop_reason', 'original_filename', 'original_size', 'requested_compression', 'processing_status', 'processing_error', 'processing_detail', 'progress_percent', 'progress_phase', 'eta_seconds', 'silent_video', 'attempt_root_id', 'retry_of', 'attempt_number', 'attempt_total', 'error_code', 'error_title', 'error_message') if key in job}
    data.update(state=state, detail=detail)
    if isinstance(job.get('source_metadata'), dict):
        data['source_metadata'] = {key: job['source_metadata'][key]
                                   for key in ('title', 'description', 'channel', 'upload_date', 'youtube_id',
                                               'uploader', 'uploader_id', 'timestamp', 'duration', 'id')
                                   if key in job['source_metadata']}
    with connection() as db:
        previous = db.execute('SELECT data FROM recordings WHERE id=?',
                              (job['job_id'],)).fetchone()
        previous_data = _recording_data(previous['data']) if previous else {}
        if 'source_metadata' not in data and previous_data.get('source_metadata') is not None:
            data['source_metadata'] = previous_data['source_metadata']
        data['download_count'] = previous_data.get('download_count', 0)
        data['last_downloaded_at'] = previous_data.get('last_downloaded_at')
        db.execute('INSERT INTO recordings VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, data=excluded.data', (job['job_id'], time.time(), json.dumps(data)))


def save_capture_request(job):
    """Persist retry inputs privately; URLs never join the public catalogue payload."""
    allowed = ('source', 'source_name', 'note', 'storage', 'archive', 'quality',
               'output_format', 'compression', 'is_live', 'url', 'stream_url')
    data = {key: job[key] for key in allowed if key in job}
    with connection() as db:
        db.execute('INSERT INTO capture_requests VALUES (?, ?, ?) '
                   'ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (job['job_id'], time.time(), json.dumps(data)))


def capture_request(job_id):
    """Return one private retry snapshot without exposing it through recordings()."""
    with connection() as db:
        row = db.execute('SELECT data FROM capture_requests WHERE id=?', (job_id,)).fetchone()
        return json.loads(row['data']) if row else None


def capture_request_ids():
    with connection() as db:
        return {row['id'] for row in db.execute('SELECT id FROM capture_requests')}


def save_archive_state(job_id, archive_status, **fields):
    """Update archive metadata without changing the producer's state."""
    allowed = {'archive_path', 'archive_error', 'archive_attempt',
               'archived_at', 'local_cleanup_after', 'archive_sha256'}
    with connection() as db:
        row = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        if not row:
            return
        data = json.loads(row['data'])
        data['archive_status'] = archive_status
        data.update({key: value for key, value in fields.items() if key in allowed})
        db.execute('UPDATE recordings SET updated=?, data=? WHERE id=?',
                   (time.time(), json.dumps(data), job_id))


def save_transcription_state(job_id, status, replace=False, guarded=False, request_id=None, publish=None, **fields):
    """Persist transcript metadata without changing the recording state."""
    allowed = {
        'requested_at', 'started_at', 'completed_at', 'processing_seconds',
        'language', 'language_probability', 'model', 'txt_filename',
        'srt_filename', 'vtt_filename', 'error', 'progress_percent', 'eta_seconds',
    }
    with connection() as db:
        if guarded:
            db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        if not row:
            return False
        data = json.loads(row['data'])
        current = data.get('transcript') or {}
        if guarded and (current.get('request_id') != request_id or current.get('status') in {'cancelled', 'completed'}):
            return False
        if publish:
            publish()
        transcript = {} if replace else dict(data.get('transcript') or {})
        transcript.update(status=status, updated_at=time.time())
        transcript.update({key: value for key, value in fields.items() if key in allowed})
        data['transcript'] = transcript
        db.execute('UPDATE recordings SET updated=?, data=? WHERE id=?',
                   (time.time(), json.dumps(data), job_id))
        return True


def recording(job_id):
    """Return one catalogue item, including transcript metadata."""
    with connection() as db:
        row = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        return _recording_data(row['data']) if row else None


def recordings():
    with connection() as db:
        rows = [_recording_data(row['data']) for row in db.execute('SELECT data FROM recordings ORDER BY updated DESC')]
        by_job = {}
        for marker in db.execute('SELECT job_id, seconds, note FROM markers ORDER BY seconds, id'):
            by_job.setdefault(marker['job_id'], []).append(dict(seconds=marker['seconds'], note=marker['note']))
        for row in rows:
            row['markers'] = by_job.get(row['job_id'], [])
        return rows


def record_download(job_id, downloaded_at=None):
    """Atomically record one successful attachment response without reordering History."""
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        if not row:
            return None
        data = _recording_data(row['data'])
        data['download_count'] += 1
        data['last_downloaded_at'] = time.time() if downloaded_at is None else float(downloaded_at)
        db.execute('UPDATE recordings SET data=? WHERE id=?', (json.dumps(data), job_id))
        return data['download_count'], data['last_downloaded_at']


def delete_recording(job_id):
    """Remove one catalogue row and its markers after its file is deleted."""
    with connection() as db:
        db.execute('DELETE FROM markers WHERE job_id=?', (job_id,))
        db.execute('DELETE FROM capture_requests WHERE id=?', (job_id,))
        deleted = db.execute('DELETE FROM recordings WHERE id=?', (job_id,)).rowcount
        return bool(deleted)


def rename_recording(job_id, filename, transcript_filenames=None):
    """Update the final local filename without replacing other metadata."""
    with connection() as db:
        row = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        if not row:
            return False
        data = json.loads(row['data'])
        data['filename'] = filename
        if transcript_filenames and data.get('transcript'):
            data['transcript'].update(transcript_filenames)
        db.execute('UPDATE recordings SET updated=?, data=? WHERE id=?',
                   (time.time(), json.dumps(data), job_id))
        return True


def disk_status(path=None):
    """Inspect the actual downloads filesystem, not the container root disk."""
    minimum = float(os.getenv('MIN_FREE_DISK_GB', '2'))
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError('MIN_FREE_DISK_GB harus lebih besar dari nol.')
    threshold = int(minimum * 1024 ** 3)
    try:
        usage = shutil.disk_usage(path or os.getenv('DOWNLOAD_DIR', '/downloads'))
        return dict(available=True, total=usage.total, free=usage.free,
                    minimum=threshold, can_record=usage.free >= threshold)
    except OSError:
        return dict(available=False, total=0, free=0, minimum=threshold, can_record=False)


def add_marker(job_id, seconds, note):
    with connection() as db:
        db.execute('INSERT INTO markers (job_id, seconds, note, created) VALUES (?, ?, ?, ?)',
                   (job_id, round(max(0, seconds), 1), note[:300], time.time()))


def markers(job_id):
    with connection() as db:
        return [dict(row) for row in db.execute('SELECT seconds, note FROM markers WHERE job_id=? ORDER BY seconds, id', (job_id,))]
