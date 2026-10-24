import os
import json
import logging
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
from telegram import BotCommand, Update
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
log = logging.getLogger(__name__)


BOT_COMMANDS = [
    BotCommand('start', 'Buka menu StreamFetch'),
    BotCommand('download', 'Pindah ke workspace Download'),
    BotCommand('transcript', 'Pindah ke workspace Transcript Only'),
    BotCommand('record', 'Rekam live Oryx'),
    BotCommand('stop', 'Hentikan capture'),
    BotCommand('watch', 'Jadwalkan pantauan YouTube'),
    BotCommand('watchlist', 'Lihat Watch aktif'),
    BotCommand('cancelwatch', 'Batalkan Watch'),
    BotCommand('transcribe', 'Transkrip rekaman yang ada'),
]


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
    snapshot = telegram_store.workspace(update.effective_chat.id, update.effective_user.id)
    active = controls.workspace_label(snapshot)
    await update.message.reply_text(
        f"StreamFetch\nWorkspace aktif: {active}\n\n"
        + ("Kirim link YouTube/Shorts, TikTok, atau Instagram Reels.\n\n"
           "Kamu juga bisa mengirim file MP3, MP4, M4A, WAV, MOV, atau WEBM.\n\n"
           if snapshot['workspace'] == 'transcript' else
           "Kirim link YouTube, TikTok video/Live, atau Instagram Reel/post video.\n\n") +
        "Perintah:\n"
        "/download - workspace Download\n"
        "/transcript - workspace Transcript Only\n"
        "/record - rekam live Oryx yang dikonfigurasi\n"
        "/stop - hentikan capture\n"
        "/watch · /watchlist · /cancelwatch\n"
        "/transcribe - pilih rekaman",
        reply_markup=controls.mode_buttons(snapshot),
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

    snapshot = telegram_store.workspace(update.effective_chat.id, update.effective_user.id)

    if await controls.watch_reply(update, context):
        return

    url = update.message.text.strip()

    if snapshot['workspace'] == 'transcript':
        try:
            url, source, source_id, source_name = telegram_controls.transcript_url(url)
        except ValueError as exc:
            await update.message.reply_text('❌ ' + str(exc))
            return
        await controls.transcript_only(update, url, snapshot, source, source_id, source_name)
        return

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
        "user_id": update.effective_user.id,
        "url": url,
        "source": source,
        "source_name": {'tiktok': 'TikTok', 'instagram': 'Instagram', 'youtube': 'YouTube'}[source],
        "owner_type": "telegram",
        "owner_id": str(update.effective_user.id),
        "workspace": "download",
        "workspace_generation": snapshot['generation'],
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


async def post_init(application):
    await controls.post_init(application)
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        log.warning('telegram command menu update failed', exc_info=True)


def main():

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
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
    app.add_handler(CommandHandler('download', controls.download))
    app.add_handler(CommandHandler('transcript', controls.transcript))
    app.add_handler(CallbackQueryHandler(controls.cancelwatch_callback, pattern=r'^watchcancel:'))
    app.add_handler(CallbackQueryHandler(controls.mode_callback, pattern=r'^(?:workspace|mode):'))
    app.add_handler(CallbackQueryHandler(controls.transcript_ai_callback, pattern=r'^transcriptai:'))
    app.add_handler(CallbackQueryHandler(controls.transcript_cancel_callback, pattern=r'^transcriptcancel:'))
    app.add_handler(CallbackQueryHandler(controls.transcript_text_callback, pattern=r'^transcripttext:'))
    app.add_handler(CallbackQueryHandler(controls.transcript_send_callback, pattern=r'^transcriptsend:'))
    app.add_handler(CallbackQueryHandler(controls.upload_ai_callback, pattern=r'^uploadai:'))
    app.add_handler(CallbackQueryHandler(controls.upload_cancel_callback, pattern=r'^uploadcancel:'))
    app.add_handler(CommandHandler('transcribe', controls.transcribe))
    app.add_handler(CallbackQueryHandler(controls.callback, pattern=r'^transcribe:'))
    app.add_handler(CallbackQueryHandler(controls.watch_callback, pattern=r'^watch:'))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url,
        )
    )
    app.add_handler(MessageHandler(filters.ATTACHMENT & ~filters.COMMAND,
                                   controls.transcript_upload))

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()
