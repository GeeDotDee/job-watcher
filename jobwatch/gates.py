"""The gate between "form filled" and "Submit". Fails closed: any doubt sends the job to a person.

A FormSnapshot is what a browser driver saw on the filled form. This repo doesn't include the driver; it includes
the decision, so the rules can be read and tested on their own:

    {
      "url": "...",
      "fields": [{"label": "...", "value": "...", "type": "text"}],   # every visible field + file inputs
      "page_text": "...",                                            # visible text, for old attachments
      "checked_options": ["Yes, I am authorized ...", ...],          # labels of checked boxes/radios/selected options
      "password_fields": ["Create a password", ...],                 # labels of password inputs
      "uploaded": true,                                              # the tailored resume is attached
      "flagged": ["question it couldn't answer", ...],
      "filled": 12,                                                  # fields it recognized and filled
      "visible_captcha": false,
      "submit_attempts": 0
    }

Account walls are the main human-in-the-loop stop: creating an account, signing in, or typing a verification
code is never automated. The job is marked needs_you, the reason names the wall, and a person takes it from there.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from . import filters, verify
from . import settings as S

MAX_SUBMIT_ATTEMPTS = 2  # never click Submit on the same job more than twice, ever
PORTFOLIO_CONTEXT = re.compile(r"portfolio|website|work samples?|case stud|personal site", re.I)
WALL_TEXT = re.compile(r"sign in to (apply|continue)|create (an |your )?account|log ?in to apply|"
                       r"verify your email|enter the (verification|security) code|already have an account", re.I)
NEEDS_SPONSOR = re.compile(r"^\s*yes\b.{0,60}(need|require).{0,40}sponsor|i (currently |will )?(need|require).{0,30}sponsor",
                           re.I)
NEGATED = re.compile(r"\b(do not|don't|not|no longer)\b", re.I)


def account_wall(snap: dict) -> str:
    """Why this form is behind an account wall, or ''. A portfolio-password question is not a wall."""
    logins = [l for l in snap.get("password_fields", []) if not PORTFOLIO_CONTEXT.search(l)]
    if logins:
        return f"site needs an account (password field: '{logins[0][:40]}')"
    m = WALL_TEXT.search(snap.get("page_text", "")[:5000])
    if m and snap.get("filled", 0) < 4:
        return f"site needs an account ('{m.group(0)}')"
    return ""


def submit_blockers(row: dict, snap: dict) -> str:
    """Every reason not to submit, first one wins. Empty string = clean."""
    job, pkg = row["job"], Path(row["package"])
    if snap.get("submit_attempts", 0) >= MAX_SUBMIT_ATTEMPTS:
        return f"already tried Submit {MAX_SUBMIT_ATTEMPTS} times; stopped"
    # A package built before the master resume last changed may say something the master no longer does.
    master_mtime = datetime.fromtimestamp(S.MASTER_RESUME.stat().st_mtime).isoformat(timespec="seconds")
    if (row.get("tailored_at") or "") < master_mtime:
        return "package was tailored from an older master resume; re-tailor first"
    try:
        match = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["match"]
    except (OSError, ValueError, KeyError):
        return "couldn't read the package's match score"
    floor = filters.submit_floor(job.get("title", ""))
    if match["match_pct"] < floor and not match.get("accepted_by_person"):
        missing = ", ".join(match.get("missing", [])[:8])
        return (f"keyword match {match['match_pct']}% is under {floor}%; missing: {missing}. "
                "Add any you truly have to extra_skills.txt, then re-tailor")
    # Account walls before anything else on the form: with no form behind the wall, every later check
    # would report something misleading like "resume not attached".
    wall = account_wall(snap)
    if wall:
        return "needs you: " + wall
    if not snap.get("uploaded"):
        return "resume not attached"
    if snap.get("flagged"):
        return "needs you: " + "; ".join(snap["flagged"][:5])
    if snap.get("filled", 0) < 4:
        return "too few fields recognized to trust this form"
    issues = verify.check_package(pkg)
    if issues:
        return "resume/answers disagree with the master resume: " + "; ".join(issues[:4])
    # Hard stop: no checked option may say the candidate needs sponsorship when they don't.
    if not S.CANDIDATE["standing_answers"]["needs_sponsorship"]:
        bad = [t for t in snap.get("checked_options", []) if NEEDS_SPONSOR.search(t) and not NEGATED.search(t)]
        if bad:
            return "form says they need visa sponsorship (they don't): " + "; ".join(bad[:2])
    problems = verify.readback(snap.get("fields", []), snap.get("page_text", ""))
    if problems:
        return "readback mismatch: " + "; ".join(problems[:5])
    # Only a VISIBLE challenge blocks here. An invisible one that fires at submit time shows up as
    # "no confirmation page", which also goes to a person.
    if snap.get("visible_captcha"):
        return "CAPTCHA challenge on the form"
    return ""
