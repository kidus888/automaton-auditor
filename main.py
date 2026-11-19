import argparse
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Load .env before LangChain/LangSmith read tracing configuration
load_dotenv()

from src.graph import auditor_graph  # noqa: E402
from src.rubric import load_rubric  # noqa: E402

DEFAULT_REPORT_CANDIDATES = ["reports/final_report.pdf", "reports/interim_report.pdf", "reports/interim_report.md"]
SELF_OUTPUT_DIR = os.path.join("audit", "report_onself_generated")
PEER_OUTPUT_DIR = os.path.join("audit", "report_onpeer_generated")
TRACE_DIR = os.path.join("audit", "langsmith_logs")


def _configure_tracing() -> Optional[str]:
    """Returns the LangSmith project name if tracing is active. Disables tracing (instead of
    spamming upload errors) when it is requested without an API key."""
    requested = any(os.environ.get(k, "").lower() == "true" for k in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"))
    has_key = bool(os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY"))
    if requested and not has_key:
        print("[Observability] LANGCHAIN_TRACING_V2=true but no LANGCHAIN_API_KEY; LangSmith disabled, local trace only.")
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
        return None
    if requested:
        return os.environ.get("LANGCHAIN_PROJECT") or os.environ.get("LANGSMITH_PROJECT") or "automaton-auditor"
    return None


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _langsmith_url(run_id: uuid.UUID, project: str) -> Optional[str]:
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers
        from langsmith import Client

        wait_for_all_tracers()
        client = Client()
        return client.get_run_url(run=client.read_run(run_id), project_name=project)
    except Exception as e:  # noqa: BLE001 - observability must never fail the audit
        print(f"[Observability] Could not resolve LangSmith URL: {e}")
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text.rstrip("/").split("/")[-1] or "repo").strip("-")[:40] or "repo"


def run_auditor(repo_url: str, pdf_path: str, output_path: Optional[str] = None,
                trace_dir: str = TRACE_DIR, peer: bool = False) -> str:
    """Runs the Automaton Auditor swarm against a repository and its PDF/Markdown report."""
    print("================================================================================")
    print("              THE AUTOMATON AUDITOR - DIGITAL COURTROOM SYSTEM                 ")
    print("================================================================================")
    print(f"Target Repository: {repo_url}")
    print(f"Target Report:     {pdf_path}")

    rubric = load_rubric()
    project = _configure_tracing()
    run_id = uuid.uuid4()
    config = {
        "run_name": "AutomatonAuditor",
        "run_id": run_id,
        "tags": ["automaton-auditor", "digital-courtroom", "peer-audit" if peer else "self-audit"],
        "metadata": {
            "repo_url": repo_url,
            "pdf_path": pdf_path,
            "rubric_version": rubric.get("rubric_metadata", {}).get("version"),
        },
    }
    initial_state = {
        "repo_url": repo_url,
        "pdf_path": pdf_path,
        "rubric_dimensions": rubric.get("dimensions", []),
        "synthesis_rules": rubric.get("synthesis_rules", {}),
        "evidences": {},
        "repo_manifest": [],
        "report_claims": None,
        "opinions": [],
        "errors": [],
        "final_report": None,
        "markdown_output": "",
    }

    events: List[Dict] = []
    final_state: Dict = {}
    for mode, chunk in auditor_graph.stream(initial_state, config=config, stream_mode=["updates", "values"]):
        if mode == "updates":
            for node, update in chunk.items():
                events.append({"timestamp": datetime.now(timezone.utc).isoformat(), "node": node, "update": _jsonable(update)})
        else:
            final_state = chunk

    markdown_report = final_state.get("markdown_output", "")
    if not output_path:
        output_path = os.path.join(PEER_OUTPUT_DIR if peer else SELF_OUTPUT_DIR, "audit_report.md")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown_report)

    trace_url = _langsmith_url(run_id, project) if project else None
    report = final_state.get("final_report")
    reasoning_loop = [
        {
            "criterion_id": cr.dimension_id,
            "opinions": [o.model_dump() for o in cr.judge_opinions],
            "rules_applied": cr.rules_applied,
            "overruled": cr.overruled_judges,
            "dissent": cr.dissent_summary,
            "final_score": cr.final_score,
        }
        for cr in (report.criteria if report else [])
    ]
    os.makedirs(trace_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trace_path = os.path.join(trace_dir, f"trace_{_slug(repo_url)}_{stamp}.json")
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_id": str(run_id),
            "langsmith_project": project,
            "langsmith_url": trace_url,
            "repo_url": repo_url,
            "pdf_path": pdf_path,
            "overall_score": report.overall_score if report else None,
            "reasoning_loop": reasoning_loop,
            "node_events": events,
        }, f, indent=2, default=str)

    print("\n================================================================================")
    print(f"AUDIT COMPLETE! Report: {output_path}")
    print(f"Reasoning-loop trace:   {trace_path}")
    if trace_url:
        print(f"LangSmith trace:        {trace_url}")
    print("================================================================================\n")
    return markdown_report


def _default_report() -> str:
    return next((p for p in DEFAULT_REPORT_CANDIDATES if os.path.isfile(p)), DEFAULT_REPORT_CANDIDATES[0])


def main():
    parser = argparse.ArgumentParser(description="Automaton Auditor - Orchestrating LangGraph Swarms for Governance")
    parser.add_argument("--repo", type=str, default=os.getcwd(), help="GitHub repository URL or local path")
    parser.add_argument("--pdf", type=str, default=None,
                        help=f"Path to the PDF (or Markdown) architecture report (default: first of {DEFAULT_REPORT_CANDIDATES} that exists)")
    parser.add_argument("--output", type=str, default=None, help="Output Markdown report path")
    parser.add_argument("--peer", action="store_true", help=f"Write the report to {PEER_OUTPUT_DIR}/ (auditing a peer)")
    parser.add_argument("--trace-dir", type=str, default=TRACE_DIR, help="Where to export the reasoning-loop trace")
    args = parser.parse_args()
    run_auditor(args.repo, args.pdf or _default_report(), args.output, args.trace_dir, args.peer)


if __name__ == "__main__":
    main()
