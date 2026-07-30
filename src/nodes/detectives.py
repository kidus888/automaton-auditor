import os
from typing import Dict, List
from src.state import AgentState, Evidence
from src.tools.repo_tools import (
    clone_repo_sandboxed,
    get_git_history,
    analyze_state_management,
    analyze_graph_orchestration,
    analyze_safe_tool_engineering,
    analyze_structured_output_and_judges,
    analyze_chief_justice_node,
)
from src.tools.doc_tools import (
    search_theoretical_depth,
    cross_reference_report_accuracy,
    extract_and_analyze_diagrams,
)


def repo_investigator_node(state: AgentState) -> Dict:
    """
    RepoInvestigator (The Code Detective):
    Performs forensic analysis on the target repository code, git log, AST structures, and safety.
    Output is populated into state['evidences']['github_repo'].
    """
    repo_url = state.get("repo_url", "")
    print(f"[Detective] RepoInvestigator running forensic audit on: {repo_url}")

    # Clone sandboxed if URL, or use local path
    repo_path, temp_obj, error = clone_repo_sandboxed(repo_url)

    evidences: List[Evidence] = []
    if error or not repo_path:
        evidences.append(
            Evidence(
                goal="git_forensic_analysis",
                found=False,
                location=repo_url,
                rationale=f"Failed to clone or access repository: {error}",
                confidence=1.0,
            )
        )
    else:
        try:
            evidences.append(get_git_history(repo_path))
            evidences.append(analyze_state_management(repo_path))
            evidences.append(analyze_graph_orchestration(repo_path))
            evidences.append(analyze_safe_tool_engineering(repo_path))
            evidences.extend(analyze_structured_output_and_judges(repo_path))
            evidences.append(analyze_chief_justice_node(repo_path))
        finally:
            if temp_obj is not None:
                temp_obj.cleanup()

    return {"evidences": {"github_repo": evidences}}


def doc_analyst_node(state: AgentState) -> Dict:
    """
    DocAnalyst (The Paperwork Detective):
    Performs forensic analysis on the PDF report for theoretical depth, concept explanations,
    and cross-reference file path accuracy.
    Output is populated into state['evidences']['pdf_report'].
    """
    pdf_path = state.get("pdf_path", "")
    repo_url = state.get("repo_url", "")
    print(f"[Detective] DocAnalyst evaluating PDF report: {pdf_path}")

    evidences: List[Evidence] = []
    if not pdf_path or not os.path.exists(pdf_path):
        evidences.append(
            Evidence(
                goal="theoretical_depth",
                found=False,
                location=pdf_path or "missing",
                rationale="PDF report file does not exist.",
                confidence=1.0,
            )
        )
        evidences.append(
            Evidence(
                goal="report_accuracy",
                found=False,
                location=pdf_path or "missing",
                rationale="PDF report file does not exist for cross-referencing.",
                confidence=1.0,
            )
        )
    else:
        evidences.append(search_theoretical_depth(pdf_path))
        evidences.append(cross_reference_report_accuracy(pdf_path, repo_url))

    return {"evidences": {"pdf_report": evidences}}


def vision_inspector_node(state: AgentState) -> Dict:
    """
    VisionInspector (The Diagram Detective):
    Analyzes architectural diagram visuals from the PDF report to verify parallel fan-out/fan-in flow.
    Output is populated into state['evidences']['pdf_images'].
    """
    pdf_path = state.get("pdf_path", "")
    print(f"[Detective] VisionInspector analyzing architectural diagrams in: {pdf_path}")

    evidences: List[Evidence] = []
    if not pdf_path or not os.path.exists(pdf_path):
        evidences.append(
            Evidence(
                goal="swarm_visual",
                found=False,
                location=pdf_path or "missing",
                rationale="PDF report file missing for visual analysis.",
                confidence=1.0,
            )
        )
    else:
        evidences.append(extract_and_analyze_diagrams(pdf_path))

    return {"evidences": {"pdf_images": evidences}}


def evidence_aggregator_node(state: AgentState) -> Dict:
    """
    EvidenceAggregator (Fan-In Synchronization Node):
    Collects and synchronizes all evidence from RepoInvestigator, DocAnalyst, and VisionInspector
    before triggering parallel Judicial deliberation.
    """
    all_evidences = state.get("evidences", {})
    total_count = sum(len(ev_list) for ev_list in all_evidences.values())
    print(f"[Fan-In Sync] EvidenceAggregator synchronized {total_count} total evidence items across all detectives.")
    return {}
