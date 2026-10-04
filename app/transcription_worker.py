"""Queued transcription with Cloudflare Workers AI and local Whisper fallback."""
import base64
import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
CLOUDFLARE_MODEL = "@cf/openai/whisper-large-v3-turbo"
CLOUDFLARE_NEURONS_PER_MINUTE = 46.63

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


class TranscriptionCancelled(RuntimeError):
    """Cooperative cancellation while preparing or transcribing chunks."""


class CloudflareError(TranscriptionError):
    """Non-retryable Cloudflare response or configuration error."""


class CloudflareRetryableError(RuntimeError):
    """Cloudflare failure for which auto mode should use local Whisper."""


@dataclass
class TranscriptWord:
    start: float
    end: float
    word: str
    probability: float | None = None


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)


@dataclass
class TranscriptInfo:
    language: str | None = None
    language_probability: float | None = None


@dataclass(frozen=True)
class AudioChunk:
    index: int
    path: Path
    offset: float
    duration: float
    core_start: float
    core_end: float


@dataclass
class ChunkResult:
    chunk: AudioChunk
    segments: list[TranscriptSegment]
    info: TranscriptInfo
    neurons: float
    elapsed: float


def _env_int(name, default, minimum=1):
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name, default, minimum=0.0):
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def configured_provider():
    provider = os.getenv("TRANSCRIPTION_PROVIDER", "local").strip().lower()
    if provider not in {"auto", "cloudflare", "local"}:
        raise TranscriptionError(
            "TRANSCRIPTION_PROVIDER harus bernilai auto, cloudflare, atau local.")
    return provider


def configured_model(provider=None):
    return CLOUDFLARE_MODEL if (provider or configured_provider()) in {
        "auto", "cloudflare"} else MODEL_NAME


def cloudflare_credentials():
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    if not account_id or not api_token:
        raise CloudflareError(
            "Konfigurasi Cloudflare belum lengkap: CLOUDFLARE_ACCOUNT_ID dan "
            "CLOUDFLARE_API_TOKEN wajib diisi.")
    return account_id, api_token


def subtitle_timestamp(seconds):
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def vtt_timestamp(seconds):
    return subtitle_timestamp(seconds).replace(",", ".")


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


def render_vtt(segments):
    cues = []
    for segment in segments:
        text = str(segment.text).strip()
        if not text:
            continue
        start = max(0.0, float(segment.start))
        end = max(start, float(segment.end))
        cues.append(f"{vtt_timestamp(start)} --> {vtt_timestamp(end)}\n{text}\n")
    return "WEBVTT\n\n" + "\n".join(cues)


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


def audio_duration(path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=False, timeout=PROBE_TIMEOUT,
        )
        duration = float(result.stdout.strip()) if not result.returncode else 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise TranscriptionError("Durasi audio tidak dapat dibaca.") from exc
    if duration <= 0:
        raise TranscriptionError("Durasi audio tidak valid.")
    return duration


def _run_ffmpeg(command, cancel_event=None):
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelled("Transkripsi dibatalkan.")
    timeout = _env_float("TRANSCRIPTION_FFMPEG_TIMEOUT", 7200, 1)
    try:
        result = subprocess.run(
            command, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError("Ekstraksi audio kehabisan waktu.") from exc
    except OSError as exc:
        raise TranscriptionError("FFmpeg tidak tersedia untuk menyiapkan audio.") from exc
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelled("Transkripsi dibatalkan.")
    if result.returncode:
        raise TranscriptionError("Audio tidak dapat disiapkan untuk transkripsi.")


def prepare_audio_chunks(media_path, target_dir, cancel_event=None):
    """Extract once, then make overlapping FLAC chunks with ownership windows."""
    normalized = Path(target_dir) / "audio.flac"
    _run_ffmpeg([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(media_path),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "flac", "-y", str(normalized),
    ], cancel_event)
    duration = audio_duration(normalized)
    chunk_seconds = _env_float("CLOUDFLARE_TRANSCRIPTION_CHUNK_SECONDS", 300, 30)
    overlap = _env_float("CLOUDFLARE_TRANSCRIPTION_CHUNK_OVERLAP_SECONDS", 2, 0)
    overlap = min(overlap, chunk_seconds / 4)
    chunks = []
    core_start = 0.0
    index = 0
    while core_start < duration:
        core_end = min(duration, core_start + chunk_seconds)
        start = max(0.0, core_start - overlap)
        end = min(duration, core_end + overlap)
        chunk_path = Path(target_dir) / f"chunk-{index:05d}.flac"
        _run_ffmpeg([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}",
            "-i", str(normalized), "-t", f"{end - start:.3f}", "-map", "0:a:0",
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "flac", "-y",
            str(chunk_path),
        ], cancel_event)
        chunks.append(AudioChunk(index, chunk_path, start, end - start,
                                 core_start, core_end))
        index += 1
        core_start = core_end
    return chunks


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


class LocalModelCache:
    def __init__(self):
        self.model = None

    def get(self):
        if self.model is None:
            self.model = load_model()
        return self.model


def _local_model(model):
    if hasattr(model, "get"):
        return model.get()
    return model if model is not None else load_model()


def normalize_local_result(result):
    segments, info = result
    normalized = []
    for segment in segments:
        words = []
        for word in (getattr(segment, "words", None) or []):
            words.append(TranscriptWord(
                float(word.start), float(word.end), str(word.word),
                getattr(word, "probability", None)))
        normalized.append(TranscriptSegment(
            float(segment.start), float(segment.end), str(segment.text), words))
    return normalized, TranscriptInfo(
        getattr(info, "language", None),
        getattr(info, "language_probability", None))


def transcribe_local(media_path, model):
    started = time.monotonic()
    log.info("transcription provider=local model=%s", MODEL_NAME)
    try:
        local_model = _local_model(model)
    except Exception as exc:
        raise TranscriptionError(
            "Model transkripsi gagal dimuat. Periksa log worker dan coba lagi.") from exc
    result = local_model.transcribe(
        str(media_path), language=None, beam_size=5, vad_filter=True,
    )
    normalized = normalize_local_result(result)
    log.info("transcription provider=local processing_seconds=%.3f",
             time.monotonic() - started)
    return normalized


def _number(value, field_name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CloudflareError(
            f"Respons Cloudflare tidak valid: {field_name} bukan angka.") from exc
    if number < 0:
        raise CloudflareError(
            f"Respons Cloudflare tidak valid: {field_name} bernilai negatif.")
    return number


def _vtt_seconds(value):
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        pass
    raise CloudflareError("Respons VTT Cloudflare tidak valid.")


def parse_vtt(vtt):
    segments = []
    lines = str(vtt or "").replace("\r", "").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        start_text, end_text = [part.strip().split()[0] for part in line.split("-->", 1)]
        index += 1
        text = []
        while index < len(lines) and lines[index].strip():
            text.append(lines[index].strip())
            index += 1
        segments.append(TranscriptSegment(
            _vtt_seconds(start_text), _vtt_seconds(end_text), " ".join(text)))
    return segments


def _words_from(raw_words):
    words = []
    if raw_words is None:
        return words
    if not isinstance(raw_words, list):
        raise CloudflareError("Respons Cloudflare tidak valid: words bukan daftar.")
    for raw in raw_words:
        if not isinstance(raw, dict):
            raise CloudflareError("Respons Cloudflare tidak valid: entri word rusak.")
        text = raw.get("word", raw.get("text", ""))
        start = _number(raw.get("start"), "word.start")
        end = max(start, _number(raw.get("end"), "word.end"))
        probability = raw.get("probability")
        if probability is not None:
            try:
                probability = float(probability)
            except (TypeError, ValueError):
                probability = None
        words.append(TranscriptWord(start, end, str(text), probability))
    return words


def normalize_cloudflare_response(payload, chunk):
    if not isinstance(payload, dict):
        raise CloudflareError("Respons Cloudflare tidak valid.")
    if payload.get("success") is False:
        raise CloudflareError("Cloudflare menolak permintaan transkripsi.")
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise CloudflareError("Respons transkripsi Cloudflare tidak valid.")
    info_raw = result.get("transcription_info") or {}
    if not isinstance(info_raw, dict):
        raise CloudflareError("Metadata transkripsi Cloudflare tidak valid.")
    language = info_raw.get("language", result.get("language"))
    probability = info_raw.get(
        "language_probability", result.get("language_probability"))
    if probability is not None:
        try:
            probability = float(probability)
        except (TypeError, ValueError):
            probability = None

    raw_segments = result.get("segments")
    if raw_segments is None:
        segments = parse_vtt(result.get("vtt"))
    else:
        if not isinstance(raw_segments, list):
            raise CloudflareError("Respons Cloudflare tidak valid: segments bukan daftar.")
        segments = []
        for raw in raw_segments:
            if not isinstance(raw, dict):
                raise CloudflareError("Respons Cloudflare tidak valid: segmen rusak.")
            start = _number(raw.get("start"), "segment.start")
            end = max(start, _number(raw.get("end"), "segment.end"))
            segments.append(TranscriptSegment(
                start, end, str(raw.get("text", "")),
                _words_from(raw.get("words"))))

    text = result.get("text")
    if text is None and not segments:
        raise CloudflareError("Respons Cloudflare tidak berisi hasil transkripsi.")
    if not segments and str(text or "").strip():
        segments = [TranscriptSegment(0, chunk.duration, str(text))]

    # Some response versions expose words only at result level. Attach them to
    # the segment that contains their midpoint so downstream normalization is stable.
    top_words = _words_from(result.get("words"))
    if top_words and not any(segment.words for segment in segments):
        for word in top_words:
            midpoint = (word.start + word.end) / 2
            owner = next((segment for segment in segments
                          if segment.start <= midpoint <= segment.end), None)
            if owner:
                owner.words.append(word)
    usage = result.get("usage") or payload.get("usage") or {}
    neurons = None
    if isinstance(usage, dict):
        neurons = usage.get("neurons", usage.get("total_neurons"))
    try:
        neurons = float(neurons) if neurons is not None else None
    except (TypeError, ValueError):
        neurons = None
    if neurons is None:
        neurons = chunk.duration * CLOUDFLARE_NEURONS_PER_MINUTE / 60
    return segments, TranscriptInfo(
        str(language) if language else None, probability), neurons


def cloudflare_request(chunk, cancel_event=None):
    account_id, api_token = cloudflare_credentials()
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelled("Transkripsi dibatalkan.")
    try:
        audio = base64.b64encode(chunk.path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise TranscriptionError("Chunk audio tidak dapat dibaca.") from exc
    body = json.dumps({
        "audio": audio,
        "task": "transcribe",
        "vad_filter": True,
        "beam_size": 5,
        "condition_on_previous_text": True,
    }, separators=(",", ":")).encode("utf-8")
    url = ("https://api.cloudflare.com/client/v4/accounts/"
           f"{account_id}/ai/run/{CLOUDFLARE_MODEL}")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    timeout = _env_float("CLOUDFLARE_TRANSCRIPTION_TIMEOUT", 120, 1)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status in {401, 403}:
            raise CloudflareError(
                f"Autentikasi Cloudflare gagal (HTTP {status}). Periksa account ID, "
                "API token, dan izin Workers AI.") from exc
        if status == 429:
            raise CloudflareRetryableError(
                "Cloudflare membatasi permintaan atau kuota Workers AI habis (HTTP 429).") from exc
        if 500 <= status <= 599:
            raise CloudflareRetryableError(
                f"Layanan Cloudflare sedang bermasalah (HTTP {status}).") from exc
        raise CloudflareError(
            f"Permintaan transkripsi Cloudflare ditolak (HTTP {status}).") from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        timed_out = (isinstance(exc, (socket.timeout, TimeoutError))
                     or isinstance(getattr(exc, "reason", None),
                                   (socket.timeout, TimeoutError)))
        reason = "timeout" if timed_out else "network"
        raise CloudflareRetryableError(
            f"Koneksi Cloudflare gagal ({reason}).") from exc
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelled("Transkripsi dibatalkan.")
    try:
        return json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CloudflareError("Respons Cloudflare bukan JSON yang valid.") from exc


def _join_words(words):
    values = [word.word for word in words]
    if any(value[:1].isspace() for value in values):
        return "".join(values).strip()
    return " ".join(value.strip() for value in values if value.strip())


def _offset_and_trim(result):
    """Offset one chunk and emit only its non-overlapping ownership window."""
    output = []
    chunk = result.chunk
    last_chunk = chunk.core_end >= chunk.offset + chunk.duration - 0.001
    for segment in result.segments:
        words = []
        for word in segment.words:
            absolute = TranscriptWord(
                word.start + chunk.offset, word.end + chunk.offset,
                word.word, word.probability)
            midpoint = (absolute.start + absolute.end) / 2
            if (midpoint >= chunk.core_start
                    and (midpoint < chunk.core_end or last_chunk)):
                words.append(absolute)
        absolute_start = segment.start + chunk.offset
        absolute_end = segment.end + chunk.offset
        midpoint = (absolute_start + absolute_end) / 2
        if words:
            text = (segment.text if len(words) == len(segment.words)
                    else _join_words(words))
            output.append(TranscriptSegment(
                words[0].start, words[-1].end, text, words))
        elif (not segment.words and midpoint >= chunk.core_start
              and (midpoint < chunk.core_end or last_chunk)):
            output.append(TranscriptSegment(
                max(chunk.core_start, absolute_start),
                min(chunk.core_end, absolute_end), segment.text, []))
    return output


def merge_chunk_results(results):
    segments = []
    language_weights = {}
    probability_weights = {}
    probability_denominators = {}
    for result in sorted(results, key=lambda item: item.chunk.index):
        segments.extend(_offset_and_trim(result))
        if result.info.language:
            weight = max(0.001, result.chunk.core_end - result.chunk.core_start)
            language_weights[result.info.language] = (
                language_weights.get(result.info.language, 0) + weight)
            if result.info.language_probability is not None:
                probability_weights[result.info.language] = (
                    probability_weights.get(result.info.language, 0)
                    + result.info.language_probability * weight)
                probability_denominators[result.info.language] = (
                    probability_denominators.get(result.info.language, 0) + weight)
    segments.sort(key=lambda item: (item.start, item.end))
    language = max(language_weights, key=language_weights.get) if language_weights else None
    probability = None
    if language and language in probability_weights:
        probability = (probability_weights[language]
                       / probability_denominators[language])
    return segments, TranscriptInfo(language, probability)


def _transcribe_cloudflare_chunk(chunk, total, cancel_event):
    if cancel_event and cancel_event.is_set():
        raise TranscriptionCancelled("Transkripsi dibatalkan.")
    started = time.monotonic()
    log.info("transcription provider=cloudflare chunk=%d/%d state=started",
             chunk.index + 1, total)
    payload = cloudflare_request(chunk, cancel_event)
    segments, info, neurons = normalize_cloudflare_response(payload, chunk)
    elapsed = time.monotonic() - started
    log.info(
        "transcription provider=cloudflare chunk=%d/%d state=completed "
        "processing_seconds=%.3f neurons_consumed=%.3f",
        chunk.index + 1, total, elapsed, neurons)
    return ChunkResult(chunk, segments, info, neurons, elapsed)


def transcribe_cloudflare(media_path, cancel_event=None):
    # Fail fast before spending time extracting audio.
    cloudflare_credentials()
    concurrency = _env_int("CLOUDFLARE_TRANSCRIPTION_CONCURRENCY", 3)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="streamfetch-transcription-") as temporary:
        chunks = prepare_audio_chunks(media_path, temporary, cancel_event)
        log.info("transcription provider=cloudflare model=%s chunks=%d concurrency=%d",
                 CLOUDFLARE_MODEL, len(chunks), concurrency)
        executor = ThreadPoolExecutor(max_workers=concurrency,
                                      thread_name_prefix="cloudflare-asr")
        futures = []
        try:
            futures = [executor.submit(
                _transcribe_cloudflare_chunk, chunk, len(chunks), cancel_event)
                       for chunk in chunks]
            results = [future.result() for future in as_completed(futures)]
        except Exception:
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
    segments, info = merge_chunk_results(results)
    elapsed = time.monotonic() - started
    neurons = sum(result.neurons for result in results)
    log.info(
        "transcription provider=cloudflare state=completed chunks=%d "
        "processing_seconds=%.3f neurons_consumed=%.3f",
        len(results), elapsed, neurons)
    return segments, info


def set_state(job_id, status, **fields):
    storage.save_transcription_state(job_id, status, **fields)
    try:
        r.set(f"transcription:state:{job_id}", status)
    except redis.exceptions.RedisError:
        # SQLite is authoritative; Redis state is only an operational hint.
        log.warning("transcription job=%s redis_state_update=failed", job_id)
    log.info("transcription job=%s state=%s", job_id, status)


def transcribe(job, model=None, cancel_event=None):
    """Process one job. The caller owns queue acknowledgement."""
    job_id = job["job_id"]
    started = time.time()
    published = []
    provider = None
    active_model = MODEL_NAME
    try:
        provider = configured_provider()
        active_model = configured_model(provider)
        set_state(job_id, "transcribing", started_at=started,
                  model=active_model, error="")
        row = storage.recording(job_id)
        if not row or row.get("state") != "ready":
            raise TranscriptionError("Rekaman belum siap untuk ditranskripsi.")
        media_path = safe_media_path(row.get("filename"))
        ensure_audio_stream(media_path)

        if provider in {"auto", "cloudflare"}:
            try:
                segments, info = transcribe_cloudflare(media_path, cancel_event)
            except CloudflareRetryableError as exc:
                if provider != "auto":
                    raise TranscriptionError(str(exc)) from exc
                log.warning(
                    "transcription job=%s provider=cloudflare fallback_provider=local "
                    "fallback_reason=%s",
                    job_id, str(exc))
                active_model = MODEL_NAME
                segments, info = transcribe_local(media_path, model)
        else:
            segments, info = transcribe_local(media_path, model)

        txt, srt = render_outputs(segments)
        vtt = render_vtt(segments)
        if not txt.strip():
            raise TranscriptionError("Tidak ada ucapan yang terdeteksi di audio.")
        outputs = [
            (media_path.with_suffix(".txt"), txt),
            (media_path.with_suffix(".srt"), srt),
            (media_path.with_suffix(".vtt"), vtt),
        ]
        for path, content in outputs:
            atomic_write(path, content, job_id)
            published.append(path)
        elapsed = round(time.time() - started, 1)
        set_state(
            job_id, "completed", completed_at=time.time(),
            processing_seconds=elapsed, language=info.language,
            language_probability=info.language_probability,
            model=active_model, txt_filename=outputs[0][0].name,
            srt_filename=outputs[1][0].name, vtt_filename=outputs[2][0].name,
            error="",
        )
        log.info("transcription job=%s provider=%s total_seconds=%.3f model=%s",
                 job_id, "local" if active_model == MODEL_NAME else "cloudflare",
                 time.time() - started, active_model)
    except TranscriptionCancelled:
        for path in published:
            path.unlink(missing_ok=True)
        log.info("transcription job=%s state=cancelled total_seconds=%.3f",
                 job_id, time.time() - started)
        raise
    except Exception as exc:
        for path in published:
            # A newly written partial result must never appear as completed.
            path.unlink(missing_ok=True)
        message = (str(exc) if isinstance(exc, TranscriptionError)
                   else "Transkripsi gagal diproses. Periksa log worker dan coba lagi.")
        set_state(job_id, "failed", completed_at=time.time(),
                  processing_seconds=round(time.time() - started, 1),
                  model=active_model, error=message)
        log.exception(
            "transcription job=%s provider=%s failed error_type=%s total_seconds=%.3f",
            job_id, provider or "unknown", type(exc).__name__,
            time.time() - started)
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
        storage.save_transcription_state(
            job_id, "queued", model=configured_model())
        r.set(f"transcription:guard:{job_id}", "queued")
        r.set(f"transcription:state:{job_id}", "queued")


def heartbeat(stop):
    while not stop.wait(3):
        try:
            r.set("transcription:heartbeat", "1", ex=15)
        except redis.exceptions.RedisError:
            log.warning("Transcription heartbeat could not reach Redis")


def main():
    try:
        provider = configured_provider()
        startup_model = configured_model(provider)
    except TranscriptionError as exc:
        log.error("Transcription worker configuration failed: %s", exc)
        raise SystemExit(2) from exc
    log.info("Transcription worker running provider=%s model=%s local_fallback=%s",
             provider, startup_model, provider == "auto")
    model = LocalModelCache()
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
