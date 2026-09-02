# Maimai-PCV

Hand tracking pakai webcam yang memetakan posisi kedua telapak tangan ke papan
sensor ala maimai, lalu membaca tap dan hold dari kepalan tangan. Tugas mata
kuliah PCV (semester 5).

Idenya: arahkan kamera ke diri sendiri, lalu program menentukan posisi tiap
tangan di atas layout maimai virtual, apakah tangan sedang mengepal, dan berapa
lama. Untuk sekarang event-nya baru dicetak ke console — menyambungkannya ke
chart adalah langkah berikutnya.

Isi repo:

- `test.py` — aplikasi utama (tracking + papan + gesture). Masih satu file.
- `chart_parser.py` — parser chart teks jadi daftar event (tap / hold + timing).
  Belum tersambung ke aplikasi kamera.
- `hand_landmarker.task` — model tangan MediaPipe.

## Cara menjalankan

```
python test.py
```

Butuh file `hand_landmarker.task` di folder yang sama (sudah ada di repo). Atur
`CAMERA_INDEX` di bagian atas file sesuai webcam yang dipakai — saya pakai Camo
lewat HP jadi index-nya `1`. Kalau layarnya hitam / tidak muncul, cek apakah
Camo memang sedang streaming, atau coba `CAMERA_INDEX` lain (webcam laptop
biasanya `0`). Window OpenCV sering muncul di belakang editor. Tekan `q` di
window itu untuk keluar.

Ada flag `RUN_PARSER_TEST` di atas file: set `True` untuk cuma menjalankan cek
`chart_parser` (print daftar event lalu keluar, tanpa buka kamera), `False`
untuk aplikasi kamera normal.

Diuji di Python 3.14, OpenCV 5.0, MediaPipe 1.0.1, NumPy 2.5.

Catatan: MediaPipe 1.x sudah menghapus API lama `mp.solutions.*`, jadi di sini
memakai Tasks API yang baru (`HandLandmarker`, mode LIVE_STREAM).

## Progress sejauh ini

![Screenshot progress](docs/screenshot.png)

**Papan.** 34 zona disusun seperti maimai: empat ring konsentris masing-masing
8 zona (diberi label B / E / A / D dari dalam ke luar) plus dua zona setengah
lingkaran di tengah (C1 kiri, C2 kanan). Ring yang berselang-seling diputar
22.5° supaya sambungannya saling mengunci. Pencarian zona murni pakai
perhitungan polar — radius menentukan ring, sudut menentukan slice — jadi tidak
ada loop jarak per zona.

**Hit scan.** Telapak tangan diperlakukan sebagai lingkaran berdiameter ~70px,
bukan satu titik. Kalau lingkaran itu menutupi dua atau tiga zona bersebelahan,
semuanya ikut terpicu. Ini memang disengaja supaya hit yang mepet lebih
toleran, sama seperti mesin aslinya.

**Gesture.** Kepalan tangan dihitung sebagai hit. Kepalan dideteksi dengan
mengecek apakah ujung jari lebih dekat ke pergelangan dibanding buku jari
tengahnya (mayoritas menekuk = mengepal). Menahan kepalan lalu melepasnya akan
mencetak durasi hold dan zona tempat hold itu dimulai.

**Feedback.** Papan digambar tiap frame; zona menyala oranye saat dilewati,
hijau saat tangan mengepal, dan berkedip putih sebentar saat hit terdaftar.

**Chart parser** (`chart_parser.py`). Membaca chart teks sederhana jadi daftar
event bertimestamp. Format: token dipisah koma, koma kosong = slot diam, satu
token bisa berisi beberapa not sekaligus (mis. `135` = not barengan). `{8}`
mengganti pembagian ketukan (berapa slot per bar). Not hold ditulis `4h[4:1]` —
posisi 4, tahan selama `multiplier × panjang-slot`. Output tiap event:
`{time_ms, type, pos}` (plus `duration_ms` untuk HOLD). Ini masih berdiri
sendiri; belum ada yang menampilkannya di papan atau mencocokkannya dengan hit
dari tangan.

**Kualitas tracking.** Landmark mentah dari MediaPipe cukup bergetar dan hanya
masuk ~25–35 kali per detik, yang kelihatan jelek saat tangan digerakkan cepat.
Setup saat ini:

- capture jalan di thread sendiri supaya proses decode tidak menahan loop
  tampilan
- deteksi berjalan async; loop utama selalu mengirim frame paling baru ke
  MediaPipe
- posisi telapak diambil dari rata-rata pergelangan + empat buku jari (tetap
  stabil saat tangan mengepal, tidak seperti ujung jari)
- filter One-Euro menghaluskan tiap deteksi baru, dan di antara deteksi loop
  melakukan ekstrapolasi mengikuti kecepatan terakhir supaya marker tetap
  bergerak halus, bukan diam lalu melompat
- tiap sample diberi timestamp waktu capture aslinya, jadi ekstrapolasinya
  sekalian menutup sebagian besar latensi kamera ke layar

Konstanta untuk semua penyetelan ini dikumpulkan di bagian atas file lengkap
dengan catatannya.

## Log progres

Dicatat per tanggal, mengikuti commit. Entri terbaru di atas.

<!-- Tiap habis commit, tambahkan baris di bawah tanggalnya:
     `hash-pendek` — ringkasan singkat apa yang berubah.
     Kalau tanggalnya baru, buat sub-judul ### tanggal baru di paling atas. -->

### 2026-09-02

- _(belum di-commit)_ — `chart_parser.py`: parser chart teks (tap + hold + ganti
  pembagian ketukan `{n}`, not hold `Nh[div:mult]`). Di `test.py` ditambah
  `test_chart_parser()` dan flag `RUN_PARSER_TEST`, plus pesan startup dan
  peringatan kalau kamera tidak mengirim frame. Parser belum tersambung ke
  aplikasi kamera.
- `967b50a` — tambah screenshot hasil program ke `docs/screenshot.png`.
- `0b77a8d` — mulai pakai README ini sebagai catatan progres.
- `0f416d5` — fase 2: papan 34 zona (ring B/E/A/D + C1/C2 di tengah), hit scan
  yang memperlakukan telapak sebagai disc (bisa memicu 2–3 zona sekaligus),
  deteksi kepalan untuk tap, dan mekanik hold (kepalan lalu lepas, dengan
  durasi + zona awalnya). Sudah termasuk kerja sebelum commit pertama:
  pindah ke MediaPipe Tasks API karena Python 3.14 / MediaPipe 1.x menghapus
  `mp.solutions`, capture di thread terpisah, deteksi async mode LIVE_STREAM,
  smoothing One-Euro + ekstrapolasi antar-deteksi, dan kompensasi latensi
  kamera-ke-layar.

## Masalah / keterbatasan yang diketahui

- Masih ada ~30–50 ms latensi pipeline yang tidak bisa dihilangkan.
  Ekstrapolasi menutupi sebagian besar, tapi bisa kelewatan (overshoot) saat
  arah gerak berubah tajam.
- Deteksi kepalan masih heuristik 2D yang kasar. Bisa salah deteksi kalau
  tangan menghadap lurus ke kamera (jari kelihatan memendek).
- Geometri papan masih hard-coded untuk frame 1280×720 dan berpusat di tengah
  gambar. Belum ada kalibrasi ke posisi pemain yang sebenarnya.
- Akurasi gerak cepat pada akhirnya dibatasi oleh jumlah deteksi per detik yang
  sanggup dilakukan mesin. `DETECT_WIDTH` sudah diturunkan ke 384 untuk
  menambah jumlahnya.
- Output baru berupa print di console — belum ada integrasi ke game/tombol.
- Chart parser dan tracking masih terpisah. Chart juga pakai posisi `1`–`8`,
  sedangkan papan sekarang punya 34 zona — perlu pemetaan (kemungkinan `1`–`8`
  ke ring `A`).

## Selanjutnya

- Sambungkan `chart_parser` ke aplikasi kamera: jalankan waktu lagu, munculkan
  not di zona yang tepat, lalu cocokkan HIT dari tangan dengan not yang diharap
  (judge PERFECT / GOOD / MISS)
- Petakan posisi chart `1`–`8` ke id zona papan
- Ubah event HIT / RELEASE jadi input sungguhan (keypress) kalau mau dipakai ke
  game lain
- Bentuk kalibrasi papan, bukan layout tetap
- Tinjau ulang heuristik kepalan, mungkin pakai model gesture yang proper
- Pecah `test.py` jadi beberapa modul setelah bentuknya sudah mapan
