"""Shared durable transcription admission for web and Telegram."""
import json
import os
import time
import uuid
from pathlib import Path
try:
    from . import storage
except ImportError:
    import storage

TRANSCRIPTION_ADMIT = """
local guard = 'transcription:guard:' .. ARGV[1]
if redis.call('EXISTS', guard) == 1 then return 'duplicate' end
redis.call('SET', guard, 'queued')
redis.call('SET', 'transcription:state:' .. ARGV[1], 'queued')
redis.call('RPUSH', 'transcription_queue', ARGV[2])
return 'accepted'
"""


def enqueue(client, job_id):
    """Return (status, created); queued SQLite state survives Redis outages."""
    with storage.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        row = json.loads(found['data']) if found else {}
        if row.get('state') != 'ready' or Path(row.get('filename') or '').suffix.lower() != '.mp4':
            raise ValueError('Rekaman tidak ditemukan atau belum siap.')
        previous = (row.get('transcript') or {}).get('status')
        if previous in {'queued', 'transcribing', 'completed'}:
            return previous, False
        if previous in {'failed', 'cancelled'}:
            client.delete('transcription:guard:' + job_id)
        request_id = uuid.uuid4().hex
        now = time.time()
        row['transcript'] = dict(status='queued', request_id=request_id, updated_at=now, requested_at=now,
                                 model=os.getenv('WHISPER_MODEL', 'small'), error='')
        db.execute('UPDATE recordings SET updated=?, data=? WHERE id=?',
                   (now, json.dumps(row), job_id))
    result = client.eval(TRANSCRIPTION_ADMIT, 0, job_id, json.dumps({'job_id': job_id, 'request_id': request_id}))
    return 'queued', result == 'accepted'


def cancel(client, job_id, request_id=None):
    """SQLite arbitrates cancellation versus completion; never stop the capture."""
    with storage.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT data FROM recordings WHERE id=?', (job_id,)).fetchone()
        if not found:
            raise ValueError('Rekaman tidak ditemukan.')
        row = json.loads(found['data'])
        transcript = row.get('transcript') or {}
        if request_id is not None and transcript.get('request_id') != request_id:
            return transcript.get('status'), False
        if transcript.get('status') not in {'queued', 'transcribing'}:
            return transcript.get('status'), False
        transcript.update(status='cancelled', completed_at=time.time(), updated_at=time.time(),
                          error='', eta_seconds=None)
        row['transcript'] = transcript
        db.execute('UPDATE recordings SET updated=?, data=? WHERE id=?', (time.time(), json.dumps(row), job_id))
    # Durable cancellation is already visible to the worker even during a Redis outage.
    return 'cancelled', True
