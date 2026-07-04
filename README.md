# Normal Activity Web Crawler

Generator **dataset aktivitas normal user** untuk web/app yang Anda miliki atau lab
yang **authorized**. Dibuat khusus untuk kebutuhan riset SAST / deteksi anomali: crawler
login sebagai admin, melakukan recon, mempelajari karakteristik path target, lalu
menyimulasikan akses normal (termasuk area admin) dan mekanisme upload OJS.

> Tool ini **bukan** alat serangan. Tidak ada fuzzing, brute force, exploit, atau payload
> injection. Upload hanya berupa file dummy dan hanya jika diaktifkan eksplisit. Gunakan
> hanya pada sistem yang Anda miliki atau yang telah memberi izin.

---

## Daftar isi
1. [Struktur repo](#struktur-repo)
2. [Instalasi](#instalasi)
3. [Cara cepat (quick start)](#cara-cepat-quick-start)
4. [Cara kerja: recon → simulasi](#cara-kerja-recon--simulasi)
5. [Multi-extension upload](#multi-extension-upload)
6. [Self-learning path & catatan per-target](#self-learning-path--catatan-per-target)
7. [Runner scripts](#runner-scripts)
8. [Menjalankan manual (semua opsi CLI)](#menjalankan-manual-semua-opsi-cli)
9. [Format output dataset](#format-output-dataset)
10. [Catatan metodologi](#catatan-metodologi)
11. [Batasan](#batasan)

---

## Struktur repo

```
normal_activity_crawler/
├── README.md               # dokumen ini
├── requirements.txt        # dependency Python
├── .gitignore
├── src/
│   └── normal_activity_crawler.py     # SCANNER UTAMA (satu-satunya scanner)
├── scripts/
│   ├── run_ojs_admin_upload_focus.sh  # runner utama (recon + self-learn + upload)
│   ├── run_ojs_admin_activity.sh      # runner admin tanpa bias fokus
│   └── run_ojs_normal_activity.sh     # runner publik (tanpa login)
├── datasets/               # output dataset + artefak runtime
│   ├── dataset_ojs_admin_normal.{jsonl,csv}
│   ├── dataset_ojs_admin_upload_focus.{jsonl,csv}
│   ├── .crawler_notes.json            # catatan self-learning per-target (auto)
│   └── .dummy_uploads/                # file dummy upload (auto)
├── dist/                   # rilis paket terbaru (zip)
└── archive/
    ├── smoke/              # output uji/eksplorasi lama
    └── releases/           # zip rilis versi sebelumnya
```

Scanner utama sekarang tunggal dan berada di `src/normal_activity_crawler.py`. Semua
runner memanggil file itu. Zip lama disimpan di `archive/releases/` hanya sebagai arsip.

---

## Instalasi

Butuh Python 3.10+ dan Playwright (Chromium).

```bash
cd normal_activity_crawler

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
# jika perlu dependency OS tambahan:
playwright install-deps chromium
```

Runner otomatis memakai `.venv/bin/python` bila ada; jika tidak, jatuh ke `python3`.
Anda juga bisa memaksa interpreter lewat `PYTHON_BIN=/path/to/python`.

---

## Cara cepat (quick start)

Target default sudah diisi ke lab OJS `publicknowledge` (admin/admin). Dari root repo:

```bash
./scripts/run_ojs_admin_upload_focus.sh
```

Itu akan: login admin → recon target → belajar path → simulasi 5 sesi browsing
admin/user → upload dummy (multi-ekstensi) → tulis dataset ke `datasets/`.

Target lain cukup dikirim sebagai argumen pertama:

```bash
./scripts/run_ojs_admin_upload_focus.sh "http://10.34.100.110:8031/index.php/efgh/"
```

Uji cepat (sesi & langkah sedikit):

```bash
SESSIONS=1 MAX_STEPS=6 RECON_STEPS=8 ./scripts/run_ojs_admin_upload_focus.sh
```

---

## Cara kerja: recon → simulasi

Crawler meniru alur user nyata dalam dua fase:

1. **Recon (di awal).** Login admin lalu menjelajah target secara *breadth-first* untuk
   memetakan struktur path situs (mis. `workflow/index/{id}/{id}`,
   `management/settings/{x}`, `issue/view/{id}`). Traffic recon dicatat sebagai event
   `recon` / `recon_enumerate`. Anggaran langkah diatur `--recon-steps` (default 40).
2. **Simulasi.** Setelah recon, crawler melakukan browsing acak seperti user biasa,
   tetapi prioritas fokusnya **dipelajari dari hasil recon**, bukan daftar hardcoded.
   Ada delay acak antar aksi agar menyerupai perilaku manusia.

Karena peta path dibangun dari situs live, target berbeda menghasilkan pembelajaran
berbeda — mengikuti karakteristik masing-masing (`/publicknowledge` ≠ `/efgh`).

---

## Multi-extension upload

Saat menemukan form upload, crawler **mencoba beberapa ekstensi berurutan sampai OJS
menerima salah satu, lalu berhenti**:

- Urutan default mengutamakan dokumen: `pdf, docx, txt, png, jpg, csv, html, css`.
- Ekstensi yang cocok dengan atribut `accept` form didahulukan (mis. form logo
  `accept=image` langsung mencoba `png`).
- Jika sebuah ekstensi ditolak (validasi tipe file/plupload OJS), percobaan itu dicatat
  lalu crawler lanjut ke ekstensi berikutnya.
- Ekstensi yang berhasil/gagal per-halaman disimpan ke catatan, sehingga run berikutnya
  tidak mengulang ekstensi yang sudah gagal di halaman yang sama.

Aktifkan dengan `--enable-dummy-upload` (+ `--submit-dummy-upload` agar request upload
benar-benar dikirim ke server dan tercatat di log OJS). Event upload:

| event_type | arti |
|---|---|
| `dummy_upload` | sukses diterima (`chosen_text` memuat ekstensi, mis. `[pdf]`) |
| `dummy_upload_attempt` | satu ekstensi dicoba tapi ditolak (`action=try_ext_<ext>`) |
| `dummy_upload_failed` | semua ekstensi ditolak (`error=rejected_exts=...`) |
| `dummy_upload_prepared` | file dipasang tanpa submit |
| `dummy_upload_skipped` | form tidak bisa dipakai |

---

## Self-learning path & catatan per-target

Catatan persisten disimpan di `--notes-file` (default runner: `datasets/.crawler_notes.json`),
di-*key* per host + konteks jurnal (mis. `.../publicknowledge` terpisah dari `.../efgh`).
Isi tiap target:

- `learned_paths` — template path yang dipelajari + kategori (admin/workflow/content/public).
- `upload_pages` — ekstensi yang diterima / ditolak per halaman upload.
- `visited` — hitungan kunjungan.
- `dead_or_denied` — URL bermasalah/ditolak.

Manfaat: fokus simulasi otomatis diarahkan ke path yang benar-benar ada di target, dan
crawler **tidak mengulang link/upload yang sama terlalu banyak** lintas run.

Kontrol:

- `--learn` / `--no-learn` — aktif/matikan recon + self-learning (default aktif di mode admin).
- `--recon-steps N` — anggaran langkah recon (`0` = lewati recon).
- `--reset-notes` — hapus catatan lama untuk target ini sebelum mulai.
- Jika `--focus-seed-urls` / `--focus-terms` dibiarkan kosong, keduanya diisi otomatis
  dari hasil pembelajaran; jika diisi manual, nilai manual dipakai.

---

## Runner scripts

Semua runner bisa dijalankan dari mana saja (mereka mencari root repo dari lokasi script)
dan menulis output ke `datasets/`.

### `scripts/run_ojs_admin_upload_focus.sh` — runner utama
Login admin, recon + self-learning, fokus admin/upload, multi-ekstensi upload.

```bash
./scripts/run_ojs_admin_upload_focus.sh [START_URL] [SCOPE_PREFIX] [FOCUS_SEED_URLS]
```

Env yang sering dipakai:

| Env | Default | Fungsi |
|---|---|---|
| `SESSIONS` | 5 | jumlah sesi simulasi |
| `MAX_STEPS` | 25 | langkah per sesi |
| `RECON_STEPS` | 40 | anggaran langkah recon (0 = lewati) |
| `LEARN` | 1 | 1 aktif self-learning, 0 = `--no-learn` |
| `FOCUS_PROB` | 0.8 | probabilitas memilih link admin/upload |
| `NOTES_FILE` | `datasets/.crawler_notes.json` | lokasi catatan |
| `RESET_NOTES` | 0 | 1 untuk menghapus catatan target ini |
| `ENABLE_DUMMY_UPLOAD` | 1 | aktifkan upload dummy |
| `SUBMIT_DUMMY_UPLOAD` | 1 | kirim/submit upload |
| `MAX_DUMMY_UPLOADS_PER_SESSION` | 1 | batas upload per sesi |
| `UPLOAD_SCAN_WAIT_MS` | 800 | tunggu form upload async (0 = tanpa tunggu) |
| `DUMMY_UPLOAD_DIR` | `datasets/.dummy_uploads` | folder file dummy |
| `OUT_JSONL` / `OUT_CSV` | `datasets/dataset_ojs_admin_upload_focus.*` | output |
| `OJS_USERNAME` / `OJS_PASSWORD` | admin / admin | kredensial |
| `PYTHON_BIN` | auto | interpreter Python |

Contoh:

```bash
RESET_NOTES=1 RECON_STEPS=30 ./scripts/run_ojs_admin_upload_focus.sh
SUBMIT_DUMMY_UPLOAD=0 ./scripts/run_ojs_admin_upload_focus.sh   # jangan submit upload
```

### `scripts/run_ojs_admin_activity.sh` — admin tanpa bias fokus
Sama-sama login + recon + self-learning, tetapi tanpa bias `--focus-admin-upload`.

```bash
./scripts/run_ojs_admin_activity.sh [START_URL] [SCOPE_PREFIX] [ADMIN_START_URL]
```

### `scripts/run_ojs_normal_activity.sh` — publik (tanpa login)
Browsing normal tanpa autentikasi, cocok untuk baseline user anonim.

```bash
./scripts/run_ojs_normal_activity.sh [START_URL] [SCOPE_PREFIX]
```

---

## Menjalankan manual (semua opsi CLI)

```bash
export OJS_PASSWORD='admin'

.venv/bin/python src/normal_activity_crawler.py \
  --start-url "http://10.34.100.110:8031/index.php/publicknowledge/" \
  --scope-prefix "http://10.34.100.110:8031/index.php/publicknowledge" \
  --username "admin" --password-env OJS_PASSWORD \
  --focus-admin-upload --focus-prob 0.8 \
  --learn --recon-steps 40 \
  --notes-file datasets/.crawler_notes.json \
  --enable-dummy-upload --submit-dummy-upload \
  --max-dummy-uploads-per-session 1 \
  --dummy-upload-dir datasets/.dummy_uploads \
  --enable-search --search-prob 0.1 \
  --sessions 5 --max-steps 25 \
  --delay-min 1.5 --delay-max 4.0 \
  --out-jsonl datasets/dataset_ojs_admin_upload_focus.jsonl \
  --out-csv  datasets/dataset_ojs_admin_upload_focus.csv
```

Opsi utama (lihat `--help` untuk daftar lengkap):

| Flag | Fungsi |
|---|---|
| `--start-url` | URL awal (wajib) |
| `--scope-prefix` | batasi crawl hanya pada prefix ini |
| `--username` / `--password-env` | mode login admin (password via env var) |
| `--admin-start-url` | halaman pertama setelah login |
| `--focus-admin-upload` / `--focus-prob` | bias ke area admin/upload |
| `--focus-seed-urls` / `--focus-terms` | override fokus (kosong = self-learn) |
| `--learn` / `--no-learn` / `--recon-steps` | recon + self-learning |
| `--notes-file` / `--reset-notes` | catatan persisten per-target |
| `--enable-dummy-upload` / `--submit-dummy-upload` | upload dummy multi-ekstensi |
| `--dummy-upload-file` / `--dummy-upload-dir` | file/folder dummy |
| `--enable-search` / `--search-terms` / `--search-prob` | search benign |
| `--sessions` / `--max-steps` / `--max-url-revisit` | volume & anti-pengulangan |
| `--delay-min` / `--delay-max` | jeda acak antar aksi |
| `--respect-robots` | patuhi robots.txt |
| `--seed` | seed random untuk replikasi |
| `--headful` | tampilkan browser |

---

## Format output dataset

Tiap event ditulis satu baris ke JSONL dan diringkas ke CSV.

| Kolom | Makna |
|---|---|
| timestamp_utc | waktu event |
| session_id | ID sesi user sintetis |
| step | urutan langkah dalam sesi |
| event_type | page_view, navigation, search, login, recon, recon_enumerate, dummy_upload, dummy_upload_attempt, dummy_upload_failed, blocked_by_robots |
| action | jenis aksi (mis. open_internal_link, recon_enumerate, try_ext_pdf) |
| url_before / url_after | URL sebelum & sesudah aksi |
| http_status | status HTTP |
| load_time_ms | durasi load |
| page_title | judul halaman |
| candidate_count | jumlah link internal aman |
| chosen_text / chosen_href | teks & URL yang dipilih |
| content_sha256 | hash isi halaman (bukan raw text) |
| viewport / user_agent | konteks browser |
| error | error/catatan bila ada |

---

## Catatan metodologi

Untuk reprodusibilitas, catat konfigurasi eksperimen: target URL, jumlah sesi/langkah,
delay, `--seed`, deny-regex, status robots.txt, dan versi catatan (`--notes-file`).
Gunakan `--seed 42` untuk replikasi pemilihan link yang deterministik.

Contoh replikasi:

```bash
.venv/bin/python src/normal_activity_crawler.py \
  --start-url "http://10.34.100.110:8031/index.php/publicknowledge/" \
  --scope-prefix "http://10.34.100.110:8031/index.php/publicknowledge" \
  --username admin --password-env OJS_PASSWORD \
  --seed 42 --sessions 30 --max-steps 25 \
  --out-jsonl datasets/run_seed42.jsonl --out-csv datasets/run_seed42.csv
```

---

## Batasan

- Login otomatis hanya untuk form login sederhana/OJS-like (username + password).
- Crawler tidak mengisi form kompleks selain login dan search benign.
- Upload hanya file dummy, hanya saat `--enable-dummy-upload` (submit perlu `--submit-dummy-upload`).
- Mode publik menghindari URL sensitif pada deny-regex default (admin, delete, edit, token, dst.).
- Mode admin membolehkan area admin, tetapi tetap menghindari aksi state-changing
  (delete, edit, submit, export, logout, setLocale, dll.) agar dataset tetap "normal".
