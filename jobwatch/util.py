"""Shared HTTP session, salary parsing, and text helpers."""

import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

UA = "job-watcher/1.0 (personal job search; polite polling)"


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def from_unix(ts) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def html_to_text(html: str) -> str:
    text = BeautifulSoup(html or "", "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def polite_sleep(seconds=1.2):
    time.sleep(seconds)


_MONEY = re.compile(r"\$?\s*(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s*([kK])?")


def parse_salary(text: str):
    """Return (min_annual, max_annual) in USD from free text, or (None, None).

    Handles "$175K - $250K", "$82.23/hr - $88.51/hr", "150,000-180,000/yr".
    Non-USD currencies return (None, None) rather than a wrong number.
    """
    if not text:
        return None, None
    t = text.replace("–", "-").replace("—", "-")
    if re.search(r"€|£|EUR|GBP|CAD|AUD|INR|PLN|R\$", t):
        return None, None
    hourly = bool(re.search(r"/\s*h(ou)?r|per hour|hourly", t, re.I))
    vals = []
    for num, k in _MONEY.findall(t):
        v = float(num.replace(",", ""))
        if k:
            v *= 1000
        if hourly and v < 1000:
            v *= 2080
        if v >= 20_000 or (hourly and v >= 20 * 2080):
            vals.append(v)
    if not vals:
        return None, None
    return int(min(vals)), int(max(vals))


def fmt_pay(lo, hi) -> str:
    if not lo and not hi:
        return ""
    if lo == hi or not lo:
        return f"${(hi or lo) / 1000:.0f}k"
    return f"${lo / 1000:.0f}k–${hi / 1000:.0f}k"
