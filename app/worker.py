import os
import json
import asyncio
import subprocess
from pathlib import Path
import signal
import re
import uuid
import time
import logging
from urllib.parse import urlsplit
from datetime import datetime

# pyrefly: ignore [missing-import]
import redis
# pyrefly: ignore [missing-import]
from telegram import Bot
try:
    from . import archive, storage
except ImportError:
    import archive
    import storage


DOWNLOAD_DIR = Path("/downloads")
STARTUP_TIMEOUT = float(os.getenv("CAPTURE_STARTUP_TIMEOUT", "30"))
IDLE_TIMEOUT = float(os.getenv("CAPTURE_IDLE_TIMEOUT", "60"))
STOP_TIMEOUT = float(os.getenv("CAPTURE_STOP_TIMEOUT", "10"))
FINALIZE_TIMEOUT = float(os.getenv("FINALIZE_TIMEOUT", "600"))
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "20"))
ARCHIVE_ENABLED = os.getenv("ARCHIVE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
ARCHIVE_PATH = Path(os.getenv("ARCHIVE_PATH", "/archive"))
ARCHIVE_MAX_RETRIES = max(0, int(os.getenv("ARCHIVE_MAX_RETRIES", "3")))
ARCHIVE_RETRY_BASE_SECONDS = max(1, int(os.getenv("ARCHIVE_RETRY_BASE_SECONDS", "60")))
ARCHIVE_LOCAL_RETENTION_HOURS = max(0, float(os.getenv("ARCHIVE_LOCAL_RETENTION_HOURS", "24")))
ORYX_STREAM_URL = os.getenv("ORYX_STREAM_URL", "").strip()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

r = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    health_check_interval=30,
)


def clear_job_state(job_id, chat_id):
    if job_id:
        r.delete(f"stop:{job_id}")
    for key in (f"active:{chat_id}", "capture:owner"):
        r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then "
               "return redis.call('DEL', KEYS[1]) end return 0", 1, key, job_id)

bot = Bot(token=TOKEN) if TOKEN else None


async def send(chat_id, text):
    if not bot or chat_id == "web":
        return None

    try:

        return await bot.send_message(
            chat_id=chat_id,
            text=text,
        )

    except Exception as e:

        print("Telegram error:", e)

        return None


async def edit(chat_id, message_id, text):

    try:

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )

    except Exception as e:

        if "Message is not modified" not in str(e):

            print(
                "Telegram edit error:",
                e,
            )


def format_size(size):

    if not size:
        return "0 B"

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ]

    size = float(size)

    for unit in units:

        if size < 1024:

            return f"{size:.1f} {unit}"

        size /= 1024

    return f"{size:.1f} PB"


def sanitize_filename(title):
    """
    Sanitize a title to be filesystem-safe while preserving readability.
    Removes or replaces invalid filesystem characters.
    """
    # Replace common problematic characters
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title)
    # Remove leading/trailing spaces and dots
    safe_title = safe_title.strip('. ')
    # Limit title to 15 chars for clean filenames.
    if len(safe_title) > 15:
        safe_title = safe_title[:15].rstrip()
    return safe_title


def generate_final_filename(title="", note=""):
    """
    Generate the final filename in DDMMYYNN.mp4 format, where NN is the
    download number for that date.
    Uses the server's local timezone.
    """
    # Get current date in server's local timezone
    now = datetime.now()
    date_str = now.strftime("%d%m%y")

    pattern = re.compile(rf"^(?:.* -)?{re.escape(date_str)}(\d+)\.mp4$", re.IGNORECASE)
    download_numbers = [
        int(match.group(1))
        for path in DOWNLOAD_DIR.iterdir()
        if path.is_file()
        for match in [pattern.match(path.name)]
        if match
    ]
    next_number = max(download_numbers, default=0) + 1
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', ' ', str(note))
    prefix = ' '.join(prefix.split()).strip('. ')
    prefix = prefix.encode('utf-8')[:160].decode('utf-8', errors='ignore').rstrip('. ')
    return DOWNLOAD_DIR / f"{prefix + ' -' if prefix else ''}{date_str}{next_number:02d}.mp4"


def progress_bar(percent):

    length = 20

    filled = int(
        length * percent / 100
    )

    return (
        "█" * filled
        + "░" * (length - filled)
    )


def probe_media_file(path):

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,pix_fmt",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("probe failed or timed out file=%s", path)
        return None

    if result.returncode != 0:
        log.warning("probe rejected file=%s", path)
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"Could not parse ffprobe output for {path}: {exc}")
        return None

    format_name = (data.get("format") or {}).get("format_name", "unknown")
    streams = data.get("streams", [])
    video_codec = next(
        (stream.get("codec_name", "unknown") for stream in streams if stream.get("codec_type") == "video"),
        "unknown",
    )
    audio_codec = next(
        (stream.get("codec_name", "unknown") for stream in streams if stream.get("codec_type") == "audio"),
        "unknown",
    )
    pix_fmt = next(
        (stream.get("pix_fmt", "unknown") for stream in streams if stream.get("codec_type") == "video"),
        "unknown",
    )

    return {
        "container": format_name,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "pix_fmt": pix_fmt,
        "duration": (data.get("format") or {}).get("duration"),
    }


# Video codecs considered compatible with MP4 + Windows Media Player.
# Everything else triggers an H.264 re-encode.
_COMPATIBLE_VIDEO_CODECS = {"h264", "avc", "avc1"}


def finalize_to_compatible_mp4(path):
    """Ensure *path* becomes a universally-compatible MP4 file.

    Rules
    -----
    * If the file is already MP4 / H.264 / AAC / yuv420p → no work needed.
    * If the container is wrong → at minimum remux.
    * If the video codec is not H.264 (VP9, AV1, …) → re-encode video to
      libx264 with ``-pix_fmt yuv420p``.
    * If the pixel format is not yuv420p → re-encode video.
    * If the audio codec is not AAC → re-encode audio to AAC.
    * Add ``-movflags +faststart`` when remuxing or encoding.
    * Writes to a temporary file first; only replaces the original on success.
    """

    probe = probe_media_file(path)
    if not probe:
        return None

    container = (probe["container"] or "unknown").lower()
    video_codec = (probe["video_codec"] or "unknown").lower()
    audio_codec = (probe["audio_codec"] or "unknown").lower()
    pix_fmt = (probe["pix_fmt"] or "unknown").lower()

    print(f"Probe  – container: {container}")
    print(f"Probe  – video codec: {video_codec}")
    print(f"Probe  – audio codec: {audio_codec}")
    print(f"Probe  – pix_fmt: {pix_fmt}")
    print(f"Probe  – file: {path}")

    need_video_reencode = video_codec not in _COMPATIBLE_VIDEO_CODECS or pix_fmt != "yuv420p"
    need_audio_reencode = audio_codec != "aac"
    is_mp4 = "mp4" in container

    # Fast path: nothing to do at all.
    if is_mp4 and not need_video_reencode and not need_audio_reencode:
        print("File is already a compatible MP4 – no processing needed.")
        return path

    action_parts = []
    if need_video_reencode:
        action_parts.append(f"re-encode video ({video_codec}/{pix_fmt} → h264/yuv420p)")
    if need_audio_reencode:
        action_parts.append(f"re-encode audio ({audio_codec} → aac)")
    if not is_mp4:
        action_parts.append(f"remux container ({container} → mp4)")
    print(f"Finalize actions: {', '.join(action_parts)}")

    # Build ffmpeg command --------------------------------------------------
    temp_path = path.with_name(f"{path.stem}.finalize_tmp.mp4")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(path),
    ]

    if need_video_reencode:
        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
        ]
    else:
        cmd += ["-c:v", "copy"]

    if need_audio_reencode:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:a", "copy"]

    cmd += ["-movflags", "+faststart", str(temp_path)]

    print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False,
                                timeout=FINALIZE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("finalization failed or timed out file=%s", path)
        temp_path.unlink(missing_ok=True)
        return None

    if result.returncode != 0:
        log.warning("finalization rejected file=%s exit=%s", path, result.returncode)
        temp_path.unlink(missing_ok=True)
        return None

    # Validate the temp file before committing ------------------------------
    tmp_probe = probe_media_file(temp_path)
    if not tmp_probe:
        print("Could not probe finalized temp file.")
        temp_path.unlink(missing_ok=True)
        return None

    tmp_container = (tmp_probe["container"] or "unknown").lower()
    tmp_video = (tmp_probe["video_codec"] or "unknown").lower()
    tmp_audio = (tmp_probe["audio_codec"] or "unknown").lower()
    tmp_pix = (tmp_probe["pix_fmt"] or "unknown").lower()

    print(f"Temp   – container: {tmp_container}")
    print(f"Temp   – video codec: {tmp_video}")
    print(f"Temp   – audio codec: {tmp_audio}")
    print(f"Temp   – pix_fmt: {tmp_pix}")

    if not compatible_media(tmp_probe):
        print("Finalized temp file does not meet compatibility requirements – aborting.")
        temp_path.unlink(missing_ok=True)
        return None

    # Atomically swap -------------------------------------------------------
    temp_path.replace(path)

    print(f"Finalized successfully: {path}")
    return path


def parse_progress(text):

    if "[download]" not in text:
        return None

    percent_match = re.search(r"(\d+(?:\.\d+)?)%", text)
    size_match = re.search(
        r"\s(\d+(?:\.\d+)?(?:KiB|MiB|GiB|TiB|B))\s+of",
        text,
        re.IGNORECASE,
    )
    if not size_match:
        size_match = re.search(
            r"\[download\]\s+(\d+(?:\.\d+)?(?:KiB|MiB|GiB|TiB|B))",
            text,
            re.IGNORECASE,
        )
    speed_match = re.search(r"at\s+(\S+/s)", text, re.IGNORECASE)
    eta_match = re.search(r"ETA\s+(\S+)", text)

    return {
        "percent": float(percent_match.group(1)) if percent_match else None,
        "size": size_match.group(1) if size_match else None,
        "speed": speed_match.group(1) if speed_match else None,
        "eta": eta_match.group(1) if eta_match else None,
    }


def live_progress(title, progress):

    parts = ["🔴 Lagi Rekam LIVE", "", f"🎬 {title}"]

    if progress["size"]:
        parts.append(f"⏺ {progress['size']} terekam")

    if progress["speed"]:
        parts.append(f"⚡ {progress['speed']}")

    return "\n\n".join(parts)


# Queue claim and the bot's Oryx reservation share the same Redis ownership key.
CLAIM_JOB = """
local payload = redis.call('LINDEX', 'download_queue', 0)
if not payload then return nil end
local job = cjson.decode(payload)
local owner = redis.call('GET', 'capture:owner')
if owner and owner ~= job.job_id then return nil end
redis.call('LPOP', 'download_queue')
redis.call('SET', 'capture:owner', job.job_id, 'EX', 30)
redis.call('SET', 'active:' .. tostring(job.chat_id), job.job_id, 'EX', 30)
return payload
"""


async def heartbeat(job=None):
    while True:
        r.set('worker:heartbeat', '1', ex=15)
        if job:
            for key in ('capture:owner', f"active:{job['chat_id']}"):
                renewed = r.eval(
                    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                    "return redis.call('EXPIRE', KEYS[1], 30) end return 0",
                    1, key, job['job_id'])
                if not renewed:
                    raise RuntimeError('Recording ownership lost')
        await asyncio.sleep(3)


async def set_state(job, status, state, detail=''):
    r.set(f"state:{job['job_id']}", state, ex=86400)
    public = {key: job[key] for key in ('job_id', 'source', 'source_name', 'note', 'origin', 'started_at', 'elapsed', 'size', 'filename') if key in job}
    public.update(state=state, detail=detail)
    r.set('web:job:' + job['job_id'], json.dumps(public), ex=86400)
    if os.getenv('DATA_DIR'):
        try:
            await asyncio.to_thread(storage.save_recording, job, state, detail)
        except Exception:
            log.error('job=%s catalogue_write=failed', job['job_id'])
    log.info('job=%s source=%s state=%s', job['job_id'], job['source'], state)
    if status:
        labels = {
            'starting': '⏳ Starting: menghubungkan sumber...',
            'recording': '🔴 Recording: capture sedang berjalan.',
            'stopping': '🛑 Stopping: menunggu capture berhenti...',
            'finalizing': '🔧 Finalizing: menyiapkan dan memvalidasi MP4...',
            'ready': '✅ Ready: file siap digunakan.',
            'failed': '❌ Failed: file belum siap.',
        }
        await edit(job['chat_id'], status.message_id,
                   labels[state] + ('\n' + detail if detail else ''))


def set_archive_state(job_id, state, **fields):
    payload = {"archive_status": state, **fields}
    r.set(f"archive:state:{job_id}", json.dumps(payload), ex=604800)
    if not os.getenv('DATA_DIR'):
        return
    try:
        storage.save_archive_state(job_id, state, **fields)
    except Exception:
        log.error("job=%s archive catalogue_write=failed", job_id)


def enqueue_archive(job, final_path):
    """Queue the shared archive pipeline only after producer success."""
    if not ARCHIVE_ENABLED or job.get("archive") is False:
        set_archive_state(job["job_id"], "local")
        return False
    payload = {"job_id": job["job_id"], "source": job["source"],
               "path": str(final_path), "attempt": 1}
    r.rpush("archive_queue", json.dumps(payload))
    set_archive_state(job["job_id"], "archive_pending", archive_attempt=0)
    log.info("ARCHIVE QUEUED job=%s file=%s attempt=1 bytes=%s",
             job["job_id"], final_path.name, final_path.stat().st_size)
    return True


async def run_archive(job):
    attempt = int(job.get("attempt", 1))
    source = Path(job["path"])
    set_archive_state(job["job_id"], "archiving", archive_attempt=attempt)
    log.info("ARCHIVE STARTED job=%s file=%s attempt=%s", job["job_id"], source.name, attempt)
    try:
        result = await asyncio.to_thread(
            archive.archive_file, source, DOWNLOAD_DIR, ARCHIVE_PATH)
        log.info("ARCHIVE COPY COMPLETED job=%s file=%s attempt=%s bytes=%s",
                 job["job_id"], source.name, attempt, result["bytes"])
        log.info("ARCHIVE VERIFY SUCCESS job=%s file=%s attempt=%s sha256=%s",
                 job["job_id"], source.name, attempt, result["sha256"])
        archived_at = time.time()
        set_archive_state(
            job["job_id"], "archived", archive_path=result["path"],
            archive_attempt=attempt, archive_sha256=result["sha256"],
            archived_at=archived_at,
            local_cleanup_after=archived_at + ARCHIVE_LOCAL_RETENTION_HOURS * 3600)
        log.info("ARCHIVE COMPLETED job=%s file=%s attempt=%s bytes=%s",
                 job["job_id"], source.name, attempt, result["bytes"])
    except archive.ArchiveConflict as exc:
        set_archive_state(job["job_id"], "archive_conflict",
                          archive_attempt=attempt, archive_error=str(exc))
        log.error("ARCHIVE FAILED job=%s file=%s attempt=%s error=%s conflict=true",
                  job["job_id"], source.name, attempt, exc)
    except Exception as exc:
        set_archive_state(job["job_id"], "archive_failed",
                          archive_attempt=attempt, archive_error=str(exc))
        log.error("ARCHIVE FAILED job=%s file=%s attempt=%s error_type=%s error=%s",
                  job["job_id"], source.name, attempt, type(exc).__name__, exc)
        if attempt <= ARCHIVE_MAX_RETRIES:
            retry = dict(job, attempt=attempt + 1)
            delay = ARCHIVE_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            r.zadd("archive_delayed", {json.dumps(retry): time.time() + delay})
            set_archive_state(job["job_id"], "archive_pending",
                              archive_attempt=attempt, archive_error=str(exc))
            log.info("ARCHIVE RETRY job=%s file=%s attempt=%s delay_seconds=%s",
                     job["job_id"], source.name, attempt + 1, delay)


async def archive_worker():
    """Single conservative archive consumer, independent of capture jobs."""
    while True:
        try:
            now = time.time()
            for payload in r.zrangebyscore("archive_delayed", 0, now):
                if r.zrem("archive_delayed", payload):
                    r.rpush("archive_queue", payload)
            payload = r.lpop("archive_queue")
            if payload:
                await run_archive(json.loads(payload))
            else:
                await asyncio.sleep(1)
        except redis.exceptions.RedisError:
            log.error("Archive worker: Redis unavailable")
            await asyncio.sleep(3)
        except Exception:
            log.exception("Archive worker recovered from unexpected error")
            await asyncio.sleep(1)


def signal_group(process, sig):
    try:
        if os.name == 'posix':
            os.killpg(process.pid, sig)
        elif process.returncode is None:
            process.send_signal(sig)
    except ProcessLookupError:
        pass


async def stop_process(process, job_id=None, chat_id=None):
    # Every capture starts in its own session, including yt-dlp's children.
    # Do not release job ownership here: finalization still owns the worker.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        signal_group(process, sig)
        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT)
            # The parent can exit before a downloader child. Check the group
            # before deciding that shutdown has completed.
            if os.name != 'posix':
                return
            deadline = asyncio.get_running_loop().time() + STOP_TIMEOUT
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(0.1)
        except asyncio.TimeoutError:
            pass
    if process.returncode is None:
        raise RuntimeError('Capture process did not exit')


async def spawn(command):
    return await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=(os.name == 'posix'))


class SourceInspectionError(RuntimeError):
    """Only fixed, credential-free diagnostics may reach logs or the UI."""
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(code)


def inspection_error(output):
    lines = output.decode(errors='replace').lower().splitlines()
    # Prefer fatal errors so unrelated warnings do not hide the actual cause.
    text = '\n'.join(line for line in lines if line.startswith('error:')) or '\n'.join(lines)
    cases = [
        (('impersonation target', 'impersonate'), 'browser_support',
         'Extractor membutuhkan dukungan browser impersonation. Rebuild image dengan dependensi curl-cffi.'),
        (('captcha', 'challenge'), 'challenge',
         'TikTok meminta verifikasi browser. Akses dari server belum berhasil.'),
        (('not currently live', 'livestream has ended'), 'not_live',
         'TikTok melaporkan akun tidak live atau room live tidak terbaca. Pastikan akun sedang live; pembatasan akses juga dapat menyebabkan respons ini.'),
        (('login required', 'log in', 'sign in', 'requiring login'), 'login_required',
         'Sumber meminta login. Sesi browser diperlukan untuk mengakses siaran ini.'),
        (('http error 403', 'http error 429'), 'access_denied',
         'Akses sumber ditolak atau dibatasi (HTTP 403/429). Coba lagi setelah beberapa saat.'),
        (('http error 400',), 'http_400',
         'API sumber menolak permintaan (HTTP 400). Extractor mungkin perlu diperbarui.'),
        (('timed out', 'timeout'), 'timeout', 'Koneksi ke sumber melewati batas waktu.'),
        (('unable to download', 'name resolution', 'connection refused'), 'network',
         'Worker gagal mengunduh informasi sumber. Periksa koneksi jaringan server.'),
        (('unable to extract', 'no video formats', 'requested format is not available'), 'extractor',
         'Extractor tidak menemukan informasi atau format siaran. Periksa akses sumber dan versi yt-dlp.'),
    ]
    for needles, code, detail in cases:
        if any(needle in text for needle in needles):
            return SourceInspectionError(code, detail)
    return SourceInspectionError('unknown', 'yt-dlp gagal membaca informasi sumber sebelum capture dimulai. Perlu diagnosis extractor dari worker.')


async def inspect_youtube(job):
    process = await spawn(['yt-dlp', '--dump-single-json', '--skip-download',
                           '--no-playlist', job['url']])
    log.info('job=%s source=%s inspect_pid=%s', job['job_id'], job['source'], process.pid)
    communication = asyncio.create_task(process.communicate())
    deadline = time.monotonic() + STARTUP_TIMEOUT
    try:
        while not communication.done():
            if r.exists(f"stop:{job['job_id']}"):
                raise RuntimeError('Stopped before capture started')
            if time.monotonic() >= deadline:
                raise TimeoutError('Source inspection timed out')
            await asyncio.sleep(0.25)
        stdout, _ = communication.result()
        if process.returncode:
            raise inspection_error(stdout)
        # stderr is merged, so find the JSON line without logging diagnostics.
        for line in stdout.decode(errors='replace').splitlines():
            if line.startswith('{'):
                return json.loads(line)
        raise RuntimeError('Missing source metadata')
    finally:
        await stop_process(process)
        if not communication.done():
            communication.cancel()
        await asyncio.gather(communication, return_exceptions=True)


def capture_command(job):
    job_id = job['job_id']
    if job['source'] == 'oryx':
        stream_url = storage.validate_url(job.get('stream_url', ORYX_STREAM_URL))
        if not stream_url or urlsplit(stream_url).scheme not in {
            'http', 'https', 'rtmp', 'rtmps', 'srt'
        }:
            raise ValueError('ORYX_STREAM_URL is missing or unsupported')
        path = DOWNLOAD_DIR / f'{job_id}-capture.ts'
        return [
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-rw_timeout', str(int(IDLE_TIMEOUT * 1000000)),
            '-i', stream_url, '-map', '0:v:0', '-map', '0:a:0?',
            '-c', 'copy', '-f', 'mpegts', '-flush_packets', '1',
            '-progress', 'pipe:1', '-nostats', str(path),
        ], path
    return [
        'yt-dlp', '-f',
        'bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*[vcodec^=avc1]+ba/bv*+ba/b',
        '--merge-output-format', 'mp4', '--remux-video', 'mp4',
        '--no-playlist', '--newline', '--retries', '10',
        '--fragment-retries', '10', '--continue',
        '-o', str(DOWNLOAD_DIR / f'{job_id}-%(title).200B.%(ext)s'), job['url'],
    ], None


async def capture(job, status, lease):
    command, temp_path = capture_command(job)
    process = await spawn(command)
    log.info('job=%s source=%s capture_pid=%s temporary=%s',
             job['job_id'], job['source'], process.pid,
             temp_path or f"{job['job_id']}-*")
    # Consume output continuously, but never log raw downloader diagnostics:
    # these may contain private playback URLs, tokens, or HTTP headers.
    activity = {'media': 0, 'percent': None}

    async def drain():
        while True:
            line = await process.stdout.readline()
            if not line:
                return
            text = line.decode(errors='replace').strip()
            if job['source'] in {'youtube', 'tiktok'}:
                progress = parse_progress(text)
                if progress:
                    activity['percent'] = progress['percent']
            elif text.startswith('out_time_us='):
                try:
                    activity['media'] = max(activity['media'], int(text.split('=', 1)[1]))
                except ValueError:
                    pass

    reader = asyncio.create_task(drain())
    started = last_activity = time.monotonic()
    last_measure = None
    recording = False
    reason = 'completed'
    last_update = 0
    try:
        while process.returncode is None:
            if lease.done():
                lease.result()
            if reader.done():
                reader.result()
            if r.exists(f"stop:{job['job_id']}"):
                reason = 'operator_stop'
                await set_state(job, status, 'stopping')
                break
            files = list(DOWNLOAD_DIR.glob(f"{job['job_id']}-*"))
            measure = (sum(p.stat().st_size for p in files if p.is_file()),
                       activity['media'])
            now = time.monotonic()
            if measure != last_measure and (measure[0] > 0 or measure[1] > 0):
                last_activity = now
                if not recording:
                    recording = True
                    job['started_at'] = time.time()
                    await set_state(job, status, 'recording')
                last_measure = measure
            if not recording and now - started > STARTUP_TIMEOUT:
                reason = 'startup_timeout'
                break
            if recording and now - last_activity > IDLE_TIMEOUT:
                reason = 'source_idle_timeout'
                break
            if recording and now - last_update >= 5:
                job.update(size=measure[0], elapsed=round(now - started, 1))
                detail = f"💾 {format_size(measure[0])}"
                if activity['percent'] is not None:
                    detail += f" ({activity['percent']:.1f}%)"
                await set_state(job, status, 'recording', detail)
                last_update = now
            await asyncio.sleep(0.25)
        if reason == 'completed' and process.returncode not in (None, 0):
            reason = 'source_error'
    finally:
        await stop_process(process)
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    log.info('job=%s source=%s stop_reason=%s exit=%s',
             job['job_id'], job['source'], reason, process.returncode)
    return reason


def compatible_media(info):
    try:
        return bool(info and 'mp4' in info['container']
                    and info['video_codec'] in _COMPATIBLE_VIDEO_CODECS
                    and info['audio_codec'] == 'aac'
                    and info['pix_fmt'] == 'yuv420p'
                    and float(info.get('duration') or 0) > 0)
    except (ValueError, TypeError):
        return False


def complete_recording(job):
    # Interrupted yt-dlp files can still contain usable media, including .part.
    # Prefer an already merged A/V file; never publish a video-only fragment.
    candidates = sorted(
        (p for p in DOWNLOAD_DIR.glob(f"{job['job_id']}-*")
         if p.is_file() and p.stat().st_size > 0
         and not p.name.endswith('.ytdl') and '.finalize_tmp.' not in p.name),
        key=lambda p: (p.suffix != '.part', p.stat().st_mtime), reverse=True)
    separate_video = separate_audio = None
    for path in candidates:
        info = probe_media_file(path)
        if not info:
            continue
        if info['video_codec'] != 'unknown' and info['audio_codec'] == 'unknown':
            separate_video = separate_video or path
            continue
        if info['audio_codec'] != 'unknown' and info['video_codec'] == 'unknown':
            separate_audio = separate_audio or path
            continue
        if info['video_codec'] == 'unknown' or info['audio_codec'] == 'unknown':
            continue
        final_path = finalize_to_compatible_mp4(path)
        if final_path is None or not compatible_media(probe_media_file(final_path)):
            continue
        # One global worker reservation protects the existing date sequence.
        # A rename error must propagate: no ready message without final naming.
        target = generate_final_filename(job.get('title', ''), note=job.get('note', ''))
        final_path.rename(target)
        log.info('job=%s source=%s finalization=passed final=%s',
                 job['job_id'], job['source'], target)
        return target
    if separate_video and separate_audio:
        # yt-dlp may be interrupted before its own separate-track merger runs.
        # Keep both originals until a merged, compatible clip has been verified.
        merged = DOWNLOAD_DIR / f"{job['job_id']}-recovered.mkv"
        try:
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-y', '-i', str(separate_video),
                '-i', str(separate_audio), '-map', '0:v:0', '-map', '1:a:0',
                '-c', 'copy', '-shortest', str(merged),
            ], capture_output=True, timeout=FINALIZE_TIMEOUT, check=False)
            if result.returncode == 0:
                final_path = finalize_to_compatible_mp4(merged)
                if final_path and compatible_media(probe_media_file(final_path)):
                    target = generate_final_filename(job.get('title', ''), note=job.get('note', ''))
                    final_path.rename(target)
                    log.info('job=%s source=%s finalization=passed final=%s recovery=tracks',
                             job['job_id'], job['source'], target)
                    return target
        except (OSError, subprocess.TimeoutExpired):
            log.warning('job=%s track recovery failed', job['job_id'])
    raise RuntimeError('No usable audio/video file; temporary media retained')


async def run_download(job):
    job.setdefault('source', 'youtube')
    job.setdefault('job_id', uuid.uuid4().hex)
    job.setdefault('requested_at', time.time())
    job.setdefault('origin', 'telegram')
    lease = asyncio.create_task(heartbeat(job))
    status = None
    stage = 'starting'
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        if job.get('origin') != 'web':
            status = await send(job['chat_id'], '⏳ Starting: menyiapkan capture...')
        await set_state(job, status, 'starting')
        if not storage.disk_status(DOWNLOAD_DIR)['can_record']:
            await set_state(job, status, 'failed', 'Ruang disk downloads terlalu rendah atau tidak bisa diperiksa. Kosongkan ruang sebelum merekam.')
            return
        if job['source'] not in {'youtube', 'oryx', 'tiktok'}:
            raise ValueError('Unsupported source type')
        if job['source'] in {'oryx', 'tiktok'} and time.time() - job.get('requested_at', 0) > 10:
            raise RuntimeError('Record request expired; please send /record again')
        if r.exists(f"stop:{job['job_id']}"):
            raise RuntimeError('Stopped before capture started')
        if job['source'] in {'youtube', 'tiktok'}:
            if job['source'] == 'tiktok':
                job['url'] = storage.validate_tiktok_url(job['url'])
            stage = 'inspection'
            info = await inspect_youtube(job)
            if job['source'] == 'tiktok' and info.get('is_live') is not True:
                await set_state(job, status, 'failed', 'Akun TikTok belum live atau siaran tidak dapat diakses. Coba lagi saat akun sedang live.')
                return
            job['title'] = info.get('title', 'Unknown')
        stage = 'capture'
        reason = await capture(job, status, lease)
        job['stop_reason'] = reason
        if job.get('started_at'):
            job['elapsed'] = round(time.time() - job['started_at'], 1)
        await set_state(job, status, 'finalizing')
        stage = 'finalization'
        final_path = await asyncio.to_thread(complete_recording, job)
        job.update(filename=final_path.name, size=final_path.stat().st_size)
        detail = f"📁 {final_path.name}\n💾 {format_size(final_path.stat().st_size)}"
        if reason not in {'completed', 'operator_stop'}:
            detail += '\n⚠️ Sumber terputus/timeout; hanya media yang berhasil direkam disimpan.'
        await set_state(job, status, 'ready', detail)
        # Producer success is committed first. Archive failures are isolated.
        try:
            enqueue_archive(job, final_path)
        except Exception as exc:
            set_archive_state(job['job_id'], 'archive_failed', archive_error=str(exc))
            log.error('ARCHIVE FAILED job=%s file=%s attempt=0 error_type=%s',
                      job['job_id'], final_path.name, type(exc).__name__)
    except Exception as exc:
        # Exception text can include a command/URL; only log its class.
        log.error('job=%s source=%s stage=%s error_type=%s reason=%s',
                  job['job_id'], job['source'], stage, type(exc).__name__,
                  exc.code if isinstance(exc, SourceInspectionError) else 'unknown')
        await set_state(job, status, 'failed',
                        exc.detail if isinstance(exc, SourceInspectionError) else
                        'Sumber tidak tersedia, capture berhenti sebelum ada media, atau '
                        'finalisasi gagal. File sementara yang ada tetap disimpan; '
                        'cek log dan coba lagi.')
    finally:
        lease.cancel()
        await asyncio.gather(lease, return_exceptions=True)
        clear_job_state(job['job_id'], job['chat_id'])


async def main():
    log.info('Worker running')
    archival = asyncio.create_task(archive_worker())
    try:
        while True:
            try:
                r.set('worker:heartbeat', '1', ex=15)
                payload = r.eval(CLAIM_JOB, 0)
                if payload:
                    await run_download(json.loads(payload))
                else:
                    await asyncio.sleep(0.25)
            except redis.exceptions.RedisError:
                log.error('Redis unavailable')
                await asyncio.sleep(3)
    finally:
        archival.cancel()
        await asyncio.gather(archival, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
