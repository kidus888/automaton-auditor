import os
from typing import Dict, List, Optional
from src.state import AgentState, AuditReport, CriterionResult, Evidence, JudicialOpinion


def _group_opinions_by_criterion(opinions: List[JudicialOpinion]) -> Dict[str, List[JudicialOpinion]]:
    grouped: Dict[str, List[JudicialOpinion]] = {}
    for op in opinions:
        cid = op.criterion_id
        if cid not in grouped:
            grouped[cid] = []
        grouped[cid].append(op)
    return grouped


def chief_justice_node(state: AgentState) -> Dict:
    """
    ChiefJusticeNode (The Synthesis Engine):
    Executes hardcoded deterministic conflict resolution rules (Rule of Security, Fact Supremacy,
    Rule of Functionality, Dissent Requirement) over the judicial opinions to produce the final AuditReport
    and rendered Markdown audit report.
    """
    print("[Supreme Court] ChiefJusticeNode synthesizing verdict & resolving dialectical conflicts...")
    opinions = state.get("opinions", [])
    evidences_dict = state.get("evidences", {})
    dimensions = state.get("rubric_dimensions", [])
    repo_url = state.get("repo_url", "https://github.com/target/repo")

    grouped_opinions = _group_opinions_by_criterion(opinions)

    criterion_results: List[CriterionResult] = []
    total_score_sum = 0.0

    for dim in dimensions:
        dim_id = dim["id"]
        dim_name = dim["name"]
        dim_ops = grouped_opinions.get(dim_id, [])

        # Separate opinions by persona
        pros_op = next((o for o in dim_ops if o.judge == "Prosecutor"), None)
        def_op = next((o for o in dim_ops if o.judge == "Defense"), None)
        tech_op = next((o for o in dim_ops if o.judge == "TechLead"), None)

        scores = [o.score for o in dim_ops if o.score is not None]
        min_score = min(scores) if scores else 1
        max_score = max(scores) if scores else 5
        score_variance = max_score - min_score

        # Default weighted calculation: Tech Lead (40%), Prosecutor (30%), Defense (30%)
        p_score = pros_op.score if pros_op else 1
        d_score = def_op.score if def_op else 3
        t_score = tech_op.score if tech_op else 3

        raw_final = round((t_score * 0.4) + (p_score * 0.3) + (d_score * 0.3))

        dissent_summary: Optional[str] = None
        overruled_reason = ""

        # --- Deterministic Rule 1: Rule of Security ---
        # Confirmed security flaws cap final score at 3 regardless of Defense effort points.
        # NOTE: only the "SECURITY VIOLATION" marker is checked here (not a bare "os.system"
        # substring) because analyze_safe_tool_engineering's *clean* rationale also reads
        # "No raw os.system() calls detected" -- matching on the substring alone would false-
        # positive a security flaw on every audit, safe or not.
        has_security_flaw = False
        for category, ev_list in evidences_dict.items():
            for ev in ev_list:
                if "SECURITY VIOLATION" in ev.rationale:
                    has_security_flaw = True

        if has_security_flaw and dim_id == "safe_tool_engineering":
            raw_final = min(raw_final, 3)
            overruled_reason += "[RULE OF SECURITY OVERRIDE]: Confirmed security vulnerability (raw os.system) caps score at 3. Defense effort points overruled. "

        # --- Deterministic Rule 2: Rule of Evidence (Fact Supremacy) ---
        # If Defense claims feature/metacognition exists but Detective evidence shows missing, Defense is overruled
        matching_evidence = None
        for category, ev_list in evidences_dict.items():
            for ev in ev_list:
                if ev.goal == dim_id or dim_id in ev.goal:
                    matching_evidence = ev

        if matching_evidence and not matching_evidence.found and d_score > 3 and not has_security_flaw:
            raw_final = min(raw_final, 3)
            overruled_reason += "[RULE OF FACT SUPREMACY]: Defense claimed implementation effort, but Detective evidence proved artifact is missing/flawed. Defense overruled for hallucination liability. "

        # --- Deterministic Rule 3: Rule of Functionality ---
        # If Tech Lead confirms modular workable architecture, carry highest weight for Architecture
        if dim_id == "graph_orchestration" and tech_op and tech_op.score >= 4:
            raw_final = max(raw_final, tech_op.score)
            overruled_reason += "[RULE OF FUNCTIONALITY]: Tech Lead confirmed modular, parallel StateGraph architecture carrying decisive weight. "

        # --- Dissent Requirement (Score Variance > 2) ---
        if score_variance > 2 or overruled_reason:
            dissent_summary = (
                f"Judge Score Variance = {score_variance} (Prosecutor={p_score}, Defense={d_score}, TechLead={t_score}). "
                f"Conflict Resolution: {overruled_reason or 'Prosecutor and Defense disagreed significantly. Tech Lead pragmatism applied.'}"
            )

        # Generate specific file-level remediation instruction
        if raw_final < 5:
            remediation = f"Fix in target file: {dim.get('failure_pattern', 'Review implementation to meet success criteria.')}"
        else:
            remediation = "Architecture meets Master Thinker standards. No remediation required."

        criterion_result = CriterionResult(
            dimension_id=dim_id,
            dimension_name=dim_name,
            final_score=max(1, min(5, raw_final)),
            judge_opinions=dim_ops,
            dissent_summary=dissent_summary,
            remediation=remediation,
        )
        criterion_results.append(criterion_result)
        total_score_sum += criterion_result.final_score

    overall_score = round(total_score_sum / len(criterion_results), 2) if criterion_results else 0.0

    exec_summary = (
        f"Automaton Auditor Final Verdict for Repository '{repo_url}': Overall Grade {overall_score}/5.0. "
        f"The Digital Courtroom evaluated {len(criterion_results)} rubric dimensions using parallel Detective evidence, "
        f"dialectical Judicial deliberation (Prosecutor, Defense, Tech Lead), and deterministic Supreme Court synthesis rules."
    )

    remediation_items = [f"- [{cr.dimension_name}] (Score: {cr.final_score}/5): {cr.remediation}" for cr in criterion_results if cr.final_score < 5]
    remediation_plan_text = "\n".join(remediation_items) if remediation_items else "No critical remediation required. Repository passed all 10 forensic standards."

    audit_report = AuditReport(
        repo_url=repo_url,
        executive_summary=exec_summary,
        overall_score=overall_score,
        criteria=criterion_results,
        remediation_plan=remediation_plan_text,
    )

    # Render Markdown report
    md_lines = [
        f"# Automaton Auditor Verdict & Governance Report",
        f"**Repository URL**: `{repo_url}`  ",
        f"**Overall Score**: `{overall_score} / 5.0`  ",
        f"\n## Executive Summary\n{exec_summary}\n",
        "---",
        "\n## Criterion Breakdown\n",
    ]

    for cr in criterion_results:
        md_lines.append(f"### {cr.dimension_name} (ID: `{cr.dimension_id}`)")
        md_lines.append(f"**Final Synthesized Score**: `{cr.final_score} / 5`")
        if cr.dissent_summary:
            md_lines.append(f"> **Dissent & Resolution**: {cr.dissent_summary}")

        md_lines.append("\n**Judicial Opinions**:")
        for op in cr.judge_opinions:
            md_lines.append(f"- **{op.judge}** (Score: {op.score}/5): {op.argument} *(Cited: {', '.join(op.cited_evidence)})*")

        md_lines.append(f"\n**Remediation Action**: {cr.remediation}\n")
        md_lines.append("---")

    md_lines.append("\n## Remediation Plan\n")
    md_lines.append(remediation_plan_text)

    markdown_str = "\n".join(md_lines)

    return {
        "final_report": audit_report,
        "markdown_output": markdown_str,
    }
