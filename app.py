"""CiteScout AI - Streamlit UI.

Pages: Scout (ask -> agent -> review -> approve), Insights (metrics), Test cases, How it works.
Design system and HTML components live in ui.py.
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
import ui  # noqa: E402
from agent import MODEL, run_agent  # noqa: E402
from annotate import annotate  # noqa: E402
from coverage import coverage_map  # noqa: E402
from export import Approval, build_plan, execute, verify_bundle  # noqa: E402

st.set_page_config(page_title="CiteScout AI", page_icon="📚", layout="wide", initial_sidebar_state="collapsed")
ui.inject()
ss = st.session_state

EXAMPLES = {
    "Medical image segmentation": ("How is deep learning used for medical image segmentation?",
                                   "medical image segmentation, U-Net, deep learning, MRI, CT", (2018, 2024)),
    "IoT intrusion detection": ("How are machine learning methods used for intrusion detection in IoT networks?",
                                "intrusion detection, IoT security, machine learning, anomaly detection", (2019, 2024)),
    "Transformers for translation": ("How do transformers improve neural machine translation?",
                                     "transformer, attention, machine translation", (2017, 2024)),
}
# Re-assigning keeps widget values alive when the user switches pages (Streamlit drops unrendered widget keys).
for k, v in {"q": EXAMPLES["Medical image segmentation"][0], "kw": EXAMPLES["Medical image segmentation"][1],
             "yf": 2018, "yt": 2024, "n": 25, "dark": False}.items():
    ss[k] = ss[k] if k in ss else v


def _use_example():
    name = ss.get("example")
    if name in EXAMPLES:
        ss.q, ss.kw, (ss.yf, ss.yt) = EXAMPLES[name]


def _status_chips() -> str:
    def c(label, ok, off="off"):
        return ui.chip(f"{label}{'' if ok else ' · ' + off}", "teal" if ok else "ghost", dot=True)
    return "".join([
        c(f"Gemini · {MODEL}", bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")), "scripted planner"),
        c("Semantic Scholar", bool(os.getenv("S2_API_KEY")), "shared pool"),
        c("OpenAlex", bool(os.getenv("OPENALEX_API_KEY")), "keyless"),
        c("Browserbase", bool(os.getenv("BROWSERBASE_API_KEY")), "plain fetch")])


# ================================================================== Scout
def page_scout():
    stage = 4 if "bundle" in ss else 3 if "result" in ss else 1
    ui.md('<div class="hero"><div class="eyebrow">Agentic literature scout · bibliography builder</div>'
          '<h1>CiteScout<span class="ai">AI</span></h1>'
          '<div class="tagline">Twenty-five papers you can <em>actually cite</em>.</div>'
          '<p class="lede">Ask a research question. An AI agent searches arXiv and Semantic Scholar, merges preprints '
          'with their journal versions and checks every DOI. Nothing is downloaded until you approve.</p></div>')
    ui.stepper(stage)

    with st.container(border=True):
        top_l, top_r = st.columns([3, 2], vertical_alignment="center")
        with top_l:
            mode = st.segmented_control("Mode", ["Live agent", "Replay a recording"], default="Live agent",
                                        key="mode", label_visibility="collapsed",
                                        help="Replay re-plays a real recorded run instantly: no network, no LLM.")
        with top_r:
            ui.md(f'<div class="chips" style="justify-content:flex-end">{_status_chips()}</div>')

        pick, go = "", False
        if mode != "Replay a recording":
            st.pills("Try an example", list(EXAMPLES), key="example", on_change=_use_example)
            st.text_area("Research question", key="q", height=80,
                         help="Write it the way you'd ask a colleague. The agent plans its searches from this.")
            c1, c2, c3, c4 = st.columns([3.2, 1, 1, 1])
            c1.text_input("Keywords", key="kw", help="Comma-separated. Used for searching and for the coverage map.")
            c2.number_input("From year", 1990, 2026, key="yf", step=1)
            c3.number_input("To year", 1990, 2026, key="yt", step=1)
            c4.number_input("Papers", 5, 50, key="n", help="How many verified open-access papers to cite.")
            bad_years = int(ss.yf) > int(ss.yt)
            if bad_years:
                st.caption(":red[“From year” must be on or before “To year”.]")
            go = st.button("Scout papers", type="primary", icon=":material/travel_explore:", width="stretch",
                           disabled=not ss.q.strip() or bad_years)
        else:
            runs = replay.list_runs()
            if not runs:
                st.info("No recordings yet. Every live run is recorded automatically, so do one live run first.")
            else:
                pick = st.selectbox("Recorded run", runs, help="Saved automatically after each live run.")
                go = st.button("Replay run", type="primary", icon=":material/play_arrow:", width="stretch")

    if go:
        _run(mode or "Live agent", pick)

    if "result" in ss:
        _results()
    else:
        ui.md('<div class="section">What you\'ll get</div>')
        a, b, c = st.columns(3)
        a.markdown("**Verified citations**  \nEvery DOI is resolved at doi.org. Title, authors and year must all match "
                   "or the paper is flagged, never cited.")
        b.markdown("**Honest about paywalls**  \nClosed papers are listed as links only. A real browser checks the "
                   "publisher page as evidence. No bypasses, ever.")
        c.markdown("**Ready for your paper**  \nA ranked list explaining why each one is worth citing, then a ZIP "
                   "with legal PDFs and a clean BibTeX file.")


def _run(mode: str, pick: str):
    for k in ("result", "bundle", "report"):
        ss.pop(k, None)
    status = st.status("The agent is working…", expanded=True)
    box = status.empty()
    steps: list[dict] = []
    if mode != "Replay a recording":
        q, keywords = ss.q.strip(), [k.strip() for k in ss.kw.split(",") if k.strip()]
        yf, yt, n = int(ss.yf), int(ss.yt), int(ss.n)
        net.reset_metrics()
        conn = db.conn()
        rid = db.new_run(conn, q, keywords, yf, yt)
        t0 = time.time()

        def log(step):
            steps.append(step)
            box.markdown(ui.timeline(steps, live_last=True), unsafe_allow_html=True)
            status.update(label=f"Step {len(steps)} · {ui.TOOL_LABEL.get(step['tool'], step['tool'])}…")

        stt = asyncio.run(run_agent(q, keywords, yf, yt, target=n, log=log))
        status.update(label="Ranking and writing why-cite notes…")
        cited = annotate(stt.bucket("cited"), q, keywords, yf, yt)[:n]
        b = {"cited": cited, "paywalled": stt.bucket("paywalled"), "flagged": stt.bucket("flagged")}
        for name, ps in b.items():
            for p in ps:
                db.log_decision(conn, rid, p.to_dict(), name, p.relevance.get("decision_reason", ""))
        for ev in net.EVIDENCE:
            db.log_evidence(conn, rid, ev["host"], ev["url"], ev["status"], ev["cached"])
        meta = {"question": q, "keywords": keywords, "year_from": yf, "year_to": yt, "target": n,
                "seconds": round(time.time() - t0, 1), "tool_calls": stt.tool_calls, "run_id": rid, "model": MODEL}
        stats, evidence = copy.deepcopy(net.STATS), list(net.EVIDENCE)
        db.finish_run(conn, rid, "awaiting_approval", {k: len(v) for k, v in b.items()})
        replay.save_run(meta, steps, b, stats, evidence)
    else:
        rec = replay.load_run(pick)
        meta, b, stats, evidence = rec["meta"], rec["buckets"], rec["stats"], rec["evidence"]
        for s in rec["steps"]:
            steps.append(s)
            box.markdown(ui.timeline(steps, live_last=True), unsafe_allow_html=True)
            time.sleep(0.3)
    box.markdown(ui.timeline(steps), unsafe_allow_html=True)
    status.update(label=f"Done · {len(b['cited'])} papers cited in {len(steps)} agent steps", state="complete",
                  expanded=False)
    ss.result = {"buckets": b, "steps": steps, "meta": meta, "stats": stats, "evidence": evidence,
                 "plan": build_plan(b["cited"], b["paywalled"], b["flagged"]),
                 "replay": mode == "Replay a recording"}
    ss.fresh = True
    st.toast(f"{len(b['cited'])} verified papers ready to review", icon=":material/task_alt:")


def _results():
    R = ss.result
    b, plan, meta = R["buckets"], R["plan"], R["meta"]
    anim = bool(ss.pop("fresh", False))
    if R["replay"]:
        st.caption(f"Recorded run from {meta.get('recorded_at', '')} · originally took {meta.get('seconds')}s")

    ui.md(f'<div class="section">Results for “{ui.e(meta.get("question", ""))}”</div>')
    ui.stats([("Cited", len(b["cited"]), "verified + open access", "teal"),
              ("Paywalled", len(b["paywalled"]), "listed as links only", "amber"),
              ("Flagged", len(b["flagged"]), "wrong DOI or retracted", "rose"),
              ("Agent steps", len(R["steps"]), f"{meta.get('seconds', '–')}s end to end", "")], anim)

    cov_steps = [s for s in R["steps"] if s.get("tool") == "coverage_gaps"]
    subtopics = (cov_steps[-1].get("args") or {}).get("subtopics") if cov_steps else None
    cov = coverage_map(b["cited"], meta.get("question", ""), meta.get("keywords", []),
                       meta.get("year_from"), meta.get("year_to"), subtopics)
    R["coverage"] = cov

    t_cited, t_pay, t_flag, t_cov, t_trace = st.tabs([
        f"Cited · {len(b['cited'])}", f"Paywalled · {len(b['paywalled'])}", f"Flagged · {len(b['flagged'])}",
        f"Coverage · {len(cov['gaps'])} gaps", f"Agent trace · {len(R['steps'])}"])

    with t_cited:
        f1, f2, f3 = st.columns([3, 1.4, 0.8], vertical_alignment="bottom")
        needle = f1.text_input("Filter", placeholder="Filter by title, author or venue",
                               label_visibility="collapsed", key="filter")
        order = f2.selectbox("Sort", ["Best match", "Newest", "Most cited"], label_visibility="collapsed")
        with f3.popover("Scores", icon=":material/help:", width="stretch"):
            st.markdown("**How each paper is scored (0–10)**\n\n"
                        "- **Topic** and **Method**: Gemini, and only if it quotes the abstract word for word\n"
                        "- **Impact**: citations per year, log-scaled (code)\n"
                        "- **Recency**: where it sits in your year range (code)\n\n"
                        "Overall = 40% topic + 25% method + 20% impact + 15% recency.")
        ps = list(b["cited"])
        if needle:
            n = needle.lower()
            ps = [p for p in ps if n in f"{p.title} {' '.join(p.authors)} {p.venue}".lower()]
        if order == "Newest":
            ps.sort(key=lambda p: p.year or 0, reverse=True)
        elif order == "Most cited":
            ps.sort(key=lambda p: p.citation_count or 0, reverse=True)
        if not ps:
            st.caption("No papers match that filter.")
        for i, p in enumerate(ps, 1):
            ui.paper_card(i, p, anim)

    with t_pay:
        ui.md('<div class="callout">These papers fit your question, but no <b>legal</b> free copy exists, so '
              'CiteScout links to them and never downloads them. For a few, the agent opens the publisher page '
              'in a real cloud browser (Browserbase) and records proof of the paywall.</div>')
        st.write("")
        if not b["paywalled"]:
            st.caption("None in this run.")
        for i, p in enumerate(b["paywalled"]):
            ui.paywalled_card(p, anim, i)

    with t_flag:
        ui.md('<div class="callout">Flagged papers <b>never</b> reach your bibliography. Either their DOI '
              'resolves to a different paper, or Crossref lists them as retracted.</div>')
        st.write("")
        if not b["flagged"]:
            st.caption("Nothing flagged in this run. Every candidate's DOI matched its paper.")
        for i, p in enumerate(b["flagged"]):
            ui.flagged_card(p, anim, i)

    with t_cov:
        ui.md('<div class="callout">Does the list cover <b>your whole question</b>, or only its most popular corner? '
              'Gemini proposes the subtopics a thorough review needs. Code counts the papers that really cover each '
              'one, and the agent searches the gaps before it finishes.</div>')
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            ui.md(f'<div class="section">Coverage of your request · gap = fewer than {cov["threshold"]} papers</div>')
            ui.md(ui.facet_rows(cov["facets"], cov["threshold"], cov["papers"]))
        with c2:
            ui.md('<div class="section">Themes in the list</div>')
            ui.md('<div class="chips">' + "".join(ui.chip(f"{t['theme']} · {t['count']}", "indigo" if t["theme"] != "Other" else "")
                                                  for t in cov["themes"]) + "</div>")
            if cov["years"]:
                ui.md('<div class="section">Papers per year</div>')
                ui.md(ui.year_bars(cov["years"]))

    with t_trace:
        ui.md('<div class="callout">Every step below was <b>chosen by the planner</b>. The one-line reason is its own. '
              'Code checks every result, and can refuse to let the agent finish.</div>')
        st.write("")
        ui.md(ui.timeline(R["steps"]))

    _approval(R, plan)


def _approval(R, plan):
    ui.md('<div class="section">Step 4 · your approval</div>')
    with st.container(border=True):
        n_pdf = sum(1 for i in plan.items if i["pdf_url"])
        st.markdown(f"#### Download {n_pdf} open-access PDFs and build `references.bib`")
        st.caption(f"{len(plan.items)} cited papers go into the BibTeX file. {len(plan.link_only)} paywalled papers "
                   f"stay as links only. You're approving this exact plan, fingerprint `{plan.plan_hash}`.")
        with st.expander("See exactly what will be downloaded"):
            ui.table([{"cite key": i["key"], "title": i["title"], "open access": i["oa_status"],
                       "PDF": i["pdf_url"]} for i in plan.items], links={"PDF"})
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        agree = c1.checkbox("I approve downloading these legal open-access PDFs")
        if c2.button("Approve & build bundle", type="primary", disabled=not agree, width="stretch",
                     icon=":material/verified:"):
            extra = {"agent_trace.json": json.dumps(R["steps"], indent=1, default=str),
                     "source_evidence.json": json.dumps(R["evidence"], indent=1),
                     "coverage_map.json": json.dumps(R.get("coverage", {}), indent=1)}
            with st.spinner("Downloading PDFs, writing BibTeX, then re-checking everything…"):
                bundle = asyncio.run(execute(plan, Approval(plan.plan_hash, True, "faculty"), extra=extra))
            ss.bundle, ss.report = bundle, verify_bundle(bundle.zip_bytes, plan)
            if R["meta"].get("run_id") and not R["replay"]:
                db.finish_run(db.conn(), R["meta"]["run_id"], "exported",
                              {"pdfs": bundle.receipt["pdfs"], "verified": ss.report["passed"]})
            st.toast("Bundle built and verified", icon=":material/inventory_2:")
            st.rerun()

        if "bundle" in ss:
            rep = ss.report
            ok = sum(1 for c in rep["checks"] if c["ok"])
            ui.md('<div class="chips" style="margin:.6rem 0">'
                  + ui.chip(f"{ss.bundle.receipt['pdfs']} PDFs + BibTeX", "teal", dot=True)
                  + ui.chip(f"post-download checks {ok}/{len(rep['checks'])}", "teal" if rep["passed"] else "rose", dot=True)
                  + (ui.chip("reused, no re-download", "ghost") if ss.bundle.reused else "") + "</div>")
            with st.expander("What the post-download verifier checked"):
                for c in rep["checks"]:
                    st.markdown(f"{'✅' if c['ok'] else '❌'} **{c['check']}** · {c['detail']}")
            st.download_button("Download ZIP", ss.bundle.zip_bytes, file_name=f"citescout_{plan.plan_hash}.zip",
                               mime="application/zip", type="primary", icon=":material/download:", width="stretch")


# ================================================================== Insights
def page_insights():
    ui.md('<div class="eyebrow">Insights</div><div class="page-title">What the agent did, in numbers</div>'
          '<p class="page-sub">Every API call, retry and cache hit from the last run, plus the full source evidence '
          'log.</p>')
    if "result" not in ss:
        st.info("Run or replay a scout first. Its numbers show up here.")
        st.page_link(SCOUT, label="Go to Scout", icon=":material/arrow_forward:")
        return
    R = ss.result
    b, steps, stats = R["buckets"], R["steps"], R["stats"]
    checked = sum(len(v) for v in b.values())
    llm_s = sum(v["secs"] for k, v in stats.items() if k.startswith("gemini"))
    api_s = sum(v["secs"] for k, v in stats.items() if not k.startswith("gemini"))
    planners: dict[str, int] = {}
    for s in steps:
        planners[s["planner"]] = planners.get(s["planner"], 0) + 1
    ui.stats([("Total time", f"{R['meta'].get('seconds', '–')}s", "question to ranked list", ""),
              ("LLM time", f"{llm_s:.0f}s", "Gemini planning + notes", ""),
              ("API time", f"{api_s:.0f}s", "arXiv, S2, doi.org, …", ""),
              ("Sources consulted", len(R["evidence"]), "requests logged as evidence", "")])
    ui.stats([("Verification pass rate", f"{100 * len(b['cited']) / checked:.0f}%" if checked else "–",
               "checked papers that were cited", "teal"),
              ("Retracted caught", sum(1 for p in b["flagged"] if p.verification.get("retracted")), "never cited", "rose"),
              ("Versions merged", sum(len(p.merged_from) for p in b["cited"]), "preprint + journal → one", ""),
              ("Found by snowballing", sum(1 for p in b["cited"] if "snowball" in (p.provenance or "")),
               "via the citation graph", "")])
    ui.md('<div class="chips">' + "".join(ui.chip(f"{k} planned {v} steps", "indigo") for k, v in planners.items())
          + "</div>")
    rows = [{"source": k, "calls": v.get("calls", 0), "cache hits": v.get("cache_hits", 0),
             "seconds": round(v.get("secs", 0), 1), "retries": v.get("retries", 0)}
            for k, v in sorted(stats.items(), key=lambda kv: -kv[1].get("secs", 0))]
    ui.md('<div class="section">Time per source</div>')
    if rows:
        ui.md(ui.hbars([(r["source"], r["seconds"]) for r in rows], unit="s"))
    ui.md('<div class="section">API calls per source</div>')
    ui.table(rows)
    with st.expander(f"Source evidence log · {len(R['evidence'])} requests"):
        ui.table(R["evidence"], links={"url"})


# ================================================================== Test cases
TC_TITLES = {"TC1": "Topic query returns verified papers", "TC2": "Preprint + journal version merged",
             "TC3": "Paywalled paper: link only, no download", "TC4": "DOI points to a different paper",
             "TC5": "Retracted paper is caught", "TC6": "No download without approval"}


def page_tests():
    ui.md('<div class="eyebrow">Proof</div><div class="page-title">Test cases</div>'
          '<p class="page-sub">The four official scenarios plus two extra integrity checks, run against the live APIs. '
          'Each result is stored with its evidence. Nine more offline tests run on GitHub Actions on every push.</p>')
    if st.button("Run all test cases live", icon=":material/play_arrow:", type="primary",
                 help="About a minute: real arXiv, Semantic Scholar, doi.org, Unpaywall, Crossref and Browserbase calls."):
        from test_scenarios import run_all
        with st.spinner("Running TC1–TC6 against the live sources…"):
            asyncio.run(run_all())
        st.toast("Test run finished", icon=":material/task_alt:")
    conn = db.conn()
    rows = conn.execute("""SELECT t.tc, t.passed, t.expected, t.actual, t.evidence, t.ts FROM test_case_runs t
                           JOIN (SELECT tc, MAX(ts) AS m FROM test_case_runs GROUP BY tc) x
                           ON t.tc = x.tc AND t.ts = x.m ORDER BY t.tc""").fetchall()
    if not rows:
        st.info("No test runs stored yet. Use the button above.")
        return
    passed = sum(r[1] for r in rows)
    ui.stats([("Passing", f"{passed}/{len(rows)}", "latest run of each case", "teal" if passed == len(rows) else "rose"),
              ("Official", "4", "TC1–TC4 from the brief", ""), ("Extra", "2", "retraction · approval gate", ""),
              ("Offline (CI)", "9", "mocked agent, export, Browserbase, coverage", "")])
    for tc, ok, exp, act, ev, ts in rows:
        key = tc.split(":")[0].strip()
        with st.expander(f"{'✅' if ok else '❌'}  {key} · {TC_TITLES.get(key, exp)}"):
            st.markdown(f"**Expected:** {exp}")
            st.markdown(f"**Got:** {act}")
            st.caption(f"Last run {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}")
            st.json(json.loads(ev), expanded=False)


# ================================================================== How it works
def page_how():
    ui.md('<div class="eyebrow">How it works</div><div class="page-title">The AI plans. Code decides what counts.</div>'
          '<p class="page-sub">Gemini chooses which source to search, when to verify, when to follow citations and when '
          'to stop. It can never mark a paper as cited itself: every citation passes deterministic checks, and '
          'downloads wait for a human.</p>')
    nodes = [("you", "human", "Ask", "question, years, keywords"),
             ("ai", "ai", "Plan", "Gemini picks the next tool and says why"),
             ("tools", "", "Act", "arXiv · Semantic Scholar · citations · publisher pages"),
             ("code", "code", "Check", "DOI match · merge · open access · retraction"),
             ("ai", "ai", "Rank", "scores quote the abstract word for word"),
             ("you", "human", "Approve", "then PDFs + BibTeX, re-verified")]
    ui.md('<div class="flow">' + "".join(
        f'<div class="node {c} anim" style="animation-delay:{i * 70}ms"><div class="l">{l}</div><div class="t">{t}</div>'
        f'<div class="d">{d}</div></div>' for i, (l, c, t, d) in enumerate(nodes)) + "</div>")

    ui.md('<div class="section">Guardrails the AI can\'t talk its way past</div>')
    g = st.columns(3)
    g[0].markdown("**Finish gate**  \nThe agent can't stop until it has searched Semantic Scholar, verified every "
                  "pending paper and checked paywalled publishers.")
    g[1].markdown("**Quote check**  \nEach relevance score must quote the abstract word for word. Made-up quotes get "
                  "the AI's scores thrown out.")
    g[2].markdown("**Approval gate**  \nDownloads need your approval of an exact plan fingerprint. Paywalled papers "
                  "can never enter the plan.")
    g = st.columns(3)
    g[0].markdown("**DOI verification**  \nResolved at doi.org. The title must match (≥ 90), authors overlap and the "
                  "year be within ±1.")
    g[1].markdown("**Fail-closed open access**  \nUnpaywall → OpenAlex → unknown. Unknown is never downloaded.")
    g[2].markdown("**Post-download verifier**  \nNine checks: real PDFs, BibTeX re-parses, unique keys, hashes match, "
                  "no paywalled file.")

    ui.md('<div class="section">The agent\'s tools</div>')
    ui.table([
        {"tool": "search_arxiv", "what it does": "keyword search; every hit is a green open-access preprint"},
        {"tool": "search_s2", "what it does": "Semantic Scholar (OpenAlex fallback); finds journal versions and paywalls"},
        {"tool": "verify_pool", "what it does": "open-access check → DOI verification → retraction check"},
        {"tool": "snowball", "what it does": "follows references and citations of a top paper, keeping provenance"},
        {"tool": "coverage_gaps", "what it does": "measures coverage of the subtopics a review needs"},
        {"tool": "inspect_publisher", "what it does": "Browserbase opens the publisher page; evidence only, never bypasses"},
        {"tool": "finish", "what it does": "stop, but code can refuse"},
    ], mono={"tool"})
    ui.md('<div class="section">Sources</div><div class="chips">' + "".join(ui.chip(s, "ghost") for s in [
        "arXiv", "Semantic Scholar", "OpenAlex", "doi.org", "Unpaywall", "Crossref", "Publisher pages · Browserbase",
        "Gemini 3.8 Flash"]) + "</div>")


SCOUT = st.Page(page_scout, title="Scout", icon=":material/travel_explore:", default=True)
nav = st.navigation([SCOUT,
                     st.Page(page_insights, title="Insights", icon=":material/monitoring:", url_path="insights"),
                     st.Page(page_tests, title="Test cases", icon=":material/fact_check:", url_path="tests"),
                     st.Page(page_how, title="How it works", icon=":material/schema:", url_path="how")],
                    position="top")
_l, _r = st.columns([6, 1], vertical_alignment="center")
_r.toggle("Dark mode", key="dark")
if ss.dark:
    ui.inject_dark()
nav.run()