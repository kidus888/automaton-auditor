# LangSmith Traces / Reasoning-Loop Logs

Every `main.py` run writes `trace_<repo>_<UTC timestamp>.json` here. It contains:

- `reasoning_loop`: for each rubric criterion, the Prosecutor / Defense / TechLead opinions, the
  deterministic rules the Chief Justice applied, who was overruled, the dissent and the final score.
- `node_events`: every node's state update in execution order (ContextBuilder → detectives →
  EvidenceAggregator → judges → ChiefJustice), as streamed from the compiled graph.
- `langsmith_url` / `langsmith_project` / `run_id`: when `LANGCHAIN_TRACING_V2=true` and
  `LANGCHAIN_API_KEY` are set, the run is also traced to LangSmith under that run id, and the
  link is recorded here.

Traces produced without an LLM API key show the deterministic persona lens. Runs with
`OPENAI_API_KEY` / `GEMINI_API_KEY` show the LLM judges' structured `JudicialOpinion`s, including any
retries after malformed output.
