"""Single-consumer CPU transcription worker for completed recordings."""
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import redis

try:
    from . import storage
except ImportError:
    import storage


DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/downloads"))
MODEL_NAME = os.getenv("WHISPER_MODEL", "small")
MODEL_CACHE_DIR = Path(os.getenv("WHISPER_MODEL_DIR", "/data/whisper-models"))
HF_HOME = Path(os.getenv("HF_HOME", "/data/huggingface"))
CPU_THREADS = max(1, int(os.getenv("WHISPER_CPU_THREADS", "4")))
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "20"))
QUEUE = "transcription_queue"
PROCESSING = "transcription_processing"

# huggingface_hub may otherwise derive auxiliary Hub/Xet caches from HOME.
# The service intentionally runs as a non-root host UID, and Docker gives that
# user HOME=/, which is not writable. Set these before faster_whisper imports it.
os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("HF_HUB_CACHE", str(HF_HOME / "hub"))
os.environ.setdefault("HF_XET_CACHE", str(HF_HOME / "xet"))
os.environ.setdefault("XDG_CACHE_HOME", str(HF_HOME / "xdg"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True,
    socket_connect_timeout=5, socket_timeout=5, health_check_interval=30,
)

CLAIM_JOB = """
local payload = redis.call('LPOP', KEYS[1])
if not payload then return nil end
redis.call('RPUSH', KEYS[2], payload)
return payload
"""

ACK_JOB = """
redis.call('LREM', KEYS[1], 1, ARGV[1])
return 1
"""


class TranscriptionError(RuntimeError):
    """Expected media/transcription failure safe to expose in the UI."""


def subtitle_timestamp(seconds):
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def render_outputs(segments):
    """Return readable TXT and standards-compliant SRT content."""
    texts = []
    subtitles = []
    for segment in segments:
        text = str(segment.text).strip()
        if not text:
            continue
        texts.append(text)
        start = max(0.0, float(segment.start))
        end = max(start, float(segment.end))
        subtitles.append(
            f"{len(subtitles) + 1}\n"
            f"{subtitle_timestamp(start)} --> {subtitle_timestamp(end)}\n"
            f"{text}\n"
        )
    return "\n".join(texts) + ("\n" if texts else ""), "\n".join(subtitles)


def safe_media_path(filename):
    if not filename or Path(filename).name != filename:
        raise TranscriptionError("Nama file media tidak valid.")
    root = DOWNLOAD_DIR.resolve()
    path = root / filename
    if path.parent.resolve() != root or path.is_symlink():
        raise TranscriptionError("Nama file media tidak valid.")
    if not path.is_file():
        raise TranscriptionError("File video lokal tidak ditemukan.")
    return path


def ensure_audio_stream(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "json", str(path)],
            capture_output=True, text=True, check=False, timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError("Pemeriksaan audio kehabisan waktu.") from exc
    except OSError as exc:
        raise TranscriptionError("FFprobe tidak tersedia untuk memeriksa audio.") from exc
    if result.returncode:
        raise TranscriptionError("File media rusak atau tidak didukung.")
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as exc:
        raise TranscriptionError("Hasil pemeriksaan audio tidak valid.") from exc
    if not streams:
        raise TranscriptionError("Video tidak memiliki stream audio.")


def atomic_write(path, content, job_id):
    temporary = path.with_name(f".{path.name}.{job_id}.part")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_model():
    # Delayed import keeps web/capture startup independent from the ML runtime.
    from faster_whisper import WhisperModel

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HF_HOME.mkdir(parents=True, exist_ok=True)
    return WhisperModel(
        MODEL_NAME, device="cpu", compute_type="int8",
        download_root=str(MODEL_CACHE_DIR), cpu_threads=CPU_THREADS,
        num_workers=1,
    )


def set_state(job_id, status, **fields):
    storage.save_transcription_state(job_id, status, **fields)
    try:
        r.set(f"transcription:state:{job_id}", status)
    except redis.exceptions.RedisError:
        # SQLite is authoritative; Redis state is only an operational hint.
        log.warning("transcription job=%s redis_state_update=failed", job_id)
    log.info("transcription job=%s state=%s", job_id, status)


def transcribe(job, model):
    """Process one job. The caller owns queue acknowledgement."""
    job_id = job["job_id"]
    started = time.time()
    txt_path = None
    srt_path = None
    published = []
    set_state(job_id, "transcribing", started_at=started, model=MODEL_NAME, error="")
    try:
        row = storage.recording(job_id)
        if not row or row.get("state") != "ready":
            raise TranscriptionError("Rekaman belum siap untuk ditranskripsi.")
        media_path = safe_media_path(row.get("filename"))
        ensure_audio_stream(media_path)
        txt_path = media_path.with_suffix(".txt")
        srt_path = media_path.with_suffix(".srt")
        segments, info = model.transcribe(
            str(media_path), language=None, beam_size=5, vad_filter=True,
        )
        txt, srt = render_outputs(segments)
        if not txt.strip():
            raise TranscriptionError("Tidak ada ucapan yang terdeteksi di audio.")
        atomic_write(txt_path, txt, job_id)
        published.append(txt_path)
        atomic_write(srt_path, srt, job_id)
        published.append(srt_path)
        elapsed = round(time.time() - started, 1)
        set_state(
            job_id, "completed", completed_at=time.time(),
            processing_seconds=elapsed, language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            model=MODEL_NAME, txt_filename=txt_path.name,
            srt_filename=srt_path.name, error="",
        )
    except Exception as exc:
        for path in published:
            if path:
                # A newly written partial result must never appear as completed.
                path.unlink(missing_ok=True)
        message = (str(exc) if isinstance(exc, TranscriptionError)
                   else "Transkripsi gagal diproses. Periksa log worker dan coba lagi.")
        set_state(job_id, "failed", completed_at=time.time(),
                  processing_seconds=round(time.time() - started, 1),
                  model=MODEL_NAME, error=message)
        log.exception("transcription job=%s failed error_type=%s", job_id, type(exc).__name__)
    finally:
        current = storage.recording(job_id)
        if current and (current.get("transcript") or {}).get("status") == "failed":
            try:
                r.delete(f"transcription:guard:{job_id}")
            except redis.exceptions.RedisError:
                log.warning("transcription job=%s redis_guard_cleanup=failed", job_id)


def queued_job_ids():
    values = r.lrange(QUEUE, 0, -1) + r.lrange(PROCESSING, 0, -1)
    found = set()
    for payload in values:
        try:
            found.add(json.loads(payload)["job_id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return found


def recover_jobs():
    """Requeue abandoned processing and durable queued/in-progress records."""
    while True:
        payload = r.lpop(PROCESSING)
        if payload is None:
            break
        try:
            job_id = json.loads(payload)["job_id"]
            row = storage.recording(job_id)
            status = ((row or {}).get("transcript") or {}).get("status")
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if status in {"queued", "transcribing"}:
            r.rpush(QUEUE, payload)
    existing = queued_job_ids()
    for row in storage.recordings():
        transcript = row.get("transcript") or {}
        if transcript.get("status") not in {"queued", "transcribing"}:
            continue
        job_id = row["job_id"]
        if job_id not in existing:
            r.rpush(QUEUE, json.dumps({"job_id": job_id}))
            existing.add(job_id)
        storage.save_transcription_state(job_id, "queued", model=MODEL_NAME)
        r.set(f"transcription:guard:{job_id}", "queued")
        r.set(f"transcription:state:{job_id}", "queued")


def heartbeat(stop):
    while not stop.wait(3):
        try:
            r.set("transcription:heartbeat", "1", ex=15)
        except redis.exceptions.RedisError:
            log.warning("Transcription heartbeat could not reach Redis")


def main():
    log.info("Transcription worker running model=%s device=cpu compute_type=int8", MODEL_NAME)
    model = None
    last_recovery = 0.0
    stop = threading.Event()
    monitor = threading.Thread(target=heartbeat, args=(stop,), daemon=True)
    monitor.start()
    try:
        while True:
            try:
                if time.time() - last_recovery > 30:
                    recover_jobs()
                    last_recovery = time.time()
                payload = r.eval(CLAIM_JOB, 2, QUEUE, PROCESSING)
                if not payload:
                    time.sleep(1)
                    continue
                job = json.loads(payload)
                row = storage.recording(job["job_id"])
                status = ((row or {}).get("transcript") or {}).get("status")
                if status not in {"queued", "transcribing"}:
                    r.eval(ACK_JOB, 1, PROCESSING, payload)
                    continue
                if model is None:
                    try:
                        model = load_model()
                    except Exception as exc:
                        log.exception("Whisper model load failed error_type=%s", type(exc).__name__)
                        set_state(job["job_id"], "failed", completed_at=time.time(),
                                  model=MODEL_NAME,
                                  error="Model transkripsi gagal dimuat. Periksa log worker dan coba lagi.")
                        r.delete(f"transcription:guard:{job['job_id']}")
                        r.eval(ACK_JOB, 1, PROCESSING, payload)
                        continue
                transcribe(job, model)
                r.eval(ACK_JOB, 1, PROCESSING, payload)
            except redis.exceptions.RedisError:
                log.error("Redis unavailable")
                time.sleep(3)
            except Exception as exc:
                log.exception("Transcription worker loop failed error_type=%s", type(exc).__name__)
                # Keep the payload in PROCESSING so recovery can safely retry it.
                time.sleep(3)
    finally:
        stop.set()
        monitor.join(timeout=5)


if __name__ == "__main__":
    main()
