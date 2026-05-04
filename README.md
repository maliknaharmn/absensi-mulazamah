# Absensi Mulazamah

Website absensi untuk peserta Mulazamah dengan sistem generate link tanpa peserta login, sehingga memudahkan admin dan peserta untuk mengisi absensi kehadiran, dengan pemisahan link ikhwan & akhwat,database admin berisi rekap kehaidran peserta, serta fitur-fitur lainnya untuk efisiensi dan efektivitas sesuai kebutuhan user. Dibangun menggunakan **Python Flask** dan **PostgreSQL** (dengan fallback SQLite untuk development lokal).

---

## 📋 Deskripsi Singkat

Sistem ini memungkinkan pengurus (admin) untuk:
1. **Generate link presensi** — satu link khusus ikhwan, satu khusus akhwat, per sesi
2. **Peserta isi absensi** — tanpa login, cukup buka link, ketik nama (autocomplete), pilih status kehadiran
3. **Kelola kehadiran** — admin bisa edit status, setujui/tolak izin
4. **Lihat rekap** — filter berdasarkan tanggal dan gender, export ke CSV

---

## ✨ Fitur Utama

| Fitur | Keterangan |
|---|---|
| **Login Admin** | Username & password, session-based |
| **Generate Link** | Pilih tanggal & judul acara, token unik otomatis |
| **Autocomplete Nama** | Hanya tampilkan nama sesuai gender link |
| **Cegah Double Submit** | Satu peserta hanya bisa isi 1x per sesi |
| **Edit Status** | Admin bisa ubah status kehadiran manual |
| **Approve Izin** | Izin dari peserta masuk status "menunggu", admin setujui/tolak |
| **Master Peserta** | CRUD peserta (tambah, edit, hapus, aktif/nonaktif) |
| **Rekap & Export** | Filter rentang tanggal + gender, download CSV |
| **No Backdate** | Tidak bisa buat presensi untuk tanggal kemarin |
| **Close & Delete Sesi** | Tutup sesi (nonaktifkan link) atau hapus permanen |
| **Dual Database** | PostgreSQL untuk production, SQLite untuk development |

---

## 🏗️ Tech Stack

| Komponen | Teknologi |
|---|---|
| Backend | Python Flask |
| Database | PostgreSQL (prod) / SQLite (dev) |
| Frontend | HTML + Tailwind CSS (CDN) |
| Autocomplete | HTML5 `<datalist>` + AJAX |
| Token | `secrets.token_urlsafe(8)` |
| Password Hash | `werkzeug.security` |
| Server | Gunicorn (production) |

---

## 📁 Struktur Folder

```
absensi-mulazamah/
├── app.py                    # Flask utama (semua routes & logic)
├── init_db.py                # Script inisialisasi database
├── migrate_to_postgres.py    # Script migrasi SQLite → PostgreSQL
├── requirements.txt          # Dependencies
├── Procfile                  # Railway deployment config
├── .gitignore
├── .env.example              # Template environment variables
├── README.md                 # File ini
├── templates/
│   ├── base.html             # Template dasar (navbar, flash messages)
│   ├── login.html            # Halaman login admin
│   ├── dashboard.html        # Dashboard: generate link, tabel kehadiran, link presensi
│   ├── presensi.html         # Form absensi peserta (public)
│   ├── peserta.html          # Master data peserta (admin)
│   └── rekap.html            # Rekap laporan + export CSV
└── instance/
    └── absensi.db            # Database SQLite (development only)
```

---

## 🗄️ Database

### Tabel: `users`
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | SERIAL | Primary key |
| username | TEXT | Unique |
| password_hash | TEXT | Hash dengan werkzeug |

### Tabel: `peserta`
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | SERIAL | Primary key |
| nama_lengkap | TEXT | Nama peserta |
| gender | TEXT | `ikhwan` atau `akhwat` |
| is_active | BOOLEAN | TRUE = aktif, FALSE = nonaktif |

### Tabel: `sesi_presensi`
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | SERIAL | Primary key |
| gender | TEXT | `ikhwan` atau `akhwat` |
| tanggal | DATE | Tanggal sesi |
| judul_acara | TEXT | Judul sesi |
| token | TEXT | Unik, URL-safe |
| is_active | BOOLEAN | TRUE = aktif, FALSE = ditutup |
| created_at | TIMESTAMP | Waktu pembuatan |

### Tabel: `kehadiran`
| Kolom | Tipe | Keterangan |
|---|---|---|
| id | SERIAL | Primary key |
| sesi_id | INTEGER | FK ke `sesi_presensi` (CASCADE delete) |
| peserta_id | INTEGER | FK ke `peserta` (CASCADE delete) |
| status_akhir | TEXT | `hadir_offline`, `hadir_online`, `izin_disetujui`, `alpha` |
| izin_menunggu | BOOLEAN | TRUE = menunggu persetujuan admin |
| alasan_izin | TEXT | Keterangan izin (opsional) |
| submitted_at | TIMESTAMP | Waktu submit |
| UNIQUE | `(sesi_id, peserta_id)` | Cegah double submit |

---

## 🚀 Cara Menjalankan (Development)

### 1. Install Dependencies
```bash
cd absensi-mulazamah
pip3 install -r requirements.txt
```

### 2. Inisialisasi Database (SQLite)
```bash
python3 init_db.py --destructive
```

### 3. Jalankan Aplikasi
```bash
python3 app.py
```

### 4. Buka di Browser
```
http://127.0.0.1:5001
```

**Login:** `admin` / `mulazamah2026`

---

## 🐘 Menggunakan PostgreSQL Lokal

### 1. Install PostgreSQL
```bash
brew install postgresql
brew services start postgresql
createdb absensi_dev
```

### 2. Set Environment Variable
```bash
export DATABASE_URL="postgresql://localhost/absensi_dev"
```

### 3. Inisialisasi Database
```bash
python3 init_db.py --destructive
```

### 4. Jalankan Aplikasi
```bash
python3 app.py
```

---

## 🔄 Migrasi SQLite → PostgreSQL

Jika sudah ada data di SQLite dan ingin pindah ke PostgreSQL:

```bash
# Set PostgreSQL connection string
export DATABASE_URL="postgresql://localhost/absensi_prod"
createdb absensi_prod

# Jalankan migrasi
python3 migrate_to_postgres.py
```

---

## 🌐 Deploy ke Railway

### 1. Push ke GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Setup Railway
1. Buka [Railway.app](https://railway.app) → Login
2. **New Project** → **Import from GitHub** → Pilih repository
3. **Add PostgreSQL** service ke project
4. Railway otomatis set `DATABASE_URL`

### 3. Set Environment Variables
Di Railway dashboard → Variables:
```
SECRET_KEY=<generate random string: python3 -c "import secrets; print(secrets.token_hex(24))">
ADMIN_PASSWORD=<password pilihanmu>
```

### 4. Inisialisasi Database
Di Railway → Klik PostgreSQL service → **Data** tab → **Connect**:
```bash
# Atau via Railway console:
python3 init_db.py
```

### 5. Deploy
Railway auto-deploy saat ada push ke GitHub. Domain: `xxx.up.railway.app`

### 6. Health Check
```
https://xxx.up.railway.app/health
```

---

## 🔒 Environment Variables

| Variable | Development | Production (Railway) |
|---|---|---|
| `DATABASE_URL` | Kosong (pakai SQLite) | Auto-set oleh Railway |
| `SECRET_KEY` | `dev-secret-key-change-me` | Random hex string |
| `ADMIN_PASSWORD` | `mulazamah2026` | Custom password |

---

## 📖 Alur Penggunaan

### Admin Generate Link
1. Login → Dashboard
2. Isi tanggal & judul acara di form "Generate Link Ikhwan" atau "Generate Link Akhwat"
3. Link muncul di tabel "Daftar Link Presensi"
4. Klik "Salin" untuk copy link, bagikan ke peserta

### Peserta Isi Absensi
1. Buka link yang dibagikan admin
2. Ketik nama → muncul saran dari daftar peserta (hanya sesuai gender link)
3. Pilih status: Hadir Offline / Hadir Online / Izin
4. Jika pilih Izin, isi alasan
5. Klik "Kirim Presensi"

### Admin Kelola Kehadiran
1. Dashboard → lihat tabel kehadiran per sesi
2. Klik **Edit** untuk ubah status peserta
3. Klik **Setujui** atau **Tolak** untuk izin yang menunggu
4. Klik **Tutup Sesi** untuk menonaktifkan link presensi
5. Setelah ditutup, klik **Hapus Sesi** untuk hapus permanen

### Admin Lihat Rekap
1. Menu **Rekap** → filter rentang tanggal & gender
2. Lihat tabel: Nama, Total Hadir, Total Izin, Total Alpha, % Hadir
3. Klik **Download CSV** untuk export

---

## 🔑 API Endpoints

| Method | Endpoint | Deskripsi |
|---|---|---|
| POST | `/login` | Login admin |
| GET | `/logout` | Logout |
| GET | `/dashboard` | Dashboard admin |
| POST | `/api/generate-sesi` | Generate link presensi |
| POST | `/api/close-sesi` | Tutup sesi (nonaktifkan) |
| POST | `/api/delete-sesi` | Hapus sesi permanen (CASCADE) |
| POST | `/api/approve-izin` | Setujui/tolak izin |
| POST | `/api/edit-kehadiran` | Edit status kehadiran |
| POST | `/api/peserta` | CRUD peserta |
| GET | `/api/search-peserta` | Autocomplete nama |
| GET | `/presensi/<gender>/<token>` | Form presensi peserta |
| POST | `/presensi/submit` | Submit kehadiran |
| GET | `/rekap` | Rekap laporan |
| GET | `/rekap/export` | Export CSV |
| GET | `/health` | Health check |

---

## 🔒 Aturan Keamanan

| Aturan | Implementasi |
|---|---|
| Token unik | `secrets.token_urlsafe(8)` — ~11 karakter acak |
| Cegah double submit | UNIQUE constraint `(sesi_id, peserta_id)` + cek server-side |
| Password hashing | `werkzeug.security.generate_password_hash` |
| Session auth | Flask session untuk admin |
| Validasi tanggal | Tidak bisa generate untuk tanggal kemarin |
| Validasi gender | Autocomplete hanya return nama sesuai gender link |
| Nama harus ada | Submit ditolak jika nama tidak ada di master data |
| Query parameter | Auto `?` → `%s` conversion untuk PostgreSQL |

---

## 🛠️ Troubleshooting

### Database tidak ditemukan
```bash
python3 init_db.py --destructive
```

### Port 5001 sudah dipakai
Edit `app.py`, ganti `port=5001` ke port lain.

### Reset database SQLite
```bash
rm instance/absensi.db
python3 init_db.py --destructive
```

### PostgreSQL tidak jalan
```bash
brew services start postgresql
```

### Migration gagal
Pastikan `DATABASE_URL` sudah set dan database PostgreSQL sudah dibuat:
```bash
export DATABASE_URL="postgresql://localhost/absensi_dev"
createdb absensi_dev
python3 migrate_to_postgres.py
```

---

## 📝 Catatan

- **Port 5001** (bukan 5000) karena macOS menggunakan port 5000 untuk AirTunes
- **Tidak ada fitur login untuk peserta** — peserta langsung isi via link
- **Tidak ada notifikasi otomatis** — admin harus share link manual (WA/Telegram)
- **Dual database** — SQLite untuk development, PostgreSQL untuk production
- **Query wrapper** — otomatis konversi `?` → `%s` untuk PostgreSQL compatibility
