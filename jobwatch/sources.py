"""Job sources. Every fetcher reads a public JSON API (no login, no browser) and returns normalized job dicts:

  id, source, title, company, location, remote, url, apply_url, posted_at (ISO UTC),
  salary_text, salary_min, salary_max, applicants, description

Add your own fetchers to ALL. Keep them polite: one request per board per scan, short sleeps between boards.
"""

import html
import json
import logging
import re

from bs4 import BeautifulSoup

from . import settings as S
from .util import from_unix, html_to_text, parse_salary, polite_sleep, session

log = logging.getLogger("jobwatch.sources")
http = session()


def job(**kw) -> dict:
    base = dict(id="", source="", title="", company="", location="", remote=None, url="",
                apply_url="", posted_at="", salary_text="", salary_min=None, salary_max=None,
                applicants=None, description="")
    base.update(kw)
    if base["salary_text"] and not base["salary_max"]:
        base["salary_min"], base["salary_max"] = parse_salary(base["salary_text"])
    return base


# ───────────────────────── Company career pages (public ATS APIs) ─────────────────────────

def company_boards() -> list[dict]:
    out = []
    for entry in S.COMPANY_BOARDS:
        ats, _, slug = entry.partition(":")
        try:
            if ats == "greenhouse":
                d = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=20).json()
                for x in d.get("jobs", []):
                    out.append(job(
                        id=f"gh:{slug}:{x['id']}", source="Company site (Greenhouse)", title=x["title"],
                        company=x.get("company_name") or slug.title(),
                        location=(x.get("location") or {}).get("name", ""), url=x["absolute_url"],
                        posted_at=x.get("first_published") or x.get("updated_at", ""),
                    ))
            elif ats == "lever":
                d = http.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=20).json()
                for x in d if isinstance(d, list) else []:
                    cat = x.get("categories") or {}
                    sr = x.get("salaryRange") or {}
                    out.append(job(
                        id=f"lever:{slug}:{x['id']}", source="Company site (Lever)", title=x["text"],
                        company=slug.title(), location=cat.get("location", ""),
                        remote=(x.get("workplaceType") == "remote") or None,
                        url=x["hostedUrl"], apply_url=x.get("applyUrl", ""),
                        posted_at=from_unix(x["createdAt"] / 1000) if x.get("createdAt") else "",
                        salary_min=sr.get("min"), salary_max=sr.get("max"),
                        description=x.get("descriptionPlain", ""),
                    ))
            elif ats == "ashby":
                d = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
                             timeout=30).json()
                for x in d.get("jobs", []):
                    comp = (x.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
                    out.append(job(
                        id=f"ashby:{slug}:{x['id']}", source="Company site (Ashby)", title=x["title"],
                        company=slug.title(), location=x.get("location", ""),
                        remote=bool(x.get("isRemote")) or None, url=x["jobUrl"],
                        apply_url=x.get("applyUrl", ""), posted_at=x.get("publishedAt", ""),
                        salary_text=comp, description=x.get("descriptionPlain", ""),
                    ))
            else:
                log.warning(f"Unknown ATS in COMPANY_BOARDS: {entry}")
            polite_sleep(0.3)
        except Exception as e:
            log.warning(f"Company board {entry} failed: {e}")
    return out


def hn_whos_hiring() -> list[dict]:
    """Monthly 'Ask HN: Who is hiring?' thread, via the public Algolia HN API."""
    out = []
    try:
        stories = http.get("https://hn.algolia.com/api/v1/search_by_date",
                           params={"tags": "story,author_whoishiring", "hitsPerPage": 5}, timeout=20).json()["hits"]
        story = next(s for s in stories if "who is hiring" in s["title"].lower())
        hits = http.get("https://hn.algolia.com/api/v1/search",
                        params={"tags": f"comment,story_{story['objectID']}", "query": "designer",
                                "hitsPerPage": 200}, timeout=20).json()["hits"]
        for h in hits:
            if h.get("parent_id") != int(story["objectID"]):
                continue  # top-level posts only
            text = html_to_text(h.get("comment_text", ""))
            first = text.split("\n", 1)[0]
            parts = [p.strip() for p in first.split("|")]
            role = next((p for p in parts if re.search(r"design", p, re.I)), "")
            out.append(job(
                id=f"hn:{h['objectID']}", source="Hacker News Who's Hiring", title=role[:120],
                company=parts[0][:80] if parts else "", location=" | ".join(parts[1:4]),
                remote=bool(re.search(r"remote", first, re.I)) or None,
                url=f"https://news.ycombinator.com/item?id={h['objectID']}",
                posted_at=from_unix(h["created_at_i"]), description=text,
            ))
    except Exception as e:
        log.warning(f"HN failed: {e}")
    return out


def fixtures() -> list[dict]:
    """Offline demo postings from examples/postings/*.json (made-up companies)."""
    return [job(**json.loads(p.read_text(encoding="utf-8"))) for p in sorted(S.FIXTURE_DIR.glob("*.json"))]


ALL = {
    "Company career pages": company_boards,
    "Hacker News": hn_whos_hiring,
}


# ───────────────────────── Full job description for tailoring ─────────────────────────

def fetch_description(j: dict) -> str:
    if j.get("description") and len(j["description"]) > 600:
        return j["description"]
    url = j.get("url", "")
    try:
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
        if m:
            d = http.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}", timeout=20).json()
            return html_to_text(html.unescape(d.get("content", "")))
        m = re.search(r"jobs\.lever\.co/([\w-]+)/([\w-]+)", url)
        if m:
            d = http.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}", timeout=20).json()
            return d.get("descriptionPlain", "") + "\n" + "\n".join(
                f"{l['text']}\n{html_to_text(l['content'])}" for l in d.get("lists", []))
        m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([\w-]+)", url)
        if m:
            d = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}", timeout=30).json()
            for x in d.get("jobs", []):
                if x["id"] == m.group(2):
                    return x.get("descriptionPlain", "")
        if not url.startswith("http"):
            return j.get("description", "")
        soup = BeautifulSoup(http.get(url, timeout=20).text, "html.parser")
        for t in soup(["script", "style", "nav", "header", "footer"]):
            t.decompose()
        return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n")).strip()[:12000]
    except Exception as e:
        log.warning(f"Description fetch failed for {url}: {e}")
        return j.get("description", "")
