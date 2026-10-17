import json
import os
from urllib.request import Request, urlopen


def generate_embeddings(texts: list[str]) -> list[list[float]] | None:
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key or not texts:
        return None
    model = os.getenv("VOYAGE_EMBEDDING_MODEL", "voyage-3-lite")
    payload = {"model": model, "input": texts, "input_type": "document"}
    request = Request(
        "https://api.voyageai.com/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    vectors = [item["embedding"] for item in sorted(result.get("data", []), key=lambda item: item["index"])]
    if len(vectors) != len(texts):
        raise ValueError("Embedding provider returned a different number of vectors than inputs")
    return vectors


def generate_query_embedding(text: str) -> list[float] | None:
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key or not text:
        return None
    model = os.getenv("VOYAGE_EMBEDDING_MODEL", "voyage-3-lite")
    payload = {"model": model, "input": [text], "input_type": "query"}
    request = Request(
        "https://api.voyageai.com/v1/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    vectors = result.get("data", [])
    return vectors[0]["embedding"] if vectors else None