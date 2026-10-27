"""Offline tests for the rules: screening, honesty checks, truth checks and the submit gate.
They run on the made-up candidate in examples/ and never call Claude or the network."""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="jobwatch-test-"))

import pytest

from jobwatch import bases, filters, gates, tailor, verify
from jobwatch import settings as S

EX = Path(__file__).resolve().parent.parent / "examples"
MASTER = S.MASTER_RESUME.read_text(encoding="utf-8")


def posting(name):
    return json.loads((EX / "postings" / f"{name}.json").read_text(encoding="utf-8"))


# ── screening ──

@pytest.mark.parametrize("name, keep, why", [
    ("contoso-senior-pd", True, "ok"),
    ("relecloud-pm", True, "ok"),
    ("adatum-pd-lowpay", False, "pay tops out"),
    ("litware-brand", False, "not a product/UX design title"),
    ("proseware-foreign", False, "foreign remote"),
    ("woodgrove-commute", False, "long commute"),
    ("alpine-old", False, "posted"),
])
def test_screen(name, keep, why):
    from jobwatch.sources import job
    ok, reason = filters.screen(job(**posting(name)))
    assert ok is keep
    assert why in reason


def test_engineering_role_in_disguise():
    assert filters.engineering_role(posting("trey-engineer")["description"])
    assert not filters.engineering_role(posting("contoso-senior-pd")["description"])


def test_newer_posting_ranks_first():
    old = {"title": "Senior Product Designer", "posted_at": "2026-01-01T10:00:00+00:00"}
    new = {"title": "Product Designer", "posted_at": "2026-01-01T12:00:00+00:00"}
    assert filters.priority(new) > filters.priority(old)


# ── tailoring guards ──

def test_honesty_rejects_invented_number():
    bad = MASTER.replace("from 61% to 88%", "from 61% to 97%")
    with pytest.raises(ValueError, match="97"):
        tailor.check_honesty(bad, MASTER, "", "Senior Product Designer")


def test_honesty_rejects_dropped_employer():
    bad = MASTER.replace("Fabrikam Health | Remote", "A Health Company | Remote")
    with pytest.raises(ValueError, match="dropped Fabrikam Health"):
        tailor.check_honesty(bad, MASTER, "", "Senior Product Designer")


def test_honesty_keeps_the_honest_headline():
    inflated = MASTER.replace("Senior Product Designer | Payments", "Principal Designer | Payments", 1)
    fixed = tailor.check_honesty(inflated, MASTER, "", "Senior Product Designer")
    assert fixed.splitlines()[1].startswith("Senior Product Designer |")


def test_keyword_match_is_stem_tolerant():
    m = tailor.keyword_match("Ran usability testing and prototyped flows.", "", ["usability testing", "prototyping"])
    assert m["match_pct"] == 100


def test_base_edits_by_id():
    text = bases.base("ai", MASTER)
    assert text.index("PROJECTS") < text.index("EXPERIENCE")
    edited = bases.apply(text, edits={"B1": "Rewritten bullet."}, drop=["B2"])
    assert "- Rewritten bullet." in edited and edited.count("\n- ") == text.count("\n- ") - 1


# ── truth checks ──

def test_consistency_resets_dates_and_years():
    resume = MASTER.replace("March 2022 - May 2026", "2022 - Present")
    answers = {"why_role": "I bring 10 years of product design experience."}
    fixed, answers, fixes, issues = verify.consistency(resume, answers, MASTER)
    assert "March 2022 - May 2026" in fixed
    assert answers["why_role"] == "I bring 7+ years of product design experience."
    assert len(fixes) == 2 and not issues


def test_consistency_blocks_claims_that_arent_true():
    answers = {"why_role": "I currently work at Northwind Payments, where I lead payments design.",
               "salary": "My base salary expectation is $95,000."}
    _, _, _, issues = verify.consistency(MASTER, answers, MASTER)
    assert any("currently employed" in i for i in issues)
    assert any("under the" in i for i in issues)


def test_legal_name_fields():
    assert verify.expected_name("legal first name") == "Samantha"
    assert verify.expected_name("preferred first name") == "Sam"
    assert verify.expected_name("emergency contact name") is None


# ── the gate ──

def _row():
    return {"job": posting("contoso-senior-pd"), "package": str(EX / "package"),
            "tailored_at": datetime.now().isoformat(timespec="seconds")}


@pytest.mark.parametrize("form, expect", [
    ("clean", ""),
    ("portfolio_password", ""),
    ("account_wall", "needs an account"),
    ("stale_autofill", "should be 'Sam'"),
    ("sponsorship", "sponsorship"),
])
def test_gate(form, expect):
    snap = json.loads((EX / "forms" / f"{form}.json").read_text(encoding="utf-8"))
    why = gates.submit_blockers(_row(), snap)
    assert (why == "") if not expect else (expect in why)


def test_gate_fails_closed_on_stale_package():
    row = dict(_row(), tailored_at="2000-01-01T00:00:00")
    snap = json.loads((EX / "forms" / "clean.json").read_text(encoding="utf-8"))
    assert "older master resume" in gates.submit_blockers(row, snap)


def test_gate_caps_submit_attempts():
    snap = json.loads((EX / "forms" / "clean.json").read_text(encoding="utf-8"))
    snap["submit_attempts"] = gates.MAX_SUBMIT_ATTEMPTS
    assert "stopped" in gates.submit_blockers(_row(), snap)


def test_pdf_renders(tmp_path):
    from jobwatch.render import generate_pdf
    out = generate_pdf(MASTER, tmp_path / "r.pdf")
    assert out.read_bytes()[:4] == b"%PDF"
