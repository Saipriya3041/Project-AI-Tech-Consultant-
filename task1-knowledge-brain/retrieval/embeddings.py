import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from fastembed import TextEmbedding

        _local_model = TextEmbedding(
            model_name=os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
            cache_dir=os.getenv(
                "LOCAL_EMBEDDING_CACHE_DIR",
                str(Path.home() / ".cache" / "fastembed"),
            ),
        )
    return _local_model


def _generate_local_embeddings(texts: list[str]) -> list[list[float]]:
    vectors = [vector.tolist() for vector in _get_local_model().embed(texts)]
    if len(vectors) != len(texts):
        raise ValueError("Local embedding model returned a different number of vectors than inputs")
    return vectors


def generate_embeddings(texts: list[str]) -> list[list[float]] | None:
    if not texts:
        return None
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        return _generate_local_embeddings(texts)
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
    if not text:
        return None
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        return _generate_local_embeddings([text])[0]
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