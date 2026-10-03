"""Add a per-admin session version without changing existing passwords."""

import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()


def migrate(conn, is_pg, commit=True):
    cur = conn.cursor()
    if is_pg:
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'users' AND column_name = 'session_version'
        """)
        exists = bool(cur.fetchone())
    else:
        cur.execute("PRAGMA table_info(users)")
        exists = any(row[1] == 'session_version' for row in cur.fetchall())
    if not exists:
        cur.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
    if commit:
        conn.commit()


if __name__ == '__main__':
    if os.environ.get('DATABASE_URL'):
        import psycopg2
        conn = psycopg2.connect(
            os.environ.get('DATABASE_URL_UNPOOLED') or os.environ['DATABASE_URL']
        )
        is_pg = True
    else:
        db_path = os.path.join(os.path.dirname(__file__), 'instance', 'absensi.db')
        conn = sqlite3.connect(db_path)
        is_pg = False
    try:
        migrate(conn, is_pg)
        print('Admin session schema ready')
    finally:
        conn.close()
