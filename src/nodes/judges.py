import json
import os
from typing import Dict, List, Optional
from langchain_core.prompts import ChatPromptTemplate
from src.state import AgentState, Evidence, JudicialOpinion

# --- System Prompts for Personas ---

PROSECUTOR_SYSTEM_PROMPT = """You are The Prosecutor on the Digital Courtroom Judicial Bench.
Core Philosophy: "Trust No One. Assume Vibe Coding."
Objective: Scrutinize forensic evidence for structural flaws, security vulnerabilities, laziness, and cheating.

Guidelines:
- If a Graph Orchestration requirement asks for parallel fan-out/fan-in and evidence shows linear execution, penalize with Score 1 (Orchestration Fraud).
- If state management lacks Pydantic models or reducers, charge with Technical Debt and assign Score 1-2.
- If Judicial output lacks structured JSON enforcement, charge with Hallucination Liability.
- If raw `os.system` calls exist, charge with Security Negligence.
- Be harsh, strict, and cite specific evidence. Do NOT give benefit of the doubt.
"""

DEFENSE_SYSTEM_PROMPT = """You are The Defense Attorney on the Digital Courtroom Judicial Bench.
Core Philosophy: "Reward Effort and Intent. Look for the 'Spirit of the Law'."
Objective: Highlight creative workarounds, deep thought, engineering process, and iteration even if execution is imperfect.

Guidelines:
- Look closely at Git history evidence. If commits tell a story of struggle and iteration, argue for higher score on process.
- If code has syntax or framework syntax errors but underlying AST parsing or domain concepts show deep comprehension, argue for partial credit (Score 3-4).
- Defend the student's architectural choices and highlight creative problem-solving.
"""

TECHLEAD_SYSTEM_PROMPT = """You are The Tech Lead on the Digital Courtroom Judicial Bench.
Core Philosophy: "Does it actually work? Is it maintainable?"
Objective: Evaluate architectural soundness, maintainability, code cleanliness, and practical viability. You are the pragmatic tie-breaker.

Guidelines:
- Ignore the "Vibe" and the "Struggle". Focus strictly on code artifacts and operational reality.
- "Pydantic Rigor vs Dict Soups": If dicts are used without Pydantic schemas, score 3 (brittle/unmaintainable). If full Pydantic with reducers, score 5.
- "Sandboxed Tooling": If tempfile sandboxing is present, reward 5. If security flaw exists, score 1.
- Provide a realistic score (1, 3, or 5) with concrete technical debt & remediation advice.
"""


def _find_evidence_by_goal(evidences_dict: Dict[str, List[Evidence]], goal_id: str) -> Optional[Evidence]:
    """Helper to locate a specific evidence item across detective outputs."""
    for category, ev_list in evidences_dict.items():
        for ev in ev_list:
            if ev.goal == goal_id or goal_id in ev.goal:
                return ev
    return None


def _evaluate_persona_heuristically(
    persona: str, dimension: Dict, evidence: Optional[Evidence]
) -> JudicialOpinion:
    """
    Fallback deterministic evaluation engine for judge personas when LLM API is unavailable.
    Guarantees robust, structured output following each persona's distinct lens.
    """
    dim_id = dimension["id"]
    dim_name = dimension["name"]

    found = evidence.found if evidence else False
    rationale = evidence.rationale if evidence else "No evidence collected for this criterion."
    location = evidence.location if evidence else "N/A"

    if persona == "Prosecutor":
        if not found:
            score = 1
            arg = f"PROSECUTION: Critical failure in '{dim_name}'. Evidence shows: {rationale}. Violates standard specification!"
        else:
            score = 4 if "minor" in rationale.lower() else 5
            arg = f"PROSECUTION: Requirement '{dim_name}' met, but under strict scrutiny: {rationale}."
        return JudicialOpinion(
            judge="Prosecutor",
            criterion_id=dim_id,
            score=score,
            argument=arg,
            cited_evidence=[location],
        )

    elif persona == "Defense":
        if found:
            score = 5
            arg = f"DEFENSE: Exemplary effort on '{dim_name}'! Evidence demonstrates solid execution: {rationale}."
        else:
            score = 3  # Forgiving, rewards attempt
            arg = f"DEFENSE: While '{dim_name}' has flaws ({rationale}), the code structure demonstrates clear intent and effort."
        return JudicialOpinion(
            judge="Defense",
            criterion_id=dim_id,
            score=score,
            argument=arg,
            cited_evidence=[location],
        )

    else:  # TechLead
        if found:
            score = 5 if ("pydantic" in rationale.lower() or "sandboxed" in rationale.lower() or "parallel" in rationale.lower()) else 4
            arg = f"TECH LEAD: '{dim_name}' is architecturally sound and maintainable: {rationale}."
        else:
            score = 1 if ("security" in rationale.lower() or "os.system" in rationale.lower()) else 2
            arg = f"TECH LEAD: '{dim_name}' introduces technical debt or failure: {rationale}."
        return JudicialOpinion(
            judge="TechLead",
            criterion_id=dim_id,
            score=score,
            argument=arg,
            cited_evidence=[location],
        )


def _query_judge_llm(
    persona: str, system_prompt: str, dimension: Dict, evidence: Optional[Evidence]
) -> JudicialOpinion:
    """
    Queries LLM with structured output enforcement for a judge persona.
    Falls back to heuristic evaluation if LLM fails or API key is not configured.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if not api_key:
        return _evaluate_persona_heuristically(persona, dimension, evidence)

    try:
        # If OpenAI key present
        if os.environ.get("OPENAI_API_KEY"):
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model="gpt-4o", temperature=0.2)
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)

        structured_llm = llm.with_structured_output(JudicialOpinion)

        ev_text = f"Goal: {evidence.goal}\nFound: {evidence.found}\nRationale: {evidence.rationale}\nLocation: {evidence.location}\nContent: {evidence.content}" if evidence else "No evidence collected."

        user_prompt = f"""Evaluate Rubric Dimension: {dimension['name']} (ID: {dimension['id']})
Rubric Instruction: {dimension.get('forensic_instruction', '')}
Success Pattern: {dimension.get('success_pattern', '')}
Failure Pattern: {dimension.get('failure_pattern', '')}

Collected Forensic Evidence:
{ev_text}

Render your JudicialOpinion (judge name, criterion_id, score 1-5, argument, cited_evidence).
"""
        messages = [
            ("system", system_prompt),
            ("user", user_prompt),
        ]
        result = structured_llm.invoke(messages)
        if isinstance(result, JudicialOpinion):
            return result
        elif isinstance(result, dict):
            return JudicialOpinion(**result)
        else:
            return _evaluate_persona_heuristically(persona, dimension, evidence)
    except Exception as e:
        print(f"[Judge LLM Warning] LLM call for {persona} on {dimension['id']} failed ({str(e)}). Falling back to heuristic judge.")
        return _evaluate_persona_heuristically(persona, dimension, evidence)


def prosecutor_node(state: AgentState) -> Dict:
    """
    Prosecutor Node: Evaluates all 10 rubric dimensions from the critical adversarial lens.
    Output is added to state['opinions'].
    """
    print("[Judicial Bench] Prosecutor deliberating on evidence...")
    evidences = state.get("evidences", {})
    dimensions = state.get("rubric_dimensions", [])

    opinions: List[JudicialOpinion] = []
    for dim in dimensions:
        ev = _find_evidence_by_goal(evidences, dim["id"])
        op = _query_judge_llm("Prosecutor", PROSECUTOR_SYSTEM_PROMPT, dim, ev)
        opinions.append(op)

    return {"opinions": opinions}


def defense_node(state: AgentState) -> Dict:
    """
    Defense Attorney Node: Evaluates all 10 rubric dimensions from the optimistic effort lens.
    Output is added to state['opinions'].
    """
    print("[Judicial Bench] Defense Attorney deliberating on evidence...")
    evidences = state.get("evidences", {})
    dimensions = state.get("rubric_dimensions", [])

    opinions: List[JudicialOpinion] = []
    for dim in dimensions:
        ev = _find_evidence_by_goal(evidences, dim["id"])
        op = _query_judge_llm("Defense", DEFENSE_SYSTEM_PROMPT, dim, ev)
        opinions.append(op)

    return {"opinions": opinions}


def tech_lead_node(state: AgentState) -> Dict:
    """
    Tech Lead Node: Evaluates all 10 rubric dimensions from the pragmatic technical viability lens.
    Output is added to state['opinions'].
    """
    print("[Judicial Bench] Tech Lead deliberating on evidence...")
    evidences = state.get("evidences", {})
    dimensions = state.get("rubric_dimensions", [])

    opinions: List[JudicialOpinion] = []
    for dim in dimensions:
        ev = _find_evidence_by_goal(evidences, dim["id"])
        op = _query_judge_llm("TechLead", TECHLEAD_SYSTEM_PROMPT, dim, ev)
        opinions.append(op)

    return {"opinions": opinions}
