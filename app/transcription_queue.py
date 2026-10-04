"""Shared durable transcription admission for web and Telegram."""
import json
import os
import time
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
        if previous == 'failed':
            client.delete('transcription:guard:' + job_id)
        now = time.time()
        row['transcript'] = dict(status='queued', updated_at=now, requested_at=now,
                                 model=os.getenv('WHISPER_MODEL', 'small'), error='')
        db.execute('UPDATE recordings SET updated=?, data=? WHERE id=?',
                   (now, json.dumps(row), job_id))
    result = client.eval(TRANSCRIPTION_ADMIT, 0, job_id, json.dumps({'job_id': job_id}))
    return 'queued', result == 'accepted'
