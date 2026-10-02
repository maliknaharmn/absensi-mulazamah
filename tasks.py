"""Small, timed assignment flow for administrators and participants."""

import json
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from app import app, eq, find_peserta, utcnow

JAKARTA = ZoneInfo('Asia/Jakarta')


def deadline_from_local(value):
    try:
        local = datetime.fromisoformat(value)
        if local.tzinfo is not None:
            return None
        deadline = local.replace(tzinfo=JAKARTA).astimezone(timezone.utc)
        return deadline.isoformat(timespec='seconds') if deadline > datetime.now(timezone.utc) else None
    except (TypeError, ValueError):
        return None


def local_deadline(value):
    return parse_time(value).astimezone(JAKARTA).strftime('%d %b %Y, %H:%M') if value else 'Tanpa tenggat'


app.jinja_env.globals['local_deadline'] = local_deadline


def task_globally_open(task):
    return bool(task['is_active']) and (
        not task['closes_at'] or datetime.now(timezone.utc) < parse_time(task['closes_at'])
    )


app.jinja_env.globals['task_globally_open'] = task_globally_open


def get_task(token):
    return eq("""
        SELECT t.*, k.judul AS kitab_judul
        FROM tugas t JOIN kitab k ON k.id = t.kitab_id
        WHERE t.token = ?
    """, (token,)).fetchone()


def get_attempt(task_id, attempt_token):
    return eq(
        "SELECT * FROM percobaan_tugas WHERE tugas_id = ? AND attempt_token = ?",
        (task_id, attempt_token)
    ).fetchone()


def parse_time(value):
    return datetime.fromisoformat(value)


def expired(attempt):
    return datetime.now(timezone.utc) >= parse_time(attempt['deadline_at'])


def access_for(task, peserta_id):
    override = eq("""
        SELECT allowed, closes_at FROM tugas_akses
        WHERE tugas_id = ? AND peserta_id = ?
    """, (task['id'], peserta_id)).fetchone()
    if override:
        allowed = bool(override['allowed'])
        closes_at = override['closes_at']
    else:
        allowed = bool(task['is_active'])
        closes_at = task['closes_at']
    if closes_at and datetime.now(timezone.utc) >= parse_time(closes_at):
        return False, closes_at
    return allowed, closes_at


def finalize_expired(task_id=None):
    now = utcnow()
    query = "SELECT * FROM percobaan_tugas WHERE submitted_at IS NULL"
    params = []
    if task_id is not None:
        query += " AND tugas_id = ?"
        params.append(task_id)
    changed = False
    for attempt in eq(query, params).fetchall():
        task = eq("SELECT * FROM tugas WHERE id = ?", (attempt['tugas_id'],)).fetchone()
        allowed, cutoff = access_for(task, attempt['peserta_id'])
        if expired(attempt) or not allowed:
            submitted_at = attempt['deadline_at'] if expired(attempt) else now
            eq("""
                UPDATE percobaan_tugas SET submitted_at = ?
                WHERE id = ? AND submitted_at IS NULL
            """, (submitted_at, attempt['id']))
            changed = True
    if changed:
        g.db.commit()


def clean_answers(raw, questions):
    if not isinstance(raw, dict):
        return {}
    answers = {}
    for index, question in enumerate(questions):
        value = raw.get(str(index), '')
        if not isinstance(value, str):
            continue
        value = value.strip()
        if question['type'] == 'mcq':
            if value.isdigit() and int(value) < len(question['options']):
                answers[str(index)] = value
        elif value:
            answers[str(index)] = value[:5000]
    return answers


def score_answers(questions, answers):
    total = sum(q['type'] == 'mcq' for q in questions)
    correct = sum(
        answers.get(str(index)) == str(q['correct'])
        for index, q in enumerate(questions) if q['type'] == 'mcq'
    )
    return correct, total


@app.route('/tugas-admin')
def admin_tasks():
    if 'admin_id' not in session:
        return redirect(url_for('login'), code=303)
    finalize_expired()
    tasks = eq("""
        SELECT t.*, k.judul AS kitab_judul,
            (SELECT COUNT(*) FROM percobaan_tugas a WHERE a.tugas_id = t.id) AS total_peserta,
            (SELECT COUNT(*) FROM percobaan_tugas a WHERE a.tugas_id = t.id
             AND a.submitted_at IS NOT NULL) AS total_terkirim
        FROM tugas t JOIN kitab k ON k.id = t.kitab_id
        ORDER BY t.id DESC
    """).fetchall()
    kitab = eq("SELECT * FROM kitab WHERE is_active = TRUE ORDER BY judul").fetchall()
    return render_template('tugas_admin.html', tasks=tasks, kitab=kitab)


@app.route('/api/tugas', methods=['POST'])
def create_task():
    if 'admin_id' not in session:
        return redirect(url_for('login'), code=303)
    judul = request.form.get('judul', '').strip()
    kitab_id = request.form.get('kitab_id', '').strip()
    closes_at = deadline_from_local(request.form.get('closes_at', ''))
    try:
        duration = int(request.form.get('durasi_menit', ''))
        questions = json.loads(request.form.get('questions_json', '[]'))
    except (ValueError, TypeError, json.JSONDecodeError):
        questions, duration = [], 0
    kitab = eq("SELECT id FROM kitab WHERE id = ? AND is_active = TRUE", (kitab_id,)).fetchone() if kitab_id.isdigit() else None
    if not kitab or len(judul) < 3 or len(judul) > 150 or not 1 <= duration <= 240 or not closes_at:
        flash('Isi kitab, judul tugas, durasi 1–240 menit, dan tenggat masa depan', 'error')
        return redirect(url_for('admin_tasks'), code=303)
    if not isinstance(questions, list) or not 1 <= len(questions) <= 30:
        flash('Tugas harus memiliki 1–30 soal', 'error')
        return redirect(url_for('admin_tasks'), code=303)
    cleaned = []
    for question in questions:
        if not isinstance(question, dict):
            cleaned = []
            break
        text = str(question.get('text', '')).strip()
        kind = question.get('type')
        if not 3 <= len(text) <= 500 or kind not in ('mcq', 'essay'):
            cleaned = []
            break
        if kind == 'mcq':
            options = question.get('options')
            correct = question.get('correct')
            if not isinstance(options, list) or len(options) != 4 or any(
                not isinstance(option, str) or not option.strip() for option in options
            ) or not isinstance(correct, int) or not 0 <= correct < 4:
                cleaned = []
                break
            cleaned.append({
                'text': text, 'type': kind,
                'options': [option.strip()[:300] for option in options],
                'correct': correct
            })
        else:
            cleaned.append({'text': text, 'type': kind})
    if not cleaned:
        flash('Periksa isi soal dan pilihan jawaban', 'error')
        return redirect(url_for('admin_tasks'), code=303)
    eq("""
        INSERT INTO tugas (kitab_id, judul, durasi_menit, token, questions_json, created_at, closes_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (kitab_id, judul, duration, secrets.token_urlsafe(16),
          json.dumps(cleaned, ensure_ascii=False), utcnow(), closes_at))
    g.db.commit()
    flash('Tugas dan link berhasil dibuat', 'success')
    return redirect(url_for('admin_tasks'), code=303)


@app.route('/api/tugas/<int:task_id>/access', methods=['POST'])
def set_task_access(task_id):
    if 'admin_id' not in session:
        return redirect(url_for('login'), code=303)
    task = eq("SELECT * FROM tugas WHERE id = ?", (task_id,)).fetchone()
    if not task:
        abort(404)
    action = request.form.get('action')
    scope = request.form.get('scope')
    if action not in ('open', 'close') or scope not in ('all', 'selected'):
        abort(400)
    closes_at = deadline_from_local(request.form.get('closes_at', '')) if action == 'open' else None
    if action == 'open' and not closes_at:
        flash('Saat membuka tugas, tetapkan tenggat baru di masa depan (WIB).', 'error')
        return redirect(url_for('task_results' if scope == 'selected' else 'admin_tasks',
                                **({'task_id': task_id} if scope == 'selected' else {})), code=303)
    if scope == 'all':
        eq("DELETE FROM tugas_akses WHERE tugas_id = ?", (task_id,))
        eq("UPDATE tugas SET is_active = ?, closes_at = ? WHERE id = ?", (
            action == 'open', closes_at if action == 'open' else task['closes_at'], task_id
        ))
    else:
        ids = {int(value) for value in request.form.getlist('participant_ids')
               if value.isdigit()}
        if not ids:
            flash('Pilih setidaknya satu peserta.', 'error')
            return redirect(url_for('task_results', task_id=task_id), code=303)
        changed = 0
        skipped = 0
        for peserta_id in ids:
            peserta = eq("SELECT id FROM peserta WHERE id = ? AND is_active = TRUE", (peserta_id,)).fetchone()
            if not peserta:
                continue
            already_started = eq("""
                SELECT id FROM percobaan_tugas WHERE tugas_id = ? AND peserta_id = ?
            """, (task_id, peserta_id)).fetchone()
            if action == 'open' and already_started:
                skipped += 1
                continue
            eq("""
                INSERT INTO tugas_akses (tugas_id, peserta_id, allowed, closes_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (tugas_id, peserta_id)
                DO UPDATE SET allowed = excluded.allowed, closes_at = excluded.closes_at
            """, (task_id, peserta_id, action == 'open', closes_at))
            changed += 1
        flash(f'Akses {changed} peserta diperbarui.' +
              (f' {skipped} peserta sudah pernah mulai dan tidak dapat mengulang.' if skipped else ''),
              'success')
    g.db.commit()
    finalize_expired(task_id)
    destination = 'dashboard' if request.form.get('return_to') == 'dashboard' else (
        'task_results' if scope == 'selected' else 'admin_tasks'
    )
    return redirect(url_for(destination, **({'task_id': task_id} if destination == 'task_results' else {})), code=303)


@app.route('/tugas-admin/<int:task_id>')
def task_results(task_id):
    if 'admin_id' not in session:
        return redirect(url_for('login'), code=303)
    finalize_expired(task_id)
    task = eq("""
        SELECT t.*, k.judul AS kitab_judul FROM tugas t
        JOIN kitab k ON k.id = t.kitab_id WHERE t.id = ?
    """, (task_id,)).fetchone()
    if not task:
        abort(404)
    questions = json.loads(task['questions_json'])
    rows = eq("""
        SELECT a.*, p.nama_lengkap, p.gender FROM percobaan_tugas a
        JOIN peserta p ON p.id = a.peserta_id
        WHERE a.tugas_id = ? ORDER BY a.started_at DESC
    """, (task_id,)).fetchall()
    results = []
    for row in rows:
        answers = json.loads(row['answers_json'])
        correct, total = score_answers(questions, answers)
        results.append({
            'attempt': row, 'answers': answers,
            'correct': correct, 'total': total
        })
    participants = eq("""
        SELECT p.id, p.nama_lengkap, p.gender, a.allowed, a.closes_at,
            x.id AS attempt_id
        FROM peserta p
        LEFT JOIN tugas_akses a ON a.peserta_id = p.id AND a.tugas_id = ?
        LEFT JOIN percobaan_tugas x ON x.peserta_id = p.id AND x.tugas_id = ?
        WHERE p.is_active = TRUE ORDER BY p.gender, p.nama_lengkap
    """, (task_id, task_id)).fetchall()
    return render_template('tugas_results.html', task=task, questions=questions,
                           results=results, participants=participants)


@app.route('/api/tugas/<int:task_id>/review/<int:attempt_id>', methods=['POST'])
def review_essay(task_id, attempt_id):
    if 'admin_id' not in session:
        return redirect(url_for('login'), code=303)
    eq("""
        UPDATE percobaan_tugas SET reviewed_at = ?
        WHERE id = ? AND tugas_id = ? AND submitted_at IS NOT NULL
    """, (utcnow(), attempt_id, task_id))
    g.db.commit()
    return redirect(url_for('task_results', task_id=task_id), code=303)


@app.route('/tugas/<token>')
def task_intro(token):
    task = get_task(token)
    if not task:
        abort(404)
    return render_template('tugas_intro.html', task=task, values={}, error=None,
                           globally_open=task_globally_open(task))


@app.route('/tugas/<token>/mulai', methods=['POST'])
def start_task(token):
    task = get_task(token)
    if not task:
        abort(404)
    values = {
        'nama': request.form.get('nama', '').strip(),
        'no_hp': request.form.get('no_hp', '').strip()
    }
    globally_open = task_globally_open(task)
    peserta, reason = find_peserta(values['nama'], values['no_hp'])
    if reason:
        errors = {
            'nama': 'Nama tidak ditemukan di daftar peserta',
            'hp': 'Nomor HP tidak cocok dengan data peserta',
            'hp_kosong': 'Nomor HP belum terdaftar. Hubungi admin.',
            'ambigu': 'Identitas terdaftar lebih dari sekali. Hubungi admin.'
        }
        if reason == 'nama':
            values['nama'] = ''
        elif reason in ('hp', 'hp_kosong'):
            values['no_hp'] = ''
        return render_template('tugas_intro.html', task=task, values=values,
                               error=errors[reason], globally_open=globally_open), 422
    existing = eq("""
        SELECT * FROM percobaan_tugas WHERE tugas_id = ? AND peserta_id = ?
    """, (task['id'], peserta['id'])).fetchone()
    if existing:
        return redirect(url_for('work_task', token=token,
                                attempt_token=existing['attempt_token']), code=303)
    allowed, cutoff = access_for(task, peserta['id'])
    if not allowed:
        return render_template('tugas_intro.html', task=task, values=values,
                               error='Akses tugas sudah ditutup atau tenggatnya telah lewat. Hubungi admin jika perlu dibuka kembali.',
                               globally_open=globally_open), 403
    started = datetime.now(timezone.utc)
    deadline = started + timedelta(minutes=task['durasi_menit'])
    if cutoff:
        deadline = min(deadline, parse_time(cutoff))
    attempt_token = secrets.token_urlsafe(24)
    eq("""
        INSERT INTO percobaan_tugas
            (tugas_id, peserta_id, attempt_token, started_at, deadline_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (tugas_id, peserta_id) DO NOTHING
    """, (task['id'], peserta['id'], attempt_token,
          started.isoformat(timespec='seconds'), deadline.isoformat(timespec='seconds')))
    g.db.commit()
    actual = eq("""
        SELECT attempt_token FROM percobaan_tugas WHERE tugas_id = ? AND peserta_id = ?
    """, (task['id'], peserta['id'])).fetchone()
    return redirect(url_for('work_task', token=token,
                            attempt_token=actual['attempt_token']), code=303)


@app.route('/tugas/<token>/kerjakan/<attempt_token>')
def work_task(token, attempt_token):
    task = get_task(token)
    if not task:
        abort(404)
    finalize_expired(task['id'])
    attempt = get_attempt(task['id'], attempt_token)
    if not attempt:
        abort(404)
    questions = json.loads(task['questions_json'])
    answers = json.loads(attempt['answers_json'])
    _, cutoff = access_for(task, attempt['peserta_id'])
    deadline = min(parse_time(attempt['deadline_at']), parse_time(cutoff)) if cutoff else parse_time(attempt['deadline_at'])
    remaining_ms = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds() * 1000))
    return render_template(
        'tugas_work.html', task=task, attempt=attempt, questions=questions,
        answers=answers, remaining_ms=remaining_ms,
        score=score_answers(questions, answers)
    )


@app.route('/tugas/<token>/kerjakan/<attempt_token>/autosave', methods=['POST'])
def autosave_task(token, attempt_token):
    task = get_task(token)
    if task:
        finalize_expired(task['id'])
    attempt = get_attempt(task['id'], attempt_token) if task else None
    if not attempt:
        abort(404)
    allowed, cutoff = access_for(task, attempt['peserta_id'])
    if attempt['submitted_at'] or expired(attempt) or not allowed:
        return jsonify({'saved': False, 'expired': True}), 409
    raw = (request.get_json(silent=True) or {}).get('answers')
    answers = clean_answers(raw, json.loads(task['questions_json']))
    eq("""
        UPDATE percobaan_tugas SET answers_json = ?
        WHERE id = ? AND submitted_at IS NULL AND deadline_at > ?
    """, (json.dumps(answers, ensure_ascii=False), attempt['id'], utcnow()))
    g.db.commit()
    return jsonify({'saved': True})


@app.route('/tugas/<token>/kerjakan/<attempt_token>/status')
def task_status(token, attempt_token):
    task = get_task(token)
    if task:
        finalize_expired(task['id'])
    attempt = get_attempt(task['id'], attempt_token) if task else None
    if not attempt:
        abort(404)
    allowed, cutoff = access_for(task, attempt['peserta_id'])
    deadline = min(parse_time(attempt['deadline_at']), parse_time(cutoff)) if cutoff else parse_time(attempt['deadline_at'])
    return jsonify({
        'active': not attempt['submitted_at'] and allowed and not expired(attempt),
        'remaining_ms': max(0, int((deadline - datetime.now(timezone.utc)).total_seconds() * 1000))
    })


@app.route('/tugas/<token>/kerjakan/<attempt_token>/kirim', methods=['POST'])
def submit_task(token, attempt_token):
    task = get_task(token)
    if task:
        finalize_expired(task['id'])
    attempt = get_attempt(task['id'], attempt_token) if task else None
    if not attempt:
        abort(404)
    if not attempt['submitted_at']:
        now = utcnow()
        allowed, _ = access_for(task, attempt['peserta_id'])
        if not expired(attempt) and allowed:
            raw = {str(i): request.form.get(f'answer_{i}', '')
                   for i in range(len(json.loads(task['questions_json'])))}
            answers = clean_answers(raw, json.loads(task['questions_json']))
            eq("""
                UPDATE percobaan_tugas SET answers_json = ?, submitted_at = ?
                WHERE id = ? AND submitted_at IS NULL AND deadline_at > ?
            """, (json.dumps(answers, ensure_ascii=False), now, attempt['id'], now))
            g.db.commit()
        finalize_expired(task['id'])
    return redirect(url_for('work_task', token=token, attempt_token=attempt_token), code=303)
