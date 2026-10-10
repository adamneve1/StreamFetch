"""Authenticated control panel. Capture work always belongs to the existing worker."""
import hmac
import json
import math
import os
import secrets
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import redis
from flask import Flask, jsonify, redirect, request, session, send_from_directory, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from . import clipper, quality, storage, telegram_store, transcript_reader, transcription_queue, watch_service
except ImportError:
    import clipper
    import quality
    import storage
    import telegram_store
    import transcript_reader
    import transcription_queue
    import watch_service

ADMIT = """
if redis.call('EXISTS', 'worker:heartbeat') == 0 then return 'offline' end
if redis.call('EXISTS', 'capture:owner') == 1 or redis.call('LLEN', 'download_queue') > 0 then return 'busy' end
redis.call('SET', 'capture:owner', ARGV[1], 'EX', 30)
redis.call('SET', 'active:web', ARGV[1], 'EX', 30)
redis.call('SET', 'state:' .. ARGV[1], 'starting', 'EX', 86400)
redis.call('RPUSH', 'download_queue', ARGV[2])
return 'accepted'
"""
RETRY_ADMIT = """
if redis.call('EXISTS', 'worker:heartbeat') == 0 then return 'offline' end
if redis.call('EXISTS', 'retry:' .. ARGV[1]) == 1 then return 'duplicate' end
if redis.call('EXISTS', 'capture:owner') == 1 or redis.call('LLEN', 'download_queue') > 0 then return 'busy' end
redis.call('SET', 'retry:' .. ARGV[1], ARGV[2], 'EX', 86400)
redis.call('SET', 'capture:owner', ARGV[2], 'EX', 30)
redis.call('SET', 'active:web', ARGV[2], 'EX', 30)
redis.call('SET', 'state:' .. ARGV[2], 'starting', 'EX', 86400)
redis.call('RPUSH', 'download_queue', ARGV[3])
return 'accepted'
"""
STOP = """
if redis.call('GET', 'capture:owner') ~= ARGV[1] then return 0 end
local state = redis.call('GET', 'state:' .. ARGV[1])
if state == 'ready' or state == 'failed' then return 0 end
redis.call('SET', 'stop:' .. ARGV[1], '1', 'EX', 300)
return 1
"""


class HTTPSCookieSessionInterface(SecureCookieSessionInterface):
    """Mark cookies Secure for direct HTTPS and for explicitly configured proxies."""

    def get_cookie_secure(self, app):
        return super().get_cookie_secure(app) or request.is_secure


def session_secret():
    """Return a configured secret, or persist one in the mounted application data."""
    configured = os.getenv('WEB_SECRET_KEY', '').strip()
    if configured:
        return configured
    root = Path(os.getenv('DATA_DIR', '/data'))
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.web-secret-key'
    try:
        value = path.read_text(encoding='ascii').strip()
    except FileNotFoundError:
        value = secrets.token_hex(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            value = path.read_text(encoding='ascii').strip()
        else:
            with os.fdopen(descriptor, 'w', encoding='ascii') as secret_file:
                secret_file.write(value)
    if not value:
        raise RuntimeError(f'Session secret file is empty: {path}')
    return value


def positive_float_env(name, default):
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'{name} must be a positive number') from exc
    if value <= 0:
        raise RuntimeError(f'{name} must be a positive number')
    return value


def positive_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def create_app(client=None):
    app = Flask(__name__, static_folder='static')
    idle_lifetime = timedelta(hours=positive_float_env('SESSION_IDLE_HOURS', 1))
    absolute_lifetime = timedelta(days=positive_float_env('SESSION_ABSOLUTE_DAYS', 1))
    app.secret_key = session_secret()
    app.session_interface = HTTPSCookieSessionInterface()
    app.config.update(MAX_CONTENT_LENGTH=16384, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_PATH='/',
                      SESSION_COOKIE_SECURE=os.getenv('WEB_COOKIE_SECURE') == '1',
                      SESSION_REFRESH_EACH_REQUEST=False,
                      PERMANENT_SESSION_LIFETIME=idle_lifetime,
                      SESSION_ABSOLUTE_LIFETIME=absolute_lifetime)
    r = client or redis.Redis(host=os.getenv('REDIS_HOST', 'redis'), decode_responses=True,
                             socket_connect_timeout=3, socket_timeout=3)

    def active_job_ids():
        ids = {r.get('capture:owner')}
        for payload in r.lrange('download_queue', 0, -1):
            try:
                ids.add(json.loads(payload)['job_id'])
            except (ValueError, KeyError, TypeError):
                continue
        ids.discard(None)
        return ids

    def duplicate_visible(row):
        """Do not let duplicate discovery broaden existing media visibility."""
        if session.get('role') == 'admin':
            return True
        owner_type, owner_id = row.get('owner_type'), row.get('owner_id')
        if owner_type or owner_id:
            return owner_type == 'web' and owner_id == session.get('role', 'user')
        # Legacy rows predate explicit ownership and retain their existing shared visibility.
        return True

    def local_media(row):
        filename = row.get('filename')
        if not filename or Path(filename).name != filename:
            return None
        root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
        path = root / filename
        try:
            if path.parent.resolve() != root or path.is_symlink() or not path.is_file():
                return None
            return path.stat()
        except OSError:
            return None

    def row_identity(row):
        source_type = row.get('source_type') or row.get('source')
        source_id = row.get('source_id')
        if source_type == 'youtube' and not source_id:
            source_id = clipper.youtube_video_id((row.get('source_metadata') or {}).get('youtube_id'))
        return source_type, source_id

    def row_output(row):
        output = row.get('output_format')
        if output in {'mp4', 'mp3'}:
            return output
        suffix = Path(row.get('filename') or '').suffix.lower().lstrip('.')
        return suffix if suffix in {'mp4', 'mp3'} else 'mp4'

    def duplicate_payload(row, status, stat=None):
        metadata = row.get('source_metadata') or {}
        title = metadata.get('title') or row.get('note') or row.get('filename') or 'Video YouTube'
        public = {key: row[key] for key in (
            'job_id', 'source', 'source_type', 'source_id', 'source_name', 'note', 'output_format',
            'compression', 'quality', 'storage', 'is_live', 'state', 'requested_at', 'size', 'filename',
        ) if key in row}
        public['title'] = title
        if metadata.get('title'):
            public['source_metadata'] = {'title': metadata['title']}
        if stat:
            public['size'] = stat.st_size
            public['file_date'] = stat.st_mtime
        return {'status': status, 'job': public}

    def find_duplicate(rows, identity, output_format):
        if not identity:
            return None
        active_ids = None
        for row in rows:
            if row.get('is_clip') or not duplicate_visible(row):
                continue
            if row_identity(row) != (identity['source_type'], identity['source_id']):
                continue
            if row_output(row) != output_format:
                continue
            if row.get('state') == 'ready':
                stat = local_media(row)
                if stat:
                    return duplicate_payload(row, 'ready', stat)
                continue
            if row.get('state') in {'queued', 'starting', 'recording', 'waiting', 'stopping', 'finalizing'}:
                active_ids = active_ids if active_ids is not None else active_job_ids()
                if row.get('job_id') in active_ids:
                    return duplicate_payload(row, 'processing')
        return None

    def current_duplicate(identity, output_format):
        return find_duplicate(storage.recordings(), identity, output_format)

    def annotate_attempts(rows):
        """Add public attempt totals/retry eligibility without exposing request URLs."""
        groups = {}
        for row in rows:
            root = row.get('attempt_root_id') or row['job_id']
            number = positive_int(row.get('attempt_number')) or 1
            row.update(attempt_root_id=root, attempt_number=number)
            groups.setdefault(root, []).append(row)
        private_ids = storage.capture_request_ids()
        for attempts in groups.values():
            total = max(row['attempt_number'] for row in attempts)
            for row in attempts:
                row['attempt_total'] = total
                downloadable = (row.get('source') in {'youtube', 'instagram'}
                                or row.get('source') == 'tiktok' and row.get('is_live') is not True)
                row['can_retry'] = bool(row.get('state') == 'failed' and downloadable
                                        and row['job_id'] in private_ids
                                        and row['attempt_number'] == total)

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

    def html_login_redirect():
        """Only full-page Reader navigation may receive the login HTML flow."""
        return (request.endpoint == 'view_transcript' and request.method == 'GET'
                and request.headers.get('Sec-Fetch-Mode') != 'cors'
                and 'text/html' in request.headers.get('Accept', ''))

    def unauthorized(message):
        session.clear()
        if html_login_redirect():
            return redirect(url_for('index', next=request.full_path.rstrip('?')))
        return jsonify(error=message), 401

    @app.before_request
    def authorize():
        if not request.path.startswith('/api/'):
            return
        if request.path != '/api/login' and not session.get('operator'):
            return unauthorized('Masuk dulu untuk lanjut, ya.')
        if request.path != '/api/login':
            now = time.time()
            created = session.get('session_created_at')
            last_seen = session.get('session_last_seen_at')
            if created is None and last_seen is None:
                # Preserve valid cookies issued before lifetime timestamps existed.
                created = last_seen = now
                session['session_created_at'] = created
                session['session_last_seen_at'] = last_seen
            valid_timestamps = (isinstance(created, (int, float))
                                and isinstance(last_seen, (int, float)))
            if (not valid_timestamps
                    or now - last_seen >= idle_lifetime.total_seconds()
                    or now - created >= absolute_lifetime.total_seconds()):
                return unauthorized('Sesi kamu sudah habis. Masuk lagi, ya.')
        if request.path != '/api/login' and session.get('auth_version') != auth_version(session.get('role', 'user')):
            return unauthorized('Password akun ini sudah diganti. Yuk, masuk lagi.')
        if request.method != 'GET' and request.path != '/api/login':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
                return jsonify(error='Sesi kamu sudah habis. Masuk lagi, ya.'), 403
        if (request.path != '/api/login'
                and request.headers.get('X-Session-Activity') == '1'
                and hmac.compare_digest(request.headers.get('X-CSRF-Token', ''),
                                        session.get('csrf', 'missing'))):
            session['session_last_seen_at'] = time.time()

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: https://i.ytimg.com; frame-ancestors 'none'")
        if request.endpoint in {'index', 'clipper_page'}:
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self' https://www.youtube.com; style-src 'self'; "
                "img-src 'self' data: https://i.ytimg.com; frame-src https://www.youtube-nocookie.com; "
                "frame-ancestors 'none'")
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

    @app.get('/clipper')
    def clipper_page():
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
        now = time.time()
        session.clear()
        session.update(operator=True, role=role, auth_version=auth_version(role),
                       csrf=secrets.token_hex(32), session_created_at=now,
                       session_last_seen_at=now)
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

    def watch_rows(role):
        now = time.time()
        rows = []
        for watch in telegram_store.watches():
            if not watch_service.web_can_manage(watch, role):
                continue
            status = watch_service.watch_state(watch, now)
            last = watch.get('last_capture')
            if last:
                recording = storage.recording(last.get('job_id')) or {}
                last = dict(last, title=(recording.get('source_metadata') or {}).get('title') or recording.get('note'),
                            state=recording.get('state'))
            rows.append(dict(id=watch['id'], channel=watch['channel'], name=watch.get('channel_name'),
                             start=watch['start'], end=watch['end'], mode=watch['mode'],
                             auto_transcribe=watch['auto_transcribe'], status=status, last_capture=last,
                             can_cancel=watch['status'] == 'active' and now < watch['end'],
                             discovery_error=watch.get('discovery_error')))
        visible = {'waiting', 'recording', 'discovery_issue'}
        rows.sort(key=lambda row: (row['status'] not in visible, -row['start']))
        return rows, sum(row['status'] in visible for row in rows)

    @app.get('/api/watches')
    def list_watches():
        rows, active_count = watch_rows(session.get('role', 'user'))
        return jsonify(watches=rows, active_count=active_count)

    @app.post('/api/watches')
    def create_web_watch():
        data = request.get_json() or {}
        watch = watch_service.create_watch(
            data.get('channel'), data.get('date'), data.get('start_time'), data.get('end_time'),
            mode=data.get('mode', 'first'), auto_transcribe=data.get('auto_transcribe', False),
            owner_type='web', owner_id=session.get('role', 'user'))
        return jsonify(id=watch['id'], channel=watch['channel'], start=watch['start'], end=watch['end'],
                       mode=watch['mode'], auto_transcribe=watch['auto_transcribe'], status='waiting'), 201

    @app.post('/api/watches/<watch_id>/cancel')
    def cancel_web_watch(watch_id):
        role = session.get('role', 'user')
        watch = (telegram_store.cancel_watch(watch_id) if role == 'admin' else
                 telegram_store.cancel_watch(watch_id, owner_type='web', owner_id=role))
        if not watch:
            return jsonify(error='Watch tidak ditemukan.'), 404
        return jsonify(ok=True)

    @app.get('/api/admin/watches')
    def list_admin_watches():
        denied = require_admin()
        if denied:
            return denied
        rows, active_count = watch_rows('admin')
        return jsonify(watches=rows, active_count=active_count)

    @app.post('/api/admin/watches/<watch_id>/cancel')
    def cancel_watch(watch_id):
        denied = require_admin()
        if denied:
            return denied
        if not telegram_store.cancel_watch(watch_id):
            return jsonify(error='Watch tidak ditemukan.'), 404
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
                identity = storage.source_identity(source, url)
                duplicate = current_duplicate(identity, output_format)
                if duplicate:
                    return jsonify(duplicate=duplicate, estimated_bytes=duplicate['job'].get('size'),
                                   quality=selected_quality, format=output_format,
                                   compression=compression, is_live=False)
            elif source == 'tiktok':
                url = storage.validate_tiktok_url(str(data.get('url', '')).strip())
            elif source == 'instagram':
                url = storage.validate_instagram_url(str(data.get('url', '')).strip())
            else:
                raise ValueError('Pilih dulu sumber yang mau dicek.')
            command = [
                'yt-dlp', '--dump-single-json', '--skip-download', '--no-playlist',
                '--no-warnings', '-f',
                quality.ytdlp_selector(selected_quality, output_format),
            ]
            if source in {'tiktok', 'instagram'}:
                command += ['--ignore-no-formats-error']
            result = subprocess.run(command + [url], capture_output=True, timeout=25)
            if result.returncode:
                return jsonify(error='Ukuran belum bisa diperkirakan dari sumber ini.'), 422
            metadata = json.loads(result.stdout)
            if source in {'tiktok', 'instagram'} and not (source == 'tiktok' and storage.tiktok_is_live(url)):
                metadata, _ = storage.social_video(metadata, source)
            return jsonify(**quality.selected_media_info(metadata), quality=selected_quality,
                           format=output_format, compression=compression)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return jsonify(error='Ukuran belum bisa diperkirakan dari sumber ini.'), 422
        except ValueError:
            raise
        finally:
            r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0", 1, 'web:estimate', token)

    @app.post('/api/clipper/metadata')
    def clipper_metadata():
        data = request.get_json() or {}
        url = storage.validate_url(str(data.get('url', '')).strip(), youtube=True)
        lock = uuid.uuid4().hex
        if not r.set('web:clipper:metadata', lock, nx=True, ex=35):
            return jsonify(error='Metadata video lain sedang diperiksa. Tunggu sebentar, ya.'), 409
        try:
            result = subprocess.run([
                'yt-dlp', '--dump-single-json', '--skip-download', '--no-playlist',
                '--no-warnings', url,
            ], capture_output=True, timeout=25)
            if result.returncode:
                return jsonify(error='Metadata video belum bisa dibaca. Periksa link dan akses videonya.'), 422
            info = json.loads(result.stdout)
            if info.get('is_live') is True or info.get('live_status') == 'is_live':
                return jsonify(error='Clipper v1 belum mendukung video yang sedang live.'), 422
            try:
                duration = float(info.get('duration'))
            except (TypeError, ValueError):
                duration = 0
            if not math.isfinite(duration) or duration <= 0:
                return jsonify(error='Durasi video belum tersedia, jadi rentang clip belum bisa divalidasi.'), 422
            thumbnail = str(info.get('thumbnail') or '')
            if urlsplit(thumbnail).hostname != 'i.ytimg.com':
                thumbnail = ''
            token = secrets.token_urlsafe(24)
            snapshot = dict(url=url, duration=duration, title=str(info.get('title') or 'Video YouTube')[:500])
            r.set('clip:metadata:' + token, json.dumps(snapshot), ex=900)
            return jsonify(token=token, title=snapshot['title'], thumbnail=thumbnail,
                           video_id=clipper.youtube_video_id(info.get('id')),
                           duration=duration, duration_label=clipper.format_timestamp(round(duration)),
                           url_start=clipper.youtube_url_start(url),
                           url_start_label=clipper.format_timestamp(clipper.youtube_url_start(url)))
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return jsonify(error='Metadata video belum bisa dibaca. Coba lagi sebentar, ya.'), 422
        finally:
            r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0", 1, 'web:clipper:metadata', lock)

    @app.post('/api/clipper')
    def create_clip():
        data = request.get_json() or {}
        url = storage.validate_url(str(data.get('url', '')).strip(), youtube=True)
        token = str(data.get('metadata_token', ''))
        try:
            metadata = json.loads(r.get('clip:metadata:' + token) or '')
        except (TypeError, json.JSONDecodeError):
            metadata = None
        if not metadata or metadata.get('url') != url:
            raise ValueError('Periksa metadata video lagi sebelum membuat clip.')
        clip_start, clip_end = clipper.validate_range(data.get('start'), data.get('end'), metadata.get('duration'))
        output_format = quality.validate_format(data.get('format', 'mp4'))
        selected_quality = quality.validate(data.get('quality', 'best'))
        compression = quality.validate_preset(data.get('compression', 'original'))
        if output_format == 'mp3':
            selected_quality = 'best'
            compression = 'original'
        storage_target = data.get('storage', 'local')
        if storage_target not in {'local', 'archive'}:
            raise ValueError('Pilih lokasi penyimpanan yang tersedia.')
        archive_enabled = os.getenv('ARCHIVE_ENABLED', 'false').lower() in {'1', 'true', 'yes', 'on'}
        if storage_target == 'archive' and not archive_enabled:
            raise ValueError('Penyimpanan arsip belum diaktifkan oleh admin.')
        job = dict(
            job_id=uuid.uuid4().hex, source='youtube', source_name='YouTube', origin='web', chat_id='web',
            requested_at=time.time(), note=str(data.get('note', '')).strip()[:500], url=url,
            storage=storage_target, archive=storage_target == 'archive', quality=selected_quality,
            output_format=output_format, compression=compression, is_live=False, is_clip=True,
            clip_start=clip_start, clip_end=clip_end, clip_duration=clip_end - clip_start,
            source_duration=metadata['duration'], title=metadata.get('title', ''),
        )
        job.update(attempt_root_id=job['job_id'], attempt_number=1, attempt_total=1)
        if not storage.disk_status()['can_record']:
            return jsonify(error='Ruang penyimpanannya hampir habis. Kosongkan dulu sebelum membuat clip.'), 507
        result = r.eval(ADMIT, 0, job['job_id'], json.dumps(job))
        if result != 'accepted':
            return jsonify(error='Masih ada rekaman yang berjalan. Tunggu sampai selesai, ya.' if result == 'busy' else 'Perekamnya belum siap. Coba lagi sebentar atau hubungi admin.'), 409
        storage.save_capture_request(job)
        telegram_store.subscribe_web(job['job_id'], session.get('role', 'user'))
        return jsonify(job_id=job['job_id']), 202

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
        role = session.get('role', 'user')
        job = dict(job_id=uuid.uuid4().hex, source=source, origin='web', chat_id='web',
                   owner_type='web', owner_id=role,
                   requested_at=time.time(), note=str(data.get('note', '')).strip()[:500],
                   storage=storage_target, archive=storage_target == 'archive',
                   quality=selected_quality, output_format=output_format,
                   compression=compression)
        job.update(attempt_root_id=job['job_id'], attempt_number=1, attempt_total=1)
        if source == 'oryx':
            selected = next((s for s in storage.sources() if s['id'] == data.get('source_id')), None)
            if not selected:
                raise ValueError('Pilih sumber live terlebih dahulu.')
            job.update(stream_url=storage.validate_url(selected['url']), source_name=selected['name'])
        elif source == 'tiktok':
            url = storage.validate_tiktok_url(data.get('url', '').strip())
            job.update(url=url, source_name='TikTok', is_live=storage.tiktok_is_live(url))
        elif source == 'instagram':
            job.update(url=storage.validate_instagram_url(data.get('url', '').strip()), source_name='Instagram', is_live=False)
        elif source == 'youtube':
            job.update(url=storage.validate_url(data.get('url', '').strip(), youtube=True), source_name='YouTube')
            identity = storage.source_identity(source, job['url'])
            if identity:
                job.update(identity)
        else:
            raise ValueError('Pilih sumber rekaman yang tersedia.')
        identity = ({'source_type': job['source_type'], 'source_id': job['source_id']}
                    if job.get('source_type') and job.get('source_id') else None)
        duplicate = current_duplicate(identity, output_format)
        if duplicate:
            return jsonify(duplicate=duplicate)
        if not storage.disk_status()['can_record']:
            return jsonify(error='Ruang penyimpanannya hampir habis. Kosongkan dulu sebelum mulai merekam.'), 507
        if identity:
            result, duplicate = storage.claim_recording(
                job, lambda rows: find_duplicate(rows, identity, output_format),
                lambda: r.eval(ADMIT, 0, job['job_id'], json.dumps(job)),
                'Capture diterima. Menunggu worker…')
            if result == 'existing':
                return jsonify(duplicate=duplicate)
        else:
            result = r.eval(ADMIT, 0, job['job_id'], json.dumps(job))
        if result != 'accepted':
            return jsonify(error='Masih ada rekaman yang berjalan. Tunggu sampai selesai, ya.' if result == 'busy' else 'Perekamnya belum siap. Coba lagi sebentar atau hubungi admin.'), 409
        storage.save_capture_request(job)
        telegram_store.subscribe_web(job['job_id'], role)
        return jsonify(job_id=job['job_id']), 202

    @app.post('/api/recordings/<job_id>/retry')
    def retry_recording(job_id):
        rows = storage.recordings()
        previous = next((row for row in rows if row['job_id'] == job_id), None)
        if not previous:
            return jsonify(error='Capture yang mau dicoba lagi tidak ditemukan.'), 404
        annotate_attempts(rows)
        if previous.get('state') != 'failed' or not previous.get('can_retry'):
            return jsonify(error='Capture ini tidak bisa dicoba lagi atau sudah memiliki percobaan pengganti.'), 409
        original = storage.capture_request(job_id)
        if not original:
            return jsonify(error='Sumber asli capture ini tidak tersedia untuk dicoba lagi.'), 409
        if not storage.disk_status()['can_record']:
            return jsonify(error='Ruang penyimpanannya hampir habis. Kosongkan dulu sebelum mencoba lagi.'), 507
        if original.get('storage') == 'archive' and os.getenv('ARCHIVE_ENABLED', 'false').lower() not in {'1', 'true', 'yes', 'on'}:
            return jsonify(error='Tujuan arsip capture ini sedang tidak tersedia.'), 409
        root = previous['attempt_root_id']
        number = previous['attempt_number'] + 1
        job = {key: original[key] for key in ('source', 'source_type', 'source_id', 'source_name',
                                               'owner_type', 'owner_id', 'note', 'storage', 'archive',
                                               'quality', 'output_format', 'compression', 'is_live',
                                               'url', 'stream_url', 'is_clip', 'clip_start', 'clip_end',
                                               'clip_duration', 'source_duration', 'title') if key in original}
        job.update(job_id=uuid.uuid4().hex, origin='web', chat_id='web', requested_at=time.time(),
                   attempt_root_id=root, retry_of=job_id, attempt_number=number, attempt_total=number)
        if previous.get('filename'):
            job['filename'] = previous['filename']
        result = r.eval(RETRY_ADMIT, 0, job_id, job['job_id'], json.dumps(job))
        if result != 'accepted':
            messages = {
                'duplicate': 'Percobaan pengganti sudah dibuat.',
                'busy': 'Masih ada capture yang berjalan. Tunggu sampai selesai, ya.',
                'offline': 'Perekamnya belum siap. Coba lagi sebentar atau hubungi admin.',
            }
            return jsonify(error=messages.get(result, messages['offline'])), 409
        storage.save_capture_request(job)
        storage.save_recording(job, 'queued', f'Percobaan {number}/{number} · Menunggu worker…')
        telegram_store.subscribe_web(job['job_id'], session.get('role', 'user'))
        return jsonify(job_id=job['job_id'], retry_of=job_id, attempt_root_id=root,
                       attempt_number=number, attempt_total=number), 202

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
        queued_ids = set()
        for payload in r.lrange('download_queue', 0, -1):
            try:
                queued_ids.add(json.loads(payload)['job_id'])
            except (ValueError, KeyError, TypeError):
                continue
        for row in rows:
            if row['state'] not in {'ready', 'failed'} and row['job_id'] != owner and row['job_id'] not in queued_ids:
                row.update(state='interrupted', detail='Proses rekaman terputus sebelum file dinyatakan siap.')
        annotate_attempts(rows)
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
        if session.get('role') != 'admin':
            for row in rows:
                row.pop('download_count', None)
                row.pop('last_downloaded_at', None)
        for row in rows:
            row.pop('owner_type', None)
            row.pop('owner_id', None)
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

    @app.post('/api/recordings/<job_id>/transcript/cancel')
    def cancel_transcript(job_id):
        try:
            status, cancelled = transcription_queue.cancel(r, job_id, (request.get_json() or {}).get('request_id'))
        except ValueError as exc:
            return jsonify(error=str(exc)), 404
        if not cancelled and status != 'cancelled':
            return jsonify(error='Transkripsi sudah selesai atau tidak sedang diproses.', status=status), 409
        return jsonify(job_id=job_id, status='cancelled')

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
        row = next((item for item in storage.recordings()
                    if (item.get('filename') == filename and item['state'] == 'ready') or
                    (item.get('original_filename') == filename and item['state'] in {'ready', 'finalizing'})), None)
        if not row:
            return jsonify(error='File rekaman tidak ditemukan atau belum siap.'), 404
        root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
        path = root / filename
        if Path(filename).name != filename or path.parent.resolve() != root or path.is_symlink():
            return jsonify(error='File rekaman tidak ditemukan.'), 404
        inline = request.args.get('inline') == '1'
        response = send_from_directory(root, filename, as_attachment=not inline, conditional=True)
        if request.method == 'GET' and not inline and response.status_code in {200, 206}:
            try:
                storage.record_download(row['job_id'])
            except Exception:
                app.logger.exception('Unable to persist download count for %s', row['job_id'])
        return response

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
