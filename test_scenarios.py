"""Official hackathon test cases TC1-TC4, runnable headless:

    python test_scenarios.py          # runs all four live, writes results to test_case_runs
    pytest -q test_scenarios.py       # same, as pytest

Each TC records expected / actual / evidence in SQLite so the Streamlit TC tab can show them.
"""
from __future__ import annotations

import asyncio
import json
import sys

import db
from models import Paper
from scout_engine import arxiv_by_id, check_oa, classify, merge_papers, paper_from_doi, run_scout
from verifier import verify_doi, verify_paper

# ---- fixtures (all public, all live-checked) ---------------------------------------------
TC1_QUESTION: str = "retrieval augmented generation for question answering"
TC1_KEYWORDS: list[str] = ["retrieval", "RAG", "question answering"]
TC1_YEARS: tuple[int, int] = (2021, 2025)
TC2_ARXIV, TC2_PUBLISHED = "1512.03385", "10.1109/CVPR.2016.90"   # ResNet preprint + CVPR 2016
# Nature (DQN, 2015): closed per Unpaywall. Nature's page is a JS check for scripts, which a real
# Browserbase browser clears by itself (Science's Cloudflare wall did not, so we moved off it).
TC3_PAYWALLED = Paper(title="Human-level control through deep reinforcement learning",
                      authors=["Volodymyr Mnih", "Koray Kavukcuoglu", "David Silver"], year=2015,
                      doi="10.1038/nature14236")
# synthetic: a real title with a DOI that actually belongs to a different paper ("Deep learning", Nature 2015)
TC4_FIXTURE = Paper(title="Deep Residual Learning for Image Recognition",
                    authors=["Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"], year=2016,
                    doi="10.1038/nature14539")
# extra: a famously retracted paper (Wakefield et al., Lancet 1998; retracted 2010)
TC5_RETRACTED = Paper(title="Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive "
                            "developmental disorder in children", authors=["A J Wakefield", "S H Murch"],
                      year=1998, doi="10.1016/S0140-6736(97)11096-0")


async def tc1() -> dict:
    b = await run_scout(TC1_QUESTION, TC1_KEYWORDS, TC1_YEARS[0], TC1_YEARS[1], target=25)
    cited = b["cited"]
    resolved = all(p.verification.get("resolved") and p.verification.get("citable") for p in cited)
    return dict(tc="TC1", passed=len(cited) == 25 and resolved,
                expected="25 papers, every DOI resolved and title/author-matched",
                actual=f"{len(cited)} cited, all resolved={resolved}; {len(b['paywalled'])} paywalled, "
                       f"{len(b['flagged'])} flagged",
                evidence=[{"title": p.title, "doi": p.best_doi, "score": p.verification.get("title_score"),
                           "resolved_title": p.verification.get("resolved_title")} for p in cited])


async def tc2() -> dict:
    pre, pub = await asyncio.gather(arxiv_by_id(TC2_ARXIV), paper_from_doi(TC2_PUBLISHED))
    if pre is None or pub is None:
        return dict(tc="TC2", passed=False, expected="preprint + published version merged",
                    actual=f"lookup failed: arxiv={pre is not None}, doi={pub is not None}", evidence={})
    merged = merge_papers([pre, pub])
    m = merged[0]
    m = await verify_paper(await check_oa(m))
    ok = (len(merged) == 1 and m.doi == TC2_PUBLISHED.lower() and m.arxiv_id == TC2_ARXIV
          and m.verification.get("citable") and m.pdf_url and "arxiv.org" in m.pdf_url)
    return dict(tc="TC2", passed=bool(ok),
                expected="arXiv preprint + CVPR version -> 1 entry citing the published DOI, arXiv PDF as OA copy",
                actual=f"{len(merged)} entry; doi={m.doi}; arxiv={m.arxiv_id}; venue={m.venue}; "
                       f"pdf={m.pdf_url}; verified={m.verification.get('status')}",
                evidence={"merged_from": m.merged_from, "verification": m.verification})


async def tc3() -> dict:
    from publisher import inspect_publisher
    p = await verify_paper(await check_oa(Paper(**{**TC3_PAYWALLED.__dict__})))
    p.publisher = await inspect_publisher(p.doi or "")
    dec, why = classify(p)
    ok = dec == "paywalled" and p.pdf_url is None and p.is_oa is False
    return dict(tc="TC3", passed=ok,
                expected="paywalled paper -> listed separately with DOI link only, no PDF download",
                actual=f"decision={dec}; is_oa={p.is_oa}; oa_status={p.oa_status}; source={p.oa_source}; pdf_url={p.pdf_url}; "
                       f"link=https://doi.org/{p.doi}; publisher page via {p.publisher.get('via')}: "
                       f"{p.publisher.get('verdict') or p.publisher.get('error')}",
                evidence={"unpaywall": f"https://api.unpaywall.org/v2/{p.doi}", "reason": why,
                          "publisher_page": p.publisher,
                          "verification": p.verification})


async def tc4() -> dict:
    f = TC4_FIXTURE
    v = await verify_doi(f.doi or "", f.title, f.authors, f.year)
    p = Paper(**{**f.__dict__})
    p.verification = v.to_dict()
    p.is_oa = True
    dec, why = classify(p)
    ok = v.status == "mismatch" and dec == "flagged"
    return dict(tc="TC4", passed=ok,
                expected="DOI resolves to a different title -> flagged, not cited",
                actual=f"verdict={v.status}; claimed='{f.title}'; resolved='{v.resolved_title}'; "
                       f"title_score={v.title_score}; decision={dec}",
                evidence=v.to_dict())


async def tc5() -> dict:
    from integrity import check_updates
    p = Paper(**{**TC5_RETRACTED.__dict__})
    p = await check_updates(await verify_paper(p))
    p.is_oa = True
    dec, why = classify(p)
    integ = p.verification.get("integrity", {})
    ok = dec == "flagged" and integ.get("status") == "retracted"
    return dict(tc="TC5 (extra)", passed=ok,
                expected="retracted paper (Crossref / Retraction Watch notice) -> flagged, not cited",
                actual=f"integrity={integ.get('status')}; decision={dec}; reason={why}",
                evidence=integ)


async def tc6() -> dict:
    from export import Approval, build_plan, execute
    good = Paper(title="Attention Is All You Need", authors=["Ashish Vaswani"], year=2017, arxiv_id="1706.03762",
                 is_oa=True, oa_status="green", verification={"citable": True})
    closed = Paper(**{**TC3_PAYWALLED.__dict__}, )
    closed.is_oa = False
    plan = build_plan([good, closed], [closed])
    try:
        await execute(plan, Approval("not-this-plan", True))
        blocked = False
    except PermissionError:
        blocked = True
    planned = [i["doi"] for i in plan.items]
    ok = blocked and closed.doi not in planned
    return dict(tc="TC6 (extra)", passed=ok,
                expected="no download without human approval of this exact plan; paywalled never planned",
                actual=f"unapproved execute blocked={blocked}; planned={planned}",
                evidence={"plan_hash": plan.plan_hash, "items": plan.items, "link_only": plan.link_only})


async def run_all(which=("TC1", "TC2", "TC3", "TC4", "TC5", "TC6")) -> list[dict]:
    fns = {"TC1": tc1, "TC2": tc2, "TC3": tc3, "TC4": tc4, "TC5": tc5, "TC6": tc6}
    results = [await fns[t]() for t in which]
    c = db.conn()
    for r in results:
        db.log_tc(c, r["tc"], r["passed"], r["expected"], r["actual"], r["evidence"])
    return results


# ---- pytest entry points ----------------------------------------------------------------
def test_tc1():
    assert asyncio.run(tc1())["passed"]


def test_tc2():
    assert asyncio.run(tc2())["passed"]


def test_tc3():
    assert asyncio.run(tc3())["passed"]


def test_tc4():
    assert asyncio.run(tc4())["passed"]


def test_tc5():
    assert asyncio.run(tc5())["passed"]


def test_tc6():
    assert asyncio.run(tc6())["passed"]


if __name__ == "__main__":
    which = tuple(a.upper() for a in sys.argv[1:]) or ("TC1", "TC2", "TC3", "TC4", "TC5", "TC6")
    for r in asyncio.run(run_all(which)):
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['tc']}  {r['actual']}")