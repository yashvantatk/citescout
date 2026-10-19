"""CiteScout AI - Streamlit UI.

Gemini agent loop -> annotated results -> human approval -> verified ZIP (PDFs + BibTeX + receipts),
with a metrics panel, live test cases, and an offline replay mode for the demo.
"""
from __future__ import annotations

import os

import streamlit as st

try:  # Streamlit Cloud: copy Secrets into env BEFORE modules read their config at import time
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)):
            os.environ.setdefault(_k, str(_v))
except Exception:
    pass

import asyncio  # noqa: E402
import copy  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import db  # noqa: E402
import net  # noqa: E402
import replay  # noqa: E402
from agent import MODEL, run_agent  # noqa: E402
from annotate import annotate  # noqa: E402
from export import Approval, build_plan, execute, verify_bundle  # noqa: E402
from coverage import coverage_map  # noqa: E402

st.set_page_config(page_title="CiteScout AI", page_icon="📚", layout="wide")
ss = st.session_state  # results survive the rerun that every button click causes

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Mode")
    mode = st.radio("Run mode", ["Live agent", "Replay a recorded run"], label_visibility="collapsed",
                    help="Replay shows a real recorded run instantly, with no network or LLM calls.")
    st.caption(f"Planner model: `{MODEL}`")
    st.caption("Gemini key: " + ("✅ set" if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                                  else "❌ missing (scripted planner will run)"))
    st.caption("Semantic Scholar key: " + ("✅" if os.getenv("S2_API_KEY") else "–")
               + " · OpenAlex key: " + ("✅" if os.getenv("OPENALEX_API_KEY") else "–"))
    st.caption("Browserbase (publisher pages): " + ("✅" if os.getenv("BROWSERBASE_API_KEY") else "– plain fetch"))

st.title("📚 CiteScout AI")
st.caption("Literature scout & bibliography builder: 25 relevant open-access papers, every DOI verified, "
           "nothing downloaded without your approval.")

tab_run, tab_metrics, tab_tc, tab_arch = st.tabs(["Scout", "Metrics", "Test cases", "How it works"])


def show_step(container, s):
    res = s["result"]
    tally = res.get("tally", {})
    extra = f" · ⚠️ {res['error']}" if res.get("error") else ""
    container.markdown(f"**{s['step']}. `{s['tool']}`** · _{s['planner']}_ · {s['thought']}  \n"
                       f"→ cited {tally.get('cited', '–')}, paywalled {tally.get('paywalled', '–')}, "
                       f"flagged {tally.get('flagged', '–')}, pending {tally.get('pending', '–')} · "
                       f"{s['ms'] / 1000:.1f}s{extra}")


# ------------------------------------------------------------------ Scout tab
with tab_run:
    q, kw, yf, yt, target, pick = "", "", 2017, 2024, 25, ""  # defaults (keeps type checkers happy)
    if mode == "Live agent":
        q = st.text_input("Research question", "How do transformers improve neural machine translation?")
        kw = st.text_input("Keywords (comma-separated)", "transformer, attention, machine translation")
        c1, c2, c3 = st.columns(3)
        yf = c1.number_input("From year", 1990, 2030, 2017)
        yt = c2.number_input("To year", 1990, 2030, 2024)
        target = c3.number_input("Papers wanted", 5, 50, 25)
        go = st.button("🔎 Scout", type="primary")
    else:
        runs = replay.list_runs()
        if not runs:
            st.info("No recordings yet. Do one live run: every live run is saved automatically.")
            go = False
        else:
            pick = st.selectbox("Recorded run", runs)
            go = st.button("▶️ Replay", type="primary")

    if go:
        for k in ("result", "bundle", "report"):
            ss.pop(k, None)
        trace = st.status("Agent working…", expanded=True)
        if mode == "Live agent":
            net.reset_metrics()
            conn = db.conn()
            keywords = [k.strip() for k in kw.split(",") if k.strip()]
            rid = db.new_run(conn, q, keywords, yf, yt)
            steps: list[dict] = []
            t0 = time.time()

            def log(step):
                steps.append(step)
                show_step(trace, step)

            stt = asyncio.run(run_agent(q, keywords, int(yf), int(yt), target=int(target), log=log))
            trace.update(label="Ranking and annotating…")
            cited = annotate(stt.bucket("cited"), q, keywords, int(yf), int(yt))[: int(target)]
            b = {"cited": cited, "paywalled": stt.bucket("paywalled"), "flagged": stt.bucket("flagged")}
            for name, ps in b.items():
                for p in ps:
                    db.log_decision(conn, rid, p.to_dict(), name, p.relevance.get("decision_reason", ""))
            for e in net.EVIDENCE:
                db.log_evidence(conn, rid, e["host"], e["url"], e["status"], e["cached"])
            meta = {"question": q, "keywords": keywords, "year_from": int(yf), "year_to": int(yt),
                    "target": int(target), "seconds": round(time.time() - t0, 1), "tool_calls": stt.tool_calls,
                    "run_id": rid, "model": MODEL}
            stats, evidence = copy.deepcopy(net.STATS), list(net.EVIDENCE)
            db.finish_run(conn, rid, "awaiting_approval", {k: len(v) for k, v in b.items()})
            replay.save_run(meta, steps, b, stats, evidence)
        else:
            rec = replay.load_run(pick)
            meta, steps, b, stats, evidence = rec["meta"], rec["steps"], rec["buckets"], rec["stats"], rec["evidence"]
            st.caption(f"🎞️ Recorded {meta.get('recorded_at')} · originally took {meta.get('seconds')}s")
            for s in steps:
                show_step(trace, s)
                time.sleep(0.35)
        trace.update(label=f"Done: {len(b['cited'])} cited · {len(steps)} agent steps", state="complete")
        ss.result = {"buckets": b, "steps": steps, "meta": meta, "stats": stats, "evidence": evidence,
                     "plan": build_plan(b["cited"], b["paywalled"], b["flagged"]), "replay": mode != "Live agent"}

    if "result" in ss:
        R = ss.result
        b, plan = R["buckets"], R["plan"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Cited (verified + OA)", len(b["cited"]))
        m2.metric("Paywalled (link only)", len(b["paywalled"]))
        m3.metric("Flagged (not cited)", len(b["flagged"]))
        m4.metric("Agent steps", len(R["steps"]))

        with st.expander(f"🧠 Agent trace ({len(R['steps'])} steps)"):
            for s in R["steps"]:
                show_step(st, s)

        cov_steps = [s for s in R["steps"] if s.get("tool") == "coverage_gaps"]
        subtopics = (cov_steps[-1].get("args") or {}).get("subtopics") if cov_steps else None
        cmeta = R["meta"]
        cov = coverage_map(b["cited"], cmeta.get("question", ""), cmeta.get("keywords", []),
                           cmeta.get("year_from"), cmeta.get("year_to"), subtopics)
        R["coverage"] = cov
        with st.expander(f"🧭 Coverage map: {len(cov['themes'])} themes · {len(cov['gaps'])} gaps", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Themes in the cited set**")
                for t in cov["themes"]:
                    st.markdown(f"- {t['theme']}: {t['count']} papers")
            with c2:
                st.markdown(f"**Coverage of the request** (gap = fewer than {cov['threshold']} papers)")
                for f in cov["facets"]:
                    icon = "🟥" if f["status"] == "gap" else "🟩"
                    st.markdown(f"{icon} {f['facet']}: {f['papers']} · _{f.get('source', '')}_")
            if cov["years"]:
                st.bar_chart({str(y): c for y, c in cov["years"].items()}, height=160)
            if cov["gaps"]:
                gs = [s for s in R["steps"] if "coverage gap" in (s.get("thought") or "").lower()
                      or (s.get("tool") in ("search_arxiv", "search_s2", "snowball") and cov_steps
                          and s["step"] > cov_steps[-1]["step"])]
                st.caption(f"The agent ran {len(gs)} follow-up step(s) after the coverage check. "
                           "Remaining gaps are listed so you can judge them yourself.")

        st.subheader(f"Cited ({len(b['cited'])})")
        for i, p in enumerate(b["cited"], 1):
            v, r = p.verification, p.relevance
            integ = (v.get("integrity") or {}).get("status", "–")
            merged = f" · merged {len(p.merged_from) + 1} versions" if p.merged_from else ""
            st.markdown(f"**{i}. {p.title}** ({p.year}) · {p.venue or 'arXiv'}  \n"
                        f"DOI [{p.best_doi}](https://doi.org/{p.best_doi}) ✅ title {v.get('title_score')} · "
                        f"OA {p.oa_status} · integrity {integ} · found via {p.provenance}{merged}")
            st.caption(f"Score {r.get('score')} · topic {r.get('topic_match')} · method {r.get('method_match')} · "
                       f"recency {r.get('recency')} · impact {r.get('citation_impact')} · quote {r.get('quote_check')}")
            if r.get("why_cite"):
                st.markdown(f"> {r.get('why_cite')}")
            if r.get("evidence_quote"):
                st.caption(f"Abstract: “{r.get('evidence_quote')}”")
        st.subheader(f"Paywalled, link only ({len(b['paywalled'])})")
        if not b["paywalled"]:
            st.caption("None in this run.")
        for p in b["paywalled"]:
            pub = getattr(p, "publisher", {}) or {}
            extra = ""
            if pub.get("verdict"):
                extra = f" · publisher page ({pub.get('via')}): **{pub['verdict']}**"
                if pub.get("paywall_markers"):
                    extra += f" (“{pub['paywall_markers'][0]}”)"
                if pub.get("replay_url"):
                    extra += f" · [session replay]({pub['replay_url']})"
            st.markdown(f"- {p.title} · [doi.org/{p.best_doi}](https://doi.org/{p.best_doi}) · no legal OA copy{extra}")
        st.subheader(f"Flagged, not cited ({len(b['flagged'])})")
        if not b["flagged"]:
            st.caption("None in this run.")
        for p in b["flagged"]:
            st.markdown(f"- {p.title} · {p.verification.get('reason')}")

        # ---------------- human approval gate: the only step with side effects
        st.divider()
        st.subheader("🛡️ Approval required")
        n_pdf = sum(1 for i in plan.items if i["pdf_url"])
        st.write(f"Download **{n_pdf} legal open-access PDFs** and build `references.bib` for {len(plan.items)} "
                 f"papers. {len(plan.link_only)} paywalled papers stay link-only. Plan `{plan.plan_hash}`.")
        with st.expander("See exactly what will be downloaded"):
            st.dataframe([{"key": i["key"], "title": i["title"], "oa": i["oa_status"], "pdf": i["pdf_url"]}
                          for i in plan.items], width="stretch")
        agree = st.checkbox("I approve downloading these open-access PDFs")
        if st.button("Approve & build bundle", disabled=not agree):
            extra = {"agent_trace.json": json.dumps(R["steps"], indent=1, default=str),
                     "source_evidence.json": json.dumps(R["evidence"], indent=1),
                     "coverage_map.json": json.dumps(R.get("coverage", {}), indent=1)}
            with st.spinner("Downloading PDFs and building the bundle…"):
                bundle = asyncio.run(execute(plan, Approval(plan.plan_hash, True, "faculty"), extra=extra))
            ss.bundle, ss.report = bundle, verify_bundle(bundle.zip_bytes, plan)
            if R["meta"].get("run_id") and not R["replay"]:
                db.finish_run(db.conn(), R["meta"]["run_id"], "exported",
                              {"pdfs": bundle.receipt["pdfs"], "verified": ss.report["passed"]})

        if "bundle" in ss:
            rep = ss.report
            st.success(f"{ss.bundle.receipt['pdfs']} PDFs + BibTeX ready"
                       + (" (reused, no re-download)" if ss.bundle.reused else ""))
            st.markdown(f"**Post-action verifier: {'✅ all checks passed' if rep['passed'] else '❌ issues found'}**")
            for c in rep["checks"]:
                st.markdown(f"{'✅' if c['ok'] else '❌'} {c['check']} · {c['detail']}")
            st.download_button("⬇️ Download ZIP", ss.bundle.zip_bytes,
                               file_name=f"citescout_{plan.plan_hash}.zip", mime="application/zip")

# ------------------------------------------------------------------ Metrics tab
with tab_metrics:
    if "result" not in ss:
        st.info("Run or replay a scout to see its metrics.")
    else:
        R = ss.result
        b, steps, stats = R["buckets"], R["steps"], R["stats"]
        checked = sum(len(v) for v in b.values())
        llm_s = sum(v["secs"] for k, v in stats.items() if k.startswith("gemini"))
        api_s = sum(v["secs"] for k, v in stats.items() if not k.startswith("gemini"))
        planners = {}
        for s in steps:
            planners[s["planner"]] = planners.get(s["planner"], 0) + 1
        c = st.columns(4)
        c[0].metric("Total time", f"{R['meta'].get('seconds', '–')}s")
        c[1].metric("LLM time", f"{llm_s:.0f}s")
        c[2].metric("API time", f"{api_s:.0f}s")
        c[3].metric("Sources consulted", len(R["evidence"]))
        c = st.columns(4)
        c[0].metric("Verification pass rate", f"{100 * len(b['cited']) / checked:.0f}%" if checked else "–")
        c[1].metric("Retracted caught", sum(1 for p in b["flagged"] if p.verification.get("retracted")))
        c[2].metric("Versions merged", sum(len(p.merged_from) for p in b["cited"]))
        c[3].metric("Found by snowballing", sum(1 for p in b["cited"] if "snowball" in (p.provenance or "")))
        st.caption("Planner decisions: " + ", ".join(f"{k} × {v}" for k, v in planners.items()))
        st.subheader("API calls per source")
        st.dataframe([{"source": k, "calls": v.get("calls", 0), "cache hits": v.get("cache_hits", 0),
                       "seconds": round(v.get("secs", 0), 1), "retries": v.get("retries", 0)}
                      for k, v in sorted(stats.items(), key=lambda kv: -kv[1].get("secs", 0))],
                     width="stretch")
        with st.expander(f"Source evidence log ({len(R['evidence'])} requests)"):
            st.dataframe(R["evidence"], width="stretch")

# ------------------------------------------------------------------ Test cases tab
with tab_tc:
    st.write("Official TC1–TC4 plus two extra integrity tests. Results are stored with their evidence.")
    if st.button("▶️ Run all test cases live (about 1 minute)"):
        from test_scenarios import run_all
        with st.spinner("Running TC1–TC6 against live arXiv, Semantic Scholar, doi.org, Unpaywall, Crossref…"):
            asyncio.run(run_all())
    conn = db.conn()
    rows = conn.execute("""SELECT t.tc, t.passed, t.expected, t.actual, t.evidence, t.ts FROM test_case_runs t
                           JOIN (SELECT tc, MAX(ts) AS m FROM test_case_runs GROUP BY tc) x
                           ON t.tc = x.tc AND t.ts = x.m ORDER BY t.tc""").fetchall()
    if not rows:
        st.info("No test runs yet. Click the button above or run `python test_scenarios.py`.")
    else:
        st.metric("Pass rate", f"{sum(r[1] for r in rows)}/{len(rows)}")
    for tc, passed, exp, act, ev, ts in rows:
        with st.expander(f"{'✅' if passed else '❌'} {tc}: {exp}"):
            st.caption(f"Last run {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}")
            st.write(act)
            st.json(json.loads(ev), expanded=False)

# ------------------------------------------------------------------ How it works tab
with tab_arch:
    st.graphviz_chart("""
digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#eef3ff", fontname="Helvetica"];
  user [label="Faculty\\nquestion, years, keywords", fillcolor="#fff4d6"];
  gemini [label="Gemini planner\\n(function calling)", fillcolor="#e3f6e8"];
  tools [label="Tools\\nsearch_arxiv · search_s2\\nsnowball · verify_pool\\ninspect_publisher (Browserbase)\\ncoverage_gaps · finish"];
  gates [label="Code gates\\nmerge versions · DOI verify\\nOA check · retraction check\\nfinish rules", fillcolor="#ffe3e3"];
  rank [label="Rank + annotate\\nquote-checked"];
  approve [label="Human approval\\n(plan hash)", fillcolor="#fff4d6"];
  action [label="Download legal OA PDFs\\nBibTeX · ZIP"];
  verify [label="Post-action verifier\\n9 checks", fillcolor="#ffe3e3"];
  user -> gemini -> tools -> gates -> gemini [label="tally"];
  gates -> rank -> approve -> action -> verify; }""")
    st.markdown("""
**The LLM decides the strategy; code decides what counts.** Gemini chooses which source to search, when to
verify, when to follow the citation graph, and when to stop. It can never mark a paper as cited itself.

- **Sources:** arXiv, Semantic Scholar (OpenAlex fallback), doi.org, Unpaywall, Crossref / Retraction Watch
- **Verification:** each DOI resolved at doi.org, then title, authors and year are matched against the claim
- **Merging:** preprint + published version become one entry citing the published DOI
- **Integrity:** retracted papers are flagged and never cited
- **Safety:** paywalled papers are link-only; nothing is downloaded until you approve the exact plan
- **Proof:** every request is logged as source evidence and shipped in the ZIP with a per-paper receipt
""")