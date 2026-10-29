import re

from retrieval.answer import answer_from_evidence
from retrieval.store import search_chunks


def query_knowledge(
    query: str,
    conversation_history: list[dict[str, str]] | None = None,
    user_id: int | None = None,
) -> tuple[str, list[dict[str, str]], bool]:
    if re.search(r"\b(private|personal|someone else's|customer's)\b.*\b(password|credential|card number|secret)\b", query, re.I):
        return "I can't provide private account credentials. A support specialist should verify account ownership.", [], True
    history = conversation_history or []
    previous_questions = [
        turn["content"].strip()
        for turn in history[-8:]
        if turn["role"] == "user" and turn["content"].strip()
    ]
    # Prioritize the current question first to avoid query pollution from past turns
    matches = search_chunks(query, limit=9, user_id=user_id)
    if not matches and previous_questions:
        # Fall back to context from the most recent question for follow-ups
        contextual_query = f"{previous_questions[-1]} {query}"
        matches = search_chunks(contextual_query, limit=9, user_id=user_id)

    if not matches:
        topic = query.strip() or "this question"
        answer = (
            f"I don't have verified knowledge-base evidence about {topic}. "
            "A support specialist should review this question."
        )
        return answer, [], True

    grouped: dict[str, dict] = {}
    for item in matches:
        source = grouped.setdefault(item["url"], {
            "title": item["title"],
            "url": item["url"],
            "relevance": item["relevance"],
            "texts": [],
            "image_references": [],
        })
        source["relevance"] = max(source["relevance"], item["relevance"])
        source["texts"].append(item["text"])
        for image in item["image_references"]:
            if image not in source["image_references"]:
                source["image_references"].append(image)
    evidence = [
        {**source, "text": "\n".join(source.pop("texts"))}
        for source in sorted(grouped.values(), key=lambda item: item["relevance"], reverse=True)[:3]
    ]
    best = evidence[0]
    answer = answer_from_evidence(query, evidence, history[-10:])
    sources = [
        {
            "title": item["title"],
            "url": item["url"],
            "relevance": round(item["relevance"], 3),
            "images": item["image_references"],
        }
        for item in evidence
    ]
    return answer, sources, False
