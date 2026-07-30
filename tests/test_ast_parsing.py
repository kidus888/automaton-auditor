import os
import tempfile
import pytest
from src.tools.repo_tools import (
    analyze_state_management,
    analyze_graph_orchestration,
    analyze_safe_tool_engineering,
)


def test_analyze_state_management_rigor():
    with tempfile.TemporaryDirectory() as tmpdir:
        src_dir = os.path.join(tmpdir, "src")
        os.makedirs(src_dir, exist_ok=True)
        state_file = os.path.join(src_dir, "state.py")

        code = """
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
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(code)

        evidence = analyze_state_management(tmpdir)
        assert evidence.found is True
        assert "BaseModel" in evidence.rationale or "TypedDict" in evidence.rationale
        assert "operator.add" in evidence.rationale or "operator.ior" in evidence.rationale


def test_analyze_safe_tool_engineering_security_violation():
    with tempfile.TemporaryDirectory() as tmpdir:
        tools_dir = os.path.join(tmpdir, "src", "tools")
        os.makedirs(tools_dir, exist_ok=True)
        bad_tool = os.path.join(tools_dir, "unsafe.py")

        unsafe_code = """
import os
def clone_repo(url):
    os.system("git clone " + url)  # Security violation!
"""
        with open(bad_tool, "w", encoding="utf-8") as f:
            f.write(unsafe_code)

        evidence = analyze_safe_tool_engineering(tmpdir)
        assert evidence.found is False
        assert "SECURITY VIOLATION" in evidence.rationale
