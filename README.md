# YouTube Downloader & Oryx Capture Bot

Internal tool yang aku gunakan untuk workflow capture/download video YouTube. Link dikirim melalui Telegram, lalu job dimasukkan ke antrean Redis dan diproses oleh worker menggunakan `yt-dlp`. Hasil akhirnya divalidasi dan disimpan sebagai MP4 yang kompatibel dengan H.264, AAC, dan `yuv420p`.

Tool ini dibuat untuk kebutuhan workflow internal, bukan sebagai layanan downloader publik.

## Fitur

- Menerima URL dari `youtube.com` dan `youtu.be` melalui Telegram.
- Mendukung download video biasa dan rekaman live stream.
- Antrean pekerjaan menggunakan Redis.
- Update progress download di chat Telegram.
- Rekam live Oryx privat dengan `/record` melalui FFmpeg langsung.
- Menghentikan download atau rekaman aktif dengan `/stop`, lalu finalisasi dan validasi.
- Finalisasi MP4 otomatis menggunakan FFmpeg.
- Nama file akhir memakai format `DDMMYYNN.mp4`, berdasarkan urutan download pada hari itu.

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
   ORYX_STREAM_URL=http://oryx/live/livestream.flv
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
   -> downloads/DDMMYY01.mp4
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

## Capture live Oryx

Alur: `vMix → SRT → Oryx/SRS → FFmpeg → TS sementara → MP4 → editor`.
Oryx tidak perlu meneruskan stream ke YouTube. YouTube tetap menggunakan yt-dlp.

Konfigurasikan `.env` (jangan commit credential):

```env
ORYX_STREAM_URL=http://oryx/live/livestream.flv
```

Gunakan **playback URL**, bukan URL publish SRT vMix atau halaman player Oryx.
Untuk jaringan internal, HTTP-FLV direkomendasikan sebagai pilihan awal; RTMP
juga dapat digunakan. HLS didukung FFmpeg tetapi biasanya lebih terlambat karena
buffer segmen. SRT playback membutuhkan dukungan SRT pada build FFmpeg dan
konfigurasi stream ID/port yang benar. Rekaman tidak melalui yt-dlp.

Hostname `oryx` di contoh harus bisa diakses dari container worker. Jika Oryx
berjalan di host Docker Desktop, gunakan alamat host yang sesuai, misalnya
`host.docker.internal`, beserta port playback sebenarnya. Untuk container lain,
siapkan jaringan Docker bersama atau alamat LAN yang dapat dijangkau.
Compose yang ada sudah membaca `.env` untuk bot dan worker; setelah mengubah
konfigurasi, jalankan `docker compose up -d --build`.

### Operasi manual

1. Pantau feed melalui player Oryx yang sudah ada.
2. Kirim `/record` di Telegram ketika ingin mulai mengambil klip.
3. Tunggu status **recording**; status **starting** belum berarti media terekam.
4. Kirim `/stop` ketika klip selesai.
5. Tunggu **stopping → finalizing → ready**. Hanya **ready** berarti FFprobe
   menerima MP4 H.264/AAC/yuv420p berdurasi positif dan penamaan akhir berhasil.
6. Ambil file `downloads/DDMMYYNN.mp4` dari folder host/shared folder.

Telegram mengirim status/nama file, bukan mengunggah video. Broadcast asli tetap
berjalan ketika recorder dihentikan. Setiap `/record` menghasilkan satu klip;
tidak ada rekaman full-program otomatis, segmentasi durasi, atau rolling buffer.
Kirim URL YouTube seperti sebelumnya; `/stop` berlaku untuk kedua sumber.

Satu worker memproses satu job hingga finalisasi selesai. `/record` ditolak jika
worker aktif, ada antrean YouTube, chat sudah aktif, atau heartbeat worker tidak
tersedia. Tidak ada antrean tertunda untuk capture Oryx: reservasi diklaim segera,
dan permintaan yang sudah berumur lebih dari 10 detik gagal tanpa mulai merekam.
URL YouTube tetap masuk antrean biasa. Tunggu **ready/failed** sebelum klip berikutnya.

### Format, Stop, dan kegagalan

FFmpeg Oryx melakukan stream copy ke `<job-id>-capture.ts`. Sesudah Stop, worker
meremux ke MP4 dengan stream copy bila video H.264/yuv420p dan audio AAC sudah
kompatibel. Codec lain ditranscode hanya bila dibutuhkan. Gunakan H.264/AAC dan
GOP pendek/teratur di vMix agar startup dan finalisasi lebih cepat. Input tanpa
audio belum memenuhi aturan validasi yang mewajibkan AAC.

Stop mengirim SIGINT ke process group, lalu SIGTERM/SIGKILL hanya jika proses
belum berhenti dalam batas waktu. yt-dlp dan turunannya juga berada dalam process
group sendiri. Operator Stop dengan exit code nonzero tetap mencoba finalisasi.
File `.part` yang dapat dibaca dan track audio/video terpisah dari yt-dlp juga
coba dipulihkan. Bila media tidak dapat dibaca atau audio belum tersedia, job gagal.

Jika sumber putus atau capture macet, worker menghentikan capture dan mencoba
menyelamatkan media yang sudah ada. Jika valid, status ready menyertakan peringatan
bahwa hanya sebagian media tersimpan. Jika tidak, status failed muncul dan file
sementara dipertahankan untuk pemeriksaan manual. Tidak ada reconnect otomatis
lintas protokol: mulai klip baru dengan `/record` setelah sumber pulih. File
sementara tidak dibersihkan otomatis; pantau kapasitas disk.

Finalisasi menulis file sementara terpisah dan melakukan replacement tanpa
menghapus original lebih dulu. Kegagalan probe/finalisasi/rename tidak dilaporkan
sebagai ready. Remux membutuhkan baca/tulis disk; transcoding dapat jauh lebih
lama. Startup bergantung pada keyframe, buffering server, dan protokol, sehingga
batas klip tidak dijamin frame-exact terhadap preview.

Timeout opsional di `.env` (detik, harus positif):

```env
CAPTURE_STARTUP_TIMEOUT=30
CAPTURE_IDLE_TIMEOUT=60
CAPTURE_STOP_TIMEOUT=10
FINALIZE_TIMEOUT=600
PROBE_TIMEOUT=20
```

Startup membatasi waktu tunggu media pertama/metadata YouTube. Idle membatasi
waktu tanpa pertumbuhan media; sesuaikan untuk jaringan lambat. Stop memberi
waktu pada setiap tahap penghentian proses/process group. Finalize adalah batas
per proses FFmpeg finalisasi/pemulihan; probe berlaku per pemeriksaan FFprobe.
Tidak ada batas durasi rekaman selama media terus diterima.

Log menyertakan job ID, sumber, PID capture, file sementara, alasan berhenti,
status, dan hasil finalisasi. URL input serta output mentah downloader tidak
dicetak agar credential playback tidak bocor. Bot ini tetap tool internal tanpa
allowlist pengguna Telegram; batasi akses deployment/bot sesuai kebutuhan.
Redis tetap tanpa persistence seperti konfigurasi awal. Restart/crash tidak
melanjutkan job aktif otomatis; tunggu reservasi kedaluwarsa dan mulai lagi.
Jalankan satu worker; scaling paralel dan jaminan recovery setelah crash tidak
termasuk perubahan ini.

### Pengujian lokal

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt 'fakeredis[lua]'
.venv/bin/python -m unittest discover -s tests -v
```

Tes menggunakan Redis palsu dengan eksekusi Lua dan Telegram mock. Instal FFmpeg
beserta FFprobe di host agar tes media dijalankan (jika tidak ada, tes tersebut
akan dilewati). Tes media mencakup TS→MP4 tanpa video encoding ulang, Stop nyata
pada FFmpeg dengan sumber sintetis, endpoint tidak tersedia, dan validasi nama.

Uji deployment setelah Oryx aktif:

```bash
docker compose up -d --build
docker compose logs -f bot worker
```

Kirim `/record`, tunggu recording, kirim `/record` lagi untuk memastikan ditolak,
lalu `/stop`. Pastikan file baru bisa dibuka editor dan stream Oryx tetap hidup.
Ulangi dengan URL YouTube dan `/stop`. Matikan sumber Oryx sementara untuk menguji
timeout/pemulihan parsial. Jangan masukkan URL bertoken ke log atau hasil tes yang
akan dibagikan.

## Web control room

Panel web memakai antrean dan worker yang sama, tanpa melewati Telegram.
Fitur awal: login operator, sumber Oryx tersimpan, cek koneksi, input YouTube,
Record/Stop, status/durasi/ukuran, catatan klip, katalog hasil, dan unduh MP4.

Tambahkan konfigurasi `.env`:

```env
WEB_PASSWORD=isi-password-operator-yang-kuat
WEB_SECRET_KEY=isi-string-acak-panjang
WEB_PORT=8080
```

Jangan commit nilai credential. `WEB_PASSWORD` wajib untuk login; jika
`WEB_SECRET_KEY` kosong, sesi login akan habis saat web restart. Untuk deployment
lokal ini credential acak sudah ditambahkan ke `.env` bila sebelumnya belum ada.

```bash
mkdir -p downloads data
docker compose up -d --build
```

Buka **http://localhost:8080**, login memakai `WEB_PASSWORD`. Jika worker/web
mengalami permission denied, pastikan folder `data` dan `downloads` bisa ditulis
UID/GID container. `data/capture.sqlite3` menyimpan sumber dan katalog secara
persisten melalui bind mount; jangan hapus folder ini jika ingin mempertahankan
pengaturan. Backup bersama folder downloads.

Untuk mengaktifkan Telegram juga:

```bash
docker compose --profile telegram up -d --build
```

Web-only tidak membutuhkan token Telegram. Service bot menggunakan profile
`telegram`; jika bot dari deployment lama sudah berjalan, ia tetap bisa digunakan.
Hentikan service bot bila ingin web-only. `/record` Telegram tetap memakai URL
`.env`; pilihan sumber tersimpan saat ini khusus web. Status pekerjaan Telegram
terlihat di web, dan operator web dapat menghentikan job aktif tersebut.

### Mengelola sumber

`ORYX_STREAM_URL` dipakai sebagai sumber awal saat database pertama dibuat.
Sesudah itu, pilih sumber untuk mengedit nama/URL, atau klik **Sumber baru**.
Gunakan **Cek koneksi**, lalu **Simpan sumber**. Mengubah `.env` tidak menimpa
sumber yang telah disimpan di database. Setiap Record menyimpan snapshot URL,
sehingga perubahan sumber tidak mengganggu rekaman aktif.

### Operasi

1. Pilih sumber Oryx atau masukkan URL YouTube.
2. Tambahkan catatan opsional, lalu klik **Mulai rekam**.
3. Tunggu status recording, kemudian klik **Stop** saat klip selesai.
4. Tunggu **ready**, lalu ambil file dari downloads/shared folder atau **Unduh**.

Web menolak Record jika worker offline, sibuk, atau ada job antrean. Stop selalu
menargetkan job yang terlihat, sehingga tab lama tidak menghentikan job baru.
State dan progress memakai Redis; katalog hasil memakai SQLite. Katalog mencatat
hingga 200 aktivitas terbaru yang ditampilkan, tanpa URL/credential playback.
File lama sebelum fitur web dibuat tetap berada di downloads tetapi tidak otomatis
masuk katalog. State aktif dari worker yang crash ditampilkan sebagai interrupted
ketika reservasi kedaluwarsa. Tidak ada recovery otomatis setelah crash.

Port web default terikat ke localhost. Untuk akses dari jaringan internal, atur
`WEB_BIND=0.0.0.0` dan akses `http://IP_RECORDER:8080`. Gunakan reverse proxy HTTPS
untuk akses jaringan, lalu `WEB_COOKIE_SECURE=1`. Jangan aktifkan secure cookie
pada HTTP biasa. Login memakai satu password operator bersama, sesi 8 jam,
proteksi CSRF, dan pembatasan percobaan login. Pengelolaan akun/peran terpisah,
preview live, dan integrasi NAS otomatis belum termasuk versi ini.

Tes web dan worker:

```bash
.venv/bin/pip install -r requirements.txt 'fakeredis[lua]'
.venv/bin/python -m unittest discover -s tests -v
```


## Ruang disk, judul klip, penanda, dan filter

Panel menunjukkan kapasitas filesystem **downloads** yang digunakan recorder.
Record ditolak jika ruang tersisa kurang dari **2 GiB** atau disk tidak dapat
diperiksa. Worker mengecek ulang sebelum capture (termasuk job Telegram).
Atur batas sesuai kebutuhan melalui `.env`:

```env
MIN_FREE_DISK_GB=2
```

Nilainya dalam GiB dan harus positif. Sisakan ruang untuk file sementara dan
remux MP4; batas ini bukan jaminan cukup untuk rekaman dengan durasi tak terbatas.
Rekaman yang sudah berjalan tidak dihentikan otomatis oleh fitur ini.

Kolom **Judul klip** menjadi awalan nama file: `Batam menyapa -14092601.mp4`.
Tanpa judul, nama tetap `14092601.mp4`. Nomor urut harian dihitung bersama untuk
file dengan/tanpa judul. Karakter path, karakter kontrol, dan karakter nama file
yang tidak valid dibersihkan; panjang judul dibatasi agar aman untuk filesystem.
File lama tidak diubah namanya.

Saat status **recording**, isi catatan momen lalu klik **Tandai**. Penanda memakai
waktu server relatif terhadap mulai capture, bukan waktu absolut siaran atau
frame-exact di MP4. Penanda tidak memotong video. Daftar penanda tetap tersimpan
di SQLite dan bisa dibuka di baris hasil rekaman, termasuk jika capture gagal.

Cari hasil berdasarkan judul/nama file/nama sumber, lalu kombinasikan filter
sumber (Oryx/YouTube), status, dan tanggal. Tanggal mengikuti zona waktu server.
Pencarian dilakukan atas katalog tersimpan, bukan hanya 200 hasil terbaru;
maksimal 200 hasil yang cocok ditampilkan. Klik Reset untuk menghapus filter.
