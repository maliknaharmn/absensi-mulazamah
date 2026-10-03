import os
import sys
import sqlite3
import secrets
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, Response, g
)
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
sys.modules.setdefault('app', sys.modules[__name__])
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'absensi.db')

IS_PG = bool(os.environ.get('DATABASE_URL'))

if IS_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.extensions import connection as PgConnection


def get_db():
    if 'db' not in g:
        if IS_PG:
            g.db = psycopg2.connect(
                os.environ['DATABASE_URL'],
                cursor_factory=RealDictCursor
            )
        else:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def eq(query, params=()):
    """Execute query with auto ? → %s conversion for PostgreSQL"""
    db = get_db()
    cur = db.cursor()
    if IS_PG:
        cur.execute(query.replace('?', '%s'), params)
    else:
        cur.execute(query, params)
    return cur


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def display_title(judul, kitab_judul=None):
    return f'Mulazamah Kitab "{kitab_judul}" - {judul}' if kitab_judul else judul


app.jinja_env.globals['display_title'] = display_title


def find_peserta(nama, no_hp, gender=None):
    query = "SELECT * FROM peserta WHERE lower(nama_lengkap) = lower(?) AND is_active = TRUE"
    params = [nama]
    if gender:
        query += " AND gender = ?"
        params.append(gender)
    matches = eq(query, params).fetchall()
    if not matches:
        return None, 'nama'
    verified = [p for p in matches if p['no_hp'] and p['no_hp'] == no_hp]
    if len(verified) == 1:
        return verified[0], None
    if len(verified) > 1:
        return None, 'ambigu'
    if len(matches) == 1 and not matches[0]['no_hp']:
        return None, 'hp_kosong'
    return None, 'hp'


def sesi_with_kitab(token):
    return eq("""
        SELECT s.*, k.judul AS kitab_judul
        FROM sesi_presensi s LEFT JOIN kitab k ON k.id = s.kitab_id
        WHERE s.token = ? AND s.is_active = TRUE
    """, (token,)).fetchone()


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


@app.before_request
def validate_admin_session():
    """Revoke every older signed session after an admin password change."""
    admin_id = session.get('admin_id')
    if admin_id is None:
        return
    user = eq("SELECT session_version FROM users WHERE id = ?", (admin_id,)).fetchone()
    if not user or session.get('session_version') != user['session_version']:
        session.clear()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('login'), code=303)
        return f(*args, **kwargs)
    return decorated_function


# ========== AUTH ==========

@app.route('/')
def index():
    if 'admin_id' in session:
        return redirect(url_for('dashboard'), code=303)
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    username = ''
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        cur = eq("SELECT * FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['admin_id'] = user['id']
            session['admin_username'] = user['username']
            session['session_version'] = user['session_version']
            return redirect(url_for('dashboard'), code=303)
        if not user:
            username = ''
        flash('Username atau password salah', 'error')
    return render_template('login.html', username=username)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'), code=303)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    csrf_token = session.setdefault('settings_csrf', secrets.token_urlsafe(32))
    if request.method == 'GET':
        return render_template('settings.html', csrf_token=csrf_token)

    submitted_token = request.form.get('csrf_token', '')
    if not secrets.compare_digest(csrf_token, submitted_token):
        return render_template('settings.html', csrf_token=csrf_token,
                               error='Sesi formulir tidak valid. Muat ulang halaman lalu coba lagi.'), 400

    current = request.form.get('current_password', '')
    new = request.form.get('new_password', '')
    confirmation = request.form.get('confirm_password', '')
    user = eq("SELECT password_hash FROM users WHERE id = ?", (session['admin_id'],)).fetchone()
    if not user or not check_password_hash(user['password_hash'], current):
        error = 'Password saat ini tidak sesuai.'
    elif not 12 <= len(new) <= 128:
        error = 'Password baru harus 12–128 karakter.'
    elif check_password_hash(user['password_hash'], new):
        error = 'Password baru harus berbeda dari password saat ini.'
    elif new != confirmation:
        error = 'Konfirmasi password baru tidak cocok.'
    else:
        updated = eq("""
            UPDATE users
            SET password_hash = ?, session_version = session_version + 1
            WHERE id = ? AND password_hash = ?
        """, (generate_password_hash(new), session['admin_id'], user['password_hash']))
        if updated.rowcount == 1:
            g.db.commit()
            session.clear()
            flash('Password berhasil diubah. Silakan login kembali.', 'success')
            return redirect(url_for('login'), code=303)
        g.db.rollback()
        session.clear()
        flash('Password berubah di sesi lain. Silakan login kembali.', 'error')
        return redirect(url_for('login'), code=303)

    return render_template('settings.html', csrf_token=csrf_token, error=error), 422


# ========== DASHBOARD ==========

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today().isoformat()

    cur = eq("""
        SELECT s.*, k.judul AS kitab_judul
        FROM sesi_presensi s LEFT JOIN kitab k ON k.id = s.kitab_id
        ORDER BY s.tanggal DESC, s.gender
    """)
    all_sesi = cur.fetchall()
    kitab = eq("SELECT * FROM kitab ORDER BY is_active DESC, judul").fetchall()

    sesi_data = []
    for s in all_sesi:
        cur2 = eq("""
            SELECT k.*, p.nama_lengkap, p.gender, p.no_hp
            FROM kehadiran k
            JOIN peserta p ON k.peserta_id = p.id
            WHERE k.sesi_id = ?
            ORDER BY p.nama_lengkap
        """, (s['id'],))
        rows = cur2.fetchall()

        hadir = len([r for r in rows if not r['izin_menunggu'] and r['status_akhir'] in ('hadir_offline', 'hadir_online')])
        izin = len([r for r in rows if r['status_akhir'] == 'izin_disetujui'])
        izin_menunggu = len([r for r in rows if r['izin_menunggu']])
        alpha = len([r for r in rows if r['status_akhir'] == 'alpha'])

        sesi_data.append({
            'sesi': s,
            'kehadiran': rows,
            'hadir': hadir,
            'izin': izin,
            'izin_menunggu': izin_menunggu,
            'alpha': alpha,
            'is_today': str(s['tanggal']) == today
        })

    tugas = eq("""
        SELECT t.*, k.judul AS kitab_judul FROM tugas t
        JOIN kitab k ON k.id = t.kitab_id ORDER BY t.id DESC LIMIT 10
    """).fetchall()
    return render_template('dashboard.html', sesi_data=sesi_data, today=today,
                           kitab=kitab, tugas=tugas)


# ========== PRESENSI PUBLIC ==========

@app.route('/presensi/<gender>/<token>', methods=['GET'])
def presensi_form(gender, token):
    if gender not in ('ikhwan', 'akhwat'):
        return render_template('presensi.html', error='Link tidak valid'), 404

    sesi = sesi_with_kitab(token)
    if not sesi:
        return render_template('presensi.html', error='Link tidak ditemukan atau sudah tidak aktif'), 404
    if sesi['gender'] != gender:
        return render_template('presensi.html', error='Link tidak sesuai gender'), 404

    tanggal_fmt = datetime.strptime(str(sesi['tanggal']), '%Y-%m-%d').strftime('%d %B %Y')

    return render_template(
        'presensi.html',
        sesi=sesi,
        tanggal_fmt=tanggal_fmt,
        error=None,
        success=False,
        values={}
    )


@app.route('/presensi/submit', methods=['POST'])
def presensi_submit():
    token = request.form.get('token', '').strip()
    nama = request.form.get('nama', '').strip()
    no_hp = request.form.get('no_hp', '').strip()
    status = request.form.get('status', '').strip()
    alasan = request.form.get('alasan_izin', '').strip()

    sesi = sesi_with_kitab(token)
    if not sesi:
        return render_template('presensi.html', error='Link tidak valid', sesi=None), 404

    values = {'nama': nama, 'no_hp': no_hp, 'status': status, 'alasan_izin': alasan}
    tanggal_fmt = datetime.strptime(str(sesi['tanggal']), '%Y-%m-%d').strftime('%d %B %Y')

    def invalid(message, clear=None):
        if clear:
            values[clear] = ''
        return render_template(
            'presensi.html', sesi=sesi, tanggal_fmt=tanggal_fmt,
            form_error=message, error=None, success=False, values=values
        ), 422

    peserta, reason = find_peserta(nama, no_hp, sesi['gender'])
    if reason == 'nama':
        return invalid('Nama tidak ditemukan di daftar peserta', 'nama')
    if reason == 'hp':
        return invalid('Nomor HP tidak cocok dengan data peserta', 'no_hp')
    if reason == 'hp_kosong':
        return invalid('Nomor HP belum terdaftar. Hubungi admin untuk melengkapi data.', 'no_hp')
    if reason:
        return invalid('Nama dan nomor HP terdaftar lebih dari sekali. Hubungi admin.')

    cur3 = eq(
        "SELECT * FROM kehadiran WHERE sesi_id = ? AND peserta_id = ?",
        (sesi['id'], peserta['id'])
    )
    existing = cur3.fetchone()

    if existing:
        return invalid('Anda sudah mengisi presensi untuk sesi ini')

    if status not in ('hadir_offline', 'hadir_online', 'izin'):
        return invalid('Pilih status kehadiran yang valid')

    if status == 'izin':
        status_akhir = 'alpha'
        izin_menunggu = True
    else:
        status_akhir = status
        izin_menunggu = False

    eq(
        "INSERT INTO kehadiran (sesi_id, peserta_id, status_akhir, izin_menunggu, alasan_izin, no_hp_terinput) VALUES (?, ?, ?, ?, ?, ?)",
        (sesi['id'], peserta['id'], status_akhir, izin_menunggu, alasan if status == 'izin' else None, no_hp)
    )
    g.db.commit()

    return render_template('presensi.html', success=True, nama=peserta['nama_lengkap'], sesi=None, error=None)


# ========== API: SEARCH PESERTA ==========

@app.route('/api/search-peserta')
def search_peserta():
    q = request.args.get('q', '').strip()
    gender = request.args.get('gender', '').strip()
    if not q or len(q) < 2 or gender not in ('ikhwan', 'akhwat'):
        return jsonify([])

    cur = eq(
        "SELECT nama_lengkap FROM peserta WHERE gender = ? AND is_active = TRUE AND lower(nama_lengkap) LIKE lower(?) ORDER BY nama_lengkap LIMIT 20",
        (gender, f'%{q}%')
    )
    return jsonify([r['nama_lengkap'] for r in cur.fetchall()])


# ========== API: GENERATE SESI ==========

@app.route('/api/generate-sesi', methods=['POST'])
@login_required
def generate_sesi():
    gender = request.form.get('gender', '').strip()
    tanggal = request.form.get('tanggal', '').strip()
    judul = request.form.get('judul', '').strip()
    kitab_id = request.form.get('kitab_id', '').strip()

    if gender not in ('ikhwan', 'akhwat'):
        flash('Gender tidak valid', 'error')
        return redirect(url_for('dashboard'), code=303)

    try:
        tgl = date.fromisoformat(tanggal)
    except ValueError:
        flash('Format tanggal tidak valid', 'error')
        return redirect(url_for('dashboard'), code=303)

    if tgl < date.today():
        flash('Tidak bisa membuat presensi untuk tanggal kemarin', 'error')
        return redirect(url_for('dashboard'), code=303)

    if len(judul) < 5:
        flash('Judul acara minimal 5 karakter', 'error')
        return redirect(url_for('dashboard'), code=303)

    selected = eq("SELECT id FROM kitab WHERE id = ? AND is_active = TRUE", (kitab_id,)).fetchone() if kitab_id.isdigit() else None
    if not selected:
        flash('Pilih judul kitab yang aktif', 'error')
        return redirect(url_for('dashboard'), code=303)

    token = secrets.token_urlsafe(8)

    eq(
        "INSERT INTO sesi_presensi (gender, tanggal, judul_acara, token, kitab_id) VALUES (?, ?, ?, ?, ?)",
        (gender, tanggal, judul, token, selected['id'])
    )
    g.db.commit()

    flash(f'Link berhasil dibuat untuk {gender}', 'success')
    return redirect(url_for('dashboard'), code=303)


@app.route('/api/kitab', methods=['POST'])
@login_required
def manage_kitab():
    action = request.form.get('action', '')
    judul = request.form.get('judul', '').strip()
    kitab_id = request.form.get('kitab_id', '').strip()
    if action in ('add', 'rename'):
        if len(judul) < 2 or len(judul) > 150:
            flash('Judul kitab harus 2–150 karakter', 'error')
            return redirect(url_for('dashboard'), code=303)
        duplicate = eq(
            "SELECT id FROM kitab WHERE lower(judul) = lower(?)",
            (judul,)
        ).fetchone()
        if duplicate and (action == 'add' or str(duplicate['id']) != kitab_id):
            flash('Judul kitab sudah ada', 'error')
            return redirect(url_for('dashboard'), code=303)
        if action == 'add':
            eq("INSERT INTO kitab (judul) VALUES (?)", (judul,))
        elif kitab_id.isdigit():
            eq("UPDATE kitab SET judul = ? WHERE id = ?", (judul, kitab_id))
    elif action == 'toggle' and kitab_id.isdigit():
        if IS_PG:
            eq("UPDATE kitab SET is_active = NOT is_active WHERE id = ?", (kitab_id,))
        else:
            eq("UPDATE kitab SET is_active = 1 - is_active WHERE id = ?", (kitab_id,))
    else:
        flash('Aksi kitab tidak valid', 'error')
        return redirect(url_for('dashboard'), code=303)
    g.db.commit()
    flash('Daftar kitab diperbarui', 'success')
    return redirect(url_for('dashboard'), code=303)


@app.route('/api/sesi-kitab', methods=['POST'])
@login_required
def set_sesi_kitab():
    sesi_id = request.form.get('sesi_id', '').strip()
    kitab_id = request.form.get('kitab_id', '').strip()
    if not sesi_id.isdigit() or not kitab_id.isdigit():
        flash('Pilihan sesi atau kitab tidak valid', 'error')
    elif eq("SELECT id FROM kitab WHERE id = ?", (kitab_id,)).fetchone():
        eq("UPDATE sesi_presensi SET kitab_id = ? WHERE id = ?", (kitab_id, sesi_id))
        g.db.commit()
        flash('Kitab sesi diperbarui', 'success')
    else:
        flash('Kitab tidak ditemukan', 'error')
    return redirect(url_for('dashboard'), code=303)


# ========== API: APPROVE IZIN ==========

@app.route('/api/approve-izin', methods=['POST'])
@login_required
def approve_izin():
    kehadiran_id = request.form.get('kehadiran_id')
    action = request.form.get('action')

    if action == 'approve':
        eq(
            "UPDATE kehadiran SET status_akhir = 'izin_disetujui', izin_menunggu = FALSE WHERE id = ?",
            (kehadiran_id,)
        )
    elif action == 'reject':
        eq(
            "UPDATE kehadiran SET status_akhir = 'alpha', izin_menunggu = FALSE WHERE id = ?",
            (kehadiran_id,)
        )
    g.db.commit()
    return redirect(url_for('dashboard'), code=303)


# ========== API: EDIT KEHADIRAN ==========

@app.route('/api/edit-kehadiran', methods=['POST'])
@login_required
def edit_kehadiran():
    kehadiran_id = request.form.get('kehadiran_id')
    status_baru = request.form.get('status_baru')

    valid_status = ('hadir_offline', 'hadir_online', 'izin_disetujui', 'alpha')
    if status_baru not in valid_status:
        flash('Status tidak valid', 'error')
        return redirect(url_for('dashboard'), code=303)

    izin_menunggu = status_baru == 'izin_disetujui'

    eq(
        "UPDATE kehadiran SET status_akhir = ?, izin_menunggu = ? WHERE id = ?",
        (status_baru, izin_menunggu, kehadiran_id)
    )
    g.db.commit()
    return redirect(url_for('dashboard'), code=303)


# ========== API: CLOSE SESI ==========

@app.route('/api/close-sesi', methods=['POST'])
@login_required
def close_sesi():
    sesi_id = request.form.get('sesi_id')
    eq("UPDATE sesi_presensi SET is_active = FALSE WHERE id = ?", (sesi_id,))
    g.db.commit()
    flash('Sesi berhasil ditutup', 'success')
    return redirect(url_for('dashboard'), code=303)


# ========== API: DELETE SESI ==========

@app.route('/api/delete-sesi', methods=['POST'])
@login_required
def delete_sesi():
    sesi_id = request.form.get('sesi_id')
    eq("DELETE FROM kehadiran WHERE sesi_id = ?", (sesi_id,))
    eq("DELETE FROM sesi_presensi WHERE id = ?", (sesi_id,))
    g.db.commit()
    flash('Sesi berhasil dihapus', 'success')
    return redirect(url_for('dashboard'), code=303)


# ========== MASTER PESERTA ==========

@app.route('/master-peserta')
@login_required
def master_peserta():
    cur = eq("SELECT * FROM peserta ORDER BY gender, nama_lengkap")
    return render_template('peserta.html', peserta=cur.fetchall())


@app.route('/api/peserta', methods=['POST'])
@login_required
def api_peserta():
    action = request.form.get('action')

    if action == 'add':
        nama = request.form.get('nama', '').strip()
        gender = request.form.get('gender', '').strip()
        no_hp = request.form.get('no_hp', '').strip()
        if nama and gender in ('ikhwan', 'akhwat'):
            eq("INSERT INTO peserta (nama_lengkap, gender, no_hp) VALUES (?, ?, ?)", (nama, gender, no_hp if no_hp else None))
            g.db.commit()
            flash('Peserta berhasil ditambahkan', 'success')

    elif action == 'edit':
        pid = request.form.get('id')
        nama = request.form.get('nama', '').strip()
        gender = request.form.get('gender', '').strip()
        no_hp = request.form.get('no_hp', '').strip()
        if pid and nama and gender in ('ikhwan', 'akhwat'):
            eq("UPDATE peserta SET nama_lengkap = ?, gender = ?, no_hp = ? WHERE id = ?", (nama, gender, no_hp if no_hp else None, pid))
            g.db.commit()
            flash('Peserta berhasil diperbarui', 'success')

    elif action == 'toggle':
        pid = request.form.get('id')
        if pid:
            if IS_PG:
                eq("UPDATE peserta SET is_active = NOT is_active WHERE id = ?", (pid,))
            else:
                eq("UPDATE peserta SET is_active = 1 - is_active WHERE id = ?", (pid,))
            g.db.commit()
            flash('Status peserta diperbarui', 'success')

    elif action == 'delete':
        pid = request.form.get('id')
        if pid:
            task_history = eq(
                "SELECT id FROM percobaan_tugas WHERE peserta_id = ? LIMIT 1", (pid,)
            ).fetchone()
            if task_history:
                flash('Peserta sudah memiliki riwayat tugas. Nonaktifkan peserta agar rekap tetap tersimpan.', 'error')
            else:
                eq("DELETE FROM kehadiran WHERE peserta_id = ?", (pid,))
                eq("DELETE FROM peserta WHERE id = ?", (pid,))
                g.db.commit()
                flash('Peserta berhasil dihapus', 'success')

    return redirect(url_for('master_peserta'), code=303)


# ========== REKAP ==========

@app.route('/rekap')
@login_required
def rekap():
    start = request.args.get('start_date', date.today().replace(day=1).isoformat())
    end = request.args.get('end_date', date.today().isoformat())
    gender_filter = request.args.get('gender', 'semua')
    sesi_query = """
        SELECT s.*, k.judul AS kitab_judul
        FROM sesi_presensi s LEFT JOIN kitab k ON k.id = s.kitab_id
        WHERE s.tanggal BETWEEN ? AND ?
    """
    sesi_params = [start, end]
    if gender_filter in ('ikhwan', 'akhwat'):
        sesi_query += " AND s.gender = ?"
        sesi_params.append(gender_filter)
    sesi_query += " ORDER BY s.tanggal DESC, s.gender"
    sesi_list = eq(sesi_query, sesi_params).fetchall()

    query = """
        SELECT p.id as peserta_id, p.nama_lengkap, p.gender,
               SUM(CASE WHEN k.status_akhir IN ('hadir_offline', 'hadir_online') THEN 1 ELSE 0 END) as hadir,
               SUM(CASE WHEN k.status_akhir = 'izin_disetujui' THEN 1 ELSE 0 END) as izin,
               SUM(CASE WHEN k.status_akhir = 'alpha' THEN 1 ELSE 0 END) as alpha
        FROM peserta p
        LEFT JOIN kehadiran k ON p.id = k.peserta_id
        LEFT JOIN sesi_presensi s ON k.sesi_id = s.id
            AND s.tanggal BETWEEN ? AND ?
    """
    params = [start, end]

    if gender_filter != 'semua':
        query += " WHERE p.gender = ?"
        params.append(gender_filter)

    query += " GROUP BY p.id ORDER BY p.gender, p.nama_lengkap"

    cur = eq(query, params)
    rows = cur.fetchall()

    rekap_data = []
    for r in rows:
        total = r['hadir'] + r['izin'] + r['alpha']
        pct = round((r['hadir'] / total * 100), 1) if total > 0 else 0
        rekap_data.append({
            'nama': r['nama_lengkap'],
            'gender': r['gender'],
            'hadir': r['hadir'],
            'izin': r['izin'],
            'alpha': r['alpha'],
            'pct': pct
        })

    return render_template(
        'rekap.html', rekap_data=rekap_data, sesi_list=sesi_list,
        start=start, end=end, gender_filter=gender_filter
    )


@app.route('/rekap/export')
@login_required
def rekap_export():
    start = request.args.get('start_date', date.today().replace(day=1).isoformat())
    end = request.args.get('end_date', date.today().isoformat())
    gender_filter = request.args.get('gender', 'semua')

    query = """
        SELECT p.nama_lengkap, p.gender,
               SUM(CASE WHEN k.status_akhir IN ('hadir_offline', 'hadir_online') THEN 1 ELSE 0 END) as hadir,
               SUM(CASE WHEN k.status_akhir = 'izin_disetujui' THEN 1 ELSE 0 END) as izin,
               SUM(CASE WHEN k.status_akhir = 'alpha' THEN 1 ELSE 0 END) as alpha
        FROM peserta p
        LEFT JOIN kehadiran k ON p.id = k.peserta_id
        LEFT JOIN sesi_presensi s ON k.sesi_id = s.id
            AND s.tanggal BETWEEN ? AND ?
    """
    params = [start, end]

    if gender_filter != 'semua':
        query += " WHERE p.gender = ?"
        params.append(gender_filter)

    query += " GROUP BY p.id ORDER BY p.gender, p.nama_lengkap"

    cur = eq(query, params)
    rows = cur.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Nama', 'Gender', 'Total Hadir', 'Total Izin', 'Total Alpha', '% Hadir'])

    for r in rows:
        total = r['hadir'] + r['izin'] + r['alpha']
        pct = round((r['hadir'] / total * 100), 1) if total > 0 else 0
        writer.writerow([r['nama_lengkap'], r['gender'], r['hadir'], r['izin'], r['alpha'], f'{pct}%'])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=rekap_{start}_{end}.csv'}
    )


# ========== HEALTH CHECK ==========

@app.route('/health')
def health():
    try:
        eq("SELECT 1")
        return jsonify({"status": "ok", "database": "postgresql" if IS_PG else "sqlite"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


import tasks  # noqa: E402 — register assignment routes after app helpers exist


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5001)
