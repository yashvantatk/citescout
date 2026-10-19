"""DOI verification gate (TC1, TC4). Deterministic code, never the LLM.

resolve_doi() uses doi.org content negotiation (CSL-JSON), which works for BOTH Crossref DOIs
(journals/conferences) and DataCite DOIs (arXiv's 10.48550/...). Crossref's REST API alone would
404 every arXiv DOI.

A paper is citable only if: DOI resolves AND title token_set_ratio >= 90 AND
(author last-name overlap >= 50% of the shorter list, or first author matches) AND year within ±1
(year is soft for preprint→published pairs, see note below).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict

from rapidfuzz import fuzz

from models import Paper, norm_doi
from net import fetch

CSL = "application/vnd.citationstyles.csl+json"
TITLE_MIN = 90
AUTHOR_MIN = 0.5


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"<[^>]+>", " ", s)            # strip JATS/MathML tags in Crossref titles
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _csl_title(csl: dict) -> str:
    t = csl.get("title") or ""
    if isinstance(t, list):
        t = t[0] if t else ""
    sub = csl.get("subtitle") or ""
    if isinstance(sub, list):
        sub = sub[0] if sub else ""
    return f"{t}: {sub}" if sub else t


def _csl_year(csl: dict) -> int | None:
    for k in ("issued", "published-print", "published-online", "created"):
        try:
            return int(csl[k]["date-parts"][0][0])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None


def _csl_last_names(csl: dict) -> list[str]:
    out = []
    for a in csl.get("author") or []:
        fam = a.get("family") or (a.get("literal") or "").split()[-1:] or [""]
        fam = fam if isinstance(fam, str) else fam[0]
        if fam:
            out.append(_fold(fam).split()[-1] if _fold(fam) else "")
    return [x for x in out if x]


@dataclass
class Verdict:
    doi: str
    resolved: bool
    status: str               # verified | mismatch | unresolved
    title_score: float = 0.0
    author_overlap: float = 0.0
    year_ok: bool = False
    resolved_title: str = ""
    resolved_year: int | None = None
    resolved_authors: list | None = None
    resolver: str = "doi.org/csl+json"
    reason: str = ""

    @property
    def citable(self) -> bool:
        return self.status == "verified"

    def to_dict(self):
        d = asdict(self)
        d["citable"] = self.citable
        return d


async def resolve_doi(doi: str) -> tuple[int, dict | None]:
    r = await fetch(f"https://doi.org/{doi}", headers={"Accept": CSL})
    if r.status != 200:
        return r.status, None
    try:
        return 200, r.json()
    except ValueError:
        return 415, None   # registrar returned HTML instead of CSL


def compare(claimed_title: str, claimed_authors: list[str], claimed_year: int | None,
            csl: dict) -> tuple[float, float, bool, str, int | None, list[str]]:
    rt = _csl_title(csl)
    ry = _csl_year(csl)
    rl = _csl_last_names(csl)
    ts = fuzz.token_set_ratio(_fold(claimed_title), _fold(rt))
    # guard: token_set_ratio scores 100 when one title is a subset of the other ("Attention" vs
    # "Attention is all you need"); require reasonable length parity too
    if ts >= TITLE_MIN and fuzz.ratio(_fold(claimed_title), _fold(rt)) < 60:
        ts = min(ts, fuzz.token_sort_ratio(_fold(claimed_title), _fold(rt)))
    cl = [_fold(a).split()[-1] for a in claimed_authors if _fold(a)]
    if cl and rl:
        overlap = len(set(cl) & set(rl)) / min(len(set(cl)), len(set(rl)))
        if cl[0] == rl[0]:
            overlap = max(overlap, AUTHOR_MIN)
    else:
        overlap = 1.0 if not cl and not rl else 0.0
    year_ok = claimed_year is None or ry is None or abs(claimed_year - ry) <= 1
    return ts, overlap, year_ok, rt, ry, rl


async def verify_doi(doi: str, claimed_title: str, claimed_authors: list[str],
                     claimed_year: int | None = None) -> Verdict:
    doi = norm_doi(doi) or ""
    if not doi:
        return Verdict(doi="", resolved=False, status="unresolved", reason="no DOI")
    status, csl = await resolve_doi(doi)
    if csl is None:
        return Verdict(doi=doi, resolved=False, status="unresolved", reason=f"doi.org returned {status}")
    ts, ov, yok, rt, ry, rl = compare(claimed_title, claimed_authors, claimed_year, csl)
    problems = []
    if ts < TITLE_MIN:
        problems.append(f"title mismatch ({ts:.0f} < {TITLE_MIN})")
    if ov < AUTHOR_MIN:
        problems.append(f"author overlap {ov:.0%}")
    if not yok:
        problems.append(f"year {claimed_year} vs {ry}")
    return Verdict(doi=doi, resolved=True, status="mismatch" if problems else "verified",
                   title_score=round(ts, 1), author_overlap=round(ov, 2), year_ok=yok,
                   resolved_title=rt, resolved_year=ry, resolved_authors=rl,
                   reason="; ".join(problems) or "title, authors and year match")


async def verify_paper(p: Paper) -> Paper:
    """Verify the DOI we'd cite. If a published DOI fails but the arXiv DOI passes, fall back to
    citing the preprint (and record why) instead of dropping a good paper."""
    v = await verify_doi(p.best_doi or "", p.title, p.authors, p.year)
    if not v.citable and p.doi and p.arxiv_id:
        from models import arxiv_doi
        v2 = await verify_doi(arxiv_doi(p.arxiv_id), p.title, p.authors, None)
        if v2.citable:
            v2.reason = f"published DOI failed ({v.reason}); citing arXiv version"
            p.verification = v2.to_dict() | {"published_attempt": v.to_dict()}
            return p
    p.verification = v.to_dict()
    return p
