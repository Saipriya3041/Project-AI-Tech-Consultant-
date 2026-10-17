import os
import tempfile
import time

from retrieval.search import query_knowledge
from retrieval.store import save_article


ARTICLES = [
    ("https://fixture.test/logo", "Logo settings", "Open design settings and choose Upload logo to replace the site logo."),
    ("https://fixture.test/hours", "Business hours", "Open directory settings and edit the weekly business hours schedule."),
    ("https://fixture.test/security", "Account security", "Use the account security page to change the account password."),
    ("https://fixture.test/billing", "Billing history", "Billing settings shows invoice history and the saved payment method."),
    ("https://fixture.test/images", "Image uploads", "Upload a PNG or JPG image smaller than the configured file size limit."),
]

CASES = [
    ("How can I replace the site logo?", "https://fixture.test/logo"),
    ("Where is Upload logo in design settings?", "https://fixture.test/logo"),
    ("How do I change our logo?", "https://fixture.test/logo"),
    ("Where can I edit business hours?", "https://fixture.test/hours"),
    ("How do I change the weekly schedule?", "https://fixture.test/hours"),
    ("Where are directory hours configured?", "https://fixture.test/hours"),
    ("How do I change my password?", "https://fixture.test/security"),
    ("Where is account security?", "https://fixture.test/security"),
    ("Can I update the account password?", "https://fixture.test/security"),
    ("Where can I see invoices?", "https://fixture.test/billing"),
    ("How do I update the saved payment method?", "https://fixture.test/billing"),
    ("Where is billing history?", "https://fixture.test/billing"),
    ("Which image formats can I upload?", "https://fixture.test/images"),
    ("Can I upload a JPG?", "https://fixture.test/images"),
    ("Where are image upload limits documented?", "https://fixture.test/images"),
    ("Does the service support biometric login?", None),
    ("What is the refund exception for annual contracts?", None),
    ("Can you promise delivery tomorrow?", None),
    ("Does the service integrate with an unknown CRM?", None),
    ("What is the customer's private account password?", None),
]


def run_evals() -> dict:
    previous_path = os.environ.get("KNOWLEDGE_DB_PATH")
    previous_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    previous_model = os.environ.pop("ANTHROPIC_MODEL", None)
    previous_embedding_key = os.environ.pop("VOYAGE_API_KEY", None)
    with tempfile.TemporaryDirectory() as directory:
        os.environ["KNOWLEDGE_DB_PATH"] = os.path.join(directory, "eval.sqlite3")
        try:
            for url, title, text in ARTICLES:
                save_article(url, title, [{"heading": title, "text": text}])
            started = time.perf_counter()
            results = []
            for query, expected_url in CASES:
                answer, sources, needs_human = query_knowledge(query)
                actual_url = sources[0]["url"] if sources else None
                passed = actual_url == expected_url and needs_human == (expected_url is None)
                results.append({"query": query, "expected": expected_url, "actual": actual_url, "passed": passed})
            elapsed_ms = (time.perf_counter() - started) * 1000
            return {"total": len(CASES), "passed": sum(item["passed"] for item in results), "latency_ms_total": round(elapsed_ms, 2), "cases": results}
        finally:
            if previous_path is None:
                os.environ.pop("KNOWLEDGE_DB_PATH", None)
            else:
                os.environ["KNOWLEDGE_DB_PATH"] = previous_path
            if previous_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = previous_key
            if previous_model is not None:
                os.environ["ANTHROPIC_MODEL"] = previous_model
            if previous_embedding_key is not None:
                os.environ["VOYAGE_API_KEY"] = previous_embedding_key


if __name__ == "__main__":
    print(run_evals())
