"""SQLite state store for resumable jobs and idempotent item transitions."""
from __future__ import annotations
import hashlib, sqlite3, time, uuid
from contextlib import contextmanager
from pathlib import Path
from config import DB_FILE, LEASE_SECONDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, profile_url TEXT NOT NULL, username TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', cancel_requested INTEGER NOT NULL DEFAULT 0, page INTEGER NOT NULL DEFAULT 1, seek TEXT, discovered INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL, last_error TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, source_key TEXT NOT NULL, page_url TEXT NOT NULL, direct_url TEXT NOT NULL, filename TEXT NOT NULL, media_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, lease_until REAL, local_path TEXT, telegram_message_id INTEGER, last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, sent_at REAL, UNIQUE(job_id, source_key));
CREATE INDEX IF NOT EXISTS idx_items_ready ON items(job_id,status,next_attempt_at);
CREATE TABLE IF NOT EXISTS stats(id INTEGER PRIMARY KEY CHECK(id=1), total_sent INTEGER NOT NULL DEFAULT 0, total_skipped INTEGER NOT NULL DEFAULT 0, total_failed INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO stats(id) VALUES(1);
"""

def key(url: str) -> str: return hashlib.sha256(url.encode()).hexdigest()
@contextmanager
def connect():
    conn = sqlite3.connect(DB_FILE, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA busy_timeout=30000")
    try: yield conn
    finally: conn.close()
def init_db():
    Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        c.execute("UPDATE items SET status='queued',lease_until=NULL WHERE status IN ('downloading','sending') AND lease_until < ?", (time.time(),))

def create_job(url, username):
    jid=uuid.uuid4().hex; now=time.time()
    with connect() as c: c.execute("INSERT INTO jobs(id,profile_url,username,created_at,updated_at) VALUES(?,?,?,?,?)", (jid,url,username,now,now))
    return jid
def get_job(jid):
    with connect() as c: return c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
def active_job():
    with connect() as c: return c.execute("SELECT * FROM jobs WHERE status IN ('queued','running','cancelling') ORDER BY created_at DESC LIMIT 1").fetchone()
def cancel(jid):
    with connect() as c: c.execute("UPDATE jobs SET cancel_requested=1,status='cancelling',updated_at=? WHERE id=?",(time.time(),jid))
def cancelled(jid):
    with connect() as c:
        row=c.execute("SELECT cancel_requested FROM jobs WHERE id=?",(jid,)).fetchone(); return bool(row and row[0])
def job_status(jid,status,error=None):
    # SQLite accepts text, not exception objects such as requests.HTTPError.
    error_text = None if error is None else str(error)[:500]
    with connect() as c: c.execute("UPDATE jobs SET status=?,last_error=?,updated_at=? WHERE id=?",(status,error_text,time.time(),jid))
def add_items(jid, items):
    now=time.time(); count=0
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for x in items:
            cur=c.execute("INSERT OR IGNORE INTO items(job_id,source_key,page_url,direct_url,filename,media_type,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(jid,key(x['direct_url']),x['page_url'],x['direct_url'],x['filename'],x['media_type'],now,now)); count += cur.rowcount
        c.execute("UPDATE jobs SET discovered=discovered+?,updated_at=? WHERE id=?",(count,now,jid)); c.execute("COMMIT")
    return count
def save_cursor(jid,page,seek):
    with connect() as c: c.execute("UPDATE jobs SET page=?,seek=?,status='running',updated_at=? WHERE id=?",(page,seek,time.time(),jid))
def claim(jid):
    now=time.time(); lease=now+LEASE_SECONDS
    with connect() as c:
        c.execute("BEGIN IMMEDIATE"); row=c.execute("SELECT * FROM items WHERE job_id=? AND status IN ('queued','retryable') AND next_attempt_at<=? ORDER BY id LIMIT 1",(jid,now)).fetchone()
        if not row: c.execute("COMMIT"); return None
        c.execute("UPDATE items SET status='downloading',attempts=attempts+1,lease_until=?,updated_at=? WHERE id=?",(lease,now,row['id'])); c.execute("COMMIT")
        return c.execute("SELECT * FROM items WHERE id=?",(row['id'],)).fetchone()
def downloaded(item_id,path):
    with connect() as c: c.execute("UPDATE items SET status='sending',local_path=?,lease_until=?,updated_at=? WHERE id=? AND status='downloading'",(path,time.time()+LEASE_SECONDS,time.time(),item_id))
def sent(item_id,message_id=None):
    now=time.time()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE"); cur=c.execute("UPDATE items SET status='sent',telegram_message_id=?,sent_at=?,lease_until=NULL,updated_at=? WHERE id=? AND status='sending'",(message_id,now,now,item_id))
        if cur.rowcount:
            c.execute("UPDATE jobs SET sent=sent+1,updated_at=? WHERE id=(SELECT job_id FROM items WHERE id=?)",(now,item_id)); c.execute("UPDATE stats SET total_sent=total_sent+1 WHERE id=1")
        c.execute("COMMIT"); return bool(cur.rowcount)
def skipped(item_id):
    now=time.time()
    with connect() as c:
        cur=c.execute("UPDATE items SET status='skipped',updated_at=? WHERE id=? AND status IN ('queued','retryable')",(now,item_id))
        if cur.rowcount: c.execute("UPDATE jobs SET skipped=skipped+1,updated_at=? WHERE id=(SELECT job_id FROM items WHERE id=?)",(now,item_id)); c.execute("UPDATE stats SET total_skipped=total_skipped+1 WHERE id=1")
def failure(item_id,error,retryable):
    now=time.time(); status='retryable' if retryable else 'failed'; delay=60
    with connect() as c:
        c.execute("UPDATE items SET status=?,last_error=?,next_attempt_at=?,lease_until=NULL,updated_at=? WHERE id=?",(status,str(error)[:500],now+delay if retryable else 0,now,item_id))
        if not retryable: c.execute("UPDATE jobs SET failed=failed+1,updated_at=? WHERE id=(SELECT job_id FROM items WHERE id=?)",(now,item_id)); c.execute("UPDATE stats SET total_failed=total_failed+1 WHERE id=1")
def pending(jid):
    with connect() as c: return c.execute("SELECT COUNT(*) FROM items WHERE job_id=? AND status IN ('queued','retryable','downloading','sending')",(jid,)).fetchone()[0]
def stats():
    with connect() as c:
        row=c.execute("SELECT total_sent,total_skipped,total_failed FROM stats WHERE id=1").fetchone(); return dict(row) if row else {}
