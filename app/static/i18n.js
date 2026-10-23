// Language preference for the whole UI. Indonesian is the source of truth for the
// workspace pages: every string lives in the markup/app.js as-is, and this file
// only carries an English overlay. Keys are the Indonesian strings themselves, so
// `i18n(key)` is the identity in Indonesian mode and tests keep asserting the
// original wording. The transcript reader is built in English and declares
// SF_SOURCE_LANG='en'; its Indonesian overlay is SF_I18N_ID (keys = the English
// strings from reader markup/reader.js), and reader strings that are already
// Indonesian are keyed verbatim here in SF_I18N_EN so the reader can switch to
// English too. English/neutral keys without an entry simply pass through.
const SF_I18N_EN = {
  // login
  'Masuk StreamFetch':'Sign in to StreamFetch',
  'Masuk':'Sign in',
  'Masuk…':'Signing in…',
  // header
  'Pengguna':'User',
  'Memeriksa':'Checking',
  'Memeriksa sistem':'Checking system',
  'Buka perintah cepat':'Open quick commands',
  'Perintah cepat · Cmd/Ctrl+K':'Quick commands · Cmd/Ctrl+K',
  'Keluar':'Log out',
  // capture panel
  'Sumber live':'Live source',
  'Belum ada sumber':'No sources yet',
  'Belum ada sumber — tambahkan dulu':'No sources yet — add one first',
  'Link TikTok':'TikTok link',
  'Video/post atau @username/live.':'Video/post or @username/live.',
  'Link Instagram':'Instagram link',
  'Reel atau post video. Tidak mendukung Stories, Live, atau foto.':'Reels or video posts. Stories, Live, and photos are not supported.',
  'Link YouTube':'YouTube link',
  'Resolusi':'Resolution',
  'Terbaik tersedia':'Best available',
  'Pilih sumber dulu untuk melihat perkiraan ukurannya.':'Pick a source first to see the size estimate.',
  'Kompresi':'Compression',
  'Original · paling cepat':'Original · fastest',
  'Seimbang':'Balanced',
  'Hemat · lebih ringkas':'Compact · smaller files',
  'Pertahankan kualitas sumber tanpa kompresi tambahan.':'Keep source quality without extra compression.',
  'Detail preset':'Preset details',
  'Stream asli; remux bila diperlukan.':'Original stream; remuxed if needed.',
  'Judul file':'File title',
  '· opsional':'· optional',
  'Contoh: Batam menyapa':'Example: Batam greeting',
  'Kosongkan untuk memakai judul asli':'Leave empty to use the original title',
  'DDMMYYNN - Judul.mp4':'DDMMYYNN - Title.mp4',
  'Nama file: DDMMYYNN - {t}.{e}':'File name: DDMMYYNN - {t}.{e}',
  'Judul video':'Video title',
  'Judul':'Title',
  'Simpan ke':'Save to',
  'Lokal':'Local',
  'Lokal + arsip':'Local + archive',
  'Folder downloads.':'Downloads folder.',
  'File disimpan di lokal, lalu disalin ke arsip.':'Saved locally, then copied to the archive.',
  'File disimpan di folder downloads.':'Saved to the downloads folder.',
  'File disimpan di folder downloads. Arsipnya belum aktif.':'Saved to the downloads folder. Archive is not enabled yet.',
  'Video YouTube':'YouTube video',
  'Sudah ada di StreamFetch':'Already in StreamFetch',
  'Video ini sedang diproses':'This video is still processing',
  'Lihat di Riwayat':'View in History',
  'Lihat proses':'View progress',
  'Mulai':'Start',
  // status island / monitoring
  'Menyiapkan':'Preparing',
  'Menunggu':'Waiting',
  'Menunggu YouTube':'Waiting for YouTube',
  'Mengunduh':'Downloading',
  'Merekam':'Recording',
  'Memproses':'Processing',
  'Transkripsi':'Transcription',
  'Selesai':'Finished',
  'Gagal':'Failed',
  'Dibatalkan':'Cancelled',
  'Terputus':'Interrupted',
  'Menghubungkan':'Connecting',
  'Menghentikan':'Stopping',
  'Siap':'Ready',
  'Berjalan':'Running',
  'Hentikan proses':'Stop the process',
  'Hentikan transkripsi':'Stop transcription',
  'Sistem siap':'System ready',
  'Sistem offline':'System offline',
  'Transkripsi gagal.':'Transcription failed.',
  'Transkripsi dibatalkan. Media asli tetap tersimpan.':'Transcription cancelled. The original media is kept.',
  'Transkrip masuk antrean. Statusnya akan diperbarui otomatis.':'Transcript queued. Its status will update automatically.',
  'Capture gagal.':'Capture failed.',
  'Capture gagal':'Capture failed',
  'Lihat detail':'View details',
  '{n} capture':'{n} captures',
  'Capture baru':'New capture',
  'Detail belum tersedia.':'Details are not available yet.',
  // markers / filters / history table
  'Tandai momen':'Mark a moment',
  'Tulis catatan singkat':'Write a short note',
  'Simpan':'Save',
  'Filter riwayat':'History filters',
  'Cari judul, file, atau sumber…':'Search titles, files, or sources…',
  'Cari judul, file, atau sumber':'Search titles, files, or sources',
  'Pilih sumber':'Choose source',
  'Semua sumber':'All sources',
  'Pilih status':'Choose status',
  'Semua status':'All statuses',
  'Pilih tanggal':'Choose date',
  'Bersihkan':'Clear',
  'SUMBER':'SOURCE',
  'WAKTU':'TIME',
  'UKURAN':'SIZE',
  'Pilih semua riwayat':'Select all history',
  'Aksi':'Actions',
  'Riwayat':'History',
  'Belum ada hasil':'No results yet',
  'Unduh pilihan':'Download selection',
  'Hapus':'Delete',
  'Belum ada rekaman.':'No recordings yet.',
  'Mulai capture dari Source. Rekaman dan download akan muncul di sini.':'Start a capture from Source. Recordings and downloads will appear here.',
  'Tidak ada rekaman yang cocok.':'No matching recordings.',
  'Ubah pencarian atau bersihkan filter untuk melihat rekaman lainnya.':'Change the search or clear the filters to see other recordings.',
  'Notifikasi':'Notifications',
  // clipper room
  'Potong bagian YouTube dengan waktu yang presisi.':'Trim YouTube sections with precise timing.',
  'Tempel link untuk membaca judul dan durasi tanpa mengunduh media.':'Paste a link to read the title and duration without downloading media.',
  'Masukkan link YouTube yang valid.':'Enter a valid YouTube link.',
  'Membaca metadata video…':'Reading video metadata…',
  'Menunggu link YouTube…':'Waiting for the YouTube link…',
  'Durasi total · {d}':'Total duration · {d}',
  'Timestamp URL dipakai sebagai Start · {d}':'URL timestamp used as Start · {d}',
  'Metadata siap. Pilih durasi atau isi End.':'Metadata ready. Choose a duration or fill in End.',
  'Memuat preview YouTube…':'Loading the YouTube preview…',
  'Preview tidak tersedia untuk video ini. Timestamp manual tetap bisa dipakai.':'Preview is unavailable for this video. Manual timestamps still work.',
  'Video ini tidak mengizinkan preview tersemat. Timestamp manual tetap bisa dipakai.':'This video does not allow embedded preview. Manual timestamps still work.',
  'Preview YouTube gagal dimuat. Timestamp manual tetap bisa dipakai.':'The YouTube preview failed to load. Manual timestamps still work.',
  'Posisi':'Position',
  'Waktu mulai':'Start time',
  'Atur waktu mulai':'Adjust start time',
  'Waktu selesai':'End time',
  'Atur waktu selesai':'Adjust end time',
  'Durasi cepat':'Quick duration',
  'Durasi':'Duration',
  'Durasi clip':'Clip duration',
  'Tukar waktu':'Swap times',
  'Pertahankan kualitas sumber jika kompatibel.':'Keep source quality when compatible.',
  'Membuat…':'Creating…',
  'Waktu mulai tidak valid. {m}':'Invalid start time. {m}',
  'Waktu selesai tidak valid. {m}':'Invalid end time. {m}',
  'Isi waktu selesai.':'Fill in the end time.',
  'Clip tidak boleh berdurasi nol.':'A clip cannot have zero duration.',
  'Waktu selesai harus setelah waktu mulai.':'The end time must be after the start time.',
  'Rentang clip melewati durasi video.':'The clip range exceeds the video duration.',
  'Timestamp harus berupa detik positif.':'The timestamp must be positive seconds.',
  'Timestamp belum valid.':'The timestamp is not valid.',
  'Gunakan detik, MM:SS, HH:MM:SS, atau 1m30s.':'Use seconds, MM:SS, HH:MM:SS, or 1m30s.',
  'YouTube Player API tidak tersedia.':'The YouTube Player API is unavailable.',
  'YouTube Player API gagal dimuat.':'The YouTube Player API failed to load.',
  // transcript-only room
  'Subtitle manual akan dicoba lebih dulu, lalu subtitle otomatis.':'Manual subtitles are tried first, then automatic subtitles.',
  'Subtitle manual maupun otomatis tidak tersedia atau diblokir.':'Neither manual nor automatic subtitles are available or allowed.',
  'Subtitle tidak tersedia':'Subtitles unavailable',
  'Gunakan AI transcription? Audio sementara akan dihapus setelah proses selesai.':'Use AI transcription? The temporary audio is deleted after processing.',
  'Uploading… {p}%':'Uploading… {p}%',
  'Upload belum berhasil. Coba lagi, ya.':'The upload did not finish. Try again.',
  'Koneksi upload terputus. Coba lagi, ya.':'The upload connection dropped. Try again.',
  // estimates / presets / compression copy
  'Lagi menghitung perkiraan ukuran…':'Calculating the size estimate…',
  'Perkiraan sumber {b} per jam':'Estimated source {b} per hour',
  ' · hasil preset bisa berbeda':' · preset result may differ',
  'Ukuran live bergantung pada durasi dan bitrate sumber':'Live size depends on the duration and bitrate of the source',
  'Perkiraan ukuran sumber ':'Estimated source size ',
  'Perkiraan ukuran ':'Estimated size ',
  'Ukuran belum tersedia dari sumber':'Size not yet available from the source',
  'Preset tidak tercatat':'Preset not recorded',
  'Hemat':'Compact',
  'Proses paling cepat, mempertahankan kualitas sumber jika kompatibel.':'Fastest processing, keeps source quality when compatible.',
  'Kualitas bagus dengan ukuran lebih hemat.':'Good quality at a smaller size.',
  'Ukuran lebih ringkas, proses lebih lama. Perangkat lama mungkin tidak mendukung.':'More compact and longer to process. Older devices may not support it.',
  'H.264 + AAC. Kompresi ulang hanya jika sumber belum kompatibel.':'H.264 + AAC. Recompressed only if the source is not compatible.',
  'Preset video tidak berlaku untuk audio MP3.':'Video presets do not apply to MP3 audio.',
  'Kompresi video tidak berlaku untuk audio MP3.':'Video compression does not apply to MP3 audio.',
  // generic errors / toasts
  'Tutup notifikasi':'Close notification',
  'Tutup':'Close',
  'Batal':'Cancel',
  'Lanjutkan':'Continue',
  'Layanannya lagi bermasalah ({s}). Coba muat ulang halaman, ya.':'The service is having trouble ({s}). Try reloading the page.',
  'Sesi kamu sudah habis. Masuk lagi, ya.':'Your session has ended. Please sign in again.',
  'Belum berhasil. Coba lagi, ya.':'Not successful yet. Try again.',
  'Coba lagi':'Try again',
  'Mengirim…':'Sending…',
  'Capture diterima. Menunggu worker…':'Capture received. Waiting for the worker…',
  'Percobaan {a}/{b} · Menunggu worker…':'Attempt {a}/{b} · Waiting for the worker…',
  'Percobaan {a}/{b} masuk antrean.':'Attempt {a}/{b} queued.',
  'Lagi dihentikan. File-nya akan muncul sebentar lagi.':'Stopping. The file will appear shortly.',
  'Preview tidak dapat diputar. File tetap bisa diunduh.':'The preview cannot be played. The file can still be downloaded.',
  'Nama file':'File name',
  // watch form / watches panel
  '{n} aktif':'{n} active',
  '0 aktif':'0 active',
  '+ Tambah Watch':'+ Add Watch',
  'Muat ulang':'Reload',
  'Jadwal bersama dari Web dan Telegram. Membatalkan watch tidak menghentikan capture yang sudah berjalan.':'Shared schedules from the Web and Telegram. Cancelling a watch does not stop a capture that is already running.',
  'Tambah Watch':'Add Watch',
  'YouTube channel URL atau @handle':'YouTube channel URL or @handle',
  'Tanggal (WIB)':'Date (WIB)',
  'Tanggal':'Date',
  'Mulai (WIB)':'Start (WIB)',
  'Selesai (WIB)':'End (WIB)',
  'Watch memakai WIB dan pipeline capture yang sama.':'Watches use WIB and the same capture pipeline.',
  'Simpan Watch':'Save Watch',
  'Menyimpan…':'Saving…',
  'Masukkan URL channel YouTube atau @handle.':'Enter a YouTube channel URL or @handle.',
  'Pilih tanggal Watch.':'Choose the watch date.',
  'Jam akhir harus setelah jam mulai (WIB).':'The end time must be after the start time (WIB).',
  'Window Watch sudah berakhir (WIB).':'The watch window has already passed (WIB).',
  'Watch dibuat. Menunggu live sesuai jadwal.':'Watch created. Waiting for the scheduled live.',
  'Watch dibatalkan. Capture yang sudah berjalan tetap berlanjut.':'Watch cancelled. A capture that is already running keeps going.',
  'Belum ada Watch. Tambah Watch untuk menjadwalkan capture live.':'No watches yet. Add a watch to schedule a live capture.',
  'Batalkan watch {n}':'Cancel watch {n}',
  'Batalkan':'Cancel',
  'Terakhir: {t}':'Last: {t}',
  'Bagian Settings':'Settings sections',
  'Mau ganti password? Pilih akunnya di bawah. Akun tersebut akan diminta masuk lagi setelah password diganti.':'Change a password? Pick the account below. That account will be asked to sign in again after the password is changed.',
  'Akun':'Account',
  'Password admin sekarang':'Current admin password',
  'Password baru':'New password',
  'Ulangi password baru':'Repeat the new password',
  'Ganti password':'Change password',
  'Password yang kamu ulangi belum sama.':'The repeated password does not match.',
  'Sip, password-nya sudah diganti.':'The password has been changed.',
  // saved sources
  'Simpan link stream yang sering kamu pakai.':'Save the stream links you use often.',
  'Tambah atau ubah sumber':'Add or edit sources',
  'Nama':'Name',
  'Studio utama':'Main studio',
  'Link stream':'Stream link',
  'Bisa pakai HTTP-FLV, HLS, RTMP, atau SRT.':'Works with HTTP-FLV, HLS, RTMP, or SRT.',
  'Kosongkan form':'Clear form',
  'Cek koneksi':'Test connection',
  'Lagi mencoba terhubung…':'Testing the connection…',
  'Berhasil tersambung · {c}':'Connected · {c}',
  'Sip, sumbernya sudah tersimpan dan siap dipakai.':'Saved. The source is ready to use.',
  // storage / system
  'Penyimpanan / arsip':'Storage / archive',
  'Memeriksa penyimpanan…':'Checking storage…',
  'Persentase ruang penyimpanan terpakai':'Percentage of storage in use',
  '{a} kosong dari {b}':'{a} free of {b}',
  'Penyimpanan belum bisa dicek':'Storage could not be checked yet',
  'Batas minimum: {b} · {s}':'Minimum limit: {b} · {s}',
  'Siap dipakai':'Ready to use',
  'Ruangnya perlu dikosongkan dulu':'Free up space first',
  'Pengaturan sistem dikelola melalui .env dan Docker Compose. Perubahan memerlukan restart layanan terkait; tidak mengubah opsi capture yang sudah dikirim.':'System settings are managed via .env and Docker Compose. Changes require restarting the related service; capture options already submitted stay unchanged.',
  'Batas resource':'Resource limits',
  'Quality, format, kompresi, dan tujuan penyimpanan tetap dipilih per capture di Source. Kredensial tidak ditampilkan di sini.':'Quality, format, compression, and the storage target are still picked per capture in Source. Credentials are not shown here.',
  // command palette
  'Perintah cepat':'Quick commands',
  'Tutup perintah cepat':'Close quick commands',
  'Cari perintah':'Search commands',
  'Cari perintah…':'Search commands…',
  'Perintah':'Commands',
  'Tidak ada perintah yang cocok.':'No matching commands.',
  '↑ ↓ Pilih · Enter Jalankan · Esc Tutup':'↑ ↓ Choose · Enter Run · Esc Close',
  // history rows / details / actions
  '{a} / {b} hasil':'{a} / {b} results',
  '{n} hasil':'{n} results',
  'Filter berlaku pada {n} rekaman yang dimuat.':'Filter applies to {n} loaded recordings.',
  'Hasil riwayat':'History results',
  'Capture terbaru':'Latest capture',
  'Rekaman {i}':'Recording {i}',
  'Pilih {t}':'Select {t}',
  'Proses belum selesai. Buka detail untuk melihat penyebab.':'The process did not finish. Open the details to see why.',
  '{s} gagal memberikan media. Buka detail untuk melihat penyebab.':'{s} failed to provide media. Open the details to see why.',
  'Sumber':'Source',
  'Tanpa catatan':'No notes',
  '{n}× download':'{n}× download',
  '{n} tanda momen':'{n} moments marked',
  'Percobaan internal {a}{b}':'Internal attempt {a}{b}',
  'Percobaan {a}/{b}':'Attempt {a}/{b}',
  'Preset: {p}':'Preset: {p}',
  'Detail teknis':'Technical details',
  'Belum ada detail tambahan.':'No additional details yet.',
  '{n}× download · {l}':'{n}× download · {l}',
  'Belum pernah diunduh':'Never downloaded',
  'Unduh {f}':'Download {f}',
  'Unduh':'Download',
  'Preview {f}':'Preview {f}',
  'Aksi lainnya untuk {t}':'More actions for {t}',
  'Aksi lainnya':'More actions',
  'Aksi {t}':'Actions for {t}',
  'riwayat':'history',
  'Ubah nama':'Rename',
  'Hapus rekaman?':'Delete recording?',
  '“{f}” dan file lokalnya':'“{f}” and its local file',
  '{n} rekaman yang dipilih beserta file lokalnya':'{n} selected recordings and their local files',
  '{s} akan dihapus. Tindakan ini tidak dapat dibatalkan.':'{s} will be deleted. This action cannot be undone.',
  '{n} item berhasil dihapus':'{n} items deleted',
  '{n} item belum bisa dihapus':'{n} items could not be deleted yet',
  '{n} file mulai diunduh. Kalau browser bertanya, izinkan download beberapa file, ya.':'{n} files started downloading. If the browser asks, allow downloading multiple files.',
  'Nama file lokal sudah diganti. Nama di arsip tetap sama.':'The local file name changed. The name in the archive stays the same.',
  'Sip, nama file sudah diganti.':'The file name has been updated.',
  'Preview hasil media':'Media preview',
  // transcript reader (English overlay for reader strings that are Indonesian)
  'Memuat transkrip…':'Loading transcript…',
  'Cari kata atau frasa…':'Search words or phrases…',
  'Transkrip kosong.':'The transcript is empty.',
  'Tidak tersedia':'Not available',
  'Narasumber':'Guests',
  'Video masih dimuat. Posisi akan dibuka saat siap.':'The video is still loading. The position will open when it is ready.',
  'Video tidak dapat diputar di sini. Transkrip tetap tersedia.':'The video cannot play here. The transcript remains available.',
  'Video melekat dari sumbernya (embed).':'Video embedded from its source (embed).',
  'Buka video di sumbernya ↗':'Open the video at its source ↗',
  'Disalin ke clipboard.':'Copied to the clipboard.',
  'Clipboard tidak tersedia. Pilih teks dan salin secara manual.':'Clipboard unavailable. Select the text and copy it manually.',
  'Transkrip tidak dapat dibuka.':'The transcript cannot be opened.',
  'Deskripsi sumber tidak tersedia untuk rekaman ini.':'No source description available for this recording.',
'FILE': 'FILE',
'Riwayat Transkrip': 'Transcript History',
'Cari judul atau sumber…': 'Search titles or sources…',
'Belum ada riwayat transkrip.': 'No transcript history yet.',
'Transkrip dari URL YouTube atau upload akan muncul di sini.': 'Transcripts from YouTube URLs or uploads will appear here.',
'Filter riwayat transkrip': 'Transcript history filters',
'Cari riwayat transkrip': 'Search transcript history',
};
// Reader overlay: keys are the exact English strings from transcript.html and
// transcript.js; values are the Indonesian UI translations.
const SF_I18N_ID = {
  'Search transcript':'Cari transkrip',
  'Timestamps':'Penanda waktu',
  'None':'Tidak ada',
  'Segment':'Segmen',
  'Subtitle':'Subtitle',
  'Copy':'Salin',
  'Copy Transcript':'Salin transkrip',
  'Copy Info':'Salin info',
  'Copy All':'Salin semua',
  'Export':'Ekspor',
  'More':'Lainnya',
  'Raw view':'Tampilan mentah',
  'Full Description':'Deskripsi lengkap',
  'Previous match':'Kecocokan sebelumnya',
  'Next match':'Kecocokan berikutnya',
  'Export transcript':'Ekspor transkrip',
  'Export {k}':'Ekspor {k}',
  'Seek to {t}':'Lompat ke {t}',
  '{a} / {b} matches':'Kecocokan {a} / {b}',
  'No matches':'Tidak ada kecocokan',
  'Manual subtitles':'Subtitle manual',
  'Auto-generated subtitles':'Subtitle otomatis',
  'AI transcription':'Transkripsi AI',
  'Source upload date: {d}':'Tanggal unggah sumber: {d}',
  'Program':'Program',
  'Date/Time':'Tanggal/Waktu',
  'Theme':'Tema',
  'Guests / Narasumber':'Narasumber',
  'Presenter':'Presenter',
  'Source':'Sumber'
};

function sfLang() {
  let lang = 'id';
  try { lang = localStorage.getItem('sf-lang') || 'id'; } catch {}
  return lang === 'en' ? 'en' : 'id';
}

function i18n(key, params) {
  let value = key;
  const target = sfLang();
  if (target !== (window.SF_SOURCE_LANG || 'id')) {
    const overlay = target === 'en' ? SF_I18N_EN : SF_I18N_ID;
    const translated = overlay[key];
    if (translated !== undefined) value = translated;
  }
  if (params) {
    for (const [name, replacement] of Object.entries(params)) value = value.replaceAll('{' + name + '}', String(replacement));
  }
  return value;
}

function sfSetLang(lang) {
  lang = lang === 'en' ? 'en' : 'id';
  try { localStorage.setItem('sf-lang', lang); } catch {}
  document.documentElement.lang = lang;
  document.documentElement.dataset.srcLang = window.SF_SOURCE_LANG || 'id';
  document.documentElement.dataset.lang = lang;
  const attr = '[data-i18n]';
  for (const node of document.querySelectorAll(attr)) node.textContent = i18n(node.getAttribute('data-i18n'));
  for (const node of document.querySelectorAll('[data-i18n-placeholder]')) node.setAttribute('placeholder', i18n(node.getAttribute('data-i18n-placeholder')));
  for (const node of document.querySelectorAll('[data-i18n-title]')) node.setAttribute('title', i18n(node.getAttribute('data-i18n-title')));
  for (const node of document.querySelectorAll('[data-i18n-aria]')) node.setAttribute('aria-label', i18n(node.getAttribute('data-i18n-aria')));
  for (const node of document.querySelectorAll('.lang-toggle')) {
    const next = sfLang() === 'en' ? 'ID' : 'EN';
    node.textContent = next;
    node.setAttribute('aria-label', sfLang() === 'en' ? 'Ganti ke Bahasa Indonesia' : 'Switch to English');
    node.title = node.getAttribute('aria-label');
  }
  window.dispatchEvent(new CustomEvent('sf-lang-change'));
}

function sfToggleLang() { sfSetLang(sfLang() === 'en' ? 'id' : 'en'); }

if (typeof document !== 'undefined' && document.currentScript?.dataset?.init !== 'never') {
  document.addEventListener('DOMContentLoaded', () => {
    sfSetLang(sfLang());
    for (const button of document.querySelectorAll('.lang-toggle')) button.onclick = sfToggleLang;
  });
}
if (typeof module !== 'undefined') module.exports = { i18n, sfLang, sfSetLang, SF_I18N_EN, SF_I18N_ID };
