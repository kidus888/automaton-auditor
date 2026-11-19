import os
import tempfile

from src.tools.repo_tools import (
    analyze_chief_justice_node,
    analyze_judicial_nuance,
    analyze_safe_tool_engineering,
    analyze_state_management,
    analyze_structured_output,
    clone_repo_sandboxed,
    validate_repo_url,
)


def _write(root, rel, code):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


STATE = """
import operator
from typing import Annotated, Dict, List
from pydantic import BaseModel
from typing_extensions import TypedDict

class Evidence(BaseModel):
    goal: str

class JudicialOpinion(BaseModel):
    score: int

class AgentState(TypedDict):
    evidences: Annotated[Dict[str, List[Evidence]], operator.ior]
    opinions: Annotated[List[JudicialOpinion], operator.add]
"""


def test_analyze_state_management_rigor():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/state.py", STATE)
        evidence = analyze_state_management(tmpdir)
    assert evidence.found is True
    assert "operator.ior" in evidence.rationale and "operator.add" in evidence.rationale
    assert "class AgentState" in evidence.content


def test_state_without_reducers_or_pydantic_is_flagged():
    code = STATE.replace("(BaseModel)", "").replace("Annotated[Dict[str, List[Evidence]], operator.ior]", "Dict[str, List[Evidence]]")
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/state.py", code)
        evidence = analyze_state_management(tmpdir)
    assert evidence.found is False
    assert any("no Annotated reducer" in g for g in evidence.gaps)
    assert any("not a Pydantic BaseModel" in g for g in evidence.gaps)


def test_analyze_safe_tool_engineering_security_violation():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/tools/unsafe.py", 'import os\ndef clone_repo(url):\n    os.system("git clone " + url)\n')
        evidence = analyze_safe_tool_engineering(tmpdir)
    assert evidence.found is False
    assert "SECURITY VIOLATION" in evidence.rationale
    assert any("unsanitized dynamic input" in g for g in evidence.gaps)


def test_shell_true_via_alias_is_a_violation():
    code = "from subprocess import run as sh\ndef clone(url):\n    sh(f'git clone {url}', shell=True)\n"
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/tools/t.py", code)
        evidence = analyze_safe_tool_engineering(tmpdir)
    assert any("shell=True" in g for g in evidence.gaps)


def test_self_repo_clone_function_is_captured():
    evidence = analyze_safe_tool_engineering(os.getcwd())
    assert evidence.found is True, evidence.gaps
    assert evidence.location.endswith("clone_repo_sandboxed")
    assert "TemporaryDirectory" in evidence.content


def test_validate_repo_url_blocks_injection():
    assert validate_repo_url("https://github.com/user/repo.git") is None
    assert validate_repo_url("git@github.com:user/repo.git") is None
    for bad in ("--upload-pack=touch /tmp/pwned", "ext::sh -c id", "file:///etc", "https://x.com/a b", ""):
        assert validate_repo_url(bad) is not None, bad
    path, tmp, err = clone_repo_sandboxed("--upload-pack=touch /tmp/pwned")
    assert path is None and tmp is None and "injection" in err


def test_structured_output_without_retry_is_gap():
    code = "def judge(llm, msgs):\n    return llm.with_structured_output(JudicialOpinion).invoke(msgs)\n"
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/nodes/judges.py", code)
        evidence = analyze_structured_output(tmpdir)
    assert evidence.found is True
    assert any("No retry" in g for g in evidence.gaps)


def test_persona_collusion_detected():
    prompt = "You are a judge. Score the evidence from 1 to 5 fairly and cite locations. Be accurate."
    code = (
        f"PROSECUTOR_PROMPT = 'Prosecutor: {prompt}'\n"
        f"DEFENSE_PROMPT = 'Defense: {prompt}'\n"
        f"TECHLEAD_PROMPT = 'TechLead: {prompt}'\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/nodes/judges.py", code)
        evidence = analyze_judicial_nuance(tmpdir)
    assert evidence.found is False
    assert any("PERSONA COLLUSION" in g for g in evidence.gaps)


def test_llm_averaging_chief_justice_is_flagged():
    code = (
        "from langchain_openai import ChatOpenAI\n"
        "def chief_justice(state):\n"
        "    return ChatOpenAI().invoke('average these scores: ' + str(state))\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(tmpdir, "src/nodes/justice.py", code)
        evidence = analyze_chief_justice_node(tmpdir)
    assert evidence.found is False
    assert any("LLM prompt" in g for g in evidence.gaps)


def test_self_chief_justice_is_deterministic():
    evidence = analyze_chief_justice_node(os.getcwd())
    assert evidence.found is True, evidence.gaps
