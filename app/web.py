"""Authenticated control panel. Capture work always belongs to the existing worker."""
import hmac
import json
import os
import secrets
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path

import redis
from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.exceptions import HTTPException
try:
    from . import storage
except ImportError:
    import storage

ADMIT = """
if redis.call('EXISTS', 'worker:heartbeat') == 0 then return 'offline' end
if redis.call('EXISTS', 'capture:owner') == 1 or redis.call('LLEN', 'download_queue') > 0 then return 'busy' end
redis.call('SET', 'capture:owner', ARGV[1], 'EX', 30)
redis.call('SET', 'active:web', ARGV[1], 'EX', 30)
redis.call('SET', 'state:' .. ARGV[1], 'starting', 'EX', 86400)
redis.call('RPUSH', 'download_queue', ARGV[2])
return 'accepted'
"""
STOP = """
if redis.call('GET', 'capture:owner') ~= ARGV[1] then return 0 end
local state = redis.call('GET', 'state:' .. ARGV[1])
if state == 'finalizing' or state == 'ready' or state == 'failed' then return 0 end
redis.call('SET', 'stop:' .. ARGV[1], '1', 'EX', 300)
return 1
"""


def create_app(client=None):
    app = Flask(__name__, static_folder='static')
    app.secret_key = os.getenv('WEB_SECRET_KEY') or secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=16384, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.getenv('WEB_COOKIE_SECURE') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    r = client or redis.Redis(host=os.getenv('REDIS_HOST', 'redis'), decode_responses=True,
                             socket_connect_timeout=3, socket_timeout=3)

    @app.before_request
    def authorize():
        if not request.path.startswith('/api/'):
            return
        if request.path != '/api/login' and not session.get('operator'):
            return jsonify(error='Silakan masuk untuk melanjutkan.'), 401
        if request.method != 'GET' and request.path != '/api/login':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
                return jsonify(error='Sesi kamu sudah berakhir. Silakan masuk lagi.'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        return response

    @app.errorhandler(redis.exceptions.RedisError)
    def redis_error(_):
        return jsonify(error='Layanan sedang tidak tersedia. Coba lagi sebentar.'), 503

    @app.errorhandler(ValueError)
    def validation_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if request.path.startswith('/api/'):
            return jsonify(error=exc.description), exc.code
        return exc

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        app.logger.exception('Unhandled web error')
        if request.path.startswith('/api/'):
            return jsonify(error='Terjadi kesalahan saat memproses permintaan. Coba lagi atau hubungi admin.'), 500
        return 'Internal server error', 500

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.post('/api/login')
    def login():
        password = os.getenv('WEB_PASSWORD', '')
        if not password:
            return jsonify(error='Password operator belum diatur. Hubungi admin.'), 503
        key = 'web:login:' + (request.remote_addr or 'unknown')
        attempts = r.incr(key)
        if attempts == 1:
            r.expire(key, 300)
        if attempts > 10:
            return jsonify(error='Terlalu banyak percobaan. Coba lagi dalam 5 menit.'), 429
        data = request.get_json() or {}
        if not hmac.compare_digest(str(data.get('password', '')).encode(), password.encode()):
            return jsonify(error='Password belum tepat. Silakan coba lagi.'), 401
        r.delete(key)
        session.clear()
        session.update(operator=True, csrf=secrets.token_hex(32))
        session.permanent = True
        return jsonify(csrf=session['csrf'])

    @app.get('/api/session')
    def current_session():
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/sources')
    def list_sources():
        return jsonify(sources=storage.sources())

    @app.post('/api/sources')
    def source_save():
        data = request.get_json() or {}
        source_id = str(data.get('id') or uuid.uuid4().hex)
        storage.save_source(source_id, data.get('name', ''), data.get('url', '').strip())
        return jsonify(id=source_id)

    @app.post('/api/check')
    def check():
        url = storage.validate_url((request.get_json() or {}).get('url', '').strip())
        # One bounded probe at a time; diagnostics can contain private tokens.
        token = uuid.uuid4().hex
        if not r.set('web:probe', token, nx=True, ex=20):
            return jsonify(error='Sumber lain sedang diperiksa. Tunggu sebentar lalu coba lagi.'), 409
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-rw_timeout', '8000000',
                                     '-show_entries', 'stream=codec_type,codec_name', '-of', 'json', url],
                                    capture_output=True, timeout=10)
            streams = json.loads(result.stdout).get('streams', []) if result.returncode == 0 else []
            if not any(s.get('codec_type') == 'video' for s in streams):
                return jsonify(error='Sumber belum bisa diakses atau tidak mengirim video.'), 422
            return jsonify(ok=True, codecs=[s.get('codec_name', '?') for s in streams])
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return jsonify(error='Sumber belum merespons. Periksa alamat dan koneksi jaringan.'), 422
        finally:
            r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0", 1, 'web:probe', token)

    @app.post('/api/record')
    def record():
        data = request.get_json() or {}
        source = data.get('source')
        storage_target = data.get('storage', 'local')
        if storage_target not in {'local', 'archive'}:
            raise ValueError('Pilih lokasi penyimpanan yang tersedia.')
        archive_enabled = os.getenv('ARCHIVE_ENABLED', 'false').lower() in {'1', 'true', 'yes', 'on'}
        if storage_target == 'archive' and not archive_enabled:
            raise ValueError('Penyimpanan arsip belum diaktifkan oleh admin.')
        job = dict(job_id=uuid.uuid4().hex, source=source, origin='web', chat_id='web',
                   requested_at=time.time(), note=str(data.get('note', '')).strip()[:500],
                   storage=storage_target, archive=storage_target == 'archive')
        if source == 'oryx':
            selected = next((s for s in storage.sources() if s['id'] == data.get('source_id')), None)
            if not selected:
                raise ValueError('Pilih sumber live terlebih dahulu.')
            job.update(stream_url=storage.validate_url(selected['url']), source_name=selected['name'])
        elif source == 'tiktok':
            job.update(url=storage.validate_tiktok_url(data.get('url', '').strip()), source_name='TikTok Live')
        elif source == 'youtube':
            job.update(url=storage.validate_url(data.get('url', '').strip(), youtube=True), source_name='YouTube')
        else:
            raise ValueError('Pilih sumber rekaman yang tersedia.')
        if not storage.disk_status()['can_record']:
            return jsonify(error='Ruang penyimpanan tidak cukup. Kosongkan ruang sebelum mulai merekam.'), 507
        result = r.eval(ADMIT, 0, job['job_id'], json.dumps(job))
        if result != 'accepted':
            return jsonify(error='Masih ada rekaman yang berjalan. Tunggu sampai selesai.' if result == 'busy' else 'Sistem perekam belum siap. Coba lagi sebentar atau hubungi admin.'), 409
        return jsonify(job_id=job['job_id']), 202

    @app.post('/api/stop')
    def stop():
        job_id = str((request.get_json() or {}).get('job_id', ''))
        if not r.eval(STOP, 0, job_id):
            return jsonify(error='Rekaman sudah berhenti atau sedang menyiapkan file. Tunggu status berikutnya.'), 409
        return jsonify(ok=True)

    @app.get('/api/status')
    def status():
        owner = r.get('capture:owner')
        info = json.loads(r.get('web:job:' + owner) or '{}') if owner else None
        if owner and not info:
            info = dict(job_id=owner, state=r.get('state:' + owner) or 'starting')
        if info:
            info['markers'] = storage.markers(owner)
        archive_enabled = os.getenv('ARCHIVE_ENABLED', 'false').lower() in {'1', 'true', 'yes', 'on'}
        return jsonify(active=info, online=bool(r.exists('worker:heartbeat')),
                       queued=r.llen('download_queue'), disk=storage.disk_status(),
                       archive_enabled=archive_enabled)

    @app.post('/api/markers')
    def mark():
        data = request.get_json() or {}
        job_id = str(data.get('job_id', ''))
        snapshot = r.eval("""
if redis.call('GET', 'capture:owner') ~= ARGV[1] then return nil end
if redis.call('GET', 'state:' .. ARGV[1]) ~= 'recording' then return nil end
if redis.call('EXISTS', 'stop:' .. ARGV[1]) == 1 then return nil end
return redis.call('GET', 'web:job:' .. ARGV[1])
""", 0, job_id)
        if not snapshot:
            return jsonify(error='Tanda momen hanya bisa disimpan saat rekaman berjalan.'), 409
        active = json.loads(snapshot)
        if not active.get('started_at'):
            return jsonify(error='Rekaman belum benar-benar dimulai. Tunggu sebentar lalu coba lagi.'), 409
        seconds = time.time() - active['started_at']
        storage.add_marker(job_id, seconds, str(data.get('note', '')).strip() or 'Momen penting')
        return jsonify(markers=storage.markers(job_id)), 201

    @app.get('/api/recordings')
    def history():
        rows = storage.recordings()
        owner = r.get('capture:owner')
        for row in rows:
            if row['state'] not in {'ready', 'failed'} and row['job_id'] != owner:
                row.update(state='interrupted', detail='Proses rekaman terputus sebelum file dinyatakan siap.')
        query = request.args.get('q', '').casefold().strip()
        source = request.args.get('source', '')
        state = request.args.get('state', '')
        date = request.args.get('date', '')
        if date:
            from datetime import datetime
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError:
                raise ValueError('Tanggal tidak valid.')
        rows = [row for row in rows
                if (not query or query in ' '.join(str(row.get(k, '')) for k in ('filename', 'note', 'source_name')).casefold())
                and (not source or row.get('source') == source)
                and (not state or row.get('state') == state)
                and (not date or time.strftime('%Y-%m-%d', time.localtime(row.get('requested_at', 0))) == date)]
        return jsonify(recordings=rows[:200], total=len(rows))

    @app.get('/api/files/<filename>')
    def download(filename):
        if not any(row.get('filename') == filename and row['state'] == 'ready' for row in storage.recordings()):
            return jsonify(error='File rekaman tidak ditemukan atau belum siap.'), 404
        return send_from_directory(Path(os.getenv('DOWNLOAD_DIR', '/downloads')), filename, as_attachment=True)

    def remove_recording(row):
        """Delete a local final file and its catalogue row, never its archive."""
        filename = row.get('filename')
        path = None
        if filename:
            root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
            path = root / filename
            if Path(filename).name != filename or path.parent.resolve() != root or path.is_symlink():
                raise ValueError('Nama file tidak valid.')
        file_exists = bool(path and path.is_file())
        if (file_exists and row.get('storage') == 'archive'
                and row.get('archive_status') != 'archived'):
            return False, 'File belum berhasil diarsipkan.'
        if path:
            path.unlink(missing_ok=True)
        storage.delete_recording(row['job_id'])
        r.delete('state:' + row['job_id'], 'web:job:' + row['job_id'],
                 'archive:state:' + row['job_id'])
        return True, ''

    @app.post('/api/recordings/delete')
    def delete_recordings():
        job_ids = (request.get_json() or {}).get('job_ids', [])
        if (not isinstance(job_ids, list) or not job_ids or len(job_ids) > 200
                or any(not isinstance(value, str) or not value or len(value) > 128
                       for value in job_ids)):
            raise ValueError('Pilih riwayat yang ingin dihapus.')
        rows = {row['job_id']: row for row in storage.recordings()
                if row['job_id'] in set(job_ids)}
        owner = r.get('capture:owner')
        deleted = []
        skipped = []
        for job_id in dict.fromkeys(job_ids):
            row = rows.get(job_id)
            if not row:
                skipped.append(dict(job_id=job_id, reason='Riwayat tidak ditemukan.'))
                continue
            if job_id == owner:
                skipped.append(dict(job_id=job_id, reason='Proses masih aktif.'))
                continue
            try:
                removed, reason = remove_recording(row)
            except OSError:
                removed, reason = False, 'File tidak bisa dihapus.'
            if removed:
                deleted.append(job_id)
            else:
                skipped.append(dict(job_id=job_id, reason=reason))
        return jsonify(deleted=deleted, skipped=skipped)

    @app.post('/api/files/<filename>/delete')
    def delete_download(filename):
        row = next((item for item in storage.recordings()
                    if item.get('filename') == filename and item.get('state') == 'ready'), None)
        if not row:
            return jsonify(error='File rekaman tidak ditemukan atau belum siap.'), 404
        if r.get('capture:owner') == row['job_id']:
            return jsonify(error='Rekaman yang masih aktif tidak bisa dihapus.'), 409
        try:
            removed, reason = remove_recording(row)
        except OSError:
            return jsonify(error='File tidak bisa dihapus. Periksa izin folder downloads.'), 500
        if not removed:
            return jsonify(error=reason + ' File lokal tidak dihapus.'), 409
        return jsonify(ok=True)

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=8080)
