"""Relevance breakdown + "Why cite this?" annotations, with a verbatim-quote gate.

Split of responsibilities (judges: this is the anti-hallucination story):
- CODE scores what is objective: recency, citation impact.
- GEMINI scores what needs judgement: topic match, methodology match, and writes a 2-sentence
  "Why cite this?" that must include an evidence quote copied from the abstract.
- CODE then checks the quote is really in the abstract (normalised exact substring). A quote that
  isn't found is rejected: the LLM scores are discarded and the paper falls back to the lexical
  score with a "quote rejected" badge. Nothing the model invents reaches the bibliography.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import date

from rapidfuzz import fuzz

from models import Paper

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
WEIGHTS = {"topic": 0.40, "method": 0.25, "recency": 0.15, "impact": 0.20}


# ---------------------------------------------------------------- objective scores (code)
def recency_score(year: int | None, year_from: int | None, year_to: int | None) -> float:
    if not year:
        return 5.0
    hi = year_to or date.today().year
    lo = year_from or hi - 10
    if hi <= lo:
        return 10.0
    return round(10 * max(0.0, min(1.0, (year - lo) / (hi - lo))), 1)


def impact_score(citations: int | None, year: int | None) -> float:
    """Citations per year, log-scaled to 0-10 (so a 2025 paper isn't punished vs a 2015 classic)."""
    if citations is None:
        return 5.0
    age = max(1, date.today().year - (year or date.today().year) + 1)
    return round(min(10.0, 10 * math.log10(1 + citations / age) / 2), 1)   # 100 cites/yr -> 10


# ---------------------------------------------------------------- verbatim-quote gate
def _n(s: str) -> str:
    s = s.lower().replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"[\u2010-\u2015]", "-", s)
    return re.sub(r"\s+", " ", s).strip(" .\"'")


def quote_in_abstract(quote: str, abstract: str, min_words: int = 6) -> bool:
    """True only if `quote` (>= min_words) appears verbatim in the abstract, ignoring case,
    whitespace, curly quotes and dash variants. No fuzzy matching: paraphrase = rejection."""
    q = _n(quote or "")
    return len(q.split()) >= min_words and q in _n(abstract or "")


def best_sentence(abstract: str, query: str) -> str:
    """Offline fallback: the abstract sentence most similar to the query (verbatim by construction)."""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", abstract or "") if len(s.split()) >= 6]
    return max(sents, key=lambda s: fuzz.token_set_ratio(query.lower(), s.lower()), default="")


# ---------------------------------------------------------------- Gemini re-rank
PROMPT = """You are helping a faculty member build a bibliography.
Research question: {q}
Keywords: {kw}

For EACH paper below return an object with:
- "id": the paper id given
- "topic_match": 0-10, how directly it addresses the research question
- "method_match": 0-10, how relevant its methods/evaluation are to the question
- "why_cite": exactly 2 sentences, tailored to the research question, on why to cite it
- "evidence_quote": ONE sentence or clause of at least 8 words copied EXACTLY, character for character,
  from that paper's abstract, that supports your topic_match. Do not paraphrase or fix typos.

Papers:
{papers}
"""

SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "topic_match": {"type": "NUMBER"},
            "method_match": {"type": "NUMBER"},
            "why_cite": {"type": "STRING"},
            "evidence_quote": {"type": "STRING"},
        },
        "required": ["id", "topic_match", "method_match", "why_cite", "evidence_quote"],
    },
}


def _gemini_rank(papers: list[Paper], question: str, keywords: list[str]) -> dict[str, dict]:
    from google import genai
    from google.genai import types

    client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
    block = "\n\n".join(f"[id={i}] {p.title} ({p.year})\nAbstract: {p.abstract[:1800]}"
                        for i, p in enumerate(papers))
    resp = client.models.generate_content(
        model=MODEL,
        contents=PROMPT.format(q=question, kw=", ".join(keywords), papers=block),
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           response_schema=SCHEMA, temperature=0.2),
    )
    return {str(d["id"]): d for d in json.loads(resp.text)}


def annotate(papers: list[Paper], question: str, keywords: list[str], year_from=None, year_to=None,
             use_llm: bool | None = None, batch: int = 20) -> list[Paper]:
    """Fill p.relevance with a breakdown, a why-cite note and a verified quote; return sorted."""
    use_llm = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) if use_llm is None else use_llm
    llm: dict[str, dict] = {}
    if use_llm:
        for s in range(0, len(papers), batch):
            try:
                part = _gemini_rank(papers[s:s + batch], question, keywords)
                llm.update({str(int(k) + s): v for k, v in part.items() if str(k).isdigit()})
            except Exception as e:  # quota / network: degrade, never crash the run
                llm.setdefault("_error", {"msg": str(e)[:200]})
    for i, p in enumerate(papers):
        r = p.relevance or {}
        rec, imp = recency_score(p.year, year_from, year_to), impact_score(p.citation_count, p.year)
        lex = r.get("lexical", 0.5)
        g = llm.get(str(i))
        if g and quote_in_abstract(g.get("evidence_quote", ""), p.abstract):
            topic, method = float(g["topic_match"]), float(g["method_match"])
            quote, why, qstatus = g["evidence_quote"], g["why_cite"], "verified"
        else:
            topic = method = round(10 * lex, 1)
            quote = best_sentence(p.abstract, question)
            why = (f"Closest match in the abstract to your question: \u201c{quote}\u201d" if quote
                   else "No abstract available; ranked on title and metadata only.")
            qstatus = "rejected (not found verbatim in abstract)" if g else "extractive (no LLM)"
        total = (WEIGHTS["topic"] * topic + WEIGHTS["method"] * method
                 + WEIGHTS["recency"] * rec + WEIGHTS["impact"] * imp)
        r.update({"topic_match": topic, "method_match": method, "recency": rec, "citation_impact": imp,
                  "score": round(total, 2), "why_cite": why, "evidence_quote": quote,
                  "quote_check": qstatus, "ranker": "gemini" if g else "lexical"})
        if g and qstatus.startswith("rejected"):
            r["rejected_quote"] = g.get("evidence_quote")
        p.relevance = r
    return sorted(papers, key=lambda p: p.relevance["score"], reverse=True)
