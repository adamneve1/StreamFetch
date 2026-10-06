"""Small private SQLite ledger; no capture or transcription processing here."""
import json
try:
    from . import storage
except ImportError:
    import storage


def tables(db):
    db.execute('CREATE TABLE IF NOT EXISTS telegram_watches (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS telegram_tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)')


def watches():
    with storage.connection() as db:
        tables(db)
        return [json.loads(row['data']) for row in db.execute('SELECT data FROM telegram_watches')]


def save_watch(watch):
    with storage.connection() as db:
        tables(db)
        db.execute('INSERT INTO telegram_watches VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (watch['id'], json.dumps(watch)))


def cancel_watch(watch_id, user_id=None, chat_id=None, owner_type=None, owner_id=None):
    """Cancel discovery in the shared ledger without touching queued/running jobs."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_watches WHERE id=?', (watch_id,)).fetchone()
        watch = json.loads(row['data']) if row else None
        if not watch or (user_id is not None and watch.get('user_id') != user_id) or (
                chat_id is not None and watch.get('chat_id') != chat_id) or (
                owner_type is not None and watch.get('owner_type') != owner_type) or (
                owner_id is not None and watch.get('owner_id') != owner_id):
            return None
        watch['status'] = 'cancelled'
        db.execute('UPDATE telegram_watches SET data=? WHERE id=?', (json.dumps(watch), watch_id))
        return watch


def tasks():
    with storage.connection() as db:
        tables(db)
        return [(row['id'], json.loads(row['data'])) for row in db.execute('SELECT * FROM telegram_tasks')]


def discovery_state(watch_id, error, checked_at):
    """Update diagnostics without overwriting a concurrent cancellation/claim."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_watches WHERE id=?', (watch_id,)).fetchone()
        if not row:
            return
        watch = json.loads(row['data'])
        watch.update(discovery_error=error, last_checked_at=checked_at)
        db.execute('UPDATE telegram_watches SET data=? WHERE id=?', (json.dumps(watch), watch_id))


def save_task(key, value):
    with storage.connection() as db:
        tables(db)
        db.execute('INSERT INTO telegram_tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (key, json.dumps(value)))


def claim_video(watch_id, video_id, task):
    """Atomically deduplicate the actual video and finish first-live discovery."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_watches WHERE id=?', (watch_id,)).fetchone()
        watch = json.loads(row['data']) if row else {}
        if watch.get('status') != 'active':
            return False
        inserted = db.execute('INSERT OR IGNORE INTO telegram_tasks VALUES (?, ?)',
                              ('capture:' + video_id, json.dumps(task))).rowcount
        if inserted:
            job = task.get('job') or {}
            watch['last_capture'] = dict(video_id=video_id, job_id=job.get('job_id'),
                                         detected_at=job.get('requested_at'))
            if watch['mode'] == 'first':
                watch['status'] = 'finished'
            db.execute('UPDATE telegram_watches SET data=? WHERE id=?', (json.dumps(watch), watch_id))
        return bool(inserted)


def subscribe(job_id, chat_id, user_id, **fields):
    key = f'subscription:{job_id}:{chat_id}:{user_id}'
    with storage.connection() as db:
        tables(db)
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        value = json.loads(row['data']) if row else dict(job_id=job_id, chat_id=chat_id, user_id=user_id)
        value.update(fields)
        db.execute('INSERT INTO telegram_tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (key, json.dumps(value)))
    return key
