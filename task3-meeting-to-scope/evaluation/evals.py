import os
import time

from agents.scope_agent import extract_requirements


def run_evals():
    cases = [
        ("We want product search filters.", "REQ-search-filters"),
        ("We need a mobile layout update.", "REQ-mobile"),
        ("Please add payment provider support.", "REQ-payment"),
        ("We need email notifications.", "REQ-notifications"),
        ("Build a booking feature.", "REQ-booking"),
        ("Add a reporting dashboard.", "REQ-reporting"),
        ("Integrate the analytics system.", "REQ-integration"),
        ("We want checkout improvements.", "REQ-checkout"),
        ("Make the search experience better.", "REQ-search"),
        ("We need to change the mobile navigation.", "REQ-mobile"),
        ("Please add a product filter.", "REQ-filters"),
        ("We want delivery by December 15.", "REQ-timeline"),
        ("Thanks for meeting today.", None),
        ("The current product is useful.", None),
        ("We discussed the next release.", None),
        ("Our team will review the notes.", None),
        ("There are three people on the call.", None),
        ("The deadline is still under discussion.", "REQ-timeline"),
        ("We need a custom booking integration.", "REQ-booking"),
        ("Please remove the old widget.", "REQ-old-widget"),
    ]
    api_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    model = os.environ.pop("ANTHROPIC_MODEL", None)
    started = time.perf_counter()
    try:
        results = []
        for transcript, expected_id in cases:
            requirements = extract_requirements(transcript)
            actual_id = requirements[0]["id"] if requirements else None
            results.append({"transcript": transcript, "expected": expected_id, "actual": actual_id, "passed": actual_id == expected_id})
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {"total": len(results), "passed": sum(item["passed"] for item in results), "latency_ms_total": round(elapsed_ms, 2), "cases": results}
    finally:
        if api_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if model is not None:
            os.environ["ANTHROPIC_MODEL"] = model
