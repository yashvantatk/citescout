"""Offline tests for the approval gate, BibTeX, idempotency and the post-action verifier."""
import asyncio
import io
import zipfile

import pytest

import export
from export import Approval, assign_keys, build_plan, execute, verify_bundle
from models import Paper

PDF = b"%PDF-1.4\n" + b"x" * 2000


def _p(title, first, year, aid=None, doi=None, oa=True, ok=True):
    return Paper(title=title, authors=[f"Ann {first}", "Bo Li"], year=year, arxiv_id=aid, doi=doi,
                 venue="Proceedings of ACL" if doi else "", is_oa=oa, oa_status="green" if oa else "closed",
                 verification={"citable": ok, "title_score": 100, "reason": "" if ok else "DOI title mismatch"})


@pytest.fixture
def papers(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "EXPORT_DIR", str(tmp_path))
    calls = []

    async def fake_download(url):
        calls.append(url)
        return (None, "not a PDF (landing/login page)") if "bad" in url else (PDF, "ok")

    monkeypatch.setattr(export, "_download", fake_download)
    cited = [_p("Attention Is All You Need", "Vaswani", 2017, aid="1706.03762"),
             _p("Attention and Alignment", "Vaswani", 2017, doi="10.18653/v1/x"),
             _p("Bad Host Paper", "Smith", 2020, doi="10.1/bad")]
    cited[1].pdf_url = "https://aclanthology.org/x.pdf"
    cited[2].pdf_url = "https://bad.example/x"
    pay = [_p("Closed Paper", "Doe", 2019, doi="10.1126/closed", oa=False)]
    flag = [_p("Wrong DOI", "Roe", 2016, doi="10.1038/nature14539", ok=False)]
    return cited, pay, flag, calls


def test_keys_unique_and_scholar_style():
    keys = list(assign_keys([_p("The Attention Model", "Vaswani", 2017), _p("Attention Again", "Vaswani", 2017)]))
    assert keys == ["vaswani2017attention", "vaswani2017attentiona"]


def test_gate_blocks_without_matching_approval(papers):
    cited, pay, flag, calls = papers
    plan = build_plan(cited, pay, flag)
    with pytest.raises(PermissionError):
        asyncio.run(execute(plan, Approval("wrong-hash", True)))
    with pytest.raises(PermissionError):
        asyncio.run(execute(plan, Approval(plan.plan_hash, False)))
    assert calls == []  # nothing fetched before approval


def test_bundle_verifies_and_is_idempotent(papers):
    cited, pay, flag, calls = papers
    plan = build_plan(cited, pay, flag)
    assert all(i["doi"] != "10.1126/closed" for i in plan.items)  # paywalled never planned
    b = asyncio.run(execute(plan, Approval(plan.plan_hash, True, "test")))
    rep = verify_bundle(b.zip_bytes, plan)
    assert rep["passed"], rep
    z = zipfile.ZipFile(io.BytesIO(b.zip_bytes))
    assert len([n for n in z.namelist() if n.startswith("pdfs/")]) == 2  # bad host kept as link only
    assert "@misc{vaswani2017attention" in z.read("references.bib").decode()
    n = len(calls)
    b2 = asyncio.run(execute(plan, Approval(plan.plan_hash, True, "test")))
    assert b2.reused and len(calls) == n  # same plan: no second download
