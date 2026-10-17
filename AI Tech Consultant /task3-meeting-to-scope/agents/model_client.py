import json
import os
from urllib.request import Request, urlopen


PROMPT_VERSION = "transcript-requirements-v1"


def extract_requirements_with_claude(transcript: str) -> list[dict] | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("ANTHROPIC_MODEL")
    if not api_key or not model:
        return None
    payload = {
        "model": model,
        "max_tokens": 1800,
        "system": (
            "Extract explicit client requests from the transcript. Do not infer commitments. "
            "Return only a JSON array of objects with id, description, source_quote, status, priority, "
            "feasibility, and acceptance_criteria. Keep source_quote verbatim. Mark vague requests unclear. "
            "Prompt version: " + PROMPT_VERSION
        ),
        "messages": [{"role": "user", "content": transcript}],
    }
    request = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    with urlopen(request, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))
    text = "".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text").strip()
    requirements = json.loads(text)
    if not isinstance(requirements, list):
        raise ValueError("Claude requirement output must be a JSON array")
    for requirement in requirements:
        if not isinstance(requirement, dict) or not all(key in requirement for key in ("id", "description", "source_quote", "status")):
            raise ValueError("Claude returned a requirement with missing required fields")
        if requirement["source_quote"] not in transcript:
            raise ValueError("Claude returned a source quote that does not appear in the transcript")
    return requirements