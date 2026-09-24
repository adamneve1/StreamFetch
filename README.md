# StreamFetch

**StreamFetch** adalah aplikasi sederhana untuk merekam stream langsung serta mengunduh video atau audio YouTube.

Stream langsung diproses dengan FFmpeg, sedangkan YouTube diproses dengan `yt-dlp`. Hasil akhirnya disimpan sebagai MP4 atau MP3.

Panel web menjadi antarmuka utama. Telegram tersedia sebagai kontrol opsional.

Project ini dibuat untuk kebutuhan workflow produksi dan broadcast internal, bukan sebagai layanan downloader publik.

## Arsitektur

```text
                       ┌─────────────────────┐
vMix ── SRT ──► Stream ►│                     │
                       │                     │
YouTube ───────────────►│     StreamFetch      │
                       │                     │
                       │ Web Control Room    │
                       │ Telegram (optional) │
                       └──────────┬──────────┘
                                  │
                               Redis
                                  │
                               Worker
                                  │
                       FFmpeg / yt-dlp
                                  │
                                  ▼
                     downloads/*.{mp4,mp3}
```

Untuk capture broadcast:

```text
vMix → server stream → StreamFetch → FFmpeg → MP4
```

Untuk YouTube:

```text
YouTube → StreamFetch → yt-dlp → MP4 / MP3
```

## Fitur

### Capture

- Rekam dari sumber stream langsung.
- Download video MP4 atau audio MP3 dari YouTube.
- Capture stream langsung menggunakan FFmpeg.
- Stop recording secara manual melalui web atau Telegram.
- Deteksi startup timeout dan stream yang berhenti mengirim media.
- Recovery parsial ketika sumber terputus.
- Satu worker memproses satu job hingga finalisasi selesai.

### Web Control Room

- Login operator.
- Mengelola beberapa sumber stream.
- Cek koneksi sumber sebelum recording.
- Mulai dan hentikan recording.
- Monitoring status, durasi, dan ukuran capture.
- Memberikan judul pada klip.
- Menambahkan catatan recording.
- Membuat penanda momen selama recording.
- Katalog hasil recording.
- Pencarian dan filter berdasarkan sumber, status, dan tanggal.
- Download MP4 atau MP3 langsung melalui panel.
- Monitoring kapasitas disk.

### Telegram

Telegram merupakan interface opsional.

Perintah utama:

```text
/record
/stop
```

URL YouTube juga dapat dikirim langsung ke bot untuk dimasukkan ke antrean.

Telegram dan web menggunakan Redis serta worker yang sama sehingga tidak menjalankan pipeline recording terpisah.

## Format Output

Rekaman video menggunakan MP4 dengan target kompatibilitas:

```text
Video : H.264
Audio : AAC
Pixel : yuv420p
```

Nama default:

```text
DDMMYYNN - Judul video.mp4
```

Contoh:

```text
14092601 - Berita pagi.mp4
14092602 - Wawancara.mp4
```

Untuk audio:

```text
14092603 - Berita pagi.mp3
```

Judul asli YouTube dipakai secara otomatis. Jika kolom judul diisi, judul manual
akan dipakai. Nomor urut dihitung berdasarkan hasil pada hari yang sama.

Untuk download audio YouTube, pilih **Audio (MP3)** pada panel. StreamFetch mengambil
audio terbaik yang tersedia dan menyimpannya sebagai `.mp3`; pilihan resolusi
video otomatis dinonaktifkan.

## Prasyarat

- Docker Engine atau Docker Desktop
- Docker Compose
- FFmpeg
- FFprobe
- Redis
- Server stream untuk live capture
- Bot Telegram jika interface Telegram digunakan

Pastikan Anda memiliki hak untuk merekam atau mengunduh konten yang diproses.

## Konfigurasi

Buat `.env` di root project:

```env
# Stream source
ORYX_STREAM_URL=http://oryx/live/livestream.flv

# Web
WEB_PASSWORD=isi-password-operator
WEB_SECRET_KEY=isi-string-acak-panjang
WEB_PORT=8080
WEB_BIND=127.0.0.1

# Telegram (optional)
TELEGRAM_BOT_TOKEN=isi_token_bot

# Redis
REDIS_HOST=redis

# Storage
MIN_FREE_DISK_GB=2

# Capture timeout
CAPTURE_STARTUP_TIMEOUT=30
CAPTURE_IDLE_TIMEOUT=60
CAPTURE_STOP_TIMEOUT=10
FINALIZE_TIMEOUT=600
PROBE_TIMEOUT=20

# Optional verified archive (disabled by default)
ARCHIVE_ENABLED=false
ARCHIVE_HOST_PATH=./archive
ARCHIVE_PATH=/archive
ARCHIVE_LOCAL_RETENTION_HOURS=24
ARCHIVE_MAX_RETRIES=3
ARCHIVE_RETRY_BASE_SECONDS=60
```

Jangan commit `.env`, token, password, URL bertoken, atau credential lainnya ke repository.

### Arsip sekunder opsional

Capture selalu ditulis dan difinalisasi di `/downloads`. Setelah status producer
menjadi `ready`, worker dapat menyalin file final secara asynchronous ke storage
sekunder. Kegagalan arsip tidak mengubah status producer dan file lokal tetap
dipertahankan.

Mount SMB, NFS, NAS, atau disk lokal pada host terlebih dahulu; StreamFetch tidak
melakukan mount dan tidak menerima credential storage. Atur `ARCHIVE_HOST_PATH`
ke mount host tersebut. Sebelum mengaktifkan arsip, buat marker di filesystem
tujuan (bukan di direktori mountpoint saat storage sedang tidak ter-mount):

```bash
touch /path/to/mounted/archive/.streamfetch-archive
```

Lalu isi `.env`, misalnya:

```env
ARCHIVE_ENABLED=true
ARCHIVE_HOST_PATH=/path/to/mounted/archive
ARCHIVE_PATH=/archive
```

Worker adalah satu-satunya service yang menerima mount `/archive`. File disalin
secara streaming ke `nama.ext.part`, SHA-256 sumber dan tujuan diverifikasi, lalu
dipublikasikan dengan rename atomik. Tujuan yang sudah ada dan cocok dianggap
sukses; isi yang berbeda menjadi `archive_conflict` dan tidak ditimpa. Kegagalan
sementara dicoba ulang dengan exponential backoff.

Validasi storage memeriksa bahwa `/archive` tercatat sebagai mount pada Linux,
marker `.streamfetch-archive` tersedia, dan direktori dapat ditulis. Marker
mencegah penulisan ke direktori lokal kosong ketika network mount host hilang.
Docker sendiri tidak dapat membedakan bind mount ke disk lokal dengan bind mount
ke network filesystem, sehingga marker harus dibuat ketika storage yang benar
sedang mounted. Retensi dicatat sebagai `local_cleanup_after`, tetapi versi ini
tidak menghapus file lokal secara otomatis.

## Menjalankan StreamFetch

Buat direktori persistent:

```bash
mkdir -p downloads data
```

Jalankan web control room dan worker:

```bash
docker compose up -d --build
```

Untuk mengaktifkan Telegram:

```bash
docker compose --profile telegram up -d --build
```

Periksa service:

```bash
docker compose ps
```

Pantau log:

```bash
docker compose logs -f
```

## Web Control Room

Secara default panel hanya tersedia melalui:

```text
http://localhost:8080
```

Login menggunakan `WEB_PASSWORD`.

Untuk mengakses dari jaringan internal:

```env
WEB_BIND=0.0.0.0
```

Kemudian akses:

```text
http://IP_RECORDER:8080
```

Untuk deployment melalui reverse proxy HTTPS:

```env
WEB_COOKIE_SECURE=1
```

Jangan aktifkan secure cookie jika panel masih menggunakan HTTP biasa.

## Workflow Recording

### Stream langsung

1. Pastikan sumber stream sudah aktif.
2. Pilih sumber pada StreamFetch.
3. Gunakan **Cek koneksi**.
4. Isi judul dan catatan jika diperlukan.
5. Klik **Mulai rekam**.
6. Tunggu hingga status berubah menjadi `recording`.
7. Tambahkan penanda jika ada momen penting.
8. Klik **Stop**.
9. Tunggu:

```text
stopping → finalizing → ready
```

Status `ready` hanya diberikan setelah MP4 berhasil divalidasi.

### TikTok Live

Pilih tab **TikTok Live**, masukkan `https://www.tiktok.com/@username/live`, lalu klik **Mulai rekam**. Link yang sama bisa dikirim ke Telegram. Akun harus sedang live dan dapat diakses dari server. Link video biasa dan link pendek belum didukung.

Tekan **Stop** untuk finalisasi MP4. Penanda momen, katalog, dan filter sumber juga mendukung TikTok. Rekaman dimulai saat terhubung, tanpa mengambil bagian sebelum capture dimulai.

Dukungan memakai extractor TikTok Live dari `yt-dlp` yang diinstal dalam Docker. Jika TikTok mengubah aksesnya, rebuild image dengan `docker compose build --no-cache worker` lalu `docker compose up -d --build`. CAPTCHA, pembatasan wilayah, atau siaran yang memerlukan login dapat membuat capture gagal.

Jika gagal sebelum status `recording`, log worker menampilkan `stage=inspection` dan kategori `reason`, misalnya `not_live`, `http_400`, atau `access_denied`. Panel menampilkan penjelasan yang sama tanpa URL playback atau credential. `not_live` berarti respons extractor; kondisi ini juga dapat terjadi saat informasi room tidak dapat dibaca.

Image menyertakan `yt-dlp[default,curl-cffi]` untuk dukungan koneksi browser yang diminta extractor TikTok. Setelah memperbarui aplikasi, tunggu rekaman aktif selesai sebelum menjalankan `docker compose up -d --build worker web`.

### YouTube

Masukkan URL YouTube melalui web, lalu pilih **Video (MP4)** atau **Audio (MP3)**.
URL yang dikirim melalui Telegram tetap memakai format MP4.

Workflow:

```text
URL
 ↓
Redis Queue
 ↓
Worker
 ↓
yt-dlp
 ↓
Validation
 ↓
MP4 / MP3
```

## Finalisasi Recording

Capture stream langsung pertama kali ditulis sebagai:

```text
<job-id>-capture.ts
```

Setelah recording dihentikan, StreamFetch memeriksa media.

Jika stream sudah:

```text
H.264 + AAC + yuv420p
```

FFmpeg melakukan **stream copy/remux**, sehingga video tidak perlu di-encode ulang.

```text
TS
 ↓
FFmpeg -c copy
 ↓
MP4
```

Jika codec tidak kompatibel, transcoding dilakukan hanya ketika diperlukan.

Setelah finalisasi, FFprobe memastikan:

- container dapat dibaca;
- video tersedia;
- audio tersedia;
- codec sesuai;
- durasi valid.

Baru setelah itu job mendapatkan status:

```text
ready
```

## Stop dan Recovery

Ketika operator menekan Stop:

```text
recording
   ↓
SIGINT
   ↓
stopping
   ↓
finalizing
   ↓
FFprobe
   ↓
ready
```

Jika proses tidak berhenti, StreamFetch dapat meningkatkan penghentian menjadi:

```text
SIGINT → SIGTERM → SIGKILL
```

Jika sumber terputus atau capture macet, worker mencoba menyelamatkan media yang sudah diterima.

Jika media masih valid, hasil dapat ditandai sebagai recording parsial.

Jika finalisasi atau validasi gagal:

```text
failed
```

File sementara dipertahankan untuk pemeriksaan manual.

StreamFetch tidak melakukan reconnect otomatis lintas protokol.

## Penanda Momen

Saat recording berlangsung, operator dapat menambahkan penanda melalui Web Control Room.

Contoh:

```text
00:02:14  Narasumber mulai menjawab
00:05:32  Statement utama
00:08:07  Closing
```

Penanda menggunakan waktu server relatif terhadap awal capture.

Penanda:

- tidak memotong video;
- tidak mengubah file media;
- tidak frame-exact;
- disimpan sebagai metadata di SQLite.

## Storage

Hasil akhir disimpan di:

```text
downloads/
```

Metadata web disimpan di:

```text
data/capture.sqlite3
```

Direktori tersebut menggunakan persistent bind mount.

StreamFetch menolak recording baru jika free space berada di bawah:

```env
MIN_FREE_DISK_GB=2
```

Batas tersebut hanya mencegah recording dimulai dalam kondisi disk hampir penuh. Recording yang sedang berjalan tidak otomatis dihentikan ketika kapasitas melewati batas.

## Struktur Project

```text
.
├── app/
│   ├── bot.py
│   ├── worker.py
│   └── ...
│
├── tests/
│   └── test_capture.py
│
├── data/
│   └── capture.sqlite3
│
├── downloads/
│   └── *.mp4
│
├── credentials/
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env
└── README.md
```

File credential, database runtime, temporary media, dan hasil recording tidak seharusnya dimasukkan ke Git.

## Pengujian

Buat virtual environment:

```bash
python3 -m venv .venv
```

Install dependency:

```bash
.venv/bin/pip install -r requirements.txt 'fakeredis[lua]'
```

Jalankan test:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Beberapa media test membutuhkan:

```text
ffmpeg
ffprobe
```

Jika binary tersebut tidak tersedia, test terkait media akan dilewati.

Test mencakup antara lain:

- duplicate recording protection;
- worker busy/offline handling;
- Stop dan shared finalization;
- TS → MP4 tanpa encode ulang;
- real FFmpeg capture + SIGINT;
- unavailable stream endpoint;
- startup timeout;
- finalization timeout;
- filename generation;
- interrupted YouTube track recovery;
- failure handling agar job yang gagal tidak dilaporkan sebagai ready.

## Operasional Docker

Status service:

```bash
docker compose ps
```

Log:

```bash
docker compose logs -f
```

Rebuild:

```bash
docker compose up -d --build
```

Stop:

```bash
docker compose down
```

Dengan Telegram:

```bash
docker compose --profile telegram up -d --build
```

## Batasan

StreamFetch saat ini dirancang sebagai tool internal dengan satu worker.

Beberapa batasan:

- Tidak ada parallel recording.
- Tidak ada automatic reconnect.
- Tidak ada rolling buffer.
- Tidak ada automatic segmentation.
- Tidak ada frame-accurate clipping.
- Tidak ada automatic NAS archival.
- Tidak ada user/role management terpisah.
- Tidak ada automatic recovery setelah worker crash.
- Redis tidak menggunakan persistence.
- Telegram belum memiliki allowlist operator.
- Temporary files tidak dibersihkan otomatis.

Untuk deployment produksi, batasi akses Web Control Room dan Telegram serta gunakan reverse proxy HTTPS.

## Keamanan

Jangan commit:

```text
.env
credentials/
data/
downloads/
*.mp4
*.ts
*.part
```

Hindari mencetak URL playback yang mengandung credential ke log.

Web Control Room menggunakan:

- password operator;
- session;
- CSRF protection;
- login rate limiting.

Untuk akses di luar localhost, gunakan HTTPS dan kontrol akses jaringan yang sesuai.

## Tujuan Project

StreamFetch dibuat untuk menyederhanakan workflow pengambilan klip dari live broadcast.

Daripada operator menjalankan FFmpeg dan mengelola file secara manual, StreamFetch menggabungkan proses tersebut menjadi:

```text
Source
  ↓
Record
  ↓
Mark
  ↓
Stop
  ↓
Finalize
  ↓
Validate
  ↓
Catalog
  ↓
Edit
```

YouTube tetap didukung sebagai sumber tambahan melalui pipeline `yt-dlp`.

## License

MIT License.
