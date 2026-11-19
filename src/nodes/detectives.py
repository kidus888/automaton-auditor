"""
Layer 1 - The Detective Layer.

ContextBuilder dispatches each rubric dimension to the detective whose capability matches its
``target_artifact``; detectives run in parallel and only collect facts (Evidence objects).
EvidenceAggregator is the fan-in synchronization node that cross-references the DocAnalyst's
claims against the RepoInvestigator's data before the Judges are woken.
"""

import os
from typing import Callable, Dict, List, Optional

from src.rubric import KNOWN_ARTIFACTS, load_rubric
from src.state import AgentState, Evidence
from src.tools.doc_tools import (
    cross_reference_report_accuracy,
    extract_and_analyze_diagrams,
    extract_report_claims,
    search_theoretical_depth,
)
from src.tools.repo_tools import (
    analyze_chief_justice_node,
    analyze_graph_structure,
    analyze_judicial_nuance,
    analyze_safe_tool_engineering,
    analyze_state_management,
    analyze_structured_output,
    build_repo_manifest,
    clone_repo_sandboxed,
    extract_git_history,
)

# Forensic protocols each detective can execute, keyed by rubric dimension id.
REPO_PROTOCOLS: Dict[str, Callable[[str], Evidence]] = {
    "git_forensic_analysis": extract_git_history,
    "state_management_rigor": analyze_state_management,
    "graph_orchestration": analyze_graph_structure,
    "langgraph_architecture": analyze_graph_structure,  # id used by the spec's sample rubric
    "safe_tool_engineering": analyze_safe_tool_engineering,
    "structured_output_enforcement": analyze_structured_output,
    "judicial_nuance": analyze_judicial_nuance,
    "chief_justice_synthesis": analyze_chief_justice_node,
}
DOC_PROTOCOLS: Dict[str, Callable[[str], Evidence]] = {
    "theoretical_depth": search_theoretical_depth,
}
# Dimensions whose evidence is only produced at fan-in, once both detectives have reported.
FAN_IN_DIMENSIONS = {"report_accuracy"}
VISION_PROTOCOLS: Dict[str, Callable[[str], Evidence]] = {
    "swarm_visual": extract_and_analyze_diagrams,
}
REPO_UNAVAILABLE = "REPO UNAVAILABLE"


def _dims_for(state: AgentState, artifact: str) -> List[Dict]:
    return [d for d in state.get("rubric_dimensions", []) if d.get("target_artifact") == artifact]


def _run_protocol(dim: Dict, protocol: Optional[Callable[[str], Evidence]], target: str, errors: List[str]) -> Evidence:
    """Runs one forensic protocol; any exception becomes a failed Evidence (node-failure
    isolation) so one broken check cannot take down the whole swarm."""
    goal = dim["id"]
    if protocol is None:
        return Evidence(goal=goal, found=False, location="n/a", confidence=0.0,
                        rationale=f"No forensic protocol is registered for rubric dimension '{goal}'.",
                        gaps=[f"Auditor has no protocol for '{goal}' (update src/nodes/detectives.py)"])
    try:
        ev = protocol(target)
        return ev if ev.goal == goal else ev.model_copy(update={"goal": goal})
    except Exception as e:  # noqa: BLE001 - deliberate isolation boundary
        errors.append(f"NODE FAILURE in {protocol.__name__} for '{goal}': {type(e).__name__}: {e}")
        return Evidence(goal=goal, found=False, location=target, confidence=0.0,
                        rationale=f"NODE FAILURE: {protocol.__name__} raised {type(e).__name__}: {e}",
                        gaps=[f"Forensic protocol crashed ({type(e).__name__}); evidence could not be collected"])


def context_builder_node(state: AgentState) -> Dict:
    """
    ContextBuilder: loads the Constitution (rubric/week2_rubric.json) when the caller did not
    supply one, validates every dimension's target_artifact, and logs the dispatch plan
    (forensic_instruction -> detective, judicial_logic -> judges, synthesis_rules -> Chief Justice).
    """
    errors: List[str] = []
    dims = state.get("rubric_dimensions") or []
    rules = state.get("synthesis_rules") or {}
    if not dims or not rules:
        rubric = load_rubric()
        dims = dims or rubric.get("dimensions", [])
        rules = rules or rubric.get("synthesis_rules", {})

    for d in dims:
        if d.get("target_artifact") not in KNOWN_ARTIFACTS:
            errors.append(f"ContextBuilder: dimension '{d.get('id')}' has unknown target_artifact '{d.get('target_artifact')}'")
    plan = {a: [d["id"] for d in dims if d.get("target_artifact") == a] for a in sorted(KNOWN_ARTIFACTS)}
    print(f"[ContextBuilder] Dispatching {len(dims)} dimensions: {plan}; {len(rules)} synthesis rules to ChiefJustice")
    return {"rubric_dimensions": dims, "synthesis_rules": rules, "errors": errors}


def repo_investigator_node(state: AgentState) -> Dict:
    """
    RepoInvestigator (The Code Detective): clones the target into a tempfile sandbox and runs
    the AST / git protocols for every 'github_repo' dimension. Also publishes the repository
    file manifest so the DocAnalyst's citations can be verified at fan-in.
    """
    repo_url = state.get("repo_url", "")
    dims = _dims_for(state, "github_repo")
    print(f"[Detective] RepoInvestigator running {len(dims)} protocols on: {repo_url}")
    errors: List[str] = []

    repo_path, temp_obj, error = clone_repo_sandboxed(repo_url)
    if error or not repo_path:
        errors.append(f"RepoInvestigator: {error}")
        evidences = [
            Evidence(goal=d["id"], found=False, location=repo_url, confidence=1.0,
                     rationale=f"Repository could not be cloned or accessed: {error}",
                     gaps=[f"{REPO_UNAVAILABLE}: {error}"])
            for d in dims
        ]
        return {"evidences": {"github_repo": evidences}, "repo_manifest": [], "errors": errors}

    try:
        manifest = build_repo_manifest(repo_path)
        evidences = [_run_protocol(d, REPO_PROTOCOLS.get(d["id"]), repo_path, errors) for d in dims]
    finally:
        if temp_obj is not None:
            temp_obj.cleanup()

    return {"evidences": {"github_repo": evidences}, "repo_manifest": manifest, "errors": errors}


def doc_analyst_node(state: AgentState) -> Dict:
    """
    DocAnalyst (The Paperwork Detective): RAG-lite analysis of the report (PDF or Markdown) for
    theoretical depth, plus extraction of cited paths / feature claims for cross-referencing.
    """
    pdf_path = state.get("pdf_path", "")
    dims = _dims_for(state, "pdf_report")
    print(f"[Detective] DocAnalyst evaluating report: {pdf_path}")
    errors: List[str] = []

    if not pdf_path or not os.path.isfile(pdf_path):
        errors.append(f"DocAnalyst: report '{pdf_path}' does not exist")
        evidences = [
            Evidence(goal=d["id"], found=False, location=pdf_path or "missing", confidence=1.0,
                     rationale="Report file does not exist.", gaps=["No report was submitted (report missing)"])
            for d in dims if d["id"] not in FAN_IN_DIMENSIONS
        ]
        return {"evidences": {"pdf_report": evidences}, "report_claims": None, "errors": errors}

    evidences = [
        _run_protocol(d, DOC_PROTOCOLS.get(d["id"]), pdf_path, errors)
        for d in dims if d["id"] not in FAN_IN_DIMENSIONS
    ]
    claims = None
    if any(d["id"] in FAN_IN_DIMENSIONS for d in dims):
        try:
            claims = extract_report_claims(pdf_path)
        except Exception as e:  # noqa: BLE001
            errors.append(f"NODE FAILURE in extract_report_claims: {type(e).__name__}: {e}")
    return {"evidences": {"pdf_report": evidences}, "report_claims": claims, "errors": errors}


def vision_inspector_node(state: AgentState) -> Dict:
    """
    VisionInspector (The Diagram Detective): extracts diagrams from the report and classifies
    whether they depict the parallel Detective / Judge flow.
    """
    pdf_path = state.get("pdf_path", "")
    dims = _dims_for(state, "pdf_images")
    print(f"[Detective] VisionInspector analyzing architectural diagrams in: {pdf_path}")
    errors: List[str] = []
    if not pdf_path or not os.path.isfile(pdf_path):
        evidences = [
            Evidence(goal=d["id"], found=False, location=pdf_path or "missing", confidence=1.0,
                     rationale="Report file missing for visual analysis.", gaps=["No report to extract diagrams from"])
            for d in dims
        ]
    else:
        evidences = [_run_protocol(d, VISION_PROTOCOLS.get(d["id"]), pdf_path, errors) for d in dims]
    return {"evidences": {"pdf_images": evidences}, "errors": errors}


def evidence_aggregator_node(state: AgentState) -> Dict:
    """
    EvidenceAggregator (Fan-In Synchronization): runs only after all three detectives finish.
    Cross-references the report's claims against the RepoInvestigator's manifest and evidence
    (Host Analysis Accuracy) and checks that every rubric dimension has evidence.
    """
    evidences = state.get("evidences", {})
    dims = state.get("rubric_dimensions", [])
    errors: List[str] = []
    update: Dict = {}

    if any(d["id"] in FAN_IN_DIMENSIONS for d in dims):
        repo_ev = {e.goal: e for e in evidences.get("github_repo", [])}
        xref = cross_reference_report_accuracy(state.get("report_claims"), state.get("repo_manifest", []), repo_ev)
        update["evidences"] = {"cross_reference": [xref]}
        evidences = {**evidences, "cross_reference": [xref]}

    covered = {e.goal for lst in evidences.values() for e in lst}
    missing = [d["id"] for d in dims if d["id"] not in covered]
    if missing:
        errors.append(f"EvidenceAggregator: no evidence collected for {missing}")

    total = sum(len(v) for v in evidences.values())
    print(f"[Fan-In Sync] EvidenceAggregator synchronized {total} evidence items from {sorted(evidences)}")
    update["errors"] = errors
    return update


def evidence_missing_node(state: AgentState) -> Dict:
    """
    Failure handler reached via the conditional edge when the target repository could not be
    collected. The judicial bench is not convened (there is nothing to judge), and the Chief
    Justice records the failure instead of fabricating opinions.
    """
    reason = "; ".join(e for e in state.get("errors", []) if e.startswith("RepoInvestigator")) or "no evidence collected"
    print(f"[EvidenceMissing] Bench not convened: {reason}")
    return {"errors": [f"EvidenceMissing: judicial bench skipped ({reason})"]}
