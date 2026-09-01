import os
import json
import asyncio
import subprocess
from pathlib import Path
import signal
import re
import uuid
from datetime import datetime

# pyrefly: ignore [missing-import]
import redis
# pyrefly: ignore [missing-import]
from telegram import Bot


DOWNLOAD_DIR = Path("/downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

r = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=30,
    health_check_interval=30,
)


def clear_job_state(job_id, chat_id):
    if job_id:
        r.delete(f"stop:{job_id}")
    if chat_id is not None:
        r.delete(f"active:{chat_id}")

bot = Bot(token=TOKEN)


async def send(chat_id, text):

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
    # Limit length to prevent filesystem issues (leaving room for date prefix and extension)
    # DDMMYY- is 7 chars, .mp4 is 4 chars, so 255 - 7 - 4 = 244
    if len(safe_title) > 200:
        safe_title = safe_title[:200].rstrip()
    return safe_title


def generate_final_filename(title):
    """
    Generate the final filename in DDMMYY-{title}.mp4 format.
    Handles collision avoidance by adding a numeric suffix if the file already exists.
    Uses the server's local timezone.
    """
    # Get current date in server's local timezone
    now = datetime.now()
    date_str = now.strftime("%d%m%y")
    
    # Sanitize the title
    safe_title = sanitize_filename(title)
    
    # Generate base filename
    base_filename = f"{date_str}-{safe_title}.mp4"
    final_path = DOWNLOAD_DIR / base_filename
    
    # If file doesn't exist, return it as-is
    if not final_path.exists():
        return final_path
    
    # Handle collision: add numeric suffix
    counter = 1
    while counter <= 999:  # Limit attempts to prevent infinite loops
        alt_filename = f"{date_str}-{safe_title}_{counter:03d}.mp4"
        alt_path = DOWNLOAD_DIR / alt_filename
        if not alt_path.exists():
            return alt_path
        counter += 1
    
    # Fallback (should rarely happen): use job_id as last resort
    fallback_filename = f"{date_str}-{safe_title}_{uuid.uuid4().hex[:8]}.mp4"
    return DOWNLOAD_DIR / fallback_filename


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
                "format=format_name:stream=codec_type:stream=codec_name:stream=pix_fmt",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("ffprobe not found in PATH.")
        return None

    if result.returncode != 0:
        print(f"ffprobe failed for {path}: {result.stderr.strip()}")
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
    * Always add ``-movflags +faststart``.
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
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("ffmpeg not found in PATH.")
        return None

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown ffmpeg error"
        print(f"ffmpeg finalize failed: {stderr}")
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

    if (
        "mp4" not in tmp_container
        or tmp_video not in _COMPATIBLE_VIDEO_CODECS
        or tmp_audio != "aac"
        or tmp_pix != "yuv420p"
    ):
        print("Finalized temp file does not meet compatibility requirements – aborting.")
        temp_path.unlink(missing_ok=True)
        return None

    # Atomically swap -------------------------------------------------------
    path.unlink(missing_ok=True)
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

    parts = ["🔴 Recording LIVE", "", f"🎬 {title}"]

    if progress["size"]:
        parts.append(f"⏺ {progress['size']} recorded")

    if progress["speed"]:
        parts.append(f"⚡ {progress['speed']}")

    return "\n\n".join(parts)


async def read_output(process, output):

    while True:
        line = await process.stdout.readline()

        if not line:
            await output.put(None)
            return

        await output.put(line.decode(errors="ignore").strip())


async def watch_stop(job_id, process):

    stop_key = f"stop:{job_id}"

    while process.returncode is None:
        if await asyncio.to_thread(r.exists, stop_key):
            return True

        await asyncio.sleep(0.25)

    return False


async def stop_process(process, job_id, chat_id):
    if process.returncode is not None:
        clear_job_state(job_id, chat_id)
        return

    try:
        process.send_signal(signal.SIGINT)
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    finally:
        clear_job_state(job_id, chat_id)


async def run_download(job):

    chat_id = job["chat_id"]
    url = job["url"]
    job_id = job.get("job_id") or uuid.uuid4().hex
    stop_key = f"stop:{job_id}"
    active_key = f"active:{chat_id}"

    r.delete(stop_key)
    r.set(active_key, job_id, ex=86400)

    status = await send(
        chat_id,
        "📥 Starting download..."
    )

    if not status:
        r.delete(active_key)
        return

    # ---------------------------------------------
    # Get information first
    # ---------------------------------------------

    inspect = [
        "yt-dlp",
        "--dump-single-json",
        "--skip-download",
        url,
    ]

    inspect_process = await asyncio.create_subprocess_exec(
        *inspect,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await inspect_process.communicate()

    if inspect_process.returncode != 0:

        await edit(
            chat_id,
            status.message_id,
            "❌ Could not access YouTube video."
        )

        r.delete(active_key)
        return

    try:

        info = json.loads(
            stdout.decode()
        )

    except Exception:

        await edit(
            chat_id,
            status.message_id,
            "❌ Could not read YouTube metadata."
        )

        r.delete(active_key)
        return

    title = info.get(
        "title",
        "Unknown",
    )

    is_live = info.get(
        "is_live",
        False,
    )

    if is_live:

        await edit(
            chat_id,
            status.message_id,
            f"🔴 LIVE detected\n\n"
            f"🎬 {title}\n\n"
            f"⏺ Recording..."
        )

    else:

        await edit(
            chat_id,
            status.message_id,
            f"📥 Downloading\n\n"
            f"🎬 {title}"
        )

    # ---------------------------------------------
    # Download
    # ---------------------------------------------

    command = [

        "yt-dlp",

        "-f",
        "bestvideo*+bestaudio/best",

        "--merge-output-format",
        "mp4",

        "--remux-video",
        "mp4",

        "--no-playlist",

        "--newline",

        "--retries",
        "10",

        "--fragment-retries",
        "10",

        "--continue",

        "-o",
        f"/downloads/{job_id}-%(title).200B.%(ext)s",

        url,
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    output = asyncio.Queue()
    output_task = asyncio.create_task(read_output(process, output))
    stop_task = asyncio.create_task(watch_stop(job_id, process))
    last_update = 0
    last_percent = -1
    progress = {
        "percent": None,
        "size": None,
        "speed": None,
        "eta": None,
    }

    try:
        while True:
            if stop_task.done() and stop_task.result():
                await edit(
                    chat_id,
                    status.message_id,
                    "🛑 Stopping...\n\n"
                    "🔧 Finalizing recording..."
                )

                await stop_process(process, job_id, chat_id)
                await edit(
                    chat_id,
                    status.message_id,
                    "✅ Recording stopped.\n\n"
                    "File finalized."
                )
                return

            if output_task.done() and output.empty():
                break

            try:
                text = await asyncio.wait_for(output.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue

            if text is None:
                break

            print(text)
            parsed = parse_progress(text)

            if not parsed:
                continue

            progress.update(parsed)
            now = asyncio.get_event_loop().time()

            if is_live:
                if now - last_update < 5:
                    continue
                message = live_progress(title, progress)
            else:
                percent = progress["percent"]

                if percent is None:
                    continue

                if percent < last_percent + 5 and now - last_update < 5:
                    continue

                last_percent = percent
                message = (
                    f"📥 Downloading\n\n"
                    f"🎬 {title}\n\n"
                    f"{progress_bar(percent)} {percent:.1f}%"
                )

                if progress["size"]:
                    message += f"\n\n💾 {progress['size']}"
                if progress["speed"]:
                    message += f"\n⚡ {progress['speed']}"
                if progress["eta"]:
                    message += f"\nETA {progress['eta']}"

            last_update = now
            await edit(chat_id, status.message_id, message)
    finally:
        if not output_task.done():
            output_task.cancel()
        if not stop_task.done():
            stop_task.cancel()
        await asyncio.gather(output_task, stop_task, return_exceptions=True)

    return_code = await process.wait()

    if return_code != 0:

        await edit(
            chat_id,
            status.message_id,
            "❌ Download failed."
        )

        clear_job_state(job_id, chat_id)
        return

    await edit(
        chat_id,
        status.message_id,
        "🔧 Download finished.\n\n"
        "Finalizing..."
    )

    await asyncio.sleep(1)

    files = [
        path
        for path in DOWNLOAD_DIR.glob(f"{job_id}-*")
        if path.is_file()
        and not path.name.lower().endswith((".part", ".ytdl"))
        and ".part" not in path.name.lower()
    ]

    if not files:

        await edit(
            chat_id,
            status.message_id,
            "❌ No output file found."
        )

        r.delete(active_key)
        return

    latest = max(
        files,
        key=lambda p: p.stat().st_mtime,
    )
    final_path = finalize_to_compatible_mp4(latest)

    if final_path is None:
        await edit(
            chat_id,
            status.message_id,
            "❌ Finalization failed – could not produce a compatible MP4."
        )
        r.delete(active_key)
        return

    # --- Strict final validation -------------------------------------------
    final_info = probe_media_file(final_path)
    if not final_info:
        await edit(
            chat_id,
            status.message_id,
            "❌ Could not validate final media file."
        )
        r.delete(active_key)
        return

    final_container = (final_info["container"] or "unknown").lower()
    final_video_codec = (final_info["video_codec"] or "unknown").lower()
    final_audio_codec = (final_info["audio_codec"] or "unknown").lower()
    final_pix_fmt = (final_info["pix_fmt"] or "unknown").lower()

    print(f"Final  – container: {final_container}")
    print(f"Final  – video codec: {final_video_codec}")
    print(f"Final  – audio codec: {final_audio_codec}")
    print(f"Final  – pix_fmt: {final_pix_fmt}")
    print(f"Final  – file: {final_path}")

    if (
        "mp4" not in final_container
        or final_video_codec not in _COMPATIBLE_VIDEO_CODECS
        or final_audio_codec != "aac"
        or final_pix_fmt != "yuv420p"
    ):
        await edit(
            chat_id,
            status.message_id,
            "❌ Final file does not meet compatibility requirements "
            f"(container={final_container}, video={final_video_codec}, "
            f"audio={final_audio_codec}, pix_fmt={final_pix_fmt})."
        )
        r.delete(active_key)
        return

    # Rename file to DDMMYY-{title}.mp4 format
    new_path = generate_final_filename(title)
    try:
        final_path.rename(new_path)
        final_path = new_path
        print(f"Renamed to final format: {final_path.name}")
    except Exception as e:
        print(f"Warning: Could not rename file to final format: {e}")
        # Continue with current name if rename fails

    await edit(
        chat_id,
        status.message_id,
        f"✅ Download complete!\n\n"
        f"📁 {final_path.name}\n"
        f"💾 {format_size(final_path.stat().st_size)}\n"
        f"🎞️ h264 / aac / yuv420p"
    )
    clear_job_state(job_id, chat_id)


async def main():

    print("Worker running...")

    while True:

        try:
            item = r.blpop(
                "download_queue",
                timeout=5,
            )
        except redis.exceptions.ConnectionError as exc:
            print("Redis connection error:", exc)
            await asyncio.sleep(5)
            continue

        if not item:
            continue

        _, payload = item

        job = json.loads(payload)

        print(
            "Downloading:",
            job["url"],
        )

        try:

            await run_download(job)

        except Exception as e:

            print(
                "Job error:",
                e,
            )
            if job.get("job_id"):
                clear_job_state(job["job_id"], job.get("chat_id"))
        finally:
            job_id = job.get("job_id")
            active_key = f"active:{job['chat_id']}"

            if job_id is None or r.get(active_key) == job_id:
                r.delete(active_key)


if __name__ == "__main__":

    asyncio.run(main())