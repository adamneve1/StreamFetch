import os
import json
import re
import uuid
import redis

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")

r = redis.Redis(
    host=REDIS_HOST,
    port=6379,
    decode_responses=True,
)


def is_youtube_url(text):
    return bool(
        re.search(
            r"https?://(www\.)?(youtube\.com|youtu\.be)/",
            text,
            re.IGNORECASE,
        )
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🎬 YouTube Downloader\n\n"
        "Send a YouTube URL to download/record.\n\n"
        "Commands:\n"
        "/stop - stop current recording"
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = update.effective_chat.id
    job_id = r.get(f"active:{chat_id}")

    if not job_id:
        await update.message.reply_text(
            "ℹ️ No active download found."
        )
        return

    r.set(
        f"stop:{job_id}",
        "1",
        ex=300,
    )

    await update.message.reply_text(
        "🛑 Stop requested.\n"
        "Waiting for downloader to finalize..."
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = update.message.text.strip()

    if not is_youtube_url(url):

        await update.message.reply_text(
            "❌ Please send a YouTube URL."
        )

        return

    job = {
        "job_id": uuid.uuid4().hex,
        "chat_id": update.effective_chat.id,
        "url": url,
    }

    r.rpush(
        "download_queue",
        json.dumps(job),
    )

    await update.message.reply_text(
        "📥 Added to download queue."
    )


def main():

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("stop", stop)
    )

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