from evaluation.evals import run_evals


def test_twenty_case_retrieval_and_abstention_evaluation():
    results = run_evals()
    assert results["total"] == 20
    assert results["passed"] == 20