import os
import sys
import sqlite3
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()

USE_PG = bool(os.environ.get('DATABASE_URL'))
DESTRUCTIVE = '--destructive' in sys.argv

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'mulazamah2026')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS peserta (
    id SERIAL PRIMARY KEY,
    nama_lengkap TEXT NOT NULL,
    gender TEXT NOT NULL CHECK(gender IN ('ikhwan', 'akhwat')),
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS sesi_presensi (
    id SERIAL PRIMARY KEY,
    gender TEXT NOT NULL CHECK(gender IN ('ikhwan', 'akhwat')),
    tanggal DATE NOT NULL,
    judul_acara TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS kehadiran (
    id SERIAL PRIMARY KEY,
    sesi_id INTEGER REFERENCES sesi_presensi(id) ON DELETE CASCADE,
    peserta_id INTEGER REFERENCES peserta(id) ON DELETE CASCADE,
    status_akhir TEXT NOT NULL CHECK(status_akhir IN ('hadir_offline', 'hadir_online', 'izin_disetujui', 'alpha')),
    izin_menunggu BOOLEAN DEFAULT FALSE,
    alasan_izin TEXT,
    submitted_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(sesi_id, peserta_id)
);
"""

DROP_TABLES = [
    "DROP TABLE IF EXISTS kehadiran CASCADE",
    "DROP TABLE IF EXISTS sesi_presensi CASCADE",
    "DROP TABLE IF EXISTS peserta CASCADE",
    "DROP TABLE IF EXISTS users CASCADE",
]

CREATE_TABLES = [
    "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS peserta (id SERIAL PRIMARY KEY, nama_lengkap TEXT NOT NULL, gender TEXT NOT NULL CHECK(gender IN ('ikhwan', 'akhwat')), no_hp TEXT, is_active BOOLEAN DEFAULT TRUE)",
    "CREATE TABLE IF NOT EXISTS sesi_presensi (id SERIAL PRIMARY KEY, gender TEXT NOT NULL CHECK(gender IN ('ikhwan', 'akhwat')), tanggal DATE NOT NULL, judul_acara TEXT NOT NULL, token TEXT UNIQUE NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW())",
    "CREATE TABLE IF NOT EXISTS kehadiran (id SERIAL PRIMARY KEY, sesi_id INTEGER REFERENCES sesi_presensi(id) ON DELETE CASCADE, peserta_id INTEGER REFERENCES peserta(id) ON DELETE CASCADE, status_akhir TEXT NOT NULL CHECK(status_akhir IN ('hadir_offline', 'hadir_online', 'izin_disetujui', 'alpha')), izin_menunggu BOOLEAN DEFAULT FALSE, alasan_izin TEXT, no_hp_terinput TEXT, submitted_at TIMESTAMP DEFAULT NOW(), UNIQUE(sesi_id, peserta_id))",
]

SAMPLE_PESERTA = [
    ("Ahmad Fauzi", "ikhwan", "081234567890", True),
    ("Budi Santoso", "ikhwan", "081234567891", True),
    ("Siti Aisyah", "akhwat", "081234567892", True),
    ("Fatimah Zahra", "akhwat", "081234567893", True),
]


def init_sqlite():
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'absensi.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    if DESTRUCTIVE:
        cur.executescript("DROP TABLE IF EXISTS kehadiran; DROP TABLE IF EXISTS sesi_presensi; DROP TABLE IF EXISTS peserta; DROP TABLE IF EXISTS users;")

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS peserta (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nama_lengkap TEXT NOT NULL,
        gender TEXT NOT NULL CHECK(gender IN ('ikhwan', 'akhwat')),
        no_hp TEXT,
        is_active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS sesi_presensi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gender TEXT NOT NULL CHECK(gender IN ('ikhwan', 'akhwat')),
        tanggal TEXT NOT NULL,
        judul_acara TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS kehadiran (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sesi_id INTEGER NOT NULL,
        peserta_id INTEGER NOT NULL,
        status_akhir TEXT NOT NULL CHECK(status_akhir IN ('hadir_offline', 'hadir_online', 'izin_disetujui', 'alpha')),
        izin_menunggu INTEGER NOT NULL DEFAULT 0,
        alasan_izin TEXT,
        no_hp_terinput TEXT,
        submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (sesi_id) REFERENCES sesi_presensi(id) ON DELETE CASCADE,
        FOREIGN KEY (peserta_id) REFERENCES peserta(id) ON DELETE CASCADE,
        UNIQUE(sesi_id, peserta_id)
    );
    """)

    cur.execute("DELETE FROM users WHERE username = ?", (ADMIN_USERNAME,))
    cur.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)))

    for nama, gender, no_hp, active in SAMPLE_PESERTA:
        try:
            cur.execute("INSERT INTO peserta (nama_lengkap, gender, no_hp, is_active) VALUES (?, ?, ?, ?)",
                        (nama, gender, no_hp, 1 if active else 0))
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()
    print(f"✅ SQLite database initialized at {db_path}")


def init_postgres():
    import psycopg2

    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    if DESTRUCTIVE:
        for stmt in DROP_TABLES:
            cur.execute(stmt)
        conn.commit()

    for stmt in CREATE_TABLES:
        cur.execute(stmt)
    conn.commit()

    cur.execute("DELETE FROM users WHERE username = %s", (ADMIN_USERNAME,))
    cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD)))

    for nama, gender, no_hp, active in SAMPLE_PESERTA:
        cur.execute(
            "INSERT INTO peserta (nama_lengkap, gender, no_hp, is_active) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (nama, gender, no_hp, active)
        )

    conn.commit()
    conn.close()
    print(f"✅ PostgreSQL database initialized ({'destructive' if DESTRUCTIVE else 'idempotent'} mode)")


if __name__ == '__main__':
    mode = "DESTRUCTIVE" if DESTRUCTIVE else "IDEMPOTENT"
    db_type = "PostgreSQL" if USE_PG else "SQLite"
    print(f"🔧 Initializing {db_type} database in {mode} mode...")

    if USE_PG:
        init_postgres()
    else:
        init_sqlite()

    print(f"\n📋 Admin credentials:")
    print(f"   Username: {ADMIN_USERNAME}")
    print(f"   Password: {ADMIN_PASSWORD}")
    print(f"\n💡 Use --destructive flag to drop and recreate all tables")
