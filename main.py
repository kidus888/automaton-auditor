import argparse
import json
import os
import sys
from dotenv import load_dotenv
from src.graph import auditor_graph, load_default_rubric

load_dotenv()


def run_auditor(repo_url: str, pdf_path: str, output_path: str = None) -> str:
    """
    Runs the Automaton Auditor swarm against a target repository URL and PDF report.
    """
    print("================================================================================")
    print("              THE AUTOMATON AUDITOR - DIGITAL COURTROOM SYSTEM                 ")
    print("================================================================================")
    print(f"Target Repository: {repo_url}")
    print(f"Target PDF Report: {pdf_path}")

    rubric_dims = load_default_rubric()
    print(f"Loaded {len(rubric_dims)} Rubric Dimensions from rubric.json.")

    initial_state = {
        "repo_url": repo_url,
        "pdf_path": pdf_path,
        "rubric_dimensions": rubric_dims,
        "evidences": {},
        "opinions": [],
        "final_report": None,
        "markdown_output": "",
    }

    final_state = auditor_graph.invoke(initial_state)

    markdown_report = final_state.get("markdown_output", "")

    if not output_path:
        out_dir = os.path.join(os.getcwd(), "audit", "report_onself_generated")
        os.makedirs(out_dir, exist_ok=True)
        output_path = os.path.join(out_dir, "audit_report.md")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_report)

    print("\n================================================================================")
    print(f"AUDIT COMPLETE! Report generated successfully at:\n{output_path}")
    print("================================================================================\n")

    return markdown_report


def main():
    parser = argparse.ArgumentParser(description="Automaton Auditor - Orchestrating LangGraph Swarms for Governance")
    parser.add_argument("--repo", type=str, default=os.getcwd(), help="GitHub Repository URL or local path")
    parser.add_argument("--pdf", type=str, default="reports/interim_report.pdf", help="Path to PDF Architectural Report")
    parser.add_argument("--output", type=str, default=None, help="Output Markdown report path")

    args = parser.parse_args()
    run_auditor(args.repo, args.pdf, args.output)


if __name__ == "__main__":
    main()
