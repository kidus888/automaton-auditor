import os

from src.graph import auditor_graph, load_default_rubric, route_after_aggregation
from src.state import Evidence

REPORT = "reports/interim_report.md"


def _state(repo, pdf):
    return {
        "repo_url": repo,
        "pdf_path": pdf,
        "rubric_dimensions": load_default_rubric(),
        "evidences": {},
        "opinions": [],
        "final_report": None,
        "markdown_output": "",
    }


def test_end_to_end_auditor_graph():
    rubric_dims = load_default_rubric()
    final_state = auditor_graph.invoke(_state(os.getcwd(), REPORT))

    report = final_state["final_report"]
    assert report is not None
    assert 1.0 <= report.overall_score <= 5.0
    assert len(report.criteria) == len(rubric_dims)
    # three opinions per criterion: every judge saw every criterion
    assert len(final_state["opinions"]) == 3 * len(rubric_dims)
    md = final_state["markdown_output"]
    assert md.index("## Executive Summary") < md.index("## Criterion Breakdown") < md.index("## Remediation Plan")
    # ContextBuilder loaded the synthesis rules for the Chief Justice
    assert "security_override" in final_state["synthesis_rules"]


def test_unreachable_repo_routes_to_evidence_missing():
    final_state = auditor_graph.invoke(_state("https://invalid.invalid/nobody/nothing.git", REPORT))
    assert final_state["opinions"] == []  # the bench was never convened
    assert any(e.startswith("EvidenceMissing") for e in final_state["errors"])
    assert all(c.final_score == 1 for c in final_state["final_report"].criteria)


def test_router_fans_out_to_all_three_judges():
    state = {
        "rubric_dimensions": load_default_rubric(),
        "evidences": {"github_repo": [Evidence(goal="x", found=True, location="a", rationale="ok")]},
    }
    assert route_after_aggregation(state) == ["Prosecutor", "Defense", "TechLead"]
    assert route_after_aggregation({"rubric_dimensions": [], "evidences": {}}) == "EvidenceMissing"


def test_compiled_graph_topology():
    g = auditor_graph.get_graph()
    nodes = set(g.nodes)
    assert {"ContextBuilder", "EvidenceAggregator", "EvidenceMissing", "ChiefJustice"} <= nodes
    edges = {(e.source, e.target) for e in g.edges}
    for detective in ("RepoInvestigator", "DocAnalyst", "VisionInspector"):
        assert ("ContextBuilder", detective) in edges
        assert (detective, "EvidenceAggregator") in edges
    for judge in ("Prosecutor", "Defense", "TechLead"):
        assert ("EvidenceAggregator", judge) in edges
        assert (judge, "ChiefJustice") in edges
