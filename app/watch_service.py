"""Shared Watch validation and persistence for web and Telegram interfaces."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

try:
    from . import storage, telegram_store
except ImportError:
    import storage
    import telegram_store


WIB = timezone(timedelta(hours=7), 'WIB')


def channel_url(value):
    value = str(value or '').strip()
    if value.startswith('@'):
        value = 'https://www.youtube.com/' + value
    elif re.match(r'^(?:www\.|m\.)?youtube\.com/', value, re.I):
        value = 'https://' + value
    try:
        storage.validate_url(value, youtube=True)
    except ValueError:
        raise ValueError('Kirim @channel atau URL channel YouTube, misalnya youtube.com/@rribatam.') from None
    parsed = urlsplit(value)
    if (parsed.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}
            or parsed.username or parsed.password or parsed.port not in {None, 80, 443}
            or not re.fullmatch(r'/(?:@[A-Za-z0-9_.-]+|channel/UC[A-Za-z0-9_-]+|(?:c|user)/[A-Za-z0-9_.-]+)(?:/(?:live|streams))?/?', parsed.path)):
        raise ValueError('Kirim URL channel YouTube, misalnya https://youtube.com/@rribatam.')
    path = re.sub(r'/(?:live|streams)/?$', '', parsed.path).rstrip('/')
    return 'https://www.youtube.com' + path


def watch_day(value, now=None):
    value = str(value or '').strip().lower()
    aliases = {'today': 0, 'hari ini': 0, 'tomorrow': 1, 'besok': 1}
    if value in aliases:
        current = (now or datetime.now(WIB)).astimezone(WIB)
        return (current.date() + timedelta(days=aliases[value])).isoformat()
    try:
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat()
    except ValueError:
        raise ValueError('Pilih Today / Tomorrow atau kirim tanggal YYYY-MM-DD.') from None


def watch_window(day, start, end, now=None):
    now = (now or datetime.now(WIB)).astimezone(WIB)
    date = datetime.strptime(watch_day(day, now), '%Y-%m-%d').date()
    if not all(re.fullmatch(r'\d{2}:\d{2}', str(value or '')) for value in (start, end)):
        raise ValueError('Gunakan jam HH:MM WIB.')
    try:
        begins = datetime.combine(date, datetime.strptime(start, '%H:%M').time(), WIB)
        ends = datetime.combine(date, datetime.strptime(end, '%H:%M').time(), WIB)
    except ValueError:
        raise ValueError('Gunakan jam HH:MM WIB.') from None
    if ends <= begins or ends <= now:
        raise ValueError('Jam akhir harus setelah jam mulai dan belum lewat (WIB).')
    return begins.timestamp(), ends.timestamp()


def create_watch(channel, day, start_time, end_time, mode='first', auto_transcribe=False,
                 owner_type='web', owner_id='user', user_id=None, chat_id=None, now=None):
    channel = channel_url(channel)
    start, end = watch_window(day, start_time, end_time, now)
    mode = str(mode or '').lower()
    if mode not in {'first', 'every'}:
        raise ValueError('Pilih First live atau Every live.')
    if not isinstance(auto_transcribe, bool):
        raise ValueError('Auto-transcribe harus on atau off.')
    if owner_type not in {'web', 'telegram'}:
        raise ValueError('Pemilik watch tidak valid.')
    watch = dict(id=uuid.uuid4().hex[:12], owner_type=owner_type, owner_id=str(owner_id),
                 channel=channel, start=start, end=end, mode=mode,
                 auto_transcribe=auto_transcribe, status='active')
    if owner_type == 'telegram':
        watch.update(user_id=user_id, chat_id=chat_id)
    telegram_store.save_watch(watch)
    return watch


def web_can_manage(watch, role):
    return role == 'admin' or (watch.get('owner_type') == 'web' and watch.get('owner_id') == role)
