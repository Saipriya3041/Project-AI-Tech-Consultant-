from app.main import ask_question
from ingestion.crawler import parse_article
from ingestion.sitemap import parse_sitemap_xml
from ingestion.scheduler import run_daily_pass
from retrieval.store import article_needs_fetch, connect, create_escalation, get_pending_article_links, get_sitemap_cursor, resolve_escalation, save_article
from multimodal.vision import analyze_image


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

    monkeypatch.setattr(crawler, "urlopen", lambda *_args, **_kwargs: RobotsResponse())
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

    monkeypatch.setattr(crawler, "urlopen", lambda *_args, **_kwargs: RobotsResponse())
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
    from retrieval.embeddings import generate_embeddings, generate_query_embedding

    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert generate_embeddings(["a test passage"]) is None
    assert generate_query_embedding("a test question") is None


def test_sitemap_parser_supports_namespaced_urlsets_and_sitemap_indexes():
    urlset = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/article</loc><lastmod>2026-10-01</lastmod></url></urlset>'
    entries, children = parse_sitemap_xml(urlset, "https://example.test/sitemap.xml")
    assert entries == [{"url": "https://example.test/article", "last_modified": "2026-10-01"}]
    assert children == []

    index = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>/articles-1.xml</loc></sitemap></sitemapindex>'
    entries, children = parse_sitemap_xml(index, "https://example.test/sitemap.xml")
    assert entries == []
    assert children == ["https://example.test/articles-1.xml"]


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

    monkeypatch.setattr(crawler, "urlopen", lambda *_args, **_kwargs: RobotsResponse())
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
