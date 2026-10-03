import json
import io
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from werkzeug.security import generate_password_hash

import app as module
import init_db
from migrate_admin_settings import migrate as migrate_admin_settings
from migrate_mvp import migrate


class MvpFlowTests(unittest.TestCase):
    @staticmethod
    def future_local(minutes=60):
        return (datetime.now(ZoneInfo('Asia/Jakarta')) + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = module.DB_PATH
        module.DB_PATH = os.path.join(self.temp.name, 'test.db')
        conn = sqlite3.connect(module.DB_PATH)
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT);
            CREATE TABLE peserta (id INTEGER PRIMARY KEY, nama_lengkap TEXT, gender TEXT,
                no_hp TEXT, is_active BOOLEAN DEFAULT TRUE);
            CREATE TABLE sesi_presensi (id INTEGER PRIMARY KEY, gender TEXT, tanggal TEXT,
                judul_acara TEXT, token TEXT, is_active BOOLEAN DEFAULT TRUE);
            CREATE TABLE kehadiran (id INTEGER PRIMARY KEY, sesi_id INTEGER, peserta_id INTEGER,
                status_akhir TEXT, izin_menunggu BOOLEAN, alasan_izin TEXT, no_hp_terinput TEXT);
        """)
        conn.execute("INSERT INTO users VALUES (1, 'admin', ?)", (generate_password_hash('password123'),))
        conn.execute("INSERT INTO peserta VALUES (1, 'Ahmad Fauzi', 'ikhwan', '081111', 1)")
        conn.execute("INSERT INTO peserta VALUES (2, 'ahmad fauzi', 'ikhwan', '082222', 1)")
        conn.execute("INSERT INTO peserta VALUES (3, 'Siti Aminah', 'akhwat', NULL, 1)")
        conn.execute("INSERT INTO peserta VALUES (4, 'Fatimah Zahra', 'akhwat', '084444', 1)")
        conn.execute(
            "INSERT INTO sesi_presensi VALUES (1, 'ikhwan', ?, 'Judul lama', 'oldtoken', 1)",
            (datetime.now().date().isoformat(),)
        )
        conn.commit()
        migrate(conn, False)
        migrate(conn, False)
        migrate_admin_settings(conn, False)
        migrate_admin_settings(conn, False)
        conn.close()
        module.app.config['TESTING'] = True
        self.client = module.app.test_client()

    def tearDown(self):
        module.DB_PATH = self.old_path
        self.temp.cleanup()

    def sql(self, query, params=()):
        conn = sqlite3.connect(module.DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(query, params).fetchone()
        conn.close()
        return row

    def login(self):
        return self.client.post('/login', data={'username': 'admin', 'password': 'password123'})

    def test_login_preserves_username_but_not_password(self):
        page = self.client.post('/login', data={'username': 'admin', 'password': 'wrong'})
        self.assertIn(b'value="admin"', page.data)
        self.assertNotIn(b'value="wrong"', page.data)
        wrong_username = self.client.post('/login', data={
            'username': 'tidakada', 'password': 'password123'
        })
        self.assertNotIn(b'value="tidakada"', wrong_username.data)

    def test_settings_change_password_revokes_all_sessions(self):
        self.assertEqual(self.client.get('/settings').status_code, 303)
        self.assertEqual(self.client.post('/settings', data={}).status_code, 303)
        self.login()
        other = module.app.test_client()
        other.post('/login', data={'username': 'admin', 'password': 'password123'})
        page = self.client.get('/settings')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Settings', page.data)
        self.assertIn(b'Password saat ini', page.data)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        form = {
            'csrf_token': token,
            'current_password': 'password123',
            'new_password': 'passwordbaru123',
            'confirm_password': 'passwordbaru123'
        }

        self.assertEqual(self.client.post('/settings', data={**form, 'csrf_token': 'invalid'}).status_code, 400)
        wrong_current = self.client.post('/settings', data={**form, 'current_password': 'wrong-secret'})
        self.assertEqual(wrong_current.status_code, 422)
        self.assertNotIn(b'wrong-secret', wrong_current.data)
        self.assertNotIn(b'passwordbaru123', wrong_current.data)
        mismatch = self.client.post('/settings', data={**form, 'confirm_password': 'tidak-cocok'})
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(self.client.post('/settings', data={**form, 'new_password': 'short'}).status_code, 422)
        self.assertEqual(self.client.post('/settings', data={
            **form, 'new_password': 'password123', 'confirm_password': 'password123'
        }).status_code, 422)
        self.assertEqual(self.sql('SELECT session_version FROM users WHERE id = 1')['session_version'], 0)

        success = self.client.post('/settings', data=form)
        self.assertEqual(success.status_code, 303)
        self.assertIn('/login', success.headers['Location'])
        self.assertEqual(self.sql('SELECT session_version FROM users WHERE id = 1')['session_version'], 1)
        self.assertEqual(self.client.get('/dashboard').status_code, 303)
        self.assertEqual(other.get('/tugas-admin').status_code, 303)
        self.assertEqual(other.get('/settings').status_code, 303)
        old_login = self.client.post('/login', data={'username': 'admin', 'password': 'password123'})
        self.assertEqual(old_login.status_code, 200)
        new_login = self.client.post('/login', data={'username': 'admin', 'password': 'passwordbaru123'})
        self.assertEqual(new_login.status_code, 303)
        self.assertEqual(self.client.get('/settings').status_code, 200)

    def test_legacy_session_and_initializer_preserves_admin_password(self):
        with self.client.session_transaction() as browser_session:
            browser_session['admin_id'] = 1
            browser_session['admin_username'] = 'admin'
        self.assertEqual(self.client.get('/dashboard').status_code, 303)

        db_path = os.path.join(self.temp.name, 'init-test.db')
        with redirect_stdout(io.StringIO()):
            init_db.init_sqlite(db_path)
        conn = sqlite3.connect(db_path)
        custom_hash = generate_password_hash('password-khusus-aman')
        conn.execute("UPDATE users SET password_hash = ?, session_version = 3 WHERE username = 'admin'", (custom_hash,))
        original_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        conn.commit()
        conn.close()
        output = io.StringIO()
        with redirect_stdout(output):
            init_db.init_sqlite(db_path)
        self.assertNotIn('password-khusus-aman', output.getvalue())
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT id, password_hash, session_version FROM users WHERE username = 'admin'").fetchone()
        participant_count = conn.execute("SELECT COUNT(*) FROM peserta").fetchone()[0]
        conn.close()
        self.assertEqual(row, (original_id, custom_hash, 3))
        self.assertEqual(participant_count, len(init_db.SAMPLE_PESERTA))

    def test_kitab_and_presensi_case_insensitive_and_retention(self):
        self.login()
        self.client.post('/api/kitab', data={'action': 'add', 'judul': 'Fiqh Shalat'})
        kitab = self.sql("SELECT * FROM kitab WHERE judul = 'Fiqh Shalat'")
        page = self.client.post('/api/generate-sesi', data={
            'gender': 'ikhwan', 'tanggal': datetime.now().date().isoformat(),
            'judul': 'Pertemuan Pertama', 'kitab_id': str(kitab['id'])
        })
        self.assertEqual(page.status_code, 303)
        row = self.sql("SELECT * FROM sesi_presensi WHERE id != 1")
        token = row['token']
        self.assertIn(b'Mulazamah Kitab', self.client.get('/dashboard').data)
        self.assertIn(b'Judul lama', self.client.get('/rekap').data)
        self.assertIn(b'Ahmad Fauzi', self.client.get('/api/search-peserta?q=ahmad&gender=ikhwan').data)
        wrong_hp = self.client.post('/presensi/submit', data={
            'token': token, 'nama': 'ahmad fauzi', 'no_hp': '089999',
            'status': 'izin', 'alasan_izin': 'Sakit'
        })
        self.assertEqual(wrong_hp.status_code, 422)
        self.assertIn(b'value="ahmad fauzi"', wrong_hp.data)
        self.assertNotIn(b'value="089999"', wrong_hp.data)
        self.assertIn(b'Sakit', wrong_hp.data)
        wrong_name = self.client.post('/presensi/submit', data={
            'token': token, 'nama': 'Tidak Ada', 'no_hp': '081111',
            'status': 'hadir_online'
        })
        self.assertNotIn(b'value="Tidak Ada"', wrong_name.data)
        self.assertIn(b'value="081111"', wrong_name.data)
        success = self.client.post('/presensi/submit', data={
            'token': token, 'nama': 'AHMAD FAUZI', 'no_hp': '082222',
            'status': 'hadir_online'
        })
        self.assertEqual(success.status_code, 200)
        self.assertIn(b'Presensi Tercatat', success.data)
        self.assertEqual(self.sql("SELECT peserta_id FROM kehadiran")['peserta_id'], 2)

    def test_task_timer_autosave_expiry_and_results(self):
        self.login()
        self.assertEqual(self.client.get('/tugas-admin').status_code, 200)
        self.client.post('/api/kitab', data={'action': 'add', 'judul': 'Fiqh'})
        kitab = self.sql("SELECT id FROM kitab WHERE judul = 'Fiqh'")
        questions = [
            {'type': 'mcq', 'text': 'Pilih jawaban benar', 'options': ['A', 'B', 'C', 'D'], 'correct': 1},
            {'type': 'essay', 'text': 'Jelaskan jawaban Anda'}
        ]
        created = self.client.post('/api/tugas', data={
            'kitab_id': str(kitab['id']), 'judul': 'Latihan Pertama',
            'durasi_menit': '1', 'questions_json': json.dumps(questions),
            'closes_at': self.future_local()
        })
        self.assertEqual(created.status_code, 303)
        task = self.sql("SELECT * FROM tugas")
        token = task['token']
        self.assertEqual(self.client.get(f'/tugas/{token}').status_code, 200)
        started = self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'AHMAD FAUZI', 'no_hp': '081111'
        })
        self.assertEqual(started.status_code, 303)
        attempt = self.sql("SELECT * FROM percobaan_tugas")
        self.assertEqual(attempt['peserta_id'], 1)
        base = f'/tugas/{token}/kerjakan/{attempt["attempt_token"]}'
        self.assertEqual(self.client.get(base).status_code, 200)
        saved = self.client.post(base + '/autosave', json={
            'answers': {'0': '1', '1': 'Penjelasan tersimpan'}
        })
        self.assertEqual(saved.json, {'saved': True})
        conn = sqlite3.connect(module.DB_PATH)
        old = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec='seconds')
        conn.execute("UPDATE percobaan_tugas SET deadline_at = ?", (old,))
        conn.commit()
        conn.close()
        done = self.client.get(base)
        self.assertIn(b'Jawaban terkirim', done.data)
        self.assertIn(b'1/1', done.data)
        self.assertEqual(self.client.post(base + '/autosave', json={
            'answers': {'0': '0'}
        }).status_code, 409)
        results = self.client.get(f'/tugas-admin/{task["id"]}')
        self.assertIn(b'Penjelasan tersimpan', results.data)
        self.assertIn(b'1/1', results.data)
        reviewed = self.client.post(
            f'/api/tugas/{task["id"]}/review/{attempt["id"]}'
        )
        self.assertEqual(reviewed.status_code, 303)
        self.assertIsNotNone(self.sql(
            "SELECT reviewed_at FROM percobaan_tugas WHERE id = ?", (attempt['id'],)
        )['reviewed_at'])

    def test_manual_submission_and_closed_task(self):
        self.login()
        self.client.post('/api/kitab', data={'action': 'add', 'judul': 'Aqidah'})
        kitab = self.sql("SELECT id FROM kitab WHERE judul = 'Aqidah'")
        self.client.post('/api/tugas', data={
            'kitab_id': str(kitab['id']), 'judul': 'Latihan Kedua',
            'durasi_menit': '15',
            'closes_at': self.future_local(),
            'questions_json': json.dumps([
                {'type': 'mcq', 'text': 'Pilih yang benar',
                 'options': ['A', 'B', 'C', 'D'], 'correct': 2}
            ])
        })
        task = self.sql("SELECT * FROM tugas")
        token = task['token']
        missing_hp = self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Siti Aminah', 'no_hp': '083333'
        })
        self.assertEqual(missing_hp.status_code, 422)
        self.assertIn(b'Hubungi admin', missing_hp.data)
        self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Ahmad Fauzi', 'no_hp': '081111'
        })
        attempt = self.sql("SELECT * FROM percobaan_tugas")
        base = f'/tugas/{token}/kerjakan/{attempt["attempt_token"]}'
        submitted = self.client.post(base + '/kirim', data={'answer_0': '2'})
        self.assertEqual(submitted.status_code, 303)
        self.assertIn(b'1/1', self.client.get(base).data)
        self.client.post(f'/api/tugas/{task["id"]}/access', data={
            'action': 'close', 'scope': 'all'
        })
        other = self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'ahmad fauzi', 'no_hp': '082222'
        })
        self.assertEqual(other.status_code, 403)
        self.assertEqual(self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Ahmad Fauzi', 'no_hp': '081111'
        }).status_code, 303)
        cannot_delete = self.client.post('/api/peserta', data={
            'action': 'delete', 'id': '1'
        }, follow_redirects=True)
        self.assertIn(b'riwayat tugas', cannot_delete.data)
        self.assertIsNotNone(self.sql('SELECT id FROM peserta WHERE id = 1'))

    def test_deadline_selected_access_and_no_retake(self):
        self.login()
        self.client.post('/api/kitab', data={'action': 'add', 'judul': 'Hadits'})
        kitab = self.sql("SELECT id FROM kitab WHERE judul = 'Hadits'")
        self.client.post('/api/tugas', data={
            'kitab_id': str(kitab['id']), 'judul': 'Tugas Hadits',
            'durasi_menit': '30', 'closes_at': self.future_local(2),
            'questions_json': json.dumps([{
                'type': 'essay', 'text': 'Jelaskan pokok bahasan'
            }])
        })
        task = self.sql('SELECT * FROM tugas')
        token = task['token']
        self.assertIn(b'Batas pengerjaan umum', self.client.get(f'/tugas/{token}').data)
        self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Ahmad Fauzi', 'no_hp': '081111'
        })
        attempt = self.sql('SELECT * FROM percobaan_tugas WHERE peserta_id = 1')
        self.assertEqual(attempt['deadline_at'], task['closes_at'])
        base = f'/tugas/{token}/kerjakan/{attempt["attempt_token"]}'
        self.client.post(base + '/autosave', json={'answers': {'0': 'Jawaban tersimpan'}})

        closed = self.client.post(f'/api/tugas/{task["id"]}/access', data={
            'action': 'close', 'scope': 'all'
        })
        self.assertEqual(closed.status_code, 303)
        self.assertIsNotNone(self.sql('SELECT submitted_at FROM percobaan_tugas WHERE id = ?', (attempt['id'],))['submitted_at'])
        self.assertFalse(self.client.get(base + '/status').json['active'])
        self.assertIn(b'Jawaban terkirim', self.client.get(base).data)
        self.assertEqual(self.client.post(base + '/autosave', json={'answers': {'0': 'Terlambat'}}).status_code, 409)

        # Buka khusus peserta yang belum mulai; peserta lama tetap tidak bisa mengulang.
        reopened = self.client.post(f'/api/tugas/{task["id"]}/access', data={
            'action': 'open', 'scope': 'selected', 'closes_at': self.future_local(),
            'participant_ids': ['1', '2']
        })
        self.assertEqual(reopened.status_code, 303)
        self.assertIsNone(self.sql('SELECT * FROM tugas_akses WHERE peserta_id = 1'))
        self.assertEqual(self.sql('SELECT allowed FROM tugas_akses WHERE peserta_id = 2')['allowed'], 1)
        self.assertEqual(self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Ahmad Fauzi', 'no_hp': '081111'
        }).status_code, 303)
        self.assertEqual(self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Siti Aminah', 'no_hp': '083333'
        }).status_code, 422)
        self.assertEqual(self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'ahmad fauzi', 'no_hp': '082222'
        }).status_code, 303)
        self.assertEqual(self.sql('SELECT COUNT(*) AS n FROM percobaan_tugas')['n'], 2)
        second = self.sql('SELECT * FROM percobaan_tugas WHERE peserta_id = 2')
        self.assertTrue(self.client.get(
            f'/tugas/{token}/kerjakan/{second["attempt_token"]}/status'
        ).json['active'])
        self.client.post(f'/api/tugas/{task["id"]}/access', data={
            'action': 'close', 'scope': 'selected', 'participant_ids': ['2']
        })
        self.assertFalse(self.client.get(
            f'/tugas/{token}/kerjakan/{second["attempt_token"]}/status'
        ).json['active'])
        self.assertIsNotNone(self.sql(
            'SELECT submitted_at FROM percobaan_tugas WHERE id = ?', (second['id'],)
        )['submitted_at'])

        # Tenggat kalender yang lewat menutup link bagi peserta baru.
        conn = sqlite3.connect(module.DB_PATH)
        old = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec='seconds')
        conn.execute('UPDATE tugas SET is_active = 1, closes_at = ?', (old,))
        conn.execute('DELETE FROM tugas_akses')
        conn.commit()
        conn.close()
        page = self.client.get(f'/tugas/{token}')
        self.assertIn(b'melewati batas waktu pengerjaan', page.data)
        denied = self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Fatimah Zahra', 'no_hp': '084444'
        })
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.sql('SELECT COUNT(*) AS n FROM percobaan_tugas')['n'], 2)
        opened = self.client.post(f'/api/tugas/{task["id"]}/access', data={
            'action': 'open', 'scope': 'all', 'closes_at': self.future_local()
        })
        self.assertEqual(opened.status_code, 303)
        self.assertEqual(self.client.post(f'/tugas/{token}/mulai', data={
            'nama': 'Fatimah Zahra', 'no_hp': '084444'
        }).status_code, 303)
        self.assertEqual(self.sql('SELECT COUNT(*) AS n FROM percobaan_tugas')['n'], 3)


if __name__ == '__main__':
    unittest.main()
