"""Small private SQLite ledger; no capture or transcription processing here."""
import json
import logging
import os
import re
import time
try:
    from . import storage
except ImportError:
    import storage

log = logging.getLogger(__name__)


def web_destination(role):
    """Explicit role association only; never guess an owner from the allowlist."""
    if role not in {'user', 'admin'}:
        return None
    prefix = 'WEB_' + role.upper() + '_TELEGRAM_'
    user = os.getenv(prefix + 'USER_ID', '').strip()
    chat = os.getenv(prefix + 'CHAT_ID', '').strip()
    allowed = re.split(r'[,\s]+', os.getenv('TELEGRAM_ALLOWED_USER_IDS', '').strip())
    if not user.isdigit() or user not in allowed or not re.fullmatch(r'-?[1-9]\d*', chat):
        return None
    return dict(user_id=int(user), chat_id=int(chat))


def subscribe_web(job_id, role):
    """Optional routing must never fail an accepted capture or transcription."""
    destination = web_destination(role)
    if destination:
        try:
            subscribe(job_id, **destination, owner_type='web', owner_id=role,
                      monitor_capture=True, monitor_transcript=True)
        except Exception:
            log.warning('Web activity subscription unavailable job=%s', job_id)


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


def update_watch(watch_id, **fields):
    """Merge notification/expiry checkpoints without overwriting discovery or cancel."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_watches WHERE id=?', (watch_id,)).fetchone()
        if not row:
            return
        watch = json.loads(row['data'])
        if fields.get('status') == 'expired' and watch['status'] != 'active':
            return
        watch.update(fields)
        db.execute('UPDATE telegram_watches SET data=? WHERE id=?', (json.dumps(watch), watch_id))


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
        watch['cancelled_at'] = time.time()
        db.execute('UPDATE telegram_watches SET data=? WHERE id=?', (json.dumps(watch), watch_id))
        return watch


def tasks():
    with storage.connection() as db:
        tables(db)
        return [(row['id'], json.loads(row['data'])) for row in db.execute('SELECT * FROM telegram_tasks')]


def task(key):
    with storage.connection() as db:
        tables(db)
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        return json.loads(row['data']) if row else None


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


def update_task(key, **fields):
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        if row:
            value = json.loads(row['data'])
            value.update(fields)
            db.execute('UPDATE telegram_tasks SET data=? WHERE id=?', (json.dumps(value), key))


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
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        value = json.loads(row['data']) if row else dict(job_id=job_id, chat_id=chat_id, user_id=user_id)
        if not row:
            # The same resource in the same chat has already notified this event.
            for peer in db.execute('SELECT data FROM telegram_tasks WHERE id LIKE ?',
                                   (f'subscription:{job_id}:%',)):
                peer = json.loads(peer['data'])
                if peer.get('chat_id') == chat_id:
                    value.update({name: peer[name] for name in ('capture_started', 'capture_done',
                                  'transcript_notice', 'transcript_request_notice') if name in peer})
        value.update(fields)
        db.execute('INSERT INTO telegram_tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (key, json.dumps(value)))
    return key


def checkpoint_subscription(key, **fields):
    """Persist each successful send immediately, shared by subscribers in one chat."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        if not row:
            return
        task = json.loads(row['data'])
        for peer in db.execute('SELECT id, data FROM telegram_tasks WHERE id LIKE ?',
                               (f"subscription:{task['job_id']}:%",)).fetchall():
            value = json.loads(peer['data'])
            if value.get('chat_id') == task['chat_id']:
                value.update(fields)
                db.execute('UPDATE telegram_tasks SET data=? WHERE id=?',
                           (json.dumps(value), peer['id']))
