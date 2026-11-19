"""
Layer 2 - The Judicial Layer (the Dialectical Bench).

Three personas judge the SAME evidence for every rubric criterion, in parallel graph branches.
Each persona's system prompt = its fixed philosophy + the criterion's ``judicial_logic`` for
that persona, loaded from the rubric (the Constitution). LLM output is forced through
``.with_structured_output(JudicialOpinion)``; free text or invalid JSON triggers a bounded retry.
"""

import os
from typing import Dict, List, Optional, Tuple

from src.state import AgentState, Evidence, JudicialOpinion

MAX_JUDGE_ATTEMPTS = 3

# --- System Prompts for Personas ---

PROSECUTOR_SYSTEM_PROMPT = """You are The Prosecutor on the Digital Courtroom Judicial Bench.
Core Philosophy: "Trust No One. Assume Vibe Coding."
Objective: Scrutinize forensic evidence for structural flaws, security vulnerabilities, laziness, and cheating.

Statutes you enforce (Protocol B):
- Orchestration Fraud: a purely linear StateGraph (no parallel fan-out) caps LangGraph architecture at 1.
- Hallucination Liability: judges returning freeform text without Pydantic validation caps Judicial Nuance at 2.
- Security Negligence: raw os.system / shell=True or cloning into the live working directory.
- Auditor Hallucination: the report cites files or features the code does not contain.

Rules of engagement:
- Every gap the detective verified is a charge. List the specific missing elements.
- Never give the benefit of the doubt; keyword mentions are not implementations.
- Be harsh, name the charge, and cite the exact evidence location.
"""

DEFENSE_SYSTEM_PROMPT = """You are The Defense Attorney on the Digital Courtroom Judicial Bench.
Core Philosophy: "Reward Effort and Intent. Look for the 'Spirit of the Law'."
Objective: Highlight creative workarounds, deep thought, engineering process, and iteration even if execution is imperfect.

Mitigations you may plead (Statute of Effort):
- If the graph trips on framework syntax but the AST tooling shows deep code comprehension, request a boost from 1 to 3.
- If synthesis is an LLM prompt but the personas are distinct and genuinely disagree, request partial credit (3 or 4).
- A git history that tells a story of struggle and iteration earns credit for Engineering Process.

Be generous, be forgiving, and argue the strongest honest case for the trainee. Do not invent strengths the evidence does not show.
"""

TECHLEAD_SYSTEM_PROMPT = """You are The Tech Lead on the Digital Courtroom Judicial Bench.
Core Philosophy: "Does it actually work? Is it maintainable?"
Objective: Evaluate architectural soundness, code cleanliness and practical viability. You are the pragmatic tie-breaker.

Precedents:
- Pydantic Rigor vs. Dict Soups: plain dicts for complex nested state is Technical Debt (score 3).
- Sandboxed Tooling: clones and parsing must use temporary directories and error handlers.
- Reducers: operator.add / operator.ior must guard every key written by parallel nodes.

Ignore the vibe and the struggle; weigh only artifacts. Give a realistic score of 1, 3 or 5 and concrete technical remediation advice.
"""

PERSONA_PROMPTS = {
    "Prosecutor": PROSECUTOR_SYSTEM_PROMPT,
    "Defense": DEFENSE_SYSTEM_PROMPT,
    "TechLead": TECHLEAD_SYSTEM_PROMPT,
}
# Key of each persona inside a rubric dimension's "judicial_logic" object
JUDICIAL_LOGIC_KEYS = {"Prosecutor": "prosecutor", "Defense": "defense", "TechLead": "tech_lead"}


def build_system_prompt(persona: str, dimension: Dict) -> str:
    """Persona philosophy + this criterion's judicial_logic from the rubric (dynamic Constitution)."""
    logic = (dimension.get("judicial_logic") or {}).get(JUDICIAL_LOGIC_KEYS[persona])
    prompt = PERSONA_PROMPTS[persona]
    if logic:
        prompt += f"\nConstitution for criterion '{dimension['id']}' ({persona} lens):\n{logic}\n"
    return prompt


def _find_evidence_by_goal(evidences_dict: Dict[str, List[Evidence]], goal_id: str) -> Optional[Evidence]:
    """Locates the evidence for a rubric criterion (exact goal match first)."""
    all_evs = [ev for ev_list in evidences_dict.values() for ev in ev_list]
    return next((e for e in all_evs if e.goal == goal_id), None) or next((e for e in all_evs if goal_id in e.goal), None)


def _format_evidence(ev: Optional[Evidence]) -> str:
    if ev is None:
        return "NO EVIDENCE was collected for this criterion."
    content = (ev.content or "")[:2500]
    gaps = "\n".join(f"  - {g}" for g in ev.gaps) or "  (none)"
    return (
        f"Goal: {ev.goal}\nFound: {ev.found}\nConfidence: {ev.confidence}\nLocation: {ev.location}\n"
        f"Rationale: {ev.rationale}\nVerified gaps:\n{gaps}\nCaptured content:\n{content}"
    )


def _judge_llm():
    """Returns an LLM bound to the JudicialOpinion schema, or None when no API key is set."""
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o", temperature=0.2)
    elif os.environ.get("GEMINI_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2)
    else:
        return None
    return llm.with_structured_output(JudicialOpinion)


# --- Deterministic persona fallback (no API key, or the LLM failed every retry) ---

def _strengths(ev: Optional[Evidence]) -> List[str]:
    if ev is None:
        return []
    return [s.strip() for s in ev.rationale.split(";") if s.strip() and not any(s.strip() in g for g in ev.gaps)]


def _charges(gaps: List[str]) -> List[str]:
    markers = {
        "SECURITY VIOLATION": "Security Negligence",
        "ORCHESTRATION FRAUD": "Orchestration Fraud",
        "HALLUCINATION LIABILITY": "Hallucination Liability",
        "HALLUCINATED PATH": "Auditor Hallucination",
        "FEATURE CLAIM CONTRADICTED": "Auditor Hallucination",
        "PERSONA COLLUSION": "Persona Collusion",
        "KEYWORD DROPPING": "Keyword Dropping",
        "MISLEADING ARCHITECTURE VISUAL": "Misleading Architecture Visual",
        "REPO UNAVAILABLE": "Failure to Produce Evidence",
    }
    return sorted({charge for g in gaps for m, charge in markers.items() if m in g})


def _evaluate_persona_heuristically(persona: str, dimension: Dict, evidence: Optional[Evidence]) -> JudicialOpinion:
    """
    Deterministic persona engine. Each lens reads the same verified facts (found, gaps,
    confidence) but weighs them differently, so the bench genuinely disagrees.
    """
    dim_id, dim_name = dimension["id"], dimension["name"]
    found = bool(evidence and evidence.found)
    gaps = evidence.gaps if evidence else ["No evidence collected for this criterion"]
    confidence = evidence.confidence if evidence else 0.0
    strengths = _strengths(evidence)
    charges = _charges(gaps)
    cited = [evidence.location] if evidence else []
    gap_text = "; ".join(gaps[:4]) or "none"

    if persona == "Prosecutor":
        if any(c in charges for c in ("Security Negligence", "Orchestration Fraud", "Auditor Hallucination", "Failure to Produce Evidence")):
            score = 1
        elif charges:
            score = 2
        elif not found:
            score = 1 if len(gaps) >= 2 or not strengths else 2
        elif not gaps:
            score = 5 if confidence >= 0.95 else 4
        else:
            score = max(2, 4 - len(gaps))
        charge = f"[{', '.join(charges)}] " if charges else ""
        arg = (
            f"PROSECUTION {charge}on '{dim_name}': "
            + (f"the detective verified these defects: {gap_text}." if gaps else
               "I searched for bypassed structure and found no verified defect; the evidence stands.")
        )
        return JudicialOpinion(judge="Prosecutor", criterion_id=dim_id, score=score, argument=arg,
                               cited_evidence=cited + gaps[:3])

    if persona == "Defense":
        if found:
            score = 5 if len(gaps) <= 1 else 4
        elif strengths:
            score = 3  # Statute of Effort: partial implementation shows intent
        else:
            score = 2
        if "Security Negligence" in charges:
            score = min(score, 3)
        arg = (
            f"DEFENSE on '{dim_name}': "
            + (f"the record shows real engineering: {'; '.join(strengths[:3])}." if strengths else
               "little was built here, but the attempt itself shows intent.")
            + (f" The remaining issues ({gaps[0]}) are refinements, not a failure of understanding." if gaps and found else "")
        )
        return JudicialOpinion(judge="Defense", criterion_id=dim_id, score=score, argument=arg, cited_evidence=cited)

    # TechLead: realistic 1 / 3 / 5
    if "Security Negligence" in charges or (not found and (not strengths or len(gaps) > 2)):
        score = 1
    elif found and not gaps:
        score = 5
    else:
        score = 3
    remediation = dimension.get("remediation", "Address the verified gaps.")
    arg = (
        f"TECH LEAD on '{dim_name}': "
        + ("artifacts are sound and maintainable as built." if score == 5 else
           f"technical debt: {gap_text}. Remediation: {remediation}")
    )
    return JudicialOpinion(judge="TechLead", criterion_id=dim_id, score=score, argument=arg, cited_evidence=cited)


def _query_judge_llm(persona: str, dimension: Dict, evidence: Optional[Evidence], llm=None) -> JudicialOpinion:
    """
    Queries the persona's LLM with structured output. If the model returns free text, invalid
    JSON or a schema violation, the error is fed back and the call is retried (up to
    MAX_JUDGE_ATTEMPTS). Only after every attempt fails does the deterministic persona decide.
    """
    if llm is None:
        return _evaluate_persona_heuristically(persona, dimension, evidence)

    user_prompt = (
        f"Evaluate Rubric Dimension: {dimension['name']} (ID: {dimension['id']})\n"
        f"Forensic Instruction: {dimension.get('forensic_instruction', '')}\n"
        f"Success Pattern: {dimension.get('success_pattern', '')}\n"
        f"Failure Pattern: {dimension.get('failure_pattern', '')}\n\n"
        f"Collected Forensic Evidence (facts from the Detectives):\n{_format_evidence(evidence)}\n\n"
        f"Return a JudicialOpinion with judge='{persona}', criterion_id='{dimension['id']}', an integer score 1-5, "
        f"your argument, and cited_evidence (evidence locations or quoted facts)."
    )
    messages: List[Tuple[str, str]] = [("system", build_system_prompt(persona, dimension)), ("user", user_prompt)]
    last_error = None
    for attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
        try:
            result = llm.invoke(messages)
            opinion = result if isinstance(result, JudicialOpinion) else JudicialOpinion.model_validate(result)
            if opinion.judge != persona or opinion.criterion_id != dimension["id"]:
                opinion = opinion.model_copy(update={"judge": persona, "criterion_id": dimension["id"]})
            return opinion
        except Exception as e:  # noqa: BLE001 - parser / validation / transport errors all trigger a retry
            last_error = e
            print(f"[Judge Retry] {persona} on {dimension['id']} attempt {attempt}/{MAX_JUDGE_ATTEMPTS} rejected: {type(e).__name__}")
            messages = messages + [(
                "user",
                f"Your previous reply was rejected by the parser ({type(e).__name__}: {str(e)[:300]}). "
                f"Reply ONLY with a JudicialOpinion object. No free text.",
            )]

    fallback = _evaluate_persona_heuristically(persona, dimension, evidence)
    return fallback.model_copy(update={
        "argument": f"[LLM produced no valid JudicialOpinion after {MAX_JUDGE_ATTEMPTS} attempts "
                    f"({type(last_error).__name__}); deterministic {persona} lens applied] {fallback.argument}"
    })


def _deliberate(persona: str, state: AgentState) -> Dict:
    evidences = state.get("evidences", {})
    dimensions = state.get("rubric_dimensions", [])
    try:
        llm = _judge_llm()
    except Exception as e:  # noqa: BLE001 - misconfigured provider must not kill the bench
        print(f"[Judicial Bench] {persona} could not initialise LLM ({e}); using deterministic lens")
        llm = None
    mode = "LLM (structured output)" if llm else "deterministic lens (no API key)"
    print(f"[Judicial Bench] {persona} deliberating on {len(dimensions)} criteria via {mode}...")
    opinions = [
        _query_judge_llm(persona, dim, _find_evidence_by_goal(evidences, dim["id"]), llm)
        for dim in dimensions
    ]
    return {"opinions": opinions}


def prosecutor_node(state: AgentState) -> Dict:
    """Prosecutor: critical, adversarial lens over every rubric criterion."""
    return _deliberate("Prosecutor", state)


def defense_node(state: AgentState) -> Dict:
    """Defense Attorney: optimistic, effort-and-intent lens over every rubric criterion."""
    return _deliberate("Defense", state)


def tech_lead_node(state: AgentState) -> Dict:
    """Tech Lead: pragmatic tie-breaker lens over every rubric criterion."""
    return _deliberate("TechLead", state)
