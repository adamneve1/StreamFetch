# StreamFetch

**StreamFetch** adalah aplikasi sederhana untuk merekam stream langsung serta mengunduh video atau audio YouTube.

Stream langsung diproses dengan FFmpeg, sedangkan YouTube diproses dengan `yt-dlp`. Hasil akhirnya disimpan sebagai MP4 atau MP3.

Panel web menjadi antarmuka utama. Telegram tersedia sebagai kontrol opsional.

Control Room menampilkan Source lalu History. Setelah capture diterima, Source
otomatis menjadi tombol “＋ Capture another source”, URL yang dikirim dikosongkan,
dan job baru disorot di History. Pilihan quality, format, kompresi, dan tujuan
penyimpanan tetap tersimpan di form. Jika pengiriman gagal, input tetap utuh.
Filter History direset setelah capture berhasil agar job baru terlihat.

Navigasi Admin memisahkan pengelolaan sumber tersimpan, password (khusus admin),
status penyimpanan, dan panduan konfigurasi sistem/default dari workflow capture.
Pengaturan provider, model, concurrency, dan resource tetap dikelola melalui
`.env`/Docker Compose; halaman ini tidak membuat API konfigurasi baru atau
menampilkan kredensial. Hak akses API yang sudah ada tidak berubah.

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

### Transkripsi

- Transkripsi manual untuk media yang sudah berstatus siap.
- `faster-whisper` model `small`, CPU `int8`, dengan deteksi bahasa otomatis.
- Worker dan antrean Redis terpisah dari proses capture/download.
- Satu transkripsi diproses pada satu waktu untuk membatasi beban CPU.
- Hasil TXT dan SRT tersimpan di samping file media asli.
- Satu tombol transkripsi mengikuti status Generate Transcript, Waiting,
  Generating, View Transcript, atau Retry Transcript. Saat diproses, progress
  dan perkiraan waktu tersisa ditampilkan tanpa detail provider/model.
  Perkiraan diperbarui dari hasil pemrosesan, termasuk saat fallback ke lokal.
- **View Transcript** membuka reader dengan informasi program, Full Description,
  pencarian dengan navigasi hasil, mode timestamp None/Segment/Subtitle, copy,
  export TXT/SRT/VTT yang tersedia, dan Raw view. Mengubah tampilan tidak
  mentranskripsi ulang atau mengubah file sumber.
- Reader menampilkan player YouTube 16:9 untuk rekaman dengan ID video valid;
  klik timestamp untuk seek. Sumber lain atau rekaman lama tanpa ID tetap
  menampilkan transkrip tanpa player. Metadata sekunder dan deskripsi asli
  tersedia di Full Description.
- Desktop memakai dua kolom: video/metadata sticky di kiri dan transkrip dengan
  search/toolbar sticky di kanan. Tablet/mobile kembali ke satu kolom.
  Timestamp tampilan memakai MM:SS atau H:MM:SS; format copy/export tetap sama.
- Deskripsi YouTube lengkap disimpan saat download baru dan menjadi sumber
  metadata berlabel (Program, Tanggal/Jam, Tema, Narasumber, Presenter).
  Rekaman lama tanpa deskripsi tetap dapat dibaca; fakta yang tidak tersedia
  tidak dibuat-buat. Tanggal upload diberi label terpisah dari tanggal program.

### Telegram

Telegram merupakan interface opsional.

Perintah utama:

```text
/record
/stop
/watch
/watchlist
/cancelwatch ID
/transcribe
```

URL YouTube juga dapat dikirim langsung ke bot untuk dimasukkan ke antrean.

Telegram dan web menggunakan Redis serta worker yang sama sehingga tidak menjalankan pipeline recording terpisah.

Set `TELEGRAM_ALLOWED_USER_IDS` ke ID user Telegram numerik (dipisahkan koma),
misalnya `123456789,987654321`. Tanpa allowlist, semua command/callback ditolak.
`STREAMFETCH_PUBLIC_URL` harus menunjuk workspace yang bisa dibuka pengguna,
misalnya `https://streamfetch.example.com`; View Transcript tetap memakai login web.

`/watch` memandu konfigurasi channel → Today/Tomorrow/Choose date → window dalam
satu pesan → auto-transcribe Yes/No → Confirm. Channel menerima `@rribatam`,
`youtube.com/@rribatam`, atau URL lengkap. Tombol Cancel atau `/cancelwatch`
membatalkan konfigurasi; input salah tidak menghapus langkah yang sedang diisi.
Window menerima `08:00-10:00`, `8:00 - 10:00`, atau `08.00-10.00`, dengan opsional
`WIB`. Cara singkat (langsung tersimpan):

```text
/watch @rribatam today 08:00-10:00
/watch https://youtube.com/@rribatam tomorrow 08.00-10.00
/watch @rribatam tomorrow 09:00-12:00 every yes
```

Mode default `first` (tanpa pertanyaan mode), auto-transcribe default `no` untuk
command satu baris. Mode `every` dapat dipilih secara eksplisit lewat command;
format lama dengan jam terpisah tetap didukung. Jam akhir harus sesudah jam
mulai pada tanggal yang sama; window yang sudah berakhir ditolak. Polling default
30 detik (`TELEGRAM_WATCH_POLL_SECONDS`, minimum 10). Discovery memakai metadata
20 siaran terbaru pada tab Streams, hanya memilih video yang sudah `is_live`,
dan mengirim URL video aktual ke antrean capture yang ada. Siaran aktif saat
window mulai juga ditemukan pada poll pertama. Satu video ID hanya dijadwalkan
sekali oleh watch, termasuk lintas watch/restart. Mode every melanjutkan discovery
untuk video live berikutnya. Capture yang masuk antrean tetap mengikuti kapasitas
worker yang ada; ketika worker sibuk, capture dapat menunggu.

Watch dan subscription notifikasi tersimpan di `data/capture.sqlite3`. Bot harus
memakai volume data yang sama dengan worker/web; Compose sudah mengaturnya.
`/watchlist` menampilkan watch milik user/chat saat ini. `/cancelwatch ID` hanya
menghentikan discovery milik user tersebut, tidak menghentikan capture yang sudah
dijadwalkan. `/cancelwatch` tanpa ID membatalkan wizard yang sedang diisi.

`/transcribe` menampilkan rekaman MP4 siap terbaru, mengutamakan yang belum punya
transkrip. Tombol Transcribe juga dikirim setelah capture selesai. Callback memakai
admission transkripsi bersama web; transkrip yang sudah selesai langsung menyediakan
View Transcript, dan yang gagal bisa diretry. Auto-transcribe watch dimasukkan setelah
capture berstatus ready. Bot memberi notifikasi status capture/transkripsi dan link
reader, bukan mengirim seluruh teks. Notifikasi diperiksa ulang setelah restart;
kegagalan pengiriman dicoba lagi, sehingga notifikasi dapat terulang pada crash
tepat setelah Telegram menerima pesan. Batasi izin akses grup tempat bot dipakai.

Aktifkan atau perbarui integrasi:

```sh
docker compose --profile telegram up -d --build bot web
```

## Format Output

Rekaman video menggunakan MP4. Preset Original dan Seimbang memakai target
kompatibilitas:

```text
Video : H.264
Audio : AAC
Pixel : yuv420p
```

Preset Hemat tetap memakai MP4 dan AAC, tetapi videonya menggunakan H.265/HEVC.

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

Untuk video, panel menyediakan preset ukuran file:

- **Original** (bawaan): mempertahankan stream jika sudah kompatibel; paling cepat.
- **Seimbang**: H.264 CRF 23 dan AAC 128 kbps; kompatibilitas luas.
- **Hemat**: H.265/HEVC CRF 27 dan AAC 128 kbps; lebih kecil, tetapi proses lebih lama
  dan perangkat lama mungkin tidak mendukung pemutaran HEVC.

Ukuran akhir preset Seimbang dan Hemat bergantung pada kompleksitas gambar, gerakan,
durasi, dan codec sumber. Perkiraan di panel menunjukkan ukuran media sumber sebelum
proses kompresi.

Media capture yang valid disimpan sebagai Original sebelum kompresi. Hasil kompresi
dipublikasikan secara atomik hanya setelah FFmpeg dan validasi ffprobe berhasil;
Original tidak ditimpa. Kegagalan, Stop saat pemrosesan, disk tidak cukup, atau
restart worker tetap menghasilkan rekaman siap dengan Original yang dapat diunduh.
Stop saat pemrosesan hanya membatalkan kompresi. Worker memulihkan checkpoint
pemrosesan saat restart dan membersihkan output sementara yang ditinggalkan.
Timeout pemrosesan adalah `min(FINALIZE_TIMEOUT_CAP, FINALIZE_TIMEOUT + durasi ×
FINALIZE_DURATION_MULTIPLIER)`; bawaan 600 detik + 4× durasi, maksimal 6 jam.
Jika durasi/progress tidak tersedia, UI menampilkan Memproses tanpa persentase palsu.

Livestream YouTube yang baru selesai dapat sementara berstatus `post_live` saat
YouTube masih membangun arsip VOD. StreamFetch mempertahankan file `.part`/`.ytdl`,
menunggu dengan backoff terbatas, memperbarui metadata, lalu menjalankan yt-dlp lagi
dengan nama format sementara yang sama agar track yang sudah selesai tidak diunduh
ulang. Nilai bawaan adalah tiga percobaan dengan jeda 20 lalu 40 detik; atur melalui
`YOUTUBE_POSTLIVE_ATTEMPTS` dan `YOUTUBE_POSTLIVE_RETRY_DELAY` bila diperlukan.

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
WEB_ADMIN_PASSWORD=isi-password-admin-yang-berbeda
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
FINALIZE_DURATION_MULTIPLIER=4
FINALIZE_TIMEOUT_CAP=21600
PROBE_TIMEOUT=20

# Cloudflare Workers AI transcription with local CPU fallback
TRANSCRIPTION_PROVIDER=auto
CLOUDFLARE_ACCOUNT_ID=isi_account_id
CLOUDFLARE_API_TOKEN=isi_api_token_workers_ai
CLOUDFLARE_TRANSCRIPTION_CONCURRENCY=3
CLOUDFLARE_TRANSCRIPTION_CHUNK_SECONDS=300
CLOUDFLARE_TRANSCRIPTION_CHUNK_OVERLAP_SECONDS=2
CLOUDFLARE_TRANSCRIPTION_TIMEOUT=120

# Local transcription fallback
WHISPER_CPU_THREADS=4
TRANSCRIPTION_CPUS=4.0
TRANSCRIPTION_MEMORY_LIMIT=8g

# Optional verified archive (disabled by default)
ARCHIVE_ENABLED=false
ARCHIVE_HOST_PATH=./archive
ARCHIVE_PATH=/archive
ARCHIVE_LOCAL_RETENTION_HOURS=24
ARCHIVE_MAX_RETRIES=3
ARCHIVE_RETRY_BASE_SECONDS=60
```

Jangan commit `.env`, token, password, URL bertoken, atau credential lainnya ke repository.

Login dengan `WEB_PASSWORD` mendapat akses pengguna biasa dan tidak dapat menghapus
riwayat maupun file hasil. Login dengan `WEB_ADMIN_PASSWORD` mendapat akses admin,
dapat menghapus riwayat, serta dapat mengganti password pengguna atau admin dari
panel **Pengaturan admin**. Password yang diganti lewat panel disimpan sebagai hash
di database dan menggantikan nilai awal dari `.env`.

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

Frontend memakai GSAP untuk koreografi login; Flip hanya untuk transisi Source dan status island.
Docker membangun aset npm yang terkunci dan menyajikannya lokal (tanpa CDN).
Untuk menjalankan web langsung dari checkout, bangun aset terlebih dahulu:

```bash
npm ci
npm run build
npm test
```

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

### TikTok dan Instagram

Pilih tab **TikTok** untuk video/post (`https://www.tiktok.com/@username/video/123`)
atau Live (`https://www.tiktok.com/@username/live`). Live otomatis memakai perilaku
rekaman yang ada; video biasa memakai download hingga selesai. Link pendek belum
didukung. Pilih **Instagram** untuk Reel (`https://www.instagram.com/reel/ID/`)
atau post video (`https://www.instagram.com/p/ID/`). Link yang sama dapat dikirim
ke Telegram. Stories, Instagram Live, profile, dan foto tidak didukung. Post tanpa
video menghasilkan pesan gagal yang jelas. Jika post berisi beberapa video, satu
job mengambil video pertama yang didukung, tanpa mengunduh gambar.

Semua sumber memakai worker yt-dlp, History, progress, cancellation, penyimpanan,
dan transkripsi MP4 yang sama. Judul/deskripsi, uploader, tanggal/durasi, dan ID
yang tersedia disimpan sebagai metadata sumber. Video privat, login, rate-limit,
dan perubahan extractor dapat menyebabkan kegagalan yang ditampilkan di History;
tidak ada konfigurasi akun/cookie sosial baru.

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

## Workflow Transkripsi

Pada item riwayat yang sudah `ready`, klik **Generate Transcript**:

```text
ready media
    ↓
transcription_queue (Redis)
    ↓
transcription-worker (concurrency 1)
    ↓
Cloudflare @cf/openai/whisper-large-v3-turbo
    └── 429 / timeout / network / 5xx → faster-whisper small / CPU / int8
    ↓
<nama-video>.txt + <nama-video>.srt + <nama-video>.vtt
```

Status berjalan melalui:

```text
Queued → Transcribing → Completed
                         └→ Failed
```

Dalam mode `auto`, worker memakai Cloudflare Workers AI sebagai provider utama.
FFmpeg mengekstrak audio mono 16 kHz ke FLAC sekali, lalu membuat chunk berbasis
waktu dengan overlap kecil. Maksimal tiga chunk diproses bersamaan secara default.
Timestamp kata dan segmen di-offset ke timeline video, lalu overlap dipangkas
menurut ownership window agar ucapan di batas chunk tidak hilang atau terduplikasi.

HTTP 429/kuota, timeout, error jaringan, dan HTTP 5xx otomatis mengulang seluruh
transkripsi memakai `faster-whisper small` CPU `int8` dari media asli. HTTP 401/403,
credential yang tidak lengkap, dan respons yang tidak valid dilaporkan sebagai
error agar kesalahan konfigurasi tidak tersembunyi. Video asli tidak diubah.
FFprobe menolak file rusak dan video tanpa stream audio sebelum inference dimulai.
Output ditulis ke file sementara dan dipublikasikan dengan rename atomik.

Metadata status disimpan di `data/capture.sqlite3`. Saat worker atau Redis
restart, worker membangun kembali antrean dari status durable `queued` atau
`transcribing`. Model tersimpan di `data/whisper-models`, sehingga download model
hanya diperlukan pada pemakaian pertama. Cache tambahan Hugging Face disimpan di
`data/huggingface`; Xet dinonaktifkan agar seluruh download memakai direktori
yang dapat ditulis oleh user non-root container.

PyAV dibatasi ke versi sebelum 19 karena `faster-whisper 1.2.1` masih memakai
parameter decoder yang dihapus pada PyAV 19.

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
│   ├── transcription_worker.py
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
│   ├── *.mp4
│   ├── *.txt
│   └── *.srt
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
