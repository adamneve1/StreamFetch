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
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from . import quality, storage, transcript_reader, transcription_queue
except ImportError:
    import quality
    import storage
    import transcript_reader
    import transcription_queue

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


def positive_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def create_app(client=None):
    app = Flask(__name__, static_folder='static')
    app.secret_key = os.getenv('WEB_SECRET_KEY') or secrets.token_hex(32)
    app.config.update(MAX_CONTENT_LENGTH=16384, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.getenv('WEB_COOKIE_SECURE') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    r = client or redis.Redis(host=os.getenv('REDIS_HOST', 'redis'), decode_responses=True,
                             socket_connect_timeout=3, socket_timeout=3)

    def credential(role):
        password_hash = storage.setting(role + '_password_hash')
        return password_hash, os.getenv('WEB_ADMIN_PASSWORD' if role == 'admin' else 'WEB_PASSWORD', '')

    def password_matches(role, password):
        password_hash, fallback = credential(role)
        if password_hash:
            return check_password_hash(password_hash, password)
        return bool(fallback) and hmac.compare_digest(password.encode(), fallback.encode())

    def auth_version(role):
        return int(storage.setting(role + '_auth_version', '0'))

    def require_admin():
        if session.get('role') != 'admin':
            return jsonify(error='Fitur ini khusus admin, ya.'), 403

    @app.before_request
    def authorize():
        if not request.path.startswith('/api/'):
            return
        if request.path != '/api/login' and not session.get('operator'):
            return jsonify(error='Masuk dulu untuk lanjut, ya.'), 401
        if request.path != '/api/login' and session.get('auth_version') != auth_version(session.get('role', 'user')):
            session.clear()
            return jsonify(error='Password akun ini sudah diganti. Yuk, masuk lagi.'), 401
        if request.method != 'GET' and request.path != '/api/login':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
                return jsonify(error='Sesi kamu sudah habis. Masuk lagi, ya.'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        if request.endpoint == 'view_transcript':
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self' https://www.youtube.com; "
                "frame-src https://www.youtube.com; style-src 'self'; frame-ancestors 'none'")
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    @app.errorhandler(redis.exceptions.RedisError)
    def redis_error(_):
        return jsonify(error='Layanannya lagi tidak tersedia. Coba lagi sebentar, ya.'), 503

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
            return jsonify(error='Ada yang bermasalah saat memproses permintaan. Coba lagi, atau hubungi admin kalau masih terjadi.'), 500
        return 'Internal server error', 500

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.post('/api/login')
    def login():
        if not any(credential(role)[0] or credential(role)[1] for role in ('user', 'admin')):
            return jsonify(error='Password masuknya belum diatur. Minta bantuan admin, ya.'), 503
        key = 'web:login:' + (request.remote_addr or 'unknown')
        attempts = r.incr(key)
        if attempts == 1:
            r.expire(key, 300)
        if attempts > 10:
            return jsonify(error='Percobaannya terlalu banyak. Tunggu 5 menit, lalu coba lagi.'), 429
        data = request.get_json() or {}
        supplied = str(data.get('password', ''))
        role = 'admin' if password_matches('admin', supplied) else 'user' if password_matches('user', supplied) else None
        if not role:
            return jsonify(error='Password-nya belum cocok. Coba lagi, ya.'), 401
        r.delete(key)
        session.clear()
        session.update(operator=True, role=role, auth_version=auth_version(role), csrf=secrets.token_hex(32))
        session.permanent = True
        return jsonify(csrf=session['csrf'], role=role, is_admin=role == 'admin')

    @app.get('/api/session')
    def current_session():
        return jsonify(csrf=session['csrf'], role=session.get('role', 'user'),
                       is_admin=session.get('role') == 'admin')

    @app.post('/api/admin/password')
    def change_password():
        denied = require_admin()
        if denied:
            return denied
        data = request.get_json() or {}
        current = str(data.get('current_password', ''))
        target = str(data.get('target', 'user'))
        new_password = str(data.get('new_password', ''))
        if not password_matches('admin', current):
            return jsonify(error='Password admin yang sekarang belum cocok.'), 403
        if target not in {'user', 'admin'}:
            raise ValueError('Pilih akun yang ingin diubah.')
        if len(new_password) < 8 or len(new_password) > 256:
            raise ValueError('Password baru perlu 8 sampai 256 karakter.')
        other_role = 'admin' if target == 'user' else 'user'
        if password_matches(other_role, new_password):
            raise ValueError('Password admin dan pengguna harus berbeda.')
        storage.save_setting(target + '_password_hash', generate_password_hash(new_password))
        version = auth_version(target) + 1
        storage.save_setting(target + '_auth_version', version)
        if target == 'admin':
            session['auth_version'] = version
        return jsonify(ok=True)

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
            return jsonify(error='Ada sumber lain yang sedang dicek. Tunggu sebentar, lalu coba lagi.'), 409
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-rw_timeout', '8000000',
                                     '-show_entries', 'stream=codec_type,codec_name', '-of', 'json', url],
                                    capture_output=True, timeout=10)
            streams = json.loads(result.stdout).get('streams', []) if result.returncode == 0 else []
            if not any(s.get('codec_type') == 'video' for s in streams):
                return jsonify(error='Sumbernya belum bisa diakses atau belum mengirim video.'), 422
            return jsonify(ok=True, codecs=[s.get('codec_name', '?') for s in streams])
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return jsonify(error='Sumbernya belum merespons. Cek lagi link dan koneksi jaringannya, ya.'), 422
        finally:
            r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0", 1, 'web:probe', token)

    @app.post('/api/estimate')
    def estimate():
        data = request.get_json() or {}
        source = data.get('source')
        output_format = quality.validate_format(data.get('format', 'mp4'))
        selected_quality = quality.validate(data.get('quality', 'best'))
        compression = quality.validate_preset(data.get('compression', 'original'))
        if output_format == 'mp3':
            if source != 'youtube':
                raise ValueError('Format MP3 hanya tersedia untuk YouTube.')
            selected_quality = 'best'
            compression = 'original'
        token = uuid.uuid4().hex
        if not r.set('web:estimate', token, nx=True, ex=35):
            return jsonify(error='Ada ukuran lain yang sedang dihitung. Tunggu sebentar, ya.'), 409
        try:
            if source == 'oryx':
                if selected_quality != 'best':
                    raise ValueError('Stream langsung memakai kualitas asli dari sumber.')
                selected = next((item for item in storage.sources()
                                 if item['id'] == data.get('source_id')), None)
                if not selected:
                    raise ValueError('Pilih sumber live dulu, ya.')
                url = storage.validate_url(selected['url'])
                result = subprocess.run([
                    'ffprobe', '-v', 'error', '-rw_timeout', '8000000',
                    '-show_entries', 'format=bit_rate:stream=codec_type,width,height,bit_rate',
                    '-of', 'json', url,
                ], capture_output=True, timeout=10)
                metadata = json.loads(result.stdout) if result.returncode == 0 else {}
                streams = metadata.get('streams', [])
                if not any(item.get('codec_type') == 'video' for item in streams):
                    return jsonify(error='Bitrate sumber belum bisa diperiksa.'), 422
                format_rate = positive_int((metadata.get('format') or {}).get('bit_rate'))
                stream_rate = sum(positive_int(item.get('bit_rate')) for item in streams)
                bits_per_second = format_rate or stream_rate
                heights = [positive_int(item.get('height')) for item in streams]
                return jsonify(estimated_bytes=None,
                               bytes_per_hour=int(bits_per_second / 8 * 3600) if bits_per_second else None,
                               height=max(heights, default=None), is_live=True,
                               quality='best', compression=compression)

            if source == 'youtube':
                url = storage.validate_url(str(data.get('url', '')).strip(), youtube=True)
            elif source == 'tiktok':
                url = storage.validate_tiktok_url(str(data.get('url', '')).strip())
            else:
                raise ValueError('Pilih dulu sumber yang mau dicek.')
            result = subprocess.run([
                'yt-dlp', '--dump-single-json', '--skip-download', '--no-playlist',
                '--no-warnings', '-f',
                quality.ytdlp_selector(selected_quality, output_format), url,
            ], capture_output=True, timeout=25)
            if result.returncode:
                return jsonify(error='Ukuran belum bisa diperkirakan dari sumber ini.'), 422
            metadata = json.loads(result.stdout)
            return jsonify(**quality.selected_media_info(metadata), quality=selected_quality,
                           format=output_format, compression=compression)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return jsonify(error='Ukuran belum bisa diperkirakan dari sumber ini.'), 422
        except ValueError:
            raise
        finally:
            r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0", 1, 'web:estimate', token)

    @app.post('/api/record')
    def record():
        data = request.get_json() or {}
        source = data.get('source')
        output_format = quality.validate_format(data.get('format', 'mp4'))
        selected_quality = quality.validate(data.get('quality', 'best'))
        compression = quality.validate_preset(data.get('compression', 'original'))
        if output_format == 'mp3':
            if source != 'youtube':
                raise ValueError('Format MP3 hanya tersedia untuk YouTube.')
            selected_quality = 'best'
            compression = 'original'
        if source == 'oryx' and selected_quality != 'best':
            raise ValueError('Stream langsung direkam dengan kualitas asli dari sumber.')
        storage_target = data.get('storage', 'local')
        if storage_target not in {'local', 'archive'}:
            raise ValueError('Pilih lokasi penyimpanan yang tersedia.')
        archive_enabled = os.getenv('ARCHIVE_ENABLED', 'false').lower() in {'1', 'true', 'yes', 'on'}
        if storage_target == 'archive' and not archive_enabled:
            raise ValueError('Penyimpanan arsip belum diaktifkan oleh admin.')
        job = dict(job_id=uuid.uuid4().hex, source=source, origin='web', chat_id='web',
                   requested_at=time.time(), note=str(data.get('note', '')).strip()[:500],
                   storage=storage_target, archive=storage_target == 'archive',
                   quality=selected_quality, output_format=output_format,
                   compression=compression)
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
            return jsonify(error='Ruang penyimpanannya hampir habis. Kosongkan dulu sebelum mulai merekam.'), 507
        result = r.eval(ADMIT, 0, job['job_id'], json.dumps(job))
        if result != 'accepted':
            return jsonify(error='Masih ada rekaman yang berjalan. Tunggu sampai selesai, ya.' if result == 'busy' else 'Perekamnya belum siap. Coba lagi sebentar atau hubungi admin.'), 409
        return jsonify(job_id=job['job_id']), 202

    @app.post('/api/stop')
    def stop():
        job_id = str((request.get_json() or {}).get('job_id', ''))
        if not r.eval(STOP, 0, job_id):
            return jsonify(error='Rekamannya sudah berhenti atau sedang menyiapkan file. Tunggu sebentar, ya.'), 409
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
            return jsonify(error='Rekamannya belum benar-benar mulai. Tunggu sebentar, lalu coba lagi.'), 409
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

    @app.post('/api/recordings/<job_id>/transcript')
    def create_transcript(job_id):
        row = storage.recording(job_id)
        if (not row or row.get('state') != 'ready' or not row.get('filename')
                or Path(row['filename']).suffix.lower() != '.mp4'):
            return jsonify(error='Rekaman tidak ditemukan atau belum siap.'), 404
        _, created = transcription_queue.enqueue(r, job_id)
        if not created:
            return jsonify(error='Transkrip untuk rekaman ini sudah ada atau sedang diproses.'), 409
        return jsonify(job_id=job_id, status='queued'), 202

    def transcript_file(job_id, kind, attachment):
        row = storage.recording(job_id)
        transcript = (row or {}).get('transcript') or {}
        if transcript.get('status') != 'completed':
            return jsonify(error='Transkrip belum selesai atau tidak ditemukan.'), 404
        keys = {'txt': 'txt_filename', 'srt': 'srt_filename',
                'vtt': 'vtt_filename'}
        key = keys.get(kind)
        if not key:
            return jsonify(error='Jenis file transkrip tidak valid.'), 404
        filename = transcript.get(key)
        if not filename or Path(filename).name != filename:
            return jsonify(error='File transkrip tidak ditemukan.'), 404
        mimetype = {'txt': 'text/plain; charset=utf-8',
                    'srt': 'application/x-subrip; charset=utf-8',
                    'vtt': 'text/vtt; charset=utf-8'}[kind]
        return send_from_directory(Path(os.getenv('DOWNLOAD_DIR', '/downloads')), filename,
                                   as_attachment=attachment, mimetype=mimetype,
                                   download_name=filename)

    @app.get('/api/recordings/<job_id>/transcript/view')
    def view_transcript(job_id):
        row = storage.recording(job_id)
        if not row or (row.get('transcript') or {}).get('status') != 'completed':
            return jsonify(error='Transkrip belum selesai atau tidak ditemukan.'), 404
        return app.send_static_file('transcript.html')

    @app.get('/api/recordings/<job_id>/transcript/data')
    def transcript_data(job_id):
        row = storage.recording(job_id)
        if not row or (row.get('transcript') or {}).get('status') != 'completed':
            return jsonify(error='Transkrip belum selesai atau tidak ditemukan.'), 404
        data = transcript_reader.reader_data(row, os.getenv('DOWNLOAD_DIR', '/downloads'))
        if not data['exports']:
            return jsonify(error='File transkrip tidak ditemukan.'), 404
        return jsonify(data)

    @app.get('/api/recordings/<job_id>/transcript/txt')
    def download_transcript_txt(job_id):
        return transcript_file(job_id, 'txt', True)

    @app.get('/api/recordings/<job_id>/transcript/srt')
    def download_transcript_srt(job_id):
        return transcript_file(job_id, 'srt', True)

    @app.get('/api/recordings/<job_id>/transcript/vtt')
    def download_transcript_vtt(job_id):
        return transcript_file(job_id, 'vtt', True)

    @app.get('/api/files/<filename>')
    def download(filename):
        if not any(row.get('filename') == filename and row['state'] == 'ready' for row in storage.recordings()):
            return jsonify(error='File rekaman tidak ditemukan atau belum siap.'), 404
        return send_from_directory(Path(os.getenv('DOWNLOAD_DIR', '/downloads')), filename, as_attachment=True)

    def remove_recording(row):
        """Delete a local final file and its catalogue row, never its archive."""
        if (row.get('transcript') or {}).get('status') in {'queued', 'transcribing'}:
            return False, 'Transkripsi masih berjalan.'
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
        transcript = row.get('transcript') or {}
        root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
        for key in ('txt_filename', 'srt_filename', 'vtt_filename'):
            sidecar = transcript.get(key)
            if sidecar and Path(sidecar).name == sidecar:
                candidate = root / sidecar
                if candidate.parent.resolve() == root and not candidate.is_symlink():
                    candidate.unlink(missing_ok=True)
        storage.delete_recording(row['job_id'])
        r.delete('state:' + row['job_id'], 'web:job:' + row['job_id'],
                 'archive:state:' + row['job_id'],
                 'transcription:state:' + row['job_id'],
                 'transcription:guard:' + row['job_id'])
        return True, ''

    @app.post('/api/recordings/delete')
    def delete_recordings():
        denied = require_admin()
        if denied:
            return denied
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

    @app.post('/api/recordings/<job_id>/rename')
    def rename_recording(job_id):
        row = next((item for item in storage.recordings()
                    if item.get('job_id') == job_id and item.get('state') == 'ready'), None)
        if not row or not row.get('filename'):
            return jsonify(error='File rekaman tidak ditemukan atau belum siap.'), 404
        if r.get('capture:owner') == job_id:
            return jsonify(error='Proses yang masih aktif tidak bisa diubah namanya.'), 409
        if (row.get('transcript') or {}).get('status') in {'queued', 'transcribing'}:
            return jsonify(error='Tunggu transkripsi selesai sebelum mengubah nama.'), 409
        if (row.get('storage') == 'archive'
                and row.get('archive_status') != 'archived'):
            return jsonify(error='Tunggu proses arsip selesai sebelum mengubah nama.'), 409

        old_name = row['filename']
        new_name = str((request.get_json() or {}).get('filename', '')).strip()
        if new_name and not Path(new_name).suffix:
            new_name += Path(old_name).suffix
        if (not new_name or len(new_name.encode('utf-8')) > 240
                or Path(new_name).name != new_name
                or any(ord(char) < 32 or char == '\x7f' for char in new_name)
                or Path(new_name).suffix.lower() != Path(old_name).suffix.lower()):
            raise ValueError('Nama file tidak valid. Gunakan nama biasa tanpa folder dan jangan ubah ekstensi.')

        root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
        source = root / old_name
        destination = root / new_name
        if (source.parent.resolve() != root or destination.parent.resolve() != root
                or source.is_symlink() or destination.is_symlink()):
            raise ValueError('Nama file tidak valid.')
        if not source.is_file():
            return jsonify(error='File lokalnya sudah tidak ada. Kamu bisa menghapus riwayat ini kalau sudah tidak diperlukan.'), 404
        if destination != source and destination.exists():
            return jsonify(error='Nama itu sudah dipakai file lain. Coba nama yang berbeda, ya.'), 409
        if destination == source:
            return jsonify(filename=new_name)
        transcript = row.get('transcript') or {}
        sidecar_moves = []
        if transcript.get('status') == 'completed':
            for kind, suffix in (('txt_filename', '.txt'), ('srt_filename', '.srt'),
                                 ('vtt_filename', '.vtt')):
                old_sidecar = transcript.get(kind)
                if old_sidecar:
                    old_path = root / old_sidecar
                    new_path = destination.with_suffix(suffix)
                    if (Path(old_sidecar).name != old_sidecar or old_path.parent.resolve() != root
                            or old_path.is_symlink() or new_path.exists()):
                        return jsonify(error='File transkrip tidak bisa ikut diubah namanya.'), 409
                    if old_path.is_file():
                        sidecar_moves.append((kind, old_path, new_path))
        try:
            source.rename(destination)
            try:
                for _, old_path, new_path in sidecar_moves:
                    old_path.rename(new_path)
                transcript_names = {kind: new_path.name for kind, _, new_path in sidecar_moves}
                storage.rename_recording(job_id, new_name, transcript_names)
            except Exception:
                for _, old_path, new_path in reversed(sidecar_moves):
                    if new_path.exists():
                        new_path.rename(old_path)
                destination.rename(source)
                raise
        except OSError:
            return jsonify(error='Nama file tidak bisa diubah. Periksa izin folder downloads.'), 500
        return jsonify(filename=new_name)

    @app.post('/api/files/<filename>/delete')
    def delete_download(filename):
        denied = require_admin()
        if denied:
            return denied
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
