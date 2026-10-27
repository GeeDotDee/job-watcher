"""Base resumes and small, per-job edits on top of them.

Every base is built from the master resume at load time, so the wording never drifts from the master:
only the headline, the summary, and the order of sections/bullets differ (set under "bases" in candidate.json).
Tailoring then edits a few bullets, the summary and the skills lines instead of rewriting the whole resume,
which uses far less Claude and leaves far less room for drift.
"""

import re

from . import settings as S

BASES = S.CANDIDATE["bases"]
AI_TERMS = re.compile(r"\b(AI|ML|LLMs?|agents?|agentic|generative|GenAI|machine learning|conversational)\b")
HEADING = re.compile(r"^[A-Z][A-Z &/]+$")


def kind(job: dict, description: str = "") -> str:
    title = job.get("title", "")
    if "pm" in BASES and re.search(r"\bproduct manager\b", title, re.I):
        return "pm"
    if "ai" in BASES and (AI_TERMS.search(title) or len(AI_TERMS.findall(description)) >= 8):
        return "ai"
    return "product"


def _sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    lines = text.strip().splitlines()
    head, secs = [], []
    for line in lines:
        if HEADING.match(line.strip()) and len(line.strip()) > 3:
            secs.append((line.strip(), []))
        elif secs:
            secs[-1][1].append(line)
        else:
            head.append(line)
    return head, secs


def _join(head, secs) -> str:
    out = list(head)
    for name, body in secs:
        while body and not body[-1].strip():
            body = body[:-1]
        out += ["", name] + list(body)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def base(k: str, master: str | None = None) -> str:
    cfg = BASES[k]
    master = master if master is not None else S.MASTER_RESUME.read_text(encoding="utf-8")
    head, secs = _sections(master)
    head[1] = f"{cfg['headline']} | {cfg['tagline']}"
    d = dict(secs)
    if cfg.get("summary") and "SUMMARY" in d:
        d["SUMMARY"] = [cfg["summary"]]
    if cfg.get("lead_bullet") and "EXPERIENCE" in d:
        # The first bullet containing this phrase moves to the top of its job.
        body = d["EXPERIENCE"]
        hit = next((i for i, l in enumerate(body) if l.startswith("- ") and cfg["lead_bullet"] in l), None)
        first = None
        if hit is not None:
            first = next(i for i in range(hit, -1, -1) if not body[i].startswith("- ")) + 1
        if hit is not None and first is not None and hit > first:
            body.insert(first, body.pop(hit))
    order = [n for n, _ in secs]
    for name, before in (cfg.get("move_before") or {}).items():
        if name in order and before in order:
            order.remove(name)
            order.insert(order.index(before), name)
    return _join(head, [(n, d[n]) for n in order])


# ── numbered view + edits ──

def numbered(text: str) -> str:
    """Bullets get [B1].., the summary [S], skills lines [K1].. so Claude can edit by id."""
    out, b, k, sec = [], 0, 0, ""
    for line in text.splitlines():
        s = line.strip()
        if HEADING.match(s) and len(s) > 3:
            sec = s
            out.append(line)
        elif line.startswith("- "):
            b += 1
            out.append(f"[B{b}] {line}")
        elif sec == "SUMMARY" and s:
            out.append(f"[S] {line}")
        elif sec == "SKILLS" and ":" in s:
            k += 1
            out.append(f"[K{k}] {line}")
        else:
            out.append(line)
    return "\n".join(out)


def apply(text: str, tagline: str = "", summary: str = "", edits: dict | None = None,
          drop: list | None = None, skills: dict | None = None) -> str:
    edits, drop, skills = edits or {}, set(drop or []), skills or {}
    out, b, k, sec = [], 0, 0, ""
    lines = text.splitlines()
    if tagline and len(lines) > 1:
        lines[1] = lines[1].split("|")[0].strip() + " | " + " ".join(tagline.split()[:10])
    for line in lines:
        s = line.strip()
        if HEADING.match(s) and len(s) > 3:
            sec = s
            out.append(line)
        elif line.startswith("- "):
            b += 1
            bid = f"B{b}"
            if bid in drop:
                continue
            new = (edits.get(bid) or "").strip().lstrip("- ").strip()
            out.append(f"- {new}" if new else line)
        elif sec == "SUMMARY" and s and summary.strip():
            out.append(summary.strip())
        elif sec == "SKILLS" and ":" in s:
            k += 1
            new = (skills.get(f"K{k}") or "").strip()
            label = s.split(":", 1)[0]
            if new:
                new = new.split(":", 1)[1].strip() if new.startswith(label + ":") else new
                out.append(f"{label}: {new}")
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out).strip() + "\n"
