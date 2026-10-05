import os
import json
import re
import uuid
import time
try:
    from . import storage, telegram_controls, telegram_store
except ImportError:
    import storage
    import telegram_controls
    import telegram_store
# pyrefly: ignore [missing-import]
import redis

# pyrefly: ignore [missing-import]
from telegram import Update
# pyrefly: ignore [missing-import]
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
ORYX_STREAM_URL = os.getenv("ORYX_STREAM_URL", "").strip()

# Admission and reservation are atomic with the worker's queue claim. Oryx
# requests must never wait behind a download or another capture.
ADMIT_ORYX = """
if redis.call('EXISTS', 'worker:heartbeat') == 0 then return 'offline' end
if redis.call('EXISTS', KEYS[1]) == 1 then return 'duplicate' end
if redis.call('EXISTS', 'capture:owner') == 1 or
   redis.call('LLEN', 'download_queue') > 0 then return 'busy' end
redis.call('SET', 'capture:owner', ARGV[1], 'EX', 30)
redis.call('SET', KEYS[1], ARGV[1], 'EX', 30)
redis.call('RPUSH', 'download_queue', ARGV[2])
return 'accepted'
"""

r = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)
controls = telegram_controls.Controls(r)


def is_youtube_url(text):
    return bool(
        re.search(
            r"https?://(www\.)?(youtube\.com|youtu\.be)/",
            text,
            re.IGNORECASE,
        )
    )


@telegram_controls.authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "StreamFetch\n\n"
        "Kirim link YouTube, TikTok video/Live, atau Instagram Reel/post video.\n\n"
        "Perintah:\n"
        "/record - rekam live Oryx yang dikonfigurasi\n"
        "/stop - hentikan capture\n"
        "/watch · /watchlist · /cancelwatch\n"
        "/transcribe - pilih rekaman"
    )


@telegram_controls.authorized
async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id
    job_id = r.get(f"active:{chat_id}")

    if not job_id:
        await update.message.reply_text(
            "ℹ️ Lagi gak ada download yang jalan nih."
        )
        return

    r.set(
        f"stop:{job_id}",
        "1",
        ex=300,
    )

    await update.message.reply_text(
        "Kompresi dibatalkan · Original tetap disimpan." if r.get(f"state:{job_id}") == "finalizing" else
        "🛑 Oke, lagi diberentiin...\nMenunggu capture berhenti, lalu file akan difinalisasi dan divalidasi."
    )


@telegram_controls.authorized
async def record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ORYX_STREAM_URL:
        await update.message.reply_text("❌ ORYX_STREAM_URL belum dikonfigurasi.")
        return
    job = {
        "job_id": uuid.uuid4().hex,
        "chat_id": update.effective_chat.id,
        "source": "oryx",
        "requested_at": time.time(),
    }
    try:
        result = r.eval(ADMIT_ORYX, 1, f"active:{job['chat_id']}",
                        job["job_id"], json.dumps(job))
    except redis.exceptions.RedisError:
        await update.message.reply_text("❌ Antrean tidak tersedia. Coba lagi nanti.")
        return
    messages = {
        "accepted": "⏳ Starting: menyiapkan rekaman Oryx. Tunggu status recording.",
        "duplicate": "❌ Chat ini masih punya proses aktif. Tunggu selesai sebelum /record lagi.",
        "busy": "❌ Worker sedang sibuk. /record ditolak, tidak dimasukkan antrean.",
        "offline": "❌ Worker tidak tersedia. /record tidak dimasukkan antrean.",
    }
    await update.message.reply_text(messages[result])
    if result == 'accepted':
        telegram_store.subscribe(job['job_id'], job['chat_id'], update.effective_user.id, monitor_capture=True)


@telegram_controls.authorized
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if await controls.watch_reply(update, context):
        return

    url = update.message.text.strip()

    source = 'youtube'
    try:
        url = storage.validate_url(url, youtube=True)
    except ValueError:
        try:
            url = storage.validate_tiktok_url(url)
            source = 'tiktok'
        except ValueError:
            try:
                url = storage.validate_instagram_url(url)
                source = 'instagram'
            except ValueError:
                await update.message.reply_text('❌ Kirim URL YouTube, TikTok video/Live, atau Instagram Reel/post video.')
                return

    job = {
        "job_id": uuid.uuid4().hex,
        "chat_id": update.effective_chat.id,
        "url": url,
        "source": source,
        "source_name": {'tiktok': 'TikTok', 'instagram': 'Instagram', 'youtube': 'YouTube'}[source],
    }

    if source in {'tiktok', 'instagram'}:
        job['is_live'] = source == 'tiktok' and storage.tiktok_is_live(url)
    if job.get('is_live'):
        job['requested_at'] = time.time()
        try:
            result = r.eval(ADMIT_ORYX, 1, f"active:{job['chat_id']}", job['job_id'], json.dumps(job))
        except redis.exceptions.RedisError:
            await update.message.reply_text('❌ Antrean tidak tersedia. Coba lagi nanti.')
            return
        await update.message.reply_text('⏳ Menyiapkan rekaman TikTok Live.' if result == 'accepted'
                                        else '❌ Worker sibuk atau offline. Coba lagi setelah siap.')
        if result == 'accepted':
            telegram_store.subscribe(job['job_id'], job['chat_id'], update.effective_user.id, monitor_capture=True)
        return

    r.rpush(
        "download_queue",
        json.dumps(job),
    )
    telegram_store.subscribe(job['job_id'], job['chat_id'], update.effective_user.id, monitor_capture=True)

    await update.message.reply_text(
        "📥 Siap, udah masuk antrian download."
    )


def main():

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(controls.post_init)
        .post_stop(controls.post_stop)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("stop", stop)
    )
    app.add_handler(CommandHandler("record", record))
    app.add_handler(CommandHandler('watch', controls.watch))
    app.add_handler(CommandHandler('watchlist', controls.watchlist))
    app.add_handler(CommandHandler('cancelwatch', controls.cancelwatch))
    app.add_handler(CommandHandler('transcribe', controls.transcribe))
    app.add_handler(CallbackQueryHandler(controls.callback, pattern=r'^transcribe:'))
    app.add_handler(CallbackQueryHandler(controls.watch_callback, pattern=r'^watch:'))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url,
        )
    )

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()
