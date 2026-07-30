import os
import pytest
from src.graph import auditor_graph, load_default_rubric


def test_end_to_end_auditor_graph():
    repo_path = os.getcwd()
    pdf_path = "reports/interim_report.pdf"
    rubric_dims = load_default_rubric()

    initial_state = {
        "repo_url": repo_path,
        "pdf_path": pdf_path,
        "rubric_dimensions": rubric_dims,
        "evidences": {},
        "opinions": [],
        "final_report": None,
        "markdown_output": "",
    }

    final_state = auditor_graph.invoke(initial_state)

    assert final_state["final_report"] is not None
    assert final_state["markdown_output"] != ""
    report = final_state["final_report"]
    assert report.overall_score >= 1.0 and report.overall_score <= 5.0
    assert len(report.criteria) == len(rubric_dims)
    assert "# Automaton Auditor Verdict" in final_state["markdown_output"]
