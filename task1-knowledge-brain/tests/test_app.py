from app.main import ask_question
from app.auth import reset_local_account_password
from fastapi.testclient import TestClient
from app.main import app
import pytest
from ingestion.crawler import parse_article
from ingestion.sitemap import parse_sitemap_xml
from ingestion.scheduler import run_daily_pass
from retrieval.store import article_needs_fetch, connect, create_escalation, get_pending_article_links, get_sitemap_cursor, resolve_escalation, save_article, save_sitemap_cursor
from multimodal.vision import analyze_image


def test_account_registration_requires_strong_password_and_protects_sitemap_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("KNOWLEDGE_SERVICE_TOKEN", "test-service-token")
    client = TestClient(app)

    assert client.get("/sitemaps").status_code == 401
    assert client.get("/ingestion/status").status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert client.get("/ask", params={"q": "hello"}).status_code == 401
    assert client.get("/internal/ask", params={"q": "hello"}).status_code == 401
    monkeypatch.setattr("app.main.query_knowledge", lambda _query: ("answer", [], False))
    assert client.get(
        "/internal/ask",
        params={"q": "hello"},
        headers={"X-Knowledge-Service-Key": "test-service-token"},
    ).json()["answer"] == "answer"
    weak_password = client.post(
        "/auth/register",
        json={"email": "owner@example.test", "password": "short"},
    )
    assert weak_password.status_code == 422

    registered = client.post(
        "/auth/register",
        json={"email": "Owner@example.test", "password": "a-strong-password-123"},
    )
    assert registered.status_code == 201
    assert registered.json()["email"] == "owner@example.test"
    assert client.get("/auth/me").json()["email"] == "owner@example.test"

    assert client.post(
        "/auth/register",
        json={"email": "owner@example.test", "password": "a-strong-password-123"},
    ).status_code == 409
    signed_in = client.post(
        "/auth/login",
        json={"email": "  OWNER@example.test  ", "password": "a-strong-password-123"},
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["email"] == "owner@example.test"
    assert client.post(
        "/auth/login",
        json={"email": "owner@example.test", "password": "incorrect-password"},
    ).status_code == 401


def test_password_reset_email_flow_uses_single_use_token_and_invalidates_sessions(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "support@example.test")
    client = TestClient(app)
    registered = client.post(
        "/auth/register",
        json={"email": "owner@example.test", "password": "owner-password-123"},
    )
    assert registered.status_code == 201
    sent = []
    monkeypatch.setattr(main, "send_password_reset_email", lambda email, token: sent.append((email, token)))

    response = client.post("/auth/password-reset/request", json={"email": "unknown@example.test"})
    assert response.status_code == 202
    assert response.json()["message"] == "If an account exists for that email, a password reset link will be sent."
    assert sent == []

    response = client.post("/auth/password-reset/request", json={"email": "OWNER@example.test"})
    assert response.status_code == 202
    assert sent[0][0] == "owner@example.test"
    token = sent[0][1]
    completed = client.post(
        "/auth/password-reset/complete",
        json={"token": token, "password": "new-owner-password-456"},
    )
    assert completed.status_code == 200
    assert client.get("/auth/me").status_code == 401
    assert client.post(
        "/auth/login",
        json={"email": "owner@example.test", "password": "owner-password-123"},
    ).status_code == 401
    assert client.post(
        "/auth/login",
        json={"email": "owner@example.test", "password": "new-owner-password-456"},
    ).status_code == 200
    assert client.post(
        "/auth/password-reset/complete",
        json={"token": token, "password": "another-owner-password-789"},
    ).status_code == 400


def test_password_reset_requires_smtp_configuration_even_for_unknown_email(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    response = TestClient(app).post(
        "/auth/password-reset/request",
        json={"email": "unknown@example.test"},
    )
    assert response.status_code == 503
    assert "SMTP_HOST and SMTP_FROM" in response.json()["detail"]


def test_local_account_password_reset_updates_password_and_revokes_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    client = TestClient(app)
    registered = client.post(
        "/auth/register",
        json={"email": "owner@example.test", "password": "old-password-123"},
    )
    assert registered.status_code == 201
    old_session = client.cookies.get("knowledge_session")

    assert reset_local_account_password(" OWNER@example.test ", "new-password-456") is True
    assert client.get("/auth/me").status_code == 401
    assert client.post(
        "/auth/login",
        json={"email": "owner@example.test", "password": "old-password-123"},
    ).status_code == 401
    assert client.post(
        "/auth/login",
        json={"email": "owner@example.test", "password": "new-password-456"},
    ).status_code == 200
    assert old_session is not None
    assert reset_local_account_password("unknown@example.test", "new-password-456") is False
    with pytest.raises(ValueError, match="at least 12 characters"):
        reset_local_account_password("owner@example.test", "short")


def test_sitemap_urls_are_persisted_and_isolated_per_account(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    owner = TestClient(app)
    other_user = TestClient(app)
    owner.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    other_user.post("/auth/register", json={"email": "other@example.test", "password": "other-password-123"})

    first = owner.post("/sitemaps", json={"url": "https://example.test/sitemap.xml"})
    second = owner.post("/sitemaps", json={"url": "https://example.test/other-sitemap.xml"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert owner.post("/sitemaps", json={"url": "https://example.test/sitemap.xml"}).status_code == 409
    assert len(owner.get("/sitemaps").json()["sitemaps"]) == 2
    assert other_user.get("/sitemaps").json()["sitemaps"] == []

    assert other_user.delete(f"/sitemaps/{first.json()['id']}").status_code == 404
    assert owner.delete(f"/sitemaps/{first.json()['id']}").status_code == 200
    assert len(owner.get("/sitemaps").json()["sitemaps"]) == 1
    assert owner.post("/sitemaps", json={"url": "file:///etc/passwd"}).status_code == 422

    owner.post("/auth/logout")
    assert owner.get("/auth/me").status_code == 401


def test_inbox_integration_token_requires_a_workbench_session(tmp_path, monkeypatch):
    import base64
    import hashlib
    import hmac
    import json
    import time

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("GMAIL_SESSION_SECRET", "a-shared-integration-secret-with-32-chars")
    client = TestClient(app)
    assert client.get("/auth/inbox-token").status_code == 401
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})

    response = client.get("/auth/inbox-token")
    assert response.status_code == 200
    payload, signature = response.json()["token"].split(".")
    expected = hmac.new(
        b"a-shared-integration-secret-with-32-chars",
        payload.encode(),
        hashlib.sha256,
    ).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["sub"] == "1"
    assert claims["exp"] > int(time.time() * 1000)


def test_ingestion_uses_only_the_signed_in_users_sitemaps(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    client = TestClient(app)
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    client.post("/sitemaps", json={"url": "https://example.test/sitemap.xml"})
    client.post("/sitemaps", json={"url": "https://example.test/second-sitemap.xml"})
    calls = []

    def fake_ingestion(limit, sitemap_url, deactivate_missing, refresh_existing, exclude_urls, progress_callback):
        calls.append((limit, sitemap_url, deactivate_missing, refresh_existing, exclude_urls))
        progress_callback({
            "completed": 3,
            "total": 3,
            "processed": 3,
            "unchanged": 0,
            "failed": 0,
            "disallowed": 0,
        })
        return {
            "discovered": 3,
            "discovered_articles": 3,
            "processed": 3,
            "added_or_updated": 3,
            "unchanged": 0,
            "failed": 0,
            "disallowed": 0,
            "completed": 3,
            "removed": 0,
            "complete": True,
            "sitemap_urls": [f"{sitemap_url}/article"],
        }

    monkeypatch.setattr(main, "run_ingestion", fake_ingestion)
    response = client.post("/ingestion/run")
    assert response.status_code == 200
    assert calls == [
        (None, "https://example.test/sitemap.xml", False, True, set()),
        (None, "https://example.test/second-sitemap.xml", False, True, {
            "https://example.test/sitemap.xml/article",
        }),
    ]
    assert response.json()["processed"] == 6
    assert len(response.json()["sitemaps"]) == 2
    progress = client.get("/ingestion/status").json()["progress"]
    assert progress["active"] is False
    assert progress["status"] == "complete"
    assert progress["completed"] == progress["total"] == 6
    assert progress["percent"] == 100
    assert progress["estimated_seconds_remaining"] == 0


def test_safe_crawler_rejects_non_public_sitemap_targets(monkeypatch):
    import socket
    from ingestion.safe_http import validate_public_url

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    try:
        validate_public_url("https://example.test/sitemap.xml")
    except ValueError as error:
        assert "public IP" in str(error)
    else:
        raise AssertionError("Private IP target should be rejected")


def test_refund_abstains_without_verified_source(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    result = ask_question("refund")
    assert result["status"] == "insufficient_evidence"
    assert "refund" in result["answer"].lower()
    assert result["sources"] == []
    assert result["needs_human"] is True
    assert result["escalation_id"] is not None
    assert ask_question("refund")["escalation_id"] == result["escalation_id"]


def test_unrelated_question_does_not_get_refund_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    result = ask_question("How do I change my logo?")
    assert result["status"] == "insufficient_evidence"
    assert "logo" in result["answer"]
    assert result["needs_human"] is True


def test_indexed_article_produces_extract_with_citation_and_image_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://example.test/logo",
        "Update a logo",
        [{"heading": "Logo settings", "text": "Open design settings to upload a new logo."}],
        images=[{"url": "https://example.test/logo-screen.png", "alt": "Logo upload button", "context": "upload a new logo"}],
    )
    result = ask_question("Where do I upload a logo?")
    assert result["status"] == "answered"
    assert "upload a new logo" in result["answer"]
    assert result["sources"][0]["url"] == "https://example.test/logo"
    assert result["sources"][0]["images"][0]["url"].endswith("logo-screen.png")


def test_combined_question_returns_unique_sources_and_evidence_from_each(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://example.test/member-import",
        "Import members via CSV",
        [
            {"heading": "Import members", "text": "Open Members and choose Import Members to upload a CSV file."},
            {"heading": "Mandatory fields", "text": "Required columns include First Name, Last Name, and Email Address."},
        ],
    )
    save_article(
        "https://example.test/member-images",
        "Import member images",
        [{"heading": "Member images", "text": "After importing records, upload profile photos and logos from Members then Member Images."}],
    )
    result = ask_question("How do I import members from CSV with mandatory fields and profile photos or logos?")
    source_urls = [source["url"] for source in result["sources"]]
    assert result["status"] == "answered"
    assert len(source_urls) == len(set(source_urls))
    assert "First Name" in result["answer"]
    assert "profile photos and logos" in result["answer"]


def test_generic_page_words_do_not_answer_unsupported_logo_question(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://example.test/dashboard",
        "Dashboard links and modules",
        [{"heading": "Dashboard", "text": "Refresh the live website and install new updates from the dashboard."}],
    )
    result = ask_question("How do I update my website logo?")
    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == []


def test_member_profile_logo_article_does_not_answer_site_logo_question(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://example.test/member-images",
        "Member images",
        [{"heading": "Profile photos and logos", "text": "Import member profile photos, logos, and cover photos using the CSV member import."}],
    )
    result = ask_question("How do I update my website logo?")
    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == []


def test_procedural_questions_require_article_level_product_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://support.brilliantdirectories.com/support/solutions/12000001522",
        "Section: Settings",
        [{"heading": "Settings", "text": "Manage website logo options in settings."}],
    )
    result = ask_question("How do I update my website logo?")
    assert result["status"] == "insufficient_evidence"
    assert result["sources"] == []


def test_expert_resolution_adds_a_searchable_knowledge_record(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    escalation_id = create_escalation("Can I change the footer color?", "No article found")
    resolve_escalation(escalation_id, "Open theme settings and choose Footer colors.", "reviewer@example.test")
    result = ask_question("Where are footer colors changed?")
    assert result["status"] == "answered"
    assert result["sources"][0]["url"] == f"expert://escalations/{escalation_id}"


def test_article_parser_keeps_heading_and_image_context():
    parsed = parse_article(
        "<html><head><title>Settings guide</title></head><body><nav><a href='/support/solutions/nav'>menu noise</a></nav><main>"
        "<h2>Logo</h2><p>Open design settings.</p><a href='/support/solutions/12000001'>Read the guide</a>"
        "<a href='https://outside.test/support/solutions/ignore'>External</a><img src='/screen.png' alt='Upload logo button'>"
        "</main></body></html>",
        "https://example.test/guide",
    )
    assert parsed["title"] == "Settings guide"
    assert "menu noise" not in str(parsed["sections"])
    assert parsed["sections"][0]["heading"] == "Logo"
    assert parsed["images"][0]["url"] == "https://example.test/screen.png"
    assert "Open design settings." in parsed["images"][0]["context"]
    assert parsed["links"] == ["https://example.test/support/solutions/12000001"]


def test_sitemap_last_modified_skips_unchanged_article(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article("https://example.test/guide", "Guide", [{"heading": "Setup", "text": "Set up your account."}], "2026-10-01")
    assert article_needs_fetch("https://example.test/guide", "2026-10-02") is True
    assert article_needs_fetch("https://example.test/guide", "2026-10-01") is False


def test_legacy_article_without_link_scan_checkpoint_is_refetched(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    url = "https://example.test/legacy"
    save_article(url, "Legacy guide", [{"heading": "Setup", "text": "Set up the account."}], "2026-10-01")
    connection = connect()
    connection.execute("UPDATE articles SET links_scanned = 0 WHERE url = ?", (url,))
    connection.commit()
    connection.close()
    assert article_needs_fetch(url, "2026-10-01") is True


def test_pending_crawl_prioritizes_article_urls_over_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://support.brilliantdirectories.com/support/solutions/category",
        "Member section",
        [{"heading": "Member section", "text": "Browse member help."}],
        links=[
            "https://support.brilliantdirectories.com/support/solutions/12000001516",
            "https://support.brilliantdirectories.com/support/solutions/folders/12000005689",
            "https://support.brilliantdirectories.com/support/solutions/articles/12000012345-member-import",
        ],
    )
    pending = get_pending_article_links()
    assert "/articles/" in pending[0]


def test_pending_crawl_prioritizes_articles_matching_open_escalations(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    save_article(
        "https://support.brilliantdirectories.com/support/solutions/category",
        "Member section",
        [{"heading": "Member section", "text": "Browse member help."}],
        links=[
            "https://support.brilliantdirectories.com/support/solutions/articles/12000000500-website-themes",
            "https://support.brilliantdirectories.com/support/solutions/articles/5000558475-how-to-import-members-via-csv-file",
        ],
    )
    create_escalation("How do I import members from a CSV file?", "No indexed article found")
    assert "import-members-via-csv" in get_pending_article_links()[0]


def test_crawler_follows_support_links_and_answers_from_article_page(tmp_path, monkeypatch):
    import ingestion.crawler as crawler

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("CRAWL_DELAY_SECONDS", "0")
    monkeypatch.setenv("VISION_ENABLED", "false")
    monkeypatch.setattr(crawler, "fetch_sitemap", lambda *_args, **_kwargs: [
        {"url": "https://support.test/support/solutions/category", "last_modified": None},
    ])

    class RobotsResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"User-agent: *\nAllow: /"

    monkeypatch.setattr(crawler, "open_public_url", lambda *_args, **_kwargs: RobotsResponse())
    pages = {
        "https://support.test/support/solutions/category": (
            "<html><title>Member help</title><main>"
            "<a href='/support/solutions/article-1'>Membership plan setup</a></main></html>"
        ),
        "https://support.test/support/solutions/article-1": (
            "<html><title>Configure a membership plan</title><main>"
            "<p>Open membership settings and select a plan.</p></main></html>"
        ),
    }
    monkeypatch.setattr(crawler, "_get_with_retries", lambda url, *_args: pages[url])

    result = crawler.run_ingestion(limit=2, sitemap_url="https://support.test/sitemap.xml")
    answer = ask_question("Where do I configure membership plans?")
    assert result["processed"] == 2
    assert result["complete"] is True
    assert answer["status"] == "answered"
    assert answer["sources"][0]["url"] == "https://support.test/support/solutions/article-1"


def test_crawl_resumes_at_next_sitemap_page_across_small_batches(tmp_path, monkeypatch):
    import ingestion.crawler as crawler

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("CRAWL_DELAY_SECONDS", "0")
    monkeypatch.setenv("VISION_ENABLED", "false")
    sitemap_url = "https://support.test/sitemap.xml"
    urls = [f"https://support.test/support/solutions/page-{index}" for index in range(3)]
    monkeypatch.setattr(crawler, "fetch_sitemap", lambda *_args, **_kwargs: [
        {"url": url, "last_modified": None} for url in urls
    ])

    class RobotsResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"User-agent: *\nAllow: /"

    monkeypatch.setattr(crawler, "open_public_url", lambda *_args, **_kwargs: RobotsResponse())
    fetched = []

    def fetch_page(url, *_args):
        fetched.append(url)
        return f"<html><title>Page {url[-1]}</title><main><p>Unique article information {url[-1]}.</p></main></html>"

    monkeypatch.setattr(crawler, "_get_with_retries", fetch_page)
    first = crawler.run_ingestion(limit=1, sitemap_url=sitemap_url)
    assert get_sitemap_cursor(sitemap_url, 3) == 1
    second = crawler.run_ingestion(limit=1, sitemap_url=sitemap_url)
    assert get_sitemap_cursor(sitemap_url, 3) == 2
    third = crawler.run_ingestion(limit=1, sitemap_url=sitemap_url)
    assert get_sitemap_cursor(sitemap_url, 3) == 0

    assert fetched == urls
    assert first["complete"] is False
    assert second["complete"] is False
    assert third["complete"] is True


def test_crawl_without_page_limit_processes_every_sitemap_url(tmp_path, monkeypatch):
    import ingestion.crawler as crawler

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("CRAWL_DELAY_SECONDS", "0")
    monkeypatch.setenv("VISION_ENABLED", "false")
    sitemap_url = "https://support.test/sitemap.xml"
    urls = [f"https://support.test/support/solutions/page-{index}" for index in range(4)]
    save_sitemap_cursor(sitemap_url, 2)
    monkeypatch.setattr(crawler, "fetch_sitemap", lambda *_args, **_kwargs: [
        {"url": url, "last_modified": None} for url in urls
    ])

    class RobotsResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"User-agent: *\nAllow: /"

    monkeypatch.setattr(crawler, "open_public_url", lambda *_args, **_kwargs: RobotsResponse())
    fetched = []

    def fetch_page(url, *_args):
        fetched.append(url)
        return f"<html><title>Page {url[-1]}</title><main><p>Article information {url[-1]}.</p></main></html>"

    monkeypatch.setattr(crawler, "_get_with_retries", fetch_page)
    result = crawler.run_ingestion(limit=None, sitemap_url=sitemap_url)

    assert fetched == urls[2:] + urls[:2]
    assert result["discovered"] == 4
    assert result["processed"] == 4
    assert result["complete"] is True


def test_daily_worker_continues_batches_until_full_pass(monkeypatch):
    import ingestion.scheduler as scheduler

    reports = iter([
        {"complete": False, "failed": 0, "disallowed": 0, "processed": 1000},
        {"complete": True, "failed": 0, "disallowed": 0, "processed": 696},
    ])
    calls = []
    monkeypatch.setattr(scheduler, "run_ingestion", lambda **kwargs: calls.append(kwargs) or next(reports))
    result = run_daily_pass()
    assert result["complete"] is True
    assert calls == [{"limit": 1000}, {"limit": 1000}]


def test_vision_reports_not_configured_without_claiming_image_content(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    result = analyze_image("https://example.test/settings.png", "Settings page")
    assert result["status"] == "vision_model_not_configured"
    assert result["description"] is None


def test_embedding_provider_is_optional_when_no_key_is_set(monkeypatch):
    import retrieval.embeddings as embeddings

    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(embeddings, "_get_local_model", lambda: type(
        "FakeEmbeddingModel",
        (),
        {"embed": lambda _self, texts: [type("Vector", (), {"tolist": lambda _self: [1.0, 0.0]})() for _ in texts]},
    )())
    assert embeddings.generate_embeddings(["a test passage"]) == [[1.0, 0.0]]
    assert embeddings.generate_query_embedding("a test question") == [1.0, 0.0]


def test_unchanged_article_backfills_missing_embeddings(tmp_path, monkeypatch):
    import retrieval.store as store

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr(store, "generate_embeddings", lambda _texts: None)
    url = "https://example.test/article"
    sections = [{"heading": "Account setup", "text": "Open account settings to update your profile."}]
    assert store.save_article(url, "Account settings", sections) is True
    assert store.article_needs_fetch(url, None) is True

    monkeypatch.setattr(store, "generate_embeddings", lambda texts: [[0.25, 0.75] for _ in texts])
    assert store.save_article(url, "Account settings", sections) is False
    assert store.article_needs_fetch(url, None) is False
    connection = store.connect()
    try:
        row = connection.execute("SELECT embedding_json FROM chunks WHERE source_id = ?", (url,)).fetchone()
    finally:
        connection.close()
    assert row["embedding_json"] == "[0.25, 0.75]"


def test_article_content_changes_are_kept_in_history(tmp_path, monkeypatch):
    import retrieval.store as store

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr(store, "generate_embeddings", lambda texts: [[0.25, 0.75] for _ in texts])
    url = "https://example.test/article"
    store.save_article(url, "Account guide", [{"heading": "Settings", "text": "Open account settings."}])
    store.save_article(url, "Updated account guide", [{"heading": "Settings", "text": "Open profile settings."}])

    history = store.get_article_change_history()
    assert len(history) == 1
    assert history[0]["url"] == url
    assert "Open account settings." in history[0]["old_content"]
    assert "Open profile settings." in history[0]["new_content"]


def test_ingestion_status_reports_embedding_counts(tmp_path, monkeypatch):
    import retrieval.store as store

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr(store, "generate_embeddings", lambda texts: [[0.5, 0.5] for _ in texts])
    store.save_article(
        "https://example.test/embedded",
        "Embedded article",
        [{"heading": "Intro", "text": "This article receives a generated embedding."}],
    )
    monkeypatch.setattr(store, "generate_embeddings", lambda _texts: None)
    store.save_article(
        "https://example.test/pending",
        "Pending article",
        [{"heading": "Intro", "text": "This article has no generated embedding."}],
    )

    status = store.get_ingestion_status()
    assert status["embedding_count"] == 1
    assert status["embedding_pending"] == 1


def test_conversational_ask_uses_history_for_follow_up_retrieval(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    client = TestClient(app)
    assert client.post(
        "/ask/chat",
        json={"question": "What about changing it?", "conversation_history": []},
    ).status_code == 401
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    received = {}

    def fake_query(question, history):
        received["question"] = question
        received["history"] = history
        return "Grounded answer", [], True

    monkeypatch.setattr(main, "query_knowledge", fake_query)
    response = client.post("/ask/chat", json={
        "question": "What about changing it?",
        "conversation_history": [
            {"role": "user", "content": "How do I change my account email?"},
            {"role": "assistant", "content": "Open account settings."},
        ],
    })

    assert response.status_code == 200
    assert received["question"] == "What about changing it?"
    assert received["history"] == [
        {"role": "user", "content": "How do I change my account email?"},
        {"role": "assistant", "content": "Open account settings."},
    ]


def test_follow_up_retrieval_uses_prior_user_question_and_model_history(monkeypatch):
    import retrieval.search as search

    received = {}
    monkeypatch.setattr(search, "search_chunks", lambda query, limit: received.update(
        {"query": query, "limit": limit}
    ) or [{
        "url": "https://example.test/alerts",
        "title": "Account alerts",
        "relevance": 0.9,
        "text": "Turn account alerts on in notification settings.",
        "image_references": [],
    }])
    monkeypatch.setattr(search, "answer_from_evidence", lambda question, evidence, history: received.update(
        {"question": question, "evidence": evidence, "history": history}
    ) or "Enable alerts in notification settings.")
    history = [
        {"role": "user", "content": "How do I configure account alerts?"},
        {"role": "assistant", "content": "Use notification settings."},
    ]

    result = search.query_knowledge("Where is that?", history)

    assert "configure account alerts" in received["query"]
    assert received["question"] == "Where is that?"
    assert received["history"] == history
    assert result[0] == "Enable alerts in notification settings."
    assert result[1][0]["url"] == "https://example.test/alerts"


def test_sitemap_parser_supports_namespaced_urlsets_and_sitemap_indexes():
    urlset = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/article</loc><lastmod>2026-10-01</lastmod></url></urlset>'
    entries, children = parse_sitemap_xml(urlset, "https://example.test/sitemap.xml")
    assert entries == [{"url": "https://example.test/article", "last_modified": "2026-10-01"}]
    assert children == []

    index = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>/articles-1.xml</loc></sitemap></sitemapindex>'
    entries, children = parse_sitemap_xml(index, "https://example.test/sitemap.xml")
    assert entries == []
    assert children == ["https://example.test/articles-1.xml"]


def test_fetch_sitemap_follows_nested_indexes_and_collects_all_unique_urls(monkeypatch):
    from ingestion.sitemap import fetch_sitemap_details

    documents = {
        "https://example.test/root.xml": b"""<sitemapindex>
          <sitemap><loc>/section.xml</loc></sitemap>
          <sitemap><loc>/nested/index.xml</loc></sitemap>
        </sitemapindex>""",
        "https://example.test/section.xml": b"""<urlset>
          <url><loc>/article-a</loc></url>
          <url><loc>/article-shared</loc></url>
        </urlset>""",
        "https://example.test/nested/index.xml": b"""<sitemapindex>
          <sitemap><loc>/nested/section.xml</loc></sitemap>
        </sitemapindex>""",
        "https://example.test/nested/section.xml": b"""<urlset>
          <url><loc>/article-b</loc></url>
          <url><loc>/article-shared</loc></url>
        </urlset>""",
    }
    fetched = []

    class FakeResponse:
        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return documents[self.url]

    def fake_open(request, timeout):
        fetched.append(request.full_url)
        return FakeResponse(request.full_url)

    monkeypatch.setattr("ingestion.sitemap.open_public_url", fake_open)
    details = fetch_sitemap_details("https://example.test/root.xml")

    assert fetched == list(documents)
    assert details["total_sitemaps_found"] == 4
    assert details["total_urls_found"] == 3
    assert details["duplicate_urls_skipped"] == 1
    assert details["url_occurrence_counts"]["https://example.test/article-shared"] == 2
    assert details["url_sitemap_references"]["https://example.test/article-shared"] == [
        "https://example.test/section.xml",
        "https://example.test/nested/section.xml",
    ]
    assert details["duplicate_urls"] == [{
        "url": "https://example.test/article-shared",
        "occurrences": 2,
        "sitemap_references": [
            "https://example.test/section.xml",
            "https://example.test/nested/section.xml",
        ],
    }]
    assert [page["url"] for page in details["pages"]] == [
        "https://example.test/article-a",
        "https://example.test/article-shared",
        "https://example.test/article-b",
    ]
    assert details["first_url"] == "https://example.test/article-a"
    assert details["last_url"] == "https://example.test/article-b"
    assert [sitemap["url"] for sitemap in details["sitemaps"] if sitemap["nested"]] == list(documents)[1:]


def test_ingestion_preview_reports_nested_sitemaps_and_unique_url_totals(tmp_path, monkeypatch):
    from app import main

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    client = TestClient(app)
    assert client.get("/ingestion/preview").status_code == 401
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    client.post("/sitemaps", json={"url": "https://example.test/root.xml"})
    client.post("/sitemaps", json={"url": "https://example.test/another.xml"})
    monkeypatch.setattr(main, "fetch_sitemap_details", lambda url, timeout: {
        "pages": [
            {"url": "https://example.test/first", "last_modified": None},
            {"url": "https://example.test/shared", "last_modified": None},
        ] if url.endswith("root.xml") else [
            {"url": "https://example.test/shared", "last_modified": None},
            {"url": "https://example.test/last", "last_modified": None},
        ],
        "url_occurrence_counts": {"https://example.test/shared": 1},
        "url_sitemap_references": {
            "https://example.test/shared": [f"{url}/nested.xml"],
        },
        "sitemaps": [
            {"url": url, "parent_url": None, "urls_found": 1, "first_url": "https://example.test/first", "last_url": "https://example.test/first", "nested": False},
            {"url": f"{url}/nested.xml", "parent_url": url, "urls_found": 1, "first_url": "https://example.test/last", "last_url": "https://example.test/last", "nested": True},
        ],
    })

    response = client.get("/ingestion/preview")
    assert response.status_code == 200
    assert response.json()["connected_sitemaps"] == 2
    assert response.json()["total_sitemaps_found"] == 4
    assert response.json()["total_urls_found"] == 3
    assert response.json()["duplicate_urls_skipped"] == 1
    assert response.json()["duplicate_urls"] == [{
        "url": "https://example.test/shared",
        "occurrences": 2,
        "sitemap_references": [
            "https://example.test/root.xml/nested.xml",
            "https://example.test/another.xml/nested.xml",
        ],
    }]
    assert response.json()["first_url"] == "https://example.test/first"
    assert response.json()["last_url"] == "https://example.test/last"
    assert len([sitemap for sitemap in response.json()["sitemaps"] if sitemap["nested"]]) == 2


def test_ingestion_preview_reports_unavailable_sitemap_with_cors_error(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from app import main

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    client = TestClient(app)
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    client.post("/sitemaps", json={"url": "https://example.test/missing-sitemap.xml"})

    def unavailable_sitemap(_url, timeout):
        raise HTTPError(_url, 404, "Not Found", None, None)

    monkeypatch.setattr(main, "fetch_sitemap_details", unavailable_sitemap)
    response = client.get(
        "/ingestion/preview",
        headers={"Origin": "http://127.0.0.1:8081"},
    )

    assert response.status_code == 502
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8081"
    assert "missing-sitemap.xml" in response.json()["detail"]
    assert "404" in response.json()["detail"]


def test_sitemap_recheck_schedule_is_saved_per_account(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    client = TestClient(app)
    assert client.put("/sitemaps/schedule", json={"schedule": "daily"}).status_code == 401
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    client.post("/sitemaps", json={"url": "https://example.test/sitemap.xml"})

    response = client.put("/sitemaps/schedule", json={"schedule": "weekly"})
    assert response.status_code == 200
    assert response.json() == {"schedule": "weekly"}
    assert client.get("/sitemaps").json()["schedule"] == "weekly"
    assert client.put("/sitemaps/schedule", json={"schedule": "hourly"}).status_code == 422
    retention = client.put("/sitemaps/retention", json={"checks": 15})
    assert retention.status_code == 200
    assert client.get("/sitemaps").json()["retention_checks"] == 15
    assert client.put("/sitemaps/retention", json={"checks": 3}).status_code == 422


def test_due_sitemap_checks_follow_the_saved_schedule(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.auth import add_sitemap, create_user, list_due_sitemaps, mark_sitemap_checked, set_sitemap_schedule

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    user = create_user("owner@example.test", "owner-password-123")
    source = add_sitemap(user["id"], "https://example.test/sitemap.xml")
    set_sitemap_schedule(user["id"], "daily")
    checked_at = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)

    assert list_due_sitemaps(checked_at) == [
        {
            "id": source["id"],
            "user_id": user["id"],
            "url": source["url"],
            "schedule": "daily",
            "retention_checks": 1,
        },
    ]
    mark_sitemap_checked(source["id"], checked_at)
    assert list_due_sitemaps(checked_at + timedelta(hours=23)) == []
    assert list_due_sitemaps(checked_at + timedelta(days=1)) == [
        {
            "id": source["id"],
            "user_id": user["id"],
            "url": source["url"],
            "schedule": "daily",
            "retention_checks": 1,
        },
    ]


def test_missing_urls_are_archived_after_configured_consecutive_checks_and_restored(tmp_path, monkeypatch):
    from app.auth import add_sitemap, create_user
    from retrieval.store import (
        list_archived_articles,
        reconcile_sitemap_urls,
        restore_archived_article,
    )

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr("retrieval.store.generate_embeddings", lambda texts: [[0.1, 0.9] for _ in texts])
    url = "https://example.test/retained-guide"
    user = create_user("owner@example.test", "owner-password-123")
    sitemap = add_sitemap(user["id"], "https://example.test/sitemap.xml")
    save_article(url, "Retained guide", [{"heading": "Setup", "text": "Keep this source for review."}])

    assert reconcile_sitemap_urls(sitemap["id"], {url}, 2) == 0
    assert reconcile_sitemap_urls(sitemap["id"], set(), 2) == 0
    assert reconcile_sitemap_urls(sitemap["id"], set(), 2) == 1
    archived = list_archived_articles()
    assert archived[0]["url"] == url
    assert archived[0]["title"] == "Retained guide"
    assert archived[0]["archived_at"]

    connection = connect()
    try:
        article = connection.execute("SELECT status FROM articles WHERE url = ?", (url,)).fetchone()
        chunk = connection.execute("SELECT text, active FROM chunks WHERE source_id = ?", (url,)).fetchone()
    finally:
        connection.close()
    assert article["status"] == "archived"
    assert chunk["text"] == "Keep this source for review."
    assert chunk["active"] == 0

    assert restore_archived_article(url) is True
    assert list_archived_articles() == []
    connection = connect()
    try:
        assert connection.execute("SELECT status FROM articles WHERE url = ?", (url,)).fetchone()["status"] == "active"
        assert connection.execute("SELECT active FROM chunks WHERE source_id = ?", (url,)).fetchone()["active"] == 1
    finally:
        connection.close()


def test_shared_sitemap_url_is_archived_only_after_all_sources_mark_it_missing(tmp_path, monkeypatch):
    from app.auth import add_sitemap, create_user
    from retrieval.store import reconcile_sitemap_urls

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr("retrieval.store.generate_embeddings", lambda texts: [[0.1, 0.9] for _ in texts])
    user = create_user("owner@example.test", "owner-password-123")
    first = add_sitemap(user["id"], "https://example.test/first.xml")
    second = add_sitemap(user["id"], "https://example.test/second.xml")
    url = "https://example.test/shared-guide"
    save_article(url, "Shared guide", [{"heading": "Guide", "text": "This page remains referenced."}])

    reconcile_sitemap_urls(first["id"], {url}, 1)
    reconcile_sitemap_urls(second["id"], {url}, 1)
    assert reconcile_sitemap_urls(first["id"], set(), 1) == 0
    assert article_needs_fetch(url, None) is False
    assert reconcile_sitemap_urls(second["id"], set(), 1) == 1
    assert article_needs_fetch(url, None) is True


def test_ingestion_dashboard_reports_page_counts_and_sync_metadata(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.auth import add_sitemap, create_user
    import retrieval.store as store

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr(store, "generate_embeddings", lambda texts: [[0.3, 0.7] for _ in texts])
    user = create_user("owner@example.test", "owner-password-123")
    sitemap = add_sitemap(user["id"], "https://example.test/sitemap.xml")
    store.save_article("https://example.test/embedded", "Embedded", [{"heading": "Page", "text": "A fully indexed page."}])
    store.ensure_pending_articles(["https://example.test/pending"])
    store.record_failure("https://example.test/failed", "Could not crawl")
    synced_at = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    store.record_sitemap_sync(sitemap["id"], 4, synced_at)

    status = store.get_ingestion_status(user["id"])
    assert status["processed_pages"] == 2
    assert status["embedded_pages"] == 1
    assert status["failed_pages"] == 1
    assert status["pending_pages"] == 1
    assert status["duplicate_urls_skipped"] == 4
    assert status["last_sync_at"] == synced_at.isoformat()


def test_archived_articles_api_lists_and_restores_retained_content(tmp_path, monkeypatch):
    from retrieval.store import reconcile_sitemap_urls

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setattr("retrieval.store.generate_embeddings", lambda texts: [[0.1, 0.9] for _ in texts])
    client = TestClient(app)
    assert client.get("/ingestion/archived").status_code == 401
    client.post("/auth/register", json={"email": "owner@example.test", "password": "owner-password-123"})
    sitemap = client.post("/sitemaps", json={"url": "https://example.test/sitemap.xml"}).json()
    url = "https://example.test/archived-guide"
    save_article(url, "Archived guide", [{"heading": "Guide", "text": "Recoverable content."}])
    reconcile_sitemap_urls(sitemap["id"], {url}, 1)
    reconcile_sitemap_urls(sitemap["id"], set(), 1)

    assert client.get("/ingestion/archived").json()["articles"][0]["url"] == url
    restored = client.post("/ingestion/archived/restore", json={"url": url})
    assert restored.status_code == 200
    assert restored.json() == {"url": url, "status": "active"}
    assert client.get("/ingestion/archived").json()["articles"] == []


def test_scheduled_rechecks_force_existing_pages_to_be_fetched(tmp_path, monkeypatch):
    import ingestion.scheduler as scheduler

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    due = [{
        "id": 7,
        "user_id": 1,
        "url": "https://example.test/sitemap.xml",
        "schedule": "daily",
        "retention_checks": 2,
    }]
    calls = []
    monkeypatch.setattr(scheduler, "list_due_sitemaps", lambda: due)
    monkeypatch.setattr(scheduler, "mark_sitemap_checked", lambda sitemap_id: calls.append(("marked", sitemap_id)))
    monkeypatch.setattr(scheduler, "run_ingestion", lambda **kwargs: calls.append(kwargs) or {"complete": True})

    reports = scheduler.run_scheduled_pass()

    assert reports == [{
        "complete": True,
        "sitemap_url": "https://example.test/sitemap.xml",
        "schedule": "daily",
        "archived": 0,
    }]
    assert calls == [
        ("marked", 7),
        {
            "limit": None,
            "sitemap_url": "https://example.test/sitemap.xml",
            "deactivate_missing": False,
            "refresh_existing": True,
            "exclude_urls": set(),
        },
    ]


def test_scheduled_recrawl_updates_changed_article_embeddings(tmp_path, monkeypatch):
    import ingestion.crawler as crawler
    import retrieval.store as store

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("CRAWL_DELAY_SECONDS", "0")
    monkeypatch.setenv("VISION_ENABLED", "false")
    url = "https://support.test/support/solutions/article"
    sitemap_url = "https://support.test/sitemap.xml"
    embedded_texts = []
    progress_updates = []
    monkeypatch.setattr(store, "generate_embeddings", lambda texts: embedded_texts.append(texts) or [[0.2, 0.8] for _ in texts])
    store.save_article(url, "Billing guide", [{"heading": "Billing", "text": "Manage billing settings."}])
    embedded_texts.clear()
    monkeypatch.setattr(crawler, "fetch_sitemap", lambda *_args, **_kwargs: [{"url": url, "last_modified": None}])

    class RobotsResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"User-agent: *\nAllow: /"

    monkeypatch.setattr(crawler, "open_public_url", lambda *_args, **_kwargs: RobotsResponse())
    monkeypatch.setattr(
        crawler,
        "_get_with_retries",
        lambda *_args: "<html><title>Billing guide</title><main><h2>Billing</h2><p>Update billing notification preferences.</p></main></html>",
    )

    report = crawler.run_ingestion(
        limit=None,
        sitemap_url=sitemap_url,
        deactivate_missing=False,
        refresh_existing=True,
        progress_callback=progress_updates.append,
    )
    connection = store.connect()
    try:
        chunk = connection.execute("SELECT text, embedding_json FROM chunks WHERE source_id = ?", (url,)).fetchone()
    finally:
        connection.close()

    assert report["added_or_updated"] == 1
    assert "Update billing notification preferences." in chunk["text"]
    assert chunk["embedding_json"] == "[0.2, 0.8]"
    assert embedded_texts == [[chunk["text"]]]
    assert progress_updates[-1]["completed"] == 1
    assert progress_updates[-1]["total"] == 1


def test_bounded_crawl_follows_category_to_article_and_retrieves_article(tmp_path, monkeypatch):
    import ingestion.crawler as crawler

    monkeypatch.setenv("KNOWLEDGE_DB_PATH", str(tmp_path / "knowledge.sqlite3"))
    monkeypatch.setenv("CRAWL_DELAY_SECONDS", "0")
    monkeypatch.setenv("VISION_ENABLED", "false")
    monkeypatch.setattr(crawler, "fetch_sitemap", lambda *_args, **_kwargs: [
        {"url": "https://support.test/support/solutions/category", "last_modified": None},
    ])

    class RobotsResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"User-agent: *\nAllow: /"

    monkeypatch.setattr(crawler, "open_public_url", lambda *_args, **_kwargs: RobotsResponse())
    pages = {
        "https://support.test/support/solutions/category": (
            "<html><title>Member help</title><main>"
            "<a href='/support/solutions/article-1'>Membership plan setup</a></main></html>"
        ),
        "https://support.test/support/solutions/article-1": (
            "<html><title>Configure a membership plan</title><main>"
            "<p>Open membership settings and select a plan.</p></main></html>"
        ),
    }
    monkeypatch.setattr(crawler, "_get_with_retries", lambda url, *_args: pages[url])

    result = crawler.run_ingestion(limit=2, sitemap_url="https://support.test/sitemap.xml")
    answer = ask_question("Where do I configure membership plans?")
    assert result["processed"] == 2
    assert result["complete"] is True
    assert answer["status"] == "answered"
    assert answer["sources"][0]["url"] == "https://support.test/support/solutions/article-1"
