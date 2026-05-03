import sqlite3
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def migrate():
    sqlite_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'absensi.db')

    if not os.path.exists(sqlite_path):
        print("❌ SQLite database not found. Run with SQLite first to generate data.")
        return

    if not os.environ.get('DATABASE_URL'):
        print("❌ DATABASE_URL not set. Set it before running migration.")
        return

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg2.connect(os.environ['DATABASE_URL'])
    pg_cur = pg_conn.cursor()

    users = sqlite_conn.execute("SELECT * FROM users").fetchall()
    for u in users:
        pg_cur.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (u['id'], u['username'], u['password_hash'])
        )

    peserta = sqlite_conn.execute("SELECT * FROM peserta").fetchall()
    for p in peserta:
        pg_cur.execute(
            "INSERT INTO peserta (id, nama_lengkap, gender, no_hp, is_active) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (p['id'], p['nama_lengkap'], p['gender'], p['no_hp'], bool(p['is_active']))
        )

    sesi = sqlite_conn.execute("SELECT * FROM sesi_presensi").fetchall()
    for s in sesi:
        pg_cur.execute(
            "INSERT INTO sesi_presensi (id, gender, tanggal, judul_acara, token, is_active) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (s['id'], s['gender'], s['tanggal'], s['judul_acara'], s['token'], bool(s['is_active']))
        )

    kehadiran = sqlite_conn.execute("SELECT * FROM kehadiran").fetchall()
    for k in kehadiran:
        pg_cur.execute(
            "INSERT INTO kehadiran (id, sesi_id, peserta_id, status_akhir, izin_menunggu, alasan_izin, no_hp_terinput) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (k['id'], k['sesi_id'], k['peserta_id'], k['status_akhir'], bool(k['izin_menunggu']), k['alasan_izin'], k.get('no_hp_terinput'))
        )

    pg_conn.commit()

    print("✅ Migration complete!")
    print(f"   Users: {len(users)}")
    print(f"   Peserta: {len(peserta)}")
    print(f"   Sesi: {len(sesi)}")
    print(f"   Kehadiran: {len(kehadiran)}")

    sqlite_conn.close()
    pg_conn.close()

if __name__ == '__main__':
    migrate()
