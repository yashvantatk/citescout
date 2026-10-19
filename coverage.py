"""Gap-aware coverage map: group papers into themes, then find the parts of the request they miss.

Deterministic and offline, so it runs the same in live runs, replays and tests:
- facets = the user's keywords plus key phrases from the question (what the faculty member asked for)
- each facet's coverage = how many cited papers mention it in their title or abstract
- themes = greedy clustering on the most distinctive shared terms (TF-IDF style)
- gaps = facets with fewer than max(2, 10% of papers) papers, plus a "recent work" gap when the
  newest years of the requested range are empty
The agent calls this as the `coverage_gaps` tool and then searches the gaps it reports.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from models import Paper

_BASIC: set[str] = set("""a an and are as at be by for from how in into is it its of on or the to what which with
using via do does can we our this that these those""".split())
_STOP: set[str] = set("""a an and are as at be by for from how in into is it its of on or the to what which with
using via towards toward based new study approach approaches method methods paper does do can we our
this that these those their they results result show shows propose proposed model models task tasks
performance data large also more than such use used both between over while well paper work works
improve improves improving improved effective efficient learning neural network networks deep
relevant information existing external framework frameworks system systems various different novel recent
significantly significant outperforms outperform state-of-the-art art state however including challenges
problem problems paper' first two three one key generate generated generating answer answers question questions
enhance enhancing enhanced provide provides present presents demonstrate demonstrates experiments not only github com https http www code available et al has been have had was were would could
should may might natural language points scores score bleu achieves achieve""".split())


def _stem(w: str) -> str:
    return w[:6] if len(w) > 6 else w  # crude prefix stem: "translation"/"translations" -> "transl"


def _tokens(text: str, stop: set[str] = _STOP) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9][a-z0-9\-]+", (text or "").lower())
            if w not in stop and len(w) > 1]


def _terms(text: str, stop: set[str] = _STOP) -> set[str]:
    """Unigrams plus bigrams of words that are adjacent in the original text (no gaps over stopwords).
    Hyphenated words count both joined and split: "U-Net" -> "unet" (+ "net")."""
    out: set[str] = set()
    prev = None
    for m in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", (text or "").lower()):
        parts = m.split("-")
        if len(parts) > 1 and m not in stop:
            out.add(_stem("".join(parts)))
        for w in parts:
            if w in stop or len(w) < 2:
                prev = None
                continue
            s = _stem(w)
            out.add(s)
            if prev:
                out.add(f"{prev} {s}")
            prev = s
    return out


def _facet_terms(facet: str) -> list[str]:
    return _tokens(facet, _BASIC)


def _covers(p_terms: set[str], facet: str) -> bool:
    """All facet words present; also tries joining adjacent words so "U Net" matches "U-Net"/"UNet"."""
    words = [w for w in re.findall(r"[a-z0-9]+", facet.lower()) if w not in _BASIC]
    variants = [words] + [words[:i] + [words[i] + words[i + 1]] + words[i + 2:] for i in range(len(words) - 1)]
    for v in variants:
        toks = [_stem(w) for w in v]
        if toks and all(t in p_terms for t in toks):
            return True
    return False


def facets_for(question: str, keywords: list[str]) -> list[str]:
    out: list[str] = []
    for k in keywords:
        k = k.strip()
        if k and _facet_terms(k) and k.lower() not in (o.lower() for o in out):
            out.append(k)
    # content words of the question that the keywords did not already cover
    covered = {t for k in out for t in _facet_terms(k)}
    for w in re.findall(r"[A-Za-z][A-Za-z\-]+", question):
        lw = w.lower()
        if lw not in _STOP and lw not in _BASIC and len(lw) > 3 and _stem(lw) not in covered:
            out.append(lw)
            covered.add(_stem(lw))
    return out[:10]


def _label(term: str, papers: list[Paper]) -> str:
    """Turn a stemmed term back into a readable phrase taken from the papers' own words."""
    parts = term.split()
    for p in papers:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", f"{p.title} {p.abstract}")
        low = [w.lower() for w in words]
        for i in range(len(low) - len(parts) + 1):
            if all(_stem(low[i + j]) == parts[j] and low[i + j] not in _STOP for j in range(len(parts))):
                return " ".join(words[i:i + len(parts)])
    return term


def themes(papers: list[Paper], max_themes: int = 6, exclude: set[str] | None = None) -> list[dict]:
    """exclude: stems of the query itself, so themes show sub-topics rather than echoing the question."""
    n = len(papers)
    if n == 0:
        return []
    exclude = exclude or set()
    docs = [_terms(f"{p.title} {p.abstract}") for p in papers]
    df = Counter(t for d in docs for t in d)
    cands = {t: c for t, c in df.items() if 2 <= c <= max(2, int(0.45 * n))
             and not any(part in exclude for part in t.split())}
    # prefer bigrams and distinctive terms: score = df * idf, bigrams boosted
    score = {t: c * math.log(1 + n / c) * (3.0 if " " in t else 1.0) for t, c in cands.items()}
    left = set(range(n))
    out = []
    for t in sorted(score, key=lambda x: score[x], reverse=True):
        members = [i for i in left if t in docs[i]]
        if len(members) < 2:
            continue
        out.append({"theme": _label(t, [papers[i] for i in members]), "count": len(members),
                    "papers": [papers[i].title for i in members][:8]})
        left -= set(members)
        if len(out) >= max_themes or len(left) < 2:
            break
    if left:
        out.append({"theme": "Other", "count": len(left), "papers": [papers[i].title for i in left][:8]})
    return out


def coverage_map(papers: list[Paper], question: str, keywords: list[str],
                 year_from: int | None = None, year_to: int | None = None,
                 subtopics: list[str] | None = None) -> dict:
    """subtopics: the sub-areas the planner (Gemini) expects a thorough review to cover. Code, not the
    LLM, measures how many cited papers actually address each one."""
    n = len(papers)
    docs = [_terms(f"{p.title} {p.abstract}", _BASIC) for p in papers]
    need = max(2, math.ceil(0.10 * n))
    rows = []
    facets: list[tuple[str, str]] = []
    for sub in (subtopics or [])[:8]:
        sub = re.sub(r"\s+", " ", str(sub)).strip()[:60]
        if sub and _facet_terms(sub):
            facets.append((sub, "planner subtopic"))
    for f in facets_for(question, keywords):
        if f.lower() not in (x.lower() for x, _ in facets):
            facets.append((f, "request"))
    for f, src in facets:
        c = sum(_covers(d, f) for d in docs)
        rows.append({"facet": f, "source": src, "papers": c, "status": "gap" if c < need else "covered"})
    gaps = [r for r in rows if r["status"] == "gap"]
    core = " ".join(k for k in keywords[:2]) or question
    suggestions = []
    for g in gaps[:3]:
        words, seen = [], set()
        for w in f"{g['facet']} {core}".split():
            if w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
        suggestions.append({"facet": g["facet"], "query": " ".join(words)})

    years = Counter(p.year for p in papers if p.year)
    recent_gap = None
    if year_to and n:
        recent = sum(c for y, c in years.items() if y and y >= year_to - 1)
        if recent == 0:
            recent_gap = {"facet": f"recent work ({year_to - 1}-{year_to})", "source": "years", "papers": 0,
                          "status": "gap"}
            rows.append(recent_gap)
            suggestions.append({"facet": recent_gap["facet"], "query": "",
                                "hint": "snowball direction=citations on a top paper to find newer follow-ups"})
    return {"papers": n, "threshold": need, "facets": rows, "gaps": [r for r in rows if r["status"] == "gap"],
            "themes": themes(papers, exclude={t for f in facets_for(question, keywords) for t in _facet_terms(f)}),
            "years": dict(sorted(years.items())), "suggestions": suggestions}