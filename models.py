"""Core data model shared by every module."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    return d.lower() or None


def norm_arxiv(aid: str | None) -> str | None:
    """'http://arxiv.org/abs/2504.13684v1' -> '2504.13684'."""
    if not aid:
        return None
    a = aid.strip().split("abs/")[-1].split("pdf/")[-1]
    a = re.sub(r"^arxiv:", "", a, flags=re.I)
    a = re.sub(r"v\d+$", "", a).removesuffix(".pdf")
    return a or None


def arxiv_doi(aid: str) -> str:
    """arXiv mints DataCite DOIs: 10.48550/arXiv.<id> (do NOT resolve these on Crossref)."""
    return f"10.48550/arxiv.{aid}".lower()


@dataclass
class Paper:
    title: str
    authors: list[str] = field(default_factory=list)   # "Given Family"
    year: int | None = None
    abstract: str = ""
    venue: str = ""
    doi: str | None = None            # published-version DOI if known
    arxiv_id: str | None = None
    s2_id: str | None = None
    openalex_id: str | None = None
    url: str = ""
    pdf_url: str | None = None        # legal OA PDF only
    is_oa: bool | None = None
    oa_status: str | None = None      # gold/green/hybrid/bronze/closed
    oa_source: str | None = None      # where the OA verdict came from (unpaywall/openalex/none)
    citation_count: int | None = None
    sources: list[str] = field(default_factory=list)   # which tools found it (provenance)
    provenance: str = ""              # e.g. "search:arxiv" or "snowball:ref-of:<id>"
    merged_from: list[dict] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    relevance: dict = field(default_factory=dict)
    publisher: dict = field(default_factory=dict)     # publisher-page evidence (Browserbase); never decides

    @property
    def best_doi(self) -> str | None:
        """DOI we cite: the published one if we have it, else the arXiv DataCite DOI."""
        if self.doi:
            return self.doi
        return arxiv_doi(self.arxiv_id) if self.arxiv_id else None

    @property
    def last_names(self) -> list[str]:
        return [a.split()[-1].lower() for a in self.authors if a.strip()]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["best_doi"] = self.best_doi
        return d