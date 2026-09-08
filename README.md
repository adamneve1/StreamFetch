# YouTube Downloader Bot

Internal tool yang aku gunakan untuk workflow capture/download video YouTube. Link dikirim melalui Telegram, lalu job dimasukkan ke antrean Redis dan diproses oleh worker menggunakan `yt-dlp`. Hasil akhirnya divalidasi dan disimpan sebagai MP4 yang kompatibel dengan H.264, AAC, dan `yuv420p`.

Tool ini dibuat untuk kebutuhan workflow internal, bukan sebagai layanan downloader publik.

## Fitur

- Menerima URL dari `youtube.com` dan `youtu.be` melalui Telegram.
- Mendukung download video biasa dan rekaman live stream.
- Antrean pekerjaan menggunakan Redis.
- Update progress download di chat Telegram.
- Menghentikan download atau rekaman aktif dengan `/stop`.
- Finalisasi MP4 otomatis menggunakan FFmpeg.
- Nama file akhir memakai format `DDMMYY-judul.mp4`.
- Menghindari overwrite jika nama file sudah digunakan.

## Prasyarat

- Docker Desktop atau Docker Engine dengan Docker Compose.
- Bot Telegram dari [@BotFather](https://t.me/BotFather).
- Hak untuk mengunduh atau merekam konten yang diproses.

## Setup

1. Buat bot Telegram lewat [@BotFather](https://t.me/BotFather), lalu salin token bot.
2. Buat file `.env` di root project:

   ```env
   TELEGRAM_BOT_TOKEN=isi_token_bot_di_sini
   REDIS_HOST=redis
   ```

3. Pastikan folder `downloads/` tersedia dan dapat ditulis:

   ```bash
   mkdir -p downloads
   ```

4. Jalankan service:

   ```bash
   export UID=$(id -u)
   export GID=$(id -g)
   docker compose up -d --build
   ```

   Di macOS, `UID` dan `GID` biasanya tidak wajib, tetapi tetap aman untuk dicantumkan.

5. Buka bot di Telegram dan kirim `/start`, lalu kirim URL YouTube.

## Workflow

```text
Telegram URL
    -> bot.py
    -> Redis download_queue
    -> worker.py
    -> yt-dlp + FFmpeg
    -> downloads/DDMMYY-judul.mp4
```

Untuk menghentikan job yang sedang berjalan, kirim:

```text
/stop
```

## Perintah Docker

```bash
# Melihat log bot dan worker
docker compose logs -f bot worker

# Melihat status service
docker compose ps

# Menghentikan service
docker compose down

# Rebuild setelah mengubah kode atau dependency
docker compose up -d --build
```

File hasil download berada di folder `downloads/` pada host. Folder tersebut sengaja di-mount ke container sehingga file tetap tersedia setelah container dihentikan.

## Struktur Project

```text
.
├── app/
│   ├── bot.py       # Menerima URL dan memasukkan job ke Redis
│   └── worker.py    # Memproses antrean, download, validasi, dan finalisasi
├── credentials/     # Data credential lokal, tidak masuk Git
├── downloads/       # Hasil file MP4
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env             # Token Telegram dan konfigurasi lokal
```

## Catatan Penggunaan

Gunakan bot hanya untuk konten yang memang boleh kamu download atau rekam. Patuhi Terms of Service YouTube, hak cipta, dan peraturan yang berlaku di wilayahmu. Jangan commit `.env`, token bot, atau data credential ke repository.

## Lisensi

Project ini dirilis di bawah [MIT License](LICENSE).