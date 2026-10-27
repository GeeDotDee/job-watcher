"""SQLite job store. Remembers every listing ever seen so each scan only does new work.

Status flow:  candidate -> tailoring -> ready -> applied
              (or needs_you / rejected / skipped / error at any point)
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import settings as S

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT,
  url_key TEXT DEFAULT '',
  status TEXT NOT NULL,
  reason TEXT DEFAULT '',
  priority REAL DEFAULT 0,
  data TEXT NOT NULL,
  package TEXT DEFAULT '',
  tailored_at TEXT DEFAULT '',
  clean INTEGER DEFAULT 0,          -- 1 = match at the floor and the package agrees with the master resume
  first_seen TEXT NOT NULL,
  updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS jobs_url ON jobs(url_key);
"""


@contextmanager
def db(path=None):
    con = sqlite3.connect(path or S.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def dedupe_key(job: dict) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", re.sub(r"\(.*?\)", "", (s or "").lower())).strip()
    company = re.sub(r"\b(inc|llc|ltd|corp|co)\b", "", norm(job.get("company")))
    return f"{company.strip()}|{norm(job.get('title'))}"


def url_key(job: dict) -> str:
    """Same posting reached via different boards: compare the ATS link without query/scheme."""
    u = (job.get("apply_url") or job.get("url") or "").lower()
    u = re.sub(r"^https?://(www\.)?", "", u.split("?")[0].split("#")[0]).rstrip("/")
    return re.sub(r"^(job-boards|boards)\.greenhouse\.io", "greenhouse.io", u)


def is_known(con, job: dict) -> bool:
    # A same-company/same-title match only blocks a NEW posting when the old one is recent or still live.
    # An old rejected row must not hide a fresh repost that may now pass (with pay listed this time, say).
    cutoff = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT 1 FROM jobs WHERE id=? OR (url_key=? AND url_key != '') OR "
        "(dedupe_key=? AND dedupe_key NOT LIKE '|%' AND NOT (status IN ('rejected','skipped') AND first_seen < ?)) "
        "LIMIT 1", (job["id"], url_key(job), dedupe_key(job), cutoff)).fetchone()
    return row is not None


def add(con, job: dict, status: str, reason: str = "", priority: float = 0):
    now = datetime.now().isoformat(timespec="seconds")
    con.execute(
        "INSERT OR IGNORE INTO jobs (id, dedupe_key, url_key, status, reason, priority, data, first_seen, updated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (job["id"], dedupe_key(job), url_key(job), status, reason, priority, json.dumps(job), now, now),
    )


def update(con, job_id: str, only_if_status: str | None = None, **fields):
    """only_if_status: change the row only if it is still in that status, so a slow step (tailoring takes minutes)
    can't revive a job that a person or a filter skipped in the meantime."""
    if "data" in fields and not isinstance(fields["data"], str):
        fields["data"] = json.dumps(fields["data"])
    fields["updated"] = datetime.now().isoformat(timespec="seconds")
    cols = ", ".join(f"{k}=?" for k in fields)
    if only_if_status:
        con.execute(f"UPDATE jobs SET {cols} WHERE id=? AND status=?", (*fields.values(), job_id, only_if_status))
    else:
        con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def get(con, job_id: str):
    r = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row(r) if r else None


def by_status(con, *statuses, limit=500) -> list[dict]:
    q = f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY priority DESC LIMIT ?"
    return [_row(r) for r in con.execute(q, (*statuses, limit))]


def tailored_today(con) -> int:
    today = datetime.now().date().isoformat()
    return con.execute("SELECT COUNT(*) FROM jobs WHERE tailored_at LIKE ?", (today + "%",)).fetchone()[0]


def _row(r) -> dict:
    d = dict(r)
    d["job"] = json.loads(d.pop("data"))
    return d


def purge_rejected_packages() -> int:
    """Delete the package folder of any job that was rejected or skipped, to free disk space."""
    import shutil
    from pathlib import Path
    n = 0
    with db() as con:
        rows = con.execute("SELECT id, package FROM jobs WHERE package != '' AND status IN ('rejected', 'skipped')"
                           ).fetchall()
        for job_id, pkg in rows:
            p = Path(pkg)
            if p.is_dir() and p.resolve().parent == S.PACKAGES_DIR.resolve():
                shutil.rmtree(p, ignore_errors=True)
                n += 1
            con.execute("UPDATE jobs SET package = '' WHERE id = ?", (job_id,))
    return n
