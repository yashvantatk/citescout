"""Research-integrity gate: is a paper retracted or corrected? (Crossref + Retraction Watch data)

Crossref links every retraction/correction notice to the work it updates via `update-to`, and since
2023 it carries the Retraction Watch database too. Asking `filter=updates:<doi>` returns every notice
that points at our paper. Retracted -> flagged, never cited. Corrected -> cited, with a warning.
arXiv DataCite DOIs (10.48550/...) are not in Crossref, so they are marked "not applicable".
"""
from __future__ import annotations

from models import Paper
from net import CONTACT, fetch

SERIOUS = {"retraction", "withdrawal", "removal", "expression_of_concern"}


async def check_updates(p: Paper) -> Paper:
    doi = p.doi if p.doi and not p.doi.startswith("10.48550") else None
    v = dict(p.verification or {})
    if not doi:
        v["integrity"] = {"status": "n/a", "note": "preprint DOI (DataCite) - not covered by Crossref"}
        p.verification = v
        return p
    try:
        r = await fetch("https://api.crossref.org/works",
                        params={"filter": f"updates:{doi}", "rows": 20, "mailto": CONTACT})
        items = r.json()["message"]["items"] if r.status == 200 else None
    except Exception as e:  # integrity lookup failing must not silently pass a paper
        items, err = None, type(e).__name__
    else:
        err = f"HTTP {r.status}"
    if items is None:
        v["integrity"] = {"status": "unknown", "note": f"Crossref lookup failed ({err})"}
        p.verification = v
        return p
    notices = []
    for it in items:
        for u in it.get("update-to") or []:
            if (u.get("DOI") or "").lower() == doi.lower():
                notices.append({"type": u.get("type"), "label": u.get("label"), "notice_doi": it.get("DOI"),
                                "source": u.get("source"), "date": (u.get("updated") or {}).get("date-parts", [[None]])[0]})
    serious = [n for n in notices if (n["type"] or "").lower() in SERIOUS]
    v["integrity"] = {"status": "retracted" if serious else ("corrected" if notices else "clean"), "notices": notices}
    if serious:
        v["retracted"] = True
        v["citable"] = False
        v["reason"] = f"{serious[0]['label']} notice {serious[0]['notice_doi']} - not cited"
    p.verification = v
    return p
