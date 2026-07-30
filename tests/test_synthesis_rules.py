import pytest
from src.nodes.justice import chief_justice_node
from src.state import Evidence, JudicialOpinion, AgentState


def test_chief_justice_security_override_rule():
    # Setup mock state where Prosecutor identifies security violation
    mock_dimensions = [
        {
            "id": "safe_tool_engineering",
            "name": "Safe Tool Engineering",
            "failure_pattern": "Raw os.system call",
        }
    ]

    mock_evidences = {
        "github_repo": [
            Evidence(
                goal="safe_tool_engineering",
                found=False,
                location="src/tools/clone.py",
                rationale="SECURITY VIOLATION: raw os.system() found in cloning script",
                confidence=1.0,
            )
        ]
    }

    mock_opinions = [
        JudicialOpinion(
            judge="Prosecutor",
            criterion_id="safe_tool_engineering",
            score=1,
            argument="Security breach!",
            cited_evidence=["src/tools/clone.py"],
        ),
        JudicialOpinion(
            judge="Defense",
            criterion_id="safe_tool_engineering",
            score=5,
            argument="Great effort, works locally!",
            cited_evidence=["src/tools/clone.py"],
        ),
        JudicialOpinion(
            judge="TechLead",
            criterion_id="safe_tool_engineering",
            score=4,
            argument="Functional but insecure",
            cited_evidence=["src/tools/clone.py"],
        ),
    ]

    state: AgentState = {
        "repo_url": "https://github.com/test/repo",
        "pdf_path": "",
        "rubric_dimensions": mock_dimensions,
        "evidences": mock_evidences,
        "opinions": mock_opinions,
        "final_report": None,
        "markdown_output": "",
    }

    res = chief_justice_node(state)
    report = res["final_report"]

    assert len(report.criteria) == 1
    crit = report.criteria[0]

    # Score should be capped at 3 due to Security Override Rule despite Defense score 5 and Tech Lead 4
    assert crit.final_score <= 3
    assert crit.dissent_summary is not None
    assert "RULE OF SECURITY OVERRIDE" in crit.dissent_summary
