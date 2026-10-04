"""Source metadata and legacy transcript sidecars for the read-only reader."""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def youtube_id(value):
    """Accept a video ID or an exact supported YouTube URL, never arbitrary embeds."""
    value = str(value or '')
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        return value
    try:
        url = urlsplit(value)
        if url.scheme not in {'https', 'http'} or url.username or url.password:
            return None
        if url.hostname == 'youtu.be':
            candidate = url.path.strip('/')
        elif url.hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            if url.path == '/watch':
                candidate = parse_qs(url.query).get('v', [''])[0]
            elif re.fullmatch(r'/(?:embed|shorts|live)/[^/]+/?', url.path):
                candidate = url.path.split('/')[2]
            else:
                return None
        else:
            return None
        return candidate if re.fullmatch(r'[A-Za-z0-9_-]{11}', candidate) else None
    except ValueError:
        return None


LABELS = {
    'program': 'program', 'acara': 'program', 'nama program': 'program',
    'tanggal': 'date_time', 'date/time': 'date_time', 'date': 'date_time',
    'hari/tanggal': 'date_time', 'hari, tanggal': 'date_time',
    'waktu': 'time', 'jam': 'time', 'time': 'time',
    'tema': 'theme', 'theme': 'theme', 'topik': 'theme',
    'narasumber': 'guests', 'nara sumber': 'guests', 'guests': 'guests',
    'guest': 'guests', 'bintang tamu': 'guests',
    'presenter': 'presenter', 'penyiar': 'presenter', 'host': 'presenter',
    'pemandu': 'presenter', 'moderator': 'presenter',
}


def parse_source_metadata(description):
    """Only extract explicitly labelled facts; the untouched description is authoritative."""
    fields = {}
    active = None
    for line in str(description or '').splitlines():
        clean = re.sub(r'^\s*(?:[-•*]|\d+[.)])\s*', '', line).strip()
        match = re.match(r'^([^:：]{1,40})\s*[:：]\s*(.*)$', clean)
        key = LABELS.get(match[1].strip().casefold()) if match else None
        if key:
            active = key
            value = match[2].strip()
            if value:
                fields.setdefault(key, []).append(value)
        elif active == 'guests' and clean and re.match(r'^\s*(?:[-•*]|\d+[.)])\s*', line):
            fields.setdefault('guests', []).append(clean)
        else:
            active = None
    result = {key: '\n'.join(values) for key, values in fields.items()
              if key not in {'guests', 'time'}}
    if fields.get('time'):
        result['date_time'] = ' · '.join(filter(None, [
            result.get('date_time'), '\n'.join(fields['time'])]))
    result['guests'] = []
    for value in fields.get('guests', []):
        # Preserve the original guest text; separate roles only when explicitly delimited.
        match = re.match(r'^(.*?)\s*\(([^()]+)\)\s*$', value)
        if not match:
            match = re.match(r'^(.*?)\s+(?:[-–—]|\|)\s+(.+)$', value)
        result['guests'].append({'name': match[1].strip() if match else value,
                                 'role': match[2].strip() if match else '',
                                 'text': value})
    return result


def recording_info(row):
    source = row.get('source_metadata') or {}
    if not isinstance(source, dict):
        source = {}
    description = source.get('description') or ''
    parsed = parse_source_metadata(description)
    upload_date = str(source.get('upload_date') or '')
    if re.fullmatch(r'\d{8}', upload_date):
        upload_date = f'{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}'
    return {
        'program': parsed.get('program') or source.get('title') or row.get('title')
                   or row.get('source_name') or row.get('filename') or 'Transcript',
        'date_time': parsed.get('date_time') or upload_date,
        'date_time_label': 'Source upload date' if upload_date and not parsed.get('date_time')
                          else 'Date/Time',
        'theme': parsed.get('theme') or '',
        'guests': parsed['guests'],
        'presenter': parsed.get('presenter') or '',
        'source': row.get('source_name') or source.get('channel') or row.get('source') or '',
        'description': description,
    }


def _read_sidecar(root, filename):
    if not isinstance(filename, str) or Path(filename).name != filename:
        return None
    path = root / filename
    if path.is_symlink() or path.parent.resolve() != root or not path.is_file():
        return None
    return path.read_text(encoding='utf-8-sig', errors='replace')


def _seconds(timestamp):
    hours, minutes, seconds = timestamp.replace(',', '.').split(':')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_subtitles(content):
    """Read both SRT and WebVTT, including cue IDs, settings and multiline cues."""
    cues = []
    timestamp = r'((?:\d+:)?\d{2}:\d{2}[.,]\d{3})'
    pattern = re.compile(r'^' + timestamp + r'\s*-->\s*' + timestamp + r'(?:\s+.*)?$')
    for block in re.split(r'\n\s*\n', str(content or '').replace('\r\n', '\n').strip()):
        lines = block.splitlines()
        if lines and lines[0].startswith(('NOTE', 'STYLE', 'REGION')):
            continue
        for index, line in enumerate(lines):
            match = pattern.match(line.strip())
            if not match:
                continue
            timestamps = [value if value.count(':') == 2 else '00:' + value
                          for value in match.groups()]
            start, end = map(_seconds, timestamps)
            text = '\n'.join(lines[index + 1:]).strip()
            if text and end >= start:
                cues.append({'start': start, 'end': end, 'text': text})
            break
    return cues


def reader_data(row, root):
    transcript = row.get('transcript') or {}
    root = Path(root).resolve()
    files = {kind: _read_sidecar(root, transcript.get(kind + '_filename'))
             for kind in ('txt', 'srt', 'vtt')}
    segments = parse_subtitles(files['srt']) or parse_subtitles(files['vtt'])
    if not segments:
        segments = [{'start': None, 'end': None, 'text': text.strip()}
                    for text in (files['txt'] or '').splitlines() if text.strip()]
    source = row.get('source_metadata') or {}
    video_id = None
    if row.get('source') == 'youtube' and isinstance(source, dict):
        video_id = next((identifier for value in (
            source.get('youtube_id'), row.get('youtube_id'),
            source.get('webpage_url'), row.get('url'))
            if (identifier := youtube_id(value))), None)
    return {'job_id': row['job_id'], 'info': recording_info(row), 'youtube_id': video_id,
            'segments': segments, 'raw': files['txt'] if files['txt'] is not None
            else '\n'.join(segment['text'] for segment in segments),
            'exports': [kind for kind, content in files.items() if content is not None],
            'language': transcript.get('language')}
