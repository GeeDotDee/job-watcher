"""Truth checks against the master resume and the candidate's standing answers (candidate.json).

* consistency(): runs after tailoring and again before any application goes out. The tailored resume and every
  written answer must agree with the master on years, employers, titles, dates, current status, contact info
  and pay. Safe mechanical fixes are applied; anything else is returned as an issue that blocks the gate.
* readback(): runs on what a live form actually holds right before Submit (every visible field and attachment,
  collected by whatever drives the browser) and compares it to the candidate's real info, so stale autofill
  from an old account profile or an old resume on file never goes out.
"""

import json
import re
from pathlib import Path

from . import settings as S

C = S.CANDIDATE
N = C["name"]
EMPLOYERS = C["employers"]
PROJECTS = C.get("projects", {})
YEARS = C["years_experience"]
TITLES = {t.lower() for t in C["titles_held"]}
PHONE_DIGITS = re.sub(r"\D", "", C["phone"])
PAY_FLOOR = S.SEARCH["absolute_min_pay"]

_emp = "|".join(re.escape(e) for e in EMPLOYERS)
# "I currently work at X", "in my current role", "my current employer": only a problem if they aren't employed.
CURRENT_JOB = re.compile(
    r"\b(I am|I'm|I)\s+(currently|presently|now)\s+(work|working|employed|serving|leading)\b|"
    r"\bmy (current|present) (role|job|position|employer|company|team)\b|"
    rf"\b(currently|presently) (at|with) ({_emp})\b|"
    rf"\bat ({_emp}),? where I (am|lead|work|currently)\b", re.I)
YEARS_CLAIM = re.compile(r"\b(\d{1,2})\+?\s*(?:or more\s+)?years?(?:\s+of)?(?=\s+(?:\w+\s+){0,3}"
                         r"(?:experience|designing|design|in (?:product|ux|design)))", re.I)
MONEY = re.compile(r"\$\s?(\d{2,3})(?:,(\d{3})|\s?[kK])")


def master_text() -> str:
    return S.MASTER_RESUME.read_text(encoding="utf-8")


def _year(s: str) -> int:
    return int(re.search(r"\d{4}", s).group(0))


def consistency(resume: str, answers: dict, master: str | None = None) -> tuple[str, dict, list[str], list[str]]:
    """Returns (fixed_resume, fixed_answers, fixes_made, blocking_issues)."""
    fixes, issues = [], []
    master = master if master is not None else master_text()

    # ── resume: header/contact and employer blocks must match the master exactly ──
    lines = resume.splitlines()
    head = "\n".join(lines[:3])
    if not lines or N["full"] not in lines[0]:
        issues.append(f"resume header name isn't '{N['full']}'")
    for want in (C["email"], C["location"]):
        if want not in head:
            issues.append(f"resume contact line is missing {want}")
    for emp, e in EMPLOYERS.items():
        if emp not in master:
            continue  # master changed; nothing to compare against
        m = re.search(rf"^{re.escape(emp)}\b.*\n(.+)$", resume, re.M)
        if not m:
            issues.append(f"resume is missing employer {emp}")
            continue
        line = m.group(1)
        good = f"{e['start']} - {e['end']}"
        if good not in line:
            # Put the date range back from the master if Claude reworded it.
            fixed_line = re.sub(r"\|\s*[^|]*\d{4}[^|]*$", f"| {good}", line)
            if good in fixed_line:
                resume = resume.replace(line, fixed_line, 1)
                fixes.append(f"{emp} dates reset to {good}")
                line = fixed_line
            else:
                issues.append(f"{emp} dates on resume don't match master ({good})")
        if not line.lower().startswith(e["title"].lower()):
            issues.append(f"{emp} title on resume isn't '{e['title']}'")

    for name, good in PROJECTS.items():
        for line in re.findall(rf"^{re.escape(name)}\b[^\n|]*\|[^\n]*$", resume, re.M):
            if good not in line:
                fixed_line = re.sub(r"\|[^|]*$", f"| {good}", line)
                resume = resume.replace(line, fixed_line, 1)
                fixes.append(f"{name} date reset to {good}")

    # ── years of experience everywhere (resume + answers) ──
    def fix_years(text: str, where: str) -> str:
        def sub(m):
            if int(m.group(1)) == YEARS:
                return m.group(0)
            fixes.append(f"{where}: '{m.group(0)}' -> '{YEARS}+ years'")
            return re.sub(r"\d{1,2}\+?\s*(or more\s+)?years?", f"{YEARS}+ years", m.group(0), count=1)
        return YEARS_CLAIM.sub(sub, text)

    resume = fix_years(resume, "resume")
    answers = {k: fix_years(v or "", k) for k, v in (answers or {}).items()}

    # ── written answers: current status, employer dates, pay, jobs that aren't on the resume ──
    for key, text in answers.items():
        if not text:
            continue
        if not C.get("currently_employed") and CURRENT_JOB.search(text):
            issues.append(f"{key} says they're currently employed ('{CURRENT_JOB.search(text).group(0)}'), "
                          "and they aren't")
        for emp, e in EMPLOYERS.items():
            for m in re.finditer(rf"{re.escape(emp)}[^.]{{0,60}}?\b((?:19|20)\d\d)\b", text):
                y = int(m.group(1))
                if not _year(e["start"]) <= y <= _year(e["end"]):
                    issues.append(f"{key} puts {emp} in {y} (master: {e['start']} to {e['end']})")
            if re.search(rf"{re.escape(emp)}[^.]{{0,40}}\b(since|to|through)\s+(today|now|present)", text, re.I):
                issues.append(f"{key} says {emp} runs to the present (ended {e['end']})")
        for m in MONEY.finditer(text):
            amount = int(m.group(1)) * 1000 + (int(m.group(2)) % 1000 if m.group(2) else 0)
            if 1000 <= amount < PAY_FLOOR and re.search(r"salary|pay|compensation|base|expect",
                                                         text[max(0, m.start() - 80):m.end() + 40], re.I):
                issues.append(f"{key} names pay ${amount:,}, under the ${PAY_FLOOR:,} floor")
        for emp in C.get("former_jobs_not_on_resume", []):
            if emp in text and emp not in master:
                issues.append(f"{key} mentions {emp}, which isn't on the current master resume")
    return resume, answers, fixes, list(dict.fromkeys(issues))


def check_package(pkg: Path) -> list[str]:
    """Re-run the consistency check on a saved package. Any fix or issue means the package is out of date."""
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    resume = (pkg / "resume.txt").read_text(encoding="utf-8")
    _, _, fixes, issues = consistency(resume, meta.get("answers", {}))
    if fixes and not issues:
        issues = [f"package needs re-tailoring, it disagrees with the master: {'; '.join(fixes[:3])}"]
    return issues


# ───────────────────────── live-form readback ─────────────────────────
# A field is {"label": str, "value": str, "type": "text" | "file" | "select" | ...}. Whatever drives the browser
# collects them; this module only judges them, so it can be tested without a browser.

LEGAL = re.compile(r"legal", re.I)


def expected_name(label: str):
    """What a name field must say. Legal-name fields get the legal name; everything else the preferred one."""
    l = label.lower()
    if re.search(r"middle", l) or re.search(r"(user|company|employer|school|reference|emergency|\breferr|manager)", l):
        return None
    legal = bool(LEGAL.search(l))
    if re.search(r"last|surname|family", l):
        return N["last"]
    if re.search(r"first|given|preferred", l):
        return N["legal_first"] if legal and "preferred" not in l else N["first"]
    if re.search(r"(^|\b)(full\s*)?name\b|your name|signature", l):
        return N["legal_full"] if legal else N["full"]
    return None


def readback(fields: list[dict], page_text: str = "", our_files: set | None = None) -> list[str]:
    """Every problem with what the form holds right now. Empty list = it matches the candidate."""
    problems = []
    ours = our_files or {S.resume_filename()}
    allowed_emp = [e.lower() for e in EMPLOYERS] + [p.lower() for p in PROJECTS]
    yes_auth = "Yes" if C["standing_answers"]["authorized_to_work"] else "No"
    yes_sponsor = "Yes" if C["standing_answers"]["needs_sponsorship"] else "No"
    for f in fields:
        lab, val, typ = f.get("label", ""), (f.get("value") or "").strip(), f.get("type", "text")
        head = lab.split("|")[0].lower()
        if not val or typ in ("checkbox", "radio", "password"):
            continue
        if typ == "file":
            if not {v.strip() for v in val.split(",")} <= ours:
                problems.append(f"attachment '{val}' isn't this package's tailored resume")
            continue
        # Standing yes/no answers that must never flip.
        if re.search(r"authori[sz]ed to work|legally (authorized|eligible)|right to work|eligible to work", head) \
                and not re.search(r"\b(require|need)\b.{0,80}(sponsor|visa)", head):
            if not val.lower().startswith(yes_auth.lower()):
                problems.append(f"'{lab[:60]}' answered '{val}', should be {yes_auth}")
            continue
        if re.search(r"sponsor|visa|h-?1b", head):
            if not val.lower().startswith(yes_sponsor.lower()):
                problems.append(f"'{lab[:60]}' answered '{val}', should be {yes_sponsor}")
            continue
        if re.search(r"pronoun", head) and val.lower() != C["pronouns"].lower():
            problems.append(f"'{lab[:60]}' says '{val}', should be {C['pronouns']}")
            continue
        if re.search(r"e-?mail", head) and "@" in val and val.lower() != C["email"].lower():
            problems.append(f"'{lab[:60]}' shows email {val}, not {C['email']}")
        elif re.search(r"phone|mobile", head) and re.search(r"\d{7}", re.sub(r"\D", "", val)):
            if not re.sub(r"\D", "", val).endswith(PHONE_DIGITS):
                problems.append(f"'{lab[:60]}' shows phone {val}, not {C['phone']}")
        elif "name" in head and (want := expected_name(head)):
            if val.lower() != want.lower():
                problems.append(f"'{lab[:60]}' says '{val}', should be '{want}'")
        elif re.search(r"\b(employer|company)\b", head) and not re.search(r"how did|hear|refer", head):
            if val.lower() not in ("not currently employed", "n/a", "none") and \
                    not any(e in val.lower() for e in allowed_emp):
                problems.append(f"'{lab[:60]}' lists employer '{val}', not on the resume")
        elif re.search(r"job title|\btitle\b", head) and len(val) < 60:
            if val.lower() not in TITLES:
                problems.append(f"'{lab[:60]}' lists title '{val}', not a title they've held")
        elif re.search(r"\bcity\b|location", head) and not re.search(r"relocat|willing|prefer", head):
            if not any(w in val.lower() for w in C["location_words"]):
                problems.append(f"'{lab[:60]}' says '{val}', not {C['location']}")

    # Old resumes some sites keep on the account and show next to the upload box.
    for fname in set(re.findall(r"\b\w[\w\- ().,']{0,79}\.(?:pdf|docx?)\b", page_text or "", re.I)):
        name = fname.strip()
        # Case-insensitive (some ATSs echo uploads in caps), and truncated display names ("Sam_Exa...pdf") count.
        stub = name.lower().split("...")[0] if "..." in name else None
        if stub and any(o.lower().startswith(stub[-12:]) or stub[-12:] in o.lower() for o in ours):
            continue
        if any(name.lower().endswith(o.lower()) for o in ours) or len(name) < 6:
            continue
        if not re.search(r"(accepted|supported|formats?|types?|upload|drag|drop)\b", name, re.I):
            problems.append(f"page shows another attached file '{name}'; remove it so only the tailored resume goes")
    return list(dict.fromkeys(problems))
