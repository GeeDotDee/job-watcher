# Job Watcher

A personal job search pipeline. It polls public job boards, screens postings with plain rules, tailors a resume to each match with headless Claude Code, then checks Claude's work before a person sees it.

By [Nate Douglas](https://nate-douglas.com), product designer. Designed and directed by me, built with Claude Code. I made the product calls: what gets screened out, what counts as honest tailoring, where a person has to step in. Claude Code wrote the code; I tested it against real postings and forms and decided every fix.

This repo is the core of the version I use. It runs on a made-up candidate (Sam Example) and made-up postings, so you can read every rule and run it offline.

## How it works

```
 public job boards ──► screen ──► fetch posting ──► tailor ──► check ──► review page ──► a person decides
  (Greenhouse, Lever,   rules:     re-check pay,     Claude     honesty,   score, gaps,
   Ashby, HN)           title,     spot engineering  Code,      truth,     resume, answers
                        place,     roles in disguise headless   keyword
                        pay, age                                score
```

1. **Screen** (`filters.py`). Title lane, location, pay floors, posting age. No AI. Every rejection has a reason you can read.
2. **Tailor** (`tailor.py`, `bases.py`). Start from a base resume and ask Claude for small edits by bullet ID instead of a full rewrite. Then boost passes for missing keywords, kept only if the score goes up.
3. **Check** (`tailor.py`, `verify.py`). Claude doesn't grade its own work:
   - Every number on the tailored resume must already exist in the master resume.
   - No employer may be dropped, and the headline can't claim a title the person never held.
   - Employer dates, years of experience, current job status and pay in written answers are checked against `candidate.json`. Safe fixes are applied; anything else blocks.
   - The keyword score is computed locally, not reported by Claude.
4. **Review** (`review.py`). A local page lists each package with its score, gaps and the keywords Claude declined to add because they aren't true.
5. **Gate** (`gates.py`). The last check before Submit, run on a snapshot of the filled form. It fails closed:
   - The form is behind an account wall: needs you.
   - A field shows stale autofill from an old profile, or an old resume is still attached: needs you.
   - Any answer says the person needs visa sponsorship when they don't: needs you.
   - The package predates the latest master resume, or Submit was already tried twice: stop.

## Decisions worth reading

- **A person at every account wall.** Creating an account, signing in and typing a verification code are never automated. The gate names the wall and hands the job to a person.
- **Fail closed.** Every check returns a reason. Any reason at all means a person looks, so a wrong "no" costs a minute and a wrong "yes" never happens.
- **Posting text is untrusted.** Every prompt says so, and headless Claude runs with all tools turned off, so a posting can't make it do anything except write text.
- **One file holds the truth.** Name, contact, employers, dates, standing answers and pay floors live in `candidate.json`. The code reads from it and never hardcodes a fact.

## What's not here

The version I run also fills application forms in a browser, which is where most of its complexity lives. That part, along with anything that signs in, sends email or touches my accounts, isn't published. This repo stops at the decision: given what the form shows, can it go?

## Run it

Python 3.10+.

```
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python watch.py scan --demo --no-tailor        # screen the made-up postings, see each reason
python watch.py gate examples/forms/stale_autofill.json examples/package
python -m pytest tests                          # offline, no Claude, no network
```

With [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) installed and signed in, `python watch.py scan --demo` also tailors the postings that pass, and `python watch.py serve` opens the review page at http://127.0.0.1:8765.

To use it for yourself, copy `examples/candidate.json` to `candidate.json` at the repo root and replace everything, point `files` at your own master resume, and set `COMPANY_BOARDS` in `.env` (see `.env.example`). Run `python watch.py scan` on a schedule (cron or Task Scheduler) every 15 minutes.

## What I'd do differently

- **Start with the candidate file.** My first version had personal facts spread across regexes, prompts and settings, so updating one fact meant finding every place it lived. One file of facts, read everywhere, is what this repo does, and I'd start there.
- **Write the failing case first.** Most rules here began as a real posting or form that got through when it shouldn't have. I patched the rule, then wrote it down. I'd write the fixture first, then the fix, so every rule has a test. The tests in this repo are that habit applied after the fact.
- **Measure outcomes sooner.** The keyword score is a proxy, and mirroring a posting's wording isn't the same as being a good fit. I added reply tracking by source a week in; I'd build it before tuning the score at all.
- **Draw the human handoff earlier.** Browser automation across many applicant tracking systems was the most fragile part, and the hardest cases were account walls and odd custom questions. I'd have defined the gate first and automated only what passed it cleanly.
- **Query-friendly storage.** Each job is a JSON blob in one SQLite column. That was quick to build and awkward to ask questions of later. Real columns for pay, source and outcome would have paid off.

## Layout

```
watch.py              command line: scan, serve, status, gate
jobwatch/
  settings.py         knobs (.env) and the candidate file
  sources.py          public ATS APIs + offline demo postings
  filters.py          screening rules and queue priority
  bases.py            base resumes and edits by bullet ID
  tailor.py           Claude Code prompts, honesty checks, keyword score, packages
  verify.py           truth checks on packages and on filled forms
  gates.py            the fail-closed submit gate
  render.py           plain-text resume to PDF
  review.py           local review page
  pipeline.py         one scan, start to finish
  store.py            SQLite job store
examples/             made-up candidate, postings, a package and form snapshots
tests/                offline tests for every rule above
```

## License

MIT. See [LICENSE](LICENSE).
