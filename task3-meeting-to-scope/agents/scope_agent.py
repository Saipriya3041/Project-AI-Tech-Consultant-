import re
from typing import Callable, Any
from agents.model_client import extract_requirements_with_claude


def _requirement_id(sentence: str) -> str:
    normalized = sentence.lower()
    if "suggestion" in normalized and "search" in normalized:
        return "REQ-search-suggestions"
    if "filter" in normalized and "search" in normalized:
        return "REQ-search-filters"
    keywords = {
        "mobile": "mobile",
        "search": "search",
        "filter": "filters",
        "checkout": "checkout",
        "payment": "payment",
        "notification": "notifications",
        "booking": "booking",
        "integration": "integration",
        "integrate": "integration",
        "report": "reporting",
        "delivery": "timeline",
        "deadline": "timeline",
    }
    for keyword, identifier in keywords.items():
        if keyword in normalized:
            return f"REQ-{identifier}"
    normalized_topic = re.sub(r"\bno longer (?:need|want)\b|\b(?:remove|drop)\b", " ", normalized)
    words = re.findall(r"[a-z0-9]+", normalized_topic)
    stable_words = [word for word in words if word not in {"we", "want", "need", "please", "would", "like", "to", "the", "a", "our", "can", "could"}]
    return "REQ-" + "-".join(stable_words[:3]) if stable_words else "REQ-clarification"


def split_transcript(transcript: str, max_chars: int = 12000, overlap_chars: int = 500) -> list[dict[str, Any]]:
    if max_chars < 100 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("Transcript chunk limits must be valid and overlap must be smaller than the chunk size")
    chunks = []
    start = 0
    while start < len(transcript):
        end = min(start + max_chars, len(transcript))
        if end < len(transcript):
            boundary = max(transcript.rfind("\n", start, end), transcript.rfind(". ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunks.append({"start_char": start, "end_char": end, "text": transcript[start:end]})
        if end >= len(transcript):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def _extract_segment(segment: str) -> list[dict[str, Any]]:
    model_requirements = extract_requirements_with_claude(segment)
    if model_requirements is not None:
        return model_requirements
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    request_pattern = re.compile(r"\b(wants?|needs?|would like|please|keep|add|improve|change|remove|build|integrate|make|require|deadline|delivery date|delivery by)\b", re.I)
    vague_pattern = re.compile(r"\b(better|improve|maybe|something|faster|easy|good)\b", re.I)

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", segment):
        passage = sentence.strip(" -\t\r\n")
        if not passage or not request_pattern.search(passage):
            continue
        identifier = _requirement_id(passage)
        is_removal = bool(re.search(r"\b(no longer (?:need|want)|remove|drop)\b", passage, re.I))
        if identifier in seen:
            existing = next(item for item in requirements if item["id"] == identifier)
            if is_removal:
                existing.update({"description": passage, "source_quote": passage, "status": "removed"})
            continue
        seen.add(identifier)
        unclear = bool(vague_pattern.search(passage))
        requirements.append({
            "id": identifier,
            "description": passage,
            "source_quote": passage,
            "status": "removed" if is_removal else "unclear" if unclear else "confirmed_request",
            "priority": "unconfirmed",
            "feasibility": "needs_knowledge_check",
            "acceptance_criteria": "Clarify a measurable success condition with the client.",
        })
    return requirements


def extract_requirements(transcript: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in split_transcript(transcript):
        for requirement in _extract_segment(chunk["text"]):
            if requirement["id"] in seen:
                if requirement.get("status") == "removed":
                    existing = next(item for item in requirements if item["id"] == requirement["id"])
                    existing.update(requirement)
                continue
            seen.add(requirement["id"])
            requirements.append(requirement)
    return requirements


def check_feasibility(
    requirements: list[dict[str, Any]],
    knowledge_lookup: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    checked: list[dict[str, Any]] = []
    for requirement in requirements:
        result = knowledge_lookup(requirement["description"]) if knowledge_lookup else None
        if result is None:
            label = "needs_human_investigation"
            sources: list[dict[str, Any]] = []
        elif result.get("status") == "answered" and result.get("sources"):
            label = "documented_capability_to_review"
            sources = result["sources"]
        else:
            label = "insufficient_product_evidence"
            sources = result.get("sources", [])
        checked.append({**requirement, "feasibility": label, "knowledge_sources": sources})
    return checked


def generate_scope(
    transcript: str,
    knowledge_lookup: Callable[[str], dict[str, Any]] | None = None,
):
    requirements = check_feasibility(extract_requirements(transcript), knowledge_lookup)
    estimates = [
        {
            "requirement_id": requirement["id"],
            "task": f"Investigate and define: {requirement['description']}",
            "low_hours": 2,
            "likely_hours": 5,
            "high_hours": 10,
            "assumptions": "Preliminary planning range; technical discovery and acceptance criteria are not confirmed.",
        }
        for requirement in requirements
    ]
    totals = {
        "low_hours": sum(task["low_hours"] for task in estimates),
        "likely_hours": sum(task["likely_hours"] for task in estimates),
        "high_hours": sum(task["high_hours"] for task in estimates),
        "status": "preliminary_requires_engineering_review",
        "tasks": estimates,
    }
    open_questions = [
        f"Confirm measurable acceptance criteria for {item['id']}."
        for item in requirements
        if item["status"] == "unclear"
    ]
    open_questions.extend(
        f"Verify existing product capability for {item['id']} with a product specialist."
        for item in requirements
        if item["feasibility"] != "documented_capability_to_review"
    )
    if not requirements:
        open_questions.append("No explicit request could be extracted; ask the client to clarify desired outcomes.")
    scope_lines = [f"- {item['id']}: {item['description']}" for item in requirements] or ["- No confirmed deliverables yet."]
    assumption_lines = [f"- {task['requirement_id']}: {task['assumptions']}" for task in estimates] or ["- Effort is not estimated until requirements are clarified."]
    proposal = "\n".join([
        "DRAFT - NOT APPROVED FOR SENDING",
        "",
        "Project objective",
        "Translate the client's stated requests into a feasible, reviewable delivery scope.",
        "",
        "Scope and deliverables",
        *scope_lines,
        "",
        "Effort estimate",
        f"Preliminary range: {totals['low_hours']}-{totals['high_hours']} hours; likely {totals['likely_hours']} hours.",
        "This is an engineering-planning range, not a schedule or commercial quote.",
        "",
        "Assumptions and exclusions",
        *assumption_lines,
        "Pricing, delivery dates, and unverified integrations are excluded until reviewed.",
        "",
        "Open questions and risks",
        *(f"- {question}" for question in open_questions),
        "",
        "Approval",
        "A named human reviewer must approve the scope, estimate, and commercial terms before this proposal is sent.",
    ])
    totals["open_questions"] = open_questions
    return requirements, totals, proposal
