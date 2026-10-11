"""Reusable YouTube subtitle ingestion for Transcript Only requests."""
import html
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from . import storage, transcript_reader, transcription_queue, transcription_worker
except ImportError:
    import storage
    import transcript_reader
    import transcription_queue
    import transcription_worker


class SubtitleUnavailable(RuntimeError):
    """YouTube did not expose a usable subtitle track."""


@dataclass(frozen=True)
class SubtitleChoice:
    source: str
    language: str
    extension: str


@dataclass(frozen=True)
class AIQueueResult:
    status: str
    created: bool = False
    deferred: bool = False


def language_base(value):
    return str(value or '').strip().lower().replace('_', '-').split('-', 1)[0]


def video_id_for(row):
    metadata = row.get('source_metadata') or {}
    if row.get('source_type') == 'youtube' and row.get('source_id'):
        return row['source_id']
    return next((identifier for value in (
        metadata.get('youtube_id'), row.get('youtube_id'), metadata.get('webpage_url'))
        if (identifier := transcript_reader.youtube_id(value))), None)


def find_cached(video_id, language, accessible=lambda _row: True):
    """Find a completed, accessible transcript for one canonical video/language."""
    wanted = language_base(language)
    root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
    for row in storage.recordings():
        transcript = row.get('transcript') or {}
        output_available = any(
            isinstance(transcript.get(key), str)
            and Path(transcript[key]).name == transcript[key]
            and (root / transcript[key]).parent.resolve() == root
            and not (root / transcript[key]).is_symlink()
            and (root / transcript[key]).is_file()
            for key in ('txt_filename', 'srt_filename', 'vtt_filename'))
        if (video_id_for(row) == video_id and transcript.get('status') == 'completed'
                and language_base(transcript.get('language')) == wanted
                and output_available and accessible(row)):
            return row
    return None


def probe(url):
    result = subprocess.run([
        'yt-dlp', '--dump-single-json', '--skip-download', '--no-playlist',
        '--ignore-no-formats-error', '--no-warnings', url,
    ], capture_output=True, timeout=30, check=True)
    metadata = json.loads(result.stdout)
    if not isinstance(metadata, dict):
        raise SubtitleUnavailable('Metadata subtitle tidak tersedia.')
    return metadata


def _extension(entries):
    extensions = [str(item.get('ext') or '').lower() for item in entries or []
                  if isinstance(item, dict)]
    return next((value for value in ('vtt', 'srt') if value in extensions), '')


def select_subtitle(metadata):
    """Prefer manual over auto, and Indonesian over the original language."""
    original = language_base(metadata.get('language') or metadata.get('original_language'))
    groups = [('manual', metadata.get('subtitles') or {}),
              ('auto', metadata.get('automatic_captions') or {})]

    def choose(source, tracks, wanted):
        for language, entries in tracks.items():
            if language == 'live_chat' or language_base(language) != wanted:
                continue
            extension = _extension(entries)
            if extension:
                return SubtitleChoice(source, language, extension)

    for source, tracks in groups:
        choice = choose(source, tracks, 'id')
        if choice:
            return choice
        if original and original != 'id':
            choice = choose(source, tracks, original)
            if choice:
                return choice
    if not original:
        for source, tracks in groups:
            for language, entries in tracks.items():
                extension = '' if language == 'live_chat' else _extension(entries)
                if extension:
                    return SubtitleChoice(source, language, extension)
    return None


def download_subtitle(url, video_id, choice):
    with tempfile.TemporaryDirectory(prefix='streamfetch-subtitle-') as directory:
        command = ['yt-dlp', '--skip-download', '--no-playlist', '--no-warnings',
                   '--write-subs' if choice.source == 'manual' else '--write-auto-subs',
                   '--sub-langs', choice.language, '--sub-format', choice.extension,
                   '--paths', directory, '-o', '%(id)s.%(ext)s', url]
        subprocess.run(command, capture_output=True, timeout=45, check=True)
        candidates = sorted(Path(directory).glob(f'{video_id}*.{choice.extension}'))
        if not candidates:
            raise SubtitleUnavailable('Subtitle tidak dapat diunduh.')
        return candidates[0].read_text(encoding='utf-8-sig', errors='replace')


def normalize_subtitle(content):
    """Convert SRT/WebVTT cues into the existing timestamped segment objects."""
    segments = []
    for cue in transcript_reader.parse_subtitles(content):
        text = html.unescape(re.sub(r'<[^>]+>', '', cue['text']))
        text = '\n'.join(' '.join(line.split()) for line in text.splitlines()).strip()
        if text and not (segments and segments[-1].text == text
                         and segments[-1].start == cue['start']):
            segments.append(transcription_worker.TranscriptSegment(
                start=cue['start'], end=cue['end'], text=text))
    if not segments:
        raise SubtitleUnavailable('Subtitle tidak berisi cue yang dapat dibaca.')
    return segments


def public_metadata(metadata, video_id):
    return {key: value for key, value in {
        'title': metadata.get('title'), 'description': metadata.get('description'),
        'channel': metadata.get('channel'), 'upload_date': metadata.get('upload_date'),
        'youtube_id': video_id, 'duration': metadata.get('duration'), 'id': video_id,
    }.items() if value is not None}


def recording_job(task):
    """Build the shared catalogue entry for Telegram and Web requests."""
    owner_type = task.get('owner_type') or 'telegram'
    owner_id = task.get('owner_id', task.get('user_id'))
    return dict(job_id=task['job_id'], source='youtube', source_type='youtube',
                source_id=task['video_id'], source_name='YouTube',
                owner_type=owner_type, owner_id=str(owner_id),
                origin=task.get('origin') or owner_type + '_transcript', transcript_only=True,
                requested_at=task['requested_at'], note=task.get('title') or '',
                source_metadata=task.get('source_metadata') or {'youtube_id': task['video_id']})


def resolve(task, accessible):
    """Resolve a cached/subtitle transcript without downloading media or invoking AI."""
    cached = find_cached(task['video_id'], 'id', accessible)
    if cached:
        return cached
    metadata = probe(task['url'])
    choice = select_subtitle(metadata)
    task.update(title=metadata.get('title') or '',
                source_metadata=public_metadata(metadata, task['video_id']),
                language=(choice.language if choice else
                          metadata.get('language') or metadata.get('original_language') or 'id'))
    cached = find_cached(task['video_id'], task['language'], accessible)
    if cached:
        return cached
    if not choice:
        raise SubtitleUnavailable('Subtitle manual maupun otomatis tidak tersedia.')
    content = download_subtitle(task['url'], task['video_id'], choice)
    return publish(recording_job(task), choice, content)


def publish(job, choice, content):
    """Publish subtitle sidecars through the same catalogue contract as STT."""
    root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    segments = normalize_subtitle(content)
    txt, srt = transcription_worker.render_outputs(segments)
    vtt = transcription_worker.render_vtt(segments)
    base = f"transcript-{job['source_id']}-{language_base(choice.language)}-{job['job_id'][:8]}"
    outputs = [(root / f'{base}.txt', txt), (root / f'{base}.srt', srt),
               (root / f'{base}.vtt', vtt)]
    storage.save_recording(job, 'ready', 'Transcript Only · subtitle YouTube')
    published = []

    def write():
        for path, body in outputs:
            transcription_worker.atomic_write(path, body, job['job_id'])
            published.append(path)

    try:
        if not storage.save_transcription_state(
                job['job_id'], 'completed', source=choice.source,
                language=choice.language, model='youtube-subtitles',
                txt_filename=outputs[0][0].name, srt_filename=outputs[1][0].name,
                vtt_filename=outputs[2][0].name, error='', progress_percent=100,
                eta_seconds=0, publish=write):
            raise RuntimeError('Riwayat transkrip tidak dapat disimpan.')
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        storage.save_transcription_state(job['job_id'], 'failed', source=choice.source,
                                         language=choice.language,
                                         error='Subtitle tidak dapat diproses.')
        raise
    return storage.recording(job['job_id'])


def download_temporary_audio(url, job_id):
    """Download one private AI input, removing partial files on every failure."""
    root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    prefix = f'transcript-ai-{job_id}'
    for stale in root.glob(prefix + '.*'):
        stale.unlink(missing_ok=True)
    try:
        subprocess.run([
            'yt-dlp', '--no-playlist', '--no-warnings', '-f', 'ba/b',
            '-x', '--audio-format', 'm4a', '-o', str(root / (prefix + '.%(ext)s')), url,
        ], capture_output=True, timeout=300, check=True)
        candidates = [path for path in root.glob(prefix + '.*')
                      if path.is_file() and not path.name.endswith(('.part', '.ytdl'))]
        if len(candidates) != 1:
            raise SubtitleUnavailable('Audio sementara tidak dapat disiapkan.')
        return candidates[0].name
    except Exception as exc:
        for partial in root.glob(prefix + '.*'):
            partial.unlink(missing_ok=True)
        if isinstance(exc, SubtitleUnavailable):
            raise
        raise SubtitleUnavailable('Audio sementara tidak dapat disiapkan.') from exc


def enqueue_ai(client, task):
    """Admit an explicitly approved request to the existing STT queue."""
    row = storage.recording(task['job_id'])
    status = ((row or {}).get('transcript') or {}).get('status')
    if status in {'completed', 'queued', 'transcribing'}:
        return AIQueueResult(status)
    temporary = None
    try:
        temporary = download_temporary_audio(task['url'], task['job_id'])
        job = recording_job(task)
        if not row:
            storage.save_recording(job, 'ready', 'Transcript Only · menunggu transkripsi AI')
        storage.save_capture_request({**job, 'url': task['url'],
                                      'temporary_media_filename': temporary})
        status, created = transcription_queue.enqueue(client, task['job_id'])
        if not created and status not in {'queued', 'transcribing', 'completed'}:
            raise ValueError('Transkrip sudah selesai atau tidak dapat diulang.')
        return AIQueueResult(status, created=created)
    except Exception:
        current = storage.recording(task['job_id'])
        status = ((current or {}).get('transcript') or {}).get('status')
        if status in {'queued', 'transcribing'}:
            return AIQueueResult(status, deferred=True)
        if temporary:
            root = Path(os.getenv('DOWNLOAD_DIR', '/downloads')).resolve()
            candidate = root / temporary
            if Path(temporary).name == temporary and candidate.parent.resolve() == root and not candidate.is_symlink():
                candidate.unlink(missing_ok=True)
        if current:
            storage.save_transcription_state(task['job_id'], 'failed', source='ai',
                                             error='Audio atau antrean AI belum dapat disiapkan.')
        raise
