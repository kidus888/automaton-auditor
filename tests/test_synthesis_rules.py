from src.nodes.justice import chief_justice_node
from src.state import Evidence, JudicialOpinion


def _op(judge, score, cid="safe_tool_engineering"):
    return JudicialOpinion(judge=judge, criterion_id=cid, score=score, argument=f"{judge} argues.", cited_evidence=["x"])


def _run(dims, evidences, opinions, pdf_path=""):
    state = {
        "repo_url": "https://github.com/test/repo",
        "pdf_path": pdf_path,
        "rubric_dimensions": dims,
        "synthesis_rules": {"security_override": "cap at 3"},
        "evidences": evidences,
        "opinions": opinions,
        "errors": [],
    }
    return chief_justice_node(state)["final_report"]


def test_chief_justice_security_override_rule():
    dims = [{"id": "safe_tool_engineering", "name": "Safe Tool Engineering", "target_artifact": "github_repo"}]
    evidences = {"github_repo": [Evidence(
        goal="safe_tool_engineering", found=False, location="src/tools/clone.py",
        rationale="SECURITY VIOLATION: raw os.system() found in cloning script",
        gaps=["SECURITY VIOLATION: src/tools/clone.py: raw os.system() with unsanitized dynamic input (line 3)"],
    )]}
    report = _run(dims, evidences, [_op("Prosecutor", 1), _op("Defense", 5), _op("TechLead", 4)])
    crit = report.criteria[0]
    assert crit.final_score <= 3
    assert "Defense" in crit.overruled_judges
    assert any(r.startswith("RULE OF SECURITY") for r in crit.rules_applied)
    assert "Prosecutor (1/5)" in crit.dissent_summary and "Defense (5/5)" in crit.dissent_summary


def test_security_flaw_caps_total_score():
    dims = [{"id": f"d{i}", "name": f"D{i}", "target_artifact": "github_repo"} for i in range(3)]
    evidences = {"github_repo": [Evidence(goal=f"d{i}", found=True, location="f", rationale="ok") for i in range(3)]
                 + [Evidence(goal="safe_tool_engineering", found=False, location="t", rationale="bad",
                             gaps=["SECURITY VIOLATION: os.system"])]}
    ops = [_op(j, 5, f"d{i}") for i in range(3) for j in ("Prosecutor", "Defense", "TechLead")]
    report = _run(dims, evidences, ops)
    assert all(c.final_score == 5 for c in report.criteria)
    assert report.overall_score == 3.0
    assert "security_override" in report.executive_summary


def test_variance_re_evaluation_overrules_prosecutor_when_evidence_is_complete():
    dims = [{"id": "state_management_rigor", "name": "State", "target_artifact": "github_repo"}]
    evidences = {"github_repo": [Evidence(goal="state_management_rigor", found=True, location="src/state.py", rationale="ok")]}
    ops = [_op("Prosecutor", 1, "state_management_rigor"), _op("Defense", 5, "state_management_rigor"),
           _op("TechLead", 5, "state_management_rigor")]
    crit = _run(dims, evidences, ops).criteria[0]
    assert crit.overruled_judges == ["Prosecutor"]
    assert crit.final_score == 5
    assert any(r.startswith("VARIANCE RE-EVALUATION") for r in crit.rules_applied)


def test_variance_re_evaluation_overrules_defense_when_evidence_missing():
    dims = [{"id": "graph_orchestration", "name": "Graph", "target_artifact": "github_repo"}]
    evidences = {"github_repo": [Evidence(goal="graph_orchestration", found=False, location="src/graph.py",
                                          rationale="linear", gaps=["ORCHESTRATION FRAUD: purely linear"])]}
    ops = [_op("Prosecutor", 1, "graph_orchestration"), _op("Defense", 5, "graph_orchestration"),
           _op("TechLead", 2, "graph_orchestration")]
    crit = _run(dims, evidences, ops).criteria[0]
    assert "Defense" in crit.overruled_judges
    assert crit.final_score == 1  # Orchestration Fraud statute
    assert "src/graph.py" in crit.remediation


def test_rule_of_evidence_overrules_defense_when_report_missing():
    dims = [{"id": "theoretical_depth", "name": "Depth", "target_artifact": "pdf_report"}]
    evidences = {"pdf_report": [Evidence(goal="theoretical_depth", found=False, location="missing", rationale="none")]}
    ops = [_op("Prosecutor", 1, "theoretical_depth"), _op("Defense", 4, "theoretical_depth"),
           _op("TechLead", 1, "theoretical_depth")]
    crit = _run(dims, evidences, ops, pdf_path="does/not/exist.pdf").criteria[0]
    assert "Defense" in crit.overruled_judges
    assert crit.final_score == 1


def test_bench_not_convened_fails_criterion():
    dims = [{"id": "git_forensic_analysis", "name": "Git", "target_artifact": "github_repo"}]
    crit = _run(dims, {}, []).criteria[0]
    assert crit.final_score == 1
    assert "not convened" in crit.rules_applied[0]
