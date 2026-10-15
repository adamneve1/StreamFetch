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
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_workspaces (
               chat_id TEXT NOT NULL, user_id TEXT NOT NULL,
               workspace TEXT NOT NULL, generation INTEGER NOT NULL,
               updated REAL NOT NULL, PRIMARY KEY (chat_id, user_id))''')


def _workspace_row(db, chat_id, user_id):
    db.execute(
        'INSERT OR IGNORE INTO telegram_workspaces VALUES (?, ?, ?, ?, ?)',
        (str(chat_id), str(user_id), 'download', 0, time.time()),
    )
    row = db.execute(
        'SELECT workspace, generation FROM telegram_workspaces WHERE chat_id=? AND user_id=?',
        (str(chat_id), str(user_id)),
    ).fetchone()
    return dict(workspace=row['workspace'], generation=row['generation'])


def workspace(chat_id, user_id):
    """Return a durable routing snapshot, creating the Download default."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        return _workspace_row(db, chat_id, user_id)


def workspace_matches(chat_id, user_id, snapshot):
    """Check whether a pending workspace UI still belongs to the active epoch."""
    with storage.connection() as db:
        tables(db)
        current = _workspace_row(db, chat_id, user_id)
        return (current['workspace'] == snapshot.get('workspace')
                and current['generation'] == snapshot.get('generation'))


def switch_workspace(chat_id, user_id, target, expected_generation=None):
    """Atomically switch workspace and invalidate pending transcript consent UI."""
    if target not in {'download', 'transcript'}:
        raise ValueError('Unknown Telegram workspace')
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        current = _workspace_row(db, chat_id, user_id)
        if (expected_generation is not None
                and current['generation'] != expected_generation):
            return current, False
        generation = current['generation'] + 1
        db.execute(
            '''UPDATE telegram_workspaces SET workspace=?, generation=?, updated=?
               WHERE chat_id=? AND user_id=?''',
            (target, generation, time.time(), str(chat_id), str(user_id)),
        )
        # Probes keep running; only consent UI is removed. Marking a probe stale
        # lets its successful result retain its original owner without allowing
        # a later AI prompt to cross the workspace boundary.
        for row in db.execute("SELECT id, data FROM telegram_tasks WHERE id LIKE 'transcript-%:%'").fetchall():
            task = json.loads(row['data'])
            if (str(task.get('chat_id')) == str(chat_id)
                    and str(task.get('user_id')) == str(user_id)):
                if task.get('state') == 'awaiting_consent':
                    db.execute('DELETE FROM telegram_tasks WHERE id=?', (row['id'],))
                elif task.get('state') == 'probing':
                    task['workspace_stale'] = True
                    db.execute('UPDATE telegram_tasks SET data=? WHERE id=?',
                               (json.dumps(task), row['id']))
        return dict(workspace=target, generation=generation), True


def claim_ai_approval(key, chat_id, user_id, generation):
    """Atomically bind an AI approval to its task and active workspace epoch."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        current = _workspace_row(db, chat_id, user_id)
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        task = json.loads(row['data']) if row else None
        try:
            consent_ttl = max(60, int(os.getenv(
                'TRANSCRIPT_UPLOAD_CONSENT_TTL_SECONDS', '86400')))
        except ValueError:
            consent_ttl = 86400
        expired = bool(key.startswith('transcript-upload:') and task
                       and task.get('state') == 'awaiting_consent'
                       and time.time() - task.get('requested_at', 0) >= consent_ttl)
        if expired:
            db.execute('DELETE FROM telegram_tasks WHERE id=?', (key,))
            return None
        if (not task or current != {'workspace': 'transcript', 'generation': generation}
                or str(task.get('chat_id')) != str(chat_id)
                or str(task.get('user_id')) != str(user_id)
                or task.get('workspace_generation') != generation
                or task.get('state') != 'awaiting_consent'):
            return None
        task['state'] = 'ai_preparing'
        db.execute('UPDATE telegram_tasks SET data=? WHERE id=?', (json.dumps(task), key))
        return task


def cancel_pending_transcript(key, chat_id, user_id, generation):
    """Delete only the exact, still-current consent request."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        current = _workspace_row(db, chat_id, user_id)
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        task = json.loads(row['data']) if row else None
        if (not task or current != {'workspace': 'transcript', 'generation': generation}
                or str(task.get('chat_id')) != str(chat_id)
                or str(task.get('user_id')) != str(user_id)
                or task.get('workspace_generation') != generation
                or task.get('state') != 'awaiting_consent'):
            return False
        db.execute('DELETE FROM telegram_tasks WHERE id=?', (key,))
        return True


def set_transcript_consent(key, task):
    """Publish consent only if the probe still owns the active transcript epoch."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        current = _workspace_row(db, task['chat_id'], task['user_id'])
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        stored = json.loads(row['data']) if row else None
        if (not stored or stored.get('job_id') != task.get('job_id')
                or stored.get('state') != 'probing'
                or current != {'workspace': 'transcript',
                               'generation': task.get('workspace_generation')}):
            if (stored and stored.get('job_id') == task.get('job_id')
                    and stored.get('state') == 'probing'):
                db.execute('DELETE FROM telegram_tasks WHERE id=?', (key,))
            return False
        task = dict(task, state='awaiting_consent')
        db.execute('UPDATE telegram_tasks SET data=? WHERE id=?', (json.dumps(task), key))
        return True


def delete_task_if(key, job_id):
    """Delete a task only when an older concurrent probe still owns the key."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        value = json.loads(row['data']) if row else None
        if value and value.get('job_id') == job_id:
            db.execute('DELETE FROM telegram_tasks WHERE id=?', (key,))
            return True
        return False


def claim_transcript_task(key, value):
    """Claim one URL per workspace epoch, replacing only stale pending UI."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        current = json.loads(row['data']) if row else None
        stale_epoch = (current and current.get('workspace_generation')
                       != value.get('workspace_generation'))
        replace = bool(stale_epoch and (
            current.get('state') == 'awaiting_consent'
            or (current.get('state') == 'probing'
                and time.time() - current.get('requested_at', 0) > 120)))
        if current and not replace:
            return False
        db.execute('INSERT INTO telegram_tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (key, json.dumps(value)))
        return True


def claim_upload_task(key, value):
    """Atomically deduplicate a web upload while allowing terminal retries."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        found = db.execute('SELECT data FROM telegram_tasks WHERE id=?', (key,)).fetchone()
        current = json.loads(found['data']) if found else None
        if current:
            recording = db.execute('SELECT data FROM recordings WHERE id=?',
                                   (current.get('job_id'),)).fetchone()
            row = json.loads(recording['data']) if recording else {}
            status = (row.get('transcript') or {}).get('status')
            replace = (status in {'failed', 'cancelled'}
                       or (not recording and time.time() - current.get('requested_at', 0) > 120))
            if not replace:
                return current, False
        db.execute('INSERT INTO telegram_tasks VALUES (?, ?) '
                   'ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (key, json.dumps(value)))
        return value, True


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


def expire_upload_tasks(now=None):
    """Expire stale Telegram upload consent records; no media exists before consent."""
    now = time.time() if now is None else now
    try:
        ttl = max(60, int(os.getenv('TRANSCRIPT_UPLOAD_CONSENT_TTL_SECONDS', '86400')))
    except ValueError:
        ttl = 86400
    expired = []
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute(
                "SELECT id, data FROM telegram_tasks WHERE id LIKE 'transcript-upload:%'").fetchall():
            value = json.loads(row['data'])
            if (value.get('state') in {'awaiting_consent', 'ai_preparing'}
                    and now - value.get('requested_at', now) >= ttl):
                db.execute('DELETE FROM telegram_tasks WHERE id=?', (row['id'],))
                expired.append(row['id'])
    return expired


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


def claim_task(key, value):
    """Atomically reserve one private Telegram workflow."""
    with storage.connection() as db:
        tables(db)
        return bool(db.execute('INSERT OR IGNORE INTO telegram_tasks VALUES (?, ?)',
                               (key, json.dumps(value))).rowcount)


def delete_task(key):
    with storage.connection() as db:
        tables(db)
        db.execute('DELETE FROM telegram_tasks WHERE id=?', (key,))


def delete_tasks_for_job(job_id):
    """Remove workflow/subscription rows owned by one deleted catalogue job."""
    with storage.connection() as db:
        tables(db)
        db.execute('BEGIN IMMEDIATE')
        deleted = 0
        for row in db.execute('SELECT id, data FROM telegram_tasks').fetchall():
            try:
                task = json.loads(row['data'])
            except (TypeError, json.JSONDecodeError):
                continue
            if task.get('job_id') == job_id:
                deleted += db.execute('DELETE FROM telegram_tasks WHERE id=?',
                                      (row['id'],)).rowcount
        return deleted


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
