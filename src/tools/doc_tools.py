"""
DocAnalyst and VisionInspector forensic tools (the "Context" and "Multimodal" Detectives).

The report is never dumped whole into an LLM context: ``ingest_pdf`` chunks it by section and
``query_report`` retrieves only the chunks relevant to a question (RAG-lite, BM25 ranking).
"""

import base64
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.state import Evidence, ReportClaims

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    fitz = None


# ---------------------------------------------------------------------------
# RAG-lite ingestion and retrieval
# ---------------------------------------------------------------------------

class DocChunk(BaseModel):
    chunk_id: int
    page: Optional[int] = Field(default=None, description="1-based PDF page (None for Markdown)")
    section: str = Field(description="Nearest preceding heading")
    text: str


_INGEST_CACHE: Dict[Tuple[str, float, int], List[DocChunk]] = {}
_STOPWORDS = set(
    "a an and are as at be by does do for from has have how in into is it its of on or say says "
    "that the their this to was we what when where which with about report".split()
)


def _is_markdown(path: str) -> bool:
    return path.lower().endswith((".md", ".markdown", ".txt"))


def _pdf_blocks(path: str) -> List[Tuple[Optional[int], str, bool]]:
    """(page, text, is_heading) per text block. Headings are blocks set noticeably larger than
    the document's median font size."""
    doc = fitz.open(path)
    raw: List[Tuple[int, str, float]] = []
    for pno, page in enumerate(doc, 1):
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            spans = [s for line in block.get("lines", []) for s in line.get("spans", [])]
            text = " ".join(s["text"] for s in spans).strip()
            if text:
                raw.append((pno, text, max(s["size"] for s in spans)))
    sizes = sorted(s for _, _, s in raw)
    median = sizes[len(sizes) // 2] if sizes else 0.0
    return [(p, t, s >= median * 1.2 and len(t) < 120) for p, t, s in raw]


def _markdown_blocks(path: str) -> List[Tuple[Optional[int], str, bool]]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    blocks: List[Tuple[Optional[int], str, bool]] = []
    in_fence, buf = False, []
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.lstrip().startswith("#"):
            if buf:
                blocks.append((None, "\n".join(buf).strip(), False))
                buf = []
            blocks.append((None, line.lstrip("# ").strip(), True))
        elif not in_fence and not line.strip():
            if buf:
                blocks.append((None, "\n".join(buf).strip(), False))
                buf = []
        else:
            buf.append(line)
    if buf:
        blocks.append((None, "\n".join(buf).strip(), False))
    return [b for b in blocks if b[1]]


def ingest_pdf(path: str, max_chars: int = 900) -> List[DocChunk]:
    """
    Parses the report (PDF via PyMuPDF; Markdown/text read directly) into section-aware chunks
    of at most ~``max_chars`` characters, so callers can retrieve only relevant context.
    """
    if not path or not os.path.isfile(path):
        return []
    key = (os.path.abspath(path), os.path.getmtime(path), max_chars)
    if key in _INGEST_CACHE:
        return _INGEST_CACHE[key]

    if _is_markdown(path) or fitz is None:
        blocks = _markdown_blocks(path)
    else:
        try:
            blocks = _pdf_blocks(path)
        except Exception:
            return []

    chunks: List[DocChunk] = []
    section, buf, buf_page = "Front Matter", [], None

    def flush():
        nonlocal buf, buf_page
        if buf:
            chunks.append(DocChunk(chunk_id=len(chunks), page=buf_page, section=section, text="\n\n".join(buf)))
        buf, buf_page = [], None

    for page, text, is_heading in blocks:
        if is_heading:
            flush()
            section = text
            continue
        if buf and sum(len(b) for b in buf) + len(text) > max_chars:
            flush()
        buf_page = buf_page or page
        buf.append(text)
    flush()
    _INGEST_CACHE[key] = chunks
    return chunks


def _tokens(text: str) -> List[str]:
    text = re.sub(r"fan[\s_]?(in|out)\b", r"fan-\1", text.lower())
    return [t for t in re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", text) if t not in _STOPWORDS]


def query_report(source, question: str, k: int = 3) -> List[Tuple[DocChunk, float]]:
    """
    RAG-lite retrieval: ranks report chunks against ``question`` with BM25 and returns the top
    ``k`` (chunk, score) pairs, e.g. query_report(path, "What does the report say about
    Dialectical Synthesis?"). ``source`` is a report path or a list of DocChunks.
    """
    chunks = ingest_pdf(source) if isinstance(source, str) else source
    if not chunks:
        return []
    docs = [_tokens(c.section + " " + c.text) for c in chunks]
    avgdl = sum(len(d) for d in docs) / len(docs) or 1.0
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    k1, b = 1.5, 0.75
    q = _tokens(question)
    scored = []
    for chunk, doc in zip(chunks, docs):
        tf = Counter(doc)
        score = 0.0
        for term in q:
            if term in tf:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(doc) / avgdl))
        if score > 0:
            scored.append((chunk, round(score, 3)))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def extract_pdf_text(pdf_path: str) -> str:
    """Full report text (kept for callers that need a whole-document regex pass)."""
    return "\n\n".join(c.text for c in ingest_pdf(pdf_path))


# ---------------------------------------------------------------------------
# Theoretical Depth
# ---------------------------------------------------------------------------

CONCEPTS = {
    "Dialectical Synthesis": re.compile(r"dialectic\w*", re.I),
    "Fan-In / Fan-Out": re.compile(r"fan[\s-]?(in|out)\b", re.I),
    "Metacognition": re.compile(r"meta-?cogniti\w*", re.I),
    "State Synchronization": re.compile(r"(state\s+)?synchroni[sz]\w*", re.I),
}
_SUMMARY_SECTION = re.compile(r"summary|abstract|introduction|overview|front matter|tl;?dr", re.I)
_IMPL_ANCHORS = re.compile(
    r"[\w/]+\.py\b|operator\.(add|ior)|add_(conditional_)?edges?|StateGraph|with_structured_output|"
    r"EvidenceAggregator|ChiefJustice|Prosecutor|Defense|TechLead|reducer|Annotated|TypedDict|BaseModel|"
    r"\bnode\b|\bedge\b|parallel|superstep|deterministic",
    re.I,
)


def _sentences_with(text: str, pattern: re.Pattern, limit: int = 2) -> List[str]:
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    out = [" ".join(s.split()) for s in sentences if pattern.search(s)]
    return out[:limit]


def search_theoretical_depth(pdf_path: str) -> Evidence:
    """
    For each orchestration concept, retrieves the most relevant chunks (RAG-lite) and decides
    whether the term is explained in architectural context (tied to concrete nodes, edges,
    reducers or files) or merely dropped as a buzzword / only in the executive summary.
    """
    chunks = ingest_pdf(pdf_path)
    if not chunks:
        return Evidence(goal="theoretical_depth", found=False, location=pdf_path or "missing",
                        rationale="Report missing or no text could be extracted.", confidence=1.0,
                        gaps=["No readable report to analyse"])

    substantive, dropped, absent, captures = [], [], [], []
    for concept, pattern in CONCEPTS.items():
        hits = [c for c in chunks if pattern.search(c.text)]
        if not hits:
            absent.append(concept)
            continue
        ranked = {c.chunk_id: s for c, s in query_report(hits, f"how does the architecture implement {concept}", k=len(hits))}
        hits.sort(key=lambda c: -ranked.get(c.chunk_id, 0.0))
        def explained(c: DocChunk, pattern: re.Pattern = pattern) -> bool:
            # implementation anchors must sit near the mention, not merely somewhere in the chunk
            for m in pattern.finditer(c.text):
                window = c.text[max(0, m.start() - 300): m.end() + 300]
                if len({a.group(0).lower() for a in _IMPL_ANCHORS.finditer(window)}) >= 2:
                    return True
            return False

        deep = [c for c in hits if not _SUMMARY_SECTION.search(c.section) and explained(c)]
        best = (deep or hits)[0]
        where = f"p.{best.page}" if best.page else f"section '{best.section}'"
        for sentence in _sentences_with(best.text, pattern):
            captures.append(f"[{concept} @ {where}] {sentence[:300]}")
        (substantive if deep else dropped).append(concept)

    gaps = [f"'{c}' is never discussed" for c in absent]
    gaps += [f"KEYWORD DROPPING: '{c}' appears without implementation detail (or only in the summary)" for c in dropped]
    rationale = (
        f"{len(substantive)}/{len(CONCEPTS)} concepts explained in architectural context"
        + (f" ({', '.join(substantive)})" if substantive else "")
        + f"; report ingested as {len(chunks)} chunks."
    )
    return Evidence(goal="theoretical_depth", found=len(substantive) >= 3, content="\n".join(captures),
                    location=pdf_path, rationale=rationale, confidence=0.85, gaps=gaps)


# ---------------------------------------------------------------------------
# Host Analysis Accuracy (cross-reference)
# ---------------------------------------------------------------------------

_PATH_RE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:py|json|toml|md|txt|ya?ml|lock|cfg|ini|pdf)|Dockerfile)\b"
)
_FEATURE_PATTERNS = {
    "parallel_detectives": (re.compile(r"detective|repoinvestigator|docanalyst|visioninspector", re.I),
                            re.compile(r"parallel|fan-?out|concurrent", re.I)),
    "parallel_judges": (re.compile(r"judge|prosecutor", re.I), re.compile(r"parallel|fan-?out|concurrent", re.I)),
    "structured_output": (re.compile(r"with_structured_output|structured output|bind_tools", re.I), None),
    "sandboxed_clone": (re.compile(r"tempfile|sandbox", re.I), None),
    "deterministic_synthesis": (re.compile(r"chief\s*justice|synthesis", re.I),
                                re.compile(r"deterministic|hardcoded|hard-coded|rule", re.I)),
    "state_reducers": (re.compile(r"operator\.(add|ior)|reducer", re.I), None),
}
# feature -> (evidence goal, gap fragment that contradicts the claim; None = contradicted when not found)
_FEATURE_CHECKS = {
    "parallel_detectives": ("graph_orchestration", "Detectives do not fan out"),
    "parallel_judges": ("graph_orchestration", "Judges (Prosecutor/Defense/TechLead) do not fan out"),
    "structured_output": ("structured_output_enforcement", None),
    "sandboxed_clone": ("safe_tool_engineering", "No tempfile"),
    "deterministic_synthesis": ("chief_justice_synthesis", None),
    "state_reducers": ("state_management_rigor", "no Annotated reducer"),
}


def extract_report_claims(pdf_path: str) -> ReportClaims:
    """Extracts the file paths and feature claims the report makes, for cross-referencing."""
    text = extract_pdf_text(pdf_path)
    text_no_urls = re.sub(r"https?://\S+", " ", text)
    paths = sorted({m.group(1).rstrip(".") for m in _PATH_RE.finditer(text_no_urls)})
    features: Dict[str, str] = {}
    for sentence in re.split(r"(?<=[.!?])\s+|\n{2,}", text):
        flat = " ".join(sentence.split())
        for key, (p1, p2) in _FEATURE_PATTERNS.items():
            if key not in features and p1.search(flat) and (p2 is None or p2.search(flat)):
                features[key] = flat[:300]
    return ReportClaims(source=pdf_path, cited_paths=paths, feature_claims=features)


def _resolve_path(claimed: str, manifest: List[str], manifest_set: set) -> Optional[str]:
    p = claimed.replace("\\", "/").lstrip("./")
    if p in manifest_set:
        return p
    suffix = "/" + p
    matches = [m for m in manifest if m.endswith(suffix)]
    return matches[0] if len(matches) >= 1 else None


def cross_reference_report_accuracy(
    claims: Optional[ReportClaims], manifest: List[str], repo_evidence: Dict[str, Evidence]
) -> Evidence:
    """
    Verifies every path the report cites against RepoInvestigator's file manifest and every
    feature claim against the code detective's evidence. Produces Verified vs Hallucinated lists.
    """
    location = claims.source if claims else "missing"
    if claims is None:
        return Evidence(goal="report_accuracy", found=False, location=location,
                        rationale="No report claims were extracted (report missing).", confidence=1.0,
                        gaps=["No report available to cross-reference"])
    if not manifest:
        return Evidence(goal="report_accuracy", found=False, location=location,
                        rationale="RepoInvestigator produced no file manifest; claims cannot be verified.",
                        confidence=0.3, gaps=["Repository unavailable: cited paths could not be verified"])

    manifest_set = set(manifest)
    verified, hallucinated = [], []
    for p in claims.cited_paths:
        resolved = _resolve_path(p, manifest, manifest_set)
        if resolved:
            verified.append(p if resolved == p else f"{p} -> {resolved}")
        else:
            hallucinated.append(p)

    contradicted = []
    for feature, sentence in claims.feature_claims.items():
        goal, fragment = _FEATURE_CHECKS[feature]
        ev = repo_evidence.get(goal)
        if ev is None:
            continue
        if (fragment is None and not ev.found) or (fragment and any(fragment in g for g in ev.gaps)):
            contradicted.append(f"{feature}: report says \"{sentence[:160]}\" but {goal} evidence contradicts it")

    gaps = [f"HALLUCINATED PATH: {p}" for p in hallucinated] + [f"FEATURE CLAIM CONTRADICTED: {c}" for c in contradicted]
    content = (
        f"Verified Paths ({len(verified)}): {', '.join(verified) or '-'}\n"
        f"Hallucinated Paths ({len(hallucinated)}): {', '.join(hallucinated) or '-'}\n"
        f"Feature claims checked ({len(claims.feature_claims)}): "
        f"{', '.join(claims.feature_claims) or '-'}; contradicted: {len(contradicted)}"
    )
    rationale = (
        f"Cross-referenced {len(claims.cited_paths)} cited paths against {len(manifest)} repository files: "
        f"{len(verified)} verified, {len(hallucinated)} hallucinated; "
        f"{len(claims.feature_claims) - len(contradicted)}/{len(claims.feature_claims)} feature claims match code evidence."
    )
    return Evidence(goal="report_accuracy", found=bool(verified) and not hallucinated and not contradicted,
                    content=content, location=location, rationale=rationale, confidence=1.0, gaps=gaps)


# ---------------------------------------------------------------------------
# VisionInspector
# ---------------------------------------------------------------------------

@dataclass
class ExtractedImage:
    data: bytes
    ext: str
    page: int
    width: int
    height: int


def extract_images_from_pdf(path: str, min_side: int = 150) -> List[ExtractedImage]:
    """Extracts embedded images (skipping icons smaller than ``min_side`` px) from a PDF, or
    local images referenced by a Markdown report."""
    if not path or not os.path.isfile(path):
        return []
    images: List[ExtractedImage] = []
    if _is_markdown(path):
        base = os.path.dirname(os.path.abspath(path))
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", f.read())
        for ref in refs:
            full = os.path.abspath(os.path.join(base, ref))
            if full.startswith(base) and os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as img:
                    images.append(ExtractedImage(img.read(), ref.rsplit(".", 1)[-1].lower(), 0, 0, 0))
        return images
    if fitz is None:
        return []
    try:
        doc = fitz.open(path)
        for pno, page in enumerate(doc, 1):
            for img in page.get_images(full=True):
                try:
                    base_image = doc.extract_image(img[0])
                except Exception:
                    continue
                w, h = base_image.get("width", 0), base_image.get("height", 0)
                if min(w, h) >= min_side:
                    images.append(ExtractedImage(base_image["image"], base_image.get("ext", "png"), pno, w, h))
    except Exception:
        return []
    return images


VISION_QUESTION = (
    "Is this a StateGraph diagram or a generic box diagram? Classify it as one of: "
    "'LangGraph StateGraph diagram', 'sequence diagram', 'generic flowchart', 'not an architecture diagram'. "
    "Then state whether it explicitly visualizes the parallel split START -> [Detectives in parallel] -> "
    "Evidence Aggregation -> [Prosecutor || Defense || TechLead in parallel] -> Chief Justice Synthesis -> END, "
    "using the words 'parallel: yes' or 'parallel: no', and describe the structural flow in one sentence."
)


def classify_diagram_with_vision(image_bytes: bytes, ext: str = "png") -> Optional[str]:
    """
    Sends one diagram to a multimodal LLM (GPT-4o or Gemini). Returns None when no vision key
    is configured; running vision is optional per the spec, so callers fall back gracefully.
    """
    has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    if not has_openai_key and not has_gemini_key:
        return None
    try:
        from langchain_core.messages import HumanMessage

        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext.lower(), "png")
        image_url = f"data:image/{mime};base64,{base64.b64encode(image_bytes).decode('utf-8')}"
        if has_openai_key:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model="gpt-4o", temperature=0.0)
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
        message = HumanMessage(content=[
            {"type": "text", "text": VISION_QUESTION},
            {"type": "image_url", "image_url": {"url": image_url}},
        ])
        return str(llm.invoke([message]).content)
    except Exception as e:
        return f"[Vision call failed: {e}]"


_DIAGRAM_ROLE = {
    "detective": re.compile(r"repo|doc|vision|detective|investigat|analy[sz]|inspect", re.I),
    "judge": re.compile(r"prosecut|defen[cs]e|tech\s*_?lead|judge", re.I),
    "aggregator": re.compile(r"aggregat|sync|fan.?in|collect", re.I),
    "chief": re.compile(r"chief|justice|synth|verdict", re.I),
}


def _parse_mermaid(block: str) -> List[Tuple[str, str]]:
    """Edges (source label, target label) from a mermaid flowchart."""
    labels: Dict[str, str] = {}
    for node_id, label in re.findall(r"([A-Za-z_]\w*)\s*[\[\(\{]+\s*\"?([^\]\)\}\"]+)\"?\s*[\]\)\}]+", block):
        labels.setdefault(node_id, label.strip())
    edges = []
    for line in block.splitlines():
        line = re.sub(r"\|[^|]*\|", "", line)
        line = re.sub(r"[\[\(\{]+[^\]\)\}]*[\]\)\}]+", "", line)
        parts = re.split(r"\s*(?:-{2,}|={2,}|-\.+-?)>?\s*", line.strip())
        if len(parts) < 2:
            continue
        groups = [[labels.get(n.strip(), n.strip()) for n in p.split("&") if n.strip()] for p in parts]
        for left, right in zip(groups, groups[1:]):
            edges.extend((a, b) for a in left for b in right)
    return edges


def analyze_text_diagrams(text: str) -> Tuple[Optional[bool], List[str]]:
    """Structurally analyses mermaid diagrams (and recognises ASCII-art diagrams) in the report.
    Returns (shows_parallel_flow or None if no diagram, notes)."""
    notes: List[str] = []
    verdict: Optional[bool] = None
    for lang, block in re.findall(r"```(\w*)\n(.*?)```", text, re.S):
        if lang.lower() == "mermaid" or re.search(r"(flowchart|graph)\s+(TD|LR|TB|BT|RL)", block):
            edges = _parse_mermaid(block)
            outs: Dict[str, set] = {}
            for a, b in edges:
                outs.setdefault(a, set()).add(b)

            def fans(role: str, outs: Dict[str, set] = outs) -> bool:
                return any(sum(bool(_DIAGRAM_ROLE[role].search(t)) for t in ts) >= 2 for ts in outs.values())

            det, jud = fans("detective"), fans("judge")
            notes.append(f"Mermaid flowchart with {len(edges)} edges: detective fan-out={det}, judge fan-out={jud}")
            verdict = bool(verdict) or (det and jud)
        elif re.search(r"[┌└│▼─]", block) and all(_DIAGRAM_ROLE[r].search(block) for r in ("detective", "judge", "chief")):
            # ASCII art is recorded but cannot be verified structurally, so it leaves the verdict alone
            notes.append("ASCII-art architecture diagram names Detectives, Judges and Chief Justice (parallelism not machine-verifiable)")
    return verdict, notes


def extract_and_analyze_diagrams(pdf_path: str, max_vision_calls: int = 3) -> Evidence:
    """
    Forensic Protocol (Flow Analysis): classifies architecture diagrams. Uses a multimodal LLM on
    extracted images when a vision key is configured, and structurally parses mermaid diagrams
    in the report text. Reports without any diagram fail the criterion.
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        return Evidence(goal="swarm_visual", found=False, location=pdf_path or "missing",
                        rationale="Report missing.", confidence=1.0, gaps=["No report to extract diagrams from"])

    images = extract_images_from_pdf(pdf_path)
    vision_results = []
    for img in images[:max_vision_calls]:
        result = classify_diagram_with_vision(img.data, img.ext)
        if result and not result.startswith("[Vision call failed"):
            vision_results.append(f"(page {img.page}) {result}")

    text_verdict, text_notes = analyze_text_diagrams(extract_pdf_text(pdf_path))
    gaps: List[str] = []
    notes = [f"{len(images)} embedded image(s) extracted"] + text_notes

    if vision_results:
        combined = " ".join(vision_results).lower()
        vision_ok = "stategraph" in combined and "parallel: yes" in combined
        notes.append(f"Vision model: {' | '.join(vision_results)[:600]}")
        found, confidence = vision_ok or bool(text_verdict), 0.9
        if not vision_ok:
            gaps.append("Vision model did not confirm a StateGraph diagram with parallel Detective/Judge branches")
    elif text_verdict is not None:
        found, confidence = text_verdict, 0.8
        if not text_verdict:
            gaps.append("MISLEADING ARCHITECTURE VISUAL: diagram does not show parallel Detective and Judge fan-out")
    elif text_notes:
        found, confidence = True, 0.5
        gaps.append("Diagram is ASCII art: parallel branches could not be verified structurally")
    elif images:
        found, confidence = False, 0.4
        gaps.append("Images found but no vision model configured to classify them (set OPENAI_API_KEY or GEMINI_API_KEY)")
    else:
        found, confidence = False, 1.0
        gaps.append("No architecture diagram (image or mermaid) found in the report")

    return Evidence(goal="swarm_visual", found=found,
                    content="\n".join(notes), location=pdf_path,
                    rationale="; ".join(notes + gaps), confidence=confidence, gaps=gaps)
