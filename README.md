# The Automaton Auditor 🏛️🤖

> **Orchestrating Deep LangGraph Swarms for Autonomous Governance**  
> FDE Challenge Week 2 - Digital Courtroom Architecture

The **Automaton Auditor** is a production-grade, multi-agent AI governance system designed to evaluate code repositories and architectural PDF reports against strict forensic standards. Rather than relying on simple single-prompt wrappers, the system operates as a **Digital Courtroom** utilizing a hierarchical LangGraph state graph.

---

## 🏗️ System Architecture

The architecture enforces a double parallel fan-out/fan-in flow across three distinct operational layers:

```
                  ┌───────────────────────┐
                  │         START         │
                  └───────────┬───────────┘
                              │ (Parallel Fan-Out)
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ RepoInvestigator │ │   DocAnalyst     │ │ VisionInspector  │
│ (Code & AST)     │ │ (PDF & RAG-Lite) │ │ (Diagram Visual) │
└────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼ (Fan-In Sync)
                   ┌─────────────────────┐
                   │  EvidenceAggregator │
                   └──────────┬──────────┘
                              │ (Parallel Fan-Out)
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│    Prosecutor    │ │ Defense Attorney │ │    Tech Lead     │
│ (Critical Lens)  │ │ (Optimistic Lens)│ │ (Pragmatic Lens) │
└────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼ (Fan-In Sync)
                   ┌─────────────────────┐
                   │  ChiefJusticeNode   │
                   │ (Synthesis Engine)  │
                   └──────────┬──────────┘
                              ▼
                   ┌─────────────────────┐
                   │      END (MD)       │
                   └─────────────────────┘
```

---

## 📜 Forensic Rubric Dimensions (10 Criteria)

1. `git_forensic_analysis`: Evaluates git commit progression (`>3` commits, atomic history vs bulk upload).
2. `state_management_rigor`: Checks Pydantic `BaseModel` / `TypedDict` schemas and `Annotated` reducers (`operator.add`, `operator.ior`).
3. `graph_orchestration`: AST parsing of `StateGraph` wiring for parallel fan-out/fan-in and conditional edges.
4. `safe_tool_engineering`: Verifies `tempfile.TemporaryDirectory()` sandboxing and flags raw `os.system()` calls.
5. `structured_output_enforcement`: Verifies `.with_structured_output(JudicialOpinion)` in Judge nodes.
6. `judicial_nuance`: Verifies distinct, conflicting system prompts across Prosecutor, Defense, and Tech Lead personas.
7. `chief_justice_synthesis`: Deterministic Python decision engine implementing Security Override, Fact Supremacy, Functionality Weight, and Dissent Summaries.
8. `theoretical_depth`: RAG-lite scan of PDF report for key concepts ("Dialectical Synthesis", "Fan-In/Fan-Out", "Metacognition").
9. `report_accuracy`: Cross-references file path citations in PDF against actual repo files (Verified vs Hallucinated paths).
10. `swarm_visual`: Inspects architecture diagrams for visual representation of parallel branches.

---

## ⚡ Quickstart & Usage

### 1. Environment Setup

Manage dependencies via `uv` or standard Python `venv`:

```bash
# Using uv (Recommended)
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e .

# Copy environment variables
cp .env.example .env
```

### 2. Configure API Keys

Set your preferred LLM provider credentials in `.env`:
```env
GEMINI_API_KEY="your-gemini-key"
# OR
OPENAI_API_KEY="your-openai-key"

# Optional LangSmith Observability
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY="your-langsmith-key"
```

### 3. Run the Auditor

Execute against any target repository URL or local folder and PDF report:

```bash
python main.py --repo https://github.com/user/target-repo --pdf path/to/report.pdf
```

The rendered audit report will be generated at `audit/report_onself_generated/audit_report.md`.

---

## 🧪 Running Tests

Execute the automated test suite with pytest:

```bash
pytest tests/ -v
```

---

## 📄 License
MIT License
