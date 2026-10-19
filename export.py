"""Approval-gated action: download legal OA PDFs + build BibTeX + ZIP, then verify the bundle.

Flow (the human stays in control of the only side-effecting step):
    plan   = build_plan(cited, paywalled, flagged)      # pure: what WOULD be downloaded
    ok     = Approval(plan.plan_hash, approved=True, by="faculty")   # given in the UI
    bundle = await execute(plan, ok)                    # refuses without a matching approval
    report = verify_bundle(bundle.zip_bytes, plan)      # independent post-action check

Hard rules enforced in code, not left to the LLM:
- only papers that passed DOI verification AND have a legal OA copy are ever downloaded
- paywalled items are link-only; a response that is not a real PDF (%PDF magic) is discarded
- the same approved plan never downloads twice (plan_hash = idempotency key)
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import re
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field

import net
from models import Paper

EXPORT_DIR = os.getenv("EXPORT_DIR", "exports")
MAX_PDF_BYTES = int(os.getenv("MAX_PDF_MB", "40")) * 1024 * 1024
net.HOST_INTERVAL.setdefault("arxiv.org", 1.0)  # be polite to arXiv when pulling PDFs

STOP = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "via", "is", "are", "towards",
        "toward", "using", "from", "by", "at", "how", "what", "why", "do", "does", "can", "we", "your"}
CONF_HINT = re.compile(r"conference|proceedings|workshop|symposium|\b(cvpr|iccv|eccv|neurips|nips|icml|iclr|"
                       r"acl|emnlp|naacl|coling|aaai|ijcai|kdd|sigir|www|interspeech|icassp)\b", re.I)


# ---------------------------------------------------------------- cite keys + BibTeX
def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def base_key(p: Paper) -> str:
    """Google-Scholar-style key: vaswani2017attention."""
    last = _ascii(p.authors[0].split()[-1]) if p.authors else "anon"
    word = next((w for w in re.findall(r"[a-z0-9]+", _ascii(p.title).lower()) if w not in STOP), "paper")
    return re.sub(r"[^a-z0-9]", "", last.lower()) + str(p.year or "nd") + word


def assign_keys(papers: list[Paper]) -> dict[str, Paper]:
    """Unique keys; collisions get a/b/c suffixes in input order (vaswani2017attention, ...attentiona)."""
    out: dict[str, Paper] = {}
    for p in papers:
        k, n = base_key(p), 0
        cand = k
        while cand in out:
            cand = k + chr(ord("a") + n)
            n += 1
        out[cand] = p
    return out


def _tex(s: str) -> str:
    s = " ".join((s or "").split())
    return re.sub(r"([&%$#_])", r"\\\1", s).replace("{", "").replace("}", "")


def _bib_author(name: str) -> str:
    parts = name.split()
    return name if len(parts) < 2 else f"{parts[-1]}, {' '.join(parts[:-1])}"


def bib_entry(key: str, p: Paper) -> str:
    arxiv_only = bool(p.arxiv_id) and (not p.doi or p.doi.startswith("10.48550"))
    if arxiv_only:
        etype, venue_field = "misc", None
    elif CONF_HINT.search(p.venue or ""):
        etype, venue_field = "inproceedings", "booktitle"
    else:
        etype, venue_field = "article", "journal"
    f = [("title", "{" + _tex(p.title) + "}"),  # double braces keep capitalisation (BERT, GPT)
         ("author", " and ".join(_tex(_bib_author(a)) for a in p.authors) or "Anonymous"),
         ("year", str(p.year or ""))]
    if venue_field and p.venue:
        f.append((venue_field, _tex(p.venue)))
    if p.best_doi:
        f.append(("doi", p.best_doi))
    if p.arxiv_id:
        f += [("eprint", p.arxiv_id), ("archivePrefix", "arXiv")]
    f.append(("url", f"https://doi.org/{p.best_doi}" if p.best_doi else p.url))
    body = ",\n".join(f"  {k:<13} = {{{v}}}" for k, v in f if v)
    return f"@{etype}{{{key},\n{body}\n}}\n"


def to_bibtex(keyed: dict[str, Paper]) -> str:
    return "\n".join(bib_entry(k, p) for k, p in keyed.items())


# ---------------------------------------------------------------- plan + approval gate
def legal_pdf_url(p: Paper) -> str | None:
    if p.pdf_url:
        return p.pdf_url
    if p.arxiv_id:  # every arXiv preprint is green OA
        return f"https://arxiv.org/pdf/{p.arxiv_id}"
    return None


@dataclass
class Plan:
    items: list[dict]            # {key, title, doi, pdf_url, oa_status}
    link_only: list[dict]        # paywalled: never downloaded
    flagged: list[dict]          # failed verification: not cited
    keyed: dict[str, Paper] = field(repr=False, default_factory=dict)
    plan_hash: str = ""


@dataclass
class Approval:
    plan_hash: str
    approved: bool
    by: str = "user"
    at: float = field(default_factory=time.time)


def build_plan(cited: list[Paper], paywalled: list[Paper] = (), flagged: list[Paper] = ()) -> Plan:
    """Pure function: nothing is fetched. Only verified + OA papers can enter the download list."""
    safe = [p for p in cited if p.is_oa and (p.verification or {}).get("citable")]
    keyed = assign_keys(safe)
    items = [{"key": k, "title": p.title, "doi": p.best_doi, "pdf_url": legal_pdf_url(p),
              "oa_status": p.oa_status} for k, p in keyed.items()]
    link_only = [{"title": p.title, "doi": p.best_doi, "url": f"https://doi.org/{p.best_doi}"} for p in paywalled]
    flag = [{"title": p.title, "doi": p.best_doi, "reason": (p.verification or {}).get("reason", "")} for p in flagged]
    h = hashlib.sha256(json.dumps([(i["key"], i["doi"], i["pdf_url"]) for i in items]).encode()).hexdigest()[:16]
    return Plan(items, link_only, flag, keyed, h)


# ---------------------------------------------------------------- the action
async def _download(url: str) -> tuple[bytes | None, str]:
    """Stream one PDF; never cached; returns (bytes, note). Rejects HTML/login pages."""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    note = "failed"
    for attempt in range(2):
        await net._pace(host)
        try:
            async with net.client().stream("GET", url, timeout=60) as r:
                if r.status_code in (429, 503) and attempt == 0:
                    await asyncio.sleep(3)
                    continue
                if r.status_code != 200:
                    return None, f"HTTP {r.status_code}"
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_PDF_BYTES:
                        return None, "too large"
            data = bytes(buf)
            if not data.startswith(b"%PDF"):
                return None, "not a PDF (landing/login page) - kept as link"
            return data, "ok"
        except Exception as e:  # network issue on one file must not kill the bundle
            note = f"{type(e).__name__}"
    return None, note


@dataclass
class Bundle:
    zip_bytes: bytes
    receipt: dict
    reused: bool = False


def _csv(keyed: dict[str, Paper]) -> str:
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(["key", "title", "year", "venue", "doi", "oa_status", "score", "why_cite", "evidence_quote", "found_via"])
    for k, p in keyed.items():
        r = p.relevance or {}
        w.writerow([k, p.title, p.year, p.venue, p.best_doi, p.oa_status, r.get("score"),
                    r.get("why_cite", ""), r.get("evidence_quote", ""), p.provenance])
    return s.getvalue()


def _markdown(plan: Plan) -> str:
    out = ["# CiteScout bibliography\n", f"## Cited ({len(plan.items)})\n"]
    for i, (k, p) in enumerate(plan.keyed.items(), 1):
        r = p.relevance or {}
        out.append(f"{i}. **{p.title}** ({p.year}). {p.venue}. https://doi.org/{p.best_doi}  \n"
                   f"   `{k}` · score {r.get('score', '-')} · {r.get('why_cite', '')}\n")
    out.append(f"\n## Paywalled, link only ({len(plan.link_only)})\n")
    out += [f"- {x['title']} · {x['url']}\n" for x in plan.link_only]
    out.append(f"\n## Flagged, not cited ({len(plan.flagged)})\n")
    out += [f"- {x['title']} · {x['reason']}\n" for x in plan.flagged]
    return "".join(out)


async def execute(plan: Plan, approval: Approval, concurrency: int = 4, extra: dict[str, str] | None = None) -> Bundle:
    """The only side-effecting step. Refuses unless a human approved THIS exact plan."""
    if not (approval and approval.approved and approval.plan_hash == plan.plan_hash):
        raise PermissionError("Download not approved for this plan (approval missing or plan changed).")
    os.makedirs(EXPORT_DIR, exist_ok=True)
    cached = os.path.join(EXPORT_DIR, f"citescout_{plan.plan_hash}.zip")
    if os.path.exists(cached):  # idempotent: same approved plan -> same bundle, no re-download
        data = open(cached, "rb").read()
        receipt = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("receipt.json"))
        return Bundle(data, receipt, reused=True)

    sem = asyncio.Semaphore(concurrency)

    async def one(item):
        if not item["pdf_url"]:
            return item, None, "no legal PDF URL - link only"
        async with sem:
            data, note = await _download(item["pdf_url"])
        return item, data, note

    t0 = time.time()
    results = await asyncio.gather(*(one(i) for i in plan.items))
    files, ledger = {}, []
    for item, data, note in results:
        entry = {**item, "downloaded": data is not None, "note": note}
        if data is not None:
            files[f"pdfs/{item['key']}.pdf"] = data
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["bytes"] = len(data)
        ledger.append(entry)
    receipt = {
        "plan_hash": plan.plan_hash, "approved_by": approval.by, "approved_at": approval.at,
        "built_at": time.time(), "seconds": round(time.time() - t0, 1),
        "cited": len(plan.items), "pdfs": len(files), "link_only": plan.link_only, "flagged": plan.flagged,
        "items": ledger,
        "verification": {k: p.verification for k, p in plan.keyed.items()},  # per-entry verification receipt
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("references.bib", to_bibtex(plan.keyed))
        z.writestr("bibliography.md", _markdown(plan))
        z.writestr("papers.csv", _csv(plan.keyed))
        z.writestr("receipt.json", json.dumps(receipt, indent=2, default=str))
        for name, data in files.items():
            z.writestr(name, data)
        for name, text in (extra or {}).items():  # e.g. agent_trace.json, source_evidence.json
            z.writestr(name, text)
    data = buf.getvalue()
    with open(cached, "wb") as fh:
        fh.write(data)
    return Bundle(data, receipt)


# ---------------------------------------------------------------- independent post-action verifier
def verify_bundle(zip_bytes: bytes, plan: Plan) -> dict:
    """Re-open the ZIP from scratch and check what was actually produced, not what we meant to produce."""
    import bibtexparser
    checks: list[tuple[str, bool, str]] = []
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = set(z.namelist())
    need = {"references.bib", "bibliography.md", "papers.csv", "receipt.json"}
    checks.append(("required files present", need <= names, ", ".join(sorted(need - names)) or "all 4"))

    bib = bibtexparser.loads(z.read("references.bib").decode("utf-8"))
    keys = [e["ID"] for e in bib.entries]
    checks.append(("BibTeX re-parses", len(bib.entries) == len(plan.items),
                   f"{len(bib.entries)} entries, expected {len(plan.items)}"))
    checks.append(("cite keys unique", len(keys) == len(set(keys)), f"{len(keys)} keys"))
    no_doi = [e["ID"] for e in bib.entries if not e.get("doi")]
    checks.append(("every entry has a DOI", not no_doi, ", ".join(no_doi) or "ok"))

    pdfs = [n for n in names if n.startswith("pdfs/")]
    bad = [n for n in pdfs if not z.read(n)[:4] == b"%PDF"]
    checks.append(("every PDF is a real PDF (%PDF magic)", not bad, ", ".join(bad) or f"{len(pdfs)} PDFs"))
    orphan = [n for n in pdfs if n[5:-4] not in keys]
    checks.append(("every PDF matches a BibTeX key", not orphan, ", ".join(orphan) or "ok"))

    receipt = json.loads(z.read("receipt.json"))
    checks.append(("bundle matches approved plan", receipt.get("plan_hash") == plan.plan_hash, receipt.get("plan_hash", "")))
    paywalled_dois = {x["doi"] for x in plan.link_only}
    leaked = [i["doi"] for i in receipt["items"] if i.get("downloaded") and i["doi"] in paywalled_dois]
    checks.append(("no paywalled paper downloaded", not leaked, ", ".join(leaked) or "ok"))
    sha_bad = [i["key"] for i in receipt["items"] if i.get("downloaded")
               and hashlib.sha256(z.read(f"pdfs/{i['key']}.pdf")).hexdigest() != i["sha256"]]
    checks.append(("PDF checksums match receipt", not sha_bad, ", ".join(sha_bad) or "ok"))
    return {"passed": all(ok for _, ok, _ in checks),
            "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks]}


if __name__ == "__main__":  # end-to-end: agent -> plan -> (auto-approve for CLI) -> bundle -> verify
    from agent import run_agent

    async def main():
        st = await run_agent("How do transformers improve neural machine translation?",
                             ["transformer", "attention", "machine translation"], 2017, 2024,
                             log=lambda s: print(f"[{s['step']}] {s['tool']:12} {s['thought'][:70]}"))
        plan = build_plan(st.bucket("cited")[:25], st.bucket("paywalled"), st.bucket("flagged"))
        print(f"\nPlan {plan.plan_hash}: {len(plan.items)} PDFs, {len(plan.link_only)} link-only, {len(plan.flagged)} flagged")
        if input("Approve download? [y/N] ").strip().lower() != "y":
            return print("Not approved. Nothing downloaded.")
        b = await execute(plan, Approval(plan.plan_hash, True, "cli"))
        print(f"Bundle: {len(b.zip_bytes)//1024} KB, {b.receipt['pdfs']} PDFs, reused={b.reused}")
        for c in verify_bundle(b.zip_bytes, plan)["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["check"] + " · " + c["detail"])

    asyncio.run(main())