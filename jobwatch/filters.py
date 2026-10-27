"""Rule-based screening, no AI. Title lane, location, pay floors and freshness, all set in candidate.json.

Cheap checks run first (screen) on every listing; checks that need the full posting (pay_and_competition,
engineering_role, pm_domain) run again once the description has been fetched.
"""

import re
from datetime import datetime, timedelta

from . import settings as S
from .util import now_utc

R = S.SEARCH

# A design role must mention one of these.
LANE = re.compile(
    r"product design|\bux\b|ui\s*/\s*ux|ux\s*/\s*ui|user experience|interaction design|"
    r"experience design|design lead|lead designer|founding designer|staff designer|principal designer|"
    r"ux research|user research|design research|product research|"
    r"design engineer|ux engineer|ai designer|"
    r"design director|director of design|head of design|head of product design|director,? (?:of )?(?:product |ux )?design",
    re.I,
)
# Different disciplines that merely sound like design.
WRONG_LANE = re.compile(
    r"(?<!design )(?<!ux )engineer|developer|technologist|brand|graphic|motion|visual designer|marketing|content design|"
    r"ux writ|copywrit|web designer|service design|design ops|program manager|product manager|"
    r"industrial|hardware|game|level designer|instructional|interior|fashion|architect|"
    r"intern\b|internship|junior|\bjr\b|associate(?! director)|apprentice|manager|\bvp\b|"
    r"vice president|relocat|business analyst|creative director|art director|"
    # "Design engineer" means product/UX design engineers, not building trades or chip design.
    r"hvac|stormwater|civil|mechanical|electrical|structural|plumbing|mep|piping|geotech|transportation|"
    r"water resources|wastewater|electronic|analog|mixed.signal|silicon|photonic|\bic\b|\basic\b|fpga|\brf\b|"
    r"semiconductor|circuit|optical|\bpcb\b",
    re.I,
)
# Product Manager roles, when include_pm_roles is on. Not program/project/marketing PMs, not people-manager levels.
PM_LANE = re.compile(r"\bproduct manager\b", re.I)
PM_WRONG_LANE = re.compile(
    r"program manager|project manager|marketing|hardware|game|group product manager|principal|staff|"
    r"intern\b|internship|junior|\bjr\b|associate|apprentice|director|head of|\bvp\b|vice president|relocat",
    re.I,
)
SENIOR_PLUS = re.compile(r"senior|\bsr\.?\b|lead|principal|staff|founding|founder|director|head of|\biii\b|\biv\b", re.I)

HOME = re.compile(R["home_metro"], re.I)
FAR_COMMUTE = re.compile(R["far_commute"], re.I) if R.get("far_commute") else None
REMOTE = re.compile(r"remote|anywhere|distributed|work from home|wfh", re.I)
# A remote role is only usable if it's open to the US or worldwide; "Remote, Poland" isn't.
US_OR_ANYWHERE = re.compile(
    r"united states|\busa?\b|u\.s\.|\bamericas?\b|north america|anywhere|worldwide|global|"
    r",\s*(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|"
    r"S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b|california|texas|florida|colorado|washington|illinois|"
    r"massachusetts|georgia|san francisco|seattle|austin|chicago|boston|denver|los angeles",
    re.I,
)
NON_US = re.compile(
    r"(?:,\s*(?:BC|ON|QC|AB|MB|SK|NS|NB|NL|PE)\b)+(?:,\s*CA\b)?|"  # "Toronto, ON, CA" isn't California
    r"canada|\bcanadian\b|ontario|quebec|british columbia|alberta|vancouver|toronto|montreal|calgary|ottawa|"
    r"mexico|brazil|argentina|colombia|chile|peru|costa rica|guatemala|latam|latin america|south america|"
    r"united kingdom|\buk\b|england|scotland|ireland|london|europe|\beu\b|\bemea\b|germany|berlin|france|"
    r"paris|spain|portugal|netherlands|amsterdam|belgium|switzerland|austria|italy|poland|ukraine|romania|"
    r"czech|hungary|greece|turkey|sweden|norway|denmark|finland|estonia|israel|egypt|nigeria|kenya|"
    r"south africa|india|pakistan|philippines|vietnam|indonesia|malaysia|singapore|thailand|china|japan|"
    r"korea|taiwan|australia|new zealand|\bapac\b|asia|dubai|\buae\b",
    re.I,
)
BLOCKED = [c.lower() for c in R.get("blocked_companies", [])]


def location_ok(loc: str, remote: bool) -> tuple[bool, str]:
    if HOME.search(loc):
        return True, "home metro"
    if not remote:
        return False, f"location not remote or home metro: {loc or 'unknown'}"
    # Bare "Remote" stays in. Remote abroad stays in only with listed pay at the floor (pay_and_competition).
    if is_foreign_remote(loc, remote):
        return True, "remote (foreign, pay must be listed)"
    return True, "remote"


def is_foreign_remote(loc: str, remote: bool) -> bool:
    return bool(remote and not HOME.search(loc or "") and NON_US.search(loc or "") and not US_OR_ANYWHERE.search(
        re.sub(r"anywhere in", "", NON_US.sub("", loc or ""), flags=re.I)))


def is_pm(title: str) -> bool:
    return bool(R.get("include_pm_roles") and PM_LANE.search(title or ""))


def submit_floor(title: str) -> int:
    """Lowest keyword match the gate accepts for this title."""
    return S.PM_SUBMIT_MIN_MATCH if is_pm(title) else S.SUBMIT_MIN_MATCH


def level(title: str) -> str:
    return "senior+" if SENIOR_PLUS.search(title or "") else "below-senior"


def screen(job: dict) -> tuple[bool, str]:
    """Return (keep, reason). Cheap checks only; runs before any detail fetch."""
    title = job.get("title") or ""
    if (job.get("company") or "").strip().lower() in BLOCKED:
        return False, f"blocked company: {job.get('company')}"
    if is_pm(title):
        if PM_WRONG_LANE.search(title):
            return False, f"different PM type or level: {title}"
    elif not LANE.search(title):
        return False, "not a product/UX design title"
    elif WRONG_LANE.search(title):
        return False, f"different discipline or level: {title}"

    loc = job.get("location") or ""
    remote = job.get("remote")
    if remote is None:
        remote = bool(REMOTE.search(loc) or REMOTE.search(title))
    # Titles often carry the region ("Remote, Latin America") when the location field is blank.
    ok, why = location_ok(f"{loc} {title}".strip(), remote)
    if not ok:
        return False, why

    posted = job.get("posted_at")
    if posted:
        try:
            age = now_utc() - datetime.fromisoformat(posted.replace("Z", "+00:00"))
            if age > timedelta(hours=R["max_age_hours"]):
                return False, f"posted {age.days}d ago"
        except ValueError:
            pass
    return pay_and_competition(job)


def pay_and_competition(job: dict) -> tuple[bool, str]:
    """Checks that may need the detail page (salary, applicant count)."""
    if job.get("hand_picked"):  # the candidate chose this one; their call overrides pay and level
        return True, "hand picked"
    hi = job.get("salary_max")
    floor = R["absolute_min_pay"]
    if is_foreign_remote(job.get("location") or "", bool(job.get("remote"))) and (not hi or hi < floor):
        return False, "foreign remote without listed pay at the floor"
    if hi and hi < floor:
        return False, f"pay tops out at ${hi:,}"
    if (FAR_COMMUTE and FAR_COMMUTE.search(job.get("location") or "") and not job.get("remote")
            and hi and hi < R["far_commute_min_pay"]):
        return False, f"long commute needs ${R['far_commute_min_pay']:,}+, pay tops out at ${hi:,}"
    if level(job.get("title", "")) == "below-senior":
        if not hi:
            return False, f"below-Senior title with no listed pay (needs ${R['below_senior_min_pay']:,}+)"
        if hi < R["below_senior_min_pay"]:
            return False, f"below-Senior title paying ${hi:,}"

    applicants = job.get("applicants")
    if applicants is not None and applicants >= R["max_applicants"]:
        return False, f"{applicants}+ applicants"
    return True, "ok"


# Engineering jobs wearing a PM/design title.
ENGINEERING_REQS = re.compile(
    r"production[- ]software engineering|experience in software engineering|years of (?:professional )?"
    r"(?:software|backend|full[- ]stack) (?:engineering|development)|comfortable (?:examining|reading|writing) code|"
    r"(?:bachelor'?s|degree) (?:degree )?in computer science,? (?:or )?(?:software )?engineering(?! or (?:design|hci))",
    re.I,
)

# PM roles only where a designer's work transfers. Edit to match your own domains.
PM_DOMAIN = re.compile(
    r"payments?\b|billing|fintech|financial services|banking|lending|invoic|checkout|wallet|"
    r"health|clinic|patient|\bai\b|artificial intelligence|machine learning|\bllms?\b|generative|workflow",
    re.I,
)


def pm_domain(title: str, description: str) -> str:
    """'' if a PM role is in the candidate's domains, else why not. Design roles always pass."""
    if not is_pm(title) or PM_DOMAIN.search(title):
        return ""
    hits = {m.group(0).lower() for m in PM_DOMAIN.finditer(description or "")}
    return "" if len(hits) >= 3 else f"PM role outside your domains ({', '.join(sorted(hits)) or 'no domain match'})"


def engineering_role(description: str) -> str:
    """Why a posting is really an engineering job, or '' if it isn't."""
    hits = {m.group(0).lower() for m in ENGINEERING_REQS.finditer(description or "")}
    return f"engineering role in disguise: {', '.join(sorted(hits))[:120]}" if len(hits) >= 2 else ""


def priority(job: dict) -> float:
    """Higher = review sooner. Newest posting first, always: early applications get read.
    The score is the posting time in hours since 1970, so ordering never goes stale as jobs age; pay,
    competition, level and location only break ties between jobs posted in the same hour."""
    posted_at = None
    if job.get("posted_at"):
        try:
            posted_at = datetime.fromisoformat(job["posted_at"].replace("Z", "+00:00"))
        except ValueError:
            pass
    if posted_at is None:
        posted_at = now_utc() - timedelta(hours=12)  # unknown age: behind anything posted in the last 12h
    score = posted_at.timestamp() / 3600
    tie = 0.0
    if (job.get("salary_max") or 0) >= R["preferred_pay"]:
        tie += 0.4
    a = job.get("applicants")
    if a is not None:
        tie += max(0.0, 0.3 - a / 400)
    if level(job.get("title", "")) == "senior+":
        tie += 0.2
    loc = job.get("location") or ""
    if HOME.search(loc) or US_OR_ANYWHERE.search(loc) or not re.sub(r"[\W_]+", "", REMOTE.sub("", loc)):
        tie += 0.09  # home / US / worldwide ahead of country-locked remote
    return round(score + min(tie, 0.99), 2)
