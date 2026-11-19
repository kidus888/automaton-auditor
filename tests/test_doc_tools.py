import os
import tempfile

from src.state import Evidence, ReportClaims
from src.tools.doc_tools import (
    analyze_text_diagrams,
    cross_reference_report_accuracy,
    extract_report_claims,
    ingest_pdf,
    query_report,
    search_theoretical_depth,
)

REPORT = """# Executive Summary

We use Dialectical Synthesis, Metacognition, Fan-In / Fan-Out and State Synchronization.

## Judicial Layer

Dialectical Synthesis is executed by three parallel judge nodes (Prosecutor, Defense, TechLead) in
src/nodes/judges.py. The ChiefJustice node resolves them with deterministic rules.

## Orchestration

The graph uses fan-out from EvidenceAggregator via add_conditional_edges and fan-in to ChiefJustice.
State synchronization relies on the operator.add reducer in src/state.py so parallel node writes merge.

## Unrelated

Our cats enjoy sunshine. The office has plants. Lunch is at noon.
"""


def _report(text=REPORT):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False)
    tmp.write(text)
    tmp.close()
    return tmp.name


def test_ingest_chunks_by_section():
    path = _report()
    try:
        chunks = ingest_pdf(path)
        sections = {c.section for c in chunks}
        assert {"Executive Summary", "Judicial Layer", "Orchestration", "Unrelated"} <= sections
    finally:
        os.unlink(path)


def test_query_report_retrieves_relevant_chunk():
    path = _report()
    try:
        top_chunk, _ = query_report(path, "What does the report say about Dialectical Synthesis?")[0]
        assert "Dialectical Synthesis" in top_chunk.text
        assert top_chunk.section != "Unrelated"
    finally:
        os.unlink(path)


def test_theoretical_depth_separates_explained_from_dropped():
    path = _report()
    try:
        ev = search_theoretical_depth(path)
        assert ev.found is True, ev.gaps
        # Metacognition only appears in the executive summary
        assert any("KEYWORD DROPPING: 'Metacognition'" in g for g in ev.gaps)
    finally:
        os.unlink(path)


def test_cross_reference_verified_vs_hallucinated():
    claims = ReportClaims(source="r.md", cited_paths=["src/state.py", "judges.py", "src/tools/ast_parser.py"],
                          feature_claims={"parallel_judges": "Judges run in parallel."})
    manifest = ["src/state.py", "src/nodes/judges.py"]
    graph_ev = Evidence(goal="graph_orchestration", found=False, location="src/graph.py", rationale="linear",
                        gaps=["Judges (Prosecutor/Defense/TechLead) do not fan out in parallel"])
    ev = cross_reference_report_accuracy(claims, manifest, {"graph_orchestration": graph_ev})
    assert ev.found is False
    assert "HALLUCINATED PATH: src/tools/ast_parser.py" in ev.gaps
    assert "judges.py -> src/nodes/judges.py" in ev.content
    assert any("FEATURE CLAIM CONTRADICTED: parallel_judges" in g for g in ev.gaps)


def test_extract_claims_ignores_urls():
    path = _report("See https://github.com/x/y/blob/main/src/fake.py and src/state.py for details.")
    try:
        assert extract_report_claims(path).cited_paths == ["src/state.py"]
    finally:
        os.unlink(path)


def test_mermaid_linear_diagram_is_not_parallel():
    linear = "```mermaid\nflowchart TD\n  A[RepoInvestigator] --> B[DocAnalyst] --> C[Judge] --> D[END]\n```"
    parallel = (
        "```mermaid\nflowchart TD\n  S[START] --> R[RepoInvestigator]\n  S --> D[DocAnalyst]\n"
        "  R --> E[EvidenceAggregator]\n  D --> E\n  E --> P[Prosecutor]\n  E --> F[Defense]\n  E --> T[TechLead]\n```"
    )
    assert analyze_text_diagrams(linear)[0] is False
    assert analyze_text_diagrams(parallel)[0] is True
