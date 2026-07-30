import operator
from typing import Annotated, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

# --- Detective Output ---
class Evidence(BaseModel):
    goal: str = Field(description="The forensic audit goal or check being performed")
    found: bool = Field(description="Whether the target artifact or evidence exists")
    content: Optional[str] = Field(default=None, description="Extracted code snippet, git history line, or doc sentence")
    location: str = Field(description="File path, line range, commit hash, or section name")
    rationale: str = Field(description="Rationale for confidence on the evidence found for this goal")
    confidence: float = Field(default=1.0, description="Confidence score between 0.0 and 1.0")


# --- Judge Output ---
class JudicialOpinion(BaseModel):
    judge: Literal["Prosecutor", "Defense", "TechLead"] = Field(description="The persona submitting the opinion")
    criterion_id: str = Field(description="ID of the rubric dimension being evaluated")
    score: int = Field(ge=1, le=5, description="Grade from 1 (poor/vibe) to 5 (master thinker)")
    argument: str = Field(description="Detailed reasoning for the assigned score based on persona lens")
    cited_evidence: List[str] = Field(default_factory=list, description="Specific evidence locations or content cited")


# --- Chief Justice Output ---
class CriterionResult(BaseModel):
    dimension_id: str = Field(description="ID of the rubric dimension")
    dimension_name: str = Field(description="Human readable rubric dimension name")
    final_score: int = Field(ge=1, le=5, description="Synthesized final score (1-5)")
    judge_opinions: List[JudicialOpinion] = Field(description="Opinions from Prosecutor, Defense, and Tech Lead")
    dissent_summary: Optional[str] = Field(
        default=None,
        description="Required when judge score variance > 2, explaining conflict resolution rationale",
    )
    remediation: str = Field(description="Specific file-level remediation instructions for improvement")


class AuditReport(BaseModel):
    repo_url: str = Field(description="Target GitHub Repository URL")
    executive_summary: str = Field(description="Overall verdict summary and synthesis narrative")
    overall_score: float = Field(description="Average final score across all 10 rubric dimensions")
    criteria: List[CriterionResult] = Field(description="Criterion-by-criterion breakdown of results")
    remediation_plan: str = Field(description="Aggregated remediation plan sorted by priority")


# --- Graph State ---
class AgentState(TypedDict):
    repo_url: str
    pdf_path: str
    rubric_dimensions: List[Dict]
    # Reducer operator.ior ensures dictionary updates merge rather than overwrite
    evidences: Annotated[Dict[str, List[Evidence]], operator.ior]
    # Reducer operator.add ensures parallel judge outputs accumulate into a single list
    opinions: Annotated[List[JudicialOpinion], operator.add]
    final_report: Optional[AuditReport]
    markdown_output: Optional[str]
