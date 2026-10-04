"""Telegram watch discovery and notifications around the existing workers."""
import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
try:
    from . import storage, telegram_store, transcription_queue
except ImportError:
    import storage
    import telegram_store
    import transcription_queue

log = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7), 'WIB')


def allowed(user_id):
    # No implicit public access when configuration is absent/malformed.
    ids = re.split(r'[,\s]+', os.getenv('TELEGRAM_ALLOWED_USER_IDS', '').strip())
    return user_id is not None and str(user_id) in {value for value in ids if value.isdigit()}


def authorized(handler):
    @wraps(handler)
    async def wrapped(*args):
        update, context = args[-2:]
        if not allowed(getattr(update.effective_user, 'id', None)):
            if update.callback_query:
                await update.callback_query.answer('Tidak diizinkan.', show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text('Tidak diizinkan.')
            return
        return await handler(*args)
    return wrapped


def reader_url(job_id):
    base = os.getenv('STREAMFETCH_PUBLIC_URL', '').rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Atur STREAMFETCH_PUBLIC_URL untuk link View Transcript.')
    return base + '/api/recordings/' + job_id + '/transcript/view'


def channel_url(value):
    storage.validate_url(value, youtube=True)
    parsed = urlsplit(value)
    if (parsed.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443}
            or not re.fullmatch(r'/(?:@[A-Za-z0-9_.-]+|channel/UC[A-Za-z0-9_-]+|(?:c|user)/[A-Za-z0-9_.-]+)(?:/(?:live|streams))?/?', parsed.path)):
        raise ValueError('Kirim URL channel YouTube, misalnya https://youtube.com/@rribatam.')
    path = re.sub(r'/(?:live|streams)/?$', '', parsed.path).rstrip('/')
    return 'https://www.youtube.com' + path


def watch_window(day, start, end, now=None):
    now = now or datetime.now(WIB)
    now = now.astimezone(WIB)
    aliases = {'today': 0, 'hari ini': 0, 'tomorrow': 1, 'besok': 1}
    date = (now.date() + timedelta(days=aliases[day.lower()]) if day.lower() in aliases
            else datetime.strptime(day, '%Y-%m-%d').date())
    if not all(re.fullmatch(r'\d{2}:\d{2}', value) for value in (start, end)):
        raise ValueError('Gunakan jam HH:MM WIB.')
    begins = datetime.combine(date, datetime.strptime(start, '%H:%M').time(), WIB)
    ends = datetime.combine(date, datetime.strptime(end, '%H:%M').time(), WIB)
    if ends <= begins or ends <= now:
        raise ValueError('Jam akhir harus setelah jam mulai dan belum lewat (WIB).')
    return begins.timestamp(), ends.timestamp()


def discover_live(channel):
    """Metadata-only yt-dlp lookup; actual media always belongs to capture worker."""
    result = subprocess.run(['yt-dlp', '--dump-single-json', '--skip-download',
                             '--flat-playlist', '--playlist-end', '20', '--no-warnings',
                             channel + '/streams'], capture_output=True, timeout=25, check=True)
    entries = json.loads(result.stdout).get('entries') or []
    live = []
    for entry in entries:
        if not isinstance(entry, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(entry.get('id') or '')):
            continue
        if entry.get('live_status') == 'is_live' or entry.get('is_live') is True:
            live.append({'id': entry['id'], 'url': 'https://www.youtube.com/watch?v=' + entry['id']})
    return live


CAPTURE_ADMIT = """
local guard = 'telegram:capture:' .. ARGV[1]
if redis.call('EXISTS', guard) == 1 then return 'duplicate' end
redis.call('SET', guard, 'queued')
redis.call('RPUSH', 'download_queue', ARGV[2])
return 'accepted'
"""


def transcript_button(row):
    if (row.get('state') == 'ready' and str(row.get('filename', '')).lower().endswith('.mp4')
            and (row.get('transcript') or {}).get('status') not in {'queued', 'transcribing', 'completed'}):
        return InlineKeyboardMarkup([[InlineKeyboardButton('Transcribe', callback_data='transcribe:' + row['job_id'])]])
    return None


class Controls:
    def __init__(self, client):
        self.client = client

    def create_watch(self, values, user_id, chat_id):
        if len(values) not in {4, 5, 6}:
            raise ValueError('/watch channel today|tomorrow|YYYY-MM-DD HH:MM HH:MM [first|every] [yes|no]')
        channel = channel_url(values[0])
        start, end = watch_window(*values[1:4])
        mode = values[4].lower() if len(values) > 4 else 'first'
        auto = values[5].lower() if len(values) > 5 else 'no'
        if mode not in {'first', 'every'} or auto not in {'yes', 'no'}:
            raise ValueError('Pilih first/every dan yes/no.')
        if auto == 'yes':
            reader_url('setup')
        watch = dict(id=uuid.uuid4().hex[:12], user_id=user_id, chat_id=chat_id,
                     channel=channel, start=start, end=end, mode=mode,
                     auto_transcribe=auto == 'yes', status='active')
        telegram_store.save_watch(watch)
        return watch

    @authorized
    async def watch(self, update, context):
        if not context.args:
            context.user_data['watch_draft'] = {'chat_id': update.effective_chat.id, 'values': []}
            await update.effective_message.reply_text('Watch WIB · kirim URL channel YouTube. /cancelwatch untuk batal.')
            return
        try:
            watch = self.create_watch(context.args, update.effective_user.id, update.effective_chat.id)
            await update.effective_message.reply_text('Watch ' + watch['id'] + ' tersimpan · ' + watch['mode'] + ' · WIB.')
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))

    async def watch_reply(self, update, context):
        draft = getattr(context, 'user_data', {}).get('watch_draft')
        if not draft or draft['chat_id'] != update.effective_chat.id:
            return False
        values = draft['values']
        answer = update.effective_message.text.strip()
        try:
            if not values:
                answer = channel_url(answer)
            elif len(values) == 1:
                # Validate the date now; final validation checks the full window.
                if answer.lower() not in {'today', 'tomorrow', 'hari ini', 'besok'}:
                    datetime.strptime(answer, '%Y-%m-%d')
            elif len(values) in {2, 3}:
                if not re.fullmatch(r'\d{2}:\d{2}', answer):
                    raise ValueError('Gunakan HH:MM WIB.')
                datetime.strptime(answer, '%H:%M')
                if len(values) == 3:
                    watch_window(values[1], values[2], answer)
            elif len(values) == 4:
                answer = 'first' if answer == '-' else answer.lower()
                if answer not in {'first', 'every'}:
                    raise ValueError('Pilih first atau every (default: first).')
            elif len(values) == 5 and answer.lower() not in {'yes', 'no'}:
                raise ValueError('Auto-transcribe: yes atau no.')
            if len(values) == 5:
                watch = self.create_watch(values + [answer], update.effective_user.id, update.effective_chat.id)
                context.user_data.pop('watch_draft', None)
                await update.effective_message.reply_text('Watch ' + watch['id'] + ' tersimpan · WIB.')
            else:
                values.append(answer)
                prompts = ['Today / tomorrow / YYYY-MM-DD?', 'Jam mulai HH:MM WIB?',
                           'Jam akhir HH:MM WIB?', 'First / every? Kirim - untuk first.',
                           'Auto-transcribe yes / no?']
                await update.effective_message.reply_text(prompts[len(values) - 1])
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))
        return True

    @authorized
    async def watchlist(self, update, context):
        watches = [w for w in telegram_store.watches() if w['user_id'] == update.effective_user.id
                   and w['chat_id'] == update.effective_chat.id and w['status'] == 'active']
        lines = [w['id'] + ' · ' + datetime.fromtimestamp(w['start'], WIB).strftime('%d/%m %H:%M')
                 + '–' + datetime.fromtimestamp(w['end'], WIB).strftime('%H:%M')
                 + ' WIB · ' + w['mode'] + (' · transcribe' if w['auto_transcribe'] else '')
                 + '\n' + w['channel'] for w in watches]
        await update.effective_message.reply_text('\n\n'.join(lines) or 'Tidak ada watch aktif.')

    @authorized
    async def cancelwatch(self, update, context):
        if not context.args:
            if context.user_data.pop('watch_draft', None) is not None:
                await update.effective_message.reply_text('Konfigurasi watch dibatalkan.')
                return
            await update.effective_message.reply_text('/cancelwatch ID · lihat /watchlist')
            return
        for watch in telegram_store.watches():
            if (watch['id'] == context.args[0] and watch['user_id'] == update.effective_user.id
                    and watch['chat_id'] == update.effective_chat.id):
                watch['status'] = 'cancelled'
                telegram_store.save_watch(watch)
                await update.effective_message.reply_text('Discovery dibatalkan. Capture yang sudah dimulai tetap berjalan.')
                return
        await update.effective_message.reply_text('Watch tidak ditemukan.')

    @authorized
    async def transcribe(self, update, context):
        rows = [row for row in storage.recordings() if row.get('state') == 'ready'
                and str(row.get('filename', '')).lower().endswith('.mp4')][:20]
        rows.sort(key=lambda row: (row.get('transcript') or {}).get('status') in {'queued', 'transcribing', 'completed'})
        buttons = [[InlineKeyboardButton(row['filename'][:45], callback_data='transcribe:' + row['job_id'])]
                   for row in rows[:6]]
        await update.effective_message.reply_text('Pilih rekaman untuk transkripsi.' if buttons else 'Belum ada rekaman siap.',
                                                  reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

    @authorized
    async def callback(self, update, context):
        query = update.callback_query
        await query.answer()
        match = re.fullmatch(r'transcribe:([A-Za-z0-9_-]{1,40})', query.data or '')
        if not match:
            return
        job_id = match[1]
        try:
            reader_url(job_id)
            row = storage.recording(job_id)
            if not row or row.get('state') != 'ready' or not str(row.get('filename') or '').lower().endswith('.mp4'):
                raise ValueError('Rekaman tidak ditemukan atau belum siap.')
            telegram_store.subscribe(job_id, update.effective_chat.id, update.effective_user.id,
                                     monitor_transcript=True, transcript_notice=None)
            status, created = transcription_queue.enqueue(self.client, job_id)
            if status == 'completed':
                await query.message.reply_text('Transkrip sudah tersedia.', reply_markup=self.view_button(job_id))
                telegram_store.subscribe(job_id, update.effective_chat.id, update.effective_user.id,
                                         transcript_notice='completed')
            else:
                await query.message.reply_text('Transkripsi masuk antrean.' if created else 'Transkripsi sedang diproses.')
        except ValueError as exc:
            await query.message.reply_text(str(exc))
        except Exception:
            log.warning('telegram transcription admission unavailable', exc_info=True)
            await query.message.reply_text('Antrean sementara tidak tersedia; status akan diperiksa kembali.')

    @staticmethod
    def view_button(job_id):
        return InlineKeyboardMarkup([[InlineKeyboardButton('View Transcript', url=reader_url(job_id))]])

    async def dispatch(self, bot):
        # A failed Telegram chat must not block other watches or subscriptions.
        for key, task in telegram_store.tasks():
            if key.startswith('capture:') and allowed(task['user_id']):
                try:
                    await self.dispatch_task(bot, key, task)
                except Exception:
                    log.warning('telegram capture dispatch/notice unavailable job=%s', task['job']['job_id'])

    async def dispatch_task(self, bot, key, task):
        if task.get('dispatch') != 'sent':
            if not storage.recording(task['job']['job_id']):
                self.client.eval(CAPTURE_ADMIT, 0, task['job']['job_id'], json.dumps(task['job']))
            telegram_store.subscribe(task['job']['job_id'], task['chat_id'], task['user_id'],
                                     monitor_capture=True, auto_transcribe=task['auto_transcribe'])
            task['dispatch'] = 'sent'
            telegram_store.save_task(key, task)
        if not task.get('detected_notice'):
            await bot.send_message(task['chat_id'], 'Live terdeteksi · capture masuk antrean.\n' + task['job']['url'])
            task['detected_notice'] = True
            telegram_store.save_task(key, task)

    async def tick(self, bot, now=None):
        now = time.time() if now is None else now
        clock_started = time.monotonic()
        await self.dispatch(bot)
        for watch in telegram_store.watches():
            if watch['status'] != 'active' or not allowed(watch['user_id']):
                continue
            if now >= watch['end']:
                try:
                    await bot.send_message(watch['chat_id'], 'Watch ' + watch['id'] + ' berakhir. Capture aktif tetap berjalan.')
                except Exception:
                    log.warning('telegram watch=%s expiry notice unavailable', watch['id'])
                    continue
                watch['status'] = 'expired'
                telegram_store.save_watch(watch)
                continue
            if now < watch['start']:
                continue
            try:
                live = await asyncio.to_thread(discover_live, watch['channel'])
            except (OSError, subprocess.SubprocessError, ValueError):
                log.warning('telegram watch=%s discovery unavailable', watch['id'])
                continue
            # Discovery may outlast the window or race a cancel callback.
            if now + time.monotonic() - clock_started >= watch['end']:
                continue
            for video in live:
                job = dict(job_id=uuid.uuid4().hex, chat_id=watch['chat_id'], source='youtube',
                           source_name='YouTube', url=video['url'], requested_at=now, origin='telegram')
                task = dict(job=job, chat_id=watch['chat_id'], user_id=watch['user_id'],
                            auto_transcribe=watch['auto_transcribe'], dispatch='pending')
                if telegram_store.claim_video(watch['id'], video['id'], task) and watch['mode'] == 'first':
                    break
        await self.dispatch(bot)
        await self.notify_jobs(bot)

    async def notify_jobs(self, bot):
        for key, task in telegram_store.tasks():
            if key.startswith('subscription:') and allowed(task['user_id']):
                try:
                    await self.notify_task(bot, key, task)
                except Exception:
                    log.warning('telegram job notice unavailable job=%s', task['job_id'])

    async def notify_task(self, bot, key, task):
        row = storage.recording(task['job_id'])
        if not row:
            return
        state = row.get('state')
        if task.get('monitor_capture'):
            if state in {'starting', 'recording', 'waiting', 'finalizing'} and not task.get('capture_started'):
                await bot.send_message(task['chat_id'], 'Capture dimulai · ' + (row.get('filename') or row['job_id'][:8]))
                task['capture_started'] = True
            if state in {'ready', 'failed', 'interrupted'} and not task.get('capture_done'):
                await bot.send_message(task['chat_id'], ('Capture selesai · ' if state == 'ready' else 'Capture gagal · ')
                                       + (row.get('filename') or row.get('detail') or row['job_id'][:8])[:500],
                                       reply_markup=transcript_button(row) if state == 'ready' else None)
                task['capture_done'] = True
            if state == 'ready' and task.get('auto_transcribe') and not task.get('transcript_requested'):
                reader_url(row['job_id'])
                task['monitor_transcript'] = True
                telegram_store.save_task(key, task)
                queue_status, created = transcription_queue.enqueue(self.client, row['job_id'])
                task['transcript_requested'] = True
                if queue_status != 'completed':
                    await bot.send_message(task['chat_id'], 'Auto-transcribe masuk antrean.' if created else 'Transkripsi sedang diproses.')
        transcript = (storage.recording(row['job_id']) or {}).get('transcript') or {}
        status = transcript.get('status')
        if task.get('monitor_transcript') and status and status != task.get('transcript_notice'):
            if status == 'transcribing':
                await bot.send_message(task['chat_id'], 'Transkripsi sedang dibuat.')
            elif status == 'completed':
                await bot.send_message(task['chat_id'], 'Transkripsi selesai.', reply_markup=self.view_button(row['job_id']))
            elif status == 'failed':
                await bot.send_message(task['chat_id'], 'Transkripsi gagal. Coba /transcribe untuk retry.')
            task['transcript_notice'] = status
        telegram_store.save_task(key, task)

    async def loop(self, application):
        interval = max(10, int(os.getenv('TELEGRAM_WATCH_POLL_SECONDS', '30')))
        while True:
            try:
                await self.tick(application.bot)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('telegram watch/notification tick failed', exc_info=True)
            await asyncio.sleep(interval)

    async def post_init(self, application):
        application.bot_data['watch_task'] = asyncio.create_task(self.loop(application))

    async def post_stop(self, application):
        task = application.bot_data.get('watch_task')
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
