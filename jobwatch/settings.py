"""All knobs in one place. Override any of them in .env; personal facts live in candidate.json, not here."""

import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name, default):
    return int(os.getenv(name, default))


# ── The candidate: everything true about the person lives in one file ──
# candidate.json at the repo root if you made one, otherwise the made-up example candidate.
CANDIDATE_PATH = Path(os.getenv("CANDIDATE_FILE") or (ROOT / "candidate.json"))
if not CANDIDATE_PATH.exists():
    CANDIDATE_PATH = ROOT / "examples" / "candidate.json"
CANDIDATE = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
SEARCH = CANDIDATE["search"]

MASTER_RESUME = ROOT / CANDIDATE["files"]["master_resume"]
# Real skills that aren't on the master resume. Tailoring may use these and nothing else.
EXTRA_SKILLS = ROOT / CANDIDATE["files"].get("extra_skills", "extra_skills.txt")
VOICE = ROOT / CANDIDATE["files"].get("voice", "voice.txt")

# ── Throughput ──
SCAN_INTERVAL_MINUTES = _int("SCAN_INTERVAL_MINUTES", 15)
TAILOR_PER_SCAN = _int("TAILOR_PER_SCAN", 6)
TAILOR_PER_DAY = _int("TAILOR_PER_DAY", 40)

# ── Keyword match (ATS-style) ──
TARGET_MATCH = _int("TARGET_MATCH", 90)  # tailoring aims for this
SUBMIT_MIN_MATCH = _int("SUBMIT_MIN_MATCH", 75)  # below this the gate says no
# A PM role on a designer's resume needs a stronger match to be worth sending.
PM_SUBMIT_MIN_MATCH = _int("PM_SUBMIT_MIN_MATCH", 80)
MAX_BOOST_PASSES = _int("MAX_BOOST_PASSES", 4)
# Base resume vs posting keywords before any writing; below this a job is rejected with no Claude usage.
PRESCREEN_MIN = _int("PRESCREEN_MIN", 20)

# ── Claude Code (headless, runs on a Claude subscription, no API key in this repo) ──
CLAUDE_BIN = os.getenv("CLAUDE_BIN") or shutil.which("claude") or "claude"
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "sonnet")
KEYWORD_MODEL = os.getenv("KEYWORD_MODEL", "sonnet")

REVIEW_PORT = _int("REVIEW_PORT", 8765)

# ── Files ──
DATA_DIR = Path(os.getenv("DATA_DIR") or (ROOT / "data"))
DB_PATH = DATA_DIR / "jobs.sqlite3"
PACKAGES_DIR = DATA_DIR / "packages"
LOG_PATH = DATA_DIR / "watcher.log"
LOCK_PATH = DATA_DIR / "scan.lock"

for d in (DATA_DIR, PACKAGES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Sources ──
# Company career pages on public ATS APIs, as "ats:board-slug" (greenhouse, lever or ashby).
COMPANY_BOARDS = [s.strip() for s in os.getenv("COMPANY_BOARDS", "").split(",") if s.strip()]
# Offline demo: read postings from this folder instead of the web.
FIXTURE_DIR = ROOT / "examples" / "postings"


def resume_filename() -> str:
    return CANDIDATE["name"]["full"].replace(" ", "_") + "_Resume.pdf"
