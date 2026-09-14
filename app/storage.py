"""Persistent source settings and recording catalogue shared by web and worker."""
import json
import os
import sqlite3
import time
import shutil
import math
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
    try:
        with db:
            yield db
    finally:
        db.close()


def validate_url(url, youtube=False):
    if not isinstance(url, str) or len(url) > 4096 or any(c.isspace() for c in url):
        raise ValueError('URL tidak valid.')
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.scheme not in ({'http', 'https'} if youtube else {'http', 'https', 'rtmp', 'rtmps', 'srt'}):
        raise ValueError('Gunakan URL playback HTTP, HTTPS, RTMP, RTMPS, atau SRT.')
    if youtube and parsed.hostname.lower() not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
        raise ValueError('Masukkan URL YouTube yang valid.')
    if not youtube and parsed.path.endswith('.html'):
        raise ValueError('Gunakan URL stream, bukan halaman player.')
    return url


def sources():
    with connection() as db:
        # Seed once; editing this entry later takes precedence over .env.
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        if not db.execute("SELECT 1 FROM settings WHERE key='seeded'").fetchone():
            url = os.getenv('ORYX_STREAM_URL', '').strip()
            if url:
                db.execute('INSERT OR IGNORE INTO sources VALUES (?, ?, ?)', ('default', 'Oryx utama', url))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('seeded', '1')")
        return [dict(row) for row in db.execute('SELECT * FROM sources ORDER BY name')]


def save_source(source_id, name, url):
    validate_url(url)
    name = str(name).strip()
    if not name or len(name) > 80:
        raise ValueError('Nama sumber wajib diisi, maksimal 80 karakter.')
    with connection() as db:
        db.execute('INSERT INTO sources VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url', (source_id, name, url))


def save_recording(job, state, detail=''):
    # Never store playback URLs/credentials in catalogue or browser status.
    data = {key: job[key] for key in ('job_id', 'source', 'source_name', 'note', 'origin', 'requested_at', 'started_at', 'elapsed', 'size', 'filename', 'stop_reason') if key in job}
    data.update(state=state, detail=detail)
    with connection() as db:
        db.execute('INSERT INTO recordings VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, data=excluded.data', (job['job_id'], time.time(), json.dumps(data)))


def recordings():
    with connection() as db:
        rows = [json.loads(row['data']) for row in db.execute('SELECT data FROM recordings ORDER BY updated DESC')]
        by_job = {}
        for marker in db.execute('SELECT job_id, seconds, note FROM markers ORDER BY seconds, id'):
            by_job.setdefault(marker['job_id'], []).append(dict(seconds=marker['seconds'], note=marker['note']))
        for row in rows:
            row['markers'] = by_job.get(row['job_id'], [])
        return rows


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
