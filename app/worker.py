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
import selectors
from collections import deque
from urllib.parse import urlsplit
from datetime import datetime

# pyrefly: ignore [missing-import]
import redis
# pyrefly: ignore [missing-import]
from telegram import Bot
try:
    from . import archive, quality, storage, telegram_controls
except ImportError:
    import archive
    import quality
    import storage
    import telegram_controls


DOWNLOAD_DIR = Path("/downloads")
STARTUP_TIMEOUT = float(os.getenv("CAPTURE_STARTUP_TIMEOUT", "30"))
IDLE_TIMEOUT = float(os.getenv("CAPTURE_IDLE_TIMEOUT", "60"))
STOP_TIMEOUT = float(os.getenv("CAPTURE_STOP_TIMEOUT", "10"))
FINALIZE_TIMEOUT = float(os.getenv("FINALIZE_TIMEOUT", "600"))
FINALIZE_DURATION_MULTIPLIER = max(0, float(os.getenv('FINALIZE_DURATION_MULTIPLIER', '4')))
FINALIZE_TIMEOUT_CAP = max(FINALIZE_TIMEOUT, float(os.getenv('FINALIZE_TIMEOUT_CAP', '21600')))
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "20"))
YOUTUBE_POSTLIVE_ATTEMPTS = max(1, min(5, int(os.getenv("YOUTUBE_POSTLIVE_ATTEMPTS", "3"))))
YOUTUBE_POSTLIVE_RETRY_DELAY = max(1, float(os.getenv("YOUTUBE_POSTLIVE_RETRY_DELAY", "20")))
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


def generate_final_filename(title="", note="", extension="mp4"):
    """
    Generate DDMMYYNN - Title.ext, using a manual title when supplied and
    otherwise the source title. NN is the download number for that date.
    Uses the server's local timezone.
    """
    # Get current date in server's local timezone
    now = datetime.now()
    date_str = now.strftime("%d%m%y")

    extension = quality.validate_format(extension)
    patterns = (
        re.compile(rf"^{re.escape(date_str)}(\d+)(?: - .+)?\.(?:mp4|mp3|ts|mkv|webm)$", re.IGNORECASE),
        # Keep counting files created with the previous Title - DDMMYYNN format.
        re.compile(rf"^.+ -{re.escape(date_str)}(\d+)\.(?:mp4|mp3|ts|mkv|webm)$", re.IGNORECASE),
    )
    download_numbers = []
    for path in DOWNLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        match = None
        for candidate in patterns:
            match = candidate.match(path.name)
            if match:
                break
        if match:
            download_numbers.append(int(match.group(1)))
    next_number = max(download_numbers, default=0) + 1
    label = str(note).strip() or str(title).strip()
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', ' ', label)
    label = ' '.join(label.split()).strip('. ')
    label = label.encode('utf-8')[:160].decode('utf-8', errors='ignore').rstrip('. ')
    stem = f"{date_str}{next_number:02d}" + (f" - {label}" if label else "")
    return DOWNLOAD_DIR / f"{stem}.{extension}"


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
                "format=format_name,duration:stream=codec_type,codec_name,pix_fmt,duration",
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
    video_duration = next(
        (stream.get("duration") for stream in streams if stream.get("codec_type") == "video"),
        None,
    )
    audio_duration = next(
        (stream.get("duration") for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )

    return {
        "container": format_name,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "pix_fmt": pix_fmt,
        "duration": (data.get("format") or {}).get("duration"),
        "video_duration": video_duration,
        "audio_duration": audio_duration,
    }


class ProcessingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def processing_timeout(info):
    return min(FINALIZE_TIMEOUT_CAP, FINALIZE_TIMEOUT + (duration_seconds(info) or 0) * FINALIZE_DURATION_MULTIPLIER)


def processing_checkpoint(job):
    """Durable Original checkpoint and the existing shared job/progress surface."""
    detail = 'Memproses · Original sudah tersimpan.'
    if os.getenv('DATA_DIR'):
        storage.save_recording(job, 'finalizing', detail)
    try:
        current = json.loads(r.get('web:job:' + job['job_id']) or '{}')
        current.update({key: job[key] for key in ('filename', 'size', 'original_filename', 'processing_status', 'progress_percent', 'progress_phase', 'eta_seconds') if key in job})
        current.update(job_id=job['job_id'], state='finalizing', detail=detail)
        r.set('web:job:' + job['job_id'], json.dumps(current), ex=86400)
        r.set('state:' + job['job_id'], 'finalizing', ex=86400)
    except redis.exceptions.RedisError:
        log.warning('job=%s processing progress unavailable', job['job_id'])


def processing_stopped(job):
    return bool(job and r.exists('stop:' + job['job_id']))


def run_processing(command, path, info, job):
    """Bounded FFmpeg execution; cancellation never touches captured media."""
    disk = storage.disk_status(path.parent)
    if not disk['available'] or disk['free'] < disk['minimum'] + path.stat().st_size * 2:
        raise ProcessingError('disk_space')
    if processing_stopped(job):
        raise ProcessingError('cancelled')
    duration = duration_seconds(info)
    deadline = time.monotonic() + processing_timeout(info)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    buffer = b''
    last_update = 0
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while process.poll() is None:
            if processing_stopped(job):
                raise ProcessingError('cancelled')
            if time.monotonic() >= deadline:
                raise ProcessingError('timeout')
            for key, _ in selector.select(.2):
                buffer += os.read(key.fd, 65536)
                lines = buffer.split(b'\n')
                buffer = lines.pop()
                for line in lines:
                    if line.startswith(b'out_time_us=') and duration:
                        try:
                            seconds = int(line.partition(b'=')[2]) / 1_000_000
                            if seconds >= 0:
                                job['progress_percent'] = min(99, round(seconds / duration * 100, 1))
                        except ValueError:
                            pass
            if time.monotonic() - last_update >= 2:
                disk = storage.disk_status(path.parent)
                if not disk['can_record']:
                    raise ProcessingError('disk_space')
                processing_checkpoint(job)
                last_update = time.monotonic()
        if process.returncode:
            raise ProcessingError('ffmpeg_failed')
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=min(STOP_TIMEOUT, 3))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        process.stdout.close()


def finalize_to_compatible_mp4(path, compression="original", allow_silent=False, job=None):
    """Finalize *path* as an MP4 using the selected size preset.

    Rules
    -----
    * Original keeps an existing MP4 / H.264 / AAC / yuv420p unchanged.
    * Seimbang always encodes H.264 CRF 23 plus AAC 128 kbps.
    * Hemat always encodes H.265 CRF 27 plus AAC 128 kbps.
    * If the container is wrong → at minimum remux.
    * Add ``-movflags +faststart`` when remuxing or encoding.
    * Writes to a temporary file first; job processing never overwrites Original.
    """

    compression = quality.validate_preset(compression)
    preset = quality.video_preset(compression)
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

    need_video_reencode = (preset["force_encode"]
                           or video_codec not in preset["codecs"]
                           or pix_fmt != "yuv420p")
    need_audio_reencode = (preset["force_encode"] or audio_codec != "aac") and not (allow_silent and audio_codec == 'unknown')
    is_mp4 = "mp4" in container

    # Fast path: nothing to do at all.
    if is_mp4 and not need_video_reencode and not need_audio_reencode:
        print("File is already a compatible MP4 – no processing needed.")
        return path

    action_parts = []
    if need_video_reencode:
        target_codec = "hevc" if preset["codec"] == "libx265" else "h264"
        action_parts.append(
            f"re-encode video ({video_codec}/{pix_fmt} → {target_codec}/yuv420p)"
        )
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
            "-c:v", preset["codec"],
            "-preset", preset["encoder_preset"],
            "-crf", preset["crf"],
            "-pix_fmt", "yuv420p",
        ]
        if preset["codec"] == "libx265":
            cmd += ["-tag:v", "hvc1"]
    else:
        cmd += ["-c:v", "copy"]

    if need_audio_reencode:
        cmd += ["-c:a", "aac", "-b:a", preset["audio_bitrate"]]
    else:
        cmd += ["-c:a", "copy"]

    cmd += ["-movflags", "+faststart"]
    if job is not None:
        cmd += ['-v', 'error', '-nostats', '-progress', 'pipe:1']
    cmd += [str(temp_path)]

    print(f"Running: {' '.join(cmd)}")

    try:
        if job is not None:
            run_processing(cmd, path, probe, job)
            result = subprocess.CompletedProcess(cmd, 0)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False,
                                    timeout=processing_timeout(probe))
    except ProcessingError:
        temp_path.unlink(missing_ok=True)
        raise
    except (OSError, subprocess.TimeoutExpired):
        log.warning("finalization failed or timed out file=%s", path)
        temp_path.unlink(missing_ok=True)
        return None
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

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

    if not compatible_media(tmp_probe, compression, allow_silent=allow_silent) or not durations_aligned(probe, tmp_probe):
        print("Finalized temp file does not meet compatibility requirements – aborting.")
        temp_path.unlink(missing_ok=True)
        return None

    # Atomically swap -------------------------------------------------------
    if job is not None:
        if processing_stopped(job):
            temp_path.unlink(missing_ok=True)
            raise ProcessingError('cancelled')
        target = path.with_name(path.stem + ('.' + compression if compression != 'original' else '') + '.mp4')
        if target == path:
            target = path.with_name(path.stem + '.processed.mp4')
        temp_path.replace(target)
        return target
    temp_path.replace(path)

    print(f"Finalized successfully: {path}")
    return path


def parse_progress(text):

    if text.startswith('streamfetch:'):
        fields = text.removeprefix('streamfetch:').split('|')
        if len(fields) != 3:
            return None
        video, audio, percentage = fields
        absent_codecs = {'none', 'na', '', 'unknown'}
        has_video = video.strip().lower() not in absent_codecs
        has_audio = audio.strip().lower() not in absent_codecs
        phase = 'media' if has_video and has_audio else 'video' if has_video else 'audio' if has_audio else None
        percent_match = re.search(r'(\d+(?:\.\d+)?)%', percentage)
        return {
            'percent': float(percent_match.group(1)) if percent_match else None,
            'phase': phase, 'size': None, 'speed': None, 'eta': None,
        }

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
    public = {key: job[key] for key in ('job_id', 'source', 'source_name', 'note', 'origin', 'quality', 'output_format', 'compression', 'is_live', 'live_status', 'was_live', 'download_attempt', 'download_attempts', 'download_exit_code', 'progress_percent', 'progress_phase', 'eta_seconds', 'started_at', 'elapsed', 'size', 'filename', 'original_filename', 'processing_status', 'processing_error', 'processing_detail', 'attempt_root_id', 'retry_of', 'attempt_number', 'attempt_total', 'error_code', 'error_title', 'error_message') if key in job}
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
            'waiting': '⏳ Waiting: menunggu YouTube menyiapkan arsip live...',
            'stopping': '🛑 Stopping: menunggu capture berhenti...',
            'finalizing': '🔧 Finalizing: menyiapkan dan memvalidasi file...',
            'ready': '✅ Ready: file siap digunakan.',
            'failed': '❌ Failed: file belum siap.',
        }
        visible_detail = job.get('error_message') if state == 'failed' else detail
        await edit(job['chat_id'], status.message_id,
                   labels[state] + ('\n' + visible_detail if visible_detail else ''))


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


async def spawn(command, merge_stderr=True):
    return await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
        start_new_session=(os.name == 'posix'))


class SourceInspectionError(RuntimeError):
    """Only fixed, credential-free diagnostics may reach logs or the UI."""
    def __init__(self, code, detail, diagnostic=None):
        self.code = code
        self.detail = detail
        self.diagnostic = diagnostic or detail
        super().__init__(code)


class MediaValidationError(RuntimeError):
    """A credential-free media failure suitable for logs and operator status."""
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(code)


def safe_diagnostic(text):
    """Keep useful yt-dlp diagnostics while removing URLs and common secrets."""
    text = re.sub(r'(?i)(?:https?|rtmps?|srt|tcp|udp)://\S+', '[URL]', str(text))
    text = re.sub(r'(?im)\b(authorization|cookie)\s*[:=][^\r\n]*', r'\1=[REDACTED]', text)
    text = re.sub(
        r'(?i)(authorization|cookie|token|signature|sig|key)=([^\s&]+)',
        r'\1=[REDACTED]', text,
    )
    return ''.join(ch for ch in text if ch == '\t' or ord(ch) >= 32)[:1200]


def diagnostic_detail(job):
    diagnostics = job.get('_capture_diagnostics') or []
    tool = 'FFmpeg' if job.get('source') == 'oryx' else 'yt-dlp'
    message = diagnostics[-1] if diagnostics else f'{tool} berhenti sebelum menghasilkan media lengkap.'
    exit_code = job.get('_capture_exit_code')
    prefix = f'{tool} berhenti dengan exit code {exit_code}' if exit_code is not None else f'{tool} berhenti'
    return f'{prefix}: {message}'


def classify_failure(job, exc):
    """Return concise UI copy separately from sanitized technical diagnostics."""
    if isinstance(exc, SourceInspectionError):
        technical = safe_diagnostic(exc.diagnostic)
        signal = f'{exc.code}\n{exc.detail}\n{technical}'.lower()
    elif isinstance(exc, MediaValidationError):
        technical = safe_diagnostic(exc.detail)
        signal = f'{exc.code}\n{technical}\n{diagnostic_detail(job)}'.lower()
    else:
        technical = safe_diagnostic(str(exc)) or diagnostic_detail(job)
        signal = f'{type(exc).__name__}\n{technical}\n{diagnostic_detail(job)}'.lower()
    if job.get('stop_reason') == 'operator_stop' or 'stopped before capture' in signal:
        return ('cancelled', 'Dibatalkan', 'Capture dibatalkan oleh operator.', technical)
    if any(value in signal for value in ('http error 403', 'http 403', 'access_denied')):
        return ('http_403', 'Gagal mengambil media',
                'YouTube menolak permintaan media. Coba lagi untuk mengambil sumber media baru.', technical)
    if any(value in signal for value in ('http error 404', 'http 404', 'video unavailable',
                                         'video tidak tersedia', 'not found')):
        return ('unavailable', 'Video tidak tersedia',
                'Video sudah dihapus, bersifat privat, atau tidak tersedia dari sumber.', technical)
    if any(value in signal for value in ('timed out', 'timeout', 'name resolution',
                                         'connection refused', 'network', 'unable to download')):
        return ('network', 'Koneksi bermasalah',
                'Koneksi ke sumber terputus atau melewati batas waktu. Coba lagi.', technical)
    return ('unknown', 'Capture gagal',
            'Capture belum berhasil. Coba lagi atau buka detail untuk diagnosis teknis.', technical)


def update_youtube_metadata(job, info):
    job['is_live'] = info.get('is_live') is True
    job['live_status'] = info.get('live_status') or ('is_live' if job['is_live'] else 'not_live')
    job['was_live'] = info.get('was_live') is True or job['live_status'] in {'post_live', 'was_live'}
    job['title'] = info.get('title') or job.get('title', '')
    if job.get('source') in {'youtube', 'tiktok', 'instagram'}:
        metadata = dict(job.get('source_metadata') or {})
        for key in ('title', 'description', 'channel', 'upload_date', 'uploader', 'uploader_id', 'timestamp', 'duration', 'id'):
            if info.get(key) is not None:
                metadata[key] = info[key]
        if job.get('source') == 'youtube' and re.fullmatch(r'[A-Za-z0-9_-]{11}', str(info.get('id') or '')):
            metadata['youtube_id'] = info['id']
        job['source_metadata'] = metadata


def inspection_error(output):
    lines = output.decode(errors='replace').splitlines()
    # Prefer fatal errors so unrelated warnings do not hide the actual cause.
    text = '\n'.join(line for line in lines if line.lower().startswith('error:')) or '\n'.join(lines)
    search = text.lower()
    diagnostic = safe_diagnostic(text)
    cases = [
        (('impersonation target', 'impersonate'), 'browser_support',
         'Extractor membutuhkan dukungan browser impersonation. Rebuild image dengan dependensi curl-cffi.'),
        (('captcha', 'challenge'), 'challenge',
         'Sumber meminta verifikasi browser. Akses dari server belum berhasil.'),
        (('not currently live', 'livestream has ended'), 'not_live',
         'TikTok melaporkan akun tidak live atau room live tidak terbaca. Pastikan akun sedang live; pembatasan akses juga dapat menyebabkan respons ini.'),
        (('login required', 'log in', 'sign in', 'requiring login', 'login-required', 'logged-in'), 'login_required',
         'Sumber meminta login atau membatasi akses. Video ini belum dapat diakses dari server.'),
        (('http error 403',), 'access_denied',
         'Akses sumber ditolak (HTTP 403). Coba lagi untuk mengambil sumber media baru.'),
        (('http error 429',), 'rate_limited',
         'Sumber membatasi terlalu banyak permintaan (HTTP 429). Coba lagi setelah beberapa saat.'),
        (('http error 400',), 'http_400',
         'API sumber menolak permintaan (HTTP 400). Extractor mungkin perlu diperbarui.'),
        (('http error 404', 'video unavailable', 'video is unavailable'), 'unavailable',
         'Video tidak tersedia dari sumber.'),
        (('timed out', 'timeout'), 'timeout', 'Koneksi ke sumber melewati batas waktu.'),
        (('unable to download', 'name resolution', 'connection refused'), 'network',
         'Worker gagal mengunduh informasi sumber. Periksa koneksi jaringan server.'),
        (('unable to extract', 'no video formats', 'requested format is not available'), 'extractor',
         'Extractor tidak menemukan informasi atau format video. Periksa akses sumber dan versi yt-dlp.'),
    ]
    for needles, code, detail in cases:
        if any(needle in search for needle in needles):
            return SourceInspectionError(code, detail, diagnostic)
    return SourceInspectionError('unknown', 'yt-dlp gagal membaca informasi sumber sebelum capture dimulai. Perlu diagnosis extractor dari worker.', diagnostic)


async def inspect_youtube(job):
    command = ['yt-dlp', '--dump-single-json', '--skip-download', '--no-playlist']
    if job['source'] in {'tiktok', 'instagram'}:
        command += ['--ignore-no-formats-error']
    process = await spawn(command + [job['url']])
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
    output_format = quality.validate_format(job.get('output_format', 'mp4'))
    if output_format == 'mp3' and job['source'] != 'youtube':
        raise ValueError('MP3 is only supported for YouTube downloads')
    command = [
        'yt-dlp', '-f', quality.ytdlp_selector(job.get('quality', 'best'), output_format),
        '--no-playlist', '--newline', '--retries', '10',
        '--fragment-retries', '10', '--file-access-retries', '3',
        '--retry-sleep', 'fragment:exp=1:20', '--continue',
    ]
    if job['source'] in {'youtube', 'tiktok', 'instagram'} and job.get('is_live') is False:
        # Report the current format separately: video and audio can each reach
        # 100%, so this is track progress, not overall download completion.
        command += [
            '--progress-template',
            'download:streamfetch:%(info.vcodec)s|%(info.acodec)s|%(progress._percent_str)s',
        ]
    if job.get('live_status') == 'post_live':
        # A post-live manifest is still changing. Do not silently accept holes;
        # the bounded outer retry will refresh metadata and resume the .part.
        command += ['--abort-on-unavailable-fragments']
    if job.get('playlist_item'):
        command += ['--playlist-items', str(job['playlist_item'])]
    if output_format == 'mp3':
        command += ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0']
    else:
        command += ['--merge-output-format', 'mp4', '--remux-video', 'mp4']
    # A stable temporary name lets a refreshed yt-dlp invocation reuse a
    # completed format and resume the missing .part/.ytdl stream.
    command += ['-o', str(DOWNLOAD_DIR / f'{job_id}-%(format_id)s.%(ext)s'), job['url']]
    return command, None


def download_size(job_id):
    """Return current job bytes while tolerating yt-dlp's atomic renames."""
    total = 0
    for path in DOWNLOAD_DIR.glob(f'{job_id}-*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # yt-dlp commonly renames format.part -> format between glob/stat.
            continue
    return total


def should_log_diagnostic(text):
    lowered = text.lower()
    return any(marker in lowered for marker in (
        'error:', 'warning:', 'retrying', 'unable to', 'failed',
        'fragment not found', 'unavailable fragment', 'http error',
    ))


async def capture(job, status, lease):
    command, temp_path = capture_command(job)
    process = await spawn(command, merge_stderr=False)
    log.info('job=%s source=%s capture_pid=%s temporary=%s',
             job['job_id'], job['source'], process.pid,
             temp_path or f"{job['job_id']}-*")
    activity = {'media': 0, 'percent': None, 'phase': None}
    job.update(progress_percent=None, progress_phase=None)
    diagnostics = deque(maxlen=40)
    output_tail = deque(maxlen=40)

    async def drain(stream, channel):
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors='replace').strip()
            parsed_progress = None
            if job['source'] in {'youtube', 'tiktok', 'instagram'}:
                parsed_progress = parse_progress(text)
                if parsed_progress:
                    activity['percent'] = parsed_progress['percent']
                    activity['phase'] = parsed_progress.get('phase')
            elif text.startswith('out_time_us='):
                try:
                    activity['media'] = max(activity['media'], int(text.split('=', 1)[1]))
                except ValueError:
                    pass
            if text and not parsed_progress and job['source'] in {'youtube', 'tiktok', 'instagram'}:
                safe = safe_diagnostic(text)
                if safe:
                    output_tail.append(f'{channel}: {safe}')
            if should_log_diagnostic(text):
                safe = safe_diagnostic(text)
                if safe:
                    diagnostics.append(f'{channel}: {safe}')
                    log.warning('job=%s yt_dlp_%s=%s', job['job_id'], channel, safe)

    readers = [asyncio.create_task(drain(process.stdout, 'stdout')),
               asyncio.create_task(drain(process.stderr, 'stderr'))]
    started = last_activity = time.monotonic()
    last_measure = None
    recording = False
    reason = 'completed'
    last_update = 0
    try:
        while process.returncode is None:
            if lease.done():
                lease.result()
            for reader in readers:
                if reader.done():
                    reader.result()
            if r.exists(f"stop:{job['job_id']}"):
                reason = 'operator_stop'
                await set_state(job, status, 'stopping')
                break
            measure = (download_size(job['job_id']), activity['media'])
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
                job.update(size=measure[0], elapsed=round(now - started, 1),
                           progress_percent=activity['percent'], progress_phase=activity['phase'])
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
        pending_readers = [reader for reader in readers if not reader.done()]
        if pending_readers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_readers, return_exceptions=True), timeout=2,
                )
            except asyncio.TimeoutError:
                for reader in pending_readers:
                    if not reader.done():
                        reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    job['_capture_exit_code'] = process.returncode
    job['download_exit_code'] = process.returncode
    if process.returncode not in (None, 0) and not diagnostics:
        diagnostics.extend(list(output_tail)[-10:])
        for line in diagnostics:
            log.warning('job=%s yt_dlp_output=%s', job['job_id'], line)
    job['_capture_diagnostics'] = list(diagnostics)
    job['_capture_output'] = list(output_tail)
    log.info('job=%s source=%s stop_reason=%s exit=%s',
             job['job_id'], job['source'], reason, process.returncode)
    return reason


async def wait_for_youtube_retry(job, status, lease, delay, attempt, total):
    detail = (
        'YouTube masih menyiapkan arsip live. '
        f'Mencoba lagi {attempt}/{total} dalam {int(delay)} detik; file parsial tetap disimpan.'
    )
    await set_state(job, status, 'waiting', detail)
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if lease.done():
            lease.result()
        if r.exists(f"stop:{job['job_id']}"):
            await set_state(job, status, 'stopping')
            return False
        await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))
    return True


async def capture_with_retries(job, status, lease):
    """Retry post-live YouTube downloads after refreshing their manifest."""
    post_live = job['source'] == 'youtube' and job.get('live_status') == 'post_live'
    attempts = YOUTUBE_POSTLIVE_ATTEMPTS if post_live else 1
    job['download_attempts'] = attempts
    last_reason = 'source_error'
    for attempt in range(1, attempts + 1):
        job['download_attempt'] = attempt
        job.update(progress_percent=None, progress_phase=None)
        if attempt > 1:
            delay = min(120, YOUTUBE_POSTLIVE_RETRY_DELAY * (2 ** (attempt - 2)))
            if not await wait_for_youtube_retry(job, status, lease, delay, attempt, attempts):
                return 'operator_stop'
            await set_state(
                job, status, 'starting',
                f'Memperbarui metadata YouTube sebelum percobaan {attempt}/{attempts}…',
            )
            try:
                update_youtube_metadata(job, await inspect_youtube(job))
            except SourceInspectionError as exc:
                safe = safe_diagnostic(exc.detail)
                job['_capture_diagnostics'] = [f'metadata: {safe}']
                job['_capture_exit_code'] = None
                log.warning('job=%s youtube_metadata_retry=%s attempt=%s/%s',
                            job['job_id'], exc.code, attempt, attempts)
                last_reason = 'inspection_error'
                continue
        last_reason = await capture(job, status, lease)
        if last_reason in {'completed', 'operator_stop'}:
            return last_reason
        if post_live:
            log.warning(
                'job=%s youtube_download_attempt=%s/%s reason=%s exit=%s detail=%s',
                job['job_id'], attempt, attempts, last_reason,
                job.get('_capture_exit_code'), diagnostic_detail(job),
            )
        else:
            log.warning(
                'job=%s capture_failed reason=%s exit=%s detail=%s',
                job['job_id'], last_reason, job.get('_capture_exit_code'),
                diagnostic_detail(job),
            )
    return last_reason


def duration_seconds(info, stream=None):
    if not info:
        return None
    value = info.get(f'{stream}_duration') if stream else None
    if value in (None, 'N/A'):
        value = info.get('duration')
    try:
        parsed = float(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def durations_aligned(video_info, audio_info=None):
    video_duration = duration_seconds(video_info, 'video')
    audio_duration = duration_seconds(audio_info or video_info, 'audio')
    if video_duration is None or audio_duration is None:
        return True
    longer = max(video_duration, audio_duration)
    tolerance = max(5.0, min(30.0, longer * 0.01))
    return abs(video_duration - audio_duration) <= tolerance


def compatible_media(info, compression="original", allow_silent=False):
    try:
        preset = quality.video_preset(compression)
        return bool(info and 'mp4' in info['container']
                    and info['video_codec'] in preset['codecs']
                    and (info['audio_codec'] == 'aac' or (allow_silent and info['audio_codec'] == 'unknown'))
                    and info['pix_fmt'] == 'yuv420p'
                    and float(info.get('duration') or 0) > 0
                    and durations_aligned(info))
    except (ValueError, TypeError):
        return False


def compatible_mp3(info):
    try:
        return bool(info and 'mp3' in info['container']
                    and info['audio_codec'] == 'mp3'
                    and float(info.get('duration') or 0) > 0)
    except (ValueError, TypeError):
        return False


def finalize_to_mp3(path):
    """Create a verified MP3 from an audio-bearing download."""
    info = probe_media_file(path)
    if not info or info['audio_codec'] == 'unknown':
        return None
    if compatible_mp3(info):
        return path
    target = path.with_name(f"{path.stem}.finalize_tmp.mp3")
    try:
        result = subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-i', str(path), '-map', '0:a:0',
            '-vn', '-c:a', 'libmp3lame', '-q:a', '2', str(target),
        ], capture_output=True, timeout=processing_timeout(info), check=False)
    except (OSError, subprocess.TimeoutExpired):
        target.unlink(missing_ok=True)
        return None
    if result.returncode or not compatible_mp3(probe_media_file(target)):
        target.unlink(missing_ok=True)
        return None
    return target


def complete_recording(job):
    # Interrupted yt-dlp files can still contain usable media, including .part.
    # Prefer an already merged A/V file; never publish a video-only fragment.
    candidate_stats = []
    for path in DOWNLOAD_DIR.glob(f"{job['job_id']}-*"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if (path.is_file() and stat.st_size > 0
                and not path.name.endswith('.ytdl')
                and '.finalize_tmp.' not in path.name):
            candidate_stats.append((path, stat.st_mtime))
    candidates = [item[0] for item in sorted(
        candidate_stats,
        key=lambda item: (item[0].suffix != '.part', item[1]), reverse=True,
    )]
    output_format = quality.validate_format(job.get('output_format', 'mp4'))
    if output_format == 'mp3':
        for path in candidates:
            final_path = finalize_to_mp3(path)
            if final_path is None:
                continue
            target = generate_final_filename(job.get('title', ''),
                                             note=job.get('note', ''),
                                             extension='mp3')
            final_path.rename(target)
            log.info('job=%s source=%s finalization=passed final=%s',
                     job['job_id'], job['source'], target)
            return target
        raise RuntimeError('No usable audio file; temporary media retained')
    separate_video = separate_audio = None
    separate_video_info = separate_audio_info = None
    allow_silent = (job['source'] in {'tiktok', 'instagram'} and job.get('silent_video')
                    and job.get('stop_reason') == 'completed')
    for path in candidates:
        info = probe_media_file(path)
        if not info:
            continue
        if info['video_codec'] != 'unknown' and info['audio_codec'] == 'unknown':
            # Only accept silence explicitly confirmed by source metadata, not
            # a missing audio fragment from an interrupted A/V download.
            if allow_silent:
                if usable_capture(info, allow_silent=True):
                    return finish_captured_media(job, path, info, allow_silent=True)
            if separate_video is None:
                separate_video, separate_video_info = path, info
            continue
        if info['audio_codec'] != 'unknown' and info['video_codec'] == 'unknown':
            if separate_audio is None:
                separate_audio, separate_audio_info = path, info
            continue
        if info['video_codec'] == 'unknown' or info['audio_codec'] == 'unknown':
            continue
        if not usable_capture(info):
            continue
        # One global worker reservation protects the existing date sequence.
        # A rename error must propagate: no ready message without final naming.
        return finish_captured_media(job, path, info)
    if separate_video and separate_audio:
        if not durations_aligned(separate_video_info, separate_audio_info):
            video_duration = duration_seconds(separate_video_info, 'video')
            audio_duration = duration_seconds(separate_audio_info, 'audio')
            log.error(
                'job=%s media_validation=incomplete_tracks video_seconds=%.2f audio_seconds=%.2f',
                job['job_id'], video_duration or 0, audio_duration or 0,
            )
            raise MediaValidationError(
                'incomplete_tracks',
                'Download belum lengkap: durasi video '
                f'{video_duration or 0:.0f} detik, sedangkan audio '
                f'{audio_duration or 0:.0f} detik. File parsial tetap disimpan. '
                + diagnostic_detail(job),
            )
        # yt-dlp may be interrupted before its own separate-track merger runs.
        # Keep both originals until a merged, compatible clip has been verified.
        merged = DOWNLOAD_DIR / f"{job['job_id']}-recovered.mkv"
        try:
            result = subprocess.run([
                'ffmpeg', '-v', 'error', '-y', '-i', str(separate_video),
                '-i', str(separate_audio), '-map', '0:v:0', '-map', '1:a:0',
                '-c', 'copy', '-shortest', str(merged),
            ], capture_output=True, timeout=processing_timeout(separate_video_info), check=False)
            if result.returncode == 0:
                info = probe_media_file(merged)
                if usable_capture(info):
                    return finish_captured_media(job, merged, info)
        except (OSError, subprocess.TimeoutExpired):
            log.warning('job=%s track recovery failed', job['job_id'])
    raise MediaValidationError(
        'incomplete_media',
        'Proses capture belum menghasilkan video dan audio yang lengkap. '
        'File parsial tetap disimpan. ' + diagnostic_detail(job),
    )


def usable_capture(info, allow_silent=False):
    return bool(info and info.get('video_codec', 'unknown') != 'unknown'
                and (allow_silent or info.get('audio_codec', 'unknown') != 'unknown')
                and duration_seconds(info) and durations_aligned(info))


def preserve_original(job, path, info):
    extension = next((ext for name, ext in [('mp4', 'mp4'), ('mpegts', 'ts'), ('matroska', 'mkv'), ('webm', 'webm')]
                      if name in info.get('container', '')), 'mkv')
    target = generate_final_filename(job.get('title', ''), note=job.get('note', '')).with_suffix('.' + extension)
    size = path.stat().st_size
    job.update(filename=target.name, original_filename=target.name, size=size,
               original_size=size, requested_compression=job.get('compression', 'original'),
               processing_status='processing', progress_phase='processing', progress_percent=None, eta_seconds=None)
    # Write intent before rename: a restart can find either the job-prefixed
    # capture or the named Original, including the gap before Redis publication.
    if os.getenv('DATA_DIR'):
        storage.save_recording(job, 'finalizing', 'Memvalidasi Original sebelum pemrosesan.')
    path.rename(target)
    processing_checkpoint(job)
    return target


def processing_failure(job, reason):
    job.update(processing_status=reason, processing_error=reason, compression='original',
               filename=job['original_filename'], size=job['original_size'], progress_percent=None, eta_seconds=None)
    labels = {'cancelled': 'kompresi dibatalkan', 'interrupted': 'kompresi terputus',
              'disk_space': 'kompresi gagal · ruang disk tidak cukup', 'timeout': 'kompresi gagal · batas waktu terlewati'}
    job['processing_detail'] = 'Rekaman berhasil · ' + labels.get(reason, 'kompresi gagal') + ' · Original tersedia.'
    log.warning('job=%s processing=%s original_preserved=%s', job['job_id'], reason, job['original_filename'])


def finish_captured_media(job, path, info, allow_silent=False):
    original = preserve_original(job, path, info)
    try:
        final = finalize_to_compatible_mp4(original, job.get('compression', 'original'), allow_silent=allow_silent, job=job)
        if final is None:
            raise ProcessingError('validation_failed')
        job['processing_status'] = 'completed'
        return final
    except Exception as exc:
        try:
            original.with_name(original.stem + '.finalize_tmp.mp4').unlink(missing_ok=True)
        except OSError:
            log.warning('job=%s abandoned processing temp could not be removed', job['job_id'])
        processing_failure(job, exc.code if isinstance(exc, ProcessingError) else 'processing_failed')
        return original


def recover_processing():
    """Single worker startup: settle abandoned processing to verified Original."""
    if not os.getenv('DATA_DIR'):
        return
    for row in storage.recordings():
        if row.get('state') != 'finalizing':
            continue
        job = dict(row)
        name = row.get('original_filename')
        path = DOWNLOAD_DIR / name if name and Path(name).name == name else None
        valid = path and not path.is_symlink() and path.is_file() and usable_capture(probe_media_file(path), row.get('silent_video', False))
        if not valid:
            # Old jobs may predate the Original checkpoint.
            for candidate in DOWNLOAD_DIR.glob(row['job_id'] + '-*'):
                if '.finalize_tmp.' in candidate.name or candidate.is_symlink() or not candidate.is_file():
                    continue
                info = probe_media_file(candidate)
                if usable_capture(info, row.get('silent_video', False)):
                    path = preserve_original(job, candidate, info)
                    valid = True
                    break
        if valid:
            job.setdefault('original_size', path.stat().st_size)
            processing_failure(job, 'interrupted')
            storage.save_recording(job, 'ready', job['processing_detail'])
            for temp in [path.with_name(path.stem + '.finalize_tmp.mp4'), *DOWNLOAD_DIR.glob(row['job_id'] + '-*.finalize_tmp.*')]:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    log.warning('job=%s abandoned processing temp could not be removed', job['job_id'])
            state, detail = 'ready', job['processing_detail']
        else:
            state, detail = 'failed', 'Capture terputus tanpa video dan audio lengkap; file parsial tetap disimpan.'
            storage.save_recording(job, state, detail)
        r.set('state:' + job['job_id'], state, ex=86400)
        r.set('web:job:' + job['job_id'], json.dumps(storage.recording(job['job_id'])), ex=86400)
        for key in ['capture:owner', *r.scan_iter('active:*')]:
            r.eval("if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0", 1, key, job['job_id'])
        r.delete('stop:' + job['job_id'])


async def run_download(job):
    job.setdefault('source', 'youtube')
    job.setdefault('is_live', job['source'] in {'oryx', 'tiktok'})
    job['quality'] = quality.validate(job.get('quality', 'best'))
    job['output_format'] = quality.validate_format(job.get('output_format', 'mp4'))
    job['compression'] = quality.validate_preset(job.get('compression', 'original'))
    if job['output_format'] == 'mp3':
        if job['source'] != 'youtube':
            raise ValueError('MP3 is only supported for YouTube downloads')
        job['quality'] = 'best'
        job['compression'] = 'original'
    job.setdefault('job_id', uuid.uuid4().hex)
    job.setdefault('requested_at', time.time())
    job.setdefault('origin', 'telegram')
    job.setdefault('attempt_root_id', job['job_id'])
    job.setdefault('attempt_number', 1)
    job.setdefault('attempt_total', job['attempt_number'])
    if os.getenv('DATA_DIR') and job['source'] in {'youtube', 'tiktok', 'instagram'}:
        try:
            await asyncio.to_thread(storage.save_capture_request, job)
        except Exception:
            log.error('job=%s retry_snapshot=failed', job['job_id'])
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
        if job['source'] not in {'youtube', 'oryx', 'tiktok', 'instagram'}:
            raise ValueError('Unsupported source type')
        if job['source'] == 'tiktok':
            job['url'] = storage.validate_tiktok_url(job['url'])
            job['is_live'] = storage.tiktok_is_live(job['url'])
        elif job['source'] == 'instagram':
            job['url'] = storage.validate_instagram_url(job['url'])
            job['is_live'] = False
        if job['source'] in {'oryx', 'tiktok'} and job['is_live'] and time.time() - job.get('requested_at', 0) > 10:
            raise RuntimeError('Record request expired; please send /record again')
        if r.exists(f"stop:{job['job_id']}"):
            raise RuntimeError('Stopped before capture started')
        if job['source'] in {'youtube', 'tiktok', 'instagram'}:
            tiktok_live = job['source'] == 'tiktok' and storage.tiktok_is_live(job['url'])
            stage = 'inspection'
            info = await inspect_youtube(job)
            if job['source'] in {'tiktok', 'instagram'} and not tiktok_live:
                try:
                    info, job['playlist_item'] = storage.social_video(info, job['source'])
                except ValueError as exc:
                    raise SourceInspectionError('unsupported_media', str(exc)) from exc
                if info.get('is_live') is True:
                    raise SourceInspectionError('unsupported_live', 'URL ini bukan video biasa. Instagram Live tidak didukung.')
                formats = info.get('formats') or [info]
                job['silent_video'] = bool(formats) and all(fmt.get('acodec') == 'none' for fmt in formats)
            update_youtube_metadata(job, info)
            if tiktok_live and info.get('is_live') is not True:
                await set_state(job, status, 'failed', 'Akun TikTok belum live atau siaran tidak dapat diakses. Coba lagi saat akun sedang live.')
                return
            if job['source'] == 'youtube' and job.get('live_status') == 'post_live':
                await set_state(
                    job, status, 'starting',
                    'YouTube masih memproses arsip livestream; download akan '
                    'dilanjutkan otomatis jika manifest berubah.',
                )
        stage = 'capture'
        reason = await capture_with_retries(job, status, lease)
        job['stop_reason'] = reason
        # A capture stop has already been fulfilled; a subsequent Stop cancels processing only.
        r.delete('stop:' + job['job_id'])
        if job.get('started_at'):
            job['elapsed'] = round(time.time() - job['started_at'], 1)
        job.update(progress_percent=None, progress_phase='processing', eta_seconds=None)
        await set_state(job, status, 'finalizing',
                        'Menggabungkan track dan memvalidasi durasi video/audio…')
        stage = 'finalization'
        final_path = await asyncio.to_thread(complete_recording, job)
        job.update(filename=final_path.name, size=final_path.stat().st_size)
        detail = f"📁 {final_path.name}\n💾 {format_size(final_path.stat().st_size)}"
        if job.get('processing_detail'):
            detail = job['processing_detail'] + '\n' + detail
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
        error_code = exc.code if isinstance(
            exc, (SourceInspectionError, MediaValidationError)) else type(exc).__name__
        error_detail = exc.detail if isinstance(
            exc, (SourceInspectionError, MediaValidationError)) else safe_diagnostic(str(exc))
        log.error('job=%s source=%s stage=%s error_type=%s reason=%s detail=%s',
                  job['job_id'], job['source'], stage, type(exc).__name__,
                  error_code, error_detail)
        failure_code, title, message, technical = classify_failure(job, exc)
        job.update(error_code=failure_code, error_title=title, error_message=message)
        await set_state(job, status, 'failed', technical)
    finally:
        lease.cancel()
        await asyncio.gather(lease, return_exceptions=True)
        clear_job_state(job['job_id'], job['chat_id'])


async def main():
    log.info('Worker running')
    await asyncio.to_thread(recover_processing)
    archival = asyncio.create_task(archive_worker())
    async def watch_loop():
        controls = telegram_controls.Controls(r)
        interval = max(10, int(os.getenv('TELEGRAM_WATCH_POLL_SECONDS', '30')))
        while True:
            try:
                await controls.tick(None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('Shared watch tick failed', exc_info=True)
            await asyncio.sleep(interval)
    watching = asyncio.create_task(watch_loop())
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
        watching.cancel()
        await asyncio.gather(archival, watching, return_exceptions=True)


if __name__ == '__main__':
    asyncio.run(main())
