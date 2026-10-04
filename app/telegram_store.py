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


def tasks():
    with storage.connection() as db:
        tables(db)
        return [(row['id'], json.loads(row['data'])) for row in db.execute('SELECT * FROM telegram_tasks')]


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
        if inserted and watch['mode'] == 'first':
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
