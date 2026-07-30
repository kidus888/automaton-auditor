import json
import os
from typing import Dict, List
from langgraph.graph import END, START, StateGraph
from src.state import AgentState
from src.nodes.detectives import (
    repo_investigator_node,
    doc_analyst_node,
    vision_inspector_node,
    evidence_aggregator_node,
)
from src.nodes.judges import (
    prosecutor_node,
    defense_node,
    tech_lead_node,
)
from src.nodes.justice import chief_justice_node


def load_default_rubric() -> List[Dict]:
    """Loads the 10 rubric dimensions from rubric.json."""
    rubric_path = os.path.join(os.path.dirname(__file__), "..", "rubric.json")
    if os.path.exists(rubric_path):
        with open(rubric_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("dimensions", [])
    return []


def check_evidence_health(state: AgentState) -> str:
    """
    Conditional edge function checking if detective evidence collection was successful.
    If evidence dict is populated, routes to judicial bench. If missing, routes to error handler.
    """
    evidences = state.get("evidences", {})
    if evidences:
        return "proceed_to_judges"
    return "evidence_missing"


def build_auditor_graph() -> StateGraph:
    """
    Constructs the Hierarchical LangGraph StateGraph with double parallel fan-out/fan-in architecture:
    START -> [RepoInvestigator, DocAnalyst, VisionInspector] -> EvidenceAggregator -> [Prosecutor, Defense, TechLead] -> ChiefJustice -> END
    """
    builder = StateGraph(AgentState)

    # Add Detective Nodes (Layer 1)
    builder.add_node("RepoInvestigator", repo_investigator_node)
    builder.add_node("DocAnalyst", doc_analyst_node)
    builder.add_node("VisionInspector", vision_inspector_node)
    builder.add_node("EvidenceAggregator", evidence_aggregator_node)

    # Add Judicial Nodes (Layer 2)
    builder.add_node("Prosecutor", prosecutor_node)
    builder.add_node("Defense", defense_node)
    builder.add_node("TechLead", tech_lead_node)

    # Add Supreme Court Synthesis Node (Layer 3)
    builder.add_node("ChiefJustice", chief_justice_node)

    # --- Wire Graph Edges ---

    # 1. Detective Parallel Fan-Out from START
    builder.add_edge(START, "RepoInvestigator")
    builder.add_edge(START, "DocAnalyst")
    builder.add_edge(START, "VisionInspector")

    # 2. Detective Fan-In to EvidenceAggregator
    builder.add_edge("RepoInvestigator", "EvidenceAggregator")
    builder.add_edge("DocAnalyst", "EvidenceAggregator")
    builder.add_edge("VisionInspector", "EvidenceAggregator")

    # 3. Conditional Edge check after EvidenceAggregator
    builder.add_conditional_edges(
        "EvidenceAggregator",
        check_evidence_health,
        {
            "proceed_to_judges": "Prosecutor",
            "evidence_missing": "ChiefJustice",
        },
    )

    # 4. Judicial Parallel Fan-Out from EvidenceAggregator to Defense & TechLead as well
    builder.add_edge("EvidenceAggregator", "Defense")
    builder.add_edge("EvidenceAggregator", "TechLead")

    # 5. Judicial Fan-In to ChiefJustice
    builder.add_edge("Prosecutor", "ChiefJustice")
    builder.add_edge("Defense", "ChiefJustice")
    builder.add_edge("TechLead", "ChiefJustice")

    # 6. Supreme Court to END
    builder.add_edge("ChiefJustice", END)

    return builder.compile()


# Export compiled graph instance
auditor_graph = build_auditor_graph()
