"""
Layer 3 - The Supreme Court (Synthesis Engine).

The Chief Justice does not average opinions and does not ask an LLM. It resolves the dialectical
conflict with hardcoded, named rules that mirror the rubric's synthesis_rules, records which
side was overruled and why, and renders the Markdown audit report.
"""

import math
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from src.state import AgentState, AuditReport, CriterionResult, Evidence, JudicialOpinion

BASE_WEIGHTS = {"TechLead": 0.4, "Prosecutor": 0.3, "Defense": 0.3}
ARCHITECTURE_DIMENSIONS = {"graph_orchestration", "langgraph_architecture"}
VARIANCE_THRESHOLD = 2
SECURITY_CAP = 3


def _half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _first_sentence(text: str, limit: int = 220) -> str:
    s = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _evidence_index(evidences: Dict[str, List[Evidence]]) -> Dict[str, Evidence]:
    index: Dict[str, Evidence] = {}
    for ev_list in evidences.values():
        for ev in ev_list:
            index.setdefault(ev.goal, ev)
    return index


def _display_repo(repo_url: str) -> str:
    if os.path.isdir(repo_url):
        try:
            origin = subprocess.run(["git", "-C", repo_url, "remote", "get-url", "origin"],
                                    capture_output=True, text=True, timeout=10, check=False).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            origin = ""
        return f"{origin} (local working tree)" if origin else "local working tree"
    return repo_url


def _synthesize_criterion(
    dim: Dict,
    ops: List[JudicialOpinion],
    ev: Optional[Evidence],
    ev_index: Dict[str, Evidence],
    security_flaw: bool,
    report_missing: bool,
) -> CriterionResult:
    dim_id, dim_name = dim["id"], dim["name"]
    by_judge = {o.judge: o for o in ops}
    applied: List[str] = []
    overruled: Dict[str, str] = {}

    if not ops:
        return CriterionResult(
            dimension_id=dim_id, dimension_name=dim_name, final_score=1, judge_opinions=[],
            rules_applied=["RULE OF EVIDENCE: bench not convened because evidence could not be collected; criterion fails by default"],
            dissent_summary="No deliberation took place: the EvidenceMissing route was taken.",
            remediation=_remediation(dim, ev, 1),
        )

    scores = {j: o.score for j, o in by_judge.items()}
    spread = max(scores.values()) - min(scores.values())
    active = dict(scores)
    evidence_ok = bool(ev and ev.found)
    gaps = ev.gaps if ev else []

    # --- Security override: confirmed vulnerability voids effort points (most specific rule first) ---
    if security_flaw and dim_id == "safe_tool_engineering" and "Defense" in active:
        overruled["Defense"] = "effort points cannot offset a confirmed security vulnerability"
        active.pop("Defense")
        applied.append("RULE OF SECURITY: confirmed vulnerability overrides Defense effort points")

    # --- Rule of Evidence (fact supremacy): facts overrule interpretation ---
    if dim.get("target_artifact") in ("pdf_report", "pdf_images") and report_missing and "Defense" in active:
        overruled["Defense"] = "no report was submitted, so claims of deep understanding are unsupported (hallucination)"
        active.pop("Defense")
        applied.append("RULE OF EVIDENCE: report missing; Defense overruled for hallucination")
    elif not evidence_ok and active.get("Defense", 0) > 3:
        overruled["Defense"] = f"argued {scores['Defense']}/5 but the detective found the artifact missing or failing ({gaps[0] if gaps else 'not found'})"
        active.pop("Defense")
        applied.append("RULE OF FACT SUPREMACY: Defense score exceeds what the forensic evidence supports")

    # --- Variance > 2 among the surviving opinions: re-evaluate the evidence they dispute ---
    active_spread = (max(active.values()) - min(active.values())) if active else 0
    if active_spread > VARIANCE_THRESHOLD:
        high = max(active, key=active.get)
        low = min(active, key=active.get)
        if evidence_ok and not gaps and active[low] < 3:
            overruled.setdefault(low, f"scored {scores[low]}/5 but re-examined evidence is complete and verified (no gaps)")
            active.pop(low)
            applied.append(f"VARIANCE RE-EVALUATION (spread {active_spread}): evidence verified complete; {low} overruled")
        elif not evidence_ok and active[high] > 3:
            overruled.setdefault(high, f"scored {scores[high]}/5 but re-examined evidence shows the requirement unmet")
            active.pop(high)
            applied.append(f"VARIANCE RE-EVALUATION (spread {active_spread}): evidence shows requirement unmet; {high} overruled")
        elif "TechLead" in active:
            active = {"TechLead": active["TechLead"]}
            applied.append(f"VARIANCE RE-EVALUATION (spread {active_spread}): evidence partially complete ({len(gaps)} gap(s)); Tech Lead's technical-debt assessment is decisive")

    # --- Weighted synthesis of the surviving opinions ---
    weight_sum = sum(BASE_WEIGHTS[j] for j in active) or 1.0
    final = _half_up(sum(BASE_WEIGHTS[j] * s for j, s in active.items()) / weight_sum) if active else 1

    # --- Rule of Functionality: Tech Lead carries the architecture criterion ---
    tech = by_judge.get("TechLead")
    if dim_id in ARCHITECTURE_DIMENSIONS and tech and tech.score >= 4 and evidence_ok:
        final = tech.score
        applied.append("RULE OF FUNCTIONALITY: Tech Lead confirmed a modular, workable architecture; carries highest weight")

    # --- Caps that facts impose regardless of opinion ---
    if not evidence_ok and final > 3:
        final = 3
        applied.append("FACT SUPREMACY CAP: requirement not met in evidence; score capped at 3")
    if security_flaw and dim_id == "safe_tool_engineering" and final > SECURITY_CAP:
        final = SECURITY_CAP
        applied.append("SECURITY CAP: confirmed security flaw caps this criterion at 3")
    if dim_id in ARCHITECTURE_DIMENSIONS and any("ORCHESTRATION FRAUD" in g for g in gaps):
        final = 1
        applied.append("STATUTE OF ORCHESTRATION: purely linear graph (Orchestration Fraud) caps score at 1")
    structured = ev_index.get("structured_output_enforcement")
    if dim_id in ("judicial_nuance", "structured_output_enforcement") and structured and any(
        "HALLUCINATION LIABILITY" in g for g in structured.gaps
    ) and final > 2:
        final = 2
        applied.append("STATUTE OF ORCHESTRATION: judges return free text (Hallucination Liability); capped at 2")
    if dim_id == "judicial_nuance" and any("PERSONA COLLUSION" in g for g in gaps) and final > 2:
        final = 2
        applied.append("PERSONA COLLUSION: judge prompts are near-identical; capped at 2")

    final = max(1, min(5, final))
    return CriterionResult(
        dimension_id=dim_id, dimension_name=dim_name, final_score=final, judge_opinions=ops,
        rules_applied=applied, overruled_judges=sorted(overruled),
        dissent_summary=_dissent(by_judge, spread, overruled, applied),
        remediation=_remediation(dim, ev, final),
    )


def _dissent(by_judge: Dict[str, JudicialOpinion], spread: int, overruled: Dict[str, str], applied: List[str]) -> str:
    p, d, t = by_judge.get("Prosecutor"), by_judge.get("Defense"), by_judge.get("TechLead")
    if spread == 0:
        return f"Unanimous bench ({next(iter(by_judge.values())).score}/5): no dialectical conflict to resolve."
    parts = []
    if p:
        parts.append(f"The Prosecutor ({p.score}/5) charged: \"{_first_sentence(p.argument)}\"")
    if d:
        parts.append(f"The Defense ({d.score}/5) countered: \"{_first_sentence(d.argument)}\"")
    if t:
        parts.append(f"The Tech Lead ({t.score}/5) assessed: \"{_first_sentence(t.argument)}\"")
    if p and d and p.score != d.score:
        parts.append(
            f"They disagreed by {abs(p.score - d.score)} point(s) because the Prosecutor weighed the verified gaps "
            f"while the Defense weighed the implemented strengths."
        )
    if overruled:
        parts.append("Overruled: " + "; ".join(f"{j} ({why})" for j, why in overruled.items()) + ".")
    elif not applied:
        parts.append("No side was overruled; the weighted bench (Tech Lead 40%, Prosecutor 30%, Defense 30%) decided.")
    return " ".join(parts)


def _remediation(dim: Dict, ev: Optional[Evidence], final: int) -> str:
    if ev is not None and ev.found and not ev.gaps and final >= 5:
        return "No remediation required."
    location = ev.location if ev else "(no evidence location)"
    steps = [f"`{location}`: resolve \"{g}\"" for g in (ev.gaps if ev else [])[:5]]
    steps.append(dim.get("remediation") or "Address the failure pattern: " + dim.get("failure_pattern", ""))
    return " | ".join(steps)


def _tier(score: float) -> str:
    if score >= 4.5:
        return "Master Thinker"
    if score >= 3:
        return "Competent Orchestrator"
    return "Vibe Coder"


def chief_justice_node(state: AgentState) -> Dict:
    """
    ChiefJusticeNode: applies the deterministic synthesis rules (security override, fact
    supremacy, functionality weight, variance re-evaluation, statute caps, dissent requirement)
    to every criterion and renders the Markdown audit report.
    """
    print("[Supreme Court] ChiefJusticeNode synthesizing verdict & resolving dialectical conflicts...")
    opinions = state.get("opinions", [])
    evidences = state.get("evidences", {})
    dimensions = state.get("rubric_dimensions", [])
    rules_text = state.get("synthesis_rules", {})
    errors = state.get("errors", [])
    repo_url = state.get("repo_url", "")
    pdf_path = state.get("pdf_path", "")

    ev_index = _evidence_index(evidences)
    security_hits = [g for e in ev_index.values() for g in e.gaps if "SECURITY VIOLATION" in g]
    security_flaw = bool(security_hits)
    report_missing = not pdf_path or not os.path.isfile(pdf_path)

    grouped: Dict[str, List[JudicialOpinion]] = {}
    for op in opinions:
        grouped.setdefault(op.criterion_id, []).append(op)

    results = [
        _synthesize_criterion(dim, grouped.get(dim["id"], []), ev_index.get(dim["id"]), ev_index, security_flaw, report_missing)
        for dim in dimensions
    ]

    raw_overall = round(sum(r.final_score for r in results) / len(results), 2) if results else 0.0
    overall = raw_overall
    if security_flaw and overall > SECURITY_CAP:
        overall = float(SECURITY_CAP)

    report = _render(repo_url, pdf_path, results, ev_index, overall, raw_overall, security_hits, rules_text, errors)
    return {"final_report": report[0], "markdown_output": report[1]}


def _render(
    repo_url: str, pdf_path: str, results: List[CriterionResult], ev_index: Dict[str, Evidence],
    overall: float, raw_overall: float, security_hits: List[str], rules_text: Dict[str, str], errors: List[str],
) -> Tuple[AuditReport, str]:
    display_repo = _display_repo(repo_url)
    n = len(results)
    overruled_counts = {j: sum(j in r.overruled_judges for r in results) for j in ("Prosecutor", "Defense", "TechLead")}
    variance_reviews = sum(any(a.startswith("VARIANCE") for a in r.rules_applied) for r in results)
    ranked = sorted(results, key=lambda r: r.final_score)
    weakest = [f"{r.dimension_name} ({r.final_score}/5)" for r in ranked[:3] if r.final_score < 5]
    strongest = [f"{r.dimension_name}" for r in sorted(results, key=lambda r: -r.final_score)[:3] if r.final_score >= 4]

    exec_summary = (
        f"Overall verdict: {overall}/5.0 ({_tier(overall)}) across {n} rubric criteria. "
        + (f"A confirmed security flaw capped the total from {raw_overall} to {overall} under the security_override rule. "
           if overall != raw_overall else "")
        + f"For every criterion the Prosecutor, Defense and Tech Lead judged the same detective evidence in parallel; "
        f"the Chief Justice then resolved their disagreement with deterministic rules rather than an average. "
        f"{variance_reviews} criterion/criteria showed a score spread above {VARIANCE_THRESHOLD} and were re-evaluated against the evidence; "
        f"the Defense was overruled {overruled_counts['Defense']} time(s) and the Prosecutor {overruled_counts['Prosecutor']} time(s). "
        + (f"Strongest: {', '.join(strongest)}. " if strongest else "")
        + (f"Highest-priority remediation: {', '.join(weakest)}." if weakest else "No criterion requires remediation.")
    )

    plan_items = [r for r in ranked if r.remediation != "No remediation required."]
    remediation_plan = "\n".join(
        f"{i}. **{r.dimension_name}** ({r.final_score}/5): " + "\n   - ".join([""] + r.remediation.split(" | "))
        for i, r in enumerate(plan_items, 1)
    ) or "No remediation required. All criteria meet the Master Thinker standard."

    audit_report = AuditReport(repo_url=display_repo, executive_summary=exec_summary, overall_score=overall,
                               criteria=results, remediation_plan=remediation_plan)

    def score_of(r: CriterionResult, judge: str) -> str:
        op = next((o for o in r.judge_opinions if o.judge == judge), None)
        return str(op.score) if op else "-"

    md = [
        "# Automaton Auditor Verdict & Governance Report",
        "",
        f"**Repository**: `{display_repo}`  ",
        f"**Report analysed**: `{pdf_path or 'none'}`  ",
        f"**Overall Score**: `{overall} / 5.0` ({_tier(overall)})  ",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Executive Summary",
        "",
        exec_summary,
        "",
        "| Criterion | Prosecutor | Defense | Tech Lead | Final | Rules fired |",
        "|---|:-:|:-:|:-:|:-:|---|",
    ]
    for r in results:
        rules = ", ".join(a.split(":")[0].title() for a in r.rules_applied) or "-"
        md.append(f"| {r.dimension_name} | {score_of(r, 'Prosecutor')} | {score_of(r, 'Defense')} | "
                  f"{score_of(r, 'TechLead')} | **{r.final_score}** | {rules} |")
    if security_hits:
        md += ["", f"> **Security finding**: {security_hits[0]}"]

    md += ["", "---", "", "## Criterion Breakdown", ""]
    for r in results:
        ev = ev_index.get(r.dimension_id)
        md.append(f"### {r.dimension_name} (`{r.dimension_id}`) — {r.final_score}/5")
        md.append("")
        if ev:
            md.append(f"**Evidence** (`{ev.location}`, found={ev.found}, confidence={ev.confidence}): {ev.rationale}")
            md.append("")
        md.append("**Judicial Opinions**:")
        for op in r.judge_opinions:
            cites = ", ".join(c for c in op.cited_evidence[:3] if c)
            md.append(f"- **{op.judge}** ({op.score}/5): {op.argument}" + (f" *(Cited: {cites})*" if cites else ""))
        md.append("")
        md.append(f"**Dissent**: {r.dissent_summary}")
        if r.rules_applied:
            md.append("")
            md.append("**Rules applied**: " + " ".join(f"`{a}`" for a in r.rules_applied))
        md.append("")
        md.append(f"**Remediation**: {r.remediation.replace(' | ', ' ')}")
        md += ["", "---", ""]

    md += ["## Remediation Plan", "", "Ordered by priority (lowest score first):", "", remediation_plan, ""]
    if rules_text:
        md += ["## Synthesis Rules (Constitution)", ""]
        md += [f"- **{k}**: {v}" for k, v in rules_text.items()]
        md.append("")
    if errors:
        md += ["## Node Failures & Warnings", ""] + [f"- {e}" for e in errors] + [""]

    return audit_report, "\n".join(md)
