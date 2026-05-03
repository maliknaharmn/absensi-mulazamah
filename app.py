import os
import sqlite3
import secrets
import csv
import io
from datetime import date, datetime
from functools import wraps
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, Response, g
)
from werkzeug.security import check_password_hash

load_dotenv()

app = Flask(__name__)
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


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


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
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        cur = eq("SELECT * FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['admin_id'] = user['id']
            session['admin_username'] = user['username']
            return redirect(url_for('dashboard'), code=303)
        flash('Username atau password salah', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'), code=303)


# ========== DASHBOARD ==========

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today().isoformat()

    cur = eq("SELECT * FROM sesi_presensi ORDER BY tanggal DESC, gender")
    all_sesi = cur.fetchall()

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

    return render_template('dashboard.html', sesi_data=sesi_data, today=today)


# ========== PRESENSI PUBLIC ==========

@app.route('/presensi/<gender>/<token>', methods=['GET'])
def presensi_form(gender, token):
    if gender not in ('ikhwan', 'akhwat'):
        return render_template('presensi.html', error='Link tidak valid'), 404

    cur = eq("SELECT * FROM sesi_presensi WHERE token = ? AND is_active = TRUE", (token,))
    sesi = cur.fetchone()
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
        success=False
    )


@app.route('/presensi/submit', methods=['POST'])
def presensi_submit():
    token = request.form.get('token', '').strip()
    nama = request.form.get('nama', '').strip()
    no_hp = request.form.get('no_hp', '').strip()
    status = request.form.get('status', '').strip()
    alasan = request.form.get('alasan_izin', '').strip()

    cur = eq("SELECT * FROM sesi_presensi WHERE token = ? AND is_active = TRUE", (token,))
    sesi = cur.fetchone()
    if not sesi:
        flash('Link tidak valid', 'error')
        return redirect(url_for('presensi_form', gender='ikhwan', token=token), code=303)

    cur2 = eq(
        "SELECT * FROM peserta WHERE nama_lengkap = ? AND gender = ? AND is_active = TRUE",
        (nama, sesi['gender'])
    )
    peserta = cur2.fetchone()

    if not peserta:
        flash('Nama tidak ditemukan di daftar peserta', 'error')
        return redirect(url_for('presensi_form', gender=sesi['gender'], token=token), code=303)

    if peserta['no_hp'] and peserta['no_hp'] != no_hp:
        flash('Nomor HP tidak cocok dengan data peserta', 'error')
        return redirect(url_for('presensi_form', gender=sesi['gender'], token=token), code=303)

    cur3 = eq(
        "SELECT * FROM kehadiran WHERE sesi_id = ? AND peserta_id = ?",
        (sesi['id'], peserta['id'])
    )
    existing = cur3.fetchone()

    if existing:
        flash('Anda sudah mengisi presensi untuk sesi ini', 'error')
        return redirect(url_for('presensi_form', gender=sesi['gender'], token=token), code=303)

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

    return render_template('presensi.html', success=True, nama=nama, sesi=None, error=None)


# ========== API: SEARCH PESERTA ==========

@app.route('/api/search-peserta')
def search_peserta():
    q = request.args.get('q', '').strip()
    gender = request.args.get('gender', '').strip()
    if not q or len(q) < 2 or gender not in ('ikhwan', 'akhwat'):
        return jsonify([])

    cur = eq(
        "SELECT nama_lengkap FROM peserta WHERE gender = ? AND is_active = TRUE AND nama_lengkap LIKE ? ORDER BY nama_lengkap LIMIT 20",
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

    token = secrets.token_urlsafe(8)

    eq(
        "INSERT INTO sesi_presensi (gender, tanggal, judul_acara, token) VALUES (?, ?, ?, ?)",
        (gender, tanggal, judul, token)
    )
    g.db.commit()

    flash(f'Link berhasil dibuat untuk {gender}', 'success')
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

    return render_template('rekap.html', rekap_data=rekap_data, start=start, end=end, gender_filter=gender_filter)


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


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5001)
