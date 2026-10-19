"""Offline test of the Gemini loop mechanics with a scripted fake model (no API key, no network for the LLM)."""
import asyncio
from google.genai import types
import agent

SCRIPT = [("I'll start on arXiv.", "search_arxiv", {"query": "x"}),
          ("Now gate the pool.", "verify_pool", {"max_papers": 5}),
          ("Done.", "finish", {"reason": "test"}),             # rejected by code: no search_s2 yet
          ("Search S2 first.", "search_s2", {"query": "x"}),
          ("Done now.", "finish", {"reason": "test"})]


class FakeModels:
    def __init__(self):
        self.i = 0
        self.seen = []

    async def generate_content(self, model, contents, config):
        self.seen.append(len(contents))
        text, name, args = SCRIPT[self.i]; self.i += 1
        c = types.Content(role="model", parts=[types.Part(text=text),
                                               types.Part(function_call=types.FunctionCall(name=name, args=args))])
        return types.GenerateContentResponse(candidates=[types.Candidate(content=c)])


class FakeClient:
    def __init__(self, *a, **k):
        self.aio = type("A", (), {"models": FakeModels()})()


async def _fake_tool(st, **kw):
    return {"ok": True, "tally": st.tally()}


def test_gemini_loop_mechanics(monkeypatch):
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", FakeClient)
    for n in ("search_arxiv", "verify_pool", "search_s2"):
        monkeypatch.setitem(agent.TOOLS, n, _fake_tool)
    st = asyncio.run(agent.run_agent("q", ["k"], 2020, 2025, 25, planner="gemini"))
    tools = [s["tool"] for s in st.trace]
    assert tools[:3] == ["search_arxiv", "verify_pool", "finish"], tools
    assert all(s["planner"] == "gemini" for s in st.trace[:3])
    assert st.trace[0]["thought"] == "I'll start on arXiv."
    assert st.trace[2]["result"]["finished"] is False          # finish gate fired
    assert [s["tool"] for s in st.trace][3:] == ["search_s2", "finish"]
    assert st.trace[-1]["result"]["finished"] is True