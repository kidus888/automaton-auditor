from src.nodes.judges import (
    MAX_JUDGE_ATTEMPTS,
    _evaluate_persona_heuristically,
    _query_judge_llm,
    build_system_prompt,
)
from src.state import Evidence, JudicialOpinion

DIM = {
    "id": "graph_orchestration",
    "name": "Graph Orchestration",
    "judicial_logic": {"prosecutor": "Charge Orchestration Fraud if linear.", "defense": "Be kind.", "tech_lead": "Check fan-in."},
}
EV = Evidence(goal="graph_orchestration", found=True, location="src/graph.py", rationale="fan-out present",
              gaps=["No conditional edges"])


class FakeLLM:
    """Returns the queued responses in order; Exceptions are raised like a parser error."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_system_prompt_injects_rubric_judicial_logic():
    assert "Charge Orchestration Fraud if linear." in build_system_prompt("Prosecutor", DIM)
    assert "Check fan-in." in build_system_prompt("TechLead", DIM)


def test_free_text_triggers_retry_then_succeeds():
    good = JudicialOpinion(judge="Prosecutor", criterion_id="graph_orchestration", score=2, argument="gaps", cited_evidence=[])
    llm = FakeLLM([ValueError("free text"), None, good])
    op = _query_judge_llm("Prosecutor", DIM, EV, llm)
    assert op == good
    assert len(llm.calls) == 3
    assert "rejected by the parser" in llm.calls[-1][-1][1]


def test_wrong_identity_is_corrected():
    wrong = JudicialOpinion(judge="Defense", criterion_id="other", score=4, argument="x", cited_evidence=[])
    op = _query_judge_llm("TechLead", DIM, EV, FakeLLM([wrong]))
    assert (op.judge, op.criterion_id) == ("TechLead", "graph_orchestration")


def test_exhausted_retries_fall_back_to_persona_lens():
    llm = FakeLLM([ValueError("bad")] * MAX_JUDGE_ATTEMPTS)
    op = _query_judge_llm("Defense", DIM, EV, llm)
    assert op.judge == "Defense"
    assert op.argument.startswith("[LLM produced no valid JudicialOpinion")


def test_personas_disagree_on_partial_evidence():
    scores = {p: _evaluate_persona_heuristically(p, DIM, EV).score for p in ("Prosecutor", "Defense", "TechLead")}
    assert scores["Defense"] > scores["TechLead"] >= scores["Prosecutor"]


def test_prosecutor_charges_security_negligence():
    ev = Evidence(goal="safe_tool_engineering", found=False, location="src/tools/x.py", rationale="bad",
                  gaps=["SECURITY VIOLATION: os.system"])
    op = _evaluate_persona_heuristically("Prosecutor", {"id": "safe_tool_engineering", "name": "Safe"}, ev)
    assert op.score == 1 and "Security Negligence" in op.argument
