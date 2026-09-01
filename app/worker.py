import os
import json
import asyncio
from pathlib import Path
import signal
import re
import uuid

import redis
from telegram import Bot


DOWNLOAD_DIR = Path("/downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

r = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    decode_responses=True,
)

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


def progress_bar(percent):

    length = 20

    filled = int(
        length * percent / 100
    )

    return (
        "█" * filled
        + "░" * (length - filled)
    )


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


async def run_download(job):

    chat_id = job["chat_id"]
    url = job["url"]
    job_id = job.get("job_id") or uuid.uuid4().hex
    stop_key = f"stop:{job_id}"
    active_key = f"active:{chat_id}"

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

                if process.returncode is None:
                    process.send_signal(signal.SIGINT)

                await process.wait()
                r.delete(stop_key)
                r.delete(active_key)
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

        r.delete(active_key)
        return

    await edit(
        chat_id,
        status.message_id,
        "🔧 Download finished.\n\n"
        "Finalizing..."
    )

    await asyncio.sleep(1)

    files = list(DOWNLOAD_DIR.glob(f"{job_id}-*.mp4"))

    if not files:

        await edit(
            chat_id,
            status.message_id,
            "❌ No MP4 found."
        )

        r.delete(active_key)
        return

    latest = max(
        files,
        key=lambda p: p.stat().st_mtime,
    )

    await edit(
        chat_id,
        status.message_id,
        f"✅ Download complete!\n\n"
        f"📁 {latest.name}\n"
        f"💾 {format_size(latest.stat().st_size)}"
    )
    r.delete(active_key)


async def main():

    print("Worker running...")

    while True:

        item = r.blpop(
            "download_queue",
            timeout=5,
        )

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
        finally:
            job_id = job.get("job_id")
            active_key = f"active:{job['chat_id']}"

            if job_id is None or r.get(active_key) == job_id:
                r.delete(active_key)


if __name__ == "__main__":

    asyncio.run(main())