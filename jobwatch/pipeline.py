"""One scan: fetch all sources -> screen -> tailor the best few -> queue for review."""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

from . import filters, sources, store, tailor
from . import settings as S
from .util import parse_salary

log = logging.getLogger("jobwatch.pipeline")


class ScanLock:
    """Skip a scan if the previous one (slow tailoring, say) is still running."""

    def __enter__(self):
        if S.LOCK_PATH.exists() and time.time() - S.LOCK_PATH.stat().st_mtime < 3600 and self._owner_alive():
            raise RuntimeError("previous scan still running")
        S.LOCK_PATH.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        S.LOCK_PATH.unlink(missing_ok=True)

    @staticmethod
    def _owner_alive() -> bool:
        """A scan that crashed leaves its lock behind, which used to block every scan for an hour.
        Only honor the lock while the process that wrote it still exists."""
        try:
            pid = int(S.LOCK_PATH.read_text().strip())
            if os.name == "nt":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                                     timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                return str(pid) in out
            os.kill(pid, 0)  # signal 0 only checks that the process exists
            return True
        except ProcessLookupError:
            return False
        except Exception:
            return True  # can't tell: keep the lock


def fetch_all(demo: bool = False) -> list[dict]:
    if demo:
        return sources.fixtures()
    jobs = []
    for name, fn in sources.ALL.items():
        t = time.time()
        try:
            got = fn()
        except Exception as e:
            log.warning(f"{name} failed: {e}")
            got = []
        log.info(f"  {name}: {len(got)} listings ({time.time() - t:.1f}s)")
        jobs += got
    return jobs


def scan(tailor_limit: int | None = None, demo: bool = False) -> dict:
    with ScanLock():
        log.info("=" * 60 + f"\nSCAN {datetime.now():%Y-%m-%d %H:%M}{' (demo postings)' if demo else ''}")
        jobs = fetch_all(demo)
        new_candidates = 0
        with store.db() as con:
            for j in jobs:
                if not j.get("title") or store.is_known(con, j):
                    continue
                keep, why = filters.screen(j)
                if keep:
                    store.add(con, j, "candidate", priority=filters.priority(j))
                    new_candidates += 1
                    log.info(f"  + {j['title']} @ {j['company']} [{j['source']}] {j.get('location', '')}")
                else:
                    store.add(con, j, "rejected", reason=why)
                    log.info(f"  - {j['title']} @ {j['company']}: {why}")

        ready = tailor_batch(tailor_limit if tailor_limit is not None else S.TAILOR_PER_SCAN)
        try:
            gone = store.purge_rejected_packages()
            if gone:
                log.info(f"  deleted {gone} rejected job package(s)")
        except Exception as e:
            log.warning(f"package cleanup failed: {e}")
        log.info(f"SCAN DONE: {len(jobs)} listings, {new_candidates} new candidates, {len(ready)} packages built")
        return {"listings": len(jobs), "new_candidates": new_candidates, "packages": len(ready)}


PAUSE_PATH = S.DATA_DIR / "claude_paused_until.txt"
LIMIT_MSG = re.compile(r"hit your (session|usage|weekly) limit|rate limit|usage limit", re.I)
PAY_RANGE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?\s*(?:-|–|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?"
                       r"(?:\s*/\s*h(?:ou)?r)?")


def claude_paused() -> bool:
    try:
        return datetime.now() < datetime.fromisoformat(PAUSE_PATH.read_text().strip())
    except (OSError, ValueError):
        return False


def pause_claude(msg: str):
    """Claude Code shares the person's subscription; when it's out of usage, wait for the reset it reports."""
    until = datetime.now() + timedelta(hours=1)
    m = re.search(r"resets\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)", msg, re.I)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        t = datetime.now().replace(hour=h, minute=int(m.group(2) or 0), second=0, microsecond=0)
        until = t if t > datetime.now() else t + timedelta(days=1)
    PAUSE_PATH.write_text(until.isoformat(timespec="minutes"))
    log.warning(f"Claude usage limit reached; tailoring paused until {until:%a %I:%M %p}, jobs stay queued")


def tailor_batch(limit: int) -> list[dict]:
    ready = []
    if claude_paused():
        log.info(f"Tailoring paused (Claude usage limit) until {PAUSE_PATH.read_text().strip()}")
        return ready
    with store.db() as con:
        room = max(0, min(limit, S.TAILOR_PER_DAY - store.tailored_today(con)))
        todo = store.by_status(con, "candidate", limit=room) if room else []
    for row in todo:
        j = row["job"]
        with store.db() as con:
            store.update(con, j["id"], status="tailoring")
        try:
            desc = sources.fetch_description(j)
            if len(desc) < 300:
                raise ValueError("could not read the job description")
            why = filters.engineering_role(desc) or filters.pm_domain(j["title"], desc)
            if why:
                with store.db() as con:
                    store.update(con, j["id"], only_if_status="tailoring", status="rejected", reason=why, data=j)
                continue
            # Re-check pay now that the full posting is known.
            if not j.get("salary_max"):
                m = PAY_RANGE.search(desc)
                if m:
                    j["salary_text"] = m.group(0)
                    j["salary_min"], j["salary_max"] = parse_salary(m.group(0))
            keep, why = filters.pay_and_competition(j)
            if not keep:
                with store.db() as con:
                    store.update(con, j["id"], only_if_status="tailoring", status="rejected", reason=why, data=j)
                continue
            pkg = tailor.build_package(j, desc)
            meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
            clean = int(meta["match"]["match_pct"] >= filters.submit_floor(j["title"])
                        and not meta["consistency"]["issues"])
            with store.db() as con:
                store.update(con, j["id"], only_if_status="tailoring", status="ready", package=str(pkg), data=j,
                             clean=clean, tailored_at=datetime.now().isoformat(timespec="seconds"))
            ready.append(meta)
        except tailor.LowMatch as e:
            with store.db() as con:
                store.update(con, j["id"], only_if_status="tailoring", status="rejected", reason=str(e)[:300], data=j)
        except Exception as e:
            if LIMIT_MSG.search(str(e)):
                with store.db() as con:
                    store.update(con, j["id"], only_if_status="tailoring", status="candidate")  # back in line
                pause_claude(str(e))
                break
            log.error(f"Tailoring failed for {j['title']} @ {j['company']}: {e}")
            with store.db() as con:
                store.update(con, j["id"], only_if_status="tailoring", status="error", reason=str(e)[:300])
    return ready
