"""CiteScout agent loop: Gemini decides WHAT to do next; code decides what counts.

The model gets 6 tools and a goal ("25 verified open-access papers"). After every tool call it sees
the current tally and decides: search again with a new query, snowball from a strong seed, verify
the pending pool, inspect a publisher page, or finish. It can never mark a paper as cited itself:
verification, merging, OA classification and the approval step are deterministic gates in code.

If no Gemini key is available (or the API fails mid-run), a scripted planner drives the SAME tools,
so the demo never dies. The trace records which planner made each decision.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import net  # noqa: F401  (loads .env, holds STATS)
import re
from dataclasses import dataclass, field

from models import Paper
from scout_engine import (check_oa, classify, enrich_with_s2, lexical_relevance, merge_papers,
                          search_arxiv, search_s2, snowball as _snowball)
from integrity import check_updates
from verifier import verify_paper

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "14"))

SYSTEM = """You are CiteScout, a literature-scouting agent for university faculty.
Goal: assemble {target} RELEVANT, OPEN-ACCESS papers with VERIFIED DOIs on the research question,
published {yf}-{yt}. Code enforces verification and OA; you choose the strategy.

Strategy guidance:
- Start with search_arxiv AND search_s2 using focused queries built from the question + keywords.
- Call verify_pool after each batch of searches; it checks OA + DOI for the most relevant pending papers.
- If cited < {target} after 2-3 searches, call snowball on the 1-3 most relevant cited papers
  (references = foundational work, citations = newer follow-ups). Snowballing is preferred over
  repeating near-identical keyword searches.
- Use reformulated queries (synonyms, sub-topics) if relevance is drifting.
- Call finish when cited >= {target}, or when further searching is clearly not productive.
- Code rules: finish is rejected until search_s2 has run at least once, and while unverified papers
  are pending below target. verify_pool also rejects retracted papers (Crossref/Retraction Watch).
- When paywalled > 0, call inspect_publisher on 1-2 paywalled DOIs (Browserbase reads the publisher page)
  to record evidence of the paywall. It is evidence only and never makes a paper citable.
Before each tool call, state your reasoning in one short sentence."""


@dataclass
class AgentState:
    question: str
    keywords: list[str]
    year_from: int | None
    year_to: int | None
    target: int = 25
    pool: list[Paper] = field(default_factory=list)       # merged, not yet verified
    checked: list[Paper] = field(default_factory=list)    # verified/classified
    trace: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    started: float = field(default_factory=time.time)

    def bucket(self, name: str) -> list[Paper]:
        return [p for p in self.checked if classify(p)[0] == name]

    def tally(self) -> dict:
        return {"cited": len(self.bucket("cited")), "paywalled": len(self.bucket("paywalled")),
                "flagged": len(self.bucket("flagged")), "pending": len(self.pool), "target": self.target}

    def add(self, papers: list[Paper]) -> int:
        """Merge new hits against everything seen so far; returns how many are genuinely new."""
        from scout_engine import _absorb, same_work
        before = len(self.pool)
        fresh = []
        for p in papers:
            dup = next(((q, why) for q in self.checked for ok, why in [same_work(q, p)] if ok), None)
            if dup:
                _absorb(dup[0], p, dup[1])  # already verified: just record the extra version
            else:
                fresh.append(p)
        self.pool = merge_papers(self.pool + fresh)
        for p in self.pool:
            p.relevance = p.relevance or {}
            p.relevance["lexical"] = lexical_relevance(p, self.question, self.keywords)
        return len(self.pool) - before


# ---------------------------------------------------------------- tools (what the model can call)
async def t_search_arxiv(st: AgentState, query: str, limit: int = 40) -> dict:
    hits = await search_arxiv(query, st.year_from, st.year_to, min(limit, 60))
    return {"found": len(hits), "new_unique": st.add(hits), "tally": st.tally()}


async def t_search_s2(st: AgentState, query: str, limit: int = 40) -> dict:
    hits = await search_s2(query, st.year_from, st.year_to, min(limit, 60))
    src = hits[0].sources[0] if hits else "s2"
    return {"found": len(hits), "via": src, "new_unique": st.add(hits), "tally": st.tally()}


async def t_verify_pool(st: AgentState, max_papers: int = 30) -> dict:
    """Deterministic gate: enrich -> OA check -> DOI verification for the top pending papers."""
    st.pool.sort(key=lambda p: p.relevance.get("lexical", 0), reverse=True)
    batch, st.pool = st.pool[:max_papers], st.pool[max_papers:]
    if os.getenv("S2_API_KEY"):
        batch = list(await asyncio.gather(*(enrich_with_s2(p) for p in batch)))
    batch = list(await asyncio.gather(*(check_oa(p) for p in batch)))
    batch = list(await asyncio.gather(*(verify_paper(p) for p in batch)))
    batch = list(await asyncio.gather(*(check_updates(p) if p.verification.get("citable") else _same(p)
                                        for p in batch)))  # retraction/correction gate
    st.checked.extend(batch)
    newly = {k: sum(1 for p in batch if classify(p)[0] == k) for k in ("cited", "paywalled", "flagged")}
    retracted = sum(1 for p in batch if p.verification.get("retracted"))
    return {"checked": len(batch), "newly": newly, "retracted": retracted, "tally": st.tally()}


async def _same(p):
    return p


async def t_snowball(st: AgentState, seed_rank: int = 1, direction: str = "both") -> dict:
    cited = sorted(st.bucket("cited"), key=lambda p: p.relevance.get("lexical", 0), reverse=True)
    if not cited:
        return {"error": "no cited papers yet to use as a seed; search and verify first"}
    seed = cited[max(0, min(seed_rank, len(cited)) - 1)]
    hits = await _snowball(seed, direction, 25, st.year_from, st.year_to)
    return {"seed": seed.title, "found": len(hits), "new_unique": st.add(hits), "tally": st.tally()}


async def t_inspect_publisher(st: AgentState, doi: str) -> dict:
    """Publisher landing page via Browserbase Fetch: evidence for paywalled items. Never changes a decision."""
    from publisher import inspect_publisher
    ev = await inspect_publisher(doi)
    for p in st.bucket("paywalled") + st.bucket("cited") + st.bucket("flagged"):
        if (p.best_doi or "").lower() == ev.get("doi"):
            p.publisher = ev
    return {k: ev.get(k) for k in ("doi", "via", "status", "verdict", "publisher_host", "paywall_markers",
                                   "oa_markers", "error", "note") if ev.get(k) not in (None, [], "")}


async def t_finish(st: AgentState, reason: str = "") -> dict:
    used = {s["tool"] for s in st.trace}
    if "search_s2" not in used:  # code gate: published versions + paywall detection need S2/OpenAlex
        return {"finished": False, "error": "finish rejected: call search_s2 at least once first, so published "
                "versions are merged and paywalled papers are identified", "tally": st.tally()}
    if st.pool and st.tally()["cited"] < st.target:
        return {"finished": False, "error": f"finish rejected: {len(st.pool)} pending papers not yet verified; "
                "call verify_pool first", "tally": st.tally()}
    return {"finished": True, "reason": reason, "tally": st.tally()}


TOOLS = {"search_arxiv": t_search_arxiv, "search_s2": t_search_s2, "verify_pool": t_verify_pool,
         "snowball": t_snowball, "inspect_publisher": t_inspect_publisher, "finish": t_finish}

DECLS = [
    {"name": "search_arxiv", "description": "Keyword search on arXiv (all results are green OA preprints).",
     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}, "limit": {"type": "INTEGER"}},
                    "required": ["query"]}},
    {"name": "search_s2", "description": "Keyword search on Semantic Scholar (falls back to OpenAlex if rate-limited). Finds published versions.",
     "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING"}, "limit": {"type": "INTEGER"}},
                    "required": ["query"]}},
    {"name": "verify_pool", "description": "Run the deterministic gate on the most relevant pending papers: OA check (Unpaywall) and DOI verification (doi.org title/author/year match). Updates the cited/paywalled/flagged tally.",
     "parameters": {"type": "OBJECT", "properties": {"max_papers": {"type": "INTEGER"}}}},
    {"name": "snowball", "description": "Follow the citation graph of the Nth most relevant cited paper: references (older foundations), citations (newer follow-ups) or both.",
     "parameters": {"type": "OBJECT", "properties": {"seed_rank": {"type": "INTEGER"},
                    "direction": {"type": "STRING", "enum": ["references", "citations", "both"]}}}},
    {"name": "inspect_publisher", "description": "Read a paywalled paper's publisher landing page via Browserbase and record paywall/OA evidence. Evidence only: never downloads or makes a paper citable.",
     "parameters": {"type": "OBJECT", "properties": {"doi": {"type": "STRING"}}, "required": ["doi"]}},
    {"name": "finish", "description": "Stop when the target is met or more searching is unproductive.",
     "parameters": {"type": "OBJECT", "properties": {"reason": {"type": "STRING"}}}},
]


async def _call(st: AgentState, name: str, args: dict, thought: str, planner: str, log) -> dict:
    t0 = time.time()
    try:
        res = await TOOLS[name](st, **args) if name in TOOLS else {"error": f"unknown tool {name}"}
    except TypeError as e:
        res = {"error": f"bad arguments: {e}"}
    except Exception as e:  # a source being down/rate-limited is information for the planner, not a crash
        res = {"error": f"{type(e).__name__}: {str(e)[:160]}", "tally": st.tally()}
    st.tool_calls += 1
    step = {"step": len(st.trace) + 1, "planner": planner, "thought": thought, "tool": name, "args": args,
            "result": res, "ms": int((time.time() - t0) * 1000)}
    st.trace.append(step)
    log(step)
    return res


# ---------------------------------------------------------------- planners
async def _scripted(st: AgentState, log) -> None:
    """Fallback planner: same tools, fixed sensible strategy."""
    base = " ".join([st.question] + st.keywords)
    plan = [("search_arxiv", {"query": base}, "Start broad on arXiv: every hit is green OA."),
            ("search_s2", {"query": base}, "Search Semantic Scholar for published versions to merge."),
            ("verify_pool", {"max_papers": 35}, "Gate the most relevant candidates.")]
    for name, a, why in plan:
        await _call(st, name, a, why, "scripted", log)
    rank = 1
    while st.tally()["cited"] < st.target and len(st.trace) < MAX_STEPS:
        if st.pool:
            await _call(st, "verify_pool", {"max_papers": 30}, "Pending candidates left; verify them.", "scripted", log)
        elif rank <= 3:
            await _call(st, "snowball", {"seed_rank": rank, "direction": "both"},
                        f"Short of {st.target}; snowball from cited paper #{rank}.", "scripted", log)
            rank += 1
        else:
            kw = " ".join(st.keywords) or st.question
            await _call(st, "search_arxiv", {"query": kw}, "Reformulate with keywords only.", "scripted", log)
            if not st.pool:
                break
    for p in st.bucket("paywalled")[:2]:
        if p.best_doi and len(st.trace) < MAX_STEPS:
            await _call(st, "inspect_publisher", {"doi": p.best_doi},
                        "Record publisher-page evidence for a paywalled paper.", "scripted", log)
    await _call(st, "finish", {"reason": "target met" if st.tally()["cited"] >= st.target else "sources exhausted"},
                "Stop.", "scripted", log)

GEMINI_TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "45"))
FALLBACK_MODELS = [MODEL, "gemini-3.6-flash", "gemini-3.5-flash-lite"]


async def _generate(client, contents, cfg):
    """Retry overloads (429/503) with backoff; on a hung call (timeout) jump to the next model."""
    last: Exception | None = None
    for model in dict.fromkeys(FALLBACK_MODELS):  # dedupe, keep order
        for wait in (0, 2, 5):
            if wait:
                await asyncio.sleep(wait)
            t0 = time.monotonic()
            g = net.STATS.setdefault(f"gemini ({model})", {"calls": 0, "secs": 0.0, "retries": 0})
            g["calls"] += 1
            g["retries"] += bool(wait)
            try:
                return await asyncio.wait_for(
                    client.aio.models.generate_content(model=model, contents=contents, config=cfg),
                    timeout=GEMINI_TIMEOUT)
            except asyncio.TimeoutError as e:
                last = e
                break  # this model is hanging: try the next one
            except Exception as e:
                last = e
                if not any(c in str(e) for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                    raise  # real error (bad key, bad request): don't retry
            finally:
                g["secs"] += time.monotonic() - t0
    raise RuntimeError(f"All Gemini models busy or timing out: {last!r}")

def _short_thought(parts) -> str:
    """Prefer the model's own reasoning sentence; else the bold heading of its thought summary."""
    said = " ".join(p.text for p in parts if p.text and not p.thought).strip()
    if said:
        return said
    summary = " ".join(p.text for p in parts if p.text and p.thought)
    m = re.search(r"\*\*(.+?)\*\*\s*(.*?[.!?])(\s|$)", summary, re.S)
    if m:
        return f"{m.group(1).strip()}: {' '.join(m.group(2).split())}"
    return " ".join(summary.split())[:200]

async def _gemini(st: AgentState, log) -> None:
    from google import genai
    from google.genai import types

    client = genai.Client()
    why = {"type": "STRING", "description": "One short sentence (max 15 words): why this call, right now."}
    decls = [types.FunctionDeclaration.model_validate({**d, "parameters": {
        **d["parameters"], "properties": {"why": why, **d["parameters"]["properties"]},
        "required": ["why", *d["parameters"].get("required", [])]}}) for d in DECLS]
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM.format(target=st.target, yf=st.year_from or "any", yt=st.year_to or "now"),
        tools=[types.Tool(function_declarations=decls)],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW, include_thoughts=True),
    )
    contents: list[types.ContentUnion] = [types.Content(role="user", parts=[types.Part(text=
        f"Research question: {st.question}\nKeywords: {', '.join(st.keywords)}\n"
        f"Years: {st.year_from}-{st.year_to}\nTarget: {st.target} verified OA papers. Begin.")])]
    while len(st.trace) < MAX_STEPS:
        resp = await _generate(client, contents, cfg)
        if not resp.candidates or resp.candidates[0].content is None:
            raise RuntimeError("Gemini returned no candidate (blocked or empty response)")
        cand = resp.candidates[0].content
        parts = cand.parts or []
        contents.append(cand)  # keep the full turn so Gemini 3 thought signatures survive
        thought = _short_thought(parts)
        calls = [p.function_call for p in parts if p.function_call]
        if not calls:
            break
        out_parts: list[types.Part] = []
        done = False
        for fc in calls:
            name = fc.name or ""
            args = dict(fc.args or {})
            reason = args.pop("why", "") or thought or "(no reasoning text)"
            res = await _call(st, name, args, reason, "gemini", log)
            out_parts.append(types.Part.from_function_response(name=name, response={"result": res}))
            done = done or (name == "finish" and res.get("finished") is True)
        contents.append(types.Content(role="user", parts=out_parts))
        if done:
            return
    if not st.trace or st.trace[-1]["tool"] != "finish" or not st.trace[-1]["result"].get("finished"):
        st.trace.append({"step": len(st.trace) + 1, "planner": "system", "tool": "finish", "args": {},
                         "thought": "Step budget reached; stopping.", "ms": 0,
                         "result": {"finished": True, "reason": "step budget", "tally": st.tally()}})
        log(st.trace[-1])


async def run_agent(question: str, keywords: list[str], year_from=None, year_to=None, target: int = 25,
                    log=None, planner: str | None = None) -> AgentState:
    """planner: 'gemini' | 'scripted' | None (auto: gemini if a key is set)."""
    log = log or (lambda step: None)
    st = AgentState(question, keywords, year_from, year_to, target)
    use_gemini = planner == "gemini" or (planner is None and (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")))
    if use_gemini:
        try:
            await _gemini(st, log)
        except Exception as e:
            print(f"\nGemini error: {e}\n")
            st.trace.append({"step": len(st.trace) + 1, "planner": "system", "tool": "fallback",
                             "thought": f"Gemini failed ({str(e)[:120]}); switching to scripted planner.",
                             "args": {}, "result": {}, "ms": 0})
            log(st.trace[-1])
    if st.tally()["cited"] < target and (not use_gemini or st.trace[-1]["tool"] == "fallback"):
        await _scripted(st, log)
    return st

if __name__ == "__main__":
    def show(s):
        print(f"[{s['step']}] {s['planner']:8} {s['tool']:14} {s['ms']/1000:6.1f}s  {s['thought'][:70]}")

    st = asyncio.run(run_agent("How do transformers improve neural machine translation?",
                               ["transformer", "attention", "machine translation"], 2017, 2024,
                               log=show))
    print("\nResult:", st.tally(), f"in {time.time() - st.started:.0f}s")
    for host, v in sorted(net.STATS.items(), key=lambda kv: -kv[1]["secs"]):
        print(f"  {host:28} {v['calls']:4} calls {v['secs']:7.1f}s  {v['retries']} retries")