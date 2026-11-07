"""SQLite regression vault."""
import json
import os
import sqlite3
from contextlib import closing

FAILURE_COLS = {
    "seed": "INTEGER", "delay": "REAL", "kind": "TEXT", "repro_rate": "TEXT",
    "fail_runs": "TEXT", "ai_explanation": "TEXT", "fix_diff": "TEXT",
    "fix_edits": "TEXT", "fix_verified": "INTEGER",
}
SCAN_COLS = {"root": "TEXT"}


def db_path():
    return os.environ.get("FLAKEHUNT_DB", "flakehunt_vault.sqlite")


def get_conn():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _add_missing(conn, table, cols):
    have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    for name, typ in cols.items():
        if name not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, typ))


def init_db():
    with closing(get_conn()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            target TEXT, runs INTEGER, profile TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, nodeid TEXT,
            original_order TEXT, min_order TEXT, traceback TEXT)""")
        _add_missing(conn, "scans", SCAN_COLS)
        _add_missing(conn, "failures", FAILURE_COLS)
        conn.commit()


def save_scan(root, target, runs, profile):
    with closing(get_conn()) as conn:
        cur = conn.execute("INSERT INTO scans (root, target, runs, profile) VALUES (?, ?, ?, ?)",
                           (root, target, runs, profile))
        conn.commit()
        return cur.lastrowid


def save_failure(scan_id, f, ai_explanation=""):
    with closing(get_conn()) as conn:
        cur = conn.execute(
            """INSERT INTO failures (scan_id, nodeid, original_order, min_order, traceback, seed, delay,
               kind, repro_rate, fail_runs, ai_explanation) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (scan_id, f.nodeid, json.dumps(f.original_order), json.dumps(f.min_order), f.traceback,
             f.seed, f.delay, f.kind, f.repro_rate, "%d/%d" % (f.fail_runs, f.runs), ai_explanation))
        conn.commit()
        return cur.lastrowid


_SELECT = """SELECT f.*, s.timestamp, s.target, s.root, s.profile, s.runs
             FROM failures f JOIN scans s ON f.scan_id = s.id"""


def list_failures():
    with closing(get_conn()) as conn:
        return [dict(r) for r in conn.execute(_SELECT + " ORDER BY f.id DESC")]


def get_failure(failure_id):
    with closing(get_conn()) as conn:
        r = conn.execute(_SELECT + " WHERE f.id = ?", (failure_id,)).fetchone()
        return dict(r) if r else None


def update_ai(failure_id, text):
    with closing(get_conn()) as conn:
        conn.execute("UPDATE failures SET ai_explanation = ? WHERE id = ?", (text, failure_id))
        conn.commit()


def update_fix(failure_id, diff, edits, verified):
    with closing(get_conn()) as conn:
        conn.execute("UPDATE failures SET fix_diff = ?, fix_edits = ?, fix_verified = ? WHERE id = ?",
                     (diff, json.dumps(edits), 1 if verified else 0, failure_id))
        conn.commit()
