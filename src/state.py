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
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    gaps: List[str] = Field(
        default_factory=list,
        description="Specific unmet sub-requirements the detective verified as missing (facts, not opinions)",
    )


# --- DocAnalyst intermediate output (cross-referenced at fan-in) ---
class ReportClaims(BaseModel):
    """Claims extracted from the PDF report, verified against RepoInvestigator data at fan-in."""
    source: str = Field(description="Path of the report the claims were extracted from")
    cited_paths: List[str] = Field(default_factory=list, description="File paths the report says exist")
    feature_claims: Dict[str, str] = Field(
        default_factory=dict,
        description="Feature key (e.g. 'parallel_judges') -> sentence in the report that claims it",
    )


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
    rules_applied: List[str] = Field(default_factory=list, description="Deterministic synthesis rules that fired")
    overruled_judges: List[str] = Field(default_factory=list, description="Judges whose score was set aside, and why")
    dissent_summary: Optional[str] = Field(
        default=None,
        description="Why the Prosecutor and Defense disagreed and which side was overruled",
    )
    remediation: str = Field(description="Specific file-level remediation instructions for improvement")


class AuditReport(BaseModel):
    repo_url: str = Field(description="Target GitHub Repository URL")
    executive_summary: str = Field(description="Overall verdict summary and synthesis narrative")
    overall_score: float = Field(description="Average final score across all rubric dimensions (after caps)")
    criteria: List[CriterionResult] = Field(description="Criterion-by-criterion breakdown of results")
    remediation_plan: str = Field(description="Aggregated remediation plan sorted by priority")


# --- Graph State ---
class AgentState(TypedDict):
    repo_url: str
    pdf_path: str
    # Loaded by ContextBuilder from rubric/week2_rubric.json (the "Constitution")
    rubric_dimensions: List[Dict]
    synthesis_rules: Dict[str, str]
    # Reducer operator.ior ensures dictionary updates merge rather than overwrite
    evidences: Annotated[Dict[str, List[Evidence]], operator.ior]
    # Written only by RepoInvestigator / DocAnalyst; cross-referenced by EvidenceAggregator at fan-in
    repo_manifest: List[str]
    report_claims: Optional[ReportClaims]
    # Reducer operator.add ensures parallel judge outputs accumulate into a single list
    opinions: Annotated[List[JudicialOpinion], operator.add]
    # Node-failure log: any node may append, so it also needs an additive reducer
    errors: Annotated[List[str], operator.add]
    final_report: Optional[AuditReport]
    markdown_output: Optional[str]
