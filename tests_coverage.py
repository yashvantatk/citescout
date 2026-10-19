"""Offline tests for the gap-aware coverage map."""
from coverage import coverage_map
from models import Paper


def _p(title, abstract="", year=2023):
    return Paper(title=title, abstract=abstract, year=year)


PAPERS = [
    _p("Dense passage retrieval for open-domain question answering", "dual encoder dense retrieval", 2021),
    _p("Dense retrieval with hard negatives", "dense passage retrieval training", 2022),
    _p("Retrieval-augmented generation for knowledge-intensive tasks", "RAG combines retrieval and generation", 2022),
    _p("Self-RAG: retrieval augmented generation with reflection", "RAG critique tokens", 2023),
    _p("Multi-hop question answering over knowledge graphs", "graph reasoning", 2022),
]


def test_planner_subtopics_become_measured_gaps():
    cm = coverage_map(PAPERS, "retrieval augmented generation", ["RAG", "dense retrieval"], 2021, 2025,
                      subtopics=["dense passage retrieval", "hallucination detection"])
    by = {f["facet"]: f for f in cm["facets"]}
    assert by["dense passage retrieval"]["status"] == "covered" and by["dense passage retrieval"]["papers"] == 2
    assert by["hallucination detection"]["status"] == "gap" and by["hallucination detection"]["papers"] == 0
    assert any(s["facet"] == "hallucination detection" and "hallucination" in s["query"] for s in cm["suggestions"])


def test_recent_years_gap_and_themes():
    cm = coverage_map(PAPERS, "retrieval augmented generation", ["RAG"], 2021, 2025)
    assert any(g["facet"].startswith("recent work") for g in cm["gaps"])  # nothing from 2024-2025
    assert sum(t["count"] for t in cm["themes"]) == len(PAPERS)            # every paper lands in one theme


def test_empty_set_is_safe():
    cm = coverage_map([], "anything", [], 2020, 2024)
    assert cm["papers"] == 0 and cm["themes"] == []
