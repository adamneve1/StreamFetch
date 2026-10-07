"""Timestamp and YouTube URL helpers for manual clip jobs."""
import math
import re
from urllib.parse import parse_qs, urlsplit


_CLOCK = re.compile(r'^\d+(?::\d{1,2}){1,2}$')
_UNITS = re.compile(r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$', re.IGNORECASE)


def parse_timestamp(value, field='Waktu'):
    """Parse supported user timestamps into non-negative whole seconds."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{field} belum valid.')
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value < 0 or not float(value).is_integer():
            raise ValueError(f'{field} harus berupa waktu positif dalam detik.')
        return int(value)
    text = str(value).strip().lower()
    if not text or text.startswith('-'):
        raise ValueError(f'{field} harus berupa waktu positif dalam detik.')
    if text.isdigit():
        return int(text)
    if _CLOCK.fullmatch(text):
        parts = [int(part) for part in text.split(':')]
        if any(part >= 60 for part in parts[1:]):
            raise ValueError(f'{field} belum valid.')
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    units = _UNITS.fullmatch(text)
    if units and any(part is not None for part in units.groups()):
        hours, minutes, seconds = (int(part or 0) for part in units.groups())
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f'{field} belum valid. Gunakan detik, MM:SS, HH:MM:SS, atau 1m30s.')


def format_timestamp(seconds):
    seconds = parse_timestamp(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def youtube_url_start(url):
    """Return a supported YouTube URL timestamp, defaulting to zero."""
    parsed = urlsplit(url)
    values = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    for key in ('t', 'start'):
        candidates = values.get(key) or fragment.get(key) or []
        if candidates:
            try:
                return parse_timestamp(candidates[0], 'Timestamp URL')
            except ValueError:
                return 0
    return 0


def validate_range(start, end, duration=None):
    start = parse_timestamp(start, 'Waktu mulai')
    end = parse_timestamp(end, 'Waktu selesai')
    if end == start:
        raise ValueError('Clip tidak boleh berdurasi nol.')
    if end < start:
        raise ValueError('Waktu selesai harus setelah waktu mulai.')
    if duration is not None:
        try:
            total = float(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError('Durasi video belum tersedia.') from exc
        if not math.isfinite(total) or total <= 0:
            raise ValueError('Durasi video belum tersedia.')
        if end > math.ceil(total):
            raise ValueError('Rentang clip melewati durasi video.')
    return start, end
