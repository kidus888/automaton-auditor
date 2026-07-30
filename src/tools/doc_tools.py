import base64
import os
import re
from typing import Dict, List, Optional, Tuple
from src.state import Evidence

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


def extract_pdf_text(pdf_path: str) -> str:
    """
    Extracts text content from all pages of a PDF file using PyMuPDF (fitz) or fallback.
    """
    if not os.path.exists(pdf_path):
        return ""

    if fitz is not None:
        try:
            doc = fitz.open(pdf_path)
            full_text = []
            for page in doc:
                full_text.append(page.get_text())
            return "\n".join(full_text)
        except Exception:
            pass

    # Fallback basic string read if PDF is plain text / markdown disguised as pdf in testing
    try:
        with open(pdf_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def search_theoretical_depth(pdf_path: str) -> Evidence:
    """
    Searches the PDF report for core architectural terms:
    - 'Dialectical Synthesis'
    - 'Fan-In / Fan-Out' or 'Fan-In'
    - 'Metacognition'
    - 'State Synchronization'
    Determines if they are explained substantively or just keyword dropped.
    """
    text = extract_pdf_text(pdf_path)
    if not text:
        return Evidence(
            goal="theoretical_depth",
            found=False,
            location=pdf_path,
            rationale="PDF report file does not exist or text could not be extracted.",
            confidence=1.0,
        )

    keywords = ["Dialectical Synthesis", "Fan-In", "Fan-Out", "Metacognition", "State Synchronization"]
    found_keywords = []
    substantive_keywords = []
    snippets = []

    text_lower = text.lower()
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in text_lower:
            found_keywords.append(kw)
            # Check context window around keyword for substantive words (how, node, graph, parallel, reducer, judge)
            idx = text_lower.find(kw_lower)
            snippet = text[max(0, idx - 100) : min(len(text), idx + 200)].strip()
            snippets.append(f"[{kw}]: ...{snippet}...")

            substantive_indicators = ["how", "node", "graph", "parallel", "reducer", "state", "prosecutor", "defense", "persona"]
            if any(ind in snippet.lower() for ind in substantive_indicators):
                substantive_keywords.append(kw)

    is_substantive = len(substantive_keywords) >= 2

    rationale = (
        f"Found {len(found_keywords)}/{len(keywords)} concepts ({', '.join(found_keywords)}). "
        f"{len(substantive_keywords)} appear with substantive architectural explanation."
    )

    return Evidence(
        goal="theoretical_depth",
        found=is_substantive,
        content="\n".join(snippets[:4]),
        location=pdf_path,
        rationale=rationale,
        confidence=1.0,
    )


def cross_reference_report_accuracy(pdf_path: str, repo_path: str) -> Evidence:
    """
    Extracts file paths mentioned in the PDF report (e.g., 'src/state.py', 'src/nodes/judges.py')
    and cross-references each path against the actual repository to detect hallucinated paths.
    """
    text = extract_pdf_text(pdf_path)
    if not text:
        return Evidence(
            goal="report_accuracy",
            found=False,
            location=pdf_path,
            rationale="PDF text empty or unavailable for cross-referencing.",
            confidence=1.0,
        )

    # Regex to find potential file paths (e.g. src/nodes/judges.py, src/state.py, etc.)
    path_matches = re.findall(r"\b(?:src/|reports/|tools/|nodes/)?\w+[\w\-/]*\.(?:py|json|toml|md|pdf)\b", text)
    unique_paths = list(set(path_matches))

    verified_paths = []
    hallucinated_paths = []

    for p in unique_paths:
        possible_full_paths = [
            os.path.join(repo_path, p),
            os.path.join(repo_path, "src", p) if not p.startswith("src/") else os.path.join(repo_path, p),
        ]

        if any(os.path.exists(fp) for fp in possible_full_paths):
            verified_paths.append(p)
        else:
            hallucinated_paths.append(p)

    accuracy_pass = len(hallucinated_paths) == 0 and len(verified_paths) > 0

    content = f"Verified Paths ({len(verified_paths)}): {', '.join(verified_paths)}\nHallucinated Paths ({len(hallucinated_paths)}): {', '.join(hallucinated_paths)}"

    rationale = (
        f"Cross-reference check: {len(verified_paths)} verified paths, {len(hallucinated_paths)} hallucinated paths cited in report."
    )

    return Evidence(
        goal="report_accuracy",
        found=accuracy_pass,
        content=content,
        location=pdf_path,
        rationale=rationale,
        confidence=1.0,
    )


def extract_images_from_pdf(pdf_path: str) -> List[bytes]:
    """
    Extracts raw image bytes for every embedded image across all pages of the PDF,
    using PyMuPDF (fitz). Returns an empty list if the file is missing, fitz is
    unavailable, or no images are embedded.
    """
    if not pdf_path or not os.path.exists(pdf_path) or fitz is None:
        return []

    images: List[bytes] = []
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    images.append(base_image["image"])
                except Exception:
                    continue
    except Exception:
        return []

    return images


def classify_diagram_with_vision(image_bytes: bytes) -> Optional[str]:
    """
    Sends an extracted diagram image to a multimodal LLM (GPT-4o or Gemini) and asks it to
    classify the diagram type and describe whether it visualizes the parallel Detective/Judge
    fan-out/fan-in flow, per Forensic Protocol A (Flow Analysis).

    Per the Week 2 spec, VisionInspector's execution is optional -- if no vision-capable API
    key is configured, or the call fails, this returns None and callers fall back to the
    text-based heuristic in extract_and_analyze_diagrams.
    """
    has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    has_gemini_key = bool(os.environ.get("GEMINI_API_KEY"))
    if not has_openai_key and not has_gemini_key:
        return None

    question = (
        "Classify this diagram: is it an accurate LangGraph StateGraph diagram, a sequence diagram, "
        "or a generic flowchart of boxes? Does it explicitly visualize the parallel split: "
        "START -> [Detectives in parallel] -> Evidence Aggregation -> "
        "[Prosecutor || Defense || TechLead in parallel] -> Chief Justice Synthesis -> END? "
        "Answer with a short classification label, then one sentence describing the structural flow."
    )

    try:
        from langchain_core.messages import HumanMessage

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        image_url = f"data:image/png;base64,{image_b64}"

        if has_openai_key:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model="gpt-4o", temperature=0.0)
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)

        message = HumanMessage(
            content=[
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        )
        response = llm.invoke([message])
        return str(response.content)
    except Exception as e:
        return f"[Vision call failed: {str(e)}]"


def extract_and_analyze_diagrams(pdf_path: str, max_vision_calls: int = 3) -> Evidence:
    """
    Extracts image artifacts from the PDF report and classifies architecture diagram flow.
    Prefers multimodal vision classification (Forensic Protocol A) when a vision-capable
    API key is configured; otherwise falls back to a text-proximity heuristic.
    """
    if not os.path.exists(pdf_path):
        return Evidence(
            goal="swarm_visual",
            found=False,
            location=pdf_path,
            rationale="PDF report missing.",
            confidence=1.0,
        )

    images = extract_images_from_pdf(pdf_path)
    image_count = len(images)

    vision_results: List[str] = []
    for image_bytes in images[:max_vision_calls]:
        result = classify_diagram_with_vision(image_bytes)
        if result and not result.startswith("[Vision call failed"):
            vision_results.append(result)

    text = extract_pdf_text(pdf_path).lower()
    has_diagram_mention = "stategraph" in text or "diagram" in text or "architecture" in text or "flow" in text
    has_parallel_mention = "parallel" in text or "fan-out" in text or "fan-in" in text

    if vision_results:
        combined = " ".join(vision_results).lower()
        is_valid_visual = "stategraph" in combined and ("parallel" in combined or "fan-out" in combined or "fan-in" in combined)
        rationale = (
            f"VisionInspector multimodal analysis of {len(vision_results)} extracted diagram(s): "
            f"{' | '.join(vision_results)[:500]}"
        )
        confidence = 0.95
    else:
        is_valid_visual = (image_count > 0 or has_diagram_mention) and has_parallel_mention
        rationale = (
            f"Diagram Analysis (heuristic fallback -- no vision-capable API key configured or no "
            f"images extracted): {image_count} image(s) found. "
            + (
                "Diagram flow explicitly mentions parallel fan-out/fan-in architecture."
                if is_valid_visual
                else "Lacks visual representation of parallel fan-out/fan-in flow."
            )
        )
        confidence = 0.6

    return Evidence(
        goal="swarm_visual",
        found=is_valid_visual,
        content=f"Extracted Images Count: {image_count}\n" + "\n".join(vision_results),
        location=pdf_path,
        rationale=rationale,
        confidence=confidence,
    )
