#!/usr/bin/env python3
"""Job Watcher command line.

  python watch.py scan                 # fetch boards, screen, tailor up to TAILOR_PER_SCAN new matches
  python watch.py scan --demo          # same, on the made-up postings in examples/postings (no network)
  python watch.py scan --no-tailor     # screen only, no Claude usage
  python watch.py serve                # review page at http://127.0.0.1:8765
  python watch.py status               # counts by status, top of the queue
  python watch.py gate FORM.json PKG   # run the submit gate on a form snapshot + package folder
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from jobwatch import settings as S

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(S.LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("watch")


def status():
    from jobwatch import store
    with store.db() as con:
        for s, n in con.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY 2 DESC"):
            print(f"  {s:12} {n}")
        print("\nTop of the queue:")
        for r in store.by_status(con, "candidate", "ready", limit=15):
            j = r["job"]
            print(f"  [{r['status']:9}] {j['title'][:50]} @ {j['company'][:24]} | {j.get('location', '')[:28]}")


def gate(form: Path, pkg: Path):
    from jobwatch import gates
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    row = {"job": meta["job"], "package": str(pkg),
           "tailored_at": meta.get("tailored_at") or datetime.now().isoformat(timespec="seconds")}
    why = gates.submit_blockers(row, json.loads(form.read_text(encoding="utf-8")))
    print(f"{form.name}: " + (f"STOP, {why}" if why else "clean, a person can press Submit"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "serve", "status", "gate"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-tailor", action="store_true")
    a = ap.parse_args()
    if a.cmd == "scan":
        from jobwatch import pipeline
        try:
            pipeline.scan(tailor_limit=0 if a.no_tailor else None, demo=a.demo)
        except RuntimeError as e:
            log.info(f"Scan skipped: {e}")
    elif a.cmd == "serve":
        from jobwatch import review
        review.serve()
    elif a.cmd == "gate":
        if len(a.args) != 2:
            ap.error("gate needs FORM.json and a package folder")
        gate(Path(a.args[0]), Path(a.args[1]))
    else:
        status()


if __name__ == "__main__":
    main()
