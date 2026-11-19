from typing import List, Union

from langgraph.graph import END, START, StateGraph

from src.nodes.detectives import (
    REPO_UNAVAILABLE,
    context_builder_node,
    doc_analyst_node,
    evidence_aggregator_node,
    evidence_missing_node,
    repo_investigator_node,
    vision_inspector_node,
)
from src.nodes.judges import defense_node, prosecutor_node, tech_lead_node
from src.nodes.justice import chief_justice_node
from src.rubric import load_default_rubric  # noqa: F401  (re-exported for callers/tests)
from src.state import AgentState


def route_after_aggregation(state: AgentState) -> Union[List[str], str]:
    """
    Conditional edge after the evidence fan-in.

    - Evidence collected: returns all three Judges as a list, which LangGraph dispatches as
      parallel branches in the same superstep (fan-out), each judging the SAME evidence.
    - Repository unavailable (clone/auth failure) or no evidence at all: routes to the
      EvidenceMissing handler instead of asking three Judges to argue over nothing.
    """
    evidences = state.get("evidences", {})
    if not any(evidences.values()):
        return "EvidenceMissing"
    repo_dims = [d for d in state.get("rubric_dimensions", []) if d.get("target_artifact") == "github_repo"]
    repo_evs = evidences.get("github_repo", [])
    if repo_dims and (not repo_evs or all(any(REPO_UNAVAILABLE in g for g in e.gaps) for e in repo_evs)):
        return "EvidenceMissing"
    return ["Prosecutor", "Defense", "TechLead"]


def build_auditor_graph():
    """
    Hierarchical StateGraph with two parallel fan-out / fan-in stages:

    START -> ContextBuilder -> [RepoInvestigator || DocAnalyst || VisionInspector]
          -> EvidenceAggregator -(conditional)-> [Prosecutor || Defense || TechLead] -> ChiefJustice -> END
                                 \\-> EvidenceMissing -> ChiefJustice
    """
    builder = StateGraph(AgentState)

    # Layer 0: the Constitution
    builder.add_node("ContextBuilder", context_builder_node)

    # Layer 1: Detectives + fan-in
    builder.add_node("RepoInvestigator", repo_investigator_node)
    builder.add_node("DocAnalyst", doc_analyst_node)
    builder.add_node("VisionInspector", vision_inspector_node)
    builder.add_node("EvidenceAggregator", evidence_aggregator_node)
    builder.add_node("EvidenceMissing", evidence_missing_node)

    # Layer 2: the Dialectical Bench
    builder.add_node("Prosecutor", prosecutor_node)
    builder.add_node("Defense", defense_node)
    builder.add_node("TechLead", tech_lead_node)

    # Layer 3: the Supreme Court
    builder.add_node("ChiefJustice", chief_justice_node)

    builder.add_edge(START, "ContextBuilder")

    # Detective fan-out: three unconditional edges from one node run in the same superstep
    builder.add_edge("ContextBuilder", "RepoInvestigator")
    builder.add_edge("ContextBuilder", "DocAnalyst")
    builder.add_edge("ContextBuilder", "VisionInspector")

    # Detective fan-in: a join edge waits for ALL detectives before the aggregator runs
    builder.add_edge(["RepoInvestigator", "DocAnalyst", "VisionInspector"], "EvidenceAggregator")

    # Judge fan-out (or failure route), decided by the evidence health check
    builder.add_conditional_edges(
        "EvidenceAggregator",
        route_after_aggregation,
        ["Prosecutor", "Defense", "TechLead", "EvidenceMissing"],
    )

    # Judge fan-in: ChiefJustice waits for all three opinions lists
    builder.add_edge(["Prosecutor", "Defense", "TechLead"], "ChiefJustice")
    builder.add_edge("EvidenceMissing", "ChiefJustice")

    builder.add_edge("ChiefJustice", END)
    return builder.compile()


# Export compiled graph instance
auditor_graph = build_auditor_graph()
