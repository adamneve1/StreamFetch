"""Telegram watch discovery and notifications around the existing workers."""
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from functools import wraps
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
try:
    from . import storage, telegram_store, transcription_queue, watch_service
except ImportError:
    import storage
    import telegram_store
    import transcription_queue
    import watch_service

log = logging.getLogger(__name__)
WIB = watch_service.WIB


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


channel_url = watch_service.channel_url


WINDOW_PATTERN = r'(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})(?:\s*WIB)?'


def parse_window(value):
    match = re.fullmatch(WINDOW_PATTERN, value.strip(), re.I)
    if not match or any(int(match[index]) > limit for index, limit in ((1, 23), (2, 59), (3, 23), (4, 59))):
        raise ValueError('Kirim window seperti 08:00-10:00 atau 8.00 - 10.00 WIB (jam 00–23).')
    return (f'{int(match[1]):02d}:{match[2]}', f'{int(match[3]):02d}:{match[4]}')


def watch_arguments(args):
    """Accept compact ranges while retaining the original separate-time command."""
    if len(args) < 3:
        raise ValueError('/watch @channel today|tomorrow|YYYY-MM-DD 08:00-10:00 [first|every] [yes|no]')
    rest = ' '.join(args[2:])
    match = re.fullmatch(WINDOW_PATTERN + r'(?:\s+(first|every))?(?:\s+(yes|no))?', rest, re.I)
    if match:
        start, end = parse_window(f'{match[1]}:{match[2]}-{match[3]}:{match[4]}')
        return [args[0], args[1], start, end, (match[5] or 'first').lower(), (match[6] or 'no').lower()]
    if len(args) in {4, 5, 6} and re.fullmatch(r'\d{2}:\d{2}', args[2]) and re.fullmatch(r'\d{2}:\d{2}', args[3]):
        return args
    raise ValueError('Contoh: /watch @rribatam tomorrow 08:00-10:00 WIB [every] [yes|no]')


watch_day = watch_service.watch_day
watch_window = watch_service.watch_window


def discover_live(channel):
    """Metadata-only yt-dlp lookup; actual media always belongs to capture worker."""
    def lookup(suffix, flat=True):
        command = ['yt-dlp', '--dump-single-json', '--skip-download', '--no-warnings']
        command += ['--flat-playlist', '--playlist-end', '20'] if flat else ['--no-playlist', '--ignore-no-formats-error']
        result = subprocess.run(command + [channel.rstrip('/') + suffix], capture_output=True, timeout=25, check=True)
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError('Invalid discovery metadata')
        return data
    try:
        data = lookup('/streams')
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.output or b'')
        detail = detail.decode(errors='replace') if isinstance(detail, bytes) else str(detail)
        if 'streams tab' not in detail.lower():
            raise
        log.info('youtube discovery fallback=channel_live reason=streams_tab_unavailable')
        try:
            data = lookup('/live', flat=False)
        except subprocess.CalledProcessError as fallback:
            text = fallback.stderr or fallback.output or b''
            text = text.decode(errors='replace') if isinstance(text, bytes) else str(text)
            if any(value in text.lower() for value in ('not currently live', 'live event will begin', 'premiere will begin')):
                return []
            raise
    entries = data.get('entries') if 'entries' in data else [data]
    if entries is not None and not isinstance(entries, list):
        raise ValueError('Invalid discovery entries')
    live = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(entry.get('id') or '')):
            continue
        if (entry.get('live_status') in {None, 'is_live'}
                and (entry.get('live_status') == 'is_live' or entry.get('is_live') is True)
                and entry.get('is_live') is not False
                and entry.get('media_type') in {None, 'livestream'}
                and not entry.get('is_upcoming') and not entry.get('is_premiere')):
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


def safe_notice(value):
    # Use the capture worker's existing credential/URL sanitizer.
    try:
        from .worker import safe_diagnostic
    except ImportError:
        from worker import safe_diagnostic
    return safe_diagnostic(value)[:300]


def watch_summary(watch):
    return (watch['channel'][:120] + '\n'
            + datetime.fromtimestamp(watch['start'], WIB).strftime('%d/%m %H:%M')
            + '–' + datetime.fromtimestamp(watch['end'], WIB).strftime('%H:%M')
            + ' WIB · ' + watch['mode'].title()
            + ' · Auto-transcribe: ' + ('on' if watch['auto_transcribe'] else 'off'))


def watch_destination(watch):
    if watch.get('owner_type', 'telegram') == 'telegram':
        return dict(chat_id=watch.get('chat_id'), user_id=watch.get('user_id'))
    return telegram_store.web_destination(watch.get('owner_id'))


class Controls:
    def __init__(self, client):
        self.client = client

    def create_watch(self, values, user_id, chat_id):
        if len(values) not in {4, 5, 6}:
            raise ValueError('/watch channel today|tomorrow|YYYY-MM-DD HH:MM HH:MM [first|every] [yes|no]')
        mode = values[4].lower() if len(values) > 4 else 'first'
        auto = values[5].lower() if len(values) > 5 else 'no'
        if mode not in {'first', 'every'} or auto not in {'yes', 'no'}:
            raise ValueError('Pilih first/every dan yes/no.')
        if auto == 'yes':
            reader_url('setup')
        return watch_service.create_watch(values[0], *values[1:4], mode=mode,
                                          auto_transcribe=auto == 'yes', owner_type='telegram',
                                          owner_id=user_id, user_id=user_id, chat_id=chat_id)

    @authorized
    async def watch(self, update, context):
        if not context.args:
            draft = {'chat_id': update.effective_chat.id, 'token': uuid.uuid4().hex[:8], 'step': 'channel'}
            context.user_data['watch_draft'] = draft
            await update.effective_message.reply_text('Watch WIB · kirim @channel atau URL channel YouTube.',
                                                      reply_markup=self.watch_buttons(draft))
            return
        try:
            watch = self.create_watch(watch_arguments(context.args), update.effective_user.id, update.effective_chat.id)
            context.user_data.pop('watch_draft', None)
            await update.effective_message.reply_text('Watch tersimpan · ' + watch_summary(watch))
            telegram_store.update_watch(watch['id'], created_notice=True)
        except ValueError as exc:
            await update.effective_message.reply_text(str(exc))

    @staticmethod
    def watch_buttons(draft):
        prefix = 'watch:' + draft['token'] + ':'
        choices = {'date': [('Today', 'date:today'), ('Tomorrow', 'date:tomorrow'), ('Choose date', 'date:choose')],
                   'auto': [('Yes', 'auto:yes'), ('No', 'auto:no')],
                   'confirm': [('Confirm', 'confirm')]}.get(draft['step'], [])
        rows = [[InlineKeyboardButton(label, callback_data=prefix + value) for label, value in choices]] if choices else []
        rows.append([InlineKeyboardButton('Cancel', callback_data=prefix + 'cancel')])
        return InlineKeyboardMarkup(rows)

    async def watch_reply(self, update, context):
        draft = getattr(context, 'user_data', {}).get('watch_draft')
        if not draft:
            return False
        if draft['chat_id'] != update.effective_chat.id:
            await update.effective_message.reply_text('Watch sedang diisi di chat lain. Lanjutkan di sana atau /cancelwatch.')
            return True
        await self.watch_answer(update, context, update.effective_message.text.strip())
        return True

    async def watch_answer(self, update, context, answer):
        draft = context.user_data['watch_draft']
        message = update.effective_message
        try:
            step = draft['step']
            if answer.lower() == 'cancel':
                context.user_data.pop('watch_draft', None)
                await message.reply_text('Konfigurasi watch dibatalkan.')
                return
            if step == 'channel':
                draft['channel'] = channel_url(answer)
                draft['step'] = 'date'
                prompt = 'Pilih tanggal (WIB), atau kirim YYYY-MM-DD.'
            elif step == 'date':
                draft['day'] = watch_day(answer)
                draft['step'] = 'window'
                prompt = 'Kirim window dalam satu pesan, misalnya 08:00-10:00 WIB.'
            elif step == 'window':
                start, end = parse_window(answer)
                watch_window(draft['day'], start, end)
                draft.update(start=start, end=end, step='auto')
                prompt = 'Auto-transcribe setelah capture selesai?'
            elif step == 'auto':
                answer = answer.lower()
                if answer not in {'yes', 'no'}:
                    raise ValueError('Auto-transcribe: pilih Yes / No.')
                if answer == 'yes':
                    reader_url('check')
                draft.update(auto=answer, step='confirm')
                prompt = (draft['channel'] + '\n' + draft['day'] + ' · ' + draft['start'] + '–' + draft['end']
                          + ' WIB\nFirst live · Auto-transcribe: ' + answer + '\nConfirm untuk menyimpan.')
            elif step == 'confirm':
                if answer.lower() != 'confirm':
                    raise ValueError('Pilih Confirm untuk menyimpan atau Cancel untuk batal.')
                watch = self.create_watch([draft['channel'], draft['day'], draft['start'], draft['end'], 'first', draft['auto']],
                                          update.effective_user.id, update.effective_chat.id)
                context.user_data.pop('watch_draft', None)
                await message.reply_text('Watch tersimpan · ' + watch_summary(watch))
                telegram_store.update_watch(watch['id'], created_notice=True)
                return
            else:
                raise ValueError('Mulai ulang dengan /watch.')
            await message.reply_text(prompt, reply_markup=self.watch_buttons(draft))
        except ValueError as exc:
            await message.reply_text(str(exc), reply_markup=self.watch_buttons(draft))

    @authorized
    async def watch_callback(self, update, context):
        query = update.callback_query
        await query.answer()
        draft = context.user_data.get('watch_draft')
        match = re.fullmatch(r'watch:([a-f0-9]{8}):(cancel|confirm|date:(?:today|tomorrow|choose)|auto:(?:yes|no))', query.data or '')
        if not match or not draft or draft.get('token') != match[1] or draft['chat_id'] != update.effective_chat.id:
            await query.message.reply_text('Tombol watch sudah tidak aktif. Gunakan /watch untuk mulai.')
            return
        action = match[2]
        if action != 'cancel' and action.split(':')[0] != draft['step']:
            await query.message.reply_text('Lanjutkan langkah watch saat ini.', reply_markup=self.watch_buttons(draft))
            return
        if action == 'date:choose':
            await query.message.reply_text('Kirim tanggal YYYY-MM-DD (WIB).', reply_markup=self.watch_buttons(draft))
            return
        await self.watch_answer(update, context, action.split(':')[-1])

    @authorized
    async def watchlist(self, update, context):
        now = time.time()
        watches = []
        for watch in telegram_store.watches():
            if not watch_service.telegram_can_manage(watch, update.effective_user.id, update.effective_chat.id):
                continue
            state = watch_service.watch_state(watch, now)
            if (state in {'waiting', 'recording', 'discovery_issue'}
                    or state == 'expired' and watch['end'] >= now - 86400):
                watches.append((watch, state))
        watches.sort(key=lambda item: (item[1] == 'expired', -item[0]['start']))
        labels = dict(waiting='Waiting', recording='Recording', discovery_issue='Discovery issue', expired='Expired')
        lines, buttons = [], []
        for number, (watch, state) in enumerate(watches, 1):
            line = str(number) + '. ' + watch_summary(watch) + '\n' + labels[state]
            if sum(len(item) + 2 for item in lines) + len(line) > 3500:
                await update.effective_message.reply_text('\n\n'.join(lines),
                            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
                lines, buttons = [], []
            lines.append(line)
            if watch['status'] == 'active' and now < watch['end']:
                buttons.append([InlineKeyboardButton('Batalkan Watch ' + str(number),
                                callback_data='watchcancel:' + watch['id'])])
        await update.effective_message.reply_text('\n\n'.join(lines) or 'Belum ada Watch aktif.',
                            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

    def cancel_owned_watch(self, watch_id, user_id, chat_id):
        watch = next((w for w in telegram_store.watches() if w['id'] == watch_id), None)
        if not watch or not watch_service.telegram_can_manage(watch, user_id, chat_id):
            return None
        if watch.get('owner_type', 'telegram') == 'telegram':
            return telegram_store.cancel_watch(watch_id, user_id, chat_id)
        return telegram_store.cancel_watch(watch_id, owner_type='web', owner_id=watch['owner_id'])

    @authorized
    async def cancelwatch_callback(self, update, context):
        query = update.callback_query
        await query.answer()
        match = re.fullmatch(r'watchcancel:([A-Za-z0-9_-]{1,40})', query.data or '')
        watch = self.cancel_owned_watch(match[1], update.effective_user.id,
                                       update.effective_chat.id) if match else None
        await query.message.reply_text('Discovery dibatalkan. Capture yang sudah dimulai tetap berjalan.'
                                       if watch else 'Watch tidak ditemukan.')
        if watch and (watch_destination(watch) or {}).get('chat_id') == update.effective_chat.id:
            telegram_store.update_watch(watch['id'], cancelled_notice=True)

    @authorized
    async def cancelwatch(self, update, context):
        if not context.args:
            if context.user_data.pop('watch_draft', None) is not None:
                await update.effective_message.reply_text('Konfigurasi watch dibatalkan.')
                return
            await update.effective_message.reply_text('/cancelwatch ID · lihat /watchlist')
            return
        watch = self.cancel_owned_watch(context.args[0], update.effective_user.id, update.effective_chat.id)
        if watch:
            await update.effective_message.reply_text('Discovery dibatalkan. Capture yang sudah dimulai tetap berjalan.')
            if (watch_destination(watch) or {}).get('chat_id') == update.effective_chat.id:
                telegram_store.update_watch(watch['id'], cancelled_notice=True)
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
                                     monitor_transcript=True)
            status, created = transcription_queue.enqueue(self.client, job_id)
            if status == 'completed':
                await query.message.reply_text('Transkrip sudah tersedia.', reply_markup=self.view_button(job_id))
                telegram_store.subscribe(job_id, update.effective_chat.id, update.effective_user.id,
                                         transcript_notice='completed',
                                         transcript_request_notice=(row.get('transcript') or {}).get('request_id'))
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
            telegram_owned = task.get('owner_type', 'telegram') == 'telegram'
            if key.startswith('capture:') and (not telegram_owned or allowed(task.get('user_id'))):
                try:
                    await self.dispatch_task(bot, key, task)
                except Exception:
                    log.warning('watch capture dispatch/notice unavailable job=%s', task['job']['job_id'])

    async def dispatch_task(self, bot, key, task):
        if task.get('dispatch') != 'sent':
            row = storage.recording(task['job']['job_id'])
            if not row:
                storage.save_recording(task['job'], 'queued', 'Live terdeteksi · capture masuk antrean.')
            if not row or row.get('state') == 'queued':
                self.client.eval(CAPTURE_ADMIT, 0, task['job']['job_id'], json.dumps(task['job']))
            destination = (dict(chat_id=task['chat_id'], user_id=task['user_id'])
                           if task.get('chat_id') is not None and task.get('user_id') is not None
                           else telegram_store.web_destination(task.get('owner_id')))
            if destination:
                task.update(destination)
                telegram_store.subscribe(task['job']['job_id'], task['chat_id'], task['user_id'],
                                         owner_type=task.get('owner_type', 'telegram'),
                                         owner_id=task.get('owner_id'),
                                         monitor_capture=True, monitor_transcript=True,
                                         auto_transcribe=task['auto_transcribe'])
            task['dispatch'] = 'sent'
            telegram_store.update_task(key, dispatch='sent', **(destination or {}))
        if (bot is not None and task.get('chat_id') is not None and allowed(task.get('user_id'))
                and (task.get('owner_type') != 'web' or telegram_store.web_destination(task.get('owner_id'))
                     == dict(chat_id=task['chat_id'], user_id=task.get('user_id')))
                and not task.get('detected_notice')):
            await bot.send_message(task['chat_id'], 'Live terdeteksi · capture masuk antrean.\n' + task['job']['url'])
            telegram_store.update_task(key, detected_notice=True)

    async def advance_web_transcripts(self):
        """Advance canonical auto-transcribe independently of optional message sends."""
        for key, task in telegram_store.tasks():
            if (not key.startswith(('capture:', 'subscription:'))
                    or not task.get('auto_transcribe') or task.get('transcript_requested')):
                continue
            job_id = task.get('job_id') or task['job']['job_id']
            row = storage.recording(job_id)
            if not row or row.get('state') != 'ready':
                continue
            try:
                transcription_queue.enqueue(self.client, row['job_id'])
                telegram_store.update_task(key, transcript_requested=True, monitor_transcript=True)
            except Exception:
                log.warning('Watch auto-transcribe unavailable job=%s', job_id)

    async def notify_watches(self, bot):
        for watch in telegram_store.watches():
            destination = watch_destination(watch)
            if not destination or destination.get('chat_id') is None or not allowed(destination.get('user_id')):
                continue
            try:
                if watch.get('created_at') and not watch.get('created_notice'):
                    await bot.send_message(destination['chat_id'], 'Watch tersimpan · ' + watch_summary(watch))
                    telegram_store.update_watch(watch['id'], created_notice=True)
                if watch['status'] in {'expired', 'cancelled'} and not watch.get(watch['status'] + '_notice'):
                    # Historical terminal watches predate notification checkpoints.
                    if not any(watch.get(name) for name in ('created_notice', 'created_at', 'expired_at', 'cancelled_at')):
                        continue
                    label = 'berakhir' if watch['status'] == 'expired' else 'dibatalkan'
                    await bot.send_message(destination['chat_id'], 'Watch ' + label + ' · '
                                           + watch['channel'][:120] + '\nCapture aktif tetap berjalan.')
                    telegram_store.update_watch(watch['id'], **{watch['status'] + '_notice': True})
                error = watch.get('discovery_error')
                if watch['status'] == 'active' and error:
                    error = safe_notice(error)
                    digest = hashlib.sha256(error.encode()).hexdigest()
                    notices = watch.get('discovery_notices', [])
                    if digest not in notices:
                        await bot.send_message(destination['chat_id'], 'Watch discovery issue · '
                                               + watch['channel'][:120] + '\n' + error)
                        telegram_store.update_watch(watch['id'], discovery_notices=notices + [digest])
            except Exception:
                log.warning('Watch notice unavailable watch=%s', watch['id'])

    async def tick(self, bot, now=None):
        now = time.time() if now is None else now
        clock_started = time.monotonic()
        if bot is not None:
            await self.notify_watches(bot)
        await self.dispatch(bot)
        for watch in telegram_store.watches():
            telegram_owned = watch.get('owner_type', 'telegram') == 'telegram'
            # The always-on worker owns web watches. Telegram watches remain
            # with the bot so notification/retry behavior stays unchanged.
            if (watch['status'] != 'active' or
                    telegram_owned and (bot is None or not allowed(watch.get('user_id')))):
                continue
            if now >= watch['end']:
                telegram_store.update_watch(watch['id'], status='expired', expired_at=now)
                continue
            if now < watch['start']:
                continue
            try:
                live = await asyncio.to_thread(discover_live, watch['channel'])
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                try:
                    from .worker import safe_diagnostic
                except ImportError:
                    from worker import safe_diagnostic
                detail = getattr(exc, 'stderr', None) or str(exc)
                if isinstance(detail, bytes):
                    detail = detail.decode(errors='replace')
                telegram_store.discovery_state(watch['id'], safe_diagnostic(detail), now)
                log.warning('telegram watch=%s discovery_issue=%s detail=%s', watch['id'], type(exc).__name__, safe_diagnostic(detail))
                continue
            telegram_store.discovery_state(watch['id'], None, now)
            # Discovery may outlast the window or race a cancel callback.
            if now + time.monotonic() - clock_started >= watch['end']:
                continue
            for video in live:
                chat_id = watch.get('chat_id', 'web')
                job = dict(job_id=uuid.uuid4().hex, chat_id=chat_id, source='youtube',
                           source_name='YouTube', url=video['url'], requested_at=now,
                           origin='telegram_watch' if telegram_owned else 'web_watch',
                           is_live=True, compression='original')
                task = dict(job=job, owner_type='telegram' if telegram_owned else 'web',
                            owner_id=watch.get('owner_id'),
                            auto_transcribe=watch['auto_transcribe'], dispatch='pending')
                if telegram_owned:
                    task.update(chat_id=watch['chat_id'], user_id=watch['user_id'])
                if telegram_store.claim_video(watch['id'], video['id'], task) and watch['mode'] == 'first':
                    break
        await self.dispatch(bot)
        await self.advance_web_transcripts()
        if bot is not None:
            await self.notify_watches(bot)
            await self.notify_jobs(bot)

    async def notify_jobs(self, bot):
        await self.advance_web_transcripts()
        for key, task in telegram_store.tasks():
            if key.startswith('subscription:') and allowed(task.get('user_id')) and task.get('chat_id') is not None:
                if (task.get('owner_type') == 'web' and telegram_store.web_destination(task.get('owner_id'))
                        != dict(chat_id=task['chat_id'], user_id=task.get('user_id'))):
                    continue
                try:
                    # Prior subscription in this chat may have checkpointed the send.
                    task = telegram_store.task(key)
                    await self.notify_task(bot, key, task)
                except Exception:
                    log.warning('telegram job notice unavailable job=%s', task['job_id'])

    async def notify_task(self, bot, key, task):
        row = storage.recording(task['job_id'])
        if not row:
            return
        state = row.get('state')
        label = safe_notice(row.get('filename') or (row.get('source_metadata') or {}).get('title')
                            or row.get('source_name') or row.get('source') or 'rekaman')
        if task.get('monitor_capture'):
            if ((state == 'recording' or row.get('started_at'))
                    and not task.get('capture_started') and not task.get('capture_done')):
                await bot.send_message(task['chat_id'], 'Capture dimulai · ' + label)
                task['capture_started'] = True
                telegram_store.checkpoint_subscription(key, capture_started=True)
            if state in {'ready', 'failed', 'interrupted'} and not task.get('capture_done'):
                await bot.send_message(task['chat_id'], ('Capture selesai · ' if state == 'ready' else 'Capture gagal · ')
                                       + label + ('\n' + safe_notice(row.get('error_message') or row.get('detail'))
                                                 if state != 'ready' and (row.get('error_message') or row.get('detail')) else '')
                                       + ('\n' + safe_notice(row['processing_detail']) if state == 'ready' and row.get('processing_detail') else ''),
                                       reply_markup=transcript_button(row) if state == 'ready' else None)
                task['capture_done'] = True
                telegram_store.checkpoint_subscription(key, capture_done=True)
        transcript = (storage.recording(row['job_id']) or {}).get('transcript') or {}
        status = transcript.get('status')
        request_id = transcript.get('request_id')
        if (status in {'completed', 'failed', 'cancelled'} and status == task.get('transcript_notice')
                and 'transcript_request_notice' not in task):
            # Upgrade successful checkpoints written by the previous bot.
            telegram_store.checkpoint_subscription(key, transcript_request_notice=request_id)
            task['transcript_request_notice'] = request_id
        if ((task.get('monitor_transcript') or task.get('monitor_capture'))
                and status in {'completed', 'failed', 'cancelled'}
                and (status != task.get('transcript_notice') or request_id != task.get('transcript_request_notice'))):
            if status == 'completed':
                try:
                    button = self.view_button(row['job_id'])
                except ValueError:
                    button = None
                await bot.send_message(task['chat_id'], 'Transkripsi selesai.', reply_markup=button)
            elif status == 'failed':
                await bot.send_message(task['chat_id'], 'Transkripsi gagal. Coba /transcribe untuk retry.'
                                       + ('\n' + safe_notice(transcript['error']) if transcript.get('error') else ''))
            elif status == 'cancelled':
                await bot.send_message(task['chat_id'], 'Transkripsi dibatalkan. Gunakan /transcribe untuk mulai lagi.')
            telegram_store.checkpoint_subscription(key, transcript_notice=status,
                                                  transcript_request_notice=request_id)

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
