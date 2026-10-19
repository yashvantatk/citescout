"""Retrieval + merging + OA checks. These are the deterministic tools the Gemini agent will call.

Tools (each returns plain Paper objects / dicts so they're easy to expose as function calls):
    search_arxiv(query, year_from, year_to, limit)
    search_s2(query, year_from, year_to, limit)        # falls back to OpenAlex on S2 429s
    search_openalex(query, year_from, year_to, limit)
    enrich_with_s2(paper)                              # find published DOI for an arXiv preprint
    check_oa(paper)                                    # Unpaywall: legal OA PDF or closed
    merge_papers(papers)                               # preprint + published -> one entry
"""
from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET

from rapidfuzz import fuzz

from models import Paper, norm_arxiv, norm_doi
from net import CONTACT, fetch

ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
S2_FIELDS = "title,abstract,year,venue,authors,externalIds,openAccessPdf,isOpenAccess,citationCount,url"


# ---------------------------------------------------------------- arXiv
def _arxiv_year(aid: str) -> int | None:
    m = re.match(r"(\d{2})(\d{2})\.\d{4,5}", aid) or re.match(r"[a-z\-\.]+/(\d{2})(\d{2})\d+", aid, re.I)
    if not m:
        return None
    yy = int(m.group(1))
    return 2000 + yy if yy < 90 else 1900 + yy


_STOP = set("""a an and are as at be by for from how in into is it of on or the to what which with
using via towards toward based new study approach approaches method methods paper does do can""".split())


def _arxiv_query(query: str) -> str:
    """arXiv's search ANDs every term and indexes no stopwords, so 'for' alone zeroes a query.
    Search title+abstract for content words only; cap at 5 terms to keep recall."""
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9\-]+", query)]
    words = [w for w in words if w not in _STOP and len(w) > 1][:5]
    return " AND ".join(f"(ti:{w} OR abs:{w})" for w in words) or f"all:{query}"


async def search_arxiv(query: str, year_from: int | None = None, year_to: int | None = None,
                       limit: int = 50) -> list[Paper]:
    params = {"search_query": _arxiv_query(query), "start": 0, "max_results": min(limit * 2, 200),
              "sortBy": "relevance"}
    try:
        r = await fetch("https://export.arxiv.org/api/query", params=params, attempts=4)
    except Exception:
        return []   # arXiv rate-limited/down: caller sees found=0 and can switch source
    if r.status != 200:
        return []
    root = ET.fromstring(r.text)
    out: list[Paper] = []
    for e in root.findall("a:entry", ATOM):
        aid = norm_arxiv(e.findtext("a:id", "", ATOM))
        if not aid:
            continue
        year = _arxiv_year(aid)
        if (year_from and year and year < year_from) or (year_to and year and year > year_to):
            continue
        doi_el = e.find("arxiv:doi", ATOM)   # author-supplied journal DOI, when present
        out.append(Paper(
            title=" ".join(e.findtext("a:title", "", ATOM).split()),
            authors=[a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)],
            year=year,
            abstract=" ".join(e.findtext("a:summary", "", ATOM).split()),
            venue=(e.findtext("arxiv:journal_ref", "", ATOM) or "arXiv").strip(),
            doi=norm_doi(doi_el.text) if doi_el is not None else None,
            arxiv_id=aid,
            url=f"https://arxiv.org/abs/{aid}",
            pdf_url=f"https://arxiv.org/pdf/{aid}",
            is_oa=True, oa_status="green",
            sources=["arxiv"], provenance="search:arxiv",
        ))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- Semantic Scholar
def _s2_headers() -> dict:
    k = os.getenv("S2_API_KEY")
    return {"x-api-key": k} if k else {}


def _from_s2(d: dict, prov: str = "search:s2") -> Paper:
    ext = d.get("externalIds") or {}
    aid = norm_arxiv(ext.get("ArXiv"))
    doi = norm_doi(ext.get("DOI"))
    if doi and doi.startswith("10.48550/"):
        doi = None  # that's just the arXiv DOI, keep `doi` for the published version
    pdf = (d.get("openAccessPdf") or {}).get("url") or None
    return Paper(
        title=d.get("title") or "",
        authors=[a.get("name", "") for a in d.get("authors") or []],
        year=d.get("year"),
        abstract=d.get("abstract") or "",
        venue=d.get("venue") or "",
        doi=doi, arxiv_id=aid, s2_id=d.get("paperId"),
        url=d.get("url") or "",
        pdf_url=pdf, is_oa=d.get("isOpenAccess"),
        citation_count=d.get("citationCount"),
        sources=["s2"], provenance=prov,
    )


async def search_s2(query: str, year_from: int | None = None, year_to: int | None = None,
                    limit: int = 50) -> list[Paper]:
    params = {"query": query, "limit": min(limit, 100), "fields": S2_FIELDS}
    if year_from or year_to:
        params["year"] = f"{year_from or ''}-{year_to or ''}"
    try:
        r = await fetch("https://api.semanticscholar.org/graph/v1/paper/search",
                        params=params, headers=_s2_headers(), attempts=4)
        if r.status == 200:
            return [_from_s2(d) for d in r.json().get("data", []) if d.get("title")]
    except Exception:
        pass
    # keyless S2 is a shared pool that 429s a lot: degrade to OpenAlex rather than fail
    return await search_openalex(query, year_from, year_to, limit)


async def s2_paper(identifier: str) -> Paper | None:
    """identifier: 'arXiv:1512.03385', 'DOI:10.1109/...', or an S2 paperId."""
    try:
        r = await fetch(f"https://api.semanticscholar.org/graph/v1/paper/{identifier}",
                        params={"fields": S2_FIELDS}, headers=_s2_headers(), attempts=4)
        return _from_s2(r.json(), prov="lookup:s2") if r.status == 200 else None
    except Exception:
        return None


# ---------------------------------------------------------------- OpenAlex (keyless fallback)
def _from_openalex(w: dict) -> Paper:
    inv = w.get("abstract_inverted_index") or {}
    words = sorted(((p, t) for t, ps in inv.items() for p in ps))
    abstract = " ".join(t for _, t in words)
    aid = None
    for loc in w.get("locations") or []:
        lp = (loc.get("landing_page_url") or "")
        if "arxiv.org/abs/" in lp:
            aid = norm_arxiv(lp)
    doi = norm_doi(w.get("doi"))
    if doi and doi.startswith("10.48550/arxiv."):
        aid = aid or doi.split("arxiv.", 1)[1]
        doi = None
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    return Paper(
        title=w.get("title") or w.get("display_name") or "",
        authors=[a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")],
        year=w.get("publication_year"),
        abstract=abstract,
        venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
        doi=doi, arxiv_id=aid, openalex_id=w.get("id"),
        url=w.get("id") or "",
        pdf_url=best.get("pdf_url"), is_oa=oa.get("is_oa"), oa_status=oa.get("oa_status"),
        citation_count=w.get("cited_by_count"),
        sources=["openalex"], provenance="search:openalex",
    )


async def search_openalex(query: str, year_from: int | None = None, year_to: int | None = None,
                          limit: int = 50) -> list[Paper]:
    flt = ["type:article|preprint"]
    if year_from:
        flt.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        flt.append(f"to_publication_date:{year_to}-12-31")
    params = {"search": query, "per_page": min(limit, 100), "filter": ",".join(flt), "mailto": CONTACT}
    if k := os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = k
    r = await fetch("https://api.openalex.org/works", params=params)
    if r.status != 200:
        return []
    return [_from_openalex(w) for w in r.json().get("results", []) if w.get("title")]


async def enrich_with_s2(p: Paper) -> Paper:
    """For an arXiv preprint, ask S2 whether a published version (journal/conference DOI) exists."""
    if p.doi or not p.arxiv_id:
        return p
    hit = await s2_paper(f"arXiv:{p.arxiv_id}")
    if hit and hit.doi:
        p.doi = hit.doi
        p.venue = hit.venue or p.venue
        p.s2_id = hit.s2_id
        p.citation_count = hit.citation_count
        p.sources = sorted(set(p.sources) | {"s2"})
    return p


# ---------------------------------------------------------------- OA check (Unpaywall)
async def check_oa(p: Paper) -> Paper:
    """Legal OA only. arXiv preprints are green OA by definition; published DOIs go to Unpaywall.
    We never try to work around a paywall (TC3)."""
    if p.arxiv_id:
        p.is_oa, p.oa_status = True, p.oa_status or "green"
        p.pdf_url = p.pdf_url or f"https://arxiv.org/pdf/{p.arxiv_id}"
        return p
    if not p.doi:
        return p
    r = await fetch(f"https://api.unpaywall.org/v2/{p.doi}", params={"email": CONTACT})
    if r.status == 200:
        d = r.json()
        p.is_oa = bool(d.get("is_oa"))
        p.oa_status = d.get("oa_status")
        best = d.get("best_oa_location") or {}
        p.pdf_url = best.get("url_for_pdf") if p.is_oa else None
        if p.is_oa and not p.pdf_url:
            p.url = best.get("url_for_landing_page") or p.url  # OA but HTML only: link, no PDF
        p.oa_source = "unpaywall"
        return p
    # Fallback: OpenAlex carries the same OA data (it is built on Unpaywall).
    r2 = await fetch(f"https://api.openalex.org/works/doi:{p.doi}", params={"mailto": CONTACT})
    if r2.status == 200:
        oa = (r2.json().get("open_access") or {})
        loc = (r2.json().get("best_oa_location") or {})
        p.is_oa = bool(oa.get("is_oa"))
        p.oa_status = oa.get("oa_status")
        p.pdf_url = loc.get("pdf_url") if p.is_oa else None
        if p.is_oa and not p.pdf_url:
            p.url = loc.get("landing_page_url") or oa.get("oa_url") or p.url
        p.oa_source = f"openalex (unpaywall http {r.status})"
        return p
    # Unknown OA status: fail closed. Link only, never download.
    p.is_oa, p.oa_status, p.pdf_url = False, "unknown", None
    p.oa_source = f"none (unpaywall http {r.status}, openalex http {r2.status})"
    return p


# ---------------------------------------------------------------- merging (TC2)


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()


def _part_marker(t: str) -> str:
    m = re.search(r"\bpart\s+([ivx]+|\d+)\b", t, re.I)
    return m.group(1).lower() if m else ""


def same_work(a: Paper, b: Paper) -> tuple[bool, str]:
    """Identifier match first, then guarded fuzzy match."""
    if a.arxiv_id and a.arxiv_id == b.arxiv_id:
        return True, "same arXiv id"
    if a.doi and a.doi == b.doi:
        return True, "same DOI"
    if a.s2_id and a.s2_id == b.s2_id:
        return True, "same S2 paperId"
    if _part_marker(a.title) != _part_marker(b.title):
        return False, "Part I/II guard"
    ts = fuzz.token_sort_ratio(_norm_title(a.title), _norm_title(b.title))
    shared = set(a.last_names) & set(b.last_names)
    first_ok = bool(a.last_names and b.last_names and a.last_names[0] == b.last_names[0])
    yr_ok = not (a.year and b.year) or abs(a.year - b.year) <= 2   # preprint often a year earlier
    if ts >= 93 and (first_ok or len(shared) >= 2) and yr_ok:
        return True, f"fuzzy title {ts:.0f} + authors"
    return False, ""


def _absorb(keep: Paper, other: Paper, reason: str) -> Paper:
    keep.merged_from.append({"title": other.title, "doi": other.doi, "arxiv_id": other.arxiv_id,
                             "venue": other.venue, "year": other.year, "reason": reason,
                             "sources": other.sources})
    keep.doi = keep.doi or other.doi
    keep.arxiv_id = keep.arxiv_id or other.arxiv_id
    keep.s2_id = keep.s2_id or other.s2_id
    keep.openalex_id = keep.openalex_id or other.openalex_id
    keep.abstract = keep.abstract if len(keep.abstract) >= len(other.abstract) else other.abstract
    keep.pdf_url = keep.pdf_url or other.pdf_url
    keep.is_oa = keep.is_oa or other.is_oa
    keep.citation_count = max(keep.citation_count or 0, other.citation_count or 0) or None
    if other.venue and other.venue.lower() != "arxiv" and (not keep.venue or keep.venue.lower() == "arxiv"):
        keep.venue = other.venue
    keep.sources = sorted(set(keep.sources) | set(other.sources))
    return keep


def merge_papers(papers: list[Paper]) -> list[Paper]:
    """Collapse preprint + published versions into one entry. The published record wins the
    citation metadata (DOI/venue/year); the arXiv PDF is kept as the legal OA copy."""
    out: list[Paper] = []
    for p in papers:
        for i, q in enumerate(out):
            ok, why = same_work(q, p)
            if ok:
                # prefer the published (non-arXiv DOI) record as the primary
                if p.doi and not q.doi:
                    out[i] = _absorb(p, q, why)
                else:
                    _absorb(q, p, why)
                break
        else:
            out.append(p)
    return out


async def gather_candidates(query: str, year_from=None, year_to=None, per_source=50) -> list[Paper]:
    arx, s2 = await asyncio.gather(
        search_arxiv(query, year_from, year_to, per_source),
        search_s2(query, year_from, year_to, per_source),
    )
    return merge_papers(arx + s2)


# ---------------------------------------------------------------- direct lookups
async def arxiv_by_id(aid: str) -> Paper | None:
    r = await fetch("https://export.arxiv.org/api/query", params={"id_list": aid, "max_results": 1})
    if r.status != 200:
        return None
    e = ET.fromstring(r.text).find("a:entry", ATOM)
    if e is None or not e.findtext("a:title", "", ATOM).strip():
        return None
    aid = norm_arxiv(e.findtext("a:id", "", ATOM)) or aid
    doi_el = e.find("arxiv:doi", ATOM)
    return Paper(title=" ".join(e.findtext("a:title", "", ATOM).split()),
                 authors=[a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)],
                 year=_arxiv_year(aid), abstract=" ".join(e.findtext("a:summary", "", ATOM).split()),
                 venue="arXiv", doi=norm_doi(doi_el.text) if doi_el is not None else None,
                 arxiv_id=aid, url=f"https://arxiv.org/abs/{aid}", pdf_url=f"https://arxiv.org/pdf/{aid}",
                 is_oa=True, oa_status="green", sources=["arxiv"], provenance="lookup:arxiv")


async def paper_from_doi(doi: str) -> Paper | None:
    """Build a record for a published version straight from its registrar metadata."""
    from verifier import resolve_doi, _csl_title, _csl_year
    d = norm_doi(doi)
    if not d:
        return None
    status, csl = await resolve_doi(d)
    if not csl:
        return None
    authors = [f"{a.get('given','')} {a.get('family','')}".strip() for a in csl.get("author") or []]
    ct = csl.get("container-title") or ""
    venue: str = str(ct[0]) if isinstance(ct, list) and ct else str(ct or "")
    return Paper(title=_csl_title(csl), authors=authors, year=_csl_year(csl), venue=venue,
                 doi=d, url=f"https://doi.org/{d}",
                 sources=["doi.org"], provenance="lookup:doi")


# ---------------------------------------------------------------- end-to-end (pre-agent baseline)
def lexical_relevance(p: Paper, question: str, keywords: list[str]) -> float:
    """Cheap first-pass score until embeddings + Gemini re-rank land (Day 2)."""
    text = f"{p.title} {p.title} {p.abstract}".lower()
    q = fuzz.token_set_ratio(question.lower(), text) / 100
    kw = sum(1 for k in keywords if k.lower() in text) / max(len(keywords), 1)
    return round(0.6 * q + 0.4 * kw, 3)


def classify(p: Paper) -> tuple[str, str]:
    v = p.verification or {}
    if not v.get("citable"):
        return "flagged", v.get("reason") or "not verified"
    if not p.is_oa:
        return "paywalled", "no legal OA copy (Unpaywall closed) - link only, no download"
    return "cited", "verified DOI + legal OA copy"


async def run_scout(question: str, keywords: list[str], year_from=None, year_to=None,
                    target: int = 25, log=None) -> dict:
    """Deterministic baseline pipeline: retrieve -> merge -> enrich -> OA -> verify -> classify.
    The Gemini agent loop (Day 2) wraps these same tools and decides when to search again."""
    from verifier import verify_paper
    query = " ".join([question] + keywords)
    log = log or (lambda *a, **k: None)
    cands = await gather_candidates(query, year_from, year_to, per_source=60)
    log("retrieve", f"{len(cands)} unique candidates after merge")
    for p in cands:
        p.relevance = {"lexical": lexical_relevance(p, question, keywords)}
    cands.sort(key=lambda p: p.relevance["lexical"], reverse=True)
    pool = cands[: target * 2 + 10]
    pool = await asyncio.gather(*(enrich_with_s2(p) for p in pool)) if os.getenv("S2_API_KEY") else pool
    pool = merge_papers(list(pool))
    pool = await asyncio.gather(*(check_oa(p) for p in pool))
    pool = await asyncio.gather(*(verify_paper(p) for p in pool))
    from integrity import check_updates
    pool = await asyncio.gather(*(check_updates(p) for p in pool if p.verification.get("citable"))) + \
        [p for p in pool if not p.verification.get("citable")]
    buckets = {"cited": [], "paywalled": [], "flagged": []}
    for p in pool:
        dec, why = classify(p)
        p.relevance["decision_reason"] = why
        buckets[dec].append(p)
        log("decide", f"{dec}: {p.title[:70]}", paper=p.to_dict(), decision=dec, reason=why)
    buckets["cited"] = buckets["cited"][:target]
    return buckets


# ---------------------------------------------------------------- snowballing (citation graph)
async def _openalex_work(p: Paper) -> dict | None:
    ids = []
    if p.openalex_id:
        ids.append(p.openalex_id.rsplit("/", 1)[-1])
    if p.doi:
        ids.append(f"doi:{p.doi}")
    if p.arxiv_id:
        ids.append(f"doi:10.48550/arxiv.{p.arxiv_id}")
    for i in ids:
        r = await fetch(f"https://api.openalex.org/works/{i}", params={"mailto": CONTACT})
        if r.status == 200:
            return r.json()
    return None


async def snowball(seed: Paper, direction: str = "both", limit: int = 25,
                   year_from: int | None = None, year_to: int | None = None) -> list[Paper]:
    """Follow references (backward) and citing papers (forward) of a seed paper.
    Semantic Scholar first; OpenAlex if S2 is rate-limited. Every hit records its provenance."""
    seed_label = seed.arxiv_id or seed.doi or seed.title[:40]
    out: list[Paper] = []
    s2id = seed.s2_id or (f"arXiv:{seed.arxiv_id}" if seed.arxiv_id else (f"DOI:{seed.doi}" if seed.doi else None))
    if s2id:
        for edge, key in (("references", "citedPaper"), ("citations", "citingPaper")):
            if direction not in ("both", edge):
                continue
            try:
                r = await fetch(f"https://api.semanticscholar.org/graph/v1/paper/{s2id}/{edge}",
                                params={"fields": S2_FIELDS, "limit": limit}, headers=_s2_headers(), attempts=3)
            except Exception:
                r = None
            if r and r.status == 200:
                for d in r.json().get("data", []):
                    if (dd := d.get(key)) and dd.get("title"):
                        out.append(_from_s2(dd, prov=f"snowball:{'ref-of' if edge == 'references' else 'cites'}:{seed_label}"))
    if not out:  # OpenAlex fallback
        w = await _openalex_work(seed)
        if w:
            if direction in ("both", "references") and w.get("referenced_works"):
                ids = "|".join(x.rsplit("/", 1)[-1] for x in w["referenced_works"][:limit])
                r = await fetch("https://api.openalex.org/works",
                                params={"filter": f"openalex:{ids}", "per_page": limit, "mailto": CONTACT})
                if r.status == 200:
                    for x in r.json().get("results", []):
                        q = _from_openalex(x); q.provenance = f"snowball:ref-of:{seed_label}"; out.append(q)
            if direction in ("both", "citations"):
                r = await fetch("https://api.openalex.org/works",
                                params={"filter": f"cites:{w['id'].rsplit('/', 1)[-1]}", "per_page": limit,
                                        "sort": "cited_by_count:desc", "mailto": CONTACT})
                if r.status == 200:
                    for x in r.json().get("results", []):
                        q = _from_openalex(x); q.provenance = f"snowball:cites:{seed_label}"; out.append(q)
    for q in out:
        q.sources = sorted(set(q.sources) | {"snowball"})
    return [q for q in out if not ((year_from and q.year and q.year < year_from) or (year_to and q.year and q.year > year_to))]


async def enrich_batch_s2(papers: list[Paper]) -> list[Paper]:
    """One POST /paper/batch call (up to 500 ids) instead of N single lookups."""
    import httpx
    todo = [p for p in papers if p.arxiv_id and not p.doi]
    if not todo or not os.getenv("S2_API_KEY"):
        return papers
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.semanticscholar.org/graph/v1/paper/batch",
                             params={"fields": S2_FIELDS}, headers=_s2_headers(),
                             json={"ids": [f"arXiv:{p.arxiv_id}" for p in todo[:500]]})
        if r.status_code != 200:
            return papers
        for p, d in zip(todo, r.json()):
            if d and (hit := _from_s2(d, prov="lookup:s2")).doi:
                p.doi, p.s2_id = hit.doi, hit.s2_id
                p.venue = hit.venue or p.venue
                p.citation_count = hit.citation_count
                p.sources = sorted(set(p.sources) | {"s2"})
    except Exception:
        pass
    return papers