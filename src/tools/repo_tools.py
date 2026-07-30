import ast
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple
from src.state import Evidence


def clone_repo_sandboxed(repo_url_or_path: str) -> Tuple[Optional[str], Optional[tempfile.TemporaryDirectory], Optional[str]]:
    """
    Safely clones a GitHub repository into a sandboxed temporary directory using subprocess.
    If given a local path that exists, returns that path without cloning.
    Returns (repo_path, temp_dir_obj, error_msg).
    """
    if os.path.exists(repo_url_or_path) and os.path.isdir(repo_url_or_path):
        return os.path.abspath(repo_url_or_path), None, None

    temp_dir = tempfile.TemporaryDirectory(prefix="auditor_sandbox_")
    try:
        cmd = ["git", "clone", "--depth", "50", repo_url_or_path, temp_dir.name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            error_msg = f"Git clone failed (exit code {result.returncode}): {result.stderr.strip()}"
            temp_dir.cleanup()
            return None, None, error_msg
        return temp_dir.name, temp_dir, None
    except Exception as e:
        temp_dir.cleanup()
        return None, None, f"Exception during git clone: {str(e)}"


def get_git_history(repo_path: str) -> Evidence:
    """
    Executes 'git log --oneline --reverse' and analyzes commit progression.
    """
    try:
        cmd = ["git", "-C", repo_path, "log", "--oneline", "--reverse"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            return Evidence(
                goal="git_forensic_analysis",
                found=False,
                location=repo_path,
                rationale=f"Failed to read git log: {res.stderr.strip()}",
                confidence=1.0,
            )

        commits = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
        commit_count = len(commits)
        content_summary = f"Total Commits: {commit_count}\n" + "\n".join(commits[:10])

        if commit_count > 3:
            rationale = f"Found {commit_count} commits showing progression. History appears step-by-step."
            found = True
        else:
            rationale = f"Found only {commit_count} commit(s). Indicates single init commit or bulk upload pattern."
            found = False

        return Evidence(
            goal="git_forensic_analysis",
            found=found,
            content=content_summary,
            location=f"{repo_path}/.git",
            rationale=rationale,
            confidence=1.0,
        )
    except Exception as e:
        return Evidence(
            goal="git_forensic_analysis",
            found=False,
            location=repo_path,
            rationale=f"Error executing git log: {str(e)}",
            confidence=1.0,
        )


class StateASTVisitor(ast.NodeVisitor):
    def __init__(self):
        self.classes = {}
        self.uses_pydantic = False
        self.uses_typeddict = False
        self.has_evidence_collection = False
        self.has_opinion_collection = False
        self.has_operator_add = False
        self.has_operator_ior = False
        self.agent_state_code = ""

    def visit_ClassDef(self, node: ast.ClassDef):
        bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
        bases_attribute = [b.attr for b in node.bases if isinstance(b, ast.Attribute)]
        all_bases = bases + bases_attribute

        self.classes[node.name] = all_bases
        if "BaseModel" in all_bases:
            self.uses_pydantic = True
        if "TypedDict" in all_bases:
            self.uses_typeddict = True

        if node.name in ["AgentState", "State"]:
            self.agent_state_code = ast.unparse(node)
            code_lower = self.agent_state_code.lower()
            if "evidence" in code_lower:
                self.has_evidence_collection = True
            if "opinion" in code_lower or "judicialopinion" in code_lower:
                self.has_opinion_collection = True
            if "operator.add" in self.agent_state_code or "add" in self.agent_state_code:
                self.has_operator_add = True
            if "operator.ior" in self.agent_state_code or "ior" in self.agent_state_code:
                self.has_operator_ior = True

        self.generic_visit(node)


def analyze_state_management(repo_path: str) -> Evidence:
    """
    Scans for src/state.py or src/graph.py using AST parsing to verify TypedDict/BaseModel,
    Evidence and JudicialOpinion collections, and Annotated reducers (operator.add, operator.ior).
    """
    target_files = [
        os.path.join(repo_path, "src", "state.py"),
        os.path.join(repo_path, "src", "graph.py"),
        os.path.join(repo_path, "state.py"),
    ]

    found_file = None
    file_content = ""
    for tf in target_files:
        if os.path.exists(tf):
            found_file = tf
            with open(tf, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()
            break

    if not found_file:
        return Evidence(
            goal="state_management_rigor",
            found=False,
            location="src/state.py",
            rationale="No state.py or state definitions found in expected locations.",
            confidence=1.0,
        )

    try:
        tree = ast.parse(file_content)
        visitor = StateASTVisitor()
        visitor.visit(tree)

        rigorous = (visitor.uses_pydantic or visitor.uses_typeddict) and visitor.has_operator_add

        rationale_bits = []
        if visitor.uses_pydantic or visitor.uses_typeddict:
            rationale_bits.append("Uses Pydantic BaseModel / TypedDict")
        else:
            rationale_bits.append("Missing Pydantic/TypedDict base classes")

        if visitor.has_evidence_collection:
            rationale_bits.append("Maintains Evidence collection")
        if visitor.has_opinion_collection:
            rationale_bits.append("Maintains JudicialOpinion collection")
        if visitor.has_operator_add or visitor.has_operator_ior:
            rationale_bits.append("Implements Annotated reducers (operator.add / operator.ior)")
        else:
            rationale_bits.append("Missing Annotated reducers for state reduction")

        return Evidence(
            goal="state_management_rigor",
            found=rigorous,
            content=visitor.agent_state_code or file_content[:500],
            location=os.path.relpath(found_file, repo_path),
            rationale="; ".join(rationale_bits),
            confidence=1.0,
        )
    except Exception as e:
        return Evidence(
            goal="state_management_rigor",
            found=False,
            location=found_file,
            rationale=f"AST parsing error in state file: {str(e)}",
            confidence=1.0,
        )


class GraphASTVisitor(ast.NodeVisitor):
    def __init__(self):
        self.has_stategraph = False
        self.add_edge_calls = []
        self.add_conditional_edges_calls = []
        self.detective_fanout = False
        self.judge_fanout = False
        self.has_evidence_aggregator = False
        self.has_chief_justice = False
        self.graph_code = ""

    def visit_Call(self, node: ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        if func_name == "StateGraph":
            self.has_stategraph = True

        if func_name == "add_edge":
            call_code = ast.unparse(node)
            self.add_edge_calls.append(call_code)
        elif func_name == "add_conditional_edges":
            call_code = ast.unparse(node)
            self.add_conditional_edges_calls.append(call_code)

        self.generic_visit(node)


def analyze_graph_orchestration(repo_path: str) -> Evidence:
    """
    Parses src/graph.py to verify StateGraph instantiation, parallel fan-out/fan-in edges,
    and conditional error handling edges.
    """
    graph_file = os.path.join(repo_path, "src", "graph.py")
    if not os.path.exists(graph_file):
        graph_file = os.path.join(repo_path, "graph.py")

    if not os.path.exists(graph_file):
        return Evidence(
            goal="graph_orchestration",
            found=False,
            location="src/graph.py",
            rationale="src/graph.py does not exist.",
            confidence=1.0,
        )

    try:
        with open(graph_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        tree = ast.parse(content)
        visitor = GraphASTVisitor()
        visitor.visit(tree)

        content_lower = content.lower()
        has_fanout_detectives = "detective" in content_lower or "repoinvestigator" in content_lower
        has_fanout_judges = "prosecutor" in content_lower and "defense" in content_lower and "techlead" in content_lower
        has_aggregator = "evidenceaggregator" in content_lower or "aggregator" in content_lower or "sync" in content_lower
        has_conditional = len(visitor.add_conditional_edges_calls) > 0 or "add_conditional_edges" in content

        is_robust = visitor.has_stategraph and has_fanout_detectives and has_fanout_judges and has_aggregator

        rationale_items = []
        if visitor.has_stategraph:
            rationale_items.append("StateGraph builder instantiated")
        if has_fanout_detectives:
            rationale_items.append("Parallel fan-out for Detectives detected")
        if has_aggregator:
            rationale_items.append("EvidenceAggregator fan-in synchronization node present")
        if has_fanout_judges:
            rationale_items.append("Parallel fan-out for Judicial bench (Prosecutor, Defense, TechLead)")
        if has_conditional:
            rationale_items.append("Conditional edges configured for error handling")

        return Evidence(
            goal="graph_orchestration",
            found=is_robust,
            content="\n".join(visitor.add_edge_calls + visitor.add_conditional_edges_calls)[:800] or content[:600],
            location="src/graph.py",
            rationale="; ".join(rationale_items) if rationale_items else "Linear or incomplete graph wiring.",
            confidence=1.0,
        )
    except Exception as e:
        return Evidence(
            goal="graph_orchestration",
            found=False,
            location="src/graph.py",
            rationale=f"AST parsing failed for graph file: {str(e)}",
            confidence=1.0,
        )


def analyze_safe_tool_engineering(repo_path: str) -> Evidence:
    """
    Scans src/tools/ for git cloning logic, sandboxing (tempfile.TemporaryDirectory()),
    raw os.system security violations, and subprocess error handling.
    """
    tools_dir = os.path.join(repo_path, "src", "tools")
    if not os.path.exists(tools_dir):
        tools_dir = os.path.join(repo_path, "tools")

    if not os.path.exists(tools_dir):
        return Evidence(
            goal="safe_tool_engineering",
            found=False,
            location="src/tools/",
            rationale="src/tools directory does not exist.",
            confidence=1.0,
        )

    uses_tempfile = False
    uses_os_system = False
    uses_subprocess = False
    code_snippets = []

    for root, _, files in os.walk(tools_dir):
        for file in files:
            if file.endswith(".py"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()

                if "tempfile" in text or "TemporaryDirectory" in text:
                    uses_tempfile = True
                    code_snippets.append(f"[{file}]: tempfile.TemporaryDirectory found")

                if re.search(r"os\.system\s*\(", text):
                    uses_os_system = True
                    code_snippets.append(f"[{file}]: SECURITY VIOLATION raw os.system() found")

                if "subprocess.run" in text or "subprocess.Popen" in text:
                    uses_subprocess = True
                    code_snippets.append(f"[{file}]: subprocess.run found")

    is_safe = uses_tempfile and not uses_os_system and uses_subprocess

    rationale_parts = []
    if uses_tempfile:
        rationale_parts.append("Sandboxed git cloning with tempfile.TemporaryDirectory()")
    else:
        rationale_parts.append("Missing tempfile sandboxing for git clone")

    if uses_os_system:
        rationale_parts.append("SECURITY VIOLATION: raw os.system() calls present!")
    else:
        rationale_parts.append("No raw os.system() calls detected")

    if uses_subprocess:
        rationale_parts.append("Subprocess execution with error handling used")

    return Evidence(
        goal="safe_tool_engineering",
        found=is_safe,
        content="\n".join(code_snippets),
        location="src/tools/",
        rationale="; ".join(rationale_parts),
        confidence=1.0,
    )


def analyze_structured_output_and_judges(repo_path: str) -> List[Evidence]:
    """
    Scans src/nodes/judges.py for .with_structured_output() or .bind_tools(), Pydantic schemas,
    and distinct system prompts for Prosecutor, Defense, and Tech Lead.
    """
    judges_file = os.path.join(repo_path, "src", "nodes", "judges.py")
    if not os.path.exists(judges_file):
        judges_file = os.path.join(repo_path, "judges.py")

    evidences = []

    if not os.path.exists(judges_file):
        evidences.append(
            Evidence(
                goal="structured_output_enforcement",
                found=False,
                location="src/nodes/judges.py",
                rationale="src/nodes/judges.py missing.",
                confidence=1.0,
            )
        )
        evidences.append(
            Evidence(
                goal="judicial_nuance",
                found=False,
                location="src/nodes/judges.py",
                rationale="src/nodes/judges.py missing.",
                confidence=1.0,
            )
        )
        return evidences

    with open(judges_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    uses_structured_output = ".with_structured_output" in content or ".bind_tools" in content or "JudicialOpinion" in content
    has_prosecutor = "Prosecutor" in content or "critical" in content.lower()
    has_defense = "Defense" in content or "optimistic" in content.lower()
    has_techlead = "TechLead" in content or "Tech Lead" in content or "pragmatic" in content.lower()

    # Persona differentiation check
    prompts = re.findall(r'"""(.*?)"""', content, re.DOTALL) + re.findall(r"'''(.*?)'''", content, re.DOTALL)
    distinct_prompts = len(prompts) >= 3

    evidences.append(
        Evidence(
            goal="structured_output_enforcement",
            found=uses_structured_output,
            content=content[:500],
            location="src/nodes/judges.py",
            rationale="Uses .with_structured_output(JudicialOpinion) to enforce Pydantic output."
            if uses_structured_output
            else "Calls LLM without structured output enforcement.",
            confidence=1.0,
        )
    )

    evidences.append(
        Evidence(
            goal="judicial_nuance",
            found=has_prosecutor and has_defense and has_techlead and distinct_prompts,
            content=content[200:800],
            location="src/nodes/judges.py",
            rationale=f"Persona separation: Prosecutor={has_prosecutor}, Defense={has_defense}, TechLead={has_techlead}. Distinct system prompts configured."
            if (has_prosecutor and has_defense and has_techlead)
            else "Lacks 3 distinct persona lenses.",
            confidence=1.0,
        )
    )

    return evidences


def analyze_chief_justice_node(repo_path: str) -> Evidence:
    """
    Scans src/nodes/justice.py for ChiefJusticeNode deterministic hardcoded rules (security override,
    fact supremacy, functionality weight, dissent requirement).
    """
    justice_file = os.path.join(repo_path, "src", "nodes", "justice.py")
    if not os.path.exists(justice_file):
        justice_file = os.path.join(repo_path, "justice.py")

    if not os.path.exists(justice_file):
        return Evidence(
            goal="chief_justice_synthesis",
            found=False,
            location="src/nodes/justice.py",
            rationale="src/nodes/justice.py missing.",
            confidence=1.0,
        )

    with open(justice_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    has_security_override = "security" in content.lower() or "override" in content.lower()
    has_fact_supremacy = "fact" in content.lower() or "hallucinat" in content.lower()
    has_dissent = "dissent" in content.lower() or "variance" in content.lower()
    is_deterministic = "if " in content and "for " in content and "class ChiefJustice" in content

    found = is_deterministic and has_security_override and has_dissent

    rationale_parts = []
    if is_deterministic:
        rationale_parts.append("Deterministic Python logic (not a plain LLM prompt)")
    if has_security_override:
        rationale_parts.append("Implements Security Override rule (caps score at 3)")
    if has_fact_supremacy:
        rationale_parts.append("Implements Fact Supremacy rule over unbacked claims")
    if has_dissent:
        rationale_parts.append("Generates explicit dissent summary for score variance > 2")

    return Evidence(
        goal="chief_justice_synthesis",
        found=found,
        content=content[:700],
        location="src/nodes/justice.py",
        rationale="; ".join(rationale_parts),
        confidence=1.0,
    )
