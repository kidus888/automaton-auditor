import os
import tempfile
import textwrap

from src.tools.repo_tools import analyze_graph_structure

LINEAR = """
from langgraph.graph import StateGraph, START, END
builder = StateGraph(dict)
builder.add_node("RepoInvestigator", a)
builder.add_node("DocAnalyst", b)
builder.add_node("Judge", c)
builder.add_edge(START, "RepoInvestigator")
builder.add_edge("RepoInvestigator", "DocAnalyst")
builder.add_edge("DocAnalyst", "Judge")
builder.add_edge("Judge", END)
"""

# Wiring through loops, a named list join, and a list-returning router
LOOPED = """
from langgraph.graph import StateGraph, START, END
DETECTIVES = ["RepoInvestigator", "DocAnalyst", "VisionInspector"]
JUDGES = ["Prosecutor", "Defense", "TechLead"]

def route(state):
    if not state["evidence"]:
        return "ErrorHandler"
    return JUDGES

def build():
    g = StateGraph(dict)
    for d in DETECTIVES:
        g.add_edge(START, d)
    g.add_edge(DETECTIVES, "Aggregator")
    g.add_conditional_edges("Aggregator", route, JUDGES + ["ErrorHandler"])
    g.add_edge(JUDGES, "ChiefJustice")
    g.add_edge("ChiefJustice", END)
    return g.compile()
"""


def _repo_with_graph(code):
    tmp = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(tmp.name, "src"))
    with open(os.path.join(tmp.name, "src", "graph.py"), "w") as f:
        f.write(textwrap.dedent(code))
    return tmp


def test_linear_graph_is_orchestration_fraud():
    with _repo_with_graph(LINEAR) as repo:
        ev = analyze_graph_structure(repo)
    assert ev.found is False
    assert any("ORCHESTRATION FRAUD" in g for g in ev.gaps)


def test_keyword_mentions_alone_do_not_pass():
    # names every node in a comment/string but wires nothing in parallel
    code = LINEAR + '\nNOTE = "Prosecutor Defense TechLead EvidenceAggregator parallel fan-out"\n'
    with _repo_with_graph(code) as repo:
        assert analyze_graph_structure(repo).found is False


def test_loop_and_router_fanout_detected():
    with _repo_with_graph(LOOPED) as repo:
        ev = analyze_graph_structure(repo)
    assert ev.found is True, ev.gaps
    assert ev.gaps == []
    assert "Aggregator" in ev.rationale and "ErrorHandler" in ev.rationale


def test_self_graph_is_robust_swarm():
    ev = analyze_graph_structure(os.getcwd())
    assert ev.found is True
    assert ev.gaps == []
    assert "EvidenceAggregator" in ev.content
