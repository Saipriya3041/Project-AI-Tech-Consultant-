from agents.scope_agent import extract_requirements, generate_scope, split_transcript
from evaluation.evals import run_evals
from scope_diff.diff import compare_scopes
from scope_store import approve_proposal, get_approved_baseline, project_history, reject_proposal, save_proposal


def test_scope_uses_transcript_and_marks_approval_required():
    scope, estimate, proposal = generate_scope("Client: We need product search filters. We would like a better mobile experience.")
    assert [item["id"] for item in scope] == ["REQ-search-filters", "REQ-mobile"]
    assert scope[0]["source_quote"] == "Client: We need product search filters."
    assert estimate["likely_hours"] == 10
    assert estimate["status"] == "preliminary_requires_engineering_review"
    assert "NOT APPROVED" in proposal
    assert "Open questions and risks" in proposal
    assert "not a schedule or commercial quote" in proposal


def test_missing_requirements_are_not_invented():
    scope, estimate, _ = generate_scope("Thanks for meeting with us.")
    assert scope == []
    assert estimate["likely_hours"] == 0


def test_scope_diff_reports_add_remove_and_modify_against_baseline():
    old = [{"id": "REQ-search", "description": "Improve search"}, {"id": "REQ-mobile", "description": "Mobile site"}]
    new = [{"id": "REQ-search", "description": "Add filters to search"}, {"id": "REQ-payment", "description": "Payment integration"}]
    changes = compare_scopes(old, new)
    assert changes["added"] == [{**new[1], "out_of_scope": True, "requires_separate_quote": True}]
    assert changes["removed"] == [old[1]]
    assert changes["modified"] == [{"before": old[0], "after": new[0]}]


def test_only_human_approved_scope_becomes_the_persisted_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOPE_DB_PATH", str(tmp_path / "scope.sqlite3"))
    requirements = [{"id": "REQ-search", "description": "Improve product search"}]
    proposal_id = save_proposal("website-project", "We need better search.", requirements, {"likely_hours": 5}, "Draft")
    assert get_approved_baseline("website-project") is None
    approval = approve_proposal(proposal_id, "reviewer")
    assert approval["status"] == "approved"
    assert get_approved_baseline("website-project") == requirements
    assert project_history("website-project")[0]["status"] == "approved"


def test_rejected_proposal_never_becomes_an_approved_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOPE_DB_PATH", str(tmp_path / "scope.sqlite3"))
    requirements = [{"id": "REQ-payment", "description": "Add payment integration"}]
    proposal_id = save_proposal("payment-project", "We need payment support.", requirements, {"likely_hours": 5}, "Draft")
    result = reject_proposal(proposal_id, "reviewer", "Provider support is unverified")
    assert result["status"] == "rejected"
    assert get_approved_baseline("payment-project") is None
    assert project_history("payment-project")[0]["status"] == "rejected"


def test_twenty_case_scope_extraction_evaluation():
    results = run_evals()
    assert results["total"] == 20
    assert results["passed"] == 20


def test_long_transcript_is_split_with_overlap_and_source_text_preserved():
    transcript = "Client: We need search filters.\n" + ("Consultant: Please clarify the requirements.\n" * 400)
    chunks = split_transcript(transcript, max_chars=1000, overlap_chars=100)
    assert len(chunks) > 10
    assert chunks[0]["text"].startswith("Client: We need search filters.")
    assert all(chunk["text"] == transcript[chunk["start_char"]:chunk["end_char"]] for chunk in chunks)
    assert any(left["end_char"] > right["start_char"] for left, right in zip(chunks, chunks[1:]))


def test_search_suggestions_are_flagged_as_new_out_of_scope_work():
    old = [{"id": "REQ-search-filters", "description": "Add category filters to search"}]
    new = [
        {"id": "REQ-search-filters", "description": "Keep category filters"},
        {"id": "REQ-search-suggestions", "description": "Add suggestions as the customer types"},
    ]
    changes = compare_scopes(old, new)
    assert changes["added"][0]["out_of_scope"] is True
    assert changes["added"][0]["requires_separate_quote"] is True


def test_followup_removal_and_addition_are_compared_to_approved_baseline():
    baseline = [
        {"id": "REQ-search-filters", "description": "Add category filters"},
        {"id": "REQ-mobile", "description": "Redesign mobile search"},
        {"id": "REQ-timeline", "description": "Delivery by December 15"},
    ]
    followup = extract_requirements(
        "Client: Keep the search category filters. Client: We no longer need the mobile layout redesign. "
        "Client: We want search suggestions as the customer types. "
        "Client: Change the delivery date to January 20."
    )
    changes = compare_scopes(baseline, followup)
    assert [item["id"] for item in changes["removed"]] == ["REQ-mobile"]
    assert changes["added"][0]["id"] == "REQ-search-suggestions"
    assert changes["added"][0]["requires_separate_quote"] is True
    assert [item["before"]["id"] for item in changes["modified"]] == ["REQ-search-filters", "REQ-timeline"]
